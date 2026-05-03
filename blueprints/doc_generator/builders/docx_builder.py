"""
DOCX Builder - Assembles final document from generated content.

Uses python-docx to:
1. Copy template as base
2. Insert generated content preserving styles
3. Build tables with proper formatting
4. Save final document
"""
import os
import json
from typing import List, Dict, Any, Optional, Tuple
from docx import Document
from docx.shared import Pt, Inches, RGBColor, Cm
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from loguru import logger


def copy_paragraph_style(source_para: Paragraph, target_para: Paragraph):
    """Copy style and formatting from source paragraph to target."""
    # Copy style
    if source_para.style:
        try:
            target_para.style = source_para.style
        except:
            pass
    
    # Copy direct formatting
    if source_para.runs:
        source_run = source_para.runs[0]
        target_run = target_para.runs[0] if target_para.runs else target_para.add_run()
        
        target_run.font.name = source_run.font.name
        target_run.font.size = source_run.font.size
        target_run.font.bold = source_run.font.bold
        target_run.font.italic = source_run.font.italic
        target_run.font.underline = source_run.font.underline
        if source_run.font.color.rgb:
            target_run.font.color.rgb = source_run.font.color.rgb


def find_paragraph_by_style(doc: Document, style_names: List[str]) -> Optional[Tuple[int, Paragraph]]:
    """Find first paragraph with matching style."""
    for idx, para in enumerate(doc.paragraphs):
        if para.style and para.style.name in style_names:
            return idx, para
    return None


def find_all_paragraphs_by_style(doc: Document, style_names: List[str]) -> List[Tuple[int, Paragraph]]:
    """Find all paragraphs with matching styles."""
    result = []
    for idx, para in enumerate(doc.paragraphs):
        if para.style and para.style.name in style_names:
            result.append((idx, para))
    return result


def clear_paragraph_content(para: Paragraph):
    """Remove all runs from paragraph but keep style."""
    for run in para.runs:
        run.text = ""
    # If no runs, add one
    if not para.runs:
        para.add_run()


def set_paragraph_text(para: Paragraph, text: str, preserve_style: bool = True):
    """Set paragraph text while preserving style."""
    # Clear existing content
    for run in para.runs:
        run.text = ""
    
    # Add new text to first run
    if para.runs:
        para.runs[0].text = text
    else:
        para.add_run(text)


def copy_table_format(source_table: Table, target_table: Table):
    """Copy formatting from source table to target."""
    # Copy column widths
    for idx, (source_col, target_col) in enumerate(zip(source_table.columns, target_table.columns)):
        if idx < len(source_table.columns) and idx < len(target_table.columns):
            try:
                target_col.width = source_col.width
            except:
                pass


def build_table_from_data(doc: Document, table_index: int, headers: List[str], rows: List[List[str]], 
                          template_table: Optional[Table] = None) -> Table:
    """Build a table with data, copying style from template table if available."""
    
    num_cols = len(headers) if headers else (len(rows[0]) if rows else 1)
    num_rows = 1 + len(rows)  # header + data rows
    
    # Add table to document
    new_table = doc.add_table(rows=num_rows, cols=num_cols)
    new_table.style = 'Table Grid'
    
    # Fill header
    if headers:
        header_cells = new_table.rows[0].cells
        for idx, header in enumerate(headers[:num_cols]):
            header_cells[idx].text = str(header)
    
    # Fill data rows
    for row_idx, row_data in enumerate(rows):
        if row_idx < len(new_table.rows) - 1:
            cells = new_table.rows[row_idx + 1].cells
            for col_idx, value in enumerate(row_data[:num_cols]):
                cells[col_idx].text = str(value)
    
    # Try to copy formatting from template
    if template_table:
        try:
            # Copy style
            if template_table.style:
                new_table.style = template_table.style
            
            # Copy cell formatting from template header row
            if template_table.rows and new_table.rows:
                template_header = template_table.rows[0]
                new_header = new_table.rows[0]
                for template_cell, new_cell in zip(template_header.cells, new_header.cells):
                    # Copy paragraph formatting
                    if template_cell.paragraphs and new_cell.paragraphs:
                        copy_paragraph_style(template_cell.paragraphs[0], new_cell.paragraphs[0])
        except Exception as e:
            logger.warning(f"Could not copy all table formatting: {e}")
    
    return new_table


def insert_section_content(doc: Document, section_title: str, content: str, 
                          heading_style: str = 'Heading 1', body_style: str = 'Normal'):
    """Insert a new section with heading and body content."""
    # Add heading
    heading = doc.add_paragraph(section_title, style=heading_style)
    
    # Add body paragraphs
    paragraphs = content.split('\n\n')
    for para_text in paragraphs:
        if para_text.strip():
            para = doc.add_paragraph(para_text.strip(), style=body_style)


def build_document(
    template_path: str,
    generated_content: Dict[str, Any],
    output_path: str,
    task_id: str = "unknown"
) -> str:
    """
    Build final document from template and generated content.
    
    Args:
        template_path: Path to original template (DOCX or PDF)
        generated_content: Content from document_builder.build_document_from_analysis()
        output_path: Where to save result
        task_id: For logging
        
    Returns:
        Path to saved document
    """
    logger.info(f"[{task_id}] Building document from {template_path}")
    
    # Check if template is PDF - use simple builder
    if template_path.lower().endswith('.pdf'):
        logger.warning(f"[{task_id}] Template is PDF, using simple builder")
        return build_document_simple(template_path, generated_content, output_path, task_id)
    
    # Load template
    doc = Document(template_path)
    
    # Get generated data
    document_title = generated_content.get('document_title', 'Document')
    sections = generated_content.get('sections', [])
    tables = generated_content.get('tables', [])
    
    logger.info(f"[{task_id}] Building: {document_title}, {len(sections)} sections, {len(tables)} tables")
    
    # Strategy: Try to find and replace content in template
    # If no matching sections found, append new content
    
    # Track what we've filled
    filled_indices = set()
    
    # Find all headings in template
    heading_styles = ['Title', 'Heading 1', 'Heading 2', 'Heading 3', 
                      'Заголовок', 'Заголовок 1', 'Заголовок 2', 'Заголовок 3']
    
    template_headings = find_all_paragraphs_by_style(doc, heading_styles)
    
    logger.info(f"[{task_id}] Found {len(template_headings)} headings in template")
    
    # Try to match generated sections to template headings
    for section in sections:
        section_title = section.get('title', '')
        section_content = section.get('content', '')
        
        if not section_title or not section_content:
            continue
        
        # Find matching heading
        matched = False
        for idx, (para_idx, para) in enumerate(template_headings):
            if para_idx in filled_indices:
                continue
                
            # Check if titles are similar (simple match)
            para_text = para.text.strip()
            if section_title.lower() in para_text.lower() or para_text.lower() in section_title.lower():
                logger.info(f"[{task_id}] Matched section '{section_title}' to template heading at {para_idx}")
                
                # Update heading text if different
                if para_text != section_title:
                    set_paragraph_text(para, section_title)
                
                # Find and fill body paragraphs after this heading
                # Look for next heading or end of document
                next_heading_idx = len(doc.paragraphs)
                for next_idx, (next_para_idx, _) in enumerate(template_headings[idx+1:], idx+1):
                    next_heading_idx = next_para_idx
                    break
                
                # Clear existing body content between this heading and next
                body_paras = []
                for p_idx in range(para_idx + 1, min(next_heading_idx, len(doc.paragraphs))):
                    body_paras.append(doc.paragraphs[p_idx])
                
                # Remove body paragraphs (we'll add new ones)
                # Note: We can't easily remove from docx, so we'll clear them
                for body_para in body_paras:
                    if body_para.style and body_para.style.name not in heading_styles:
                        clear_paragraph_content(body_para)
                
                # Insert new content after heading
                # Split content into paragraphs
                content_paras = section_content.split('\n\n')
                insert_idx = para_idx + 1
                
                for content_text in content_paras:
                    if content_text.strip():
                        if insert_idx < len(doc.paragraphs):
                            # Reuse existing paragraph
                            set_paragraph_text(doc.paragraphs[insert_idx], content_text.strip())
                            insert_idx += 1
                        else:
                            # Add new paragraph
                            new_para = doc.add_paragraph(content_text.strip())
                
                filled_indices.add(para_idx)
                matched = True
                break
        
        if not matched:
            # No match found - append as new section
            logger.info(f"[{task_id}] No match for '{section_title}', appending as new section")
            insert_section_content(doc, section_title, section_content)
    
    # Handle tables
    if tables:
        logger.info(f"[{task_id}] Processing {len(tables)} tables")
        
        # Get template tables for style copying
        template_tables = list(doc.tables)
        
        for table_data in tables:
            table_index = table_data.get('table_index', 0)
            headers = table_data.get('headers', [])
            rows = table_data.get('rows', [])
            
            if not rows:
                continue
            
            # Find matching template table or use last one
            template_table = None
            if table_index < len(template_tables):
                template_table = template_tables[table_index]
            elif template_tables:
                template_table = template_tables[-1]
            
            # Build new table with data
            logger.info(f"[{task_id}] Building table {table_index}: {len(rows)} rows x {len(headers)} cols")
            new_table = build_table_from_data(doc, table_index, headers, rows, template_table)
    
    # Save document
    doc.save(output_path)
    logger.success(f"[{task_id}] Document saved to: {output_path}")
    
    return output_path


def build_document_simple(
    template_path: str,
    generated_content: Dict[str, Any],
    output_path: str,
    task_id: str = "unknown"
) -> str:
    """
    Simplified document builder that appends content after template.
    
    This is a fallback when complex replacement fails.
    """
    logger.info(f"[{task_id}] Using simple document builder")
    
    # Create new document (PDF templates can't be loaded by python-docx)
    doc = Document()
    
    # Apply document formatting
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(14)
    font.color.rgb = RGBColor(0, 0, 0)  # Black
    
    paragraph_format = style.paragraph_format
    paragraph_format.line_spacing = 1.5
    paragraph_format.first_line_indent = Cm(1.2)
    
    # Get data
    document_title = generated_content.get('document_title', 'Document')
    sections = generated_content.get('sections', [])
    tables = generated_content.get('tables', [])
    
    # Add generated content marker
    doc.add_paragraph()
    doc.add_paragraph("— Згенерований зміст —", style='Intense Quote')
    doc.add_paragraph()
    
    # Add title if different from template
    if document_title:
        doc.add_paragraph(document_title, style='Title')
        doc.add_paragraph()
    
    # Add sections
    for section in sections:
        title = section.get('title', '')
        content = section.get('content', '')
        
        if title:
            doc.add_paragraph(title, style='Heading 1')
        
        if content:
            for para_text in content.split('\n\n'):
                if para_text.strip():
                    doc.add_paragraph(para_text.strip())
        
        doc.add_paragraph()
    
    # Add tables
    for table_data in tables:
        headers = table_data.get('headers', [])
        rows = table_data.get('rows', [])
        
        if rows:
            num_cols = len(headers) if headers else len(rows[0])
            table = doc.add_table(rows=1+len(rows), cols=num_cols)
            table.style = 'Table Grid'
            
            # Headers
            if headers:
                for idx, header in enumerate(headers):
                    table.rows[0].cells[idx].text = str(header)
            
            # Data
            for row_idx, row_data in enumerate(rows):
                for col_idx, value in enumerate(row_data):
                    table.rows[row_idx+1].cells[col_idx].text = str(value)
            
            doc.add_paragraph()
    
    # Save
    doc.save(output_path)
    logger.success(f"[{task_id}] Document (simple) saved to: {output_path}")
    
    return output_path


if __name__ == '__main__':
    print("DOCX Builder - use through Celery task")
