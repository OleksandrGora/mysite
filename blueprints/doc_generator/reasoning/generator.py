"""
Generator - Creates document content using LLM.

Generates:
- Section text content
- Table data rows
- Maintains consistent tone and style
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
    MAX_TOKENS_PER_SECTION, MAX_TOKENS_FOR_TABLE,
    SECTION_GENERATION_PROMPT, TABLE_GENERATION_PROMPT,
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
    """Build prompt for section content generation."""
    
    title = section_plan.get('title', 'Untitled')
    content_type = section_plan.get('content_type', 'body')
    key_points = section_plan.get('key_points', [])
    tone = section_plan.get('tone', 'formal')
    max_words = section_plan.get('max_length_words', 200)
    relevant_sources = section_plan.get('source_references', [])
    
    # Build sources text
    sources_parts = []
    for src_name in relevant_sources:
        if src_name in sources_text:
            text = sources_text[src_name]
            # Truncate if too long
            if len(text) > 3000:
                text = text[:3000] + "..."
            sources_parts.append(f"=== {src_name} ===\n{text}\n")
    
    sources_block = '\n'.join(sources_parts) if sources_parts else "(джерела не вказані)"
    
    key_points_text = '\n'.join(f"- {kp}" for kp in key_points)
    
    prompt = SECTION_GENERATION_PROMPT.format(
        section_name=title,
        section_description=f"Тип: {content_type}, Тон: {tone}, Довжина: {max_words} слів",
        sources=sources_block
    )
    
    return prompt


def build_table_prompt(
    table_plan: Dict[str, Any],
    sources_text: Dict[str, str],
    document_context: Dict[str, Any]
) -> str:
    """Build prompt for table data generation."""
    
    purpose = table_plan.get('purpose', 'Table')
    columns = table_plan.get('columns', [])
    estimated_rows = table_plan.get('estimated_rows', 3)
    data_hint = table_plan.get('data_source_hint', 'all sources')
    
    # Get all sources if not specified
    sources_parts = []
    for name, text in sources_text.items():
        # Truncate
        if len(text) > 2000:
            text = text[:2000] + "..."
        sources_parts.append(f"=== {name} ===\n{text}\n")
    
    sources_block = '\n'.join(sources_parts)
    
    headers_text = ', '.join(columns) if columns else "(headers from template)"
    
    prompt = TABLE_GENERATION_PROMPT.format(
        table_name=purpose,
        columns=headers_text,
        sources=sources_block
    )
    
    return prompt


def generate_section_content(
    section_plan: Dict[str, Any],
    sources_text: Dict[str, str],
    document_context: Dict[str, Any],
    task_id: str = "unknown",
    user_prompt: Optional[str] = None
) -> GeneratedSection:
    """
    Generate content for a single section.
    
    Args:
        section_plan: Section plan from DocumentPlan
        sources_text: Dictionary of source name -> full text
        document_context: Document type, tone, etc.
        task_id: For logging
        
    Returns:
        GeneratedSection with content
    """
    logger.info(f"[{task_id}] Generating section: {section_plan.get('title')}")
    
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    
    prompt = build_section_prompt(section_plan, sources_text, document_context, user_prompt)
    
    try:
        if not client:
            raise ValueError("GEMINI_API_KEY not configured")
        
        response = client.models.generate_content(
            model=REASONING_MODEL,
            contents=prompt,
            config={"temperature": TEMPERATURE}
        )
        
        content = response.text.strip()
        
        # Save AI response for debugging
        save_ai_response(task_id, f"section_{section_plan.get('section_index', 'unknown')}", prompt, content)
        
        # Clean up common LLM artifacts
        if content.startswith('##') or content.startswith('**'):
            # Remove markdown headers
            lines = content.split('\n')
            while lines and (lines[0].startswith('#') or lines[0].startswith('*') or not lines[0].strip()):
                lines.pop(0)
            content = '\n'.join(lines).strip()
        
        word_count = len(content.split())
        
        result = GeneratedSection(
            title=section_plan.get('title', 'Untitled'),
            content=content,
            word_count=word_count,
            sources_used=section_plan.get('source_references', [])
        )
        
        logger.info(f"[{task_id}] Section generated: {word_count} words")
        
        # Force garbage collection to free memory
        gc.collect()
        
        return result
        
    except Exception as e:
        logger.error(f"[{task_id}] Section generation failed: {e}")
        gc.collect()
        # Return placeholder on error
        return GeneratedSection(
            title=section_plan.get('title', 'Untitled'),
            content=f"[Помилка генерації: {e}]",
            word_count=0,
            sources_used=[]
        )


def generate_table_data(
    table_plan: Dict[str, Any],
    sources_text: Dict[str, str],
    document_context: Dict[str, Any],
    task_id: str = "unknown"
) -> GeneratedTable:
    """
    Generate data rows for a table.
    
    Args:
        table_plan: Table plan from DocumentPlan
        sources_text: Dictionary of source name -> full text
        document_context: Document context
        task_id: For logging
        
    Returns:
        GeneratedTable with rows
    """
    logger.info(f"[{task_id}] Generating table #{table_plan.get('table_index')}")
    
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    
    prompt = build_table_prompt(table_plan, sources_text, document_context)
    
    try:
        if not client:
            raise ValueError("GEMINI_API_KEY not configured")
        
        response = client.models.generate_content(
            model=REASONING_MODEL,
            contents=prompt,
            config={"temperature": 0.1}  # Lower for structured data
        )
        
        # Parse JSON response
        response_text = response.text.strip()
        
        # Save AI response for debugging
        save_ai_response(task_id, f"table_{table_plan.get('table_index', 'unknown')}", prompt, response_text)
        
        # Clean markdown wrappers
        if response_text.startswith('```json'):
            response_text = response_text[7:]
        if response_text.startswith('```'):
            response_text = response_text[3:]
        if response_text.endswith('```'):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        logger.info(f"[{task_id}] Table response text: {response_text[:200]}...")
        
        # Check if response is empty or only contains headers
        if not response_text or response_text.lower() in ['[]', '[{}]', '[]']:
            logger.warning(f"[{task_id}] Table response is empty or contains only headers")
            rows = []
        else:
            try:
                rows = json.loads(response_text)
                logger.info(f"[{task_id}] Table JSON parsed successfully: {len(rows)} rows")
            except json.JSONDecodeError as e:
                logger.error(f"[{task_id}] Failed to parse table JSON: {e}")
                logger.error(f"[{task_id}] Response text: {response_text}")
                rows = []
        
        # Validate structure
        if not isinstance(rows, list):
            raise ValueError("Response is not a list")
        
        # Ensure all rows are lists
        cleaned_rows = []
        for row in rows:
            if isinstance(row, list):
                cleaned_rows.append([str(cell) for cell in row])
            elif isinstance(row, dict):
                # Convert dict to list based on column order
                cleaned_rows.append([str(row.get(col, '')) for col in table_plan.get('columns', [])])
        
        result = GeneratedTable(
            table_index=table_plan.get('table_index', 0),
            headers=table_plan.get('columns', []),
            rows=cleaned_rows,
            row_count=len(cleaned_rows)
        )
        
        logger.info(f"[{task_id}] Table generated: {len(cleaned_rows)} rows")
        
        # Force garbage collection to free memory
        gc.collect()
        
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"[{task_id}] Failed to parse table JSON: {e}")
        logger.error(f"Response: {response.text[:500] if 'response' in dir() else 'N/A'}")
        # Return empty table on error
        return GeneratedTable(
            table_index=table_plan.get('table_index', 0),
            headers=table_plan.get('columns', []),
            rows=[],
            row_count=0
        )
        
    except Exception as e:
        logger.error(f"[{task_id}] Table generation failed: {e}")
        return GeneratedTable(
            table_index=table_plan.get('table_index', 0),
            headers=table_plan.get('columns', []),
            rows=[],
            row_count=0
        )


def generate_all_content(
    document_plan: Dict[str, Any],
    sources_data: Dict[str, Dict[str, Any]],
    task_id: str = "unknown",
    progress_callback: Optional[callable] = None,
    user_prompt: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate all content for document.
    
    Args:
        document_plan: Full document plan
        sources_data: Sources with text
        task_id: For logging
        progress_callback: Called with (completed, total, message)
        
    Returns:
        Dictionary with all generated content
    """
    logger.info(f"[{task_id}] Starting full content generation...")
    
    # Extract sources text only
    sources_text = {name: data.get('text', '') for name, data in sources_data.items()}
    
    # Document context
    document_context = {
        'document_type': document_plan.get('document_type', 'document'),
        'overall_tone': document_plan.get('overall_tone', 'formal'),
        'document_title': document_plan.get('document_title', 'Untitled'),
    }
    
    # Generate sections
    sections = []
    section_plans = document_plan.get('sections', [])
    total_items = len(section_plans) + len(document_plan.get('tables', []))
    completed = 0
    
    for sec_plan in section_plans:
        if progress_callback:
            progress_callback(completed, total_items, f"Generating section: {sec_plan.get('title', 'Untitled')}")
        
        generated = generate_section_content(sec_plan, sources_text, document_context, task_id, user_prompt)
        sections.append({
            'title': generated.title,
            'content': generated.content,
            'word_count': generated.word_count,
            'plan': sec_plan
        })
        completed += 1
    
    # Generate tables
    tables = []
    for tbl_plan in document_plan.get('tables', []):
        if progress_callback:
            progress_callback(completed, total_items, f"Generating table #{tbl_plan.get('table_index')}")
        
        generated = generate_table_data(tbl_plan, sources_text, document_context, task_id)
        tables.append({
            'table_index': generated.table_index,
            'headers': generated.headers,
            'rows': generated.rows,
            'row_count': generated.row_count,
            'plan': tbl_plan
        })
        completed += 1
    
    result = {
        'document_title': document_context['document_title'],
        'document_type': document_context['document_type'],
        'sections': sections,
        'tables': tables,
        'total_word_count': sum(s['word_count'] for s in sections),
        'total_table_rows': sum(t['row_count'] for t in tables),
    }
    
    logger.success(f"[{task_id}] Content generation complete: {result['total_word_count']} words, {result['total_table_rows']} table rows")
    
    return result


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
