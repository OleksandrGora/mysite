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
from .config import (
    ANALYSIS_PROMPT, AVAILABLE_MODELS, REASONING_MODEL, save_selected_model,
    get_selected_model, get_openai_completion_kwargs, GEMINI_API_KEY,
    OPENAI_API_KEY, DEEPSEEK_API_KEY, TEMPERATURE, get_model_provider
)
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
    - Remove dangerous characters: / \\ : * ? " < > |
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
    use_rag = data.get('use_rag', False)
    chunk_params = data.get('chunk_params', {})
    
    # Update task info
    task['generation_prompt'] = generation_prompt
    task['source_files'] = source_files
    task['use_rag'] = use_rag
    save_task_info(folder_path, task)
    
    if use_rag:
        append_debug_log(f"[API] Запуск RAG-режиму для завдання {task_id}")
        append_debug_log(f"[API] Параметри чанкування: {chunk_params}")
    
    # Start Celery task
    result = generate_final_document_task.delay(
        task_id, folder_path, generation_prompt, source_files,
        use_rag=use_rag, chunk_params=chunk_params
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
    
    # For source files, trigger RAG processing in background
    celery_task_id = None
    if upload_type == 'source' and result.get('path'):
        try:
            celery_result = process_source_rag_task.delay(result['path'], result['filename'])
            celery_task_id = celery_result.id
            append_debug_log(f"[RAG] Запущено фонове чанкування для джерела: {result['filename']}")
        except Exception as e:
            logger.warning(f"Failed to start RAG processing for {result['filename']}: {e}")
            append_debug_log(f"[RAG] Не вдалося запустити чанкування для {result['filename']}: {e}")
    
    return jsonify({
        'status': 'success',
        'filename': result['filename'],
        'path': result['path'],
        'rag_task_id': celery_task_id
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
        'selected': get_current_model()
    })


@doc_generator_bp.route('/api/models/selected', methods=['GET'])
@login_required
def get_current_model() -> str:
    """Get currently selected model (from user's saved choice or env)."""
    return get_selected_model()


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
    
    # Save selected model to settings file
    save_selected_model(model)
    return jsonify({'status': 'success', 'model': model})


# ============================================================================
# CELERY TASKS - New Architecture
# ============================================================================

@shared_task(bind=True, max_retries=3)
def analyze_document_task(self, task_id, folder_path, analysis_prompt):
    """Celery task for document analysis using LLM."""
    from loguru import logger
    from .config import GEMINI_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, TEMPERATURE, get_model_provider
    from google import genai
    
    # Use the model selected by user, fallback to env variable
    selected_model = get_selected_model()
    
    logger.info(f"[ANALYZE] Starting analysis for task {task_id}")
    logger.info(f"[ANALYZE] Folder path: {folder_path}")
    
    # Write to debug log for UI visibility
    attempt_num = self.request.retries + 1
    attempt_suffix = f" (Спроба {attempt_num})" if attempt_num > 1 else ""
    append_debug_log(f"[CELERY] Аналіз запущено для завдання {task_id}{attempt_suffix}")
    append_debug_log(f"[CELERY] Модель для аналізу: {selected_model}")
    
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
        provider = get_model_provider(selected_model)
        logger.info(f"[ANALYZE] Using provider: {provider} for model: {selected_model}")
        
        # Debug: check types before format
        logger.info(f"[ANALYZE] ANALYSIS_PROMPT type: {type(ANALYSIS_PROMPT)}")
        logger.info(f"[ANALYZE] text_content type: {type(text_content)}")
        logger.info(f"[ANALYZE] ANALYSIS_PROMPT value: {str(ANALYSIS_PROMPT)[:100]}...")
        
        prompt = ANALYSIS_PROMPT.format(text_content=text_content[:500000])
        logger.info(f"[ANALYZE] Prompt prepared, length: {len(prompt)}")
        
        if provider == 'gemini':
            if not GEMINI_API_KEY:
                raise ValueError("Gemini API key not configured")
            
            client = genai.Client(api_key=GEMINI_API_KEY)
            logger.info(f"[ANALYZE] Calling Gemini model: {selected_model}...")
            response = client.models.generate_content(
                model=selected_model,
                contents=prompt,
                config={"temperature": TEMPERATURE}
            )
            analysis_text = response.text
            
        elif provider == 'openai':
            if not OPENAI_API_KEY:
                raise ValueError("OpenAI API key not configured")
            
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            logger.info(f"[ANALYZE] Calling OpenAI model: {selected_model}...")
            openai_kwargs = get_openai_completion_kwargs(
                model_name=selected_model,
                max_tokens=4000,
                temperature=TEMPERATURE
            )
            response = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": "Ти — експерт з аналізу структури документів. Відповідай українською мовою."},
                    {"role": "user", "content": prompt}
                ],
                **openai_kwargs
            )
            raw_content = response.choices[0].message.content
            logger.info(f"[ANALYZE] OpenAI response content type: {type(raw_content)}, finish_reason: {response.choices[0].finish_reason}")
            if raw_content is None:
                logger.warning(f"[ANALYZE] OpenAI returned None content. Full response: {response}")
                raise ValueError(
                    f"Модель {selected_model} повернула порожню відповідь під час аналізу. "
                    f"Finish reason: {response.choices[0].finish_reason}. "
                    f"Можливо, модель не підтримує системні промпти."
                )
            analysis_text = raw_content
        
        elif provider == 'deepseek':
            if not DEEPSEEK_API_KEY:
                raise ValueError("DeepSeek API key not configured")
            
            from openai import OpenAI
            client = OpenAI(
                api_key=DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com"
            )
            logger.info(f"[ANALYZE] Calling DeepSeek model: {selected_model}...")
            deepseek_kwargs = get_openai_completion_kwargs(
                model_name=selected_model,
                max_tokens=4000,
                temperature=TEMPERATURE
            )
            response = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": "Ти — експерт з аналізу структури документів. Відповідай українською мовою."},
                    {"role": "user", "content": prompt}
                ],
                **deepseek_kwargs
            )
            raw_content = response.choices[0].message.content
            logger.info(f"[ANALYZE] DeepSeek response content type: {type(raw_content)}, finish_reason: {response.choices[0].finish_reason}")
            if raw_content is None:
                logger.warning(f"[ANALYZE] DeepSeek returned None content. Full response: {response}")
                raise ValueError(
                    f"Модель {selected_model} повернула порожню відповідь під час аналізу. "
                    f"Finish reason: {response.choices[0].finish_reason}."
                )
            analysis_text = raw_content
            
        else:
            raise ValueError(f"Unsupported model provider: {provider}")
        
        logger.info(f"[ANALYZE] LLM response received, type: {type(response)}")
        logger.info(f"[ANALYZE] Analysis text length: {len(analysis_text)}")
        logger.info(f"[ANALYZE] Analysis text length: {len(analysis_text)}")
        
        # Save AI response for debugging
        from .reasoning.generator import save_ai_response
        save_ai_response(task_id, "analysis", prompt, analysis_text, folder_path, selected_model)
        
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
        
        error_msg = str(e)
        if '503 UNAVAILABLE' in error_msg or 'This model is currently experiencing high demand' in error_msg:
            try:
                import re
                match = re.search(r"'message': '([^']+)'", error_msg)
                if match:
                    error_msg = match.group(1)
            except:
                pass
        
        append_debug_log(f"[CELERY] ПОМИЛКА аналізу завдання {task_id}: '{error_msg}'")
        
        if self.request.retries < self.max_retries:
            next_attempt = self.request.retries + 2
            append_debug_log(f"[CELERY] Буде запущена {next_attempt} спроба аналізу через 30 секунд")
        
        if self.request.retries >= self.max_retries:
            update_task_status(folder_path, 'failed', error_msg)
        raise self.retry(exc=e, countdown=30)


# ============================================================================
# CELERY TASK - Generate final document
# ============================================================================

@shared_task(bind=True, max_retries=2)
def generate_final_document_task(self, task_id, folder_path, generation_prompt, source_files,
                                 use_rag=False, chunk_params=None):
    """Celery task for final document generation."""
    from loguru import logger
    ...


# ============================================================================
# CELERY TASK - Auto process source file for RAG
# ============================================================================

RAG_STATUS_FILE = os.path.join(os.path.dirname(__file__), 'Tasks-data', 'Sources', '.rag_status.json')


def _get_rag_status() -> dict:
    """Get RAG processing status for all source files."""
    if os.path.exists(RAG_STATUS_FILE):
        try:
            with open(RAG_STATUS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def _save_rag_status(status: dict):
    """Save RAG processing status."""
    os.makedirs(os.path.dirname(RAG_STATUS_FILE), exist_ok=True)
    with open(RAG_STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


@doc_generator_bp.route('/api/sources/rag-status', methods=['GET'])
@login_required
def get_sources_rag_status():
    """Get RAG processing status for all source files."""
    return jsonify(_get_rag_status())


@shared_task(bind=True, max_retries=1)
def process_source_rag_task(self, file_path: str, filename: str):
    """
    Celery task: chunk + embed a source file and store in ChromaDB.
    
    Runs automatically after source file upload.
    Results are cached: if ChromaDB collection already exists, skip.
    """
    from loguru import logger
    import time
    
    logger.info(f"[RAG-SOURCE] Processing source file: {filename}")
    append_debug_log(f"[RAG-SOURCE] Початок обробки джерела: {filename}")
    
    # Update status
    status = _get_rag_status()
    status[filename] = {
        'status': 'processing',
        'started_at': datetime.utcnow().isoformat(),
        'message': 'Чанкування та побудова векторної бази...',
        'file_path': file_path
    }
    _save_rag_status(status)
    
    try:
        # Extract text from source file
        from .parsers.source_parser import parse_sources_with_metadata
        sources_data = parse_sources_with_metadata([file_path])
        
        if not sources_data or not any(d.get('text', '').strip() for d in sources_data.values()):
            raise ValueError(f"Не вдалося витягти текст з файлу {filename}")
        
        text_data = sources_data.get(filename, {})
        char_count = text_data.get('char_count', 0)
        logger.info(f"[RAG-SOURCE] Extracted {char_count} chars from {filename}")
        
        # Chunk
        from .rag.chunker import chunk_documents
        chunks = chunk_documents(
            sources_data=sources_data,
            min_chunk_size=100,
            max_chunk_size=500
        )
        logger.info(f"[RAG-SOURCE] Created {len(chunks)} chunks from {filename}")
        append_debug_log(f"[RAG-SOURCE] {filename}: {len(chunks)} чанків, {char_count} символів")
        
        # Build vector store (with cache check)
        from .rag.embedder import Embedder, compute_sources_hash
        embedder = Embedder()
        sources_hash = compute_sources_hash([file_path])
        
        cache_hit = embedder.collection_exists(sources_hash)
        if cache_hit:
            collection_name = embedder.get_collection_name(sources_hash)
            logger.info(f"[RAG-SOURCE] Cache HIT for {filename}: collection '{collection_name}'")
            append_debug_log(f"[RAG-SOURCE] {filename}: векторна база вже існує (кеш)")
        else:
            t_start = time.time()
            collection_name = embedder.build(
                chunks=chunks,
                sources_hash=sources_hash,
                source_files=[filename]
            )
            t_elapsed = time.time() - t_start
            logger.info(f"[RAG-SOURCE] Built ChromaDB for {filename}: '{collection_name}' in {t_elapsed:.1f}s")
            append_debug_log(f"[RAG-SOURCE] {filename}: векторну базу побудовано за {t_elapsed:.0f}с")
        
        # Update status: success
        status = _get_rag_status()
        status[filename] = {
            'status': 'ready',
            'completed_at': datetime.utcnow().isoformat(),
            'message': f'Готово: {len(chunks)} чанків' if not cache_hit else 'Готово: з кешу',
            'chunks': len(chunks),
            'chars': char_count,
            'collection': collection_name,
            'cached': cache_hit,
            'file_path': file_path
        }
        _save_rag_status(status)
        
        return {'status': 'success', 'filename': filename, 'chunks': len(chunks)}
        
    except Exception as e:
        logger.error(f"[RAG-SOURCE] Failed to process {filename}: {e}")
        import traceback
        traceback.print_exc()
        
        # Update status: error
        status = _get_rag_status()
        status[filename] = {
            'status': 'error',
            'error': str(e),
            'completed_at': datetime.utcnow().isoformat(),
            'message': f'Помилка: {str(e)[:100]}',
            'file_path': file_path
        }
        _save_rag_status(status)
        append_debug_log(f"[RAG-SOURCE] ПОМИЛКА обробки {filename}: {str(e)[:100]}")
        
        # Don't retry for source processing (not critical)
        return {'status': 'error', 'filename': filename, 'error': str(e)}


