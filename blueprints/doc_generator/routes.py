import os
import sys
import json
import uuid
import time
import shutil
import traceback
from datetime import datetime
from flask import request, jsonify, render_template, current_app, send_file, session, redirect, url_for
from loguru import logger
from werkzeug.utils import secure_filename
import re
from celery import shared_task
from functools import wraps
from . import doc_generator_bp

# Middleware для логування всіх запитів
@doc_generator_bp.before_request
def log_request_info():
    """Log information about all requests to this blueprint."""
    if request.method == 'POST':
        logger.info(f"[DEBUG] POST request: {request.method} {request.path}")
        logger.info(f"[DEBUG] Content-Type: {request.content_type}")
        logger.info(f"[DEBUG] Request data preview: {request.get_data(as_text=True)[:200]}...")
    elif request.method == 'GET':
        logger.info(f"[DEBUG] GET request: {request.method} {request.path}")
from .parsers.template_parser import parse_template
from .parsers.source_parser import parse_sources_with_metadata
from .config import ANALYSIS_PROMPT, AVAILABLE_MODELS, MODEL_SETTINGS_FILE
try:
    from .old_architecture.cleanup import cleanup_old_tasks, cleanup_chroma_collections, get_task_stats, get_disk_usage
except ImportError:
    # cleanup module may not exist
    cleanup_old_tasks = None
    cleanup_chroma_collections = None
    get_task_stats = None
    get_disk_usage = None
from .task_manager import (
    ensure_directories, get_unique_task_folder, create_task_info, save_task_info,
    load_task_info, get_all_tasks, get_task_by_id, update_task_status,
    save_analysis_result, load_analysis_result, save_final_document, delete_task,
    handle_file_upload, get_available_files, get_unique_filename,
    TASKS_DATA_DIR, TEMPLATES_DIR, SOURCES_DIR
)

# --- Debug Log Configuration ---
DEBUG_LOG_FILE = os.path.join(os.path.dirname(__file__), 'debug.log')

def get_debug_log_path() -> str:
    """Get path to debug log file."""
    return DEBUG_LOG_FILE

def read_debug_log() -> str:
    """Read entire debug log content."""
    if not os.path.exists(DEBUG_LOG_FILE):
        return ''
    try:
        with open(DEBUG_LOG_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to read debug log: {e}")
        return ''

def append_debug_log(message: str) -> bool:
    """Append a line to debug log with timestamp. Keep only last 100 entries."""
    try:
        timestamp = datetime.now().strftime('%H:%M:%S')
        lines = []
        
        # Read existing content
        if os.path.exists(DEBUG_LOG_FILE):
            with open(DEBUG_LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
        
        # Add new line
        lines.append(f"[{timestamp}] {message}")
        
        # Keep only last 100 entries
        if len(lines) > 100:
            lines = lines[-100:]
        
        # Write back
        with open(DEBUG_LOG_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        
        return True
    except Exception as e:
        logger.error(f"Failed to append to debug log: {e}")
        return False

def clear_debug_log() -> bool:
    """Clear the debug log file."""
    try:
        with open(DEBUG_LOG_FILE, 'w', encoding='utf-8') as f:
            f.write('')
        return True
    except Exception as e:
        logger.error(f"Failed to clear debug log: {e}")
        return False

# --- Model Selection Helpers ---

def get_selected_model() -> str:
    """Get the currently selected model from settings file."""
    if os.path.exists(MODEL_SETTINGS_FILE):
        try:
            with open(MODEL_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                model = settings.get('selected_model')
                if model in AVAILABLE_MODELS:
                    return model
        except Exception as e:
            logger.error(f"Failed to load model settings: {e}")
    # Default to first available model
    return AVAILABLE_MODELS[0] if AVAILABLE_MODELS else 'gemini-2.5-pro'

def save_selected_model(model: str) -> bool:
    """Save the selected model to settings file."""
    if model not in AVAILABLE_MODELS:
        logger.error(f"Invalid model: {model}")
        return False
    try:
        with open(MODEL_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'selected_model': model}, f)
        return True
    except Exception as e:
        logger.error(f"Failed to save model settings: {e}")
        return False

# --- Authentication Decorator ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            if request.is_json or '/api/' in request.path:
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- File Validation ---

ALLOWED_EXTENSIONS = {'.docx', '.pdf', '.DOCX', '.PDF', '.txt', '.TXT', '.ppt', '.pptx', '.PPT', '.PPTX', '.doc', '.DOC', '.xls', '.xlsx', '.XLS', '.XLSX'}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

def allowed_file(filename):
    return any(filename.endswith(ext) for ext in ALLOWED_EXTENSIONS)


def sanitize_filename(filename):
    """
    Sanitize filename while preserving Unicode characters.
    - Replace spaces with underscores
    - Remove dangerous characters: / \ : * ? " < > |
    - Keep Cyrillic and other Unicode
    - Limit length to 200 chars
    """
    # Replace spaces with underscores
    filename = filename.replace(' ', '_')
    
    # Remove dangerous filesystem characters but keep Unicode letters
    # Allow: letters (any language), digits, underscore, hyphen, dot
    filename = re.sub(r'[^\w\-\.]', '', filename, flags=re.UNICODE)
    
    # Remove multiple consecutive dots
    filename = re.sub(r'\.{2,}', '.', filename)
    
    # Limit length
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200 - len(ext)] + ext
    
    # Ensure not empty
    if not filename or filename == '.':
        filename = 'file'
    
    return filename

# --- Routes ---

@doc_generator_bp.route('/')
@login_required
def index():
    """Main page with upload form and task list."""
    return render_template('doc_generator/index-doc-generator.html')


# --- Maintenance Routes ---

@doc_generator_bp.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    """Get system statistics."""
    data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
    stats = get_task_stats(data_dir)
    return jsonify(stats)


@doc_generator_bp.route('/api/cleanup', methods=['POST'])
@login_required
def run_cleanup():
    """Clean up old tasks."""
    data = request.get_json() or {}
    max_age_days = data.get('max_age_days', 7)
    dry_run = data.get('dry_run', False)
    
    data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
    
    logger.info(f"Running cleanup: max_age={max_age_days}d, dry_run={dry_run}")
    
    # Clean up tasks
    task_stats = cleanup_old_tasks(data_dir, max_age_days, dry_run)
    
    # Clean up Chroma collections
    chroma_deleted = cleanup_chroma_collections(data_dir, max_age_days, dry_run)
    
    return jsonify({
        'tasks': task_stats,
        'chroma_collections_deleted': chroma_deleted,
        'dry_run': dry_run
    })


# ============================================================================
# NEW ARCHITECTURE API - Task-based document generation
# ============================================================================

@doc_generator_bp.route('/api/tasks', methods=['GET'])
@login_required
def list_tasks():
    """Get all tasks from Tasks-data folder."""
    ensure_directories()
    tasks = get_all_tasks()
    return jsonify({'tasks': tasks})


@doc_generator_bp.route('/api/task', methods=['POST'])
@login_required
def create_new_task():
    """Create new task (initial setup)."""
    data = request.get_json()
    
    template_file = data.get('template_file')
    analysis_prompt = data.get('analysis_prompt', '')
    force_create = data.get('force', False)
    
    if not template_file:
        return jsonify({'error': 'Template file is required'}), 400
    
    # Check for existing task with same template
    existing_tasks = get_all_tasks()
    for task in existing_tasks:
        if task.get('template_file') == template_file and not force_create:
            logger.info(f"[API] Duplicate task detected for template: {template_file}")
            return jsonify({
                'error': 'Task with this template already exists',
                'existing_task_id': task.get('task_id'),
                'existing_task_name': task.get('folder_name'),
                'duplicate': True
            }), 409
    
    # Create unique task folder
    task_folder = get_unique_task_folder(template_file)
    task_id = str(uuid.uuid4())[:8]
    
    # Create task info
    task_info = create_task_info(
        task_id=task_id,
        template_file=template_file,
        analysis_prompt=analysis_prompt,
        status='idle'
    )
    
    save_task_info(task_folder, task_info)
    
    return jsonify({
        'task_id': task_id,
        'folder': os.path.basename(task_folder),
        'folder_path': task_folder,
        'status': 'created'
    })


@doc_generator_bp.route('/api/tasks/<task_id>', methods=['GET'])
@login_required
def get_task(task_id):
    """Get task details."""
    sys.stderr.write(f"[GET /api/tasks/{task_id}] Request received\n")
    task = get_task_by_id(task_id)
    sys.stderr.write(f"[GET /api/tasks/{task_id}] Task found: {task is not None}\n")
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    # Load analysis if exists
    folder_path = task.get('folder_path')
    if folder_path and task.get('analysis_result'):
        analysis_text = load_analysis_result(folder_path)
        task['analysis_content'] = analysis_text
    
    return jsonify(task)


@doc_generator_bp.route('/api/tasks/<task_id>', methods=['DELETE'])
@login_required
def delete_task_endpoint(task_id):
    """Delete task and its folder."""
    task = get_task_by_id(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    folder_path = task.get('folder_path')
    if folder_path and delete_task(folder_path):
        return jsonify({'message': 'Task deleted'})
    
    return jsonify({'error': 'Failed to delete task'}), 500


@doc_generator_bp.route('/api/tasks/<task_id>/analysis', methods=['POST'])
@login_required
def run_analysis(task_id):
    """Start analysis task (Celery)."""
    task = get_task_by_id(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    folder_path = task.get('folder_path')
    data = request.get_json()
    analysis_prompt = data.get('analysis_prompt', task.get('analysis_prompt', ''))
    
    # Update task with prompt if changed
    if analysis_prompt != task.get('analysis_prompt'):
        task['analysis_prompt'] = analysis_prompt
        save_task_info(folder_path, task)
    
    # Start Celery task
    result = analyze_document_task.delay(task_id, folder_path, analysis_prompt)
    
    return jsonify({
        'message': 'Analysis started',
        'celery_task_id': result.id,
        'task_id': task_id
    })


@doc_generator_bp.route('/api/tasks/<task_id>/save_analysis', methods=['POST'])
@login_required
def save_analysis(task_id):
    """Save edited analysis result."""
    try:
        logger.info(f"[DEBUG] Save analysis request for task: {task_id}")
        logger.info(f"[DEBUG] Request content type: {request.content_type}")
        logger.info(f"[DEBUG] Request data: {request.get_data(as_text=True)[:500]}...")
        
        task = get_task_by_id(task_id)
        if not task:
            logger.error(f"[DEBUG] Task not found: {task_id}")
            return jsonify({'error': 'Task not found'}), 404
        
        folder_path = task.get('folder_path')
        logger.info(f"[DEBUG] Task folder path: {folder_path}")
        
        try:
            data = request.get_json()
            logger.info(f"[DEBUG] JSON data parsed successfully")
            logger.info(f"[DEBUG] Data keys: {list(data.keys()) if data else 'None'}")
        except Exception as e:
            logger.error(f"[DEBUG] Failed to parse JSON: {e}")
            logger.error(f"[DEBUG] Raw request data: {request.get_data(as_text=True)}")
            return jsonify({'error': f'Invalid JSON: {str(e)}'}), 400
        
        analysis_text = data.get('analysis', '')
        logger.info(f"[DEBUG] Analysis text length: {len(analysis_text)} chars")
        logger.info(f"[DEBUG] Analysis text preview: {analysis_text[:100]}...")
        
        if not analysis_text.strip():
            logger.warning(f"[DEBUG] Analysis text is empty")
            return jsonify({'error': 'Analysis text cannot be empty'}), 400
        
        save_analysis_result(folder_path, analysis_text)
        logger.info(f"[DEBUG] Analysis saved successfully")
        
        result = {
            'message': 'Analysis saved',
            'analysis_text': analysis_text,
            'word_count': len(analysis_text.split()) if analysis_text else 0
        }
        logger.info(f"[DEBUG] Returning result: {result}")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"[DEBUG] Save analysis failed: {e}")
        logger.error(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return jsonify({'error': f'Save failed: {str(e)}'}), 500


@doc_generator_bp.route('/api/tasks/<task_id>/generate', methods=['POST'])
@login_required
def generate_document(task_id):
    """Generate final document (Celery)."""
    task = get_task_by_id(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    folder_path = task.get('folder_path')
    data = request.get_json()
    
    generation_prompt = data.get('generation_prompt', task.get('generation_prompt', ''))
    source_files = data.get('source_files', task.get('source_files', []))
    
    # Update task info
    task['generation_prompt'] = generation_prompt
    task['source_files'] = source_files
    save_task_info(folder_path, task)
    
    # Start Celery task
    result = generate_final_document_task.delay(
        task_id, folder_path, generation_prompt, source_files
    )
    
    return jsonify({
        'message': 'Document generation started',
        'celery_task_id': result.id,
        'task_id': task_id
    })


@doc_generator_bp.route('/api/tasks/<task_id>/download', methods=['GET'])
@login_required
def download_task_result(task_id):
    """Download final document from task."""
    task = get_task_by_id(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    # Check if file exists regardless of status (to handle cases where file was created but status not updated)
    final_doc = task.get('final_document')
    folder_path = task.get('folder_path')
    
    if not final_doc or not folder_path:
        return jsonify({'error': 'No document available'}), 404
    
    file_path = os.path.join(folder_path, final_doc)
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    return send_file(file_path, as_attachment=True, download_name=final_doc)


@doc_generator_bp.route('/api/files', methods=['GET'])
@login_required
def list_files():
    """List available template and source files."""
    file_type = request.args.get('type', 'all')
    files = get_available_files(file_type)
    return jsonify(files)


@doc_generator_bp.route('/api/files/upload', methods=['POST'])
@login_required
def upload_file():
    """Upload new file (template or source)."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    upload_type = request.form.get('type', 'source')  # 'template' or 'source'
    
    if upload_type not in ['template', 'source']:
        return jsonify({'error': 'Invalid upload type'}), 400
    
    result = handle_file_upload(file, upload_type)
    
    if result['status'] == 'exists':
        return jsonify({
            'status': 'exists',
            'filename': result['filename'],
            'message': 'File already exists'
        }), 409
    
    return jsonify({
        'status': 'success',
        'filename': result['filename'],
        'path': result['path']
    })


@doc_generator_bp.route('/api/files/upload/unique', methods=['POST'])
@login_required
def upload_file_unique():
    """Upload file with unique name (adds 'копія' if exists)."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    upload_type = request.form.get('type', 'source')
    
    target_dir = TEMPLATES_DIR if upload_type == 'template' else SOURCES_DIR
    original_filename = file.filename
    
    # Get unique filename
    unique_filename = get_unique_filename(target_dir, original_filename)
    target_path = os.path.join(target_dir, unique_filename)
    
    file.save(target_path)
    
    return jsonify({
        'status': 'success',
        'original_filename': original_filename,
        'filename': unique_filename,
        'path': target_path,
        'renamed': unique_filename != original_filename
    })


# ============================================================================
# DEBUG LOG API
# ============================================================================

@doc_generator_bp.route('/api/debug-log', methods=['GET'])
@login_required
def get_debug_log():
    """Get debug log content."""
    content = read_debug_log()
    return jsonify({'content': content})


@doc_generator_bp.route('/api/debug-log', methods=['POST'])
@login_required
def add_debug_log():
    """Append a line to debug log."""
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({'error': 'Message is required'}), 400
    
    message = data.get('message', '')
    if not message.strip():
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    success = append_debug_log(message)
    if success:
        return jsonify({'status': 'success'})
    else:
        return jsonify({'error': 'Failed to append to log'}), 500


@doc_generator_bp.route('/api/debug-log', methods=['DELETE'])
@login_required
def delete_debug_log():
    """Clear the debug log."""
    success = clear_debug_log()
    if success:
        return jsonify({'status': 'success', 'message': 'Debug log cleared'})
    else:
        return jsonify({'error': 'Failed to clear log'}), 500


# ============================================================================
# MODEL API
# ============================================================================

@doc_generator_bp.route('/api/models', methods=['GET'])
@login_required
def get_available_models():
    """Get list of available models."""
    return jsonify({
        'models': AVAILABLE_MODELS,
        'selected': get_selected_model()
    })


@doc_generator_bp.route('/api/models/selected', methods=['GET'])
@login_required
def get_current_model():
    """Get currently selected model."""
    return jsonify({'model': get_selected_model()})


@doc_generator_bp.route('/api/models/selected', methods=['POST'])
@login_required
def set_current_model():
    """Set the selected model."""
    data = request.get_json()
    if not data or 'model' not in data:
        return jsonify({'error': 'Model is required'}), 400
    
    model = data.get('model')
    if model not in AVAILABLE_MODELS:
        return jsonify({'error': f'Invalid model: {model}'}), 400
    
    success = save_selected_model(model)
    if success:
        return jsonify({'status': 'success', 'model': model})
    else:
        return jsonify({'error': 'Failed to save model selection'}), 500


# ============================================================================
# CELERY TASKS - New Architecture
# ============================================================================

@shared_task(bind=True, max_retries=3)
def analyze_document_task(self, task_id, folder_path, analysis_prompt):
    """Celery task for document analysis using LLM."""
    from loguru import logger
    from .config import GEMINI_API_KEY, REASONING_MODEL, TEMPERATURE
    from google import genai
    
    logger.info(f"[ANALYZE] Starting analysis for task {task_id}")
    logger.info(f"[ANALYZE] Folder path: {folder_path}")
    
    # Write to debug log for UI visibility
    append_debug_log(f"[CELERY] Аналіз запущено для завдання {task_id}")
    
    try:
        update_task_status(folder_path, 'analyzing')
        
        # Load task info
        logger.info(f"[ANALYZE] Loading task info from {folder_path}")
        task_info = load_task_info(folder_path)
        logger.info(f"[ANALYZE] Task info loaded: {task_info}")
        
        template_file = task_info.get('template_file')
        logger.info(f"[ANALYZE] Template file: {template_file}")
        
        template_path = os.path.join(TEMPLATES_DIR, template_file)
        logger.info(f"[ANALYZE] Full template path: {template_path}")
        
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Template not found: {template_file}")
        
        # Parse template for text extraction
        logger.info(f"[ANALYZE] Parsing template...")
        from .parsers.template_parser import parse_template
        template_data = parse_template(template_path)
        logger.info(f"[ANALYZE] Template data type: {type(template_data)}")
        logger.info(f"[ANALYZE] Template data: {str(template_data)[:200]}")
        
        # Check if template_data is a dict
        if not isinstance(template_data, dict):
            logger.error(f"[ANALYZE] ERROR: template_data is not dict, it's {type(template_data)}")
            raise TypeError(f"Expected dict from parse_template, got {type(template_data)}")
        
        logger.info(f"[ANALYZE] Template parsed, keys: {list(template_data.keys())}")
        
        # Get text content for LLM
        text_content = template_data.get('text', '')
        logger.info(f"[ANALYZE] Text content length: {len(text_content)}")
        
        if not text_content:
            # For PDF, extract text differently
            if template_path.lower().endswith('.pdf'):
                logger.info(f"[ANALYZE] Extracting text from PDF...")
                import fitz
                doc = fitz.open(template_path)
                text_content = ""
                for page in doc:
                    text_content += page.get_text()
                doc.close()
                logger.info(f"[ANALYZE] PDF text extracted, length: {len(text_content)}")
        
        # Call LLM for analysis
        logger.info(f"[ANALYZE] Initializing LLM client...")
        client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        
        if not client:
            raise ValueError("No API key configured")
        
        # Debug: check types before format
        logger.info(f"[ANALYZE] ANALYSIS_PROMPT type: {type(ANALYSIS_PROMPT)}")
        logger.info(f"[ANALYZE] text_content type: {type(text_content)}")
        logger.info(f"[ANALYZE] ANALYSIS_PROMPT value: {str(ANALYSIS_PROMPT)[:100]}...")
        
        prompt = ANALYSIS_PROMPT.format(text_content=text_content[:500000])
        logger.info(f"[ANALYZE] Prompt prepared, length: {len(prompt)}")

        logger.info(f"[ANALYZE] Calling LLM model: {REASONING_MODEL}...")
        response = client.models.generate_content(
            model=REASONING_MODEL,
            contents=prompt,
            config={"temperature": TEMPERATURE}
        )
        logger.info(f"[ANALYZE] LLM response received, type: {type(response)}")
        
        analysis_text = response.text
        logger.info(f"[ANALYZE] Analysis text length: {len(analysis_text)}")
        
        # Save AI response for debugging
        from .reasoning.generator import save_ai_response
        save_ai_response(task_id, "template_analysis", prompt, analysis_text, folder_path)
        
        # Save analysis
        logger.info(f"[ANALYZE] Saving analysis result...")
        append_debug_log(f"[CELERY] Збереження результату аналізу завдання {task_id}")
        save_analysis_result(folder_path, analysis_text)
        update_task_status(folder_path, 'idle')
        
        logger.info(f"[ANALYZE] Completed for task {task_id}")
        append_debug_log(f"[CELERY] Аналіз завершено для завдання {task_id}")
        return {'status': 'success', 'task_id': task_id}
        
    except Exception as e:
        import traceback
        logger.error(f"[ANALYZE] Failed for task {task_id}: {e}")
        logger.error(f"[ANALYZE] Traceback: {traceback.format_exc()}")
        
        # Extract clean error message from Gemini API errors
        error_msg = str(e)
        if '503 UNAVAILABLE' in error_msg or 'This model is currently experiencing high demand' in error_msg:
            # Extract the actual message from Gemini error format
            try:
                import re
                match = re.search(r"'message': '([^']+)'", error_msg)
                if match:
                    error_msg = match.group(1)
            except:
                pass  # Fallback to original error if regex fails
        
        append_debug_log(f"[CELERY] ПОМИЛКА аналізу завдання {task_id}: '{error_msg}'")
        
        # Check if retry is possible
        if self.request.retries < self.max_retries:
            next_retry = self.request.retries + 1
            append_debug_log(f"[CELERY] Буде запущена {next_retry} спроба аналізу через 60 секунд")
        
        # Only mark as failed if this is the final retry attempt
        if self.request.retries >= self.max_retries:
            update_task_status(folder_path, 'failed', error_msg)
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, max_retries=2)
def generate_final_document_task(self, task_id, folder_path, generation_prompt, source_files):
    """Celery task for final document generation using one-pass LLM approach."""
    from loguru import logger
    
    logger.info(f"[GENERATE] Starting generation for task {task_id}")
    append_debug_log(f"[CELERY] Початок генерації документа для завдання {task_id}")
    
    try:
        update_task_status(folder_path, 'generating')
        
        # Load task info and analysis
        task_info = load_task_info(folder_path)
        analysis_text = load_analysis_result(folder_path)
        
        logger.info(f"[GENERATE] Task info loaded: {task_info is not None}")
        logger.info(f"[GENERATE] Analysis text loaded: {analysis_text is not None}")
        
        if not analysis_text:
            raise ValueError("Analysis not found. Run analysis first.")
        
        # Validate analysis_text is not None and has content
        if analysis_text is None:
            raise ValueError("Analysis text is None. Run analysis first.")
        
        if not isinstance(analysis_text, str):
            raise ValueError(f"Analysis text is not string: {type(analysis_text)}")
        
        if not analysis_text.strip():
            raise ValueError("Analysis text is empty. Run analysis first.")
        
        # Load source files
        sources_data = {}
        for src_file in source_files:
            src_path = os.path.join(SOURCES_DIR, src_file)
            if os.path.exists(src_path):
                from .parsers.source_parser import parse_sources_with_metadata
                src_data = parse_sources_with_metadata([src_path])
                sources_data.update(src_data)
        
        if not sources_data:
            raise ValueError("No source files found or could not be parsed.")
        
        # Validate generation_prompt
        if generation_prompt is None:
            generation_prompt = ""
        elif not isinstance(generation_prompt, str):
            generation_prompt = str(generation_prompt)
        
        logger.info(f"[GENERATE] Generation prompt type: {type(generation_prompt)}")
        logger.info(f"[GENERATE] Generation prompt length: {len(generation_prompt)}")
        
        # Generate document using LLM
        from .reasoning import generate_document_full
        
        markdown_content = generate_document_full(
            analysis_text=analysis_text,
            sources_data=sources_data,
            task_id=task_id,
            user_prompt=generation_prompt,
            task_folder=folder_path
        )
        
        # Convert markdown to DOCX
        output_dir = os.path.join(folder_path, 'temp')
        os.makedirs(output_dir, exist_ok=True)
        temp_docx_path = os.path.join(output_dir, 'temp_result.docx')
        
        from .reasoning.markdown_to_docx import convert_markdown_to_docx
        convert_markdown_to_docx(markdown_content, temp_docx_path)
        
        # Save final document with proper name
        save_final_document(folder_path, temp_docx_path)
        
        # Cleanup temp
        shutil.rmtree(output_dir, ignore_errors=True)
        
        update_task_status(folder_path, 'completed')
        
        logger.info(f"[GENERATE] Completed for task {task_id}")
        append_debug_log(f"[CELERY] Генерація документа завершена для завдання {task_id}")
        return {'status': 'success', 'task_id': task_id}
        
    except Exception as e:
        logger.error(f"[GENERATE] Failed for task {task_id}: {e}")
        
        # Extract clean error message from Gemini API errors
        error_msg = str(e)
        if '503 UNAVAILABLE' in error_msg or 'This model is currently experiencing high demand' in error_msg:
            try:
                import re
                match = re.search(r"'message': '([^']+)'", error_msg)
                if match:
                    error_msg = match.group(1)
            except:
                pass
        
        append_debug_log(f"[CELERY] ПОМИЛКА генерації документа для завдання {task_id}: '{error_msg}'")
        
        # Check if retry is possible
        if self.request.retries < self.max_retries:
            next_retry = self.request.retries + 1
            append_debug_log(f"[CELERY] Буде запущена {next_retry} спроба генерації через 60 секунд")
        
        # Only mark as failed if this is the final retry attempt
        if self.request.retries >= self.max_retries:
            update_task_status(folder_path, 'failed', error_msg)
        raise self.retry(exc=e, countdown=60)
