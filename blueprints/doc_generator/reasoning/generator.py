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
    GEMINI_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, REASONING_MODEL,
    TEMPERATURE, FULL_DOCUMENT_GENERATION_PROMPT, get_model_provider,
    get_openai_completion_kwargs, is_deepseek_model
)
from loguru import logger
from ..routes import append_debug_log

# Initialize clients
gemini_client = None
openai_client = None
deepseek_client = None

if GEMINI_API_KEY:
    try:
        # For new google-genai package
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Gemini client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Gemini client: {e}")
else:
    logger.warning("GEMINI_API_KEY not configured")

if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        logger.info("OpenAI client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {e}")
else:
    logger.warning("OPENAI_API_KEY not configured")

if DEEPSEEK_API_KEY:
    try:
        from openai import OpenAI
        deepseek_client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com"
        )
        logger.info("DeepSeek client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize DeepSeek client: {e}")
else:
    logger.warning("DEEPSEEK_API_KEY not configured")


# def save_full_document_prompt_response(task_id: str, prompt: str, response: str, task_folder: str = None):
#     """
#     Save full document prompt and response in specific files.
    
#     Args:
#         task_id: ID of the task
#         prompt: The prompt sent to LLM
#         response: The response from LLM
#         task_folder: Task folder path (if available)
#     """
#     # Determine base directory
#     if task_folder and os.path.exists(task_folder):
#         base_dir = task_folder
#         logger.info(f"[DEBUG] Using task folder as base dir: {base_dir}")
#     else:
#         # Use temp folder as fallback
#         base_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'temp')
#         os.makedirs(base_dir, exist_ok=True)
#         logger.info(f"[DEBUG] Using temp folder as base dir: {base_dir}")
    
#     # Create ai_responses directory
#     ai_responses_dir = os.path.join(base_dir, 'ai_responses')
#     os.makedirs(ai_responses_dir, exist_ok=True)
#     logger.info(f"[DEBUG] AI responses directory: {ai_responses_dir}")
    
#     try:
#         # Save prompt file
#         prompt_filename = f"{task_id}+fulldoc-prompt.txt"
#         prompt_path = os.path.join(ai_responses_dir, prompt_filename)
        
#         with open(prompt_path, 'w', encoding='utf-8') as f:
#             f.write(f"TASK ID: {task_id}\n")
#             f.write(f"TYPE: FULL_DOCUMENT_PROMPT\n")
#             f.write(f"MODEL: {REASONING_MODEL}\n")
#             f.write("="*50 + "\n")
#             f.write(prompt)
        
#         # Save response file
#         response_filename = f"{task_id}+fulldoc-response.txt"
#         response_path = os.path.join(ai_responses_dir, response_filename)
        
#         with open(response_path, 'w', encoding='utf-8') as f:
#             f.write(f"TASK ID: {task_id}\n")
#             f.write(f"TYPE: FULL_DOCUMENT_RESPONSE\n")
#             f.write(f"MODEL: {REASONING_MODEL}\n")
#             f.write("="*50 + "\n")
#             f.write(response)
        
#         logger.info(f"Full document prompt saved: {prompt_path}")
#         logger.info(f"Full document response saved: {response_path}")
        
#     except Exception as e:
#         logger.error(f"Failed to save full document prompt/response: {e}")
#         raise


def save_ai_response(task_id: str, response_type: str, prompt: str, response: str, task_folder: str = None, model_name: str = None):
    """
    Зберегти AI відповідь в папку завдання для відлагодження.
    Якщо task_folder не вказано — пропускає збереження (викликач сам вирішує,
    чи треба зберігати, як у випадку RAG-запиту, де все зберігається окремо).
    
    Args:
        task_id: ID завдання
        response_type: Тип відповіді (section_N, table_N, full_document, etc.)
        prompt: Промпт що відправився до AI
        response: Відповідь від AI
        task_folder: Папка завдання (якщо є)
        model_name: Назва моделі (з сервера або fallback)
    """
    # Якщо папка не вказана — пропускаємо збереження.
    # Викликач (напр. RAG-запит у routes.py) сам зберігає потрібні файли.
    if not task_folder:
        logger.debug(f"[{task_id}] task_folder=None, skip save_ai_response (caller handles saving)")
        return
    
    actual_model = model_name or REASONING_MODEL
    
    # Debug info: input parameters
    logger.info(f"[DEBUG] Saving AI response for task: {task_id}")
    logger.info(f"[DEBUG] Response type: {response_type}")
    logger.info(f"[DEBUG] Task folder: {task_folder}")
    logger.info(f"[DEBUG] Model: {actual_model}")
    logger.info(f"[DEBUG] Prompt length: {len(prompt)} chars")
    logger.info(f"[DEBUG] Response length: {len(response)} chars")
    
    base_dir = task_folder
    
    # Створити папку для AI відповідей
    ai_responses_dir = os.path.join(base_dir, 'ai_responses')
    os.makedirs(ai_responses_dir, exist_ok=True)
    logger.info(f"[DEBUG] AI responses directory: {ai_responses_dir}")
    logger.info(f"[DEBUG] AI responses dir exists: {os.path.exists(ai_responses_dir)}")
    
    try:
        # Зберегти промпт
        prompt_filename = f"{task_id}_{response_type}_prompt.txt"
        prompt_path = os.path.join(ai_responses_dir, prompt_filename)
        logger.info(f"[DEBUG] Saving prompt to: {prompt_path}")
        
        with open(prompt_path, 'w', encoding='utf-8') as f:
            f.write(f"TASK ID: {task_id}\n")
            f.write(f"TYPE: {response_type}\n")
            f.write(f"MODEL: {actual_model}\n")
            f.write(f"TIMESTAMP: {os.path.getmtime(__file__)}\n")
            f.write("="*50 + "\n")
            f.write(prompt)
        
        # Зберегти відповідь
        response_filename = f"{task_id}_{response_type}_response.txt"
        response_path = os.path.join(ai_responses_dir, response_filename)
        logger.info(f"[DEBUG] Saving response to: {response_path}")
        
        with open(response_path, 'w', encoding='utf-8') as f:
            f.write(f"TASK ID: {task_id}\n")
            f.write(f"TYPE: {response_type}\n")
            f.write(f"MODEL: {actual_model}\n")
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


# def load_ai_response(task_id: str, response_type: str, task_folder: str = None) -> Optional[str]:
#     """
#     Завантажити збережену AI відповідь для відлагодження.
    
#     Args:
#         task_id: ID завдання
#         response_type: Тип відповіді (section, table, full_document)
#         task_folder: Шлях до папки завдання (якщо є)
        
#     Returns:
#         Збережена відповідь або None
#     """
#     # Debug info: input parameters
#     logger.info(f"[DEBUG] Loading AI response for task: {task_id}")
#     logger.info(f"[DEBUG] Response type: {response_type}")
#     logger.info(f"[DEBUG] Task folder: {task_folder}")
    
#     try:
#         # Визначити папку для пошуку
#         if task_folder and os.path.exists(task_folder):
#             base_dir = Path(task_folder)
#             logger.info(f"[DEBUG] Using task folder as base dir: {base_dir}")
#         else:
#             base_dir = Path("/home/openclaw/mysite/blueprints/doc_generator/temp/ai_responses")
#             logger.info(f"[DEBUG] Using default temp folder as base dir: {base_dir}")
        
#         response_file = base_dir / "ai_responses" / response_type / f"{task_id}_{response_type}_response.txt"
#         logger.info(f"[DEBUG] Looking for response file: {response_file}")
#         logger.info(f"[DEBUG] Response file exists: {response_file.exists()}")
        
#         if response_file.exists():
#             logger.info(f"[DEBUG] Response file found, loading content...")
#             with open(response_file, 'r', encoding='utf-8') as f:
#                 content = f.read()
            
#             # Debug info: file loaded successfully
#             file_size = response_file.stat().st_size
#             logger.info(f"[DEBUG] Response file loaded: {file_size} bytes")
#             logger.info(f"[DEBUG] Content length: {len(content)} chars")
#             logger.info(f"[DEBUG] Content preview: {content[:100]}...")
            
#             # Видалити заголовок
#             if "="*50 in content:
#                 cleaned_content = content.split("="*50, 1)[1].strip()
#                 logger.info(f"[DEBUG] Content cleaned (removed header)")
#                 return cleaned_content
            
#             logger.info(f"[DEBUG] Content returned as-is (no header found)")
#             return content.strip()
        
#         logger.warning(f"[DEBUG] Response file not found: {response_file}")
#         return None
        
#     except Exception as e:
#         logger.warning(f"[{task_id}] Failed to load AI response: {e}")
#         return None

# NOTE: Old architecture functions moved to old_architecture/generator_old.py
# - class GeneratedSection, GeneratedTable, build_section_prompt, etc.


def call_llm(
    prompt: str,
    task_id: str = "unknown",
    task_folder: Optional[str] = None,
    selected_model: Optional[str] = None,
    response_type: str = "full_doc"
) -> str:
    """
    Send prompt to LLM and return markdown response.
    
    This is the shared LLM calling logic used by both FULL and RAG modes.
    
    Args:
        prompt: The complete prompt to send
        task_id: For logging
        task_folder: For saving AI responses
        selected_model: Model name
        response_type: Label for saved response files
        
    Returns:
        Generated markdown text
        
    Raises:
        ValueError: If response is empty or API fails
    """
    actual_model = selected_model or REASONING_MODEL
    provider = get_model_provider(actual_model)
    logger.info(f"[{task_id}] Calling LLM ({provider}/{actual_model})...")

    if provider == 'gemini' and not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    elif provider == 'openai' and not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not configured")
    elif provider == 'deepseek' and not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY not configured")
    elif provider not in ['gemini', 'openai', 'deepseek']:
        raise ValueError(f"Unsupported model provider: {provider}")

    import time
    max_retries = 3
    retry_delays = [10, 20, 30]

    for attempt in range(max_retries):
        try:
            if attempt == 0:
                save_ai_response(task_id, response_type, prompt,
                                 "[PENDING - AI call in progress]", task_folder, actual_model)

            if provider == 'gemini':
                logger.info(f"[{task_id}] Sending to Gemini (attempt {attempt + 1}/{max_retries})...")
                response = gemini_client.models.generate_content(
                    model=actual_model,
                    contents=prompt,
                    config={"temperature": TEMPERATURE}
                )
                markdown_text = response.text.strip()

            elif provider == 'openai':
                logger.info(f"[{task_id}] Sending to OpenAI (attempt {attempt + 1}/{max_retries})...")
                openai_kwargs = get_openai_completion_kwargs(
                    model_name=actual_model, max_tokens=8000, temperature=TEMPERATURE
                )
                response = openai_client.chat.completions.create(
                    model=actual_model,
                    messages=[
                        {"role": "system", "content": "Ти — експерт з генерації документів українською мовою. Створюй якісні, структуровані документи."},
                        {"role": "user", "content": prompt}
                    ],
                    **openai_kwargs
                )
                message = response.choices[0].message
                raw_content = message.content
                finish_reason = response.choices[0].finish_reason
                logger.info(f"[{task_id}] OpenAI finish_reason: {finish_reason}")

                if raw_content is not None:
                    markdown_text = raw_content.strip()
                elif hasattr(message, 'refusal') and message.refusal:
                    logger.warning(f"[{task_id}] OpenAI refusal: {message.refusal}")
                    markdown_text = ""
                else:
                    logger.warning(f"[{task_id}] OpenAI empty response. finish_reason={finish_reason}")
                    markdown_text = ""

            elif provider == 'deepseek':
                logger.info(f"[{task_id}] Sending to DeepSeek (attempt {attempt + 1}/{max_retries})...")
                deepseek_kwargs = get_openai_completion_kwargs(
                    model_name=actual_model, max_tokens=8000, temperature=TEMPERATURE
                )
                response = deepseek_client.chat.completions.create(
                    model=actual_model,
                    messages=[
                        {"role": "system", "content": "Ти — експерт з генерації документів українською мовою. Створюй якісні, структуровані документи."},
                        {"role": "user", "content": prompt}
                    ],
                    **deepseek_kwargs
                )
                raw_content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason
                logger.info(f"[{task_id}] DeepSeek finish_reason: {finish_reason}")

                if raw_content is not None:
                    markdown_text = raw_content.strip()
                else:
                    logger.warning(f"[{task_id}] DeepSeek None content. finish_reason={finish_reason}")
                    markdown_text = ""

            else:
                raise ValueError(f"Unsupported provider: {provider}")

            # Validate response
            if not markdown_text.strip():
                error_msg = (
                    f"Модель {actual_model} повернула порожню відповідь. "
                    f"Finish reason: {getattr(response.choices[0], 'finish_reason', 'unknown')}. "
                    f"Можливі причини: модель не підтримує системні промпти, "
                    f"або відповідь була відфільтрована."
                )
                logger.error(f"[{task_id}] {error_msg}")
                save_ai_response(task_id, response_type, prompt,
                                 f"[EMPTY RESPONSE] {error_msg}", task_folder, actual_model)
                raise ValueError(error_msg)

            # Save AI response (промпт вже анонімізований, відповідь — сира з плейсхолдерами)
            save_ai_response(task_id, response_type, prompt, markdown_text, task_folder, actual_model)

            # Clean markdown code blocks
            if markdown_text.startswith('```'):
                lines = markdown_text.split('\n')
                if lines[0].startswith('```'):
                    lines.pop(0)
                if lines and lines[-1].startswith('```'):
                    lines.pop()
                markdown_text = '\n'.join(lines).strip()

            logger.success(f"[{task_id}] LLM response: {len(markdown_text)} chars")
            gc.collect()
            return markdown_text

        except Exception as e:
            error_str = str(e)
            is_503 = '503' in error_str or 'UNAVAILABLE' in error_str

            if is_503 and attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.warning(f"[{task_id}] 503 error, retrying in {delay}s...")
                time.sleep(delay)
                continue
            else:
                logger.error(f"[{task_id}] LLM call failed: {e}")
                gc.collect()
                raise


def generate_document_full(
    analysis_text: str,
    sources_data: Dict[str, Dict[str, Any]],
    task_id: str = "unknown",
    user_prompt: Optional[str] = None,
    task_folder: Optional[str] = None,
    selected_model: Optional[str] = None
) -> str:
    """
    Generate full document in one pass using LLM.
    
    Args:
        analysis_text: Parsed analysis text
        sources_data: Dictionary of source name -> parsed source data
        task_id: For logging
        user_prompt: Optional user prompt for generation
        task_folder: Optional task folder path for saving AI responses
        selected_model: Model to use (from user selection), fallback to env REASONING_MODEL
        
    Returns:
        Generated markdown text
    """
    logger.info(f"[{task_id}] Starting full document generation (one-pass)...")
    logger.info(f"[{task_id}] Using only analysis.txt data, no test data generation")
    
    # Use selected_model if provided, otherwise fallback to env variable
    actual_model = selected_model or REASONING_MODEL
    provider = get_model_provider(actual_model)
    logger.info(f"[{task_id}] Using provider: {provider} for model: {actual_model}")
    
    if provider == 'gemini' and not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    elif provider == 'openai' and not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not configured")
    elif provider == 'deepseek' and not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY not configured")
    elif provider not in ['gemini', 'openai', 'deepseek']:
        raise ValueError(f"Unsupported model provider: {provider}")
    
    # Parse analysis text to get structure
    from ..parsers.analysis_parser import parse_analysis

    parsed_analysis = parse_analysis(analysis_text)
    
    append_debug_log(f"[{task_id}] Parsed analysis: {len(parsed_analysis.sections)} sections, {len(parsed_analysis.tables)} tables")
    logger.info(f"[{task_id}] Parsed analysis: {len(parsed_analysis.sections)} sections, {len(parsed_analysis.tables)} tables")
    
    # Log table details for debugging
    for i, table in enumerate(parsed_analysis.tables):
        logger.info(f"[{task_id}] Table {i+1}: {table.title} with {len(table.columns)} columns")
        if not table.columns:
            logger.warning(f"[{task_id}] Table {i+1} has no columns - will generate empty table")
        else:
            logger.info(f"[{task_id}] Table {i+1} columns: {', '.join(table.columns)}")
    
    # Build structure for JSON (remove general field from description)
    structure = []
    for section in parsed_analysis.sections:
        section_data = {
            "id": section.index,
            "type": "text",
            "title": section.title,
            "level": section.level
        }
        if section.has_text:
            if section.description_specific:
                section_data["description"] = {"specific": section.description_specific}
            if section.word_count_range:
                section_data["words"] = f"від {section.word_count_range[0]} до {section.word_count_range[1]} слів"
        structure.append(section_data)
    
    for table in parsed_analysis.tables:
        table_data = {
            "id": table.index,
            "type": "table",
            "title": table.title,
            "level": table.level
        }
        if table.columns:
            table_data["columns"] = table.columns
        if table.rows is not None:
            table_data["rows"] = table.rows
        if table.first_column_data is not None:
            table_data["first_column_data"] = table.first_column_data
        structure.append(table_data)
    
    # Extract instructions from description.general fields
    instructions = "\n".join(
        f"- Розділ '{section.title}': {section.description_general}"
        for section in parsed_analysis.sections
        if section.has_text and section.description_general
    )
    
    # Build sources text
    sources_parts = []
    for name, data in sources_data.items():
        text = data.get('text', '')
        # Truncate if too long
        if len(text) > 500000:
            text = text[:500000] + "\n... (текст обрізано через обмеження довжини)"
        sources_parts.append(f"=== {name} ===\n{text}\n")
    
    sources_block = '\n'.join(sources_parts) if sources_parts else "(джерела не вказані)"
    
    # return "" # for test

    # Анонімізуємо джерела перед відправкою в LLM
    from ..rag.anonymizer import get_anonymizer
    anonymizer = get_anonymizer()
    sources_anon = anonymizer.anonymize(sources_block)
    logger.info(f"[{task_id}] Sources anonymized: {len(sources_block)} → {len(sources_anon)} chars, "
                f"{len(anonymizer.get_map())} placeholders")

    # Build prompt
    import json
    structure_json = json.dumps(structure, ensure_ascii=False, indent=2)
    prompt = FULL_DOCUMENT_GENERATION_PROMPT.format(
        instructions=instructions,
        structure=structure_json,
        sources=sources_anon
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
                save_ai_response(task_id, "full_doc", prompt, "[PENDING - AI call in progress]", task_folder, actual_model)
            
            if provider == 'gemini':
                logger.info(f"[{task_id}] Sending request to Gemini (спроба {attempt + 1}/{max_retries})...")
                response = gemini_client.models.generate_content(
                    model=actual_model,
                    contents=prompt,
                    config={"temperature": TEMPERATURE}
                )
                markdown_text = response.text.strip()
                
            elif provider == 'openai':
                logger.info(f"[{task_id}] Sending request to OpenAI (спроба {attempt + 1}/{max_retries})...")
                openai_kwargs = get_openai_completion_kwargs(
                    model_name=actual_model,
                    max_tokens=8000,
                    temperature=TEMPERATURE
                )
                response = openai_client.chat.completions.create(
                    model=actual_model,
                    messages=[
                        {"role": "system", "content": "Ти — експерт з генерації документів українською мовою. Створюй якісні, структуровані документи."},
                        {"role": "user", "content": prompt}
                    ],
                    **openai_kwargs
                )
                # Get content - newer models may return None for filtered/refused responses
                message = response.choices[0].message
                raw_content = message.content
                finish_reason = response.choices[0].finish_reason
                
                logger.info(f"[{task_id}] OpenAI response content type: {type(raw_content)}")
                logger.info(f"[{task_id}] OpenAI finish_reason: {finish_reason}")
                
                # Try to get content from different possible locations
                if raw_content is not None:
                    markdown_text = raw_content.strip()
                elif hasattr(message, 'refusal') and message.refusal:
                    logger.warning(f"[{task_id}] OpenAI returned refusal: {message.refusal}")
                    markdown_text = ""
                else:
                    logger.warning(f"[{task_id}] OpenAI returned empty response. finish_reason={finish_reason}")
                    # Save full response as JSON for debugging
                    try:
                        debug_path = os.path.join(task_folder or '', 'ai_responses', f"{task_id}_full_doc_raw_response.json")
                        os.makedirs(os.path.dirname(debug_path), exist_ok=True)
                        with open(debug_path, 'w', encoding='utf-8') as f:
                            f.write(response.model_dump_json(indent=2))
                        logger.info(f"[{task_id}] Saved raw response to: {debug_path}")
                    except Exception as dump_err:
                        logger.warning(f"[{task_id}] Failed to dump raw response: {dump_err}")
                    markdown_text = ""
            
            elif provider == 'deepseek':
                logger.info(f"[{task_id}] Sending request to DeepSeek (спроба {attempt + 1}/{max_retries})...")
                deepseek_kwargs = get_openai_completion_kwargs(
                    model_name=actual_model,
                    max_tokens=8000,
                    temperature=TEMPERATURE
                )
                response = deepseek_client.chat.completions.create(
                    model=actual_model,
                    messages=[
                        {"role": "system", "content": "Ти — експерт з генерації документів українською мовою. Створюй якісні, структуровані документи."},
                        {"role": "user", "content": prompt}
                    ],
                    **deepseek_kwargs
                )
                # DeepSeek uses same response format as OpenAI
                raw_content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason
                logger.info(f"[{task_id}] DeepSeek response content type: {type(raw_content)}")
                logger.info(f"[{task_id}] DeepSeek finish_reason: {finish_reason}")
                
                if raw_content is not None:
                    markdown_text = raw_content.strip()
                else:
                    logger.warning(f"[{task_id}] DeepSeek returned None content. finish_reason={finish_reason}")
                    markdown_text = ""
                
            else:
                raise ValueError(f"Unsupported provider: {provider}")
            
            # Деанонімізуємо відповідь LLM перед збереженням
            from ..rag.anonymizer import get_anonymizer
            anonymizer = get_anonymizer()
            markdown_text = anonymizer.deanonymize(markdown_text)

            # Save AI response for debugging
            logger.info(f"[DEBUG] About to save AI response for full_document")
            logger.info(f"[DEBUG] Task folder: {task_folder}")
            logger.info(f"[DEBUG] Task ID: {task_id}")
            logger.info(f"[DEBUG] Response type: full_document")
            logger.info(f"[DEBUG] Markdown text length: {len(markdown_text)}")
            
            # Validate: raise clear error if response is empty
            if not markdown_text.strip():
                error_msg = (
                    f"Модель {actual_model} повернула порожню відповідь. "
                    f"Finish reason: {getattr(response.choices[0], 'finish_reason', 'unknown')}. "
                    f"Можливі причини: модель не підтримує системні промпти, "
                    f"або відповідь була відфільтрована."
                )
                logger.error(f"[{task_id}] {error_msg}")
                # Save error response for debugging
                save_ai_response(task_id, "full_doc", prompt, f"[EMPTY RESPONSE] {error_msg}", task_folder, actual_model)
                raise ValueError(error_msg)
            
            # save_full_document_prompt_response(task_id, prompt, markdown_text, task_folder)
            save_ai_response(task_id, "full_doc", prompt, markdown_text, task_folder, actual_model)
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
            # logger.info(f"[DEBUG] Saving AI error response for full_document_error")
            # error_response = f"ERROR: {error_str}\n\nATTEMPT: {attempt + 1}/{max_retries}\n\nIS_503: {is_503_error}"
            # save_ai_response(task_id, f"full_document_error_attempt_{attempt + 1}", prompt, error_response, task_folder)
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
