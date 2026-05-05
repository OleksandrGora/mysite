"""
Reasoning module for LLM-based document planning and generation.

NEW ARCHITECTURE (current):
- generate_document_full: One-pass LLM generation
- save_ai_response: Debug utilities

OLD ARCHITECTURE (moved to old_architecture/generator_old.py):
- generate_all_content: Section-by-section generation
- generate_section_content: Single section generation
- generate_table_data: Single table generation
- create_document_plan, DocumentPlan: Document planning
"""

from .generator import generate_document_full, save_ai_response

__all__ = [
    'generate_document_full',
    'save_ai_response',
]
