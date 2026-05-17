"""
Chunked Analysis - Split large documents into chunks, analyze independently, merge results.
"""
import json
import re
from typing import List, Dict, Any
from loguru import logger


def split_text_into_chunks(text: str, max_chunk_size: int = 100000) -> List[str]:
    from ..routes import append_debug_log

    if not text:
        append_debug_log("[CHUNKED] split_text_into_chunks: empty text, returning []")
        return []

    if len(text) <= max_chunk_size:
        append_debug_log(f"[CHUNKED] split_text_into_chunks: text fits in one chunk ({len(text)} chars)")
        return [text]

    append_debug_log(f"[CHUNKED] split_text_into_chunks: splitting {len(text)} chars, max_chunk={max_chunk_size}")

    heading_matches = list(re.finditer(r'^(#{1,6})\s+', text, re.MULTILINE))

    append_debug_log(f"[CHUNKED] split_text_into_chunks: {len(heading_matches)} headings found, {len(text)} chars total")

    if len(heading_matches) > 1:
        boundaries = [0] + [m.start() for m in heading_matches[1:]]
        boundaries.append(len(text))

        sections = []
        for i in range(len(boundaries) - 1):
            s = text[boundaries[i]:boundaries[i + 1]]
            if s.strip():
                sections.append(s)

        chunks = []
        current = ""
        for section in sections:
            if not current:
                current = section
            elif len(current) + len(section) <= max_chunk_size:
                current += section
            else:
                chunks.append(current.strip())
                current = section
        if current:
            chunks.append(current.strip())
        append_debug_log(f"[CHUNKED] split by headings: {len(chunks)} chunks, sizes: {[len(c) for c in chunks]}")
        return chunks

    paragraphs = [p for p in re.split(r'\n\n+', text) if p.strip()]
    if len(paragraphs) > 1:
        chunks = []
        current = ""
        for para in paragraphs:
            if not current:
                current = para
            elif len(current) + 2 + len(para) <= max_chunk_size:
                current += "\n\n" + para
            else:
                chunks.append(current)
                current = para
        if current:
            chunks.append(current)
        append_debug_log(f"[CHUNKED] split by paragraphs: {len(chunks)} chunks, sizes: {[len(c) for c in chunks]}")
        return chunks

    chunks = []
    for i in range(0, len(text), max_chunk_size):
        chunk = text[i:i + max_chunk_size].strip()
        if chunk:
            chunks.append(chunk)
    append_debug_log(f"[CHUNKED] split by char: {len(chunks)} chunks")
    return chunks


def _parse_chunk_items(chunk_result: str, chunk_index: int = -1) -> List[Dict[str, Any]]:
    from ..routes import append_debug_log
    chunk_tag = f"[CHUNKED:chunk#{chunk_index}]" if chunk_index >= 0 else "[CHUNKED]"

    chunk_result = chunk_result.strip()

    if chunk_result.startswith('```json'):
        chunk_result = chunk_result[7:]
    if chunk_result.startswith('```'):
        chunk_result = chunk_result[3:]
    if chunk_result.endswith('```'):
        chunk_result = chunk_result[:-3]
    chunk_result = chunk_result.strip()

    if chunk_result.startswith('['):
        try:
            items = json.loads(chunk_result)
            if isinstance(items, list):
                append_debug_log(f"{chunk_tag} parsed as JSON: {len(items)} items")
                return items
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"{chunk_tag} starts with '[' but is not valid JSON, falling back to text parsing")
            append_debug_log(f"{chunk_tag} JSON parse failed, falling back to text parsing")

    from .analysis_parser import parse_analysis
    parsed = parse_analysis(chunk_result)

    items = []
    for section in parsed.sections:
        words_str = ""
        if section.word_count_range:
            words_str = f"від {section.word_count_range[0]} до {section.word_count_range[1]} слів"
        items.append({
            "id": section.index,
            "type": "text",
            "title": section.title,
            "level": section.level,
            "words": words_str,
            "description": {
                "general": section.description_general,
                "specific": section.description_specific
            }
        })
    for table in parsed.tables:
        items.append({
            "id": table.index,
            "type": "table",
            "title": table.title,
            "level": table.level,
            "rows": table.rows,
            "columns": table.columns,
            "first_column_data": table.first_column_data
        })

    items.sort(key=lambda x: x['id'])
    append_debug_log(f"{chunk_tag} parsed via text parser: {len(parsed.sections)} sections, {len(parsed.tables)} tables → {len(items)} items")
    return items


def merge_analyses(chunks_results: List[str]) -> str:
    from ..routes import append_debug_log
    all_items: List[Dict[str, Any]] = []
    next_id = 1

    append_debug_log(f"[CHUNKED] merge_analyses: merging {len(chunks_results)} chunks")

    for i, chunk_result in enumerate(chunks_results):
        if not chunk_result or not chunk_result.strip():
            logger.warning(f"[CHUNKED] Skipping empty chunk result at index {i}")
            append_debug_log(f"[CHUNKED] chunk#{i} is empty, skipping")
            continue

        preview = chunk_result.strip()[:200].replace('\n', ' ')
        append_debug_log(f"[CHUNKED] merge_analyses: parsing chunk#{i} ({len(chunk_result)} chars, preview: {preview}...)")

        try:
            items = _parse_chunk_items(chunk_result, chunk_index=i)
        except Exception as e:
            logger.warning(f"[CHUNKED] Failed to parse chunk result at index {i}: {e}")
            append_debug_log(f"[CHUNKED] chunk#{i} parse error: {e}")
            continue

        append_debug_log(f"[CHUNKED] chunk#{i} produced {len(items)} items")
        for item in items:
            item['id'] = next_id
            next_id += 1
            all_items.append(item)

    append_debug_log(f"[CHUNKED] merge_analyses: total {len(all_items)} items across all chunks")
    return json.dumps(all_items, ensure_ascii=False, indent=2)
