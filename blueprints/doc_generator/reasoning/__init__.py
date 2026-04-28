"""
Reasoning module for LLM-based document planning and generation.
"""
try:
    from .planner import create_document_plan, DocumentPlan
except ImportError:
    # planner.py may not exist - old architecture
    create_document_plan = None
    DocumentPlan = None

from .generator import generate_section_content, generate_table_data, generate_document_full

__all__ = [
    'create_document_plan',
    'DocumentPlan',
    'generate_section_content',
    'generate_table_data',
    'generate_document_full',
]
