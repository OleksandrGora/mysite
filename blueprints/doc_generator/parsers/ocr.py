"""
OCR Module - Handles scanned PDFs and images.

Uses:
- Tesseract OCR with Ukrainian language support
- PyMuPDF to convert PDF pages to images
- PIL/Pillow for image processing

If Tesseract quality is insufficient, can fallback to Google Vision API.
"""
import os
import io
from typing import List, Optional
from pathlib import Path
from loguru import logger

try:
    import pytesseract
    from PIL import Image
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False
    logger.warning("pytesseract or PIL not available, OCR will be limited")

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("PyMuPDF not available, PDF to image conversion will fail")


# Tesseract configuration
TESSERACT_LANG = 'ukr+eng'  # Ukrainian + English
TESSERACT_CONFIG = '--psm 6'  # Assume a single uniform block of text


def is_tesseract_available() -> bool:
    """Check if Tesseract is installed and available."""
    if not PYTESSERACT_AVAILABLE:
        return False
    
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def pdf_page_to_image(doc: 'fitz.Document', page_num: int, dpi: int = 300) -> Image.Image:
    """
    Convert PDF page to PIL Image.
    
    Args:
        doc: PyMuPDF document
        page_num: Page number (0-indexed)
        dpi: Resolution for rendering
        
    Returns:
        PIL Image
    """
    page = doc[page_num]
    
    # Render at specified DPI
    mat = fitz.Matrix(dpi/72, dpi/72)  # 72 is default PDF DPI
    pix = page.get_pixmap(matrix=mat)
    
    # Convert to PIL Image
    img_data = pix.tobytes("png")
    img = Image.open(io.BytesIO(img_data))
    
    return img


def ocr_image(image: Image.Image, lang: str = TESSERACT_LANG) -> str:
    """
    Run OCR on a PIL Image.
    
    Args:
        image: PIL Image
        lang: Tesseract language code
        
    Returns:
        Extracted text
    """
    if not PYTESSERACT_AVAILABLE:
        raise RuntimeError("pytesseract not installed")
    
    if not is_tesseract_available():
        raise RuntimeError("Tesseract OCR not found. Install: sudo apt install tesseract-ocr tesseract-ocr-ukr")
    
    # Convert to RGB if necessary
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # Run OCR
    text = pytesseract.image_to_string(
        image,
        lang=lang,
        config=TESSERACT_CONFIG
    )
    
    return text.strip()


def parse_pdf_with_ocr(
    pdf_path: str,
    start_page: int = 0,
    end_page: Optional[int] = None,
    dpi: int = 300
) -> str:
    """
    Extract text from scanned PDF using OCR.
    
    Args:
        pdf_path: Path to PDF file
        start_page: First page to process (0-indexed)
        end_page: Last page to process (None = all pages)
        dpi: Resolution for page rendering
        
    Returns:
        Extracted text from all pages
    """
    if not PYMUPDF_AVAILABLE:
        raise RuntimeError("PyMuPDF not installed")
    
    if not PYTESSERACT_AVAILABLE:
        raise RuntimeError("pytesseract not installed")
    
    if not is_tesseract_available():
        raise RuntimeError(
            "Tesseract OCR not available. "
            "Install: sudo apt install tesseract-ocr tesseract-ocr-ukr"
        )
    
    logger.info(f"OCR processing: {pdf_path}")
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    # Determine page range
    if end_page is None or end_page > total_pages:
        end_page = total_pages
    
    start_page = max(0, start_page)
    pages_to_process = range(start_page, end_page)
    
    logger.info(f"OCR: Processing {len(pages_to_process)} pages at {dpi} DPI")
    
    all_text = []
    
    for page_num in pages_to_process:
        try:
            logger.debug(f"OCR processing page {page_num + 1}/{total_pages}")
            
            # Convert to image
            img = pdf_page_to_image(doc, page_num, dpi)
            
            # Run OCR
            text = ocr_image(img)
            
            if text:
                all_text.append(text)
            
        except Exception as e:
            logger.error(f"OCR failed on page {page_num + 1}: {e}")
            all_text.append(f"[OCR error on page {page_num + 1}]")
    
    doc.close()
    
    # Combine with page separators
    full_text = '\n\n--- Page Break ---\n\n'.join(all_text)
    
    logger.info(f"OCR complete: {len(full_text)} characters from {len(all_text)} pages")
    
    return full_text


def ocr_image_file(image_path: str, lang: str = TESSERACT_LANG) -> str:
    """
    Run OCR on an image file (PNG, JPG, etc.).
    
    Args:
        image_path: Path to image file
        lang: Tesseract language code
        
    Returns:
        Extracted text
    """
    if not PYTESSERACT_AVAILABLE:
        raise RuntimeError("pytesseract not installed")
    
    if not is_tesseract_available():
        raise RuntimeError("Tesseract OCR not found")
    
    logger.info(f"OCR processing image: {image_path}")
    
    with Image.open(image_path) as img:
        text = ocr_image(img, lang)
    
    logger.info(f"OCR complete: {len(text)} characters")
    
    return text


# --- Google Vision API Fallback (for future use) ---

def parse_pdf_with_google_vision(pdf_path: str) -> str:
    """
    Alternative OCR using Google Vision API.
    Requires GOOGLE_APPLICATION_CREDENTIALS environment variable.
    
    This is a stub - implement when Tesseract quality is insufficient.
    """
    raise NotImplementedError(
        "Google Vision API not yet implemented. "
        "Use Tesseract OCR or implement this function."
    )


# --- Preprocessing for better OCR ---

def preprocess_image_for_ocr(image: Image.Image) -> Image.Image:
    """
    Preprocess image to improve OCR accuracy.
    
    Operations:
    - Convert to grayscale
    - Increase contrast
    - Denoise (simple)
    """
    # Convert to grayscale
    img = image.convert('L')
    
    # Increase contrast
    from PIL import ImageEnhance
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    
    return img


def quick_ocr_test() -> bool:
    """
    Quick test to verify OCR is working.
    Returns True if OCR is functional.
    """
    if not PYTESSERACT_AVAILABLE or not is_tesseract_available():
        return False
    
    try:
        # Create a simple test image
        from PIL import Image, ImageDraw, ImageFont
        
        # Create white image
        img = Image.new('RGB', (200, 50), color='white')
        draw = ImageDraw.Draw(img)
        
        # Draw text
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
        except:
            font = ImageFont.load_default()
        
        draw.text((10, 10), "Test 123", fill='black', font=font)
        
        # OCR
        text = ocr_image(img, lang='eng')
        
        return 'test' in text.lower() or '123' in text
        
    except Exception as e:
        logger.error(f"OCR test failed: {e}")
        return False


if __name__ == '__main__':
    import sys
    
    # Test mode
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        ext = Path(file_path).suffix.lower()
        
        try:
            if ext == '.pdf':
                text = parse_pdf_with_ocr(file_path)
                print(text)
            elif ext in ['.png', '.jpg', '.jpeg', '.tiff', '.bmp']:
                text = ocr_image_file(file_path)
                print(text)
            else:
                print(f"Unsupported file type: {ext}")
        except Exception as e:
            print(f"Error: {e}")
    else:
        # Self-test
        print("Running OCR self-test...")
        
        if not PYTESSERACT_AVAILABLE:
            print("ERROR: pytesseract not installed")
            print("Install: pip install pytesseract pillow")
        elif not is_tesseract_available():
            print("ERROR: Tesseract not found")
            print("Install: sudo apt install tesseract-ocr tesseract-ocr-ukr")
        else:
            version = pytesseract.get_tesseract_version()
            print(f"Tesseract version: {version}")
            print(f"Languages: {pytesseract.get_languages()}")
            
            if quick_ocr_test():
                print("OCR self-test: PASSED")
            else:
                print("OCR self-test: FAILED")
