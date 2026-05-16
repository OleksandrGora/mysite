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
from .hybrid_search import HybridSearch

__all__ = [
    'chunk_documents',
    'Chunk',
    'Embedder',
    'Retriever',
    'QueryBuilder',
    'HybridSearch',
    'Anonymizer',
    'get_anonymizer',
    'generate_document_rag',
    'generate_document_rag_enhanced',
    'parse_full_doc_prompt',
    'detect_structure',
    'build_parent_context',
    'ParsedPrompt',
]


# Anonymizer requires spacy, which may not be installed.
# Import it lazily so the rest of the rag module can be loaded without spacy.
def _lazy_anonymizer():
    from .anonymizer import Anonymizer, get_anonymizer
    return Anonymizer, get_anonymizer
