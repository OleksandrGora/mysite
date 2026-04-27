"""
Retriever - Searches for relevant chunks in ChromaDB.
"""
from typing import List, Dict, Any
from loguru import logger

import google.generativeai as genai
from .embedder import get_chroma_collection, EMBEDDING_MODEL


def generate_query_embedding(query: str) -> List[float]:
    """Generate embedding for a query."""
    from ..config import GEMINI_API_KEY
    
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=query,
        task_type="retrieval_query"
    )
    
    return result.get('embedding', [])


def retrieve_relevant_chunks(
    query: str,
    task_id: str,
    n_results: int = 5,
    min_similarity: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Retrieve most relevant chunks for a query.
    
    Args:
        query: Search query (section title/description)
        task_id: Task collection
        n_results: Number of chunks to retrieve
        min_similarity: Minimum similarity score (0-1, optional)
        
    Returns:
        List of chunk dicts with text and metadata
    """
    collection = get_chroma_collection(task_id)
    
    # Generate query embedding
    query_embedding = generate_query_embedding(query)
    
    # Query ChromaDB
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=['documents', 'metadatas', 'distances']
    )
    
    # Format results
    chunks = []
    
    if results and results.get('documents'):
        docs = results['documents'][0] if results['documents'] else []
        metas = results['metadatas'][0] if results.get('metadatas') else []
        distances = results['distances'][0] if results.get('distances') else []
        
        for doc, meta, dist in zip(docs, metas, distances):
            # Convert distance to similarity (ChromaDB uses cosine distance)
            similarity = 1 - (dist / 2) if dist else 0
            
            if min_similarity and similarity < min_similarity:
                continue
            
            chunks.append({
                'text': doc,
                'source': meta.get('source', 'unknown'),
                'chunkIndex': meta.get('chunkIndex', 0),
                'similarity': similarity
            })
    
    logger.debug(f"Retrieved {len(chunks)} chunks for query: {query[:50]}...")
    
    return chunks


def retrieve_for_section(
    section_title: str,
    section_content_type: str,
    key_points: List[str],
    task_id: str,
    n_results: int = 5
) -> List[Dict[str, Any]]:
    """
    Retrieve relevant chunks for a document section.
    
    Builds a comprehensive query from section metadata.
    """
    # Build query from section info
    query_parts = [section_title]
    
    if key_points:
        query_parts.extend(key_points[:3])  # Top 3 key points
    
    if section_content_type:
        query_parts.append(section_content_type)
    
    query = " | ".join(query_parts)
    
    return retrieve_relevant_chunks(query, task_id, n_results)


def format_chunks_for_llm(chunks: List[Dict[str, Any]]) -> str:
    """
    Format retrieved chunks for inclusion in LLM prompt.
    """
    if not chunks:
        return "(no relevant chunks found)"
    
    parts = []
    for i, chunk in enumerate(chunks, 1):
        source = chunk.get('source', 'unknown')
        text = chunk.get('text', '')
        sim = chunk.get('similarity', 0)
        
        parts.append(f"[Excerpt {i} from {source} | relevance: {sim:.2f}]\n{text}\n")
    
    return "\n---\n".join(parts)


if __name__ == '__main__':
    print("Retriever module - use through Celery task")
