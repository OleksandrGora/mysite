import os
import uuid
import tempfile
import shutil
from datetime import datetime
from flask import request, jsonify, render_template, current_app, send_file, session, redirect, url_for
from loguru import logger
from functools import wraps
from . import ocr_processor_bp
from .ocr_engine import OCREngine


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            if request.is_json or '/api/' in request.path:
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@ocr_processor_bp.route('/')
@login_required
def index():
    """Main OCR processor page"""
    logger.info(f"[OCR_INDEX] Rendering main OCR page for {session.get('email')}")
    return render_template('ocr_processor/index-ocr.html')




@ocr_processor_bp.route('/api/upload', methods=['POST'])
@login_required
def upload_image():
    """
    Upload image and extract text

    Expected form data:
    - file: image file
    - include_handwritten: boolean (optional, default true)
    - output_format: 'txt' or 'md' (optional, default 'txt')
    """
    logger.info(f"[OCR_API_UPLOAD] Request from {session.get('email')}")
    try:
        # Check if file is present
        if 'file' not in request.files:
            logger.warning("[OCR_API_UPLOAD] No file in request.files")
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']

        if file.filename == '':
            logger.warning("[OCR_API_UPLOAD] Empty filename")
            return jsonify({'error': 'No file selected'}), 400

        # Check file extension
        allowed_extensions = {'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'gif', 'pdf'}
        ext = file.filename.lower().split('.')[-1]
        if ext not in allowed_extensions:
            logger.warning(f"[OCR_API_UPLOAD] Invalid extension: {ext}")
            return jsonify({'error': 'File must be an image or PDF (png, jpg, jpeg, tiff, bmp, gif, pdf)'}), 400
        
        # Get options
        include_handwritten = request.form.get('include_handwritten', 'true').lower() in ['true', '1', 'yes']
        output_format = request.form.get('output_format', 'txt').lower()
        if output_format not in ['txt', 'md']:
            output_format = 'txt'
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp(prefix='ocr_upload_')
        image_path = os.path.join(temp_dir, file.filename)
        file.save(image_path)
        
        # Initialize OCR engine
        ocr_engine = OCREngine()
        
        # Extract text
        logger.info(f"[OCR_API_UPLOAD] Starting OCR for {file.filename}, handwritten={include_handwritten}")
        result = ocr_engine.extract_all_text(image_path, include_handwritten)
        
        # Combine text
        combined_text = result['printed_text']
        if include_handwritten and result['handwritten_text']:
            if combined_text:
                combined_text += '\n\n--- Рукописний текст ---\n' + result['handwritten_text']
            else:
                combined_text = result['handwritten_text']
        
        # Generate output filename
        base_name = os.path.splitext(file.filename)[0]
        output_filename = f"{base_name}_extracted.{output_format}"
        output_path = os.path.join(temp_dir, output_filename)
        
        # Save text to file
        success = ocr_engine.save_text_to_file(combined_text, output_path, output_format)
        
        if not success:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({'error': 'Failed to save extracted text'}), 500
        
        # Move file to permanent location
        permanent_dir = os.path.join(current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data'), 'ocr_output')
        os.makedirs(permanent_dir, exist_ok=True)
        
        permanent_path = os.path.join(permanent_dir, output_filename)
        shutil.move(output_path, permanent_path)
        
        # Cleanup temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        logger.info(f"[OCR_API_UPLOAD] Completed for {file.filename}: total={len(combined_text)} chars")
        
        return jsonify({
            'success': True,
            'filename': output_filename,
            'original_filename': file.filename,
            'printed_text_length': len(result['printed_text']),
            'handwritten_text_length': len(result['handwritten_text']),
            'total_text_length': len(combined_text),
            'output_path': permanent_path,
            'page_count': result.get('page_count', 1),
            'printed_text': result['printed_text'][:500] + '...' if len(result['printed_text']) > 500 else result['printed_text'],
            'handwritten_text': result['handwritten_text'][:500] + '...' if len(result['handwritten_text']) > 500 else result['handwritten_text']
        })
        
    except Exception as e:
        logger.error(f"[OCR_API_UPLOAD] Error: {e}", exc_info=True)
        return jsonify({'error': f'OCR processing failed: {str(e)}'}), 500


@ocr_processor_bp.route('/api/extract-only', methods=['POST'])
@login_required
def extract_text_only():
    """
    Extract text without saving to file (for preview)
    
    Expected form data:
    - file: image file
    - include_handwritten: boolean (optional, default true)
    """
    try:
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Check file extension
        allowed_extensions = {'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'gif', 'pdf'}
        if not file.filename.lower().split('.')[-1] in allowed_extensions:
            return jsonify({'error': 'File must be an image or PDF'}), 400
        
        # Get options
        include_handwritten = request.form.get('include_handwritten', 'true').lower() in ['true', '1', 'yes']
        
        # Create temporary file
        temp_dir = tempfile.mkdtemp(prefix='ocr_preview_')
        image_path = os.path.join(temp_dir, file.filename)
        file.save(image_path)
        
        # Initialize OCR engine
        ocr_engine = OCREngine()
        
        # Extract text
        logger.info(f"Starting OCR preview for {file.filename}")
        result = ocr_engine.extract_all_text(image_path, include_handwritten)
        
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        return jsonify({
            'success': True,
            'printed_text': result['printed_text'],
            'handwritten_text': result['handwritten_text'],
            'printed_text_length': len(result['printed_text']),
            'handwritten_text_length': len(result['handwritten_text']),
            'page_count': result.get('page_count', 1)
        })
        
    except Exception as e:
        logger.error(f"OCR preview error: {e}", exc_info=True)
        return jsonify({'error': f'OCR processing failed: {str(e)}'}), 500


@ocr_processor_bp.route('/api/download/<filename>')
@login_required
def download_file(filename):
    """Download extracted text file"""
    try:
        data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
        file_path = os.path.join(data_dir, 'ocr_output', filename)
        
        if not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(file_path, as_attachment=True)
        
    except Exception as e:
        logger.error(f"File download error: {e}")
        return jsonify({'error': f'Download failed: {str(e)}'}), 500


@ocr_processor_bp.route('/api/list-files')
@login_required
def list_files():
    """List all OCR output files"""
    try:
        data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
        output_dir = os.path.join(data_dir, 'ocr_output')
        
        if not os.path.exists(output_dir):
            return jsonify({'files': []})
        
        files = []
        for filename in os.listdir(output_dir):
            file_path = os.path.join(output_dir, filename)
            if os.path.isfile(file_path):
                stat = os.stat(file_path)
                files.append({
                    'filename': filename,
                    'size': stat.st_size,
                    'created': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
        
        # Sort by creation date (newest first)
        files.sort(key=lambda x: x['created'], reverse=True)
        
        return jsonify({'files': files})
        
    except Exception as e:
        logger.error(f"File listing error: {e}")
        return jsonify({'error': f'Failed to list files: {str(e)}'}), 500


@ocr_processor_bp.route('/api/delete/<filename>', methods=['DELETE'])
@login_required
def delete_file(filename):
    """Delete OCR output file"""
    try:
        data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
        file_path = os.path.join(data_dir, 'ocr_output', filename)

        if not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404

        os.remove(file_path)
        logger.info(f"Deleted OCR file: {filename}")

        return jsonify({'success': True, 'message': 'File deleted successfully'})

    except Exception as e:
        logger.error(f"File deletion error: {e}")
        return jsonify({'error': f'Deletion failed: {str(e)}'}), 500


@ocr_processor_bp.route('/api/logs/<filename>')
@login_required
def get_ocr_logs(filename):
    """Get OCR processing logs for a specific file"""
    try:
        data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
        log_path = os.path.join(data_dir, 'ocr_logs', f"{filename}.log")

        if not os.path.exists(log_path):
            return jsonify({'logs': [], 'message': 'No logs found for this file'})

        with open(log_path, 'r', encoding='utf-8') as f:
            logs = f.read().split('\n')

        return jsonify({'logs': logs})

    except Exception as e:
        logger.error(f"Log retrieval error: {e}")
        return jsonify({'error': f'Failed to get logs: {str(e)}'}), 500


@ocr_processor_bp.route('/api/status')
@login_required
def get_ocr_status():
    """Check OCR engine status, especially EasyOCR"""
    try:
        from .ocr_engine import OCREngine
        engine = OCREngine()

        status = {
            'easyocr_ready': engine.easyocr_reader is not None,
            'languages': ['uk', 'en'],  # Ukrainian and English only (no Russian)
            'timestamp': datetime.now().isoformat()
        }

        if engine.easyocr_reader is None:
            status['error'] = 'EasyOCR reader failed to initialize. Check server logs for details.'
            logger.warning("[OCR_STATUS] EasyOCR not ready - handwritten text will not work")
        else:
            logger.info("[OCR_STATUS] EasyOCR is ready")

        return jsonify(status)

    except Exception as e:
        logger.error(f"[OCR_STATUS] Error checking status: {e}")
        return jsonify({
            'easyocr_ready': False,
            'error': str(e),
            'languages': ['uk', 'en']
        }), 500
