"""
Generator - Creates document content using LLM.

Uses one-pass generation for entire document (NEW ARCHITECTURE).

OLD ARCHITECTURE functions (section-by-section generation) moved to:
- old_architecture/generator_old.py
- Functions: generate_all_content, generate_section_content, generate_table_data
"""
import json
import gc
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path
from pydantic import BaseModel
import google.genai as genai
from ..config import (
    GEMINI_API_KEY, REASONING_MODEL, TEMPERATURE,
    FULL_DOCUMENT_GENERATION_PROMPT
)
from loguru import logger

# Initialize Gemini client
client = None
if GEMINI_API_KEY:
    try:
        # For new google-genai package
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Gemini client: {e}")
else:
    logger.warning("GEMINI_API_KEY not configured")


def save_ai_response(task_id: str, response_type: str, prompt: str, response: str, task_folder: str = None):
    """
    Зберегти AI відповідь в папку завдання для відлагодження.
    
    Args:
        task_id: ID завдання
        response_type: Тип відповіді (section_N, table_N, full_document, etc.)
        prompt: Промпт що відправився до AI
        response: Відповідь від AI
        task_folder: Папка завдання (якщо є)
    """
    # Debug info: input parameters
    logger.info(f"[DEBUG] Saving AI response for task: {task_id}")
    logger.info(f"[DEBUG] Response type: {response_type}")
    logger.info(f"[DEBUG] Task folder: {task_folder}")
    logger.info(f"[DEBUG] Prompt length: {len(prompt)} chars")
    logger.info(f"[DEBUG] Response length: {len(response)} chars")
    
    # Визначити базову папку для збереження
    if task_folder:
        base_dir = task_folder
        logger.info(f"[DEBUG] Using task folder as base dir: {base_dir}")
    else:
        # Якщо папка завдання не вказана, зберігати в temp
        base_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'temp')
        os.makedirs(base_dir, exist_ok=True)
        logger.info(f"[DEBUG] Using temp folder as base dir: {base_dir}")
    
    # Створити папку для AI відповідей
    ai_responses_dir = os.path.join(base_dir, 'ai_responses')
    os.makedirs(ai_responses_dir, exist_ok=True)
    logger.info(f"[DEBUG] AI responses directory: {ai_responses_dir}")
    logger.info(f"[DEBUG] AI responses dir exists: {os.path.exists(ai_responses_dir)}")
    
    # Створити папку для конкретного типу відповіді
    type_dir = os.path.join(ai_responses_dir, response_type)
    os.makedirs(type_dir, exist_ok=True)
    logger.info(f"[DEBUG] Type directory: {type_dir}")
    logger.info(f"[DEBUG] Type dir exists: {os.path.exists(type_dir)}")
    
    try:
        # Зберегти промпт
        prompt_filename = f"{task_id}_{response_type}_prompt.txt"
        prompt_path = os.path.join(type_dir, prompt_filename)
        logger.info(f"[DEBUG] Saving prompt to: {prompt_path}")
        
        with open(prompt_path, 'w', encoding='utf-8') as f:
            f.write(f"TASK ID: {task_id}\n")
            f.write(f"TYPE: {response_type}\n")
            f.write(f"MODEL: {REASONING_MODEL}\n")
            f.write(f"TIMESTAMP: {os.path.getmtime(__file__)}\n")
            f.write("="*50 + "\n")
            f.write(prompt)
        
        # Зберегти відповідь
        response_filename = f"{task_id}_{response_type}_response.txt"
        response_path = os.path.join(type_dir, response_filename)
        logger.info(f"[DEBUG] Saving response to: {response_path}")
        
        with open(response_path, 'w', encoding='utf-8') as f:
            f.write(f"TASK ID: {task_id}\n")
            f.write(f"TYPE: {response_type}\n")
            f.write(f"MODEL: {REASONING_MODEL}\n")
            f.write(f"TIMESTAMP: {os.path.getmtime(__file__)}\n")
            f.write("="*50 + "\n")
            f.write(response)
        
        # Debug info: files saved successfully
        prompt_size = os.path.getsize(prompt_path)
        response_size = os.path.getsize(response_path)
        logger.info(f"[DEBUG] Prompt file saved: {prompt_size} bytes")
        logger.info(f"[DEBUG] Response file saved: {response_size} bytes")
        logger.info(f"[DEBUG] Files exist after save: {os.path.exists(prompt_path)}, {os.path.exists(response_path)}")
        
        logger.info(f"AI response saved: {response_path}")
        
    except Exception as e:
        logger.error(f"[DEBUG] Failed to save AI response: {e}")
        logger.error(f"[DEBUG] Prompt path: {prompt_path}")
        logger.error(f"[DEBUG] Response path: {response_path}")
        raise


def load_ai_response(task_id: str, response_type: str, task_folder: str = None) -> Optional[str]:
    """
    Завантажити збережену AI відповідь для відлагодження.
    
    Args:
        task_id: ID завдання
        response_type: Тип відповіді (section, table, full_document)
        task_folder: Шлях до папки завдання (якщо є)
        
    Returns:
        Збережена відповідь або None
    """
    # Debug info: input parameters
    logger.info(f"[DEBUG] Loading AI response for task: {task_id}")
    logger.info(f"[DEBUG] Response type: {response_type}")
    logger.info(f"[DEBUG] Task folder: {task_folder}")
    
    try:
        # Визначити папку для пошуку
        if task_folder and os.path.exists(task_folder):
            base_dir = Path(task_folder)
            logger.info(f"[DEBUG] Using task folder as base dir: {base_dir}")
        else:
            base_dir = Path("/home/openclaw/mysite/blueprints/doc_generator/temp/ai_responses")
            logger.info(f"[DEBUG] Using default temp folder as base dir: {base_dir}")
        
        response_file = base_dir / "ai_responses" / response_type / f"{task_id}_{response_type}_response.txt"
        logger.info(f"[DEBUG] Looking for response file: {response_file}")
        logger.info(f"[DEBUG] Response file exists: {response_file.exists()}")
        
        if response_file.exists():
            logger.info(f"[DEBUG] Response file found, loading content...")
            with open(response_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Debug info: file loaded successfully
            file_size = response_file.stat().st_size
            logger.info(f"[DEBUG] Response file loaded: {file_size} bytes")
            logger.info(f"[DEBUG] Content length: {len(content)} chars")
            logger.info(f"[DEBUG] Content preview: {content[:100]}...")
            
            # Видалити заголовок
            if "="*50 in content:
                cleaned_content = content.split("="*50, 1)[1].strip()
                logger.info(f"[DEBUG] Content cleaned (removed header)")
                return cleaned_content
            
            logger.info(f"[DEBUG] Content returned as-is (no header found)")
            return content.strip()
        
        logger.warning(f"[DEBUG] Response file not found: {response_file}")
        return None
        
    except Exception as e:
        logger.warning(f"[{task_id}] Failed to load AI response: {e}")
        return None

# NOTE: Old architecture functions moved to old_architecture/generator_old.py:
# - class GeneratedSection
# - class GeneratedTable  
# - def build_section_prompt
# - def build_table_prompt
# - def generate_section_content
# - def generate_table_data
# - def generate_all_content


def generate_document_full(
    analysis_text: str,
    sources_data: Dict[str, Dict[str, Any]],
    task_id: str = "unknown",
    user_prompt: Optional[str] = None,
    task_folder: Optional[str] = None
) -> str:
    """
    Generate full document in one pass using LLM.
    
    Args:
        analysis_text: Parsed analysis text
        sources_data: Dictionary of source name -> parsed source data
        task_id: For logging
        user_prompt: Optional user prompt for generation
        task_folder: Optional task folder path for saving AI responses
        
    Returns:
        Generated markdown text
    """
    logger.info(f"[{task_id}] Starting full document generation (one-pass)...")
    logger.info(f"[{task_id}] Using only analysis.txt data, no test data generation")
    
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    
    # Parse analysis text to get structure
    from ..parsers.analysis_parser import parse_analysis
    parsed_analysis = parse_analysis(analysis_text)
    
    logger.info(f"[{task_id}] Parsed analysis: {len(parsed_analysis.sections)} sections, {len(parsed_analysis.tables)} tables")
    
    # Log table details for debugging
    for i, table in enumerate(parsed_analysis.tables):
        logger.info(f"[{task_id}] Table {i+1}: {table.title} with {len(table.columns)} columns")
        if not table.columns:
            logger.warning(f"[{task_id}] Table {i+1} has no columns - will generate empty table")
        else:
            logger.info(f"[{task_id}] Table {i+1} columns: {', '.join(table.columns)}")
    
    # Build structure description for prompt
    structure_lines = []
    for section in parsed_analysis.sections:
        prefix = '#' * section.level
        structure_lines.append(f"{prefix} {section.title}")
        if section.has_text:
            if section.description_general:
                structure_lines.append(f"  Опис (що написати): {section.description_general}")
            if section.description_specific:
                structure_lines.append(f"  Приклад стилю: {section.description_specific}")
    
    for table in parsed_analysis.tables:
        prefix = '#' * table.level
        structure_lines.append(f"{prefix} {table.title}")
        if table.columns:
            structure_lines.append(f"  Колонки: {', '.join(table.columns)}")
    
    structure_text = '\n'.join(structure_lines)
    
    # Build sources text
    sources_parts = []
    for name, data in sources_data.items():
        text = data.get('text', '')
        # Truncate if too long
        if len(text) > 8000:
            text = text[:8000] + "\n... (текст обрізано через обмеження довжини)"
        sources_parts.append(f"=== {name} ===\n{text}\n")
    
    sources_block = '\n'.join(sources_parts) if sources_parts else "(джерела не вказані)"
    
    # Build prompt
    prompt = FULL_DOCUMENT_GENERATION_PROMPT.format(
        structure=structure_text,
        sources=sources_block
    )
    
    # Add user prompt if provided
    if user_prompt:
        prompt += f"\n\n## ДОДАТКОВІ ВИМОГИ\n\n{user_prompt}"
    
    import time
    
    # Retry logic for 503 errors
    max_retries = 3
    retry_delays = [10, 20, 30]  # seconds
    
    for attempt in range(max_retries):
        try:
            # Save prompt before calling AI (for debugging, even if AI fails)
            if attempt == 0:
                logger.info(f"[DEBUG] Saving prompt before AI call for full_document")
                save_ai_response(task_id, "full_document_prompt", prompt, "[PENDING - AI call in progress]", task_folder)
            
            logger.info(f"[{task_id}] Sending request to Gemini (спроба {attempt + 1}/{max_retries})...")
            response = client.models.generate_content(
                model=REASONING_MODEL,
                contents=prompt,
                config={"temperature": TEMPERATURE}
            )
            
            markdown_text = response.text.strip()
            
            # Save AI response for debugging
            logger.info(f"[DEBUG] About to save AI response for full_document")
            logger.info(f"[DEBUG] Task folder: {task_folder}")
            logger.info(f"[DEBUG] Task ID: {task_id}")
            logger.info(f"[DEBUG] Response type: full_document")
            logger.info(f"[DEBUG] Markdown text length: {len(markdown_text)}")
            save_ai_response(task_id, "full_document", prompt, markdown_text, task_folder)
            logger.info(f"[DEBUG] AI response saved for full_document")
            
            # Clean up common LLM artifacts
            if markdown_text.startswith('```'):
                # Remove markdown code block wrapper
                lines = markdown_text.split('\n')
                if lines[0].startswith('```'):
                    lines.pop(0)
                if lines and lines[-1].startswith('```'):
                    lines.pop()
                markdown_text = '\n'.join(lines).strip()
            
            logger.success(f"[{task_id}] Document generated: {len(markdown_text)} chars")
            
            # Force garbage collection
            gc.collect()
            
            return markdown_text
            
        except Exception as e:
            error_str = str(e)
            is_503_error = '503' in error_str or 'UNAVAILABLE' in error_str
            
            # Save error for debugging
            logger.info(f"[DEBUG] Saving AI error response for full_document_error")
            error_response = f"ERROR: {error_str}\n\nATTEMPT: {attempt + 1}/{max_retries}\n\nIS_503: {is_503_error}"
            save_ai_response(task_id, f"full_document_error_attempt_{attempt + 1}", prompt, error_response, task_folder)
            logger.info(f"[DEBUG] AI error response saved")
            
            if is_503_error and attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.warning(f"[{task_id}] 503 error, retrying after {delay}s... (спроба {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            else:
                logger.error(f"[{task_id}] Full document generation failed: {e}")
                gc.collect()
                raise


if __name__ == '__main__':
    print("Generator module - use through Celery task")
