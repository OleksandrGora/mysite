"""
Embedder - Creates vector embeddings using Gemini and stores in ChromaDB.
"""
import os
import hashlib
from typing import List, Dict, Any
from loguru import logger

import chromadb
from chromadb.config import Settings

# Gemini embeddings
import google.generativeai as genai
from ..config import GEMINI_API_KEY, EMBEDDING_MODEL

# Configure Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ChromaDB settings
CHROMA_DB_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'chroma_db')


def get_chroma_client():
    """Get or create ChromaDB client."""
    os.makedirs(CHROMA_DB_DIR, exist_ok=True)
    
    client = chromadb.Client(Settings(
        persist_directory=CHROMA_DB_DIR,
        anonymized_telemetry=False
    ))
    
    return client


def get_chroma_collection(task_id: str):
    """Get collection for a specific task."""
    client = get_chroma_client()
    
    # Use task-specific collection
    collection_name = f"task_{task_id}"
    
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"task_id": task_id}
    )
    
    return collection


def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings for texts using Gemini.
    
    Args:
        texts: List of text strings
        
    Returns:
        List of embedding vectors
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    
    if not texts:
        return []
    
    model = EMBEDDING_MODEL  # 'models/text-embedding-004'
    
    # Gemini has batch limit, process in batches
    batch_size = 100
    all_embeddings = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        
        try:
            result = genai.embed_content(
                model=model,
                content=batch,
                task_type="retrieval_document"
            )
            
            # Extract embeddings
            embeddings = result.get('embedding', [])
            if embeddings:
                all_embeddings.extend(embeddings)
            else:
                logger.warning(f"No embeddings returned for batch {i}")
                # Return zero vectors as fallback
                dim = 768  # Gemini embedding dimension
                all_embeddings.extend([[0.0] * dim] * len(batch))
                
        except Exception as e:
            logger.error(f"Embedding generation failed for batch {i}: {e}")
            # Return zero vectors as fallback
            dim = 768
            all_embeddings.extend([[0.0] * dim] * len(batch))
    
    return all_embeddings


def embed_chunks(
    chunks: List[Dict[str, Any]],
    task_id: str,
    source_name: str = None
) -> int:
    """
    Generate embeddings for chunks and store in ChromaDB.
    
    Args:
        chunks: List of chunk dicts with 'text' and metadata
        task_id: Task ID for collection
        source_name: Source document name (for logging)
        
    Returns:
        Number of chunks embedded
    """
    if not chunks:
        return 0
    
    logger.info(f"[{task_id}] Embedding {len(chunks)} chunks from {source_name or 'unknown'}")
    
    # Get collection
    collection = get_chroma_collection(task_id)
    
    # Generate embeddings
    texts = [c['text'] for c in chunks]
    embeddings = generate_embeddings(texts)
    
    # Prepare for ChromaDB
    ids = []
    metadatas = []
    documents = []
    
    for i, chunk in enumerate(chunks):
        # Create unique ID
        chunk_hash = hashlib.md5(chunk['text'].encode()).hexdigest()[:16]
        chunk_id = f"{source_name or 'chunk'}_{i}_{chunk_hash}"
        
        ids.append(chunk_id)
        metadatas.append({
            'source': chunk.get('source', 'unknown'),
            'chunkIndex': chunk.get('chunkIndex', i),
            'totalChunks': chunk.get('totalChunks', len(chunks)),
            'charCount': chunk.get('charCount', len(chunk['text']))
        })
        documents.append(chunk['text'])
    
    # Add to collection
    collection.add(
        ids=ids,
        embeddings=embeddings,
        metadatas=metadatas,
        documents=documents
    )
    
    logger.info(f"[{task_id}] Embedded {len(chunks)} chunks into ChromaDB")
    
    return len(chunks)


def embed_all_sources(
    sources_data: Dict[str, Dict[str, Any]],
    task_id: str,
    chunk_size: int = 800,
    overlap: int = 150
) -> int:
    """
    Embed all source documents for a task.
    
    Args:
        sources_data: Dict of source_name -> {text, ...}
        task_id: Task ID
        chunk_size: Chunk size
        overlap: Chunk overlap
        
    Returns:
        Total chunks embedded
    """
    from .chunker import chunk_source_document
    
    total_chunks = 0
    
    for source_name, data in sources_data.items():
        text = data.get('text', '')
        if not text:
            continue
        
        # Chunk
        chunks = chunk_source_document(source_name, text, chunk_size, overlap)
        
        # Embed
        count = embed_chunks(chunks, task_id, source_name)
        total_chunks += count
    
    return total_chunks


def should_use_rag(sources_data: Dict[str, Dict[str, Any]], threshold_chars: int = 100000) -> bool:
    """
    Determine if RAG should be used based on total text size.
    
    Args:
        sources_data: Sources with text
        threshold_chars: Threshold for using RAG (default 100K chars ~ 25K tokens)
        
    Returns:
        True if RAG should be used
    """
    total_chars = sum(d.get('char_count', 0) for d in sources_data.values())
    
    return total_chars > threshold_chars


if __name__ == '__main__':
    print("Embedder module - use through Celery task")
