"""
Generator - Creates document content using LLM.

Generates:
- Section text content
- Table data rows
- Maintains consistent tone and style
"""
import json
import gc
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from loguru import logger

from google import genai
from ..config import (
    GEMINI_API_KEY, REASONING_MODEL, TEMPERATURE,
    MAX_TOKENS_PER_SECTION, MAX_TOKENS_FOR_TABLE,
    SECTION_GENERATION_PROMPT, TABLE_GENERATION_PROMPT
)

# Initialize client
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


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
        table_name=table_name,
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
        
        # Clean markdown wrappers
        if response_text.startswith('```json'):
            response_text = response_text[7:]
        if response_text.startswith('```'):
            response_text = response_text[3:]
        if response_text.endswith('```'):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        rows = json.loads(response_text)
        
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


if __name__ == '__main__':
    print("Generator module - use through Celery task")
