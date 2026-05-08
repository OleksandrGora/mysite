"""
RAG (Retrieval-Augmented Generation) module for document generator.

Provides chunking, embedding (Ollama), vector search (ChromaDB),
and a complete pipeline for RAG-based document generation.

Two pipeline modes:
- generate_document_rag: Original mode (single global search)
- generate_document_rag_enhanced: Enhanced mode (per-section search, 
  parses structured prompt, falls back to original if no structure)
"""

from .chunker import chunk_documents, Chunk
from .embedder import Embedder
from .retriever import Retriever
from .query_builder import QueryBuilder
from .pipeline import generate_document_rag, generate_document_rag_enhanced
from .prompt_parser import (
    parse_full_doc_prompt,
    detect_structure,
    build_parent_context,
    ParsedPrompt
)

__all__ = [
    'chunk_documents',
    'Chunk',
    'Embedder',
    'Retriever',
    'QueryBuilder',
    'generate_document_rag',
    'generate_document_rag_enhanced',
    'parse_full_doc_prompt',
    'detect_structure',
    'build_parent_context',
    'ParsedPrompt',
]
