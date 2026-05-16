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
    ANALYSIS_PROMPT, RAG_PROMPT_TEMPLATE, AVAILABLE_MODELS, REASONING_MODEL,
    save_selected_model, get_selected_model, get_openai_completion_kwargs,
    GEMINI_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, TEMPERATURE,
    get_model_provider
)
from .task_manager import (
    ensure_directories, get_unique_task_folder, create_task_info, save_task_info,
    load_task_info, get_all_tasks, get_task_by_id, update_task_status,
    update_task_progress, save_analysis_result, load_analysis_result,
    save_final_document, delete_task,
    handle_file_upload, get_available_files, get_unique_filename, register_celery_task, sanitize_filename,
    TASKS_DATA_DIR, SOURCES_DIR
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
            # File uploads use multipart/form-data, not JSON.
            # Check path for API routes to always return JSON.
            if request.is_json or '/api/' in request.path or request.content_type and 'multipart' in request.content_type:
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- File Validation ---

ALLOWED_EXTENSIONS = {'.docx', '.pdf', '.DOCX', '.PDF', '.txt', '.TXT', '.ppt', '.pptx', '.PPT', '.PPTX', '.doc', '.DOC', '.xls', '.xlsx', '.XLS', '.XLSX'}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

def allowed_file(filename):
    return any(filename.endswith(ext) for ext in ALLOWED_EXTENSIONS)

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
    register_celery_task(folder_path, result.id)
    
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
    register_celery_task(folder_path, result.id)
    
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
    
    original_filename = file.filename
    safe_name = sanitize_filename(original_filename)
    
    unique_filename = get_unique_filename(SOURCES_DIR, safe_name)
    target_path = os.path.join(SOURCES_DIR, unique_filename)
    
    file.save(target_path)
    
    return jsonify({
        'status': 'success',
        'original_filename': original_filename,
        'filename': unique_filename,
        'path': target_path,
        'renamed': unique_filename != safe_name
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
        update_task_progress(folder_path, 'Завантаження інформації про завдання...', 5)
        
        # Load task info
        logger.info(f"[ANALYZE] Loading task info from {folder_path}")
        task_info = load_task_info(folder_path)
        logger.info(f"[ANALYZE] Task info loaded: {task_info}")
        
        template_file = task_info.get('template_file')
        logger.info(f"[ANALYZE] Template file: {template_file}")
        
        safe_template = sanitize_filename(template_file)
        template_path = os.path.join(SOURCES_DIR, safe_template)
        logger.info(f"[ANALYZE] Full template path: {template_path}")
        logger.info(f"[ANALYZE] Template path exists: {os.path.exists(template_path)}")
        
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Template not found: {template_file}")
        
        try:
            file_size = os.path.getsize(template_path)
            logger.info(f"[ANALYZE] Template file size: {file_size} bytes")
        except Exception as e:
            logger.warning(f"[ANALYZE] Could not get file size: {e}")
        
        update_task_progress(folder_path, 'Парсинг шаблону...', 15)
        
        # Parse template for text extraction
        logger.info(f"[ANALYZE] Parsing template...")
        from .parsers.template_parser import parse_template
        template_data = parse_template(template_path)
        logger.info(f"[ANALYZE] Template data type: {type(template_data)}")
        logger.info(f"[ANALYZE] Template data (full): {json.dumps(template_data, ensure_ascii=False, default=str)[:500]}")
        
        # Check if template_data is a dict
        if not isinstance(template_data, dict):
            logger.error(f"[ANALYZE] ERROR: template_data is not dict, it's {type(template_data)}")
            raise TypeError(f"Expected dict from parse_template, got {type(template_data)}")
        
        logger.info(f"[ANALYZE] Template parsed, keys: {list(template_data.keys())}")
        has_text_key = 'text' in template_data
        logger.info(f"[ANALYZE] Has 'text' key in template_data: {has_text_key}")
        
        # Get text content for LLM
        text_content = template_data.get('text', '')
        text_content_len = len(text_content)
        logger.info(f"[ANALYZE] Text content length: {text_content_len}")
        
        if text_content:
            logger.info(f"[ANALYZE] Text content preview: {text_content[:300]}")
        else:
            logger.warning(f"[ANALYZE] Text content is EMPTY (length={text_content_len})")
            logger.warning(f"[ANALYZE] Template file extension: {os.path.splitext(template_path)[1].lower()}")
            logger.warning(f"[ANALYZE] Template data keys: {list(template_data.keys())}")
            # Log all values in template_data to see what was parsed
            for tk, tv in template_data.items():
                tv_str = str(tv)
                logger.warning(f"[ANALYZE]  template_data['{tk}']: {tv_str[:200]}")
        
        if not text_content:
            update_task_progress(folder_path, 'Витяг тексту з файлу шаблону...', 20)
            ext = os.path.splitext(template_path)[1].lower()
            if ext == '.pdf':
                logger.info(f"[ANALYZE] Extracting text from PDF...")
                import fitz
                doc = fitz.open(template_path)
                text_content = ""
                for page in doc:
                    text_content += page.get_text()
                doc.close()
                logger.info(f"[ANALYZE] PDF text extracted, length: {len(text_content)}")
            elif ext == '.docx':
                logger.warning(f"[ANALYZE] parse_template returned empty text for DOCX, trying direct extraction...")
                try:
                    from docx import Document
                    doc = Document(template_path)
                    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
                    for table in doc.tables:
                        for row in table.rows:
                            row_text = [c.text.strip() for c in row.cells if c.text.strip()]
                            if row_text:
                                parts.append(' | '.join(row_text))
                    text_content = '\n\n'.join(parts)
                    logger.info(f"[ANALYZE] Direct DOCX extraction: {len(text_content)} chars, {len(parts)} parts")
                except Exception as docx_err:
                    logger.error(f"[ANALYZE] Direct DOCX extraction failed: {docx_err}")
            else:
                logger.warning(f"[ANALYZE] No fallback extraction for extension: {ext}")
        
        update_task_progress(folder_path, 'Підготовка запиту до LLM...', 30)
        
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
        
        update_task_progress(folder_path, f'Виклик {selected_model} для аналізу...', 50)
        
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
                max_tokens=16384,
                temperature=TEMPERATURE
            )
            logger.info(f"[ANALYZE] Calling OpenAI model: {selected_model}...")
            response = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": "Ти — експерт з аналізу структури документів. Відповідай українською мовою."},
                    {"role": "user", "content": prompt}
                ],
                **openai_kwargs
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
                max_tokens=16384,
                temperature=TEMPERATURE
            )
            logger.info(f"[ANALYZE] Calling DeepSeek model: {selected_model}...")
            response = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": "Ти — експерт з аналізу структури документів. Відповідай українською мовою."},
                    {"role": "user", "content": prompt}
                ],
                **deepseek_kwargs
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
        
        # Validate: analysis must not be empty
        if not analysis_text or not analysis_text.strip():
            raise ValueError(
                f"Модель {selected_model} повернула порожню відповідь під час аналізу. "
                f"Будь ласка, спробуйте іншу модель або повторіть аналіз."
            )
        
        update_task_progress(folder_path, 'Збереження відповіді LLM...', 80)
        
        # Save AI response for debugging
        from .reasoning.generator import save_ai_response
        save_ai_response(task_id, "analysis", prompt, analysis_text, folder_path, selected_model)
        
        # Save analysis
        logger.info(f"[ANALYZE] Saving analysis result...")
        update_task_progress(folder_path, 'Збереження результату аналізу...', 90)
        append_debug_log(f"[CELERY] Збереження результату аналізу завдання {task_id}")
        save_analysis_result(folder_path, analysis_text)
        update_task_progress(folder_path, 'Аналіз завершено', 100)
        update_task_status(folder_path, 'idle')
        
        logger.info(f"[ANALYZE] Completed for task {task_id}")
        append_debug_log(f"[CELERY] Аналіз завершено для завдання {task_id}")
        return {'status': 'success', 'task_id': task_id}
        
    except Exception as e:
        import traceback
        logger.error(f"[ANALYZE] Failed for task {task_id}: {e}")
        logger.error(f"[ANALYZE] Traceback: {traceback.format_exc()}")
        local_vars = locals()
        ctx_template = local_vars.get('template_path', 'N/A')
        ctx_text = local_vars.get('text_content', 'N/A')
        ctx_text_len = len(ctx_text) if isinstance(ctx_text, str) else 'N/A'
        logger.error(f"[ANALYZE] Context: template_path={ctx_template}, text_content_len={ctx_text_len}")
        
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
    from .reasoning.generator import call_llm, generate_document_full
    from .task_manager import load_analysis_result, save_final_document, update_task_status, SOURCES_DIR
    from .reasoning.markdown_to_docx import convert_markdown_to_docx
    from .parsers.source_parser import parse_sources_with_metadata
    from .rag.pipeline import generate_document_rag
    import traceback
    import os

    selected_model = get_selected_model()
    attempt_num = self.request.retries + 1
    attempt_suffix = f" (Спроба {attempt_num})" if attempt_num > 1 else ""

    append_debug_log(f"[CELERY] Початок генерації документа для завдання {task_id}{attempt_suffix}")
    append_debug_log(f"[CELERY] Модель для генерації: {selected_model}")
    logger.info(f"[GENERATE] Starting document generation for task {task_id}")

    try:
        update_task_status(folder_path, 'generating')
        update_task_progress(folder_path, 'Завантаження аналізу...', 5)

        # 1. Load analysis text
        analysis_text = load_analysis_result(folder_path)
        if not analysis_text or not analysis_text.strip():
            raise ValueError("Аналіз порожній. Спочатку виконайте аналіз шаблону.")
        logger.info(f"[{task_id}] Analysis text loaded: {len(analysis_text)} chars")
        append_debug_log(f"[{task_id}] Аналіз завантажено: {len(analysis_text)} символів")

        # 2. Load source files
        update_task_progress(folder_path, 'Парсинг файлів джерел...', 12)
        source_paths = [os.path.join(SOURCES_DIR, f) for f in source_files if f]
        sources_data = {}
        if source_paths:
            logger.info(f"[{task_id}] Parsing source files: {source_files}")
            sources_data = parse_sources_with_metadata(source_paths)
            logger.info(f"[{task_id}] Sources parsed: {len(sources_data)} files")
            append_debug_log(f"[{task_id}] Джерела завантажено: {len(sources_data)} файлів")
        else:
            logger.info(f"[{task_id}] No source files provided")
            append_debug_log(f"[{task_id}] Джерела не вказано")

        # 3. Generate document content
        if use_rag:
            logger.info(f"[{task_id}] Using RAG mode for generation")
            append_debug_log(f"[{task_id}] Режим RAG: генерація з контекстом")
            update_task_progress(folder_path, 'RAG: Підготовка контексту...', 20)

            # Create progress callback for RAG pipeline
            def rag_progress(message, pct):
                update_task_progress(folder_path, f'RAG: {message}', 20 + int(pct * 0.55))

            # Pipeline returns the prompt (context + structure)
            rag_prompt = generate_document_rag(
                analysis_text=analysis_text,
                sources_data=sources_data,
                task_id=task_id,
                user_prompt=generation_prompt or None,
                task_folder=folder_path,
                selected_model=selected_model,
                chunk_params=chunk_params,
                progress_callback=rag_progress
            )

            update_task_progress(folder_path, f'Виклик {selected_model} для генерації...', 75)

            # Call LLM with the RAG-built prompt
            markdown_text = call_llm(
                prompt=rag_prompt,
                task_id=task_id,
                task_folder=folder_path,
                selected_model=selected_model,
                response_type="rag_doc"
            )

            # Деанонімізуємо відповідь LLM (промпт був анонімізований в pipeline)
            update_task_progress(folder_path, 'Деанонімізація відповіді...', 82)
            from .rag.anonymizer import get_anonymizer
            anonymizer = get_anonymizer()
            markdown_text = anonymizer.deanonymize(markdown_text)
        else:
            logger.info(f"[{task_id}] Using FULL mode (one-pass generation)")
            append_debug_log(f"[{task_id}] Режим FULL: одноетапна генерація")
            update_task_progress(folder_path, f'Генерація через {selected_model}...', 25)

            markdown_text = generate_document_full(
                analysis_text=analysis_text,
                sources_data=sources_data,
                task_id=task_id,
                user_prompt=generation_prompt or None,
                task_folder=folder_path,
                selected_model=selected_model
            )

        logger.info(f"[{task_id}] Markdown generated: {len(markdown_text)} chars")
        append_debug_log(f"[{task_id}] Згенеровано: {len(markdown_text)} символів")

        # 4. Convert markdown to DOCX
        update_task_progress(folder_path, 'Конвертація в DOCX...', 88)
        temp_docx_path = os.path.join(folder_path, f"temp_{task_id}.docx")
        convert_markdown_to_docx(markdown_text, temp_docx_path)
        logger.info(f"[{task_id}] DOCX created: {temp_docx_path}")
        append_debug_log(f"[{task_id}] DOCX створено")

        # 5. Save final document to task folder
        update_task_progress(folder_path, 'Збереження результату...', 95)
        save_final_document(folder_path, temp_docx_path)
        update_task_progress(folder_path, 'Готово', 100)
        update_task_status(folder_path, 'completed')

        # Cleanup temp
        if os.path.exists(temp_docx_path):
            os.remove(temp_docx_path)

        logger.success(f"[{task_id}] Document generation completed")
        append_debug_log(f"[CELERY] Генерація документа завершена для завдання {task_id}")
        return {'status': 'success', 'task_id': task_id}

    except Exception as e:
        logger.error(f"[GENERATE] Failed for task {task_id}: {e}")
        logger.error(f"[GENERATE] Traceback: {traceback.format_exc()}")
        error_msg = str(e)[:200]
        append_debug_log(f"[CELERY] ПОМИЛКА генерації документа для завдання {task_id}: '{error_msg}'")

        if self.request.retries < self.max_retries:
            next_attempt = self.request.retries + 2
            append_debug_log(f"[CELERY] Буде запущена {next_attempt} спроба генерації через 30 секунд")

        if self.request.retries >= self.max_retries:
            update_task_status(folder_path, 'failed', error_msg)
        raise self.retry(exc=e, countdown=30)


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


# ============================================================================
# SOURCE FILES MANAGEMENT - New page with progress visualization
# ============================================================================

@doc_generator_bp.route('/sources')
@login_required
def sources_page():
    """Page for managing source files with processing visualization."""
    return render_template('doc_generator/sources.html')


@doc_generator_bp.route('/api/sources', methods=['GET'])
@login_required
def list_sources():
    """List all source files with their processing status."""
    from .source_processor import get_source_status
    status = get_source_status()
    
    # Also return raw file list for UI
    from .task_manager import get_available_files
    files = get_available_files('source')
    
    return jsonify({
        'sources': files.get('sources', []),
        'status': status
    })


@doc_generator_bp.route('/api/sources/upload', methods=['POST'])
@login_required
def upload_source_file():
    """Upload a source file (saves to disk but doesn't process yet)."""
    logger.info(f"[SOURCES-UPLOAD] Request files: {list(request.files.keys()) if request.files else 'none'}")
    logger.info(f"[SOURCES-UPLOAD] Content-Type: {request.content_type}")
    logger.info(f"[SOURCES-UPLOAD] Content-Length: {request.content_length}")
    
    if 'file' not in request.files:
        logger.warning(f"[SOURCES-UPLOAD] No file in request. Files keys: {list(request.files.keys())}")
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    logger.info(f"[SOURCES-UPLOAD] File received: filename='{file.filename}', content_type='{file.content_type}'")
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Validate file extension
    if not allowed_file(file.filename):
        logger.warning(f"[SOURCES-UPLOAD] File type not allowed: {file.filename}")
        return jsonify({'error': f'Тип файлу не підтримується: {file.filename}'}), 400
    
    # Sanitize and save
    safe_name = sanitize_filename(file.filename)
    target_path = os.path.join(SOURCES_DIR, safe_name)
    logger.info(f"[SOURCES-UPLOAD] Sanitized filename: {safe_name}")
    
    # Handle duplicate
    if os.path.exists(target_path):
        safe_name = get_unique_filename(SOURCES_DIR, safe_name)
        target_path = os.path.join(SOURCES_DIR, safe_name)
        logger.info(f"[SOURCES-UPLOAD] Duplicate, renamed to: {safe_name}")
    
    try:
        file.save(target_path)
        file_size = os.path.getsize(target_path)
        logger.info(f"[SOURCES-UPLOAD] File saved: {target_path} ({file_size} bytes)")
    except Exception as e:
        logger.error(f"[SOURCES-UPLOAD] Failed to save file: {e}")
        return jsonify({'error': f'Помилка збереження файлу: {str(e)}'}), 500
    
    logger.info(f"[SOURCES] File uploaded: {safe_name}")
    append_debug_log(f"[SOURCES] Завантажено файл: {safe_name}")
    
    # Initialize status as pending
    try:
        from .source_processor import get_source_status, _save_status as save_src_status
        status = get_source_status()
        status[safe_name] = {
            'filename': safe_name,
            'status': 'pending',
            'progress': 0,
            'message': 'Очікування обробки',
            'uploaded_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        save_src_status(status)
    except Exception as e:
        logger.warning(f"[SOURCES] Failed to initialize status for {safe_name}: {e}")
    
    return jsonify({
        'status': 'success',
        'filename': safe_name,
        'path': target_path
    })


@doc_generator_bp.route('/api/sources/process', methods=['POST'])
@login_required
def process_source():
    """Start processing (chunking + vectorization) for a source file."""
    data = request.get_json()
    if not data or 'filename' not in data:
        return jsonify({'error': 'filename is required'}), 400
    
    filename = data['filename']
    file_path = os.path.join(SOURCES_DIR, filename)
    
    if not os.path.exists(file_path):
        return jsonify({'error': f'File not found: {filename}'}), 404
    
    # Start Celery task
    from .source_processor import process_source_file_task
    result = process_source_file_task.delay(file_path, filename)
    
    append_debug_log(f"[SOURCES] Запущено обробку джерела: {filename}")
    
    return jsonify({
        'message': 'Processing started',
        'celery_task_id': result.id,
        'filename': filename
    })


@doc_generator_bp.route('/api/sources/process_all', methods=['POST'])
@login_required
def process_all_pending_sources():
    """Start processing all pending source files in sequence."""
    from .source_processor import get_source_status
    status = get_source_status()
    
    pending = [
        fn for fn, info in status.items()
        if info.get('status') in ('pending', 'error')
    ]
    
    started = []
    for filename in pending:
        file_path = os.path.join(SOURCES_DIR, filename)
        if os.path.isfile(file_path):
            from .source_processor import process_source_file_task
            result = process_source_file_task.delay(file_path, filename)
            started.append({'filename': filename, 'celery_task_id': result.id})
    
    append_debug_log(f"[SOURCES] Запущено обробку {len(started)} джерел")
    
    return jsonify({
        'message': f'Processing started for {len(started)} sources',
        'started': started
    })


@doc_generator_bp.route('/api/sources/status', methods=['GET'])
@login_required
def sources_status():
    """Get processing status for all source files (for real-time updates)."""
    from .source_processor import get_source_status
    status = get_source_status()
    return jsonify(status)


@doc_generator_bp.route('/api/sources/delete', methods=['POST'])
@login_required
def delete_source_file():
    """Delete a source file and its status entry."""
    data = request.get_json()
    if not data or 'filename' not in data:
        return jsonify({'error': 'filename is required'}), 400
    
    filename = data['filename']
    file_path = os.path.join(SOURCES_DIR, filename)
    
    # Delete file
    deleted_file = False
    if os.path.exists(file_path):
        os.remove(file_path)
        deleted_file = True
    
    # Delete from status
    from .source_processor import _load_status, _save_status
    status = _load_status()
    if filename in status:
        del status[filename]
        _save_status(status)
    
    # Also try to delete ChromaDB collection if exists
    try:
        from .rag.embedder import Embedder, compute_sources_hash
        sources_hash = compute_sources_hash([file_path]) if os.path.exists(file_path) else None
        if sources_hash:
            embedder = Embedder()
            if embedder.collection_exists(sources_hash):
                embedder.delete_collection(sources_hash)
                logger.info(f"[SOURCES] Deleted ChromaDB collection for {filename}")
    except Exception as e:
        logger.warning(f"[SOURCES] Failed to delete ChromaDB collection for {filename}: {e}")
    
    append_debug_log(f"[SOURCES] Видалено джерело: {filename}")
    
    return jsonify({
        'status': 'success',
        'filename': filename,
        'file_deleted': deleted_file
    })


# ============================================================================
# SOURCE CHUNKS - View all chunks for a specific source
# ============================================================================

@doc_generator_bp.route('/api/sources/<filename>/chunks', methods=['GET'])
@login_required
def get_source_chunks(filename: str):
    """Get all chunks for a specific source from ChromaDB."""
    logger.info(f"[CHUNKS] Request for chunks: filename='{filename}'")
    append_debug_log(f"[CHUNKS] Запит чанків для: {filename}")
    try:
        from .source_processor import get_source_status
        from .rag.embedder import Embedder, compute_sources_hash
        from .task_manager import SOURCES_DIR
        
        # Get source info from status
        status = get_source_status(filename)
        logger.info(f"[CHUNKS] Status for '{filename}': {status.get('status') if status else 'NO STATUS'}")
        append_debug_log(f"[CHUNKS] Статус: {status.get('status') if status else 'NO STATUS'}")
        
        if not status:
            logger.warning(f"[CHUNKS] No status found for '{filename}'")
            append_debug_log(f"[CHUNKS] ❌ Статус не знайдено для: {filename}")
            return jsonify({'error': 'Source not found in status'}), 404
        
        if status.get('status') != 'ready':
            logger.warning(f"[CHUNKS] Source '{filename}' not ready (status={status.get('status')})")
            append_debug_log(f"[CHUNKS] ❌ Джерело не готове: {status.get('status')}")
            return jsonify({'error': 'Source not ready or not found'}), 404
        
        stored_collection = status.get('collection')
        logger.info(f"[CHUNKS] Stored collection name: {stored_collection}")
        append_debug_log(f"[CHUNKS] Колекція з статусу: {stored_collection}")
        
        embedder = Embedder()
        collection = None
        
        # Try stored collection name first
        if stored_collection:
            all_cols = [c.name for c in embedder.client.list_collections()]
            logger.info(f"[CHUNKS] Available ChromaDB collections ({len(all_cols)}): {all_cols}")
            append_debug_log(f"[CHUNKS] Доступні колекції: {all_cols}")
            
            if stored_collection in all_cols:
                collection = embedder.client.get_collection(stored_collection)
                logger.info(f"[CHUNKS] Found collection by stored name: '{stored_collection}'")
                append_debug_log(f"[CHUNKS] ✅ Знайдено колекцію за ім'ям: {stored_collection}")
            else:
                logger.warning(f"[CHUNKS] Stored collection '{stored_collection}' NOT found in ChromaDB!")
                append_debug_log(f"[CHUNKS] ❌ Колекція '{stored_collection}' не знайдена в ChromaDB!")
        
        # Fallback: recompute hash
        if not collection:
            file_path = os.path.join(SOURCES_DIR, filename)
            logger.info(f"[CHUNKS] Trying fallback: file_path='{file_path}', exists={os.path.isfile(file_path)}")
            append_debug_log(f"[CHUNKS] Спроба знайти за хешем: {file_path}")
            
            if os.path.isfile(file_path):
                sources_hash = compute_sources_hash([file_path])
                logger.info(f"[CHUNKS] Computed sources_hash: {sources_hash}")
                
                if embedder.collection_exists(sources_hash):
                    col_name = embedder.get_collection_name(sources_hash)
                    logger.info(f"[CHUNKS] Found collection by hash: '{col_name}'")
                    append_debug_log(f"[CHUNKS] ✅ Знайдено колекцію за хешем: {col_name}")
                    collection = embedder.client.get_collection(col_name)
                else:
                    logger.warning(f"[CHUNKS] No collection exists for hash '{sources_hash}'")
                    append_debug_log(f"[CHUNKS] ❌ Немає колекції для хешу {sources_hash}")
            else:
                logger.warning(f"[CHUNKS] File not found for fallback: '{file_path}'")
                append_debug_log(f"[CHUNKS] ❌ Файл не знайдено: {filename}")
        
        if not collection:
            logger.error(f"[CHUNKS] No collection found for '{filename}' after all attempts")
            append_debug_log(f"[CHUNKS] ❌ Колекцію не знайдено жодним способом")
            return jsonify({'error': 'ChromaDB collection not found'}), 404
        
        # Get all chunks
        data = collection.get(include=['documents', 'metadatas'])
        
        documents = data.get('documents', []) or []
        metadatas = data.get('metadatas', []) or []
        
        logger.info(f"[CHUNKS] Collection '{stored_collection or '?'}' returned {len(documents)} docs, {len(metadatas)} metadatas")
        append_debug_log(f"[CHUNKS] Колекція повернула: {len(documents)} документів, {len(metadatas)} метаданих")
        
        if not documents:
            logger.warning(f"[CHUNKS] Collection is empty - no documents found!")
            append_debug_log(f"[CHUNKS] ❌ Колекція порожня!")
            return jsonify({
                'filename': filename,
                'collection': stored_collection or 'unknown',
                'total_chunks': 0,
                'chunks': []
            })
        
        chunks = []
        for i, (doc, meta) in enumerate(zip(documents, metadatas)):
            chunk_index = meta.get('chunk_index', i)
            source = meta.get('source', filename)
            word_count = meta.get('word_count', 0)
            text_len = len(doc) if doc else 0
            
            logger.debug(f"[CHUNKS]   Chunk #{chunk_index}: source={source}, words={word_count}, chars={text_len}")
            
            chunks.append({
                'chunk_index': chunk_index,
                'source': source,
                'word_count': word_count,
                'text_preview': doc[:200] + ('...' if doc and len(doc) > 200 else ''),
                'text': doc or ''
            })
        
        # Sort by chunk_index
        chunks.sort(key=lambda x: x['chunk_index'])
        
        logger.info(f"[CHUNKS] Returning {len(chunks)} chunks for '{filename}'")
        append_debug_log(f"[CHUNKS] ✅ Повертаю {len(chunks)} чанків для {filename}")
        
        return jsonify({
            'filename': filename,
            'collection': stored_collection or 'unknown',
            'total_chunks': len(chunks),
            'chunks': chunks
        })
        
    except Exception as e:
        logger.error(f"[CHUNKS] Failed to get chunks for {filename}: {e}")
        logger.error(traceback.format_exc())
        append_debug_log(f"[CHUNKS] ❌ Помилка: {str(e)[:200]}")
        return jsonify({'error': str(e)[:200]}), 500


# ============================================================================
# RAG QUERY - Interactive prompt analysis across all ready sources
# ============================================================================

RAG_TESTING_DIR = os.path.join(
    os.path.dirname(__file__),
    'Tasks-data', 'rag-testing'
)


@doc_generator_bp.route('/api/sources/rag-query', methods=['POST'])
@login_required
def rag_query_sources():
    """
    Interactive RAG query against ALL processed source documents.
    
    1. Find all 'ready' source files from .sources_status.json
    2. Use HybridSearch (BM25 + semantic) across all ChromaDB collections
    3. Build prompt with chunks + user question, send to LLM
    4. Save prompt/response via save_ai_response() to rag-testing/
    5. Return response to UI
    """
    data = request.get_json()
    if not data or 'prompt' not in data:
        return jsonify({'error': 'prompt is required'}), 400
    
    user_prompt = data['prompt'].strip()
    if not user_prompt:
        return jsonify({'error': 'prompt cannot be empty'}), 400
    
    # Similarity threshold (0.3–0.7). Chunks with cosine similarity below this
    # are excluded. Lower = more creative/lenient, higher = more precise/strict.
    threshold = float(data.get('threshold', 0.5))
    threshold = max(0.3, min(0.7, threshold))  # Clamp to valid range
    
    # Alpha for hybrid search (0.0 = pure semantic, 1.0 = pure BM25)
    alpha = float(data.get('alpha', 0.5))
    alpha = max(0.0, min(1.0, alpha))
    
    append_debug_log(f"[RAG-QUERY] Запит: {user_prompt[:100]}..., поріг: {threshold}, alpha: {alpha}")
    
    try:
        # ── Step 1: Collect ready source files ──────────────────────────────
        from .source_processor import get_source_status
        from .rag.embedder import Embedder, compute_sources_hash
        from .rag.hybrid_search import HybridSearch
        from .rag.retriever import Retriever
        from .reasoning.generator import call_llm
        from .task_manager import SOURCES_DIR
        
        all_status = get_source_status()
        ready_sources = [
            fn for fn, info in all_status.items()
            if info.get('status') == 'ready'
        ]
        
        if not ready_sources:
            append_debug_log(f"[RAG-QUERY] Немає оброблених джерел")
            return jsonify({
                'response': None,
                'error': 'Немає оброблених джерел. Спочатку обробіть джерела.',
                'chunks_used': 0,
                'sources_used': []
            })
        
        logger.info(f"[RAG-QUERY] Ready sources: {ready_sources}")
        append_debug_log(f"[RAG-QUERY] Знайдено джерел: {len(ready_sources)}")
        
        # ── Step 2: Hybrid search across ALL collections ────────────────────
        embedder = Embedder()
        sources_hashes = []
        per_source_stats = {}
        
        for filename in ready_sources:
            file_path = os.path.join(SOURCES_DIR, filename)
            
            if not os.path.isfile(file_path):
                logger.warning(f"[RAG-QUERY] Source file not found: {file_path}")
                per_source_stats[filename] = {'status': 'file_not_found', 'chunks': []}
                continue
            
            src_info = all_status.get(filename, {})
            stored_collection = src_info.get('collection')
            sources_hash = None
            
            if stored_collection:
                all_chroma_cols = [c.name for c in embedder.client.list_collections()]
                if stored_collection in all_chroma_cols:
                    sources_hash = stored_collection.replace('rag_', '', 1)
            
            if not sources_hash:
                sources_hash = compute_sources_hash([file_path])
                if not embedder.collection_exists(sources_hash):
                    logger.warning(f"[RAG-QUERY] No ChromaDB collection for {filename}")
                    per_source_stats[filename] = {'status': 'collection_not_found', 'chunks': []}
                    continue
            
            sources_hashes.append(sources_hash)
            per_source_stats[filename] = {'status': 'ok', 'chunks': []}  # will fill below
        
        if not sources_hashes:
            return jsonify({
                'response': None,
                'error': 'Не знайдено жодної ChromaDB-колекції. Переобробіть джерела.',
                'chunks_used': 0,
                'sources_used': []
            })
        
        # Hybrid search: BM25 + semantic
        hybrid = HybridSearch(embedder)
        all_chunks = hybrid.search(
            query=user_prompt,
            sources_hashes=sources_hashes,
            top_k=10,
            alpha=alpha,
            threshold=threshold,
        )
        
        # Fill per_source_stats with actual results
        sources_used = []
        for chunk in all_chunks:
            source_name = chunk.get('source', 'unknown')
            if source_name not in sources_used:
                sources_used.append(source_name)
            if source_name in per_source_stats:
                per_source_stats[source_name]['chunks'].append({
                    'chunk_index': chunk.get('chunk_index', 0),
                    'cosine_similarity': chunk.get('score', 0.0),
                    'bm25_score': chunk.get('bm25_score', 0.0),
                    'semantic_score': chunk.get('semantic_score', 0.0),
                })
        
        logger.info(f"[RAG-QUERY] Total unique chunks: {len(all_chunks)}")
        append_debug_log(f"[RAG-QUERY] Знайдено фрагментів: {len(all_chunks)}")
        
        # ── Step 3: Format context from chunks ──────────────────────────────
        if all_chunks:
            context = Retriever.format_context_for_llm(all_chunks)
        else:
            context = "(Не знайдено релевантних фрагментів у джерелах)"
        
        # ── Step 4: Анонімізуємо контекст + будуємо LLM prompt ─────────────
        from .rag.anonymizer import get_anonymizer
        anonymizer = get_anonymizer()
        context_anon = anonymizer.anonymize(context)
        logger.info(f"[RAG-QUERY] Context anonymized: {len(context)} → {len(context_anon)} chars, "
                    f"{len(anonymizer.get_map())} placeholders")

        full_prompt = RAG_PROMPT_TEMPLATE.format(
            context=context_anon,
            user_prompt=user_prompt
        )
        
        # ── Step 5: Send to LLM ─────────────────────────────────────────────
        selected_model = get_selected_model()
        rag_testing_folder = RAG_TESTING_DIR
        os.makedirs(rag_testing_folder, exist_ok=True)
        
        # Pass task_folder=None to call_llm so its internal save_ai_response
        # skips saving (save_ai_response skips when task_folder is None).
        # All needed data is saved explicitly below (prompt.txt, response.txt, stats.json).
        query_task_id = f"rag-query-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        
        markdown_response = call_llm(
            prompt=full_prompt,
            task_id=query_task_id,
            task_folder=None,
            selected_model=selected_model,
            response_type="rag_query"
        )
        
        # ── Step 6: Save 4 fixed files directly in rag-testing (overwrite) ──
        # File 1: prompt.txt — анонімізований промпт (реальний, що пішов у LLM)
        prompt_path = os.path.join(rag_testing_folder, 'prompt.txt')
        with open(prompt_path, 'w', encoding='utf-8') as f:
            f.write(full_prompt)
        
        # File 2: response.txt — сира відповідь LLM (з плейсхолдерами, ще не деанонімізована)
        raw_response = markdown_response
        response_path = os.path.join(rag_testing_folder, 'response.txt')
        with open(response_path, 'w', encoding='utf-8') as f:
            f.write(raw_response)
        
        # File 3: response_deanonymized.txt — деанонімізована відповідь (для читання людиною)
        deanonymized_response = anonymizer.deanonymize(raw_response)
        deanonymized_path = os.path.join(rag_testing_folder, 'response_deanonymized.txt')
        with open(deanonymized_path, 'w', encoding='utf-8') as f:
            f.write(deanonymized_response)
        
        # Для повернення в UI використовуємо деанонімізовану відповідь
        markdown_response = deanonymized_response
        
        # File 3: stats.json — user query, per-source chunk search, LLM response
        stats = {
            'user_query': user_prompt,
            'threshold': threshold,
            'alpha': alpha,
            'search_mode': 'hybrid',
            'model': selected_model,
            'sources_searched': ready_sources,
            'sources_with_chunks': sources_used,
            'total_chunks_found': len(all_chunks),
            'per_source_stats': per_source_stats,
            'chunks_in_final_query': [
                {
                    'source': c.get('source', 'unknown'),
                    'chunk_index': c.get('chunk_index', 0),
                    'score': c.get('score', c.get('similarity', 0.0)),
                    'bm25_score': c.get('bm25_score', 0.0),
                    'semantic_score': c.get('semantic_score', 0.0),
                }
                for c in all_chunks
            ],
            'llm_response': markdown_response,
            'timestamp': datetime.utcnow().isoformat()
        }
        stats_path = os.path.join(rag_testing_folder, 'stats.json')
        with open(stats_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        
        append_debug_log(f"[RAG-QUERY] Відповідь отримано ({len(markdown_response)} символів)")
        
        return jsonify({
            'response': markdown_response,
            'chunks_used': len(all_chunks),
            'sources_used': sources_used
        })
        
    except Exception as e:
        logger.error(f"[RAG-QUERY] Failed: {e}")
        logger.error(traceback.format_exc())
        append_debug_log(f"[RAG-QUERY] ПОМИЛКА: {str(e)[:200]}")
        
        return jsonify({
            'response': None,
            'error': f'Помилка аналізу: {str(e)[:200]}',
            'chunks_used': 0,
            'sources_used': []
        }), 500


