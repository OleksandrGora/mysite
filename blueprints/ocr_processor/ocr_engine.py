"""
OCR Engine for processing printed and handwritten text
"""
import os
import tempfile
import shutil
from pathlib import Path
from loguru import logger
from PIL import Image
import pytesseract

# Set tesseract path explicitly for systemd/gunicorn environment
pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

import easyocr
import cv2
import numpy as np


class OCREngine:
    def __init__(self):
        self.easyocr_reader = None
        self._init_easyocr()
    
    def _init_easyocr(self):
        """Initialize EasyOCR reader for handwritten text"""
        try:
            self.easyocr_reader = easyocr.Reader(['uk', 'en'])
            logger.info("EasyOCR reader initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize EasyOCR: {e}")
            self.easyocr_reader = None
    
    def preprocess_image(self, image_path):
        """
        Preprocess image for better OCR results
        
        Args:
            image_path: path to input image
            
        Returns:
            processed image as numpy array
        """
        try:
            # Read image
            img = cv2.imread(image_path)
            if img is None:
                raise ValueError(f"Cannot read image: {image_path}")
            
            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Apply thresholding to get binary image
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            # Noise removal
            denoised = cv2.medianBlur(binary, 3)
            
            # Dilation for better text detection
            kernel = np.ones((2, 2), np.uint8)
            dilated = cv2.dilate(denoised, kernel, iterations=1)
            
            return dilated
            
        except Exception as e:
            logger.error(f"Image preprocessing failed: {e}")
            # Return original image if preprocessing fails
            return cv2.imread(image_path)
    
    def extract_printed_text(self, image_path, language='ukr'):
        """
        Extract printed text using Tesseract OCR

        Args:
            image_path: path to image file
            language: language code for OCR ('ukr', 'rus', 'eng')

        Returns:
            extracted text as string
        """
        try:
            logger.info(f"[OCR_ENGINE] Starting printed text extraction (lang={language})")

            # Preprocess image
            processed_img = self.preprocess_image(image_path)

            # Configure Tesseract for better results
            custom_config = r'--oem 3 --psm 6 -l ' + language

            # Extract text
            text = pytesseract.image_to_string(processed_img, config=custom_config)

            logger.info(f"[OCR_ENGINE] Printed text ({language}): {len(text.strip())} chars")
            return text.strip()

        except Exception as e:
            logger.error(f"[OCR_ENGINE] Printed text extraction failed: {e}")
            return ""
    
    def extract_handwritten_text(self, image_path):
        """
        Extract handwritten text using EasyOCR

        Args:
            image_path: path to image file

        Returns:
            extracted text as string
        """
        try:
            if self.easyocr_reader is None:
                logger.error("[OCR_ENGINE] EasyOCR reader not initialized")
                return ""

            logger.info(f"[OCR_ENGINE] Starting handwritten text extraction for {image_path}")

            # Get image info
            img = cv2.imread(image_path)
            if img is not None:
                height, width = img.shape[:2]
                logger.info(f"[OCR_ENGINE] Image size: {width}x{height}")

            # Preprocess image
            processed_img = self.preprocess_image(image_path)

            # Extract text using EasyOCR
            results = self.easyocr_reader.readtext(processed_img)
            logger.info(f"[OCR_ENGINE] EasyOCR found {len(results)} text blocks")

            # Combine detected text with lower confidence threshold
            text_parts = []
            confidences = []
            for (bbox, text, confidence) in results:
                logger.info(f"[OCR_ENGINE] Detected: '{text}' (confidence: {confidence:.3f})")
                confidences.append(confidence)
                if confidence > 0.3:  # Lowered threshold for handwritten text
                    text_parts.append(text)

            extracted_text = '\n'.join(text_parts)
            avg_conf = sum(confidences) / len(confidences) if confidences else 0
            logger.info(f"[OCR_ENGINE] Handwritten: {len(text_parts)} blocks, {len(extracted_text)} chars, avg_conf={avg_conf:.3f}")
            return extracted_text

        except Exception as e:
            logger.error(f"[OCR_ENGINE] Handwritten text extraction failed: {e}", exc_info=True)
            return ""

    def extract_handwritten_text_with_logs(self, image_path, logs_list, confidence_threshold=0.3):
        """
        Extract handwritten text with detailed logging

        Args:
            image_path: path to image file
            logs_list: list to append logs to
            confidence_threshold: minimum confidence threshold (0.0-1.0)

        Returns:
            extracted text as string
        """
        try:
            if self.easyocr_reader is None:
                logs_list.append("[OCR_ENGINE] ERROR: EasyOCR not initialized")
                return ""

            logs_list.append("[OCR_ENGINE] Starting handwritten extraction")
            logs_list.append(f"[OCR_ENGINE] Confidence threshold: {confidence_threshold}")

            # Get image info
            img = cv2.imread(image_path)
            if img is not None:
                height, width = img.shape[:2]
                logs_list.append(f"[OCR_ENGINE] Image size: {width}x{height}")

            # Preprocess image
            processed_img = self.preprocess_image(image_path)

            # Extract text using EasyOCR
            logs_list.append("[OCR_ENGINE] Running EasyOCR...")
            results = self.easyocr_reader.readtext(processed_img)
            logs_list.append(f"[OCR_ENGINE] EasyOCR found {len(results)} text blocks")

            # Show all detected blocks with confidence
            for i, (bbox, text, confidence) in enumerate(results):
                logs_list.append(f"[OCR_ENGINE] Block {i+1}: '{text}' (conf: {confidence:.3f})")

            # Combine detected text with confidence threshold
            text_parts = []
            confidences = []
            for (bbox, text, confidence) in results:
                confidences.append(confidence)
                if confidence > confidence_threshold:
                    text_parts.append(text)
                else:
                    logs_list.append(f"[OCR_ENGINE] SKIPPED (conf {confidence:.3f} < {confidence_threshold}): '{text}'")

            extracted_text = '\n'.join(text_parts)
            avg_conf = sum(confidences) / len(confidences) if confidences else 0
            logs_list.append(f"[OCR_ENGINE] Kept {len(text_parts)}/{len(results)} blocks, avg_conf={avg_conf:.3f}, threshold={confidence_threshold}")

            return extracted_text

        except Exception as e:
            logs_list.append(f"[OCR_ENGINE] ERROR: {str(e)}")
            logger.error(f"[OCR_ENGINE] Handwritten extraction failed: {e}", exc_info=True)
            return ""
    
    def extract_all_text(self, image_path, include_handwritten=True, confidence_threshold=0.3):
        """
        Extract both printed and handwritten text

        Args:
            image_path: path to image file
            include_handwritten: whether to include handwritten text recognition
            confidence_threshold: minimum confidence for handwritten text (0.0-1.0)

        Returns:
            dict with printed and handwritten text and processing logs
        """
        processing_logs = []
        try:
            processing_logs.append(f"[OCR_ENGINE] Starting extraction for {image_path}")
            processing_logs.append(f"[OCR_ENGINE] Handwritten enabled: {include_handwritten}, threshold: {confidence_threshold}")

            # Try multiple languages for printed text
            languages = ['ukr', 'rus', 'eng']
            printed_text = ""
            best_lang = ""

            for lang in languages:
                text = self.extract_printed_text(image_path, lang)
                processing_logs.append(f"[OCR_ENGINE] {lang}: {len(text)} chars")
                if text and len(text) > len(printed_text):
                    printed_text = text
                    best_lang = lang

            if best_lang:
                processing_logs.append(f"[OCR_ENGINE] Best language: {best_lang}")

            result = {
                'printed_text': printed_text,
                'handwritten_text': "",
                'logs': processing_logs
            }

            if include_handwritten:
                hw_logs = []
                result['handwritten_text'] = self.extract_handwritten_text_with_logs(image_path, hw_logs, confidence_threshold)
                processing_logs.extend(hw_logs)

            processing_logs.append(f"[OCR_ENGINE] FINAL: printed={len(printed_text)} chars, handwritten={len(result['handwritten_text'])} chars")
            logger.info(f"[OCR_ENGINE] Final result: printed={len(printed_text)} chars, handwritten={len(result['handwritten_text'])} chars")
            return result

        except Exception as e:
            processing_logs.append(f"[OCR_ENGINE] ERROR: {str(e)}")
            logger.error(f"[OCR_ENGINE] Text extraction failed: {e}", exc_info=True)
            return {'printed_text': "", 'handwritten_text': "", 'logs': processing_logs}
    
    def save_text_to_file(self, text, output_path, file_format='txt'):
        """
        Save extracted text to file
        
        Args:
            text: text content to save
            output_path: output file path
            file_format: file format ('txt', 'md')
            
        Returns:
            success status
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            if file_format.lower() == 'md':
                # Format as Markdown
                content = f"# Extracted Text\n\n{text}"
            else:
                content = text
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"Text saved to {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save text file: {e}")
            return False
