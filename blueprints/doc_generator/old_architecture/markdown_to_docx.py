"""
Markdown to DOCX converter using Pandoc.

Based on converter.py from the main application.
"""
import subprocess
import os
import tempfile
from loguru import logger

# Path to pandoc executable
PANDOC = '/usr/bin/pandoc'
# Reference document for styling
REFERENCE_DOC = '/home/openclaw/mysite/reference.docx'


def preprocess_markdown(text: str) -> str:
    """
    Preprocess markdown text before conversion.
    
    Args:
        text: Raw markdown text
        
    Returns:
        Preprocessed markdown text
    """
    # Basic cleanup - can be extended
    text = text.strip()
    
    # Ensure proper line endings
    text = text.replace('\r\n', '\n')
    
    return text


def run_pandoc(src_path: str, dst_path: str) -> tuple[bool, list[str], str | None]:
    """
    Run pandoc to convert markdown to docx.
    
    Args:
        src_path: Path to source markdown file
        dst_path: Path to destination docx file
        
    Returns:
        Tuple of (success, warnings, error_message)
    """
    try:
        result = subprocess.run(
            [PANDOC, src_path,
             '-f', 'markdown+tex_math_single_backslash+tex_math_dollars',
             '-t', 'docx',
             f'--reference-doc={REFERENCE_DOC}',
             '-o', dst_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return False, [], f"Pandoc не знайдено за шляхом: {PANDOC}"
    except subprocess.TimeoutExpired:
        return False, [], "Pandoc перевищив ліміт часу (120 с)"
    except Exception as e:
        return False, [], f"Помилка запуску Pandoc: {e}"

    # Parse warnings
    warnings = []
    if result.stderr:
        for line in result.stderr.splitlines():
            line = line.strip()
            if line:
                warnings.append(line)

    if result.returncode != 0:
        return False, warnings, result.stderr or "Невідома помилка Pandoc"

    if not os.path.exists(dst_path):
        return False, warnings, "Pandoc завершився без помилок, але файл не створено"

    return True, warnings, None


def postprocess_docx(docx_path: str) -> None:
    """
    Postprocess the generated DOCX file.
    
    Applies formatting adjustments using python-docx.
    
    Args:
        docx_path: Path to the docx file
    """
    from docx import Document
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING

    logger.info(f"Postprocessing DOCX: {docx_path}")
    
    doc = Document(docx_path)

    # Remove bold and italic from all text (optional - depends on preference)
    for para in doc.paragraphs:
        for run in para.runs:
            # Keep formatting as-is for now, can be customized
            pass

    # Remove bookmarks
    body = doc.element.body
    for bm in body.findall('.//' + qn('w:bookmarkStart')):
        bm.getparent().remove(bm)
    for bm in body.findall('.//' + qn('w:bookmarkEnd')):
        bm.getparent().remove(bm)

    # Document margins
    section = doc.sections[0]
    section.left_margin = Cm(2)
    section.right_margin = Cm(1)

    # Helper functions for table formatting
    def set_table_width(table):
        tbl = table._tbl
        tblPr = tbl.find(qn('w:tblPr'))
        if tblPr is None:
            tblPr = OxmlElement('w:tblPr')
            tbl.insert(0, tblPr)

        for old in tblPr.findall(qn('w:tblW')):
            tblPr.remove(old)
        for old in tblPr.findall(qn('w:tblLayout')):
            tblPr.remove(old)

        tblW = OxmlElement('w:tblW')
        tblW.set(qn('w:w'), '5000')
        tblW.set(qn('w:type'), 'pct')
        tblPr.append(tblW)

        tblLayout = OxmlElement('w:tblLayout')
        tblLayout.set(qn('w:type'), 'autofit')
        tblPr.append(tblLayout)

        # Remove tblGrid (fixed column grid)
        for tblGrid in tbl.findall(qn('w:tblGrid')):
            tbl.remove(tblGrid)

        # Remove fixed width from each cell
        for tc in tbl.findall('.//' + qn('w:tc')):
            tcPr = tc.find(qn('w:tcPr'))
            if tcPr is not None:
                for tcW in tcPr.findall(qn('w:tcW')):
                    tcW.set(qn('w:w'), '0')
                    tcW.set(qn('w:type'), 'auto')

    def set_cell_border(cell):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        tcBorders = OxmlElement('w:tcBorders')
        for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            el = OxmlElement(f'w:{side}')
            el.set(qn('w:val'), 'single')
            el.set(qn('w:sz'), '4')
            el.set(qn('w:color'), '000000')
            tcBorders.append(el)
        tcPr.append(tcBorders)

    def set_cell_margin(cell):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        tcMar = OxmlElement('w:tcMar')
        for side in ('top', 'left', 'bottom', 'right'):
            el = OxmlElement(f'w:{side}')
            el.set(qn('w:w'), '11')
            el.set(qn('w:type'), 'dxa')
            tcMar.append(el)
        tcPr.append(tcMar)

    # Format tables
    for table in doc.tables:
        set_table_width(table)
        for row in table.rows:
            for cell in row.cells:
                set_cell_border(cell)
                set_cell_margin(cell)
                for para in cell.paragraphs:
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    pf = para.paragraph_format
                    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
                    pf.first_line_indent = Pt(0)
                    pf.space_before = Pt(0)
                    pf.space_after = Pt(0)
                    for run in para.runs:
                        run.font.name = 'Arial'
                        run.font.size = Pt(12)
                        run.font.color.rgb = RGBColor(0, 0, 0)

    doc.save(docx_path)
    logger.info(f"DOCX postprocessed successfully")


def convert_markdown_to_docx(markdown_text: str, output_path: str) -> tuple[bool, list[str], str | None]:
    """
    Convert markdown text to DOCX file.
    
    Args:
        markdown_text: Markdown content
        output_path: Path where DOCX file should be saved
        
    Returns:
        Tuple of (success, warnings, error_message)
    """
    logger.info(f"Converting markdown to DOCX: {output_path}")
    
    # Preprocess
    markdown_text = preprocess_markdown(markdown_text)
    
    # Use temporary directory for intermediate file
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, 'input.md')
        
        # Write markdown to temp file
        with open(src_path, 'w', encoding='utf-8') as f:
            f.write(markdown_text)
        
        # Run pandoc
        success, warnings, error = run_pandoc(src_path, output_path)
        
        if not success:
            logger.error(f"Pandoc conversion failed: {error}")
            return False, warnings, error
        
        # Postprocess
        try:
            postprocess_docx(output_path)
        except Exception as e:
            logger.warning(f"Postprocessing failed (but file created): {e}")
            warnings.append(f"Postprocessing warning: {e}")
        
        logger.success(f"DOCX created successfully: {output_path}")
        return True, warnings, None


if __name__ == '__main__':
    # Test conversion
    test_md = """# Тестовий документ

## Розділ 1

Це тестовий текст.

## Розділ 2

| Колонка 1 | Колонка 2 |
|-----------|-----------|
| Дані 1    | Дані 2    |
"""
    
    success, warnings, error = convert_markdown_to_docx(test_md, '/tmp/test_output.docx')
    if success:
        print("Success!")
        if warnings:
            print("Warnings:", warnings)
    else:
        print("Error:", error)
