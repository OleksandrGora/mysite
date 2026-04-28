"""
RAG (Retrieval-Augmented Generation) module for large source documents.
"""
from .chunker import chunk_text
from .embedder import embed_chunks, get_chroma_collection
from .retriever import retrieve_relevant_chunks

__all__ = [
    'chunk_text',
    'embed_chunks',
    'get_chroma_collection',
    'retrieve_relevant_chunks',
]
