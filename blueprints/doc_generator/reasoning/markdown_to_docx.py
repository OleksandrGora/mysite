"""
Markdown to DOCX Converter - Converts markdown text to DOCX format.
"""
import re
from pathlib import Path
from typing import List
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
            
            if not in_table:
                # Start new table
                in_table = True
                table_rows = [cells]
            else:
                table_rows.append(cells)
            continue
        
        if in_table and (line.strip() == '' or line.strip().startswith('|---')):
            # Table separator or end of table
            if len(table_rows) > 1:  # Skip if only header row
                # Create table
                table = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
                table.style = 'Table Grid'
                
                for i, row_data in enumerate(table_rows):
                    for j, cell_text in enumerate(row_data):
                        if j < len(table_rows[0]):  # Ensure column exists
                            cell = table.cell(i, j)
                            cell.text = cell_text or ''
            
            # Reset table state
            in_table = False
            table_rows = []
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
            
            # Process markdown formatting in text
            processed_text = process_inline_formatting(line.strip())
            current_paragraph.add_run(processed_text)
        else:
            current_paragraph = None
    
    # Save document
    doc.save(output_path)
    logger.success(f"DOCX saved: {output_path}")


def process_inline_formatting(text: str) -> str:
    """
    Process inline markdown formatting (bold, italic).
    
    Args:
        text: Text with markdown formatting
        
    Returns:
        Processed text (formatting preserved for now)
    """
    # For now, preserve the markdown formatting
    # In the future, we could apply actual formatting to DOCX runs
    return text


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
