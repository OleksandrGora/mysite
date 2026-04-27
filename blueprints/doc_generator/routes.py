import os
import json
import uuid
import time
from datetime import datetime
from flask import request, jsonify, render_template, current_app, send_file, session, redirect, url_for
from werkzeug.utils import secure_filename
import re
from celery import shared_task
from functools import wraps
from . import doc_generator_bp
from .parsers.template_parser import parse_template
from .parsers.source_parser import parse_sources_with_metadata
from .reasoning.planner import create_document_plan, save_plan
from .reasoning.generator import generate_all_content
from .builders.docx_builder import build_document, build_document_simple
from .rag.embedder import embed_all_sources, should_use_rag
from .rag.retriever import retrieve_for_section, format_chunks_for_llm
from .config import USE_RAG
from .cleanup import cleanup_old_tasks, cleanup_chroma_collections, get_task_stats, get_disk_usage

# --- Authentication Decorator ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- File Validation ---

ALLOWED_EXTENSIONS = {'.docx', '.pdf', '.DOCX', '.PDF'}
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
    """
    from loguru import logger
    
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
                
                first_prompt = f"""Проаналізуй структуру шаблону та джерел.

Шаблон має {len(template_structure.get('sections', []))} секцій та {len(template_structure.get('tables', []))} таблиць.

Користувацький запит: {user_prompt if user_prompt else 'Створити документ відповідно до джерел'}

Поверни детальний план заповнення кожної секції та таблиці даними з джерел.
"""
                
                response1 = llm_call_with_retry(first_prompt, "LLM Analysis")
                first_llm_response = response1.text
                logger.info(f"[{task_id}] LLM analysis: {len(first_llm_response)} chars")
                
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


@doc_generator_bp.route('/api/tasks/<task_id>', methods=['GET'])
@login_required
def get_task_status(task_id):
    """Get task status by ID."""
    data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
    task_json_path = os.path.join(data_dir, 'tasks', task_id, 'task.json')
    
    if not os.path.exists(task_json_path):
        return jsonify({'error': 'Task not found'}), 404
    
    with open(task_json_path, 'r') as f:
        task_data = json.load(f)
    
    return jsonify(task_data)


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


@doc_generator_bp.route('/api/tasks/<task_id>/download', methods=['GET'])
@login_required
def download_result(task_id):
    """Download completed document."""
    data_dir = current_app.config.get('DATA_DIR', '/home/openclaw/mysite/data')
    task_json_path = os.path.join(data_dir, 'tasks', task_id, 'task.json')
    
    if not os.path.exists(task_json_path):
        return jsonify({'error': 'Task not found'}), 404
    
    with open(task_json_path, 'r') as f:
        task_data = json.load(f)
    
    if task_data.get('status') != 'completed':
        return jsonify({'error': 'Task not completed'}), 400
    
    output_file = task_data.get('output_file')
    if not output_file or not os.path.exists(output_file):
        return jsonify({'error': 'Output file not found'}), 404
    
    # Use the actual filename for download
    download_name = os.path.basename(output_file)
    return send_file(output_file, as_attachment=True, download_name=download_name)
