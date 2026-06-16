"""
Generator - Creates document content using LLM.

Uses one-pass generation for entire document (NEW ARCHITECTURE).
"""
import gc
import os
import time
from typing import Dict, Any, Optional
from ..config import (
    REASONING_MODEL, TEMPERATURE, FULL_DOCUMENT_GENERATION_PROMPT
)
from common_config import (
    load_llm_config, get_model_provider, get_api_key_for_provider,
    get_ai_response
)
from loguru import logger
from ..routes import append_debug_log


def save_ai_response(task_id: str, response_type: str, prompt: str, response: str, task_folder: str = None, model_name: str = None):
    """
    Зберегти AI відповідь в папку завдання для відлагодження.
    Якщо task_folder не вказано — пропускає збереження (викликач сам вирішує,
    чи треба зберігати, як у випадку RAG-запиту, де все зберігається окремо).
    """
    if not task_folder:
        logger.debug(f"[{task_id}] task_folder=None, skip save_ai_response")
        return

    actual_model = model_name or REASONING_MODEL
    logger.info(f"[DEBUG] Saving AI response for task: {task_id}")
    logger.info(f"[DEBUG] Response type: {response_type}")
    logger.info(f"[DEBUG] Task folder: {task_folder}")
    logger.info(f"[DEBUG] Model: {actual_model}")
    logger.info(f"[DEBUG] Prompt length: {len(prompt)} chars")
    logger.info(f"[DEBUG] Response length: {len(response)} chars")

    base_dir = task_folder
    ai_responses_dir = os.path.join(base_dir, 'ai_responses')
    os.makedirs(ai_responses_dir, exist_ok=True)
    logger.info(f"[DEBUG] AI responses directory: {ai_responses_dir}")
    logger.info(f"[DEBUG] AI responses dir exists: {os.path.exists(ai_responses_dir)}")

    try:
        prompt_filename = f"{task_id}_{response_type}_prompt.txt"
        prompt_path = os.path.join(ai_responses_dir, prompt_filename)
        logger.info(f"[DEBUG] Saving prompt to: {prompt_path}")

        with open(prompt_path, 'w', encoding='utf-8') as f:
            f.write(f"TASK ID: {task_id}\n")
            f.write(f"TYPE: {response_type}\n")
            f.write(f"MODEL: {actual_model}\n")
            f.write(f"TIMESTAMP: {time.time()}\n")
            f.write("="*50 + "\n")
            f.write(prompt)

        response_filename = f"{task_id}_{response_type}_response.txt"
        response_path = os.path.join(ai_responses_dir, response_filename)
        logger.info(f"[DEBUG] Saving response to: {response_path}")

        with open(response_path, 'w', encoding='utf-8') as f:
            f.write(f"TASK ID: {task_id}\n")
            f.write(f"TYPE: {response_type}\n")
            f.write(f"MODEL: {actual_model}\n")
            f.write(f"TIMESTAMP: {time.time()}\n")
            f.write("="*50 + "\n")
            f.write(response)

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


def call_llm(
    prompt: str,
    task_id: str = "unknown",
    task_folder: Optional[str] = None,
    selected_model: Optional[str] = None,
    response_type: str = "full_doc"
) -> str:
    """
    Send prompt to LLM and return markdown response.

    Uses shared get_ai_response function from common_config.
    Initializes the LLM client per-call using config from llm-config.json.

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
    config = load_llm_config()
    provider_name, provider = get_model_provider(config, actual_model)
    if not provider:
        raise ValueError(f"Provider not found for model: {actual_model}")

    api_key = get_api_key_for_provider(provider)
    if not api_key:
        raise ValueError(f"API key not configured for provider: {provider.get('id', 'unknown')}")

    logger.info(f"[{task_id}] Calling LLM ({provider_name}/{actual_model})...")

    # Save pending status
    save_ai_response(task_id, response_type, prompt,
                     "[PENDING - AI call in progress]", task_folder, actual_model)

    try:
        # Build context for get_ai_response
        system_message = "Ти — експерт з генерації документів українською мовою. Створюй якісні, структуровані документи."
        context = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ]

        # Get model config for get_ai_response
        model_config = {"apiModel": actual_model}

        # Use shared get_ai_response function
        markdown_text, _, _ = get_ai_response(context, provider, model_config)
        markdown_text = markdown_text.strip()

        if not markdown_text:
            error_msg = (
                f"Модель {actual_model} повернула порожню відповідь. "
                f"Можливі причини: модель не підтримує системні промпти, "
                f"або відповідь була відфільтрована."
            )
            logger.error(f"[{task_id}] {error_msg}")
            save_ai_response(task_id, response_type, prompt,
                             f"[EMPTY RESPONSE] {error_msg}", task_folder, actual_model)
            raise ValueError(error_msg)

        save_ai_response(task_id, response_type, prompt, markdown_text, task_folder, actual_model)

        # Strip markdown code blocks if present
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
    Uses shared call_llm function for LLM interaction.

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

    actual_model = selected_model or REASONING_MODEL
    config = load_llm_config()
    provider_name, provider = get_model_provider(config, actual_model)
    if not provider:
        raise ValueError(f"Provider not found for model: {actual_model}")

    api_key = get_api_key_for_provider(provider)
    if not api_key:
        raise ValueError(f"API key not configured for provider: {provider.get('id', 'unknown')}")

    logger.info(f"[{task_id}] Using provider: {provider_name} for model: {actual_model}")

    from ..parsers.analysis_parser import parse_analysis

    parsed_analysis = parse_analysis(analysis_text)

    append_debug_log(f"[{task_id}] Parsed analysis: {len(parsed_analysis.sections)} sections, {len(parsed_analysis.tables)} tables")
    logger.info(f"[{task_id}] Parsed analysis: {len(parsed_analysis.sections)} sections, {len(parsed_analysis.tables)} tables")

    for i, table in enumerate(parsed_analysis.tables):
        logger.info(f"[{task_id}] Table {i+1}: {table.title} with {len(table.columns)} columns")
        if not table.columns:
            logger.warning(f"[{task_id}] Table {i+1} has no columns - will generate empty table")
        else:
            logger.info(f"[{task_id}] Table {i+1} columns: {', '.join(table.columns)}")

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

    instructions = "\n".join(
        f"- Розділ '{section.title}': {section.description_general}"
        for section in parsed_analysis.sections
        if section.has_text and section.description_general
    )

    sources_parts = []
    for name, data in sources_data.items():
        text = data.get('text', '')
        if len(text) > 500000:
            text = text[:500000] + "\n... (текст обрізано через обмеження довжини)"
        sources_parts.append(f"=== {name} ===\n{text}\n")

    sources_block = '\n'.join(sources_parts) if sources_parts else "(джерела не вказані)"

    from ..rag.anonymizer import get_anonymizer
    anonymizer = get_anonymizer()
    sources_anon = anonymizer.anonymize(sources_block)
    logger.info(f"[{task_id}] Sources anonymized: {len(sources_block)} → {len(sources_anon)} chars, "
                f"{len(anonymizer.get_map())} placeholders")

    import json
    structure_json = json.dumps(structure, ensure_ascii=False, indent=2)
    prompt = FULL_DOCUMENT_GENERATION_PROMPT.format(
        instructions=instructions,
        structure=structure_json,
        sources=sources_anon
    )

    if user_prompt:
        prompt += f"\n\n## ДОДАТКОВІ ВИМОГИ\n\n{user_prompt}"

    # Use shared call_llm function
    markdown_text = call_llm(
        prompt=prompt,
        task_id=task_id,
        task_folder=task_folder,
        selected_model=actual_model,
        response_type="full_doc"
    )

    # Deanonymize the response
    from ..rag.anonymizer import get_anonymizer
    anonymizer = get_anonymizer()
    markdown_text = anonymizer.deanonymize(markdown_text)

    # Save the deanonymized response
    save_ai_response(task_id, "full_doc", prompt, markdown_text, task_folder, actual_model)

    logger.success(f"[{task_id}] Document generated: {len(markdown_text)} chars")
    gc.collect()

    return markdown_text


if __name__ == '__main__':
    print("Generator module - use through Celery task")