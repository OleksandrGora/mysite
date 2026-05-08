"""
Prompt Parser - Parses full_doc_prompt to extract structured data.

Detects whether the prompt has a structured format (## ІНСТРУКЦІЇ ДО РОЗДІЛІВ,
## СТРУКТУРА ДОКУМЕНТА blocks) or is unstructured (raw text).

If structured → returns parsed instructions, JSON structure, sources text.
If unstructured → returns None, so caller can fall back to existing pipeline.
"""

import json
import re
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, asdict
from loguru import logger

from ..parsers.analysis_parser import ParsedAnalysis, Section, Table


@dataclass
class ParsedPrompt:
    """Result of parsing a full_doc_prompt."""
    instructions: str               # Raw instructions block text
    parsed_analysis: ParsedAnalysis # Sections + tables from JSON structure
    sources_text: str               # Raw sources block text
    has_structure: bool = True      # Whether structured blocks were found


def detect_structure(prompt_text: str) -> bool:
    """
    Detect if prompt has structured blocks (## ІНСТРУКЦІЇ, ## СТРУКТУРА).
    
    Returns True if both blocks are present.
    """
    has_instructions = '## ІНСТРУКЦІЇ ДО РОЗДІЛІВ' in prompt_text
    has_structure = '## СТРУКТУРА ДОКУМЕНТА' in prompt_text
    
    # Also check English variants
    if not has_instructions:
        has_instructions = '## ІНСТРУКЦІЇ' in prompt_text or '## INSTRUCTIONS' in prompt_text.upper()
    if not has_structure:
        has_structure = '## СТРУКТУРА' in prompt_text or '## STRUCTURE' in prompt_text.upper()
    
    return has_instructions and has_structure


def _extract_block(prompt_text: str, header: str, next_header: Optional[str] = None) -> Optional[str]:
    """
    Extract content between `header` and `next_header` (or end of text).
    
    Args:
        prompt_text: Full prompt text
        header: Start marker (e.g., '## ІНСТРУКЦІЇ')
        next_header: Optional end marker. If None, search for next ## block.
    
    Returns:
        Extracted text or None if header not found
    """
    # Find the start
    start_idx = prompt_text.find(header)
    if start_idx == -1:
        return None
    
    start_idx += len(header)
    
    # Find the end
    if next_header:
        end_idx = prompt_text.find(next_header, start_idx)
        if end_idx == -1:
            end_idx = len(prompt_text)
    else:
        # Find next ## block or end
        remaining = prompt_text[start_idx:]
        next_block = re.search(r'\n## ', remaining)
        if next_block:
            end_idx = start_idx + next_block.start()
        else:
            end_idx = len(prompt_text)
    
    return prompt_text[start_idx:end_idx].strip()


def _extract_instructions_block(prompt_text: str) -> Optional[str]:
    """Extract the instructions block between ## ІНСТРУКЦІЇ and ## СТРУКТУРА."""
    # Try Ukrainian header first
    result = _extract_block(prompt_text, '## ІНСТРУКЦІЇ ДО РОЗДІЛІВ', '## СТРУКТУРА')
    if result:
        return result
    
    # Fallback: try shorter header
    result = _extract_block(prompt_text, '## ІНСТРУКЦІЇ', '## СТРУКТУРА')
    return result


def _extract_structure_block(prompt_text: str) -> Optional[str]:
    """Extract the JSON structure block between ## СТРУКТУРА and ## ДЖЕРЕЛА."""
    result = _extract_block(prompt_text, '## СТРУКТУРА ДОКУМЕНТА', '## ДЖЕРЕЛА')
    if result:
        return result
    result = _extract_block(prompt_text, '## СТРУКТУРА', '## ДЖЕРЕЛА')
    return result


def _extract_sources_block(prompt_text: str) -> Optional[str]:
    """Extract the sources block after ## ДЖЕРЕЛА."""
    result = _extract_block(prompt_text, '## ДЖЕРЕЛА', None)
    if result:
        return result
    # Try English
    result = _extract_block(prompt_text, '## SOURCES', None)
    return result


def _parse_instructions_to_dict(instructions_text: str) -> Dict[str, str]:
    """
    Parse instructions list into dict: {section_title: instruction_text}
    
    Input format:
        - Розділ 'Title': Instruction text here
        - Розділ 'Another Title': More text
    """
    result = {}
    pattern = r"- Розділ '([^']+)':\s*(.+)"
    
    for match in re.finditer(pattern, instructions_text):
        title = match.group(1).strip()
        instruction = match.group(2).strip()
        result[title] = instruction
    
    return result


def _try_parse_json_structure(structure_text: str) -> Optional[ParsedAnalysis]:
    """
    Try to parse JSON structure from the structure block.
    
    The block may contain JSON array directly or wrapped in ```json ... ```.
    """
    # Clean markdown code blocks
    text = structure_text.strip()
    if text.startswith('```json'):
        text = text[7:]
    elif text.startswith('```'):
        text = text[3:]
    if text.endswith('```'):
        text = text[:-3]
    text = text.strip()
    
    # Try to find JSON array in the text
    # Look for [ ... ] pattern
    json_match = re.search(r'(\[.*\])', text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_str = text
    
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON structure from prompt")
        return None
    
    if not isinstance(data, list):
        logger.warning("JSON structure is not a list")
        return None
    
    sections = []
    tables = []
    
    for i, item in enumerate(data):
        item_type = item.get('type', 'text')
        item_id = item.get('id', i + 1)
        title = item.get('title', '')
        level = item.get('level', 1)
        
        if item_type == 'table':
            columns = item.get('columns', [])
            rows = item.get('rows')
            first_column_data = item.get('first_column_data')
            tables.append(Table(
                index=item_id,
                title=title,
                level=level,
                columns=columns,
                rows=rows,
                first_column_data=first_column_data
            ))
        else:
            description = item.get('description', {}) or {}
            desc_general = ''
            desc_specific = ''
            
            if isinstance(description, dict):
                desc_general = description.get('general', '')
                desc_specific = description.get('specific', '')
            
            # Parse word count
            word_count_range = None
            words = item.get('words', '')
            if words and isinstance(words, str):
                match = re.search(r'від (\d+) до (\d+)', words)
                if match:
                    word_count_range = (int(match.group(1)), int(match.group(2)))
            
            sections.append(Section(
                index=item_id,
                title=title,
                level=level,
                description_general=desc_general,
                description_specific=desc_specific,
                has_text=True,
                word_count_range=word_count_range
            ))
    
    return ParsedAnalysis(sections=sections, tables=tables)


def parse_full_doc_prompt(prompt_text: str) -> ParsedPrompt:
    """
    Parse a full_doc_prompt and extract structured data.
    
    Detects if the prompt has structured blocks. If not, returns
    a ParsedPrompt with has_structure=False for fallback handling.
    
    Args:
        prompt_text: The full document prompt text
        
    Returns:
        ParsedPrompt with instructions, parsed_analysis, sources_text
    """
    # Step 1: Detect structure
    has_structure = detect_structure(prompt_text)
    
    if not has_structure:
        logger.info("Prompt has no structured blocks — returning unstructured result")
        return ParsedPrompt(
            instructions='',
            parsed_analysis=ParsedAnalysis(sections=[], tables=[]),
            sources_text=prompt_text,
            has_structure=False
        )
    
    # Step 2: Extract blocks
    instructions_block = _extract_instructions_block(prompt_text) or ''
    structure_block = _extract_structure_block(prompt_text) or ''
    sources_block = _extract_sources_block(prompt_text) or ''
    
    logger.info(f"Extracted: instructions={len(instructions_block)} chars, "
                f"structure={len(structure_block)} chars, "
                f"sources={len(sources_block)} chars")
    
    # Step 3: Parse JSON structure
    parsed_analysis = _try_parse_json_structure(structure_block)
    if parsed_analysis is None:
        logger.warning("Could not parse JSON structure — falling back to unstructured")
        return ParsedPrompt(
            instructions='',
            parsed_analysis=ParsedAnalysis(sections=[], tables=[]),
            sources_text=prompt_text,
            has_structure=False
        )
    
    logger.info(f"Parsed structure: {len(parsed_analysis.sections)} sections, "
                f"{len(parsed_analysis.tables)} tables")
    
    # Step 4: Parse instructions to dict for enrichment
    instructions_dict = _parse_instructions_to_dict(instructions_block)
    
    # Enrich sections with general descriptions from instructions if missing
    for section in parsed_analysis.sections:
        if not section.description_general and section.title in instructions_dict:
            section.description_general = instructions_dict[section.title]
    
    return ParsedPrompt(
        instructions=instructions_block,
        parsed_analysis=parsed_analysis,
        sources_text=sources_block,
        has_structure=True
    )


def build_parent_context(parsed_analysis: ParsedAnalysis) -> Dict[str, List[str]]:
    """
    Build parent context for each section.
    
    Returns dict: section_title → [parent_titles...]
    Useful for enriching search queries with hierarchical context.
    """
    # Build level-based hierarchy
    hierarchy = {}  # section_title -> list of parent titles
    level_stack = []  # stack of (level, title)
    
    all_items = []
    for s in parsed_analysis.sections:
        all_items.append(('section', s.title, s.level))
    for t in parsed_analysis.tables:
        all_items.append(('table', t.title, t.level))
    
    # Sort by index order (sections and tables are already in order)
    for item_type, title, level in all_items:
        # Pop stack entries deeper or equal to current level
        while level_stack and level_stack[-1][0] >= level:
            level_stack.pop()
        
        # The remaining stack are the parent sections
        parents = [t for _, t in level_stack]
        hierarchy[title] = parents
        
        # Push current item (only sections, not tables, can be parents)
        if item_type == 'section':
            level_stack.append((level, title))
    
    return hierarchy
