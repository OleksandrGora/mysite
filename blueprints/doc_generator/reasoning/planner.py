"""
Planner - Creates document generation plan using LLM.

Analyzes template structure and source texts to create a plan
for filling each section and table with appropriate content.
"""
import json
import os
import gc
import time
import fitz  # PyMuPDF for PDF text extraction
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field
from loguru import logger

from google import genai
from ..config import GEMINI_API_KEY, REASONING_MODEL, TEMPERATURE

# Initialize client
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def analyze_sections_with_llm(template_path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Use LLM to analyze document template and extract sections.

    For large documents, splits text into chunks and processes each chunk separately.

    Args:
        template_path: Path to template file (DOCX or PDF)

    Returns:
        Tuple of (sections list, chunk processing info dict)
    """
    logger.info(f"Analyzing sections with LLM: {template_path}")
    
    # Extract text from template
    if template_path.lower().endswith('.pdf'):
        doc = fitz.open(template_path)
        full_text = ""
        for page_num, page in enumerate(doc):
            full_text += page.get_text() + "\n\n"
            # Витягуємо таблиці
            try:
                tabs = page.find_tables()
                for tab_idx, tab in enumerate(tabs.tables):
                    if tab.col_count < 4:
                        continue
                    if tab.row_count < 2:
                        continue
                    full_text += f"\n[ТАБЛИЦЯ на сторінці {page_num + 1}, таблиця {tab_idx + 1}]\n"
                    for row in tab.extract():
                        clean_row = [str(cell).strip().replace('\n', ' ') if cell else '' for cell in row]
                        # Пропускаємо порожні рядки
                        if any(clean_row):
                            full_text += ' | '.join(clean_row) + "\n"
                    full_text += "\n"
            except Exception as e:
                logger.warning(f"Could not extract tables from page {page_num + 1}: {e}")
        doc.close()
    else:
        # For DOCX, extract text using python-docx
        from docx import Document
        doc = Document(template_path)
        full_text = "\n\n".join([para.text for para in doc.paragraphs if para.text.strip()])
    
    # Chunk size for LLM (characters)
    chunk_size = 500000
    overlap = 300  # Overlap between chunks to avoid cutting sections
    
    # If text is small enough, process in one go
    if len(full_text) <= chunk_size:
        logger.info(f"Document size {len(full_text)} chars, processing in single chunk")
        sections = _analyze_single_chunk(full_text, 0)
        chunk_info = {'total_chunks': 1, 'total_chars': len(full_text), 'sections_found': len(sections)}
        return sections, chunk_info
    
    # Split into chunks with overlap
    chunks = []
    start = 0
    chunk_num = 0
    total_chars = len(full_text)
    
    while start < total_chars:
        end = start + chunk_size
        chunk = full_text[start:end]
        chunk_start_pct = (start / total_chars) * 100
        chunk_end_pct = (end / total_chars) * 100
        chunks.append((chunk, chunk_num, chunk_start_pct, chunk_end_pct))
        start = end - overlap
        chunk_num += 1
    
    logger.info(f"Document split into {len(chunks)} chunks (total {total_chars} chars)")
    
    # Process each chunk and merge results
    all_sections = []
    seen_titles = set()  # To avoid duplicates from overlapping chunks
    
    for chunk_text, chunk_idx, start_pct, end_pct in chunks:
        logger.info(f"Processing chunk {chunk_idx + 1}/{len(chunks)} (chars {int(start_pct)}%-{int(end_pct)}%)")
        
        chunk_sections = _analyze_single_chunk(chunk_text, chunk_idx)
        
        # Add sections that haven't been seen before
        for sec in chunk_sections:
            title = sec.get('title', '').strip()
            if title and title not in seen_titles:
                seen_titles.add(title)
                all_sections.append(sec)
            elif not title:
                # Add unnamed sections with a unique identifier
                sec['title'] = f"Section_{len(all_sections) + 1}"
                all_sections.append(sec)
        
        # Force garbage collection between chunks
        gc.collect()

        # Пауза між чанками щоб не перевищити rate limit
        if chunk_idx < len(chunks) - 1:
            time.sleep(30)
    
    logger.info(f"Total sections found across all chunks: {len(all_sections)}")
    
    chunk_info = {
        'total_chunks': len(chunks),
        'total_chars': total_chars,
        'sections_found': len(all_sections)
    }
    
    return all_sections, chunk_info


def _analyze_single_chunk(text: str, chunk_index: int) -> List[Dict[str, Any]]:
    """
    Analyze a single chunk of text with LLM to extract sections.
    
    Args:
        text: Text chunk to analyze
        chunk_index: Index of this chunk (for logging)
        
    Returns:
        List of sections found in this chunk
    """
    prompt = f"""Ти — експерт з аналізу структури документів.

Проаналізуй наступний текст документа і виділи всі секції (розділи, підрозділи, заголовки).
Зверни увагу на
- нумеровані абзаци
- короткі самостійні речення
- короткі речення на початку абзацу

Довгі інформативні речення, швидше за все, не є заголовками секцій.

## ТЕКСТ ДОКУМЕНТА (Частина {chunk_index + 1})

{text}

## ЗАВДАННЯ

Поверни JSON список секцій у такому форматі:
[
  {{
    "title": "Назва секції",
    "level": 1,
    "page": 0
  }}
]

Де:
- title: точний текст заголовка секції
- level: рівень вкладеності (1 = головний розділ, 2 = підрозділ, 3 = підпункт)
- page: номер сторінки (для PDF) або 0 (для DOCX)

ВАЖЛИВО:
- Поверни ТІЛЬКИ JSON, без додаткових пояснень
- Ігноруй нумерацію сторінок і колонтитули
- Якщо текст не має чіткої структури — поверни порожній масив []
- Використовуй українську мову для назв секцій
"""

    try:
        if not client:
            logger.error(f"Chunk {chunk_index}: No API key configured")
            return []
        
        # Retry logic for 503 errors
        max_retries = 5
        base_delay = 5  # seconds
        
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=REASONING_MODEL,
                    contents=prompt,
                    config={"temperature": TEMPERATURE}
                )
                
                # Parse JSON response
                response_text = response.text.strip()
                
                # Remove markdown code blocks if present
                if response_text.startswith('```json'):
                    response_text = response_text[7:]
                if response_text.startswith('```'):
                    response_text = response_text[3:]
                if response_text.endswith('```'):
                    response_text = response_text[:-3]
                response_text = response_text.strip()
                
                sections = json.loads(response_text)
                
                # Validate structure
                if not isinstance(sections, list):
                    logger.warning(f"Chunk {chunk_index}: LLM returned non-list, using empty sections")
                    return []
                
                # Ensure each section has required fields
                for sec in sections:
                    if 'title' not in sec:
                        sec['title'] = 'Untitled'
                    if 'level' not in sec:
                        sec['level'] = 1
                    if 'page' not in sec:
                        sec['page'] = 0
                
                logger.info(f"Chunk {chunk_index}: Found {len(sections)} sections")
                return sections
                
            except Exception as e:
                error_str = str(e)
                # Check if it's a 503 error
                if '503' in error_str or 'UNAVAILABLE' in error_str:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)  # Exponential backoff
                        logger.warning(f"Chunk {chunk_index}: 503 error, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Chunk {chunk_index}: Max retries exceeded for 503 error")
                        return []
                else:
                    # Non-503 error, don't retry
                    logger.error(f"Chunk {chunk_index}: LLM analysis failed: {e}")
                    return []
        
        return []
        
    except Exception as e:
        logger.error(f"Chunk {chunk_index}: Unexpected error: {e}")
        return []


class TablePlan(BaseModel):
    """Plan for generating a single table."""
    table_index: int = Field(0, description="Index of the table in template")
    purpose: str = Field("", description="What this table should contain")
    columns: List[str] = Field(default_factory=list, description="Column headers")
    estimated_rows: int = Field(5, description="Estimated number of rows needed")
    data_source_hint: str = Field("", description="Which source data to use for this table")
    notes: Optional[str] = Field(None, description="Additional notes for generation")


class SectionPlan(BaseModel):
    """Plan for generating a single document section."""
    section_index: int = Field(0, description="Index of the section")
    level: int = Field(1, description="Heading level (1=title, 2=h1, etc.)")
    title: str = Field("", description="Section title from template")
    content_type: str = Field("body", description="introduction|body|conclusion|summary")
    key_points: List[str] = Field(default_factory=list, description="Key points this section must cover")
    tone: str = Field("formal", description="formal|technical|business|academic")
    max_length_words: int = Field(200, description="Approximate word count")
    source_references: List[str] = Field(default_factory=list, description="Which sources are relevant")
    has_table: bool = Field(default=False, description="Does this section have a table?")
    table_plan: Optional[TablePlan] = Field(None, description="Table plan if applicable")


class DocumentPlan(BaseModel):
    """Complete plan for document generation."""
    document_title: str = Field("", description="Generated document title")
    document_type: str = Field("report", description="Type: report|letter|act|contract|etc.")
    target_audience: str = Field("general", description="Who will read this document")
    overall_tone: str = Field("formal", description="formal|business|technical|legal")
    sections: List[SectionPlan] = Field(default_factory=list, description="Plans for each section")
    tables: List[TablePlan] = Field(default_factory=list, description="Plans for tables not attached to sections")
    sources_summary: Dict[str, str] = Field(default_factory=dict, description="Summary of each source (source_name -> description)")
    generation_notes: Optional[str] = Field(None, description="Global notes for generation")


def build_planner_prompt(
    template_structure: Dict[str, Any],
    sources_data: Dict[str, Dict[str, Any]],
    total_chars: int,
    user_prompt: Optional[str] = None
) -> str:
    """
    Build the prompt for the LLM planner.
    Ukrainian language for better results with Ukrainian documents.
    """
    
    # Extract template info
    template_title = template_structure.get('title', 'Unknown')
    sections = template_structure.get('sections', [])
    tables = template_structure.get('tables', [])
    
    # Build sources summary
    sources_summary_parts = []
    for name, data in sources_data.items():
        full_text = data.get('text', '')
        # Обрізати якщо дуже велике (лімітом є токени моделі, ~800K для flash)
        if len(full_text) > 80000:
            full_text = full_text[:80000] + "\n...[текст обрізано]..."
        sources_summary_parts.append(f"=== {name} ===\n{full_text}\n")
    
    sources_text = '\n'.join(sources_summary_parts)
    
    # Build template structure description
    sections_text = []
    for i, sec in enumerate(sections):
        sec_desc = f"  {i}. [{sec.get('level', 0)}] '{sec.get('title', 'Untitled')}'"
        if sec.get('has_table'):
            sec_desc += f" (has table #{sec.get('table_index')})"
        sections_text.append(sec_desc)
    
    tables_text = []
    for i, tbl in enumerate(tables):
        tbl_desc = f"  {i}. {tbl.get('rows', 0)}x{tbl.get('columns', 0)} table"
        if tbl.get('has_header_row'):
            headers = ', '.join(tbl.get('headers', []))
            tbl_desc += f" with headers: {headers}"
        tables_text.append(tbl_desc)
    
    prompt = f"""Ти — експерт з аналізу документів і створення планів генерації.

Твоє завдання: проаналізувати шаблон документа та вміст джерел, і створити детальний план для генерації нового документа.

## ШАБЛОН ДОКУМЕНТА

Заголовок шаблону: {template_title}

Секції шаблону (рівень вкладеності [X]):
{chr(10).join(sections_text) if sections_text else "  (не знайдено секцій)"}

Таблиці в шаблоні:
{chr(10).join(tables_text) if tables_text else "  (не знайдено таблиць)"}

## ДЖЕРЕЛА ДАНИХ

Загальний обсяг тексту: {total_chars} символів

Джерела:
{sources_text}

## ЗАВДАННЯ

Створи JSON план (DocumentPlan) який визначає:

1. **document_title** — згенеруй відповідну назву документа на основі шаблону і джерел
2. **document_type** — тип документа (report, letter, act, contract, etc.)
3. **target_audience** — хто буде читати цей документ
4. **overall_tone** — загальний тон (formal, business, technical, legal)
5. **sections** — для кожної секції шаблону:
   - content_type: що це за частина (introduction, body, conclusion, summary)
   - key_points: ключові моменти які тут мають бути (3-5 пунктів)
   - tone: тон для цієї секції
   - max_length_words: приблизна кількість слів (100-500)
   - source_references: які джерела використати

6. **tables** — для кожної таблиці:
   - purpose: що має бути в таблиці
   - estimated_rows: скільки рядків потрібно (1-20)
   - data_source_hint: звідки брати дані

7. **sources_summary** — короткий опис кожного джерела у форматі словника: {{"filename.pdf": "опис джерела", ...}}

ВАЖЛИВО:
- Всі тексти мають бути українською мовою (діловий стиль)
- План має бути реалістичним — не більше 3-5 рядків в таблиці, 100-300 слів в секції
- Якщо даних в джерелах не вистачає — вкажи це в notes
- Поверни ТІЛЬКИ JSON, без додаткових пояснень
- Використовуй текст джерела для наповнення кожної секції дослівно або максимально близько до оригіналу.
- Твоє завдання — точно відтворити структуру шаблона, наповнити змістом з джерела

Приклад структури відповіді:
```json
{{
  "document_title": "Звіт про виконання робіт",
  "document_type": "report",
  "target_audience": "Керівництво компанії",
  "overall_tone": "formal",
  "sections": [...],
  "tables": [...],
  "sources_summary": {{"source1.pdf": "Опис першого джерела", "source2.pdf": "Опис другого джерела"}}
}}
```

{chr(10) + chr(10) + "## ДОДАТКОВІ ІНСТРУКЦІЇ ВІД КОРИСТУВАЧА" + chr(10) + chr(10) + user_prompt + chr(10) + chr(10) + "Врахуй ці інструкції при створенні плану документа." if user_prompt else ""}
"""
    
    return prompt


def create_document_plan(
    template_structure: Dict[str, Any],
    sources_data: Dict[str, Dict[str, Any]],
    task_id: str = "unknown",
    user_prompt: Optional[str] = None
) -> DocumentPlan:
    """
    Use Gemini LLM to create a document generation plan.
    
    Args:
        template_structure: Parsed template structure
        sources_data: Parsed sources with text and metadata
        task_id: For logging purposes
        
    Returns:
        DocumentPlan object
    """
    logger.info(f"[{task_id}] Creating document plan with LLM...")
    
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    
    # Calculate total source size
    total_chars = sum(d.get('char_count', 0) for d in sources_data.values())
    logger.info(f"[{task_id}] Total source chars: {total_chars}")
    if user_prompt:
        logger.info(f"[{task_id}] User prompt: {user_prompt[:100]}...")
    
    # Build prompt
    prompt = build_planner_prompt(template_structure, sources_data, total_chars, user_prompt)
    
    # Call Gemini
    logger.info(f"[{task_id}] Calling Gemini ({REASONING_MODEL})...")
    
    try:
        if not client:
            raise ValueError("GEMINI_API_KEY not configured")
        
        response = client.models.generate_content(
            model=REASONING_MODEL,
            contents=prompt,
            config={"temperature": TEMPERATURE}
        )
        
        # Parse JSON response
        response_text = response.text
        
        # Sometimes Gemini wraps JSON in markdown code blocks
        if response_text.startswith('```json'):
            response_text = response_text[7:]
        if response_text.startswith('```'):
            response_text = response_text[3:]
        if response_text.endswith('```'):
            response_text = response_text[:-3]
        
        response_text = response_text.strip()
        
        # Parse and validate
        plan_dict = json.loads(response_text)
        plan = DocumentPlan(**plan_dict)
        
        logger.success(f"[{task_id}] Document plan created: {len(plan.sections)} sections, {len(plan.tables)} tables")
        
        # Force garbage collection to free memory
        gc.collect()
        
        return plan
        
    except json.JSONDecodeError as e:
        logger.error(f"[{task_id}] Failed to parse LLM response as JSON: {e}")
        logger.error(f"Response preview: {response.text[:500]}")
        gc.collect()
        raise ValueError(f"Invalid JSON from LLM: {e}")
        
    except Exception as e:
        logger.error(f"[{task_id}] LLM planning failed: {e}")
        gc.collect()
        raise


def save_plan(plan: DocumentPlan, output_path: str):
    """Save plan to JSON file."""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(plan.model_dump(), f, ensure_ascii=False, indent=2)
    logger.info(f"Plan saved to: {output_path}")


if __name__ == '__main__':
    # Test mode
    import sys
    
    if len(sys.argv) >= 3:
        # Load template and sources
        with open(sys.argv[1], 'r') as f:
            template = json.load(f)
        with open(sys.argv[2], 'r') as f:
            sources = json.load(f)
        
        plan = create_document_plan(template, sources)
        print(json.dumps(plan.model_dump(), ensure_ascii=False, indent=2))
    else:
        print("Usage: python planner.py <template_structure.json> <sources_text.json>")
