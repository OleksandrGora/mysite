"""
Embedder - Generates embeddings via Ollama and stores in ChromaDB.

Uses:
  - Ollama nomic-embed-text for embeddings (local, CPU)
  - ChromaDB for vector storage and search
"""

import os
import json
import hashlib
import time
from typing import List, Optional
from pathlib import Path
import requests
from loguru import logger

import chromadb
from chromadb.config import Settings

from .chunker import Chunk

# ChromaDB persistent directory (global, inside doc-generator)
CHROMA_DB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'data', 'chroma_db'
)

# Ollama endpoint
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'nomic-embed-text')

# Batch size for embedding requests to Ollama
BATCH_SIZE = 20


class Embedder:
    """
    Handles embedding generation and ChromaDB storage.
    
    Usage:
        embedder = Embedder()
        collection_name = embedder.build(chunks, sources_hash)
        # or check cache:
        if embedder.collection_exists(sources_hash):
            collection_name = embedder.get_collection_name(sources_hash)
    """

    def __init__(self):
        os.makedirs(CHROMA_DB_DIR, exist_ok=True)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = chromadb.Client(Settings(
                persist_directory=CHROMA_DB_DIR,
                anonymized_telemetry=False
            ))
        return self._client

    @staticmethod
    def get_collection_name(sources_hash: str) -> str:
        """Generate ChromaDB collection name from sources hash."""
        return f"rag_{sources_hash[:16]}"

    def collection_exists(self, sources_hash: str) -> bool:
        """Check if a collection with this hash already exists."""
        name = self.get_collection_name(sources_hash)
        try:
            collections = self.client.list_collections()
            collection_names = []
            for col in collections:
                try:
                    collection_names.append(col.name)
                except:
                    collection_names.append(str(col))
            return name in collection_names
        except Exception as e:
            logger.warning(f"Failed to check collection existence: {e}")
            return False

    def get_collection_metadata(self, sources_hash: str) -> Optional[dict]:
        """Get metadata from an existing collection."""
        name = self.get_collection_name(sources_hash)
        try:
            collection = self.client.get_collection(name)
            return collection.metadata
        except:
            return None

    def delete_collection(self, sources_hash: str):
        """Delete a collection."""
        name = self.get_collection_name(sources_hash)
        try:
            self.client.delete_collection(name)
            logger.info(f"Deleted ChromaDB collection: {name}")
        except Exception as e:
            logger.warning(f"Failed to delete collection {name}: {e}")

    def build(
        self,
        chunks: List[Chunk],
        sources_hash: str,
        source_files: List[str]
    ) -> str:
        """
        Build ChromaDB collection from chunks.
        
        Args:
            chunks: List of Chunk objects
            sources_hash: Hash of source files for caching
            source_files: List of source file names (for metadata)
            
        Returns:
            Collection name
        """
        collection_name = self.get_collection_name(sources_hash)
        texts = [c.text for c in chunks]

        if not texts:
            raise ValueError("No chunks to embed")

        # Generate embeddings via Ollama
        logger.info(f"Generating embeddings for {len(texts)} chunks...")
        t_start = time.time()

        embeddings = self._generate_embeddings(texts)

        t_embed = time.time() - t_start
        logger.info(f"Embeddings generated: {len(embeddings)} vectors in {t_embed:.2f}s")

        # Store in ChromaDB
        t_start = time.time()

        # Prepare data
        ids = []
        metadatas = []
        documents = []

        for i, chunk in enumerate(chunks):
            chunk_hash = hashlib.md5(chunk.text.encode()).hexdigest()[:12]
            chunk_id = f"{chunk.source_file}_{chunk.chunk_index}_{chunk_hash}"

            ids.append(chunk_id)
            metadatas.append({
                'source': chunk.source_file,
                'chunk_index': chunk.chunk_index,
                'word_count': chunk.word_count
            })
            documents.append(chunk.text)

        # Delete old collection if exists
        try:
            self.client.delete_collection(collection_name)
        except:
            pass

        # Create new collection
        collection = self.client.create_collection(
            name=collection_name,
            metadata={
                'sources_hash': sources_hash,
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'source_files': json.dumps(source_files, ensure_ascii=False),
                'total_chunks': len(chunks),
                'embedding_model': EMBEDDING_MODEL,
                'chunk_params': json.dumps({
                    'mode': 'paragraph'
                })
            }
        )

        # Add in batches
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            end = i + batch_size
            collection.add(
                ids=ids[i:end],
                embeddings=embeddings[i:end],
                metadatas=metadatas[i:end],
                documents=documents[i:end]
            )

        t_chroma = time.time() - t_start
        logger.info(f"ChromaDB collection '{collection_name}': {len(chunks)} docs stored in {t_chroma:.2f}s")

        return collection_name

    def _truncate_text(self, text: str, max_chars: int = 1500) -> str:
        """
        Truncate text to fit within embedding model's context window.
        
        nomic-embed-text has 2048 token context.
        For Cyrillic text, each word can be 3-8 BPE tokens.
        We limit to ~1500 chars (~200 words) to be safe.
        """
        if len(text) <= max_chars:
            return text
        
        # Truncate to max_chars at word boundary
        truncated = text[:max_chars]
        # Cut at last space to avoid cutting words
        last_space = truncated.rfind(' ')
        if last_space > 0:
            truncated = truncated[:last_space]
        
        logger.debug(f"Truncated text from {len(text)} to {len(truncated)} chars")
        return truncated

    def _generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for texts via Ollama API.
        
        Processes in batches to avoid overloading Ollama.
        Truncates texts that exceed the model's context window.
        """
        all_embeddings = []

        # Truncate texts to fit model context
        truncated_texts = [self._truncate_text(t) for t in texts]

        for i in range(0, len(truncated_texts), BATCH_SIZE):
            batch = truncated_texts[i:i + BATCH_SIZE]
            
            try:
                response = requests.post(
                    f"{OLLAMA_BASE_URL}/api/embed",
                    json={
                        'model': EMBEDDING_MODEL,
                        'input': batch
                    },
                    timeout=120
                )
                response.raise_for_status()
                data = response.json()

                # nomic-embed-text returns {'embeddings': [[...], ...]}
                batch_embeddings = data.get('embeddings', [])
                if not batch_embeddings:
                    logger.error(f"Empty embeddings response for batch {i}")
                    # Return zero vectors as fallback
                    dim = self._get_embedding_dim()
                    batch_embeddings = [[0.0] * dim] * len(batch)

                all_embeddings.extend(batch_embeddings)

            except requests.exceptions.Timeout:
                logger.error(f"Ollama timeout for batch {i}")
                raise
            except requests.exceptions.ConnectionError:
                logger.error(f"Cannot connect to Ollama at {OLLAMA_BASE_URL}")
                raise
            except Exception as e:
                logger.error(f"Embedding failed for batch {i}: {e}")
                # Try one text at a time to isolate failures
                logger.warning(f"Retrying batch {i} one text at a time...")
                for j, single_text in enumerate(batch):
                    try:
                        resp = requests.post(
                            f"{OLLAMA_BASE_URL}/api/embed",
                            json={'model': EMBEDDING_MODEL, 'input': [single_text]},
                            timeout=120
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        single_emb = data.get('embeddings', [])
                        if single_emb:
                            all_embeddings.append(single_emb[0])
                        else:
                            dim = self._get_embedding_dim()
                            all_embeddings.append([0.0] * dim)
                            logger.warning(f"Empty response for text {i+j}, using zero vector")
                    except Exception as single_e:
                        logger.error(f"Single text {i+j} also failed: {single_e}")
                        dim = self._get_embedding_dim()
                        all_embeddings.append([0.0] * dim)

            # Small delay between batches to not overwhelm Ollama on weak hardware
            if i + BATCH_SIZE < len(texts):
                time.sleep(0.5)

        return all_embeddings

    def _get_embedding_dim(self) -> int:
        """Get embedding dimension for nomic-embed-text (768)."""
        return 768

    def search(
        self,
        query: str,
        sources_hash: str,
        top_k: int = 3,
        threshold: float = 0.0
    ) -> List[dict]:
        """
        Search for relevant chunks in ChromaDB.
        
        Args:
            query: Search query text
            sources_hash: Hash to identify the collection
            top_k: Number of results to return
            threshold: Minimum similarity score (0.0-1.0)
            
        Returns:
            List of dicts with text, source, chunk_index, similarity
        """
        collection_name = self.get_collection_name(sources_hash)

        try:
            collection = self.client.get_collection(collection_name)
        except Exception as e:
            logger.warning(f"Collection {collection_name} not found: {e}")
            return []

        # Generate query embedding
        query_embedding = self._generate_embeddings([query])[0]

        # Search
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=['documents', 'metadatas', 'distances']
        )

        # Format results
        chunks = []
        if results and results.get('documents'):
            docs = results['documents'][0] if results['documents'] else []
            metas = results['metadatas'][0] if results.get('metadatas') else []
            distances = results.get('distances', [[]])[0] if results.get('distances') else []

            for doc, meta, dist in zip(docs, metas, distances):
                # ChromaDB returns L2 distance; convert to cosine similarity
                # For normalized embeddings, cosine_sim = 1 - (dist^2 / 2)
                similarity = max(0.0, 1.0 - (dist / 2.0))

                if similarity < threshold:
                    continue

                chunks.append({
                    'text': doc,
                    'source': meta.get('source', 'unknown'),
                    'chunk_index': meta.get('chunk_index', 0),
                    'word_count': meta.get('word_count', 0),
                    'similarity': round(similarity, 4)
                })

        # Sort by similarity descending
        chunks.sort(key=lambda x: x['similarity'], reverse=True)

        return chunks


# Module-level helper
def compute_sources_hash(source_files: List[str]) -> str:
    """
    Compute a hash from source file names and modification times.
    
    This is fast: doesn't read file contents, only metadata.
    If a file is modified, mtime changes → new hash → rebuild.
    """
    hash_input = []
    for f in sorted(source_files):
        mtime = os.path.getmtime(f) if os.path.exists(f) else 0
        hash_input.append(f"{f}:{mtime}")
    
    raw = '|'.join(hash_input)
    return hashlib.sha256(raw.encode()).hexdigest()
