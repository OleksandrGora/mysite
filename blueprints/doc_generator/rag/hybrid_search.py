"""
Hybrid Search - BM25 (keyword) + Semantic (vector) fusion for RAG.

Combines:
  - BM25Okapi (rank_bm25) for exact keyword matching across all chunks
  - ChromaDB semantic search per collection for meaning-based retrieval

Key design:
  - Per-collection top_k ensures each source is represented (small sources
    aren't drowned out by large ones)
  - Threshold applies to the SEMANTIC score only (preserving original meaning)
  - BM25 matches are kept regardless of threshold
  - Final ranking uses combined score: alpha * bm25 + (1-alpha) * semantic

Usage:
    hybrid = HybridSearch(embedder)
    results = hybrid.search("двері", sources_hashes=["hash1", "hash2"],
                            top_k=5, alpha=0.5, threshold=0.0)
"""

import re
from typing import List, Dict, Optional
from loguru import logger

from .embedder import Embedder


# Regex to keep only letters (Ukrainian + English) and digits
_TOKEN_PATTERN = re.compile(r"[^\w]|_", re.UNICODE)


def _tokenize(text: str) -> List[str]:
    """
    Tokenize text for BM25: lowercase, strip punctuation, split on whitespace.
    Handles Ukrainian text properly (removes commas, periods, quotes attached
    to words so 'двері.' and 'двері' both match).
    """
    cleaned = _TOKEN_PATTERN.sub(" ", text.lower())
    return cleaned.split()


class HybridSearch:
    """
    BM25 + semantic hybrid search across one or more ChromaDB collections.

    For each query:
      1. Pulls ALL chunks from all specified collections
      2. Builds a BM25 index over the full corpus
      3. Runs semantic search per collection
      4. Fuses scores with weighted combination

    Per-collection sampling: each collection contributes up to `top_k` results,
    ensuring small sources aren't drowned out by large ones.
    """

    def __init__(self, embedder: Embedder):
        self.embedder = embedder

    # ── Public API ──────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        sources_hashes: List[str],
        top_k: int = 5,
        alpha: float = 0.5,
        threshold: float = 0.0,
    ) -> List[Dict]:
        """
        Hybrid search across multiple ChromaDB collections.

        Args:
            query: Search query text
            sources_hashes: List of sources hashes (one per source file/collection)
            top_k: Results PER SOURCE (total results = up to top_k * num_sources)
            alpha: Weight for BM25 score (0.0 = pure semantic, 1.0 = pure BM25)
            threshold: Minimum semantic similarity (0.0-1.0) to keep chunk;
                       BM25-only matches bypass this filter.

        Returns:
            List of scored chunk dicts, globally sorted by score descending.
        """
        if not query.strip():
            return []

        # 1. Collect all chunks from all collections
        all_chunks: List[Dict] = []
        collection_names = []

        for s_hash in sources_hashes:
            col_name = self.embedder.get_collection_name(s_hash)
            try:
                collection = self.embedder.client.get_collection(col_name)
            except Exception:
                logger.warning(f"Collection {col_name} not found, skipping")
                continue

            collection_names.append(col_name)
            data = collection.get(include=["documents", "metadatas"])

            if not data or not data.get("ids"):
                continue

            for i, chunk_id in enumerate(data["ids"]):
                all_chunks.append({
                    "id": chunk_id,
                    "text": data["documents"][i],
                    "metadata": data["metadatas"][i],
                    "collection_name": col_name,
                })

        if not all_chunks:
            logger.warning("No chunks found across any collection")
            return []

        logger.info(f"HybridSearch: {len(all_chunks)} total chunks across "
                     f"{len(collection_names)} collections")

        # 2. BM25 scores over the full corpus
        bm25_scores = self._compute_bm25_scores(query, all_chunks)

        # 3. Semantic scores per collection
        semantic_scores = self._compute_semantic_scores(
            query, collection_names, top_k
        )

        # 4. Per-collection fusion → each source gets its own top-k
        combined = self._fuse_scores_per_collection(
            all_chunks, bm25_scores, semantic_scores,
            alpha, threshold, top_k, collection_names
        )

        return combined

    # ── BM25 ────────────────────────────────────────────────────────────────

    def _compute_bm25_scores(
        self, query: str, chunks: List[Dict]
    ) -> Dict[str, float]:
        """
        Tokenize corpus, build BM25 index, return normalized scores keyed by chunk_id.
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.warning(
                "rank_bm25 not installed. Install with: pip install rank_bm25"
            )
            return {c["id"]: 0.0 for c in chunks}

        tokenized_corpus = [_tokenize(c["text"]) for c in chunks]
        bm25 = BM25Okapi(tokenized_corpus)
        query_tokens = _tokenize(query)

        if not query_tokens:
            return {c["id"]: 0.0 for c in chunks}

        raw_scores = bm25.get_scores(query_tokens)
        max_score = max(raw_scores) if raw_scores.size > 0 else 1.0
        if max_score <= 0:
            max_score = 1.0

        return {
            chunks[i]["id"]: raw_scores[i] / max_score
            for i in range(len(chunks))
        }

    # ── Semantic ────────────────────────────────────────────────────────────

    def _compute_semantic_scores(
        self, query: str, collection_names: List[str], top_k: int
    ) -> Dict[str, float]:
        """
        Run semantic search per collection, return {chunk_id: similarity} dict.
        Requests more results per collection for better BM25 complement.
        """
        scores: Dict[str, float] = {}

        query_embedding = self.embedder._generate_embeddings([query])[0]

        for col_name in collection_names:
            try:
                collection = self.embedder.client.get_collection(col_name)
            except Exception:
                continue

            col_size = collection.count()
            if col_size == 0:
                continue

            # Request more for good coverage (helps BM25 fusion later)
            n_results = min(top_k * 5, col_size)

            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                include=["distances"],
            )

            if not results or not results.get("ids"):
                continue

            for chunk_id, dist in zip(
                results["ids"][0], results["distances"][0]
            ):
                similarity = max(0.0, 1.0 - (dist / 2.0))
                if chunk_id not in scores or similarity > scores[chunk_id]:
                    scores[chunk_id] = similarity

        return scores

    # ── Per-collection Fusion ──────────────────────────────────────────────

    @staticmethod
    def _fuse_scores_per_collection(
        chunks: List[Dict],
        bm25_scores: Dict[str, float],
        semantic_scores: Dict[str, float],
        alpha: float,
        threshold: float,
        top_k: int,
        collection_names: List[str],
    ) -> List[Dict]:
        """
        Per-collection fusion: each collection contributes up to `top_k` results,
        then all are globally sorted by combined score.

        This prevents small collections (e.g. 1-chunk REDFORD) from being
        drowned out by large ones (e.g. 40-chunk gonchar9.doc).

        Threshold applies to semantic score only; BM25 matches bypass it.
        """
        # Group chunks by collection
        col_groups: Dict[str, List[Dict]] = {n: [] for n in collection_names}
        for chunk in chunks:
            cn = chunk["collection_name"]
            if cn in col_groups:
                col_groups[cn].append(chunk)

        all_fused = []

        for col_name, col_chunks in col_groups.items():
            fused = []
            for chunk in col_chunks:
                chunk_id = chunk["id"]
                bm25_s = bm25_scores.get(chunk_id, 0.0)
                semantic_s = semantic_scores.get(chunk_id, 0.0)

                # Threshold applies to semantic score only
                # BM25 matches bypass the filter
                if semantic_s < threshold and bm25_s <= 0:
                    continue

                final_score = alpha * bm25_s + (1 - alpha) * semantic_s

                fused.append({
                    "id": chunk_id,
                    "text": chunk["text"],
                    "source": chunk["metadata"].get("source", "unknown"),
                    "chunk_index": chunk["metadata"].get("chunk_index", 0),
                    "word_count": chunk["metadata"].get("word_count", 0),
                    "score": round(final_score, 4),
                    "bm25_score": round(bm25_s, 4),
                    "semantic_score": round(semantic_s, 4),
                })

            # Sort per-collection, take top_k from this collection
            fused.sort(key=lambda x: x["score"], reverse=True)
            all_fused.extend(fused[:top_k])

        # Global sort across all collections
        all_fused.sort(key=lambda x: x["score"], reverse=True)
        return all_fused

    # ── Query-level hybrid (for multi-query section retrieval) ─────────────

    def search_for_queries(
        self,
        queries: List[Dict[str, str]],
        sources_hashes: List[str],
        top_k: int = 5,
        alpha: float = 0.5,
        threshold: float = 0.0,
        max_total_chunks: int = 50,
    ) -> List[Dict]:
        """
        Hybrid search for multiple queries (e.g. per document section), deduplicated.

        Args:
            queries: List of dicts with 'section_title' and 'query_text'
            sources_hashes: List of sources hashes
            top_k: Results per query per source
            alpha: BM25 weight
            threshold: Minimum semantic score filter
            max_total_chunks: Max unique chunks to return

        Returns:
            Deduplicated list of chunks, sorted by source → chunk_index
        """
        all_chunks = []
        seen_texts = set()

        for q in queries:
            section = q.get("section_title", "unknown")
            query_text = q.get("query_text", "")

            if not query_text:
                continue

            results = self.search(
                query=query_text,
                sources_hashes=sources_hashes,
                top_k=top_k,
                alpha=alpha,
                threshold=threshold,
            )

            for chunk in results:
                dedup_key = f"{chunk.get('source')}:{chunk.get('text', '')[:100]}"
                if dedup_key not in seen_texts:
                    seen_texts.add(dedup_key)
                    chunk["matched_section"] = section
                    all_chunks.append(chunk)

        all_chunks.sort(key=lambda x: (x.get("source", ""), x.get("chunk_index", 0)))

        if len(all_chunks) > max_total_chunks:
            logger.info(
                f"HybridSearch: limiting from {len(all_chunks)} to {max_total_chunks} chunks"
            )
            all_chunks = all_chunks[:max_total_chunks]

        logger.info(f"HybridSearch: total unique chunks retrieved: {len(all_chunks)}")
        return all_chunks
