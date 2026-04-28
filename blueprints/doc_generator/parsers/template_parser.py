"""
Template Parser - Extracts structure from DOCX or PDF template files.

Parses:
- Document sections with headers and hierarchy
- Tables with column count and headers
- Paragraph styles (headings, body text)
- Formatting hints

Returns JSON structure describing the template.
"""
import json
import os
import re
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from loguru import logger

# PDF support
import fitz  # PyMuPDF


def parse_template(template_path: str) -> Dict[str, Any]:
    """
    Main entry point: Parse a template file (DOCX or PDF).
    
    Args:
        template_path: Path to the template file
        
    Returns:
        Dictionary with template structure
    """
    ext = os.path.splitext(template_path)[1].lower()
    
    if ext == '.pdf':
        return parse_pdf_template(template_path)
    elif ext == '.docx':
        return parse_docx_template(template_path)
    elif ext == '.txt':
        return parse_txt_template(template_path)
    else:
        raise ValueError(f"Unsupported template format: {ext}. Use DOCX, PDF or TXT.")


@dataclass
class TableInfo:
    """Information about a table in the template."""
    index: int
    rows: int
    columns: int
    headers: List[str]
    has_header_row: bool
    sample_row_cells: int  # Number of cells in a data row (for complex tables)


@dataclass
class SectionInfo:
    """Document section with header hierarchy."""
    level: int  # 1=Title, 2=Heading1, 3=Heading2, etc.
    title: str
    paragraphs: int  # Number of body paragraphs in this section
    has_table: bool
    table_index: Optional[int] = None  # Which table belongs to this section


@dataclass
class TemplateStructure:
    """Complete template structure."""
    title: Optional[str]
    sections: List[SectionInfo]
    tables: List[TableInfo]
    total_paragraphs: int
    style_names: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'title': self.title,
            'sections': [asdict(s) for s in self.sections],
            'tables': [asdict(t) for t in self.tables],
            'total_paragraphs': self.total_paragraphs,
            'style_names': self.style_names,
        }


def parse_table(table: Table, table_index: int) -> TableInfo:
    """Parse a single table and extract its structure."""
    rows = len(table.rows)
    columns = len(table.columns)
    
    # Try to extract headers from first row
    headers = []
    has_header_row = False
    if rows > 0:
        first_row = table.rows[0]
        headers = [cell.text.strip() for cell in first_row.cells]
        # Check if first row looks like headers (not empty, different from typical data)
        if any(headers) and rows > 1:
            has_header_row = True
    
    # Sample row cells count (for tables with merged cells)
    sample_cells = 0
    if rows > (1 if has_header_row else 0):
        data_row_idx = 1 if has_header_row else 0
        if data_row_idx < rows:
            sample_cells = len(table.rows[data_row_idx].cells)
    
    return TableInfo(
        index=table_index,
        rows=rows,
        columns=columns,
        headers=headers,
        has_header_row=has_header_row,
        sample_row_cells=sample_cells
    )


def get_heading_level(paragraph: Paragraph) -> int:
    """
    Determine heading level from paragraph style.
    Returns 0 for non-heading paragraphs.
    """
    style_name = paragraph.style.name if paragraph.style else ""
    
    # Check for heading styles
    if style_name.startswith('Heading '):
        try:
            return int(style_name.split()[-1])
        except ValueError:
            pass
    
    # Common Ukrainian heading style names
    heading_map = {
        'Title': 1,
        'Заголовок': 1,
        'Heading 1': 1,
        'Heading 2': 2,
        'Heading 3': 3,
        'Heading 4': 4,
        'Heading 5': 5,
        'Heading 6': 6,
        'Заголовок 1': 1,
        'Заголовок 2': 2,
        'Заголовок 3': 3,
    }
    
    return heading_map.get(style_name, 0)


def is_body_text(paragraph: Paragraph) -> bool:
    """Check if paragraph is body text (not heading, not empty)."""
    if not paragraph.text.strip():
        return False
    
    if get_heading_level(paragraph) > 0:
        return False
    
    # Skip table paragraphs (they're handled separately)
    style_name = paragraph.style.name if paragraph.style else ""
    if 'Table' in style_name:
        return False
    
    return True


def parse_pdf_template(pdf_path: str) -> Dict[str, Any]:
    """
    Parse a PDF file as a template - extract text structure.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        Dictionary with template structure
    """
    logger.info(f"Parsing PDF template: {pdf_path}")
    
    doc = fitz.open(pdf_path)
    
    # Extract text by pages and identify structure
    full_text = []
    sections = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        if text.strip():
            full_text.append(text)
            
            # Try to identify section headers (lines that look like headers)
            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                # Heuristic: short lines, often with numbers or colons
                if line and len(line) < 100 and (line.isupper() or re.match(r'^\d+[\.\)]', line)):
                    sections.append({
                        'title': line,
                        'level': 0 if line.isupper() else 1,
                        'page': page_num
                    })
    
    # Save page count before closing
    page_count = len(doc)
    
    # Extract tables from PDF
    tables = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        try:
            tabs = page.find_tables()
            for tab_idx, tab in enumerate(tabs.tables):
                if tab.col_count < 4 or tab.row_count < 2:
                    continue
                rows_data = []
                for row in tab.extract():
                    clean_row = [str(cell).strip().replace('\n', ' ') if cell else '' for cell in row]
                    if any(clean_row):
                        rows_data.append(clean_row)
                if rows_data:
                    tables.append({
                        'index': len(tables),
                        'page': page_num + 1,
                        'rows': len(rows_data),
                        'columns': tab.col_count,
                        'headers': rows_data[0] if rows_data else [],
                        'has_header_row': True,
                        'sample_data': rows_data[1:3]
                    })
        except Exception as e:
            logger.warning(f"Could not extract tables from page {page_num + 1}: {e}")
    
    doc.close()
    
    # Build result similar to DOCX structure
    result = {
        'title': os.path.basename(pdf_path),
        'source_file': pdf_path,
        'is_pdf': True,
        'pages': page_count,
        'sections': sections if sections else [
            {'title': 'Section 1', 'level': 0},
            {'title': 'Section 2', 'level': 0}
        ],
        'tables': tables,  # PDF tables harder to detect accurately
        'paragraph_count': len(full_text),
        'has_headers': len(sections) > 0
    }
    
    logger.info(f"PDF parsed: {result['pages']} pages, {len(sections)} sections detected")
    return result


def parse_txt_template(txt_path: str) -> Dict[str, Any]:
    """
    Parse a TXT file as a template - extract text structure.
    
    Args:
        txt_path: Path to the TXT file
        
    Returns:
        Dictionary with template structure
    """
    logger.info(f"Parsing TXT template: {txt_path}")
    
    with open(txt_path, 'r', encoding='utf-8') as f:
        text_content = f.read()
    
    # Try to identify sections (lines that look like headers)
    lines = text_content.split('\n')
    sections = []
    for i, line in enumerate(lines):
        line = line.strip()
        # Heuristic: short lines, often with numbers or uppercase
        if line and len(line) < 100 and (line.isupper() or re.match(r'^\d+[\.\)]', line)):
            sections.append({
                'title': line,
                'level': 0 if line.isupper() else 1,
                'line': i
            })
    
    result = {
        'title': os.path.basename(txt_path),
        'source_file': txt_path,
        'is_txt': True,
        'text': text_content,
        'sections': sections if sections else [
            {'title': 'Section 1', 'level': 0},
            {'title': 'Section 2', 'level': 0}
        ],
        'tables': [],  # No tables in plain text
        'paragraph_count': len([l for l in lines if l.strip()]),
        'has_headers': len(sections) > 0,
        'pages': 1
    }
    
    logger.info(f"TXT parsed: {len(text_content)} chars, {len(sections)} sections detected")
    return result


def parse_docx_template(docx_path: str) -> Dict[str, Any]:
    """
    Parse a DOCX template file and return its structure as JSON.
    
    Args:
        docx_path: Path to the DOCX template file
        
    Returns:
        Dictionary with template structure including sections, tables, and metadata
    """
    logger.info(f"Parsing DOCX template: {docx_path}")
    
    doc = Document(docx_path)
    
    # Collect all unique style names
    style_names = set()
    for para in doc.paragraphs:
        if para.style and para.style.name:
            style_names.add(para.style.name)
    
    # Parse tables
    tables = []
    for idx, table in enumerate(doc.tables):
        table_info = parse_table(table, idx)
        tables.append(table_info)
        logger.debug(f"Found table {idx}: {table_info.rows}x{table_info.columns}")
    
    # Parse sections (headings and their content)
    sections = []
    current_section: Optional[SectionInfo] = None
    body_paragraphs_count = 0
    current_table_index = 0
    
    # Track which tables have been assigned to sections
    tables_assigned = set()
    
    for element in doc.inline_shapes:
        # Note: This iterates shapes, but we need paragraphs and tables in order
        pass
    
    # Re-iterate properly through document elements
    # DOCX doesn't have a direct mixed iterator, so we scan paragraphs
    # and track table references
    
    last_heading_level = 0
    paragraphs_since_heading = 0
    
    for para in doc.paragraphs:
        heading_level = get_heading_level(para)
        
        if heading_level > 0:
            # Save previous section if exists
            if current_section:
                current_section.paragraphs = paragraphs_since_heading
                sections.append(current_section)
            
            # Start new section
            current_section = SectionInfo(
                level=heading_level,
                title=para.text.strip(),
                paragraphs=0,
                has_table=False,
                table_index=None
            )
            paragraphs_since_heading = 0
            last_heading_level = heading_level
            
        elif is_body_text(para):
            paragraphs_since_heading += 1
            body_paragraphs_count += 1
    
    # Don't forget the last section
    if current_section:
        current_section.paragraphs = paragraphs_since_heading
        sections.append(current_section)
    
    # Try to associate tables with sections
    # Simple heuristic: tables usually follow their section heading
    if tables and sections:
        for table_idx, table in enumerate(tables):
            # Find best matching section (simplified)
            # In a real implementation, we'd track position in document
            if table_idx < len(sections):
                sections[table_idx].has_table = True
                sections[table_idx].table_index = table.index
    
    # Determine document title
    title = None
    if sections:
        # First section with level 1 or 2 is likely the title
        for sec in sections:
            if sec.level <= 2:
                title = sec.title
                break
        if not title:
            title = sections[0].title
    
    structure = TemplateStructure(
        title=title,
        sections=sections,
        tables=tables,
        total_paragraphs=body_paragraphs_count,
        style_names=sorted(list(style_names))
    )
    
    result = structure.to_dict()
    
    logger.info(f"Template parsed: {len(sections)} sections, {len(tables)} tables")
    
    return result


def parse_template_to_json(docx_path: str, output_path: Optional[str] = None) -> str:
    """
    Parse template and optionally save to JSON file.
    
    Returns:
        JSON string of the template structure
    """
    structure = parse_template(docx_path)
    json_str = json.dumps(structure, ensure_ascii=False, indent=2)
    
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(json_str)
        logger.info(f"Template structure saved to: {output_path}")
    
    return json_str


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        docx_file = sys.argv[1]
        result = parse_template_to_json(docx_file)
        print(result)
    else:
        print("Usage: python template_parser.py <docx_file>")
