"""
Markdown to DOCX Converter - Converts markdown text to DOCX format.
"""
import re
from pathlib import Path
from typing import List, Optional
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from loguru import logger


def convert_markdown_to_docx(markdown_text: str, output_path: str) -> None:
    """
    Convert markdown text to DOCX document.
    
    Args:
        markdown_text: Markdown content to convert
        output_path: Path to save DOCX file
    """
    logger.info(f"Converting markdown to DOCX: {len(markdown_text)} chars")
    
    # Create new document
    doc = Document()
    
    # Set default font
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(14)
    
    # Process markdown line by line
    lines = markdown_text.split('\n')
    current_paragraph = None
    in_table = False
    table_rows = []
    
    for line in lines:
        line = line.rstrip()
        
        # Handle headers
        if line.startswith('#'):
            # Close any open paragraph
            if current_paragraph:
                current_paragraph = None
            
            # Count header level
            level = len(line) - len(line.lstrip('#'))
            title = line.lstrip('#').strip()
            
            # Add heading with appropriate style
            if level == 1:
                doc.add_paragraph(title, style='Heading 1')
            elif level == 2:
                doc.add_paragraph(title, style='Heading 2')
            elif level == 3:
                doc.add_paragraph(title, style='Heading 3')
            elif level == 4:
                doc.add_paragraph(title, style='Heading 4')
            else:
                doc.add_paragraph(title, style='Heading 5')
            
            continue
        
        # Handle tables
        if '|' in line and line.strip().startswith('|') and line.strip().endswith('|'):
            # This looks like a table row
            cells = [cell.strip() for cell in line.split('|')[1:-1]]  # Remove empty first/last
            
            # Skip separator rows (e.g. |---|---|---|---|)
            # Separator consists only of '---' cells (possibly with spaces)
            if cells and all(re.match(r'^-+\s*$', cell) or re.match(r'^\s*-+\s*$', cell) or cell == '' for cell in cells):
                continue
            
            if not in_table:
                # Start new table
                in_table = True
                table_rows = [cells]
            else:
                table_rows.append(cells)
            continue
        
        if in_table and (line.strip() == '' or line.strip().startswith('|---') or 
                         (line.strip() and not line.strip().startswith('|') and not line.strip().startswith('#') and 
                          not line.strip().startswith(('- ', '* ', '+ ')) and not re.match(r'^\d+\.\s', line.strip()))):
            # Table separator, end of table, or non-table content encountered
            if len(table_rows) >= 1:  # Create table even with just header row
                # Create table
                table = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
                table.style = 'Table Grid'
                
                for i, row_data in enumerate(table_rows):
                    for j, cell_text in enumerate(row_data):
                        if j < len(table_rows[0]):  # Ensure column exists
                            cell = table.cell(i, j)
                            # Apply markdown formatting (**bold**, *italic*) to cell
                            apply_inline_formatting_to_cell(cell, cell_text or '')
            
            # Reset table state
            in_table = False
            table_rows = []
            
            # If this was non-table content, process it
            if line.strip() and not line.strip().startswith('|---') and not line.strip() == '':
                # Continue processing this line as regular content
                pass
            else:
                continue
        
        # Handle lists
        if line.strip().startswith(('- ', '* ', '+ ')):
            # Close any open paragraph
            if current_paragraph:
                current_paragraph = None
            
            item_text = line.strip()[2:]  # Remove marker
            doc.add_paragraph(item_text, style='List Bullet')
            continue
        
        if re.match(r'^\d+\.\s', line.strip()):
            # Close any open paragraph
            if current_paragraph:
                current_paragraph = None
            
            item_text = re.sub(r'^\d+\.\s', '', line.strip())
            doc.add_paragraph(item_text, style='List Number')
            continue
        
        # Handle empty lines
        if not line.strip():
            if current_paragraph:
                current_paragraph = None
            doc.add_paragraph()  # Empty paragraph
            continue
        
        # Handle regular text
        if line.strip():
            if current_paragraph is None:
                current_paragraph = doc.add_paragraph()
            
            # Process markdown formatting in text (**bold**, *italic*)
            apply_inline_formatting_to_paragraph(current_paragraph, line.strip())
        else:
            current_paragraph = None
    
    # Handle any remaining table at end of document
    if in_table and table_rows:
        if len(table_rows) >= 1:  # Create table even with just header
            table = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
            table.style = 'Table Grid'
            
            for i, row_data in enumerate(table_rows):
                for j, cell_text in enumerate(row_data):
                    if j < len(table_rows[0]):  # Ensure column exists
                        cell = table.cell(i, j)
                        apply_inline_formatting_to_cell(cell, cell_text or '')
            
            logger.info(f"Created table at end of document: {len(table_rows)} rows x {len(table_rows[0])} columns")
    
    # Save document
    doc.save(output_path)
    logger.success(f"DOCX saved: {output_path}")


def post_process_tables(
    docx_path: str,
    table_structures: List[dict],
    task_id: str = "unknown"
) -> None:
    """
    Post-process tables in DOCX file to enforce correct structure from analysis.

    Ensures:
    1. Header row contains column names from `columns`
    2. First column contains data from `first_column_data`
    3. Table has at least the specified number of rows (from `rows`)

    Args:
        docx_path: Path to the generated DOCX file
        table_structures: List of table dicts from parsed analysis
            (each has: title, columns, rows, first_column_data)
        task_id: For logging
    """
    if not table_structures:
        logger.info(f"[{task_id}] No table structures for post-processing")
        return

    logger.info(f"[{task_id}] Post-processing {len(table_structures)} tables in DOCX")

    # Open the generated document
    doc = Document(docx_path)
    docx_tables = list(doc.tables)

    logger.info(f"[{task_id}] Found {len(docx_tables)} tables in DOCX")

    for idx, table_struct in enumerate(table_structures):
        if idx >= len(docx_tables):
            logger.warning(f"[{task_id}] Table {idx} (title={table_struct.get('title','')}) "
                           f"not found in DOCX — only {len(docx_tables)} tables exist")
            continue

        table = docx_tables[idx]
        columns = table_struct.get("columns", [])
        first_column_data = table_struct.get("first_column_data")
        expected_rows = table_struct.get("rows")  # includes header

        logger.info(f"[{task_id}] Processing table {idx}: "
                     f"title='{table_struct.get('title','')}', "
                     f"expected_rows={expected_rows}, "
                     f"columns={len(columns)}, "
                     f"has_first_col_data={first_column_data is not None}")

        # --- Fix header row ---
        if columns:
            existing_cols = len(table.rows[0].cells) if table.rows else 0
            for col_idx, header_text in enumerate(columns):
                if col_idx < existing_cols:
                    current_text = table.rows[0].cells[col_idx].text.strip()
                    # Only overwrite if empty or different placeholder
                    if not current_text or current_text == '---':
                        table.rows[0].cells[col_idx].text = header_text
                        logger.info(f"[{task_id}]   Fixed header [{col_idx}]: "
                                     f"'{current_text}' → '{header_text}'")

        # --- Fix first column data ---
        if first_column_data:
            for row_idx, cell_value in enumerate(first_column_data):
                data_row_idx = row_idx + 1  # +1 because row 0 is header
                if data_row_idx < len(table.rows):
                    current_text = table.rows[data_row_idx].cells[0].text.strip()
                    if not current_text or current_text == '---':
                        table.rows[data_row_idx].cells[0].text = cell_value
                        logger.info(f"[{task_id}]   Fixed first col row {data_row_idx}: "
                                     f"'{current_text}' → '{cell_value}'")

        # --- Ensure minimum number of rows ---
        if expected_rows and len(table.rows) < expected_rows:
            current_rows = len(table.rows)
            rows_to_add = expected_rows - current_rows
            logger.info(f"[{task_id}]   Adding {rows_to_add} rows "
                         f"(current={current_rows}, expected={expected_rows})")
            for _ in range(rows_to_add):
                # Add a row by copying the last row's structure
                row = table.add_row()

        logger.info(f"[{task_id}]   Table now has {len(table.rows)} rows × "
                     f"{len(table.columns)} columns")

    # Save changes
    doc.save(docx_path)
    logger.success(f"[{task_id}] Post-processing complete, saved: {docx_path}")


def parse_markdown_inline(text: str) -> list:
    """
    Parse inline markdown formatting and return segments with formatting flags.
    
    Handles:
    - `**bold text**` → bold=True
    - `*italic text*` → italic=True
    - `` `code text` `` → (rendered as plain text)
    
    Args:
        text: Text with markdown formatting
        
    Returns:
        List of dicts: [{"text": "...", "bold": bool, "italic": bool}, ...]
    """
    segments = []
    # Regex to match **bold** or *italic* or `code`
    # Priority: ** (bold) first, then * (italic), then `code`, then plain text
    pattern = r'(\*\*(.+?)\*\*|(\*)(.+?)\3|`(.+?)`)'
    
    last_end = 0
    for match in re.finditer(pattern, text):
        # Add plain text before this match
        if match.start() > last_end:
            segments.append({
                "text": text[last_end:match.start()],
                "bold": False,
                "italic": False
            })
        
        if match.group(1) and match.group(1).startswith('**'):
            # Bold **text**
            segments.append({
                "text": match.group(2),
                "bold": True,
                "italic": False
            })
        elif match.group(3) == '*' and match.group(4):
            # Italic *text*
            segments.append({
                "text": match.group(4),
                "bold": False,
                "italic": True
            })
        elif match.group(5):
            # Inline code `text` — render as plain
            segments.append({
                "text": match.group(5),
                "bold": False,
                "italic": False
            })
        
        last_end = match.end()
    
    # Add remaining plain text
    if last_end < len(text):
        segments.append({
            "text": text[last_end:],
            "bold": False,
            "italic": False
        })
    
    # Filter out empty segments
    return [s for s in segments if s["text"]]


def apply_inline_formatting_to_paragraph(paragraph, text: str):
    """
    Apply inline markdown formatting (**bold**, *italic*) to a paragraph.
    
    Adds formatted runs to the paragraph (does NOT clear existing runs,
    since consecutive text lines reuse the same paragraph).
    
    Args:
        paragraph: docx Paragraph object
        text: Text with markdown formatting
    """
    # Parse markdown and create formatted runs
    segments = parse_markdown_inline(text)
    
    for seg in segments:
        if seg["text"]:
            run = paragraph.add_run(seg["text"])
            if seg["bold"]:
                run.bold = True
            if seg["italic"]:
                run.italic = True


def apply_inline_formatting_to_cell(cell, text: str):
    """
    Apply inline markdown formatting (**bold**, *italic*) to a table cell.
    
    Args:
        cell: docx Cell object
        text: Text with markdown formatting
    """
    # Clear cell content
    for paragraph in cell.paragraphs:
        for run in paragraph.runs:
            run.text = ""
    
    # Use first paragraph (cells always have at least one)
    paragraph = cell.paragraphs[0]
    
    # Parse markdown and create formatted runs
    segments = parse_markdown_inline(text)
    
    for seg in segments:
        if seg["text"]:
            run = paragraph.add_run(seg["text"])
            if seg["bold"]:
                run.bold = True
            if seg["italic"]:
                run.italic = True


if __name__ == '__main__':
    # Test the converter
    test_markdown = """# Тестовий документ

## Розділ 1

Це звичайний текст з **жирним** форматуванням та *курсивом*.

### Підрозділ 1.1

- Елемент списку 1
- Елемент списку 2
- Елемент списку 3

## Розділ 2

| Колонка 1 | Колонка 2 | Колонка 3 |
|-----------|-----------|-----------|
| Дані 1    | Дані 2    | Дані 3    |
| Дані 4    | Дані 5    | Дані 6    |

1. Нумерований елемент 1
2. Нумерований елемент 2

### Заключний розділ

Текст після таблиці."""
    
    output_path = "/tmp/test_output.docx"
    convert_markdown_to_docx(test_markdown, output_path)
    print(f"Test document saved: {output_path}")
