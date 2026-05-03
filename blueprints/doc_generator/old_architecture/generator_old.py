"""
OLD ARCHITECTURE - Section-by-section document generator.

This module contains the old document generation approach that:
1. Generated each section separately using LLM calls
2. Generated each table separately
3. Used multiple LLM calls (one per section/table)

REPLACED BY: generate_document_full() in reasoning/generator.py
New approach uses one-pass LLM generation for entire document.
"""

import json
import gc
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path
from pydantic import BaseModel

# These imports would need to be adjusted when using from old_architecture
# import google.genai as genai
# from ..config import (
#     GEMINI_API_KEY, REASONING_MODEL, TEMPERATURE,
#     MAX_TOKENS_PER_SECTION, MAX_TOKENS_FOR_TABLE,
#     SECTION_GENERATION_PROMPT, TABLE_GENERATION_PROMPT,
# )
from loguru import logger


class GeneratedSection(BaseModel):
    """Result of section generation."""
    title: str
    content: str
    word_count: int
    sources_used: List[str]


class GeneratedTable(BaseModel):
    """Result of table generation."""
    table_index: int
    headers: List[str]
    rows: List[List[str]]  # Each inner list is a row of cell values
    row_count: int


def build_section_prompt(
    section_plan: Dict[str, Any],
    sources_text: Dict[str, str],
    document_context: Dict[str, Any],
    user_prompt: Optional[str] = None
) -> str:
    """
    OLD ARCHITECTURE - Build prompt for section content generation.
    
    Part of the section-by-section generation pipeline.
    
    REPLACED BY: Direct structure building in generate_document_full()
    """
    pass  # Implementation moved from generator.py


def build_table_prompt(
    table_plan: Dict[str, Any],
    sources_text: Dict[str, str],
    document_context: Dict[str, Any]
) -> str:
    """
    OLD ARCHITECTURE - Build prompt for table data generation.
    
    Part of the section-by-section generation pipeline.
    
    REPLACED BY: Direct table structure in generate_document_full()
    """
    pass  # Implementation moved from generator.py


def generate_section_content(
    section_plan: Dict[str, Any],
    sources_text: Dict[str, str],
    document_context: Dict[str, Any],
    task_id: str = "unknown",
    user_prompt: Optional[str] = None
) -> GeneratedSection:
    """
    OLD ARCHITECTURE - Generate content for a single section.
    
    Called by generate_all_content() for each section in the document plan.
    Made separate LLM call for each section.
    
    REPLACED BY: generate_document_full() - generates entire document in one call
    """
    pass  # Implementation moved from generator.py


def generate_table_data(
    table_plan: Dict[str, Any],
    sources_text: Dict[str, str],
    document_context: Dict[str, Any],
    task_id: str = "unknown"
) -> GeneratedTable:
    """
    OLD ARCHITECTURE - Generate data rows for a table.
    
    Called by generate_all_content() for each table in the document plan.
    Made separate LLM call for each table.
    
    REPLACED BY: generate_document_full() - generates entire document in one call
    """
    pass  # Implementation moved from generator.py


def generate_all_content(
    document_plan: Dict[str, Any],
    sources_data: Dict[str, Dict[str, Any]],
    task_id: str = "unknown",
    progress_callback: Optional[callable] = None,
    user_prompt: Optional[str] = None
) -> Dict[str, Any]:
    """
    OLD ARCHITECTURE - Generate all content for document section-by-section.
    
    This was the main generation function for the old workflow that:
    1. Looped through all sections and called generate_section_content for each
    2. Looped through all tables and called generate_table_data for each
    3. Made multiple LLM calls (N sections + M tables calls)
    4. Used progress_callback to report progress
    
    Issues with this approach:
    - Slow: Multiple LLM calls (one per section/table)
    - Expensive: Each call costs tokens
    - Inconsistent: Each section generated independently
    - Rate limiting: Many API calls risk hitting rate limits
    
    REPLACED BY: generate_document_full() in reasoning/generator.py
    New approach:
    - One LLM call for entire document
    - Faster (single API call)
    - Cheaper (one context setup)
    - More consistent (full context visible to LLM)
    - Better for rate limits
    
    Called by: process_document_task (old Celery task, now in routes_old.py)
    """
    pass  # Implementation moved from generator.py
