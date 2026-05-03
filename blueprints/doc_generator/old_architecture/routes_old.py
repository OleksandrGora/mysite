"""
OLD ARCHITECTURE - Deprecated routes and functions.

This module contains the old document generation workflow that has been replaced
by the new task-based architecture.

The old workflow:
1. POST /api/tasks (create_task) - Upload template + sources
2. Celery: process_document_task - Parse, plan, generate, build
   - Used planner.py for document planning
   - Used generate_all_content for section-by-section generation
   - Used RAG for large documents

The new workflow:
1. POST /api/task - Create task
2. POST /api/tasks/<id>/analysis - Analyze template (Celery)
3. POST /api/tasks/<id>/save_analysis - Save edited analysis
4. POST /api/tasks/<id>/generate - Generate document (Celery)
"""

import os
import sys
import json
import uuid
import time
import shutil
import traceback
from datetime import datetime
from flask import request, jsonify, render_template, current_app, send_file, session, redirect, url_for
from loguru import logger
from werkzeug.utils import secure_filename
import re
from celery import shared_task
from functools import wraps

# These imports need to be adjusted when moving to old_architecture
# from .. import doc_generator_bp
# from ..parsers.template_parser import parse_template
# from ..parsers.source_parser import parse_sources_with_metadata
# from ..reasoning.generator import generate_all_content
# from ..builders import build_document, build_document_simple
# from ..config import USE_RAG, ANALYSIS_PROMPT, LLM_ANALYSIS_PROMPT
# from ..task_manager import TASKS_DATA_DIR, TEMPLATES_DIR, SOURCES_DIR

# ============================================================================
# OLD CELERY TASK - Full document processing pipeline
# ============================================================================

@shared_task(bind=True)
def process_document_task(self, task_id, template_path, sources_paths, data_dir, user_prompt=None):
    """
    OLD ARCHITECTURE - Process document generation task.
    
    This was the main Celery task for the old workflow that:
    1. Parsed template structure
    2. Parsed sources
    3. Created document plan using planner.py
    4. Generated content section-by-section using generate_all_content
    5. Built final DOCX
    
    REPLACED BY: New 3-stage workflow in routes.py
    - analyze_document_task
    - generate_final_document_task
    
    NOTE: This function has a bug on line ~248 where 'folder_path' is used
    but 'task_dir' should be used instead.
    """
    from loguru import logger
    
    # These imports would need to be adjusted for old_architecture
    # from ..reasoning.planner import create_document_plan, save_plan
    # from ..rag.embedder import embed_all_sources, should_use_rag
    # from ..rag.retriever import retrieve_for_section, format_chunks_for_llm
    
    task_dir = os.path.join(data_dir, 'tasks', task_id)
    task_json_path = os.path.join(task_dir, 'task.json')
    
    def update_status(status, progress=None, message=None, error=None, extra=None):
        task_data = {
            'task_id': task_id,
            'status': status,
            'created_at': datetime.utcnow().isoformat(),
        }
        if progress is not None:
            task_data['progress'] = progress
        if message:
            task_data['message'] = message
        if error:
            task_data['error'] = error
        if extra:
            task_data.update(extra)
        if status == 'completed':
            task_data['completed_at'] = datetime.utcnow().isoformat()
        
        with open(task_json_path, 'w') as f:
            json.dump(task_data, f, indent=2)
        
        logger.info(f"Task {task_id}: {status} ({progress}%) - {message}")
    
    try:
        # Step 1: Parse template (10%)
        update_status('processing', progress=5, message='Parsing template structure...')
        
        # template_structure = parse_template(template_path)
        # ... rest of old implementation
        
        logger.warning(f"[{task_id}] OLD architecture task called - should use new workflow")
        update_status('failed', error="Old architecture deprecated. Use new workflow.")
        
    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        update_status('failed', error=str(e))
        raise


# ============================================================================
# OLD API ENDPOINTS
# ============================================================================

# @doc_generator_bp.route('/api/tasks', methods=['POST'])
# @login_required
def create_task():
    """
    OLD ARCHITECTURE - Create new task with template + sources upload.
    
    This endpoint:
    1. Accepted template file + source files in one request
    2. Saved files to task-specific directory
    3. Queued process_document_task Celery task
    
    REPLACED BY: 
    - POST /api/task (create_new_task) - Just creates task structure
    - File uploads go to shared Templates/Sources directories
    """
    pass  # Implementation moved from routes.py


# @doc_generator_bp.route('/api/analyze-template', methods=['POST'])
# @login_required
def analyze_template():
    """
    OLD ARCHITECTURE - Analyze template file and return structure.
    
    This endpoint was used by the old frontend (index_old.html) to get
    template structure before creating a task.
    
    REPLACED BY: 
    - POST /api/tasks/<id>/analysis - Celery task that saves analysis to task folder
    - Analysis is now part of the 3-stage workflow
    """
    pass  # Implementation moved from routes.py


# ============================================================================
# OLD FILE UPLOAD FUNCTION
# ============================================================================

def handle_file_upload(file, upload_type: str):
    """
    OLD ARCHITECTURE - Handle file upload with uniqueness check.
    
    This was used by the old upload endpoints. The new architecture
    uses direct file saving to Templates/Sources directories.
    
    upload_type: 'template' or 'source'
    Returns: {"status": "success|exists|copied", "path": "...", "filename": "..."}
    
    REPLACED BY: Direct file operations in new endpoints
    """
    pass  # Implementation moved from task_manager.py


# ============================================================================
# OLD REASONING/GENERATOR FUNCTIONS
# ============================================================================

def generate_all_content(document_plan, sources_data, task_id="unknown", 
                         progress_callback=None, user_prompt=None):
    """
    OLD ARCHITECTURE - Generate all content for document section-by-section.
    
    This function:
    1. Generated each section separately using generate_section_content
    2. Generated each table separately using generate_table_data
    3. Called LLM multiple times (once per section/table)
    
    REPLACED BY: generate_document_full() - One-pass LLM generation
    """
    pass  # Implementation moved from generator.py


def generate_section_content(section_plan, sources_text, document_context, 
                             task_id="unknown", user_prompt=None):
    """
    OLD ARCHITECTURE - Generate content for a single section.
    
    Part of generate_all_content pipeline.
    """
    pass  # Implementation moved from generator.py


def generate_table_data(table_plan, sources_text, document_context, task_id="unknown"):
    """
    OLD ARCHITECTURE - Generate data rows for a table.
    
    Part of generate_all_content pipeline.
    """
    pass  # Implementation moved from generator.py


def build_section_prompt(section_plan, sources_text, document_context, user_prompt=None):
    """
    OLD ARCHITECTURE - Build prompt for section content generation.
    
    Helper for generate_section_content.
    """
    pass  # Implementation moved from generator.py


def build_table_prompt(table_plan, sources_text, document_context):
    """
    OLD ARCHITECTURE - Build prompt for table data generation.
    
    Helper for generate_table_data.
    """
    pass  # Implementation moved from generator.py


# ============================================================================
# OLD CONFIG PROMPTS
# ============================================================================

# Old prompts that are no longer used in new architecture:
PLANNER_ANALYSIS_PROMPT_OLD = """Ти — експерт з аналізу структури документів.

Проаналізуй структуру шаблону та джерел.

ШАБЛОН:
{template_structure}

ДЖЕРЕЛА:
{sources_structure}

Визнач які секції та таблиці потрібно заповнити даними з джерел."""

PLANNER_GENERATION_PROMPT_OLD = """Ти — експерт з аналізу документів і створення планів генерації.

Твоє завдання: проаналізувати шаблон документа та вміст джерел, 
і створити детальний план для генерації нового документа.

## ШАБЛОН ДОКУМЕНТА

Заголовок шаблону: {template_title}

Секції шаблону (рівень вкладеності [X]):
{sections_text}

Таблиці в шаблоні:
{tables_text}

## ДЖЕРЕЛА ДАНИХ

Загальний обсяг тексту: {total_chars} символів

Джерела:
{sources_text}

## ЗАВДАННЯ

Створи JSON план (DocumentPlan) який визначає:
1. **document_title** — згенеруй відповідну назву документа
2. **document_type** — тип документа (report, letter, act, contract, etc.)
3. **target_audience** — хто буде читати цей документ
4. **overall_tone** — загальний тон (formal, business, technical, legal)
5. **sections** — для кожної секції шаблону
6. **tables** — для кожної таблиці
7. **sources_summary** — короткий опис кожного джерела

ВАЖЛИВО:
- Всі тексти мають бути українською мовою (діловий стиль)
- План має бути реалістичним
- Поверни ТІЛЬКИ JSON, без додаткових пояснень"""

SECTION_GENERATION_PROMPT_OLD = """Відтвори текст секції документа на основі наданих джерел українською мовою.

СЕКЦІЯ: {section_name}
ОПИС: {section_description}

ДЖЕРЕЛА:
{sources}

Вимоги:
- Використовуй тільки інформацію з джерел
- Зберігай структуру та стиль оригінального документа
- Якщо в джерелах немає інформації — напиши "Дані відсутні"
- Відповідь українською мовою"""

TABLE_GENERATION_PROMPT_OLD = """Згенеруй дані для таблиці на основі джерел.

НАЗВА ТАБЛИЦІ: {table_name}
СТОВПЦІ: {columns}

ДЖЕРЕЛА:
{sources}

Вимоги:
- Заповни таблицю даними з джерел
- Якщо даних немає — залиште порожнім або напишіть "—"
- Відповідь українською мовою"""

LLM_ANALYSIS_PROMPT_OLD = """Проаналізуй структуру шаблону та джерел.

Шаблон має {num_sections} секцій та {num_tables} таблиць.

Користувацький запит: {user_prompt}

Поверни детальний план заповнення кожної секції та таблиці даними з джерел."""
