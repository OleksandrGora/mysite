"""
Source Parser - Extracts text from DOCX and PDF source files.

Handles:
- DOCX files: Extracts text with paragraph structure
- PDF files: Extracts text with layout preservation, detects scanned PDFs
- OCR for scanned documents (delegates to ocr.py)

Returns clean text with structure markers.
"""
import os
import re
from pathlib import Path
from typing import List, Dict, Optional, Union
from docx import Document
from loguru import logger

# Import OCR module for scanned PDFs
from . import ocr


def clean_text(text: str) -> str:
    """
    Clean extracted text from artifacts.
    - Remove excessive whitespace
    - Normalize line breaks
    - Remove page numbers and headers/footers (heuristic)
    """
    if not text:
        return ""
    
    # Normalize line breaks
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Remove excessive whitespace
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Remove standalone numbers (likely page numbers)
    # Match numbers on their own line
    text = re.sub(r'\n\s*\d+\s*\n', '\n', text)
    
    # Remove common header/footer patterns (heuristic)
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        line = line.strip()
        # Skip short lines that look like page numbers
        if re.match(r'^\d+$', line) and len(line) < 5:
            continue
        # Skip date-only lines (common in headers)
        if re.match(r'^\d{1,2}[./]\d{1,2}[./]\d{2,4}$', line):
            continue
        cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines).strip()


def parse_docx(file_path: str) -> str:
    """
    Extract text from DOCX file preserving paragraph structure.
    
    Args:
        file_path: Path to .docx file
        
    Returns:
        Extracted and cleaned text
    """
    logger.info(f"Parsing DOCX: {file_path}")
    
    doc = Document(file_path)
    
    paragraphs = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)
    
    # Extract tables as structured text
    for table in doc.tables:
        table_text = []
        for row in table.rows:
            row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if row_text:
                table_text.append(' | '.join(row_text))
        if table_text:
            paragraphs.append('\n'.join(table_text))
    
    full_text = '\n\n'.join(paragraphs)
    cleaned = clean_text(full_text)
    
    logger.info(f"DOCX parsed: {len(cleaned)} chars, {len(paragraphs)} paragraphs")
    
    return cleaned


def is_scanned_pdf(file_path: str) -> bool:
    """
    Check if PDF is scanned (no text layer).
    
    Heuristic: If page has very few characters relative to size,
    it's likely a scanned image.
    
    Args:
        file_path: Path to PDF file
        
    Returns:
        True if PDF appears to be scanned
    """
    try:
        import fitz  # PyMuPDF
        
        doc = fitz.open(file_path)
        
        # Check first few pages
        pages_to_check = min(3, len(doc))
        total_chars = 0
        
        for page_num in range(pages_to_check):
            page = doc[page_num]
            text = page.get_text()
            total_chars += len(text.strip())
        
        doc.close()
        
        # If very few characters per page, likely scanned
        avg_chars_per_page = total_chars / pages_to_check if pages_to_check > 0 else 0
        
        logger.debug(f"PDF character check: {avg_chars_per_page:.0f} chars/page average")
        
        # Threshold: less than 100 chars per page suggests scanned document
        return avg_chars_per_page < 100
        
    except Exception as e:
        logger.error(f"Error checking if PDF is scanned: {e}")
        # Assume not scanned on error (safer to try text extraction)
        return False


def parse_pdf_text(file_path: str) -> str:
    """
    Extract text from PDF with text layer.
    
    Args:
        file_path: Path to PDF file
        
    Returns:
        Extracted and cleaned text
    """
    import fitz  # PyMuPDF
    
    logger.info(f"Parsing PDF text: {file_path}")
    
    doc = fitz.open(file_path)
    
    pages_text = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        if text.strip():
            pages_text.append(text)
    
    doc.close()
    
    full_text = '\n\n'.join(pages_text)
    cleaned = clean_text(full_text)
    
    logger.info(f"PDF parsed: {len(cleaned)} chars from {len(pages_text)} pages")
    
    return cleaned


def parse_pdf(file_path: str, force_ocr: bool = False) -> str:
    """
    Extract text from PDF, using OCR if necessary.
    
    Args:
        file_path: Path to PDF file
        force_ocr: If True, always use OCR even if text layer exists
        
    Returns:
        Extracted and cleaned text
    """
    logger.info(f"Parsing PDF: {file_path}")
    
    # Check if scanned
    is_scanned = force_ocr or is_scanned_pdf(file_path)
    
    if is_scanned:
        logger.info("PDF detected as scanned, using OCR")
        return ocr.parse_pdf_with_ocr(file_path)
    
    # Try normal text extraction
    text = parse_pdf_text(file_path)
    
    # If we got very little text, try OCR as fallback
    if len(text.strip()) < 200:
        logger.warning("Very little text extracted, trying OCR as fallback")
        try:
            ocr_text = ocr.parse_pdf_with_ocr(file_path)
            if len(ocr_text) > len(text):
                logger.info(f"OCR produced more text ({len(ocr_text)} vs {len(text)} chars)")
                return ocr_text
        except Exception as e:
            logger.error(f"OCR fallback failed: {e}")
    
    return text


def parse_source(file_path: str) -> str:
    """
    Parse a source file (DOCX or PDF) and return clean text.
    
    Args:
        file_path: Path to source file
        
    Returns:
        Extracted and cleaned text
        
    Raises:
        ValueError: If file type is not supported
    """
    ext = Path(file_path).suffix.lower()
    
    if ext == '.docx':
        return parse_docx(file_path)
    elif ext == '.pdf':
        return parse_pdf(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def parse_sources(file_paths: List[str]) -> Dict[str, str]:
    """
    Parse multiple source files.
    
    Args:
        file_paths: List of paths to source files
        
    Returns:
        Dictionary mapping file names to extracted text
    """
    results = {}
    
    for path in file_paths:
        try:
            text = parse_source(path)
            file_name = Path(path).name
            results[file_name] = text
            logger.info(f"Parsed {file_name}: {len(text)} characters")
        except Exception as e:
            logger.error(f"Failed to parse {path}: {e}")
            results[Path(path).name] = f"[Error parsing file: {e}]"
    
    return results


def parse_sources_with_metadata(file_paths: List[str]) -> Dict[str, Dict]:
    """
    Parse sources and return with metadata.
    
    Returns dict with structure:
    {
        'filename.docx': {
            'text': 'extracted text...',
            'char_count': 1234,
            'type': 'docx',
            'status': 'success' | 'error',
            'error': None or error message
        }
    }
    """
    results = {}
    
    for path in file_paths:
        file_name = Path(path).name
        ext = Path(path).suffix.lower()
        
        result = {
            'text': '',
            'char_count': 0,
            'type': ext.replace('.', ''),
            'status': 'success',
            'error': None
        }
        
        try:
            text = parse_source(path)
            result['text'] = text
            result['char_count'] = len(text)
            logger.info(f"Parsed {file_name}: {len(text)} characters")
        except Exception as e:
            logger.error(f"Failed to parse {path}: {e}")
            result['status'] = 'error'
            result['error'] = str(e)
        
        results[file_name] = result
    
    return results


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        files = sys.argv[1:]
        results = parse_sources_with_metadata(files)
        
        for name, info in results.items():
            print(f"\n{'='*60}")
            print(f"File: {name}")
            print(f"Status: {info['status']}")
            print(f"Type: {info['type']}")
            print(f"Characters: {info['char_count']}")
            if info['error']:
                print(f"Error: {info['error']}")
            else:
                # Preview first 500 chars
                preview = info['text'][:500].replace('\n', ' ')
                print(f"Preview: {preview}...")
    else:
        print("Usage: python source_parser.py <file1> [file2] ...")
