"""
Pipeline - Orchestrates the RAG document generation process.

Each step logs results to:
  - task_folder/rag_logs/ for persistent inspection
  - Debug log for real-time UI visibility
"""

import os
import json
import time
from typing import List, Dict, Any, Optional
from pathlib import Path
from loguru import logger

from .chunker import chunk_documents, Chunk, format_chunks_for_log
from .embedder import Embedder, compute_sources_hash
from .retriever import Retriever
from .query_builder import QueryBuilder
from .prompt_parser import (
    parse_full_doc_prompt,
    detect_structure,
    build_parent_context,
    ParsedPrompt
)


def _ensure_log_dir(task_folder: str) -> str:
    """Create and return rag_logs directory path."""
    log_dir = os.path.join(task_folder, 'rag_logs')
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def _write_log(log_dir: str, step: str, content: str):
    """Write step log to file."""
    path = os.path.join(log_dir, step)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    logger.debug(f"RAG log written: {path}")


def generate_document_rag(
    analysis_text: str,
    sources_data: Dict[str, Dict[str, Any]],
    task_id: str = "unknown",
    user_prompt: Optional[str] = None,
    task_folder: Optional[str] = None,
    selected_model: Optional[str] = None,
    chunk_params: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[callable] = None
) -> str:
    """
    Generate document using RAG pipeline.

    Args:
        analysis_text: Parsed analysis JSON/text from template analysis
        sources_data: Dict of source_name -> {text, char_count, ...}
        task_id: For logging
        user_prompt: Optional user instructions
        task_folder: Optional task folder for saving logs
        selected_model: Model name (for logging, actual LLM call is external)
        chunk_params: Dict with min_chunk_size, max_chunk_size, top_k, threshold

    Returns:
        Markdown text (to be converted to DOCX by caller)

    Raises:
        ValueError: If any step fails critically
    """
    params = chunk_params or {}
    min_chunk_size = params.get('min_chunk_size', 50)
    max_chunk_size = params.get('max_chunk_size', 250)
    top_k = params.get('top_k', 3)
    threshold = params.get('threshold', 0.0)

    log_dir = _ensure_log_dir(task_folder) if task_folder else None

    logger.info(f"[{task_id}] ===== RAG Pipeline Start =====")
    logger.info(f"[{task_id}] Params: min_chunk={min_chunk_size}, max_chunk={max_chunk_size}, "
                f"top_k={top_k}, threshold={threshold}")
    logger.info(f"[{task_id}] Sources: {list(sources_data.keys())}")

    total_start = time.time()

    # ============================================================
    # Step 1: Extract / prepare source texts
    # ============================================================
    step_start = time.time()
    step_info = {
        'step': '1_extract',
        'sources': {},
        'total_chars': 0
    }

    for name, data in sources_data.items():
        text = data.get('text', '')
        char_count = len(text)
        word_count = len(text.split()) if text else 0
        step_info['sources'][name] = {
            'chars': char_count,
            'words': word_count
        }
        step_info['total_chars'] += char_count

    step_info['time_seconds'] = round(time.time() - step_start, 2)

    log_msg = (f"[{task_id}] Step 1 - Extract: {step_info['total_chars']} chars "
               f"from {len(sources_data)} sources ({step_info['time_seconds']}s)")
    logger.info(log_msg)
    if log_dir:
        _write_log(log_dir, '01_extract.txt',
                   json.dumps(step_info, ensure_ascii=False, indent=2))

    if progress_callback:
        progress_callback("Витяг тексту з джерел", 0.05)

    # ============================================================
    # Step 2: Chunk documents
    # ============================================================
    step_start = time.time()

    chunks: List[Chunk] = chunk_documents(
        sources_data=sources_data,
        min_chunk_size=min_chunk_size,
        max_chunk_size=max_chunk_size
    )

    if not chunks:
        raise ValueError("No chunks created from source documents")

    step_info = {
        'step': '2_chunks',
        'total_chunks': len(chunks),
        'params': {'min_chunk_size': min_chunk_size, 'max_chunk_size': max_chunk_size},
        'chunk_word_counts': [c.word_count for c in chunks],
        'avg_words': round(sum(c.word_count for c in chunks) / len(chunks), 1),
        'time_seconds': round(time.time() - step_start, 2)
    }
    step_info['max_words'] = max(c.word_count for c in chunks)
    step_info['min_words'] = min(c.word_count for c in chunks)

    log_msg = (f"[{task_id}] Step 2 - Chunk: {len(chunks)} chunks, "
               f"avg {step_info['avg_words']} words each ({step_info['time_seconds']}s)")
    logger.info(log_msg)
    if log_dir:
        _write_log(log_dir, '02_chunks.json',
                   json.dumps(step_info, ensure_ascii=False, indent=2))
        # Also save full chunks list for inspection
        chunks_log = [c.to_dict() for c in chunks]
        _write_log(log_dir, '02_chunks_full.json',
                   json.dumps(chunks_log, ensure_ascii=False, indent=2))

    if progress_callback:
        progress_callback(f"Чанкування: {len(chunks)} фрагментів", 0.20)

    # ============================================================
    # Step 3: Build vector store (with cache)
    # ============================================================
    step_start = time.time()

    # Compute source file paths for hash
    source_paths = []
    for name in sources_data.keys():
        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'Tasks-data', 'Sources', name
        )
        source_paths.append(path)

    sources_hash = compute_sources_hash(source_paths)
    embedder = Embedder()
    cache_hit = embedder.collection_exists(sources_hash)

    if not cache_hit:
        collection_name = embedder.build(
            chunks=chunks,
            sources_hash=sources_hash,
            source_files=list(sources_data.keys())
        )
    else:
        collection_name = embedder.get_collection_name(sources_hash)
        logger.info(f"[{task_id}] Cache HIT: using existing ChromaDB collection '{collection_name}'")

    step_info = {
        'step': '3_embeddings',
        'cache_hit': cache_hit,
        'collection_name': collection_name,
        'embedding_model': 'nomic-embed-text',
        'total_chunks': len(chunks),
        'time_seconds': round(time.time() - step_start, 2)
    }

    log_msg = (f"[{task_id}] Step 3 - Vector store: {'CACHE' if cache_hit else 'BUILD'}, "
               f"collection='{collection_name}' ({step_info['time_seconds']}s)")
    logger.info(log_msg)
    if log_dir:
        _write_log(log_dir, '03_embeddings.txt',
                   json.dumps(step_info, ensure_ascii=False, indent=2))

    if progress_callback:
        progress_callback("Векторна база готова" if cache_hit else "Створення ембеддингів...", 0.40)

    # ============================================================
    # Step 4: Parse analysis structure
    # ============================================================
    from ..parsers.analysis_parser import parse_analysis

    parsed_analysis = parse_analysis(analysis_text)

    step_info = {
        'step': '4_analysis',
        'sections': len(parsed_analysis.sections),
        'tables': len(parsed_analysis.tables),
        'section_titles': [s.title for s in parsed_analysis.sections],
        'table_titles': [t.title for t in parsed_analysis.tables]
    }

    log_msg = (f"[{task_id}] Step 4 - Analysis: {len(parsed_analysis.sections)} sections, "
               f"{len(parsed_analysis.tables)} tables")
    logger.info(log_msg)
    if log_dir:
        _write_log(log_dir, '04_analysis.txt',
                   json.dumps(step_info, ensure_ascii=False, indent=2))

    if progress_callback:
        progress_callback("Парсинг структури аналізу", 0.50)

    # ============================================================
    # Step 5: Build search queries
    # ============================================================
    query_builder = QueryBuilder()
    queries = query_builder.build_queries(parsed_analysis)

    step_info = {
        'step': '5_queries',
        'total_queries': len(queries),
        'queries': queries
    }

    log_msg = (f"[{task_id}] Step 5 - Queries: {len(queries)} queries built")
    logger.info(log_msg)
    if log_dir:
        _write_log(log_dir, '05_queries.json',
                   json.dumps(step_info, ensure_ascii=False, indent=2))

    if progress_callback:
        progress_callback("Побудова пошукових запитів", 0.60)

    # ============================================================
    # Step 6: Retrieve relevant chunks
    # ============================================================
    step_start = time.time()

    retriever = Retriever(embedder)
    retrieved_chunks = retriever.retrieve_for_queries(
        queries=queries,
        sources_hash=sources_hash,
        top_k=top_k,
        threshold=threshold
    )

    step_info = {
        'step': '6_retrieved',
        'total_chunks': len(retrieved_chunks),
        'time_seconds': round(time.time() - step_start, 2),
        'chunks': [{
            'source': c.get('source'),
            'chunk_index': c.get('chunk_index'),
            'similarity': c.get('similarity'),
            'matched_section': c.get('matched_section'),
            'word_count': c.get('word_count'),
            'text_preview': c.get('text', '')[:100]
        } for c in retrieved_chunks]
    }

    log_msg = (f"[{task_id}] Step 6 - Retrieve: {len(retrieved_chunks)} unique chunks "
               f"({step_info['time_seconds']}s)")
    logger.info(log_msg)

    # Check if we found anything useful
    if not retrieved_chunks:
        log_msg = (f"[{task_id}] ⚠ RAG returned 0 relevant chunks! "
                   f"Will fall back to FULL mode or return empty context.")
        logger.warning(log_msg)

    if log_dir:
        _write_log(log_dir, '06_retrieved_chunks.json',
                   json.dumps(step_info, ensure_ascii=False, indent=2))

    if progress_callback:
        progress_callback(f"Пошук: {len(retrieved_chunks)} фрагментів знайдено", 0.75)

    # ============================================================
    # Step 7: Build context + anonymize + generate prompt
    # ============================================================
    context_text = retriever.format_context_for_llm(retrieved_chunks)

    # Анонімізуємо контекст перед відправкою в LLM
    from .anonymizer import get_anonymizer
    anonymizer = get_anonymizer()
    context_anon = anonymizer.anonymize(context_text)
    logger.info(f"[{task_id}] Context anonymized: {len(context_text)} → {len(context_anon)} chars, "
                f"{len(anonymizer.get_map())} placeholders")

    prompt = _build_rag_prompt(
        analysis_text=analysis_text,
        context_text=context_anon,
        user_prompt=user_prompt
    )

    total_time = round(time.time() - total_start, 2)

    step_info = {
        'step': '7_prompt',
        'total_pipeline_time_seconds': total_time,
        'context_chars': len(context_text),
        'context_words': len(context_text.split()),
        'prompt_chars': len(prompt),
        'params': params
    }

    log_msg = (f"[{task_id}] Step 7 - Prompt built: {step_info['context_words']} context words, "
               f"{step_info['prompt_chars']} total prompt chars")
    logger.info(log_msg)
    if log_dir:
        _write_log(log_dir, '07_prompt.txt',
                   json.dumps(step_info, ensure_ascii=False, indent=2))
        # Also save the actual context for inspection
        _write_log(log_dir, '07_context_for_llm.txt', context_text)

    logger.info(f"[{task_id}] ===== RAG Pipeline Complete: {total_time}s =====")

    # Return the prompt + context; the caller (Celery task) will call LLM
    return prompt


def _build_rag_prompt(
    analysis_text: str,
    context_text: str,
    user_prompt: Optional[str] = None
) -> str:
    """
    Build the LLM prompt for document generation using RAG context.

    This is similar to FULL_DOCUMENT_GENERATION_PROMPT from config.py,
    but uses the retrieved context_text instead of full source documents.
    """
    from ..config import FULL_DOCUMENT_GENERATION_PROMPT

    # Parse analysis for structure and instructions
    from ..parsers.analysis_parser import parse_analysis
    parsed = parse_analysis(analysis_text)

    # Build structure JSON (same as in generator.py)
    import json as json_mod
    structure = []
    for section in parsed.sections:
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

    for table in parsed.tables:
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

    # Extract instructions from description.general
    instructions = "\n".join(
        f"- Розділ '{section.title}': {section.description_general}"
        for section in parsed.sections
        if section.has_text and section.description_general
    )

    structure_json = json_mod.dumps(structure, ensure_ascii=False, indent=2)

    prompt = FULL_DOCUMENT_GENERATION_PROMPT.format(
        instructions=instructions,
        structure=structure_json,
        sources=context_text
    )

    # Add note that this is RAG-based
    prompt += (
        "\n\n## ПРИМІТКА\n"
        "Надані джерела — це релевантні фрагменти, знайдені через векторний пошук. "
        "Використовуйте їх як основний матеріал. Якщо для розділу не вистачає даних — "
        "напишіть 'Дані відсутні'."
    )

    if user_prompt:
        prompt += f"\n\n## ДОДАТКОВІ ВИМОГИ\n\n{user_prompt}"

    return prompt


def generate_document_rag_enhanced(
    full_prompt_text: str,
    sources_data: Dict[str, Dict[str, Any]],
    task_id: str = "unknown",
    user_prompt: Optional[str] = None,
    task_folder: Optional[str] = None,
    selected_model: Optional[str] = None,
    chunk_params: Optional[Dict[str, Any]] = None
) -> str:
    """
    Enhanced RAG pipeline with per-section chunk retrieval.

    Key difference from generate_document_rag():
    1. Parses the full_doc_prompt (not analysis_text) to extract structure
    2. Builds per-section search queries (more precise, with parent context)
    3. Makes N separate ChromaDB searches (one per section)
    4. Collects all chunks into ONE prompt → ONE LLM call
    5. If prompt has no structure → falls back to generate_document_rag()

    The caller (Celery task) calls LLM with the returned prompt.
    """
    # Step 0: Try to parse the full_prompt_text for structure
    parsed_prompt = parse_full_doc_prompt(full_prompt_text)

    if not parsed_prompt.has_structure:
        logger.info(f"[{task_id}] Prompt has no structure — falling back to standard RAG")
        # Use full_prompt_text as analysis_text for backward compatibility
        return generate_document_rag(
            analysis_text=full_prompt_text,
            sources_data=sources_data,
            task_id=task_id,
            user_prompt=user_prompt,
            task_folder=task_folder,
            selected_model=selected_model,
            chunk_params=chunk_params
        )

    params = chunk_params or {}
    min_chunk_size = params.get('min_chunk_size', 50)
    max_chunk_size = params.get('max_chunk_size', 250)
    top_k = params.get('top_k', 3)
    threshold = params.get('threshold', 0.0)

    log_dir = _ensure_log_dir(task_folder) if task_folder else None

    logger.info(f"[{task_id}] ===== Enhanced RAG Pipeline Start =====")
    logger.info(f"[{task_id}] Parsed: {len(parsed_prompt.parsed_analysis.sections)} sections, "
                f"{len(parsed_prompt.parsed_analysis.tables)} tables")
    logger.info(f"[{task_id}] Params: min_chunk={min_chunk_size}, max_chunk={max_chunk_size}, "
                f"top_k={top_k}, threshold={threshold}")

    total_start = time.time()

    # ============================================================
    # Step 1: Extract / prepare source texts
    # ============================================================
    step_start = time.time()
    for name, data in sources_data.items():
        text = data.get('text', '')
        logger.info(f"[{task_id}] Source '{name}': {len(text)} chars, {len(text.split())} words")

    if log_dir:
        _write_log(log_dir, '01_extract.txt',
                   json.dumps({
                       'step': '1_extract',
                       'sources': {name: {'chars': len(d.get('text', '')),
                                          'words': len(d.get('text', '').split())}
                                   for name, d in sources_data.items()},
                       'time_seconds': round(time.time() - step_start, 2)
                   }, ensure_ascii=False, indent=2))

    # ============================================================
    # Step 2: Chunk documents
    # ============================================================
    step_start = time.time()

    chunks: List[Chunk] = chunk_documents(
        sources_data=sources_data,
        min_chunk_size=min_chunk_size,
        max_chunk_size=max_chunk_size
    )

    if not chunks:
        raise ValueError("No chunks created from source documents")

    step_info = {
        'step': '2_chunks',
        'total_chunks': len(chunks),
        'avg_words': round(sum(c.word_count for c in chunks) / len(chunks), 1),
        'time_seconds': round(time.time() - step_start, 2)
    }
    logger.info(f"[{task_id}] Step 2 - Chunk: {len(chunks)} chunks ({step_info['time_seconds']}s)")
    if log_dir:
        _write_log(log_dir, '02_chunks.json',
                   json.dumps(step_info, ensure_ascii=False, indent=2))
        chunks_log = [c.to_dict() for c in chunks]
        _write_log(log_dir, '02_chunks_full.json',
                   json.dumps(chunks_log, ensure_ascii=False, indent=2))

    # ============================================================
    # Step 3: Build vector store (with cache)
    # ============================================================
    step_start = time.time()

    source_paths = []
    for name in sources_data.keys():
        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'Tasks-data', 'Sources', name
        )
        source_paths.append(path)

    sources_hash = compute_sources_hash(source_paths)
    embedder = Embedder()
    cache_hit = embedder.collection_exists(sources_hash)

    if not cache_hit:
        collection_name = embedder.build(
            chunks=chunks,
            sources_hash=sources_hash,
            source_files=list(sources_data.keys())
        )
    else:
        collection_name = embedder.get_collection_name(sources_hash)
        logger.info(f"[{task_id}] Cache HIT: using '{collection_name}'")

    logger.info(f"[{task_id}] Step 3 - Vector store: {'CACHE' if cache_hit else 'BUILD'}")
    if log_dir:
        _write_log(log_dir, '03_embeddings.txt',
                   json.dumps({'step': '3_embeddings', 'cache_hit': cache_hit,
                               'collection_name': collection_name,
                               'time_seconds': round(time.time() - step_start, 2)},
                              ensure_ascii=False, indent=2))

    # ============================================================
    # Step 4: Build parent context for hierarchical queries
    # ============================================================
    parent_ctx = build_parent_context(parsed_prompt.parsed_analysis)

    if log_dir:
        _write_log(log_dir, '04_parent_context.txt',
                   json.dumps(parent_ctx, ensure_ascii=False, indent=2))

    # ============================================================
    # Step 5: Build per-section search queries (MORE PRECISE)
    # ============================================================
    query_builder = QueryBuilder()
    queries = query_builder.build_per_section_queries(
        parsed_prompt.parsed_analysis,
        parent_context=parent_ctx
    )

    step_info = {'step': '5_queries', 'total_queries': len(queries), 'queries': queries}
    logger.info(f"[{task_id}] Step 5 - Queries: {len(queries)} per-section queries built")
    if log_dir:
        _write_log(log_dir, '05_queries.json',
                   json.dumps(step_info, ensure_ascii=False, indent=2))

    # ============================================================
    # Step 6: N separate ChromaDB searches → collect all chunks
    # ============================================================
    step_start = time.time()

    retriever = Retriever(embedder)
    retrieved_chunks = retriever.retrieve_all_section_chunks(
        queries=queries,
        sources_hash=sources_hash,
        top_k=top_k,
        threshold=threshold
    )

    step_info = {
        'step': '6_retrieved',
        'total_chunks': len(retrieved_chunks),
        'time_seconds': round(time.time() - step_start, 2),
        'chunks': [{
            'source': c.get('source'),
            'chunk_index': c.get('chunk_index'),
            'similarity': c.get('similarity'),
            'matched_section': c.get('matched_section'),
            'word_count': c.get('word_count'),
            'text_preview': c.get('text', '')[:100]
        } for c in retrieved_chunks]
    }

    log_msg = (f"[{task_id}] Step 6 - Retrieve: {len(retrieved_chunks)} unique chunks "
               f"from {len(queries)} per-section searches ({step_info['time_seconds']}s)")
    logger.info(log_msg)

    if not retrieved_chunks:
        logger.warning(f"[{task_id}] ⚠ RAG returned 0 relevant chunks!")

    if log_dir:
        _write_log(log_dir, '06_retrieved_chunks.json',
                   json.dumps(step_info, ensure_ascii=False, indent=2))

    # ============================================================
    # Step 7: Build context grouped by section + anonymize + generate prompt
    # ============================================================
    # Use grouped format: chunks organized by matched_section
    context_text = retriever.format_chunks_grouped_by_section(retrieved_chunks)

    # Анонімізуємо контекст перед відправкою в LLM
    from .anonymizer import get_anonymizer
    anonymizer = get_anonymizer()
    context_anon = anonymizer.anonymize(context_text)
    logger.info(f"[{task_id}] Context anonymized: {len(context_text)} → {len(context_anon)} chars, "
                f"{len(anonymizer.get_map())} placeholders")

    prompt = _build_rag_prompt_v2(
        parsed_prompt=parsed_prompt,
        context_text=context_anon,
        user_prompt=user_prompt
    )

    total_time = round(time.time() - total_start, 2)

    step_info = {
        'step': '7_prompt',
        'total_pipeline_time_seconds': total_time,
        'context_chars': len(context_text),
        'context_words': len(context_text.split()),
        'prompt_chars': len(prompt),
        'params': params
    }

    log_msg = (f"[{task_id}] Step 7 - Prompt: {step_info['context_words']} context words, "
               f"{step_info['prompt_chars']} total chars")
    logger.info(log_msg)
    if log_dir:
        _write_log(log_dir, '07_prompt.txt',
                   json.dumps(step_info, ensure_ascii=False, indent=2))
        _write_log(log_dir, '07_context_for_llm.txt', context_text)

    logger.info(f"[{task_id}] ===== Enhanced RAG Complete: {total_time}s =====")

    return prompt


def _build_rag_prompt_v2(
    parsed_prompt: ParsedPrompt,
    context_text: str,
    user_prompt: Optional[str] = None
) -> str:
    """
    Build LLM prompt from parsed prompt + per-section chunks.

    Unlike _build_rag_prompt(), this uses the already-parsed structure
    from the full_doc_prompt, avoiding re-parsing.

    Args:
        parsed_prompt: ParsedPrompt from prompt_parser
        context_text: Formatted context with chunks grouped by section
        user_prompt: Optional user instructions

    Returns:
        Complete prompt string for LLM
    """
    from ..config import FULL_DOCUMENT_GENERATION_PROMPT

    parsed = parsed_prompt.parsed_analysis
    import json as json_mod

    # Build structure JSON (same format as original prompt)
    structure = []
    for section in parsed.sections:
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

    for table in parsed.tables:
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

    # Extract instructions
    instructions = "\n".join(
        f"- Розділ '{section.title}': {section.description_general}"
        for section in parsed.sections
        if section.has_text and section.description_general
    )

    structure_json = json_mod.dumps(structure, ensure_ascii=False, indent=2)

    prompt = FULL_DOCUMENT_GENERATION_PROMPT.format(
        instructions=instructions,
        structure=structure_json,
        sources=context_text
    )

    # Add note about per-section chunk organization
    prompt += (
        "\n\n## ПРИМІТКА\n"
        "Надані джерела згруповані за розділами документа. "
        "Кожен блок '=== Назва розділу ===' містить фрагменти, "
        "релевантні саме цьому розділу. Використовуйте їх як основний матеріал. "
        "Якщо для розділу не вистачає даних — напишіть 'Дані відсутні'."
    )

    if user_prompt:
        prompt += f"\n\n## ДОДАТКОВІ ВИМОГИ\n\n{user_prompt}"

    return prompt
