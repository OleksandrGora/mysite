"""
Retriever - Searches ChromaDB, deduplicates results, formats context for LLM.
"""

from typing import List, Dict
from loguru import logger

from .embedder import Embedder


class Retriever:
    """
    Retrieves relevant chunks for a set of queries,
    deduplicates them, and formats for LLM consumption.
    """

    def __init__(self, embedder: Embedder):
        self.embedder = embedder

    def retrieve_for_queries(
        self,
        queries: List[Dict[str, str]],
        sources_hash: str,
        top_k: int = 3,
        threshold: float = 0.0,
        max_total_chunks: int = 50
    ) -> List[Dict]:
        """
        Search for relevant chunks for each query, then deduplicate.

        Args:
            queries: List of dicts with 'section_title' and 'query_text'
            sources_hash: Hash to identify ChromaDB collection
            top_k: Chunks per query
            threshold: Minimum similarity (0.0-1.0)
            max_total_chunks: Max chunks to return total

        Returns:
            List of unique chunk dicts, sorted by source then chunk_index
        """
        all_chunks = []
        seen_texts = set()

        for q in queries:
            section = q.get('section_title', 'unknown')
            query_text = q.get('query_text', '')

            if not query_text:
                continue

            results = self.embedder.search(
                query=query_text,
                sources_hash=sources_hash,
                top_k=top_k,
                threshold=threshold
            )

            # Deduplicate: skip if same text already seen
            for chunk in results:
                text = chunk.get('text', '')
                # Use first 100 chars + source as dedup key
                dedup_key = f"{chunk.get('source')}:{text[:100]}"
                if dedup_key not in seen_texts:
                    seen_texts.add(dedup_key)
                    chunk['matched_section'] = section
                    all_chunks.append(chunk)

            logger.debug(f"Query '{section[:50]}': got {len(results)} results "
                         f"({len(all_chunks)} unique so far)")

        # Sort by source file then chunk_index (preserves document order)
        all_chunks.sort(key=lambda x: (x.get('source', ''), x.get('chunk_index', 0)))

        # Limit total chunks
        if len(all_chunks) > max_total_chunks:
            logger.info(f"Limiting from {len(all_chunks)} to {max_total_chunks} chunks")
            all_chunks = all_chunks[:max_total_chunks]

        logger.info(f"Total unique chunks retrieved: {len(all_chunks)}")
        return all_chunks

    def retrieve_all_section_chunks(
        self,
        queries: List[Dict[str, str]],
        sources_hash: str,
        top_k: int = 3,
        threshold: float = 0.0,
        max_total_chunks: int = 50
    ) -> List[Dict]:
        """
        Per-section retrieval: N separate ChromaDB searches → deduplicated pool.

        Unlike retrieve_for_queries() which deduplicates globally during search,
        this method makes INDEPENDENT searches for each query and then collects
        all results with deduplication. This gives each section a fair chance
        to find its own relevant chunks.

        Args:
            queries: List of dicts with 'section_title' and 'query_text'
            sources_hash: Hash to identify ChromaDB collection
            top_k: Chunks per query (can be overridden per section)
            threshold: Minimum similarity (0.0-1.0)
            max_total_chunks: Max chunks to return total

        Returns:
            List of unique chunk dicts, sorted by source then chunk_index.
            Each chunk has 'matched_section' indicating which query found it.
        """
        all_chunks = []
        seen_texts = set()

        for q in queries:
            section = q.get('section_title', 'unknown')
            query_text = q.get('query_text', '')

            if not query_text:
                continue

            # Determine per-query top_k based on section size
            per_query_top_k = top_k
            wc = q.get('word_count_range')
            if wc:
                min_w, max_w = wc
                avg_words = (min_w + max_w) / 2
                if avg_words < 30:
                    # Small section: need fewer, more precise chunks
                    per_query_top_k = min(top_k, 2)
                elif avg_words > 150:
                    # Large section: need more chunks for coverage
                    per_query_top_k = top_k + 1

            results = self.embedder.search(
                query=query_text,
                sources_hash=sources_hash,
                top_k=per_query_top_k,
                threshold=threshold
            )

            # Deduplicate: skip if same text already seen
            for chunk in results:
                text = chunk.get('text', '')
                dedup_key = f"{chunk.get('source')}:{text[:100]}"
                if dedup_key not in seen_texts:
                    seen_texts.add(dedup_key)
                    chunk['matched_section'] = section
                    all_chunks.append(chunk)

            logger.debug(f"Query '{section[:50]}': got {len(results)} results "
                         f"({len(all_chunks)} unique so far)")

        # Sort by source file then chunk_index (preserves document order)
        all_chunks.sort(key=lambda x: (x.get('source', ''), x.get('chunk_index', 0)))

        # Limit total chunks
        if len(all_chunks) > max_total_chunks:
            logger.info(f"Limiting from {len(all_chunks)} to {max_total_chunks} chunks")
            all_chunks = all_chunks[:max_total_chunks]

        logger.info(f"Total unique chunks retrieved (per-section): {len(all_chunks)}")
        return all_chunks

    @staticmethod
    def format_context_for_llm(chunks: List[Dict]) -> str:
        """
        Format retrieved chunks as a context block for the LLM prompt.

        Each chunk includes source attribution for traceability.
        """
        if not chunks:
            return "(Не знайдено релевантних фрагментів у джерелах)"

        parts = []
        current_source = None

        for i, chunk in enumerate(chunks, 1):
            source = chunk.get('source', 'unknown')
            chunk_idx = chunk.get('chunk_index', 0)
            similarity = chunk.get('similarity', 0)
            text = chunk.get('text', '')

            # Add source header when source changes
            if source != current_source:
                current_source = source
                parts.append(f"\n=== {source} ===")

            parts.append(
                f"[Фрагмент {i} | релевантність: {similarity:.2f}]\n{text}\n"
            )

        return '\n'.join(parts)

    @staticmethod
    def format_chunks_grouped_by_section(chunks: List[Dict]) -> str:
        """
        Format chunks grouped by matched_section for better LLM context.

        Unlike format_context_for_llm() which groups by source file,
        this groups by which section the chunk was matched to.
        """
        if not chunks:
            return "(Не знайдено релевантних фрагментів у джерелах)"

        # Group chunks by matched_section
        groups = {}
        for chunk in chunks:
            section = chunk.get('matched_section', 'Загальне')
            if section not in groups:
                groups[section] = []
            groups[section].append(chunk)

        parts = []
        for section, section_chunks in groups.items():
            parts.append(f"\n=== {section} ===")
            for c in section_chunks:
                source = c.get('source', '?')
                sim = c.get('similarity', 0)
                text = c.get('text', '')
                parts.append(f"[Джерело: {source} | релевантність: {sim:.2f}]\n{text}\n")

        return '\n'.join(parts)

    @staticmethod
    def format_chunks_for_log(chunks: List[Dict]) -> str:
        """Format chunks for log output."""
        lines = []
        for c in chunks:
            source = c.get('source', '?')
            idx = c.get('chunk_index', 0)
            sim = c.get('similarity', 0)
            section = c.get('matched_section', '?')
            preview = c.get('text', '')[:60].replace('\n', ' ')
            lines.append(
                f"  [{source}:{idx}] sim={sim:.3f} "
                f"matched='{section[:30]}' → '{preview}...'"
            )
        return '\n'.join(lines)
