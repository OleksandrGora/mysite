"""
RAG (Retrieval-Augmented Generation) module for document generator.

Provides chunking, embedding (Ollama), vector search (ChromaDB),
and a complete pipeline for RAG-based document generation.
"""

from .chunker import chunk_documents, Chunk
from .embedder import Embedder
from .retriever import Retriever
from .query_builder import QueryBuilder
from .pipeline import generate_document_rag

__all__ = [
    'chunk_documents',
    'Chunk',
    'Embedder',
    'Retriever',
    'QueryBuilder',
    'generate_document_rag',
]
