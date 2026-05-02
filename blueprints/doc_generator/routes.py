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
try:
    from .reasoning.planner import create_document_plan, save_plan
except ImportError:
    # planner.py may not exist - old architecture
    create_document_plan = None
    save_plan = None
from .reasoning.generator import generate_all_content
from .builders import build_document, build_document_simple
try:
    from .rag.embedder import embed_all_sources, should_use_rag
    from .rag.retriever import retrieve_for_section, format_chunks_for_llm
except ImportError:
    # RAG module may not exist
    embed_all_sources = None
    should_use_rag = None
    retrieve_for_section = None
    format_chunks_for_llm = None
from .config import USE_RAG, ANALYSIS_PROMPT, LLM_ANALYSIS_PROMPT
try:
    from .cleanup import cleanup_old_tasks, cleanup_chroma_collections, get_task_stats, get_disk_usage
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

# --- Celery Task ---

@shared_task(bind=True)
def process_document_task(self, task_id, template_path, sources_paths, data_dir, user_prompt=None):
    """
    Process document generation task.
    Stage 3: Parsing template and sources.
    NOTE: This is the OLD architecture - requires planner.py
    """
    from loguru import logger
    
    if create_document_plan is None or save_plan is None:
        logger.error(f"[{task_id}] OLD architecture not available - planner.py missing")
        raise ValueError("OLD architecture not available. Use new architecture: POST /api/task → /api/tasks/<id>/analysis → /api/tasks/<id>/generate")
    
    task_dir = os.path.join(data_dir, 'tasks', task_id)
    task_json_path = os.path.join(task_dir, 'task.json')
    
    def update_status(status, progress=None, message=None, error=None, extra=None):
        task_data = {
            'task_id': task_id,
            'status': status,
            'created_at': datetime.utcnow().isoformat(),
        }
        if progress is not None:
            task_data['progress'] = progress
        if message:
            task_data['message'] = message
        if error:
            task_data['error'] = error
        if extra:
            task_data.update(extra)
        if status == 'completed':
            task_data['completed_at'] = datetime.utcnow().isoformat()
            # Output filename will be set later in the process
        
        with open(task_json_path, 'w') as f:
            json.dump(task_data, f, indent=2)
        
        logger.info(f"Task {task_id}: {status} ({progress}%) - {message}")
    
    try:
        # Step 1: Parse template (10%)
        update_status('processing', progress=5, message='Parsing template structure...')
        
        template_structure = parse_template(template_path)
        template_json_path = os.path.join(task_dir, 'template_structure.json')
        with open(template_json_path, 'w', encoding='utf-8') as f:
            json.dump(template_structure, f, ensure_ascii=False, indent=2)
        
        update_status('processing', progress=15, message=f"Template parsed: {len(template_structure.get('sections', []))} sections, {len(template_structure.get('tables', []))} tables")
        
        # Step 2: Parse sources (15-50%)
        update_status('processing', progress=20, message=f'Parsing {len(sources_paths)} source files...')
        
        sources_data = parse_sources_with_metadata(sources_paths)
        
        # Check for errors
        errors = [name for name, data in sources_data.items() if data['status'] == 'error']
        if errors:
            logger.warning(f"Some sources failed to parse: {errors}")
        
        # Calculate total text size
        total_chars = sum(d['char_count'] for d in sources_data.values())
        sources_json_path = os.path.join(task_dir, 'sources_text.json')
        with open(sources_json_path, 'w', encoding='utf-8') as f:
            json.dump(sources_data, f, ensure_ascii=False, indent=2)
        
        source_names = list(sources_data.keys())
        
        # Stage 6: RAG - Embed sources if needed
        use_rag = USE_RAG or should_use_rag(sources_data)
        embedded_chunks = 0
        
        if use_rag:
            update_status('processing', progress=48, message=f'Embedding sources for RAG (USE_RAG={USE_RAG}, auto={should_use_rag(sources_data)})...')
            try:
                embedded_chunks = embed_all_sources(sources_data, task_id, chunk_size=800, overlap=150)
                logger.info(f"[{task_id}] Embedded {embedded_chunks} chunks for RAG")
            except Exception as e:
                logger.warning(f"[{task_id}] RAG embedding failed, falling back to direct context: {e}")
                use_rag = False
        
        update_status('processing', progress=50, 
                     message=f"Sources parsed: {len(source_names)} files, {total_chars} total chars" + (f", {embedded_chunks} RAG chunks" if use_rag else ""),
                     extra={'parsed_sources': source_names, 'total_source_chars': total_chars, 'rag_enabled': use_rag, 'rag_chunks': embedded_chunks})
        
        # Step 3: LLM Analysis (pre-planning insights)
        update_status('processing', progress=52, message='Running LLM analysis of template and sources...')
        
        try:
            # Import LLM client
            from .config import GEMINI_API_KEY, REASONING_MODEL, TEMPERATURE
            from google import genai
            
            client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
            
            if client:
                # Helper function for LLM calls with retry logic
                def llm_call_with_retry(prompt, step_name, max_retries=3, base_delay=10):
                    for attempt in range(max_retries):
                        try:
                            logger.info(f"[{task_id}] {step_name}: Attempt {attempt + 1}/{max_retries}")
                            response = client.models.generate_content(
                                model=REASONING_MODEL,
                                contents=prompt,
                                config={"temperature": TEMPERATURE}
                            )
                            return response
                        except Exception as e:
                            error_str = str(e)
                            if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
                                if attempt < max_retries - 1:
                                    delay = base_delay * (2 ** attempt)
                                    logger.warning(f"[{task_id}] {step_name}: 429 error, waiting {delay}s before retry...")
                                    time.sleep(delay)
                                    continue
                            raise
                    raise Exception(f"Max retries exceeded for {step_name}")
                
                # LLM Analysis - get insights before planning
                update_status('processing', progress=53, message='LLM: Analyzing how to fill template with source data...')
                
                first_prompt = LLM_ANALYSIS_PROMPT.format(
                    num_sections=len(template_structure.get('sections', [])),
                    num_tables=len(template_structure.get('tables', [])),
                    user_prompt=user_prompt if user_prompt else 'Створити документ відповідно до джерел'
                )
                
                response1 = llm_call_with_retry(first_prompt, "LLM Analysis")
                first_llm_response = response1.text
                logger.info(f"[{task_id}] LLM analysis: {len(first_llm_response)} chars")
                
                # Save AI response for debugging
                from .reasoning.generator import save_ai_response
                save_ai_response(task_id, "llm_analysis", first_prompt, first_llm_response, folder_path)
                
                # Save LLM analysis for reference (can be used by planning)
                analysis_path = os.path.join(task_dir, 'llm_analysis.json')
                with open(analysis_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        'analysis': first_llm_response,
                        'user_prompt': user_prompt,
                        'template_sections': len(template_structure.get('sections', [])),
                        'template_tables': len(template_structure.get('tables', []))
                    }, f, ensure_ascii=False, indent=2)
                
                update_status('processing', progress=55, message='LLM analysis completed')
                
                # Wait between LLM calls to avoid rate limiting
                logger.info(f"[{task_id}] Waiting 30s between LLM calls...")
                time.sleep(30)
            else:
                logger.warning(f"[{task_id}] No API key, skipping LLM pre-analysis")
                
        except Exception as e:
            logger.warning(f"[{task_id}] LLM analysis failed: {e}, continuing with standard workflow")
        
        # Step 4: Create document plan using LLM (Stage 4 - Planning)
        update_status('processing', progress=56, message='Creating document plan with AI...')
        
        try:
            document_plan = create_document_plan(template_structure, sources_data, task_id, user_prompt)
            plan_path = os.path.join(task_dir, 'document_plan.json')
            save_plan(document_plan, plan_path)
            
            update_status('processing', progress=65, 
                         message=f"Plan created: {len(document_plan.sections)} sections, {len(document_plan.tables)} tables",
                         extra={'plan_sections': len(document_plan.sections), 
                                'plan_tables': len(document_plan.tables),
                                'document_title': document_plan.document_title})
            
            # Wait between planning and generation to avoid rate limiting
            logger.info(f"[{task_id}] Waiting 30s before content generation...")
            time.sleep(30)
        except Exception as e:
            logger.error(f"[{task_id}] Planning failed: {e}")
            update_status('failed', error=f"Failed to create plan: {e}")
            raise
        
        # Step 5: Generate content
        update_status('processing', progress=70, message='Generating document content with AI...')
        
        def progress_callback(completed, total, message):
            # Map 70-90% to generation progress
            pct = 70 + int((completed / total) * 20) if total > 0 else 70
            update_status('processing', progress=pct, message=message)
        
        try:
            document_plan_dict = document_plan.model_dump()
            
            # If RAG enabled, modify sources_data to use retrieved chunks per section
            if use_rag:
                sources_for_generation = {}
                for section in document_plan_dict.get('sections', []):
                    title = section.get('title', '')
                    content_type = section.get('content_type', '')
                    key_points = section.get('key_points', [])
                    
                    # Retrieve relevant chunks for this section
                    chunks = retrieve_for_section(title, content_type, key_points, task_id, n_results=5)
                    
                    # Format as virtual "document"
                    section_source = f"retrieved_for_{title[:30]}"
                    sources_for_generation[section_source] = {
                        'text': format_chunks_for_llm(chunks),
                        'char_count': sum(len(c['text']) for c in chunks),
                        'type': 'rag_chunks',
                        'status': 'success'
                    }
                
                logger.info(f"[{task_id}] Using RAG with {len(sources_for_generation)} virtual sources")
            else:
                sources_for_generation = sources_data
            
            generated_content = generate_all_content(
                document_plan_dict, 
                sources_for_generation, 
                task_id,
                progress_callback,
                user_prompt
            )
            
            # Save generated content
            content_path = os.path.join(task_dir, 'generated_content.json')
            with open(content_path, 'w', encoding='utf-8') as f:
                json.dump(generated_content, f, ensure_ascii=False, indent=2)
            
            update_status('processing', progress=90, 
                         message=f"Content generated: {generated_content['total_word_count']} words, {generated_content['total_table_rows']} table rows",
                         extra={'generated_words': generated_content['total_word_count'],
                                'generated_table_rows': generated_content['total_table_rows']})
        except Exception as e:
            logger.error(f"[{task_id}] Content generation failed: {e}")
            update_status('failed', error=f"Failed to generate content: {e}")
            raise
        
        # Step 5: Build document (Stage 5)
        update_status('processing', progress=95, message='Building final DOCX document...')
        
        output_dir = os.path.join(task_dir, 'output')
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate output filename: source name + " новий"
        if sources_paths:
            first_source_name = os.path.splitext(os.path.basename(sources_paths[0]))[0]
            output_filename = f"{first_source_name} новий.docx"
        else:
            output_filename = "result.docx"
        
        result_file = os.path.join(output_dir, output_filename)
        
        try:
            # Try smart document building (matching sections to template)
            build_document(template_path, generated_content, result_file, task_id)
            
            # Also create text preview for reference
            preview_file = result_file.replace('.docx', '_preview.txt')
            with open(preview_file, 'w', encoding='utf-8') as f:
                f.write(f"Документ: {generated_content.get('document_title', 'Untitled')}\n")
                f.write(f"Тип: {generated_content.get('document_type', 'document')}\n")
                f.write(f"===========================\n\n")
                for sec in generated_content.get('sections', []):
                    f.write(f"\n## {sec['title']}\n\n")
                    f.write(f"{sec['content']}\n")
                    f.write(f"\n({sec.get('word_count', 0)} слів)\n")
                f.write(f"\n\nВсього: {generated_content.get('total_word_count', 0)} слів\n")
            
            logger.success(f"[{task_id}] DOCX built successfully")
            
        except Exception as e:
            logger.error(f"[{task_id}] Smart document build failed: {e}, using simple builder")
            # Fallback to simple builder
            try:
                build_document_simple(template_path, generated_content, result_file, task_id)
            except Exception as e2:
                logger.error(f"[{task_id}] Simple build also failed: {e2}")
                raise
        
        # Step 6: Complete
        update_status('completed', progress=100, 
                     message='Document generated successfully!',
                     extra={'template_sections': len(template_structure.get('sections', [])),
                            'template_tables': len(template_structure.get('tables', [])),
                            'sources_parsed': len(sources_data),
                            'generated_words': generated_content.get('total_word_count', 0),
                            'generated_table_rows': generated_content.get('total_table_rows', 0),
                            'output_file': result_file,
                            'output_filename': os.path.basename(result_file)})
        
        logger.success(f"Task {task_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        update_status('failed', error=str(e))
        raise


# --- Routes ---

@doc_generator_bp.route('/')
@login_required
def index():
    """Main page with upload form and task list."""
    return render_template('doc_generator/index.html')


@doc_generator_bp.route('/api/analyze-template', methods=['POST'])
@login_required
def analyze_template():
    """Analyze template file and return structure."""
    from loguru import logger
    
    logger.info("[ANALYZE] Starting template analysis")
    
    if 'template' not in request.files:
        logger.warning("[ANALYZE] No template file provided")
        return jsonify({'error': 'No template file provided'}), 400
    
    template_file = request.files['template']
    logger.info(f"[ANALYZE] Template file: {template_file.filename}")
    
    if template_file.filename == '':
        logger.warning("[ANALYZE] Empty template filename")
        return jsonify({'error': 'Empty template filename'}), 400
    
    if not allowed_file(template_file.filename):
        logger.warning(f"[ANALYZE] Invalid file type: {template_file.filename}")
        return jsonify({'error': f'Invalid template file type. Allowed: {ALLOWED_EXTENSIONS}'}), 400
    
    # Save temp file for analysis
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(template_file.filename)[1]) as tmp:
        template_file.save(tmp.name)
        template_path = tmp.name
    
    logger.info(f"[ANALYZE] Saved temp file: {template_path}")
    
    # Extract page count for PDF
    page_count = 0
    if template_path.lower().endswith('.pdf'):
        import fitz
        doc = fitz.open(template_path)
        page_count = len(doc)
        doc.close()
        logger.info(f"[ANALYZE] PDF page count: {page_count}")
    
    try:
        # Get user prompt if provided
        user_prompt = request.form.get('prompt', '').strip()
        logger.info(f"[ANALYZE] User prompt length: {len(user_prompt)}")
        
        # Use LLM-based section analysis only
        from .reasoning.planner import analyze_sections_with_llm, build_planner_prompt
        
        logger.info("[ANALYZE] Using LLM for section analysis")
        llm_sections, chunk_info = analyze_sections_with_llm(template_path)
        logger.info(f"[ANALYZE] Template analysis: {chunk_info}")
        
        template_structure = {
            'title': os.path.basename(template_path),
            'source_file': template_path,
            'is_pdf': template_path.lower().endswith('.pdf'),
            'page_count': page_count,
            'sections': llm_sections if llm_sections else [],
            'tables': [],
            'analysis_method': 'llm'
        }
        
        if llm_sections:
            logger.info(f"[ANALYZE] LLM found {len(llm_sections)} sections")
        else:
            logger.warning("[ANALYZE] LLM returned no sections, returning empty structure")
        
        # Build prompt that would be sent to LLM for document generation
        mock_sources = {
            'example_source.pdf': {
                'text': 'Приклад тексту джерела для демонстрації структури промпту...',
                'char_count': 100
            }
        }
        
        prompt_preview = build_planner_prompt(
            template_structure=template_structure,
            sources_data=mock_sources,
            total_chars=100,
            user_prompt=user_prompt
        )
        
        logger.info("[ANALYZE] Analysis completed successfully")
        
        return jsonify({
            'template_structure': template_structure,
            'prompt_preview': prompt_preview,
            'user_prompt': user_prompt,
            'analysis_method': template_structure.get('analysis_method', 'unknown'),
            'chunk_info': chunk_info
        })
        
    except Exception as e:
        logger.error(f"[ANALYZE] Error during analysis: {e}", exc_info=True)
        return jsonify({'error': f'Analysis failed: {str(e)}'}), 500
        
    finally:
        # Clean up temp file
        if os.path.exists(template_path):
            os.unlink(template_path)


@doc_generator_bp.route('/api/tasks', methods=['POST'])
@login_required
def create_task():
    """Create new task - upload template and sources."""
    # Check file size
    content_length = request.content_length
    if content_length and content_length > MAX_FILE_SIZE:
        return jsonify({'error': f'Total files size exceeds {MAX_FILE_SIZE // 1024 // 1024}MB'}), 400
    
    if 'template' not in request.files:
        return jsonify({'error': 'No template file provided'}), 400
    
    template_file = request.files['template']
    source_files = request.files.getlist('sources')
    
    if template_file.filename == '':
        return jsonify({'error': 'Empty template filename'}), 400
    
    # Validate template extension
    if not allowed_file(template_file.filename):
        return jsonify({'error': f'Invalid template file type. Allowed: {ALLOWED_EXTENSIONS}'}), 400
    
    # Validate sources
    valid_sources = []
    for src_file in source_files:
        if src_file.filename:
            if not allowed_file(src_file.filename):
                return jsonify({'error': f'Invalid source file type: {src_file.filename}'}), 400
            valid_sources.append(src_file)
    
    if not valid_sources:
        return jsonify({'error': 'At least one valid source file required'}), 400
    
    # Generate task ID
    task_id = str(uuid.uuid4())[:8]
    
    # Create task directory
    data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
    task_dir = os.path.join(data_dir, 'tasks', task_id)
    template_dir = os.path.join(task_dir, 'template')
    sources_dir = os.path.join(task_dir, 'sources')
    
    os.makedirs(template_dir, exist_ok=True)
    os.makedirs(sources_dir, exist_ok=True)
    
    # Save template with sanitized filename (keep original for display)
    template_filename_orig = template_file.filename
    template_filename = sanitize_filename(template_file.filename)
    template_path = os.path.join(template_dir, template_filename)
    template_file.save(template_path)
    
    # Save sources with sanitized filenames (keep originals for display)
    sources_paths = []
    sources_filenames_orig = []
    for src_file in valid_sources:
        sources_filenames_orig.append(src_file.filename)
        src_filename = sanitize_filename(src_file.filename)
        src_path = os.path.join(sources_dir, src_filename)
        src_file.save(src_path)
        sources_paths.append(src_path)
    
    # Create initial task.json
    task_json_path = os.path.join(task_dir, 'task.json')
    # Get user prompt from form
    user_prompt = request.form.get('prompt', '').strip()
    if len(user_prompt) > 2000:
        return jsonify({'error': 'Prompt too long (max 2000 characters)'}), 400
    
    task_data = {
        'task_id': task_id,
        'status': 'queued',
        'created_at': datetime.utcnow().isoformat(),
        'user_email': session.get('email'),
        'template_file': template_filename_orig,
        'sources_count': len(sources_paths),
        'sources_files': sources_filenames_orig,
        'user_prompt': user_prompt if user_prompt else None,
    }
    with open(task_json_path, 'w') as f:
        json.dump(task_data, f, indent=2)
    
    # Queue Celery task
    data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
    process_document_task.delay(task_id, template_path, sources_paths, data_dir, user_prompt)
    
    return jsonify({'task_id': task_id, 'status': 'queued'}), 201


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
    
    if task.get('status') != 'completed':
        return jsonify({'error': 'Task not completed'}), 400
    
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
        save_analysis_result(folder_path, analysis_text)
        update_task_status(folder_path, 'analysis_completed')
        
        logger.info(f"[ANALYZE] Completed for task {task_id}")
        return {'status': 'success', 'task_id': task_id}
        
    except Exception as e:
        import traceback
        logger.error(f"[ANALYZE] Failed for task {task_id}: {e}")
        logger.error(f"[ANALYZE] Traceback: {traceback.format_exc()}")
        update_task_status(folder_path, 'failed', str(e))
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, max_retries=2)
def generate_final_document_task(self, task_id, folder_path, generation_prompt, source_files):
    """Celery task for final document generation using one-pass LLM approach."""
    from loguru import logger
    
    logger.info(f"[GENERATE] Starting generation for task {task_id}")
    
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
        
        # Build document directly from analysis structure (like test_table_generator)
        from .reasoning.document_builder import build_document_from_analysis
        
        output_dir = os.path.join(folder_path, 'temp')
        os.makedirs(output_dir, exist_ok=True)
        
        temp_output = build_document_from_analysis(
            analysis_text=analysis_text,
            sources_data=sources_data,
            task_id=task_id,
            task_folder=folder_path
        )
        
        # Save final document with proper name
        save_final_document(folder_path, temp_output)
        
        # Cleanup temp
        shutil.rmtree(output_dir, ignore_errors=True)
        
        update_task_status(folder_path, 'completed')
        
        logger.info(f"[GENERATE] Completed for task {task_id}")
        return {'status': 'success', 'task_id': task_id}
        
    except Exception as e:
        logger.error(f"[GENERATE] Failed for task {task_id}: {e}")
        update_task_status(folder_path, 'failed', str(e))
        raise self.retry(exc=e, countdown=60)
