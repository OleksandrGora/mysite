"""
Analysis Parser - Parses the analysis result from template analysis.

Supports two formats:
1. Old text format: N| Назва[рівень] | Опис ## Наприклад: приклад |
2. New JSON format: [{"id": 1, "type": "text/table", "title": "...", "level": 0, ...}]
"""
import json
import re
from typing import List, Dict, Any, Optional, Union
from loguru import logger
from dataclasses import dataclass, asdict


@dataclass
class Section:
    """Document section from analysis."""
    index: int
    title: str
    level: int
    description_general: str
    description_specific: str
    has_text: bool
    word_count_range: Optional[tuple] = None  # (min, max)


@dataclass
class Table:
    """Table from analysis."""
    index: int
    title: str
    level: int
    columns: List[str]


@dataclass
class ParsedAnalysis:
    """Complete parsed analysis structure."""
    sections: List[Section]
    tables: List[Table]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'sections': [asdict(s) for s in self.sections],
            'tables': [asdict(t) for t in self.tables]
        }


def parse_json_analysis(analysis_text: str) -> ParsedAnalysis:
    """
    Parse analysis in JSON format.
    
    Args:
        analysis_text: JSON string with analysis
        
    Returns:
        ParsedAnalysis with sections and tables
    """
    logger.info("Parsing JSON analysis...")
    
    try:
        data = json.loads(analysis_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {e}")
        # Fall back to text format
        return parse_text_analysis(analysis_text)
    
    if not isinstance(data, list):
        logger.error("JSON analysis must be a list")
        raise ValueError("Invalid JSON format: expected list")
    
    sections = []
    tables = []
    
    for item in data:
        item_type = item.get('type')
        item_id = item.get('id', 0)
        title = item.get('title', '')
        level = item.get('level', 1)
        
        if item_type == 'table':
            tables.append(Table(
                index=item_id,
                title=title,
                level=level,
                columns=item.get('columns', [])
            ))
        elif item_type == 'text':
            description = item.get('description', {})
            desc_general = description.get('general', '') if isinstance(description, dict) else str(description)
            desc_specific = description.get('specific', '') if isinstance(description, dict) else ''
            
            # Parse word count range if present
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
                has_text=True,  # JSON format always has text
                word_count_range=word_count_range
            ))
    
    logger.info(f"Parsed JSON: {len(sections)} sections, {len(tables)} tables")
    return ParsedAnalysis(sections=sections, tables=tables)


def parse_analysis_line(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse a single line from analysis (old text format).
    
    Returns dict with type='section' or type='table', or None if invalid.
    """
    line = line.strip()
    if not line:
        return None
    
    # Split by first |
    parts = line.split('|', 1)
    if len(parts) != 2:
        return None
    
    index_part = parts[0].strip()
    content_part = parts[1].strip()
    
    try:
        index = int(index_part)
    except ValueError:
        return None
    
    # Check if it's a table
    if 'Таблиця' in content_part or content_part.lower().startswith('таблиця'):
        return parse_table_line(index, content_part)
    else:
        return parse_section_line(index, content_part)


def parse_section_line(index: int, content: str) -> Dict[str, Any]:
    """Parse a section line: Назва[рівень] | Опис ## Наприклад: приклад"""
    # Extract title and level
    title_match = re.search(r'([^\[\]]+)\[(\d+)\]', content)
    if not title_match:
        # Fallback: no level specified
        title = content.split('|')[0].strip()
        level = 1
    else:
        title = title_match.group(1).strip()
        level = int(title_match.group(2))
    
    # Extract description and example
    if '##' in content:
        desc_part, example_part = content.split('##', 1)
        description = desc_part.split('|')[-1].strip() if '|' in desc_part else desc_part.strip()
        example = example_part.strip()
    else:
        description = content.split('|')[-1].strip() if '|' in content else content
        example = ""
    
    # Check word count range
    word_count_range = None
    word_match = re.search(r'\[від (\d+) до (\d+) слів\]', description)
    if word_match:
        min_words = int(word_match.group(1))
        max_words = int(word_match.group(2))
        word_count_range = (min_words, max_words)
        # Remove from description
        description = re.sub(r'\[від \d+ до \d+ слів\]', '', description).strip()
    
    # Check if has text (sections without | after title are headers only)
    has_text = '|' in content or '##' in content
    
    return {
        'type': 'section',
        'index': index,
        'title': title,
        'level': level,
        'description_general': description,
        'description_specific': example,
        'has_text': has_text,
        'word_count_range': word_count_range
    }


def parse_table_line(index: int, content: str) -> Dict[str, Any]:
    """Parse a table line: Таблиця Назва[рівень] [кол1 | кол2 | кол3]"""
    # Extract title and level
    title_match = re.search(r'Таблиця\s+([^\[\]]+)\[(\d+)\]', content, re.IGNORECASE)
    if not title_match:
        # Fallback
        title = content.replace('Таблиця', '').strip()
        level = 1
    else:
        title = title_match.group(1).strip()
        level = int(title_match.group(2))
    
    # Extract columns
    columns_match = re.search(r'\[([^\]]+)\]', content[content.find('['):])
    if columns_match:
        columns_str = columns_match.group(1)
        columns = [col.strip() for col in columns_str.split('|')]
    else:
        columns = []
    
    return {
        'type': 'table',
        'index': index,
        'title': title,
        'level': level,
        'columns': columns
    }


def parse_text_analysis(analysis_text: str) -> ParsedAnalysis:
    """
    Parse analysis in old text format.
    
    Args:
        analysis_text: Raw analysis from template analysis
        
    Returns:
        ParsedAnalysis with sections and tables
    """
    logger.info("Parsing text analysis...")
    
    sections = []
    tables = []
    
    lines = analysis_text.split('\n')
    for line in lines:
        parsed = parse_analysis_line(line)
        if parsed:
            if parsed['type'] == 'section':
                sections.append(Section(
                    index=parsed['index'],
                    title=parsed['title'],
                    level=parsed['level'],
                    description_general=parsed['description_general'],
                    description_specific=parsed['description_specific'],
                    has_text=parsed['has_text'],
                    word_count_range=parsed['word_count_range']
                ))
            elif parsed['type'] == 'table':
                tables.append(Table(
                    index=parsed['index'],
                    title=parsed['title'],
                    level=parsed['level'],
                    columns=parsed['columns']
                ))
    
    logger.info(f"Parsed text: {len(sections)} sections and {len(tables)} tables")
    
    return ParsedAnalysis(sections=sections, tables=tables)


def parse_analysis(analysis_text: str) -> ParsedAnalysis:
    """
    Parse complete analysis text (auto-detects JSON or text format).
    
    Args:
        analysis_text: Raw analysis from template analysis
        
    Returns:
        ParsedAnalysis with sections and tables
    """
    analysis_text = analysis_text.strip()
    
    # Try to detect JSON format
    if analysis_text.startswith('[') or analysis_text.startswith('{'):
        try:
            return parse_json_analysis(analysis_text)
        except (json.JSONDecodeError, ValueError):
            logger.info("Not valid JSON, falling back to text format")
    
    # Use text format
    return parse_text_analysis(analysis_text)


def build_markdown_structure(analysis: ParsedAnalysis) -> str:
    """
    Build a markdown structure representation from parsed analysis.
    Useful for debugging or as input to LLM.
    """
    lines = []
    
    for section in analysis.sections:
        prefix = '#' * section.level
        lines.append(f"{prefix} {section.title}")
        if section.has_text:
            if section.description_general:
                lines.append(f"*{section.description_general}*")
            if section.description_specific:
                lines.append(f"Приклад: {section.description_specific}")
        lines.append("")
    
    for table in analysis.tables:
        prefix = '#' * table.level
        lines.append(f"{prefix} {table.title}")
        if table.columns:
            lines.append(f"Columns: {', '.join(table.columns)}")
        lines.append("")
    
    return '\n'.join(lines)


if __name__ == '__main__':
    # Test with example
    test_analysis = """1| Лабораторна робота №8[0] | Ідентифікація типу та номера роботи ## Наприклад: це лабораторна робота №8 |
2| Дослідження якості сухих яєчних продуктів[0] | Формулювання теми або предмету дослідження ## Наприклад: темою є дослідження якості сухих яєчних продуктів |
3| Мета роботи[1] | Визначення основних завдань та очікуваних результатів ## Наприклад: метою є визначення якості сухих яйцепродуктів за органолептичними і фізико-хімічними показниками та перелік знань і вмінь студента |
10| Таблиця 1 Органолептичні показники сухих яєчних продуктів[3] [Показник | Характеристика | Яєчного порошку | Білка яєчного сухого | Жовтка яєчного сухого] |"""
    
    parsed = parse_analysis(test_analysis)
    print("Sections:", len(parsed.sections))
    print("Tables:", len(parsed.tables))
    for s in parsed.sections:
        print(f"  - {s.title} (level {s.level})")
    for t in parsed.tables:
        print(f"  - Table: {t.title} (cols: {t.columns})")
