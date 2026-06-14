import os
import json
import uuid
import tempfile
import shutil
import threading
from datetime import datetime
from flask import request, jsonify, render_template, current_app, send_file, session, redirect, url_for, Response, stream_with_context
from loguru import logger
from common_config import login_required
from . import pptx_generator_bp
from .create_pptx import create_presentation

# ---------------------------------------------------------------------------
# Налаштування генерації зображень OpenAI
# ---------------------------------------------------------------------------

# gpt-image-1-mini   — швидка, дешева (за замовчуванням)
# gpt-image-1        — якісніша, повільніша, дорожча
# gpt-image-1.5      — найновіша, краще слідує інструкціям
OPENAI_IMAGE_MODEL = "gpt-image-1-mini"

# "low" / "medium" / "high"
OPENAI_IMAGE_QUALITY = "medium"

# "1024x1024" / "1536x1024" / "1024x1536"
OPENAI_IMAGE_SIZE = "1024x1024"

OPENAI_IMAGE_MAX_WORKERS = 5

# ---------------------------------------------------------------------------
# Налаштування генерації тексту OpenAI
# ---------------------------------------------------------------------------

# gpt-4o-mini  — швидка, дешева
# gpt-4o       — якісніша
OPENAI_TEXT_MODEL = "gpt-5-nano"

OPENAI_TEXT_TEMPERATURE = 0.3

# ---------------------------------------------------------------------------
# Налаштування генерації зображень Gemini
# ---------------------------------------------------------------------------

# "gemini-3.1-flash-image-preview"              — найновіша, thinking mode
# "gemini-2.5-flash-image-preview"              — швидша, без thinking
# "gemini-2.0-flash-preview-image-generation"   — стара стабільна
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image-preview"

# Thinking budget (тільки для моделей з thinking)
# 0 — вимкнено, -1 — авто, 1024 — обмежений
GEMINI_IMAGE_THINKING_BUDGET = 0

GEMINI_IMAGE_TEMPERATURE = 0.7

GEMINI_IMAGE_MAX_WORKERS = 3

# ---------------------------------------------------------------------------
# Налаштування генерації тексту Gemini
# ---------------------------------------------------------------------------

# "gemini-2.5-pro"    — найякісніша
# "gemini-2.5-flash"  — швидша
# "gemini-2.0-flash"  — стабільна, без thinking
GEMINI_TEXT_MODEL = "gemini-2.5-flash-lite"

# Thinking budget для текстової моделі
# 0 — вимкнено, -1 — авто, 1024+ — обмежений
GEMINI_TEXT_THINKING_BUDGET = 0

GEMINI_TEXT_TEMPERATURE = 0.3

# ---------------------------------------------------------------------------
# Session management — серверне збереження стану pptx-генератора
# ---------------------------------------------------------------------------

PPTX_SESSION_ROOT = os.path.join(
    os.getenv('DATA_DIR', '/home/openclaw/mysite/data'),
    'pptx_sessions'
)


def _get_session_dir():
    """Get or create persistent session directory for current user."""
    if 'pptx_session_id' not in session:
        session['pptx_session_id'] = uuid.uuid4().hex
    sdir = os.path.join(PPTX_SESSION_ROOT, session['pptx_session_id'])
    for sub in ('designs', 'images', 'presentations'):
        os.makedirs(os.path.join(sdir, sub), exist_ok=True)
    return sdir


def _load_session_state():
    """Load session state from disk."""
    sdir = _get_session_dir()
    state_file = os.path.join(sdir, 'state.json')
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            return json.load(f)
    return {
        'has_designs': False,
        'has_images': False,
        'slide_count': 0,
        'designs': [],
        'has_input': False,
        'selected_designs': [],
        'total_tokens': 0,
        'raw_input': '',
    }


def _save_session_state(state):
    """Save session state to disk."""
    state_file = os.path.join(_get_session_dir(), 'state.json')
    with open(state_file, 'w') as f:
        json.dump(state, f)


# --- Routes ---

def extract_text_from_docx(file_path):
    """
    Extract text from DOCX file.
    
    Args:
        file_path: path to DOCX file
    
    Returns:
        extracted text as string
    """
    try:
        from docx import Document
        doc = Document(file_path)
        
        text_parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text.strip())
        
        return '\n\n'.join(text_parts)
    except Exception as e:
        logger.error(f"Failed to extract text from DOCX: {e}")
        return None


def _sse_event(event_type, data):
    """Format a Server-Sent Event message."""
    payload = json.dumps({'type': event_type, **data})
    return f"data: {payload}\n\n"


# ---------------------------------------------------------------------------
# Валідація та автокорекція зон дизайну
# ---------------------------------------------------------------------------

def _rects_overlap(a, b):
    return not (
        a['x'] + a['w'] <= b['x'] or
        b['x'] + b['w'] <= a['x'] or
        a['y'] + a['h'] <= b['y'] or
        b['y'] + b['h'] <= a['y']
    )


def _resolve_overlap(fixed, moving):
    gap_y = (fixed['y'] + fixed['h']) - moving['y']
    gap_x = (fixed['x'] + fixed['w']) - moving['x']
    new = dict(moving)
    if abs(gap_y) <= abs(gap_x):
        new['y'] = min(fixed['y'] + fixed['h'] + 0.01, 1.0 - moving['h'])
    else:
        new['x'] = min(fixed['x'] + fixed['w'] + 0.01, 1.0 - moving['w'])
    new['x'] = max(0.0, min(new['x'], 1.0 - new['w']))
    new['y'] = max(0.0, min(new['y'], 1.0 - new['h']))
    return new


def _fix_image_aspect_ratio(design, slide_ratio=16.0/9.0):
    if 'image' not in design or 'position' not in design['image']:
        return
    pos = design['image']['position']
    w = pos.get('w', 0)
    h = pos.get('h', 0)
    if w <= 0 or h <= 0:
        return

    current_aspect = (h / w) / slide_ratio
    target_aspect = 1.1

    if abs(current_aspect - target_aspect) < 0.15:
        return

    new_h = target_aspect * w * slide_ratio
    if pos['y'] + new_h <= 1.0:
        pos['h'] = new_h
        return

    new_w = h / (target_aspect * slide_ratio)
    if pos['x'] + new_w <= 1.0:
        pos['w'] = new_w


def _validate_and_fix_zones(design):
    zones = ['title', 'body', 'image']
    conflicts_log = []
    was_fixed = False

    orig_pos = {}
    for key in ['image']:
        if key in design and 'position' in design[key]:
            orig_pos[key] = dict(design[key]['position'])

    _fix_image_aspect_ratio(design)

    for key, orig in orig_pos.items():
        if key in design and 'position' in design[key]:
            cur = design[key]['position']
            if cur['w'] != orig['w'] or cur['h'] != orig['h']:
                was_fixed = True
                conflicts_log.append(f"{key} aspect ratio adjusted ({orig['w']:.3f}x{orig['h']:.3f} → {cur['w']:.3f}x{cur['h']:.3f})")

    for i in range(len(zones)):
        for j in range(i + 1, len(zones)):
            a_key, b_key = zones[i], zones[j]
            a = design[a_key]['position']
            b = design[b_key]['position']
            if _rects_overlap(a, b):
                conflicts_log.append(f"{a_key} ↔ {b_key}")
                design[b_key]['position'] = _resolve_overlap(a, b)
                was_fixed = True

    return design, was_fixed, conflicts_log


@pptx_generator_bp.route('/')
@login_required
def index():
    """Main page with JSON input form."""
    return render_template('pptx_generator/index-pptx-generator.html')


@pptx_generator_bp.route('/api/generate', methods=['POST'])
@login_required
def generate_pptx():
    """
    Generate PowerPoint presentation from JSON.
    Streams progress via Server-Sent Events.
    
    Expected JSON format:
    [
        {
            "slide_number": 1,
            "title": "Назва слайду",
            "body": "Основний текст слайду",
            "image_description": "Опис ілюстрації для генерації (опціонально)"
        }
    ]
    
    Query parameters:
        provider: 'google' (default) or 'openai' - image generation provider
        generate_images: 'true' (default) or 'false'
    """
    raw_data = request.get_data(as_text=True)
    image_provider = request.args.get('provider', 'google').lower()
    if image_provider not in ['google', 'openai']:
        image_provider = 'google'
    generate_images_param = request.args.get('generate_images', 'true').lower()
    generate_images = generate_images_param in ['true', '1', 'yes']

    # 1. Парсить JSON (або викликає fix_json_with_llm() при помилці)
    # Валідує структуру слайдів
    # Генерує зображення паралельно через ThreadPoolExecutor
    # Створює тимчасову директорію
    # Викликає create_presentation() з create_pptx.py
    # Повертає файл у форматі base64 через SSE події
    def generate():
        try:
            if not raw_data:
                yield _sse_event('error', {'message': 'No data provided'})
                return

            yield _sse_event('status', {'step': 'parse', 'message': 'Парсинг JSON...'})

            total_tokens = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
            design_config = None

            # Parse JSON
            try:
                parsed = json.loads(raw_data)
            except json.JSONDecodeError:
                yield _sse_event('status', {'step': 'llm_fix', 'message': 'JSON некоректний, виправляю через LLM...'})
                parsed, tokens = fix_json_with_llm(raw_data, provider=image_provider)
                if tokens:
                    total_tokens['input_tokens'] += tokens.get('input_tokens', 0)
                    total_tokens['output_tokens'] += tokens.get('output_tokens', 0)
                    total_tokens['total_tokens'] += tokens.get('total_tokens', 0)
                if parsed is None:
                    yield _sse_event('error', {'message': 'Invalid JSON and LLM fix failed.'})
                    return

            # Підтримка нового формату: {"slides": [...], "design_config": {...}}
            # та старого формату: [slide, slide, ...]
            if isinstance(parsed, dict) and 'slides' in parsed:
                data = parsed['slides']
                design_config = parsed.get('design_config')
            elif isinstance(parsed, list):
                data = parsed
            else:
                yield _sse_event('error', {'message': 'Data must be an array of slide objects or {slides: [...], design_config: ...}'})
                return

            # Validate slides
            for i, slide in enumerate(data):
                if not isinstance(slide, dict):
                    yield _sse_event('error', {'message': f'Slide {i} must be an object'})
                    return
                if 'title' not in slide:
                    yield _sse_event('error', {'message': f'Slide {i} missing required field: title'})
                    return
                if 'body' not in slide:
                    yield _sse_event('error', {'message': f'Slide {i} missing required field: body'})
                    return

            total_slides = len(data)
            yield _sse_event('status', {
                'step': 'validated',
                'message': f'Валідація пройдена. Слайдів: {total_slides}',
                'slide_count': total_slides
            })

            # Create temp directory
            temp_dir = tempfile.mkdtemp(prefix='pptx_gen_')
            images_dir = os.path.join(temp_dir, 'images')
            os.makedirs(images_dir, exist_ok=True)

            # Generate images (parallel or skip)
            slides_with_images = [slide.copy() for slide in data]

            if generate_images:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                import threading

                # Збираємо слайди з image_description
                image_tasks = {
                    i: slide.get('image_description', '')
                    for i, slide in enumerate(data)
                    if slide.get('image_description', '')
                }

                if image_tasks:
                    yield _sse_event('status', {
                        'step': 'images_start',
                        'message': f'Генерую {len(image_tasks)} зображень паралельно...',
                        'slide_total': total_slides
                    })

                    # Лічильник завершених для SSE — thread-safe
                    completed_lock = threading.Lock()
                    completed_count = [0]
                    sse_queue = []  # збираємо події поки futures виконуються

                    def _generate_one(idx, desc):
                        if image_provider == 'openai':
                            return idx, *generate_image_with_openai(desc, images_dir, idx)
                        else:
                            return idx, *generate_image_with_google(desc, images_dir, idx)

                    max_workers = OPENAI_IMAGE_MAX_WORKERS if image_provider == 'openai' else GEMINI_IMAGE_MAX_WORKERS
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = {
                            executor.submit(_generate_one, i, desc): i
                            for i, desc in image_tasks.items()
                        }
                        for future in as_completed(futures):
                            try:
                                idx, image_path, image_tokens = future.result()
                            except Exception as e:
                                idx = futures[future]
                                logger.warning(f"Image generation failed for slide {idx}: {e}")
                                yield _sse_event('warning', {
                                    'message': f'Помилка зображення для слайду {idx + 1}: {e}'
                                })
                                continue

                            with completed_lock:
                                completed_count[0] += 1
                                done = completed_count[0]

                            if image_path:
                                slides_with_images[idx]['image_path'] = image_path
                                if image_tokens:
                                    total_tokens['input_tokens'] += image_tokens.get('input_tokens', 0)
                                    total_tokens['output_tokens'] += image_tokens.get('output_tokens', 0)
                                    total_tokens['total_tokens'] += image_tokens.get('total_tokens', 0)
                                yield _sse_event('status', {
                                    'step': 'image_done',
                                    'message': f'Зображення {done}/{len(image_tasks)} готово (слайд {idx + 1})',
                                    'slide_index': idx,
                                    'slide_total': total_slides
                                })
                            else:
                                yield _sse_event('warning', {
                                    'message': f'Зображення для слайду {idx + 1} не згенеровано, буде заглушка'
                                })
            else:
                # Без генерації — тільки статус про заглушки
                for i, slide in enumerate(data):
                    if slide.get('image_description', ''):
                        yield _sse_event('status', {
                            'step': 'image_placeholder',
                            'message': f'Слайд {i + 1}/{total_slides}: зарезервовано місце під зображення',
                            'slide_index': i
                        })

            # Build PPTX
            yield _sse_event('status', {'step': 'building', 'message': 'Створюю презентацію...'})

            output_filename = f"presentation_{uuid.uuid4().hex[:8]}.pptx"
            output_path = os.path.join(temp_dir, output_filename)

            create_presentation(output_path, slides_with_images, images_dir, design_config=design_config)

            logger.info(f"Generated presentation: {output_path}")

            # Read file and base64-encode for delivery via SSE
            import base64
            with open(output_path, 'rb') as f:
                file_bytes = f.read()
            file_b64 = base64.b64encode(file_bytes).decode('utf-8')

            # Cleanup
            shutil.rmtree(temp_dir, ignore_errors=True)

            yield _sse_event('done', {
                'message': 'Презентацію створено',
                'filename': output_filename,
                'file_b64': file_b64,
                'tokens': total_tokens
            })

        except Exception as e:
            logger.error(f"Error generating PPTX: {e}", exc_info=True)
            yield _sse_event('error', {'message': f'Generation failed: {str(e)}'})

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',  # Вимикає буферизацію nginx
        }
    )


# ---------------------------------------------------------------------------
# 1.5 Дизайн: пропозиція дизайну через LLM
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Генерація випадкових дизайнів (як у традиційному workflow)
# ---------------------------------------------------------------------------


def _generate_random_design(design_index):
    """Generate a random design config matching traditional workflow styles."""
    import random

    color_map = {
        "light_pastels": [
            ("#EBF5FF", "#E1F0FA"),
            ("#EBFFF0", "#E1FAEB"),
            ("#FAF0FF", "#F5EBFA"),
            ("#FFF8EB", "#FAF5E6"),
            ("#FFF0F5", "#FAEBF0"),
            ("#F0FCFF", "#EBFAFC"),
            ("#F5FFF5", "#F0FCF0"),
            ("#FFFAF5", "#FCF5F0"),
            ("#FFF8FC", "#FCF5FA"),
            ("#F8FCFF", "#F5FAFC"),
        ],
        "title": ["#28283C", "#323232", "#3C283C", "#503228", "#283C32"],
    }

    bg_pair = random.choice(color_map["light_pastels"])
    bg_color, _ = bg_pair
    title_color_hex = random.choice(color_map["title"])

    design_types = [
        "rectangle", "circle", "sidebar", "header_bar",
        "diagonal_split", "triangle_accents", "thin_frame"
    ]
    design_type = design_types[design_index % len(design_types)]

    name_map = {
        "rectangle": "Мінімалістичний",
        "circle": "Акцентний",
        "sidebar": "Бокова панель",
        "header_bar": "Класичний",
        "diagonal_split": "Динамічний",
        "triangle_accents": "Технічний",
        "thin_frame": "Рамковий",
    }
    shape_color_map = {
        "rectangle": ["#3498DB", "#2ECC71", "#E74C3C", "#9B59B6", "#1ABC9C"],
        "circle": ["#E74C3C", "#F39C12", "#1ABC9C", "#3498DB", "#9B59B6"],
        "sidebar": ["#2C3E50", "#34495E", "#1A252F", "#2C3E50", "#16A085"],
        "header_bar": ["#1B4F72", "#2C3E50", "#8E44AD", "#16A085", "#C0392B"],
        "triangle_accents": ["#238636", "#1F6FEB", "#9B59B6", "#E67E22", "#E74C3C"],
        "thin_frame": ["#3498DB", "#2ECC71", "#E74C3C", "#9B59B6", "#95A5A6"],
    }
    if design_type == "diagonal_split":
        shape_colors = ["#9B59B6", "#3498DB", "#E74C3C", "#1ABC9C", "#F39C12"]
    else:
        shape_colors = shape_color_map.get(design_type, ["#3498DB"])

    shape_color = random.choice(shape_colors)
    title_align = random.choice(["left", "center"])

    body_color = "#34495E" if not bg_color == "#FFFFFF" else "#2C3E50"

    if design_type == "rectangle":
        rect_layout = random.choice(["full", "left_half", "right_half"])
        shapes = []
        if rect_layout == "full":
            shapes.append({"type": "rectangle", "position": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}, "color": shape_color, "z_order": 0})
        elif rect_layout == "left_half":
            shapes.append({"type": "rectangle", "position": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}, "color": shape_color, "z_order": 0})
        elif rect_layout == "right_half":
            shapes.append({"type": "rectangle", "position": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}, "color": shape_color, "z_order": 0})
        title_pos = {"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.15}
        body_pos = {"x": 0.05, "y": 0.25, "w": 0.6, "h": 0.65}
        img_pos = {"x": 0.65, "y": 0.2, "w": 0.3, "h": 0.6}

    elif design_type == "circle":
        corner = random.choice(["corner_top_left", "corner_top_right", "corner_bottom_left", "corner_bottom_right"])
        circle_map = {
            "corner_top_left": (0.0, 0.0),
            "corner_top_right": (0.7, 0.0),
            "corner_bottom_left": (0.0, 0.6),
            "corner_bottom_right": (0.7, 0.6),
        }
        pos = circle_map[corner]
        shapes = [
            {"type": "oval", "position": {"x": pos[0], "y": pos[1], "w": 0.3, "h": 0.4}, "color": shape_color, "z_order": 0},
        ]
        title_pos = {"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.15}
        body_pos = {"x": 0.05, "y": 0.25, "w": 0.55, "h": 0.65}
        img_pos = {"x": 0.65, "y": 0.25, "w": 0.3, "h": 0.6}

    elif design_type == "sidebar":
        side = random.choice(["sidebar_left", "sidebar_right"])
        if side == "sidebar_left":
            shapes = [{"type": "rectangle", "position": {"x": 0.0, "y": 0.0, "w": 0.18, "h": 1.0}, "color": shape_color, "z_order": 0}]
            title_pos = {"x": 0.22, "y": 0.05, "w": 0.75, "h": 0.15}
            body_pos = {"x": 0.22, "y": 0.25, "w": 0.5, "h": 0.65}
            img_pos = {"x": 0.75, "y": 0.25, "w": 0.22, "h": 0.6}
        else:
            shapes = [{"type": "rectangle", "position": {"x": 0.82, "y": 0.0, "w": 0.18, "h": 1.0}, "color": shape_color, "z_order": 0}]
            title_pos = {"x": 0.03, "y": 0.05, "w": 0.75, "h": 0.15}
            body_pos = {"x": 0.03, "y": 0.25, "w": 0.5, "h": 0.65}
            img_pos = {"x": 0.55, "y": 0.25, "w": 0.25, "h": 0.6}

    elif design_type == "header_bar":
        bar_pos = random.choice(["header_bar", "footer_bar"])
        if bar_pos == "header_bar":
            shapes = [{"type": "rectangle", "position": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.08}, "color": shape_color, "z_order": 0}]
            title_pos = {"x": 0.05, "y": 0.12, "w": 0.9, "h": 0.13}
        else:
            shapes = [{"type": "rectangle", "position": {"x": 0.0, "y": 0.92, "w": 1.0, "h": 0.08}, "color": shape_color, "z_order": 0}]
            title_pos = {"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.15}
        body_pos = {"x": 0.05, "y": 0.3, "w": 0.6, "h": 0.6}
        img_pos = {"x": 0.68, "y": 0.3, "w": 0.28, "h": 0.55}

    elif design_type == "diagonal_split":
        shapes = [
            {"type": "right_triangle", "position": {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}, "color": shape_color, "z_order": 0}
        ]
        title_pos = {"x": 0.05, "y": 0.05, "w": 0.5, "h": 0.15}
        body_pos = {"x": 0.05, "y": 0.25, "w": 0.45, "h": 0.65}
        img_pos = {"x": 0.55, "y": 0.2, "w": 0.4, "h": 0.6}

    elif design_type == "triangle_accents":
        shapes = [
            {"type": "right_triangle", "position": {"x": 0.0, "y": 0.0, "w": 0.15, "h": 0.15}, "color": shape_color, "z_order": 0},
            {"type": "right_triangle", "position": {"x": 0.85, "y": 0.85, "w": 0.15, "h": 0.15}, "color": shape_color, "z_order": 0},
        ]
        title_pos = {"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.12}
        body_pos = {"x": 0.05, "y": 0.22, "w": 0.55, "h": 0.7}
        img_pos = {"x": 0.63, "y": 0.22, "w": 0.32, "h": 0.6}

    elif design_type == "thin_frame":
        shapes = [
            {"type": "rectangle", "position": {"x": 0.02, "y": 0.02, "w": 0.96, "h": 0.005}, "color": shape_color, "z_order": 0},
            {"type": "rectangle", "position": {"x": 0.02, "y": 0.975, "w": 0.96, "h": 0.005}, "color": shape_color, "z_order": 0},
            {"type": "rectangle", "position": {"x": 0.02, "y": 0.02, "w": 0.005, "h": 0.96}, "color": shape_color, "z_order": 0},
            {"type": "rectangle", "position": {"x": 0.975, "y": 0.02, "w": 0.005, "h": 0.96}, "color": shape_color, "z_order": 0},
        ]
        title_pos = {"x": 0.08, "y": 0.08, "w": 0.84, "h": 0.12}
        body_pos = {"x": 0.08, "y": 0.25, "w": 0.84, "h": 0.65}
        img_pos = {"x": 0.08, "y": 0.25, "w": 0.84, "h": 0.65}

    font_family = random.choice(["Calibri", "Arial", "Segoe UI", "Verdana", "Tahoma", "Candara", "Century Gothic", "Franklin Gothic Medium"])
    # Choose font_size more carefully to avoid overlap
    title_font_size = random.choice([28, 30, 32, 34, 36])
    body_font_size = random.choice([16, 18, 20])

    return {
        "id": f"random_{design_index + 1}",
        "name": f"{name_map.get(design_type, 'Дизайн')} #{design_index + 1}",
        "global": {
            "background_color": bg_color,
            "invert": False,
            "font_family": font_family,
            "mirror_even_slides": random.choice([True, False]),
        },
        "title": {
            "font_size": title_font_size,
            "font_bold": True,
            "font_color": title_color_hex,
            "alignment": title_align,
            "position": title_pos,
        },
        "body": {
            "font_size": body_font_size,
            "font_bold": False,
            "font_color": body_color,
            "alignment": "left",
            "position": body_pos,
        },
        "image": {"position": img_pos},
        "shapes": shapes,
        "table_style": None,
        "chart_style": None,
    }


@pptx_generator_bp.route('/api/random-designs', methods=['GET'])
@login_required
def random_designs():
    """Return 3 randomly generated designs using traditional workflow logic."""
    designs = [_generate_random_design(i) for i in range(3)]
    for d in designs:
        d, was_fixed, conflicts = _validate_and_fix_zones(d)
        if was_fixed:
            logger.warning(f"Random design '{d.get('id', '?')}': auto-fixed overlaps: {conflicts}")
    return jsonify({
        'designs': designs,
        'tokens': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
    })

DESIGN_SYSTEM_PROMPT = """Ти — креативний дизайнер PowerPoint презентацій. Генеруєш параметри дизайну у форматі JSON.
Повертай ТІЛЬКИ валідний JSON масив з 3 об'єктами. Без пояснень, без markdown, без ```json."""


DESIGN_USER_PROMPT = """Проаналізуй тему та зміст слайдів і запропонуй 3 різних дизайни.

СЛАЙДИ:
{slides_summary}

ВИМОГИ ДО 3 ВАРІАНТІВ:
- Варіант 1: світла схема (invert: false), стримана
- Варіант 2: темна схема (invert: true), контрастна
- Варіант 3: твій вибір — підходить до теми, можеш бути сміливим

Кожен варіант має СУТТЄВО відрізнятися від інших:
різний фон, різні позиції фігур, різна типографіка, різне розміщення тексту і зображення.

ЗАОХОЧУЄТЬСЯ (використовуй як натхнення, не копіюй):
- заголовок знизу слайду, великий шрифт — тіло і зображення займають верхню частину
- вузька вертикальна смуга тексту зліва (w: 0.30), велике зображення справа
- темний трикутник або овал у куті займає третину слайду, текст накладається поверх
- зображення зверху (y: 0.05), заголовок і текст знизу (y > 0.55)
- два тонких прямокутники як рамка (зверху і знизу) замість суцільного фону
- заголовок по центру (alignment: center), великий font_size (38-44), тіло дрібніше (16-18)
- фігура-акцент займає всю ліву половину (w: 0.45), текст і зображення справа

ОБМЕЖЕННЯ:
- Координати position: x, y, w, h — від 0.0 до 1.0 (частка від розміру слайду)
- Слайд 16:9, тому h=1.0 при w=0.5 — це висока вертикальна смуга, враховуй пропорції
- Зони title, body, image НЕ перекриваються між собою — перевір координати
- Фігури (shapes) мають z_order=0..1, текстові зони завжди поверх фігур
- font_family — тільки беззасічкові: Calibri, Arial, Segoe UI, Verdana, Tahoma, Candara, Century Gothic, Franklin Gothic Medium
- font_size для title: 24-44, для body: 14-26
- shapes: від 1 до 4 фігур (rectangle, oval, right_triangle)
- Якщо є слайди з content_type "table" або "chart" — додай table_style / chart_style

ФОРМАТ ВІДПОВІДІ (JSON масив з 3 об'єктів):
[
  {{
    "id": "...",
    "name": "...",
    "global": {{ "background_color": "...", "invert": true/false, "font_family": "...", "mirror_even_slides": true/false }},
    "title": {{ "font_size": N, "font_bold": true/false, "font_color": "...", "alignment": "left/center/right", "position": {{"x":N,"y":N,"w":N,"h":N}} }},
    "body": {{ "font_size": N, "font_bold": false, "font_color": "...", "alignment": "left/center/right", "position": {{"x":N,"y":N,"w":N,"h":N}} }},
    "image": {{ "position": {{"x":N,"y":N,"w":N,"h":N}} }},
    "shapes": [ {{ "type": "...", "position": {{"x":N,"y":N,"w":N,"h":N}}, "color": "..." or null, "gradient": {{"angle":N,"color_start":"...","color_end":"..."}} or null, "z_order": N }} ],
    "table_style": {{ ... }} or null,
    "chart_style": {{ ... }} or null
  }}
]
"""


@pptx_generator_bp.route('/api/suggest-design', methods=['POST'])
@login_required
def suggest_design():
    """Generate 3 design suggestions based on slide content."""
    try:
        body = request.get_json(force=True)
        if not body:
            return jsonify({'error': 'No data provided'}), 400

        slides = body.get('slides', [])
        provider = body.get('provider', 'google').lower()
        if provider not in ['google', 'openai']:
            provider = 'google'

        if not slides:
            return jsonify({'error': 'No slides provided'}), 400

        slides_summary_lines = []
        for i, slide in enumerate(slides):
            title = slide.get('title', '')
            body_text = slide.get('body', '')[:200]
            content_type = slide.get('content_type', 'text')
            ct_label = f"[{content_type}]" if content_type != 'text' else ""
            slides_summary_lines.append(f"Слайд {i + 1}: {title} {ct_label}\n{body_text}")

        slides_summary = '\n\n'.join(slides_summary_lines)
        user_prompt = DESIGN_USER_PROMPT.format(slides_summary=slides_summary)

        designs = None
        tokens_used = None

        for attempt in range(2):
            try:
                if provider == 'openai':
                    api_key = os.getenv('OPENAI_API_KEY')
                    if not api_key:
                        return jsonify({'error': 'OPENAI_API_KEY not set'}), 500

                    from openai import OpenAI
                    client = OpenAI(api_key=api_key)

                    response = client.chat.completions.create(
                        model=OPENAI_TEXT_MODEL,
                        messages=[
                            {"role": "system", "content": DESIGN_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt}
                        ]
                    )

                    result_text = response.choices[0].message.content.strip()

                    try:
                        if response.usage:
                            tokens_used = {
                                'input_tokens': response.usage.prompt_tokens,
                                'output_tokens': response.usage.completion_tokens,
                                'total_tokens': response.usage.total_tokens
                            }
                    except Exception:
                        pass

                else:
                    api_key = os.getenv('GEMINI_API_KEY')
                    if not api_key:
                        return jsonify({'error': 'GEMINI_API_KEY not set'}), 500

                    from google import genai
                    from google.genai import types
                    client = genai.Client(api_key=api_key)

                    full_prompt = f"{DESIGN_SYSTEM_PROMPT}\n\n{user_prompt}"
                    response = client.models.generate_content(
                        model=GEMINI_TEXT_MODEL,
                        contents=full_prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.4,
                        )
                    )

                    result_text = response.text.strip()

                    try:
                        if hasattr(response, 'usage_metadata') and response.usage_metadata:
                            tokens_used = {
                                'input_tokens': getattr(response.usage_metadata, 'prompt_token_count', 0),
                                'output_tokens': getattr(response.usage_metadata, 'candidates_token_count', 0),
                                'total_tokens': getattr(response.usage_metadata, 'total_token_count', 0)
                            }
                    except Exception:
                        pass

                start = result_text.find('[')
                end = result_text.rfind(']')
                if start != -1 and end != -1 and end > start:
                    result_text = result_text[start:end + 1]
                if '```json' in result_text:
                    result_text = result_text.split('```json', 1)[1]
                if '```' in result_text:
                    result_text = result_text.rsplit('```', 1)[0]
                result_text = result_text.strip()

                designs = json.loads(result_text)
                if isinstance(designs, list) and len(designs) == 3:
                    break
                else:
                    designs = None
                    logger.warning(f"LLM suggested design: expected array of 3, got {type(designs)}")
            except Exception as e:
                logger.warning(f"Suggest design attempt {attempt + 1} failed: {e}")
                designs = None

        if not designs:
            return jsonify({'error': 'Failed to generate design suggestions', 'designs': []}), 200

        fixed_designs = []
        for design in designs:
            design, was_fixed, conflicts = _validate_and_fix_zones(design)
            if was_fixed:
                logger.warning(f"Design '{design.get('id', '?')}': auto-fixed overlaps: {conflicts}")
            fixed_designs.append(design)

        return jsonify({
            'designs': fixed_designs,
            'tokens': tokens_used or {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
        })

    except Exception as e:
        logger.error(f"Suggest design error: {e}", exc_info=True)
        return jsonify({'error': str(e), 'designs': []}), 200


# 2. Повертає розпарсений JSON масив або None
def fix_json_with_llm(raw_text, provider='google'):
    """
    Use LLM to fix and format JSON structure for slides.

    Args:
        raw_text: raw text input that may not be valid JSON
        provider: 'google' or 'openai'

    Returns:
        tuple: (parsed JSON list or None, tokens_used dict or None)
    """
    prompt = f"""Ти — експерт з форматування даних для PowerPoint презентацій.

Перетвори наступний текст на валідний JSON масив об'єктів для слайдів.

СТРУКТУРА КОЖНОГО СЛАЙДА:
- title: назва слайду (обов'язково)
- body: основний текст слайду (обов'язково)
- image_description: опис ілюстрації для генерації (опціонально)
- content_type: тип вмісту (опціонально):
  * "text" — звичайний текст (за замовчуванням)
  * "table" — body містить табличні дані у вигляді markdown-таблиці або CSV
  * "chart" — body містить числові дані для діаграми (формат: "Категорія: значення")

ВИМОГИ:
- Якщо у вхідному тексті є табличні дані (стовпці, ряди чисел) — створи слайд з content_type: "table", body у форматі markdown-таблиці
- Якщо у вхідному тексті є числові дані для порівняння (категорія → значення) — створи слайд з content_type: "chart", body у форматі "Категорія: значення"
- Поверни ТІЛЬКИ JSON масив, без додаткових пояснень
- Якщо текст не має чіткої структури слайдів — розділи його логічно на 3-5 слайдів
- Якщо є заголовки/підрозділи — використай їх як title
- Якщо текст короткий — створи 1 слайд
- Українська мова

ВХІДНИЙ ТЕКСТ:
{raw_text}

Поверни JSON:"""

    try:
        from dotenv import load_dotenv
        load_dotenv()

        if provider == 'openai':
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                logger.warning("OPENAI_API_KEY not set, cannot fix JSON")
                return None, None

            logger.info(f"[PPTX] Виклик LLM (OpenAI/{OPENAI_TEXT_MODEL}) для виправлення JSON")

            from openai import OpenAI
            client = OpenAI(api_key=api_key)

            response = client.chat.completions.create(
                model=OPENAI_TEXT_MODEL,
                # temperature=OPENAI_TEXT_TEMPERATURE,
                messages=[{"role": "user", "content": prompt}]
            )

            result_text = response.choices[0].message.content.strip()
            logger.debug(f"OpenAI LLM raw response (first 200): {result_text[:200]}")

            tokens_used = None
            try:
                if response.usage:
                    tokens_used = {
                        'input_tokens': response.usage.prompt_tokens,
                        'output_tokens': response.usage.completion_tokens,
                        'total_tokens': response.usage.total_tokens
                    }
            except Exception as token_err:
                logger.warning(f"Could not extract OpenAI token usage: {token_err}")

        else:  # google
            api_key = os.getenv('GEMINI_API_KEY')
            if not api_key:
                logger.warning("GEMINI_API_KEY not set, cannot fix JSON")
                return None, None

            logger.info(f"[PPTX] Виклик LLM (Gemini/{GEMINI_TEXT_MODEL}) для виправлення JSON")

            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)

            response = client.models.generate_content(
                model=GEMINI_TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=GEMINI_TEXT_TEMPERATURE,
                    # thinking_config=types.ThinkingConfig(
                    #     thinking_budget=GEMINI_TEXT_THINKING_BUDGET
                    # )
                )
            )

            result_text = response.text.strip()
            logger.debug(f"Gemini LLM raw response (first 200): {result_text[:200]}")

            tokens_used = None
            try:
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    tokens_used = {
                        'input_tokens': getattr(response.usage_metadata, 'prompt_token_count', 0),
                        'output_tokens': getattr(response.usage_metadata, 'candidates_token_count', 0),
                        'total_tokens': getattr(response.usage_metadata, 'total_token_count', 0)
                    }
            except Exception as token_err:
                logger.warning(f"Could not extract Gemini token usage: {token_err}")

        # Extract JSON array — find first '[' ... last ']'
        start = result_text.find('[')
        end = result_text.rfind(']')
        if start != -1 and end != -1 and end > start:
            result_text = result_text[start:end + 1]
        else:
            if '```json' in result_text:
                result_text = result_text.split('```json', 1)[1]
            if '```' in result_text:
                result_text = result_text.rsplit('```', 1)[0]
            result_text = result_text.strip()

        data = json.loads(result_text)

        if isinstance(data, list):
            logger.info(f"Successfully fixed JSON with LLM ({provider}): {len(data)} slides")
            return data, tokens_used
        else:
            logger.warning("LLM returned non-array JSON")
            return None, tokens_used

    except Exception as e:
        import traceback
        logger.error(f"Failed to fix JSON with LLM ({provider}): {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return None, None

# 3. Генерація зображень
def generate_image_with_google(description, output_dir, slide_index):
    """
    Generate image using Gemini AI based on description.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            logger.warning("GEMINI_API_KEY not set, skipping image generation")
            return None, None
        
        from google import genai
        from google.genai import types
        import base64
        
        client = genai.Client(api_key=api_key)
        
        enhanced_prompt = f"""Create a clean, professional illustration for a presentation slide.
        
Description: {description}

Requirements:
- Simple, minimalist design
- Suitable for educational/business presentation
- Clear visual elements
- No text in the image
- Professional color palette"""
        
        logger.info(f"Generating image for slide {slide_index}: {description[:50]}...")
        
        response = client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=enhanced_prompt,
            config=types.GenerateContentConfig(
                temperature=GEMINI_IMAGE_TEMPERATURE,
                response_modalities=['Text', 'Image'],
                thinking_config=types.ThinkingConfig(thinking_budget=GEMINI_IMAGE_THINKING_BUDGET)
            )
        )
        
        image_data = None
        logger.info(f"Response candidates count: {len(response.candidates)}")
        
        if response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]
            logger.info(f"Content parts count: {len(candidate.content.parts)}")
            
            for i, part in enumerate(candidate.content.parts):
                logger.info(f"Part {i}: type={type(part)}, text={getattr(part, 'text', None)[:50] if hasattr(part, 'text') and part.text else 'None'}")
                
                if hasattr(part, 'inline_data') and part.inline_data is not None:
                    image_data = part.inline_data.data
                    logger.info(f"Found inline_data for slide {slide_index}")
                    break
                elif hasattr(part, 'inlineData') and part.inlineData is not None:
                    image_data = part.inlineData.data
                    logger.info(f"Found inlineData for slide {slide_index}")
                    break
        
        if image_data is None:
            logger.warning(f"No image data returned for slide {slide_index}")
            return None, None
        
        image_path = os.path.join(output_dir, f"slide_{slide_index}.png")
        with open(image_path, 'wb') as f:
            f.write(base64.b64decode(image_data) if isinstance(image_data, str) else image_data)
        
        tokens_used = None
        try:
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                tokens_used = {
                    'input_tokens': getattr(response.usage_metadata, 'prompt_token_count', 0),
                    'output_tokens': getattr(response.usage_metadata, 'candidates_token_count', 0),
                    'total_tokens': getattr(response.usage_metadata, 'total_token_count', 0)
                }
                logger.info(f"Image generation tokens: {tokens_used}")
        except Exception as token_err:
            logger.warning(f"Could not extract image generation token usage: {token_err}")
        
        logger.info(f"Image saved to {image_path}")
        return image_path, tokens_used
        
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        return None, None

# 3.1 Генерація зображень
def generate_image_with_openai(description, output_dir, slide_index):
    """
    Generate image using OpenAI GPT Image 1 Mini based on description.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, skipping image generation")
            return None, None
        
        from openai import OpenAI
        import httpx
        import base64
        
        # Таймаут 120с — OpenAI docs: складні промпти до 2 хвилин
        client = OpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(120.0, connect=10.0)
        )
        
        enhanced_prompt = f"""Create a clean, professional illustration for a presentation slide.
        
Description: {description}

Requirements:
- Simple, minimalist design
- Suitable for educational/business presentation
- Clear visual elements
- No text in the image
- Professional color palette"""
        
        logger.info(f"Generating image with OpenAI for slide {slide_index}: {description[:50]}...")
        
        response = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=enhanced_prompt,
            n=1,
            size=OPENAI_IMAGE_SIZE,
            quality=OPENAI_IMAGE_QUALITY
            # response_format="b64_json"
        )
        
        if response.data and len(response.data) > 0:
            image_data = response.data[0].b64_json
            if image_data:
                image_path = os.path.join(output_dir, f"slide_{slide_index}.png")
                with open(image_path, 'wb') as f:
                    f.write(base64.b64decode(image_data))
                
                tokens_used = None
                try:
                    if hasattr(response, 'usage') and response.usage:
                        tokens_used = {
                            'input_tokens': getattr(response.usage, 'prompt_tokens', 0),
                            'output_tokens': getattr(response.usage, 'completion_tokens', 0),
                            'total_tokens': getattr(response.usage, 'total_tokens', 0)
                        }
                        logger.info(f"OpenAI image generation tokens: {tokens_used}")
                except Exception as token_err:
                    logger.warning(f"Could not extract OpenAI image generation token usage: {token_err}")
                
                logger.info(f"OpenAI image saved to {image_path}")
                return image_path, tokens_used
        
        logger.warning(f"No image data returned from OpenAI for slide {slide_index}")
        return None, None
        
    except Exception as e:
        logger.error(f"OpenAI image generation failed: {e}", exc_info=True)
        return None, None

# Валідує JSON без генерації, Може викликати fix_json_with_llm() при невалідному JSON
@pptx_generator_bp.route('/api/validate', methods=['POST'])
@login_required
def validate_json():
    """Validate JSON structure without generating. If invalid, try LLM fix."""
    try:
        raw_data = request.get_data(as_text=True)
        
        if not raw_data:
            return jsonify({'error': 'No data provided'}), 400
        
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            logger.info("JSON parsing failed in validate, attempting LLM fix")
            data, _ = fix_json_with_llm(raw_data, provider='google')
            
            if data is None:
                return jsonify({
                    'valid': False,
                    'errors': ['Invalid JSON and LLM fix failed. Please provide valid JSON or clear text.'],
                    'warnings': [],
                    'slide_count': 0,
                    'llm_fixed': False
                })
            
            return jsonify({
                'valid': True,
                'errors': [],
                'warnings': ['JSON was automatically fixed by LLM'],
                'slide_count': len(data),
                'llm_fixed': True,
                'fixed_data': data
            })
        
        if not isinstance(data, list):
            return jsonify({'error': 'Data must be an array of slide objects'}), 400
        
        errors = []
        warnings = []
        
        for i, slide in enumerate(data):
            if not isinstance(slide, dict):
                errors.append(f'Slide {i}: must be an object')
                continue
            
            if 'title' not in slide:
                errors.append(f'Slide {i}: missing required field "title"')
            elif not slide['title'].strip():
                warnings.append(f'Slide {i}: title is empty')
            
            if 'body' not in slide:
                errors.append(f'Slide {i}: missing required field "body"')
            elif not slide['body'].strip():
                warnings.append(f'Slide {i}: body is empty')
            
            if 'image_description' in slide and slide['image_description']:
                if len(slide['image_description']) < 10:
                    warnings.append(f'Slide {i}: image_description too short for good generation')
        
        return jsonify({
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'slide_count': len(data),
            'llm_fixed': False
        })
        
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return jsonify({'error': f'Validation failed: {str(e)}'}), 500


@pptx_generator_bp.route('/api/upload-docx', methods=['POST'])
@login_required
def upload_docx():
    """Upload and extract text from DOCX file."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.docx'):
            return jsonify({'error': 'File must be a .docx file'}), 400
        
        temp_dir = tempfile.mkdtemp(prefix='docx_upload_')
        safe_name = os.path.basename(file.filename)
        file_path = os.path.join(temp_dir, safe_name)
        file.save(file_path)
        
        text = extract_text_from_docx(file_path)
        
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        if text is None:
            return jsonify({'error': 'Failed to extract text from DOCX'}), 500
        
        logger.info(f"Extracted {len(text)} characters from DOCX")
        
        return jsonify({
            'success': True,
            'text': text,
            'filename': file.filename
        })
        
    except Exception as e:
        logger.error(f"DOCX upload error: {e}")
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500


# ===================================================================
# NEW: Session-aware endpoints for multi-design workflow
# ===================================================================


@pptx_generator_bp.route('/api/save-selection', methods=['POST'])
@login_required
def save_selection():
    """Save selected design indices to session state."""
    try:
        body = request.get_json(force=True)
        if not body or 'selected_indices' not in body:
            return jsonify({'error': 'selected_indices required'}), 400
        state = _load_session_state()
        state['selected_designs'] = body['selected_indices']
        _save_session_state(state)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Save selection error: {e}")
        return jsonify({'error': str(e)}), 500


@pptx_generator_bp.route('/api/save-raw-input', methods=['POST'])
@login_required
def save_raw_input():
    """Save raw textarea input to session state."""
    try:
        raw_text = request.get_data(as_text=True)
        state = _load_session_state()
        state['raw_input'] = raw_text
        _save_session_state(state)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Save raw input error: {e}")
        return jsonify({'error': str(e)}), 500


@pptx_generator_bp.route('/api/session-state', methods=['GET'])
@login_required
def session_state():
    """Return current session state — designs, images, input status, selection, tokens."""
    try:
        state = _load_session_state()
        sdir = _get_session_dir()
        input_file = os.path.join(sdir, 'input.json')

        if os.path.exists(input_file):
            with open(input_file, 'r') as f:
                state['input'] = json.load(f)

        # Reload images status
        img_dir = os.path.join(sdir, 'images')
        image_files = [f for f in os.listdir(img_dir) if f.endswith('.png')]
        state['has_images'] = len(image_files) > 0
        state['image_count'] = len(image_files)

        # Reload designs from disk
        designs_dir = os.path.join(sdir, 'designs')
        if os.path.isdir(designs_dir):
            import glob as glob_mod
            design_files = sorted(glob_mod.glob(os.path.join(designs_dir, '*.json')))
            if design_files:
                loaded = []
                for df in design_files:
                    with open(df, 'r') as f:
                        loaded.append(json.load(f))
                state['designs'] = loaded
                state['has_designs'] = len(loaded) > 0

        # Reload presentations
        pres_dir = os.path.join(sdir, 'presentations')
        if os.path.isdir(pres_dir):
            pres_files = [f for f in os.listdir(pres_dir) if f.endswith('.pptx')]
            state['presentations'] = pres_files
        else:
            state['presentations'] = []

        # Ensure new fields exist (backward compatibility)
        state.setdefault('selected_designs', [])
        state.setdefault('total_tokens', 0)
        state.setdefault('raw_input', '')

        return jsonify(state)
    except Exception as e:
        logger.error(f"Session state error: {e}")
        return jsonify({'error': str(e)}), 500


@pptx_generator_bp.route('/api/generate-designs', methods=['POST'])
@login_required
def generate_designs():
    """
    Generate 6 designs (3 LLM + 3 random) and store in session.
    Body: raw JSON text or {slides: [...], input_text: '...'}.
    """
    try:
        raw_data = request.get_data(as_text=True)
        if not raw_data:
            return jsonify({'error': 'No data provided'}), 400

        sdir = _get_session_dir()
        total_tokens = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}

        # Parse input
        try:
            parsed = json.loads(raw_data)
        except json.JSONDecodeError:
            return jsonify({'error': 'Невірний JSON. Використайте /api/validate для автовиправлення.'}), 400

        # Support {slides: [...], ...} and direct array
        if isinstance(parsed, dict) and 'slides' in parsed:
            slides = parsed['slides']
        elif isinstance(parsed, list):
            slides = parsed
        else:
            return jsonify({'error': 'Data must be array of slides or {slides: [...]}'}), 400

        # Validate
        for i, slide in enumerate(slides):
            if not isinstance(slide, dict):
                return jsonify({'error': f'Slide {i} must be an object'}), 400
            if 'title' not in slide:
                return jsonify({'error': f'Slide {i} missing title'}), 400
            if 'body' not in slide:
                return jsonify({'error': f'Slide {i} missing body'}), 400

        slide_count = len(slides)

        # Save raw input to state (before clearing old data)
        prev_state = _load_session_state()
        prev_state['raw_input'] = raw_data
        _save_session_state(prev_state)

        # Save input to session
        input_file = os.path.join(sdir, 'input.json')
        with open(input_file, 'w') as f:
            json.dump(slides, f)

        # Clear old designs
        designs_dir = os.path.join(sdir, 'designs')
        shutil.rmtree(designs_dir, ignore_errors=True)
        os.makedirs(designs_dir, exist_ok=True)

        provider = request.args.get('provider', 'google').lower()
        if provider not in ['google', 'openai']:
            provider = 'google'

        # --- 1. LLM designs ---
        llm_designs = []
        try:
            slides_summary_lines = []
            for i, slide in enumerate(slides):
                title = slide.get('title', '')
                body_text = slide.get('body', '')[:200]
                content_type = slide.get('content_type', 'text')
                ct_label = f"[{content_type}]" if content_type != 'text' else ""
                slides_summary_lines.append(f"Слайд {i + 1}: {title} {ct_label}\n{body_text}")
            slides_summary = '\n\n'.join(slides_summary_lines)
            user_prompt = DESIGN_USER_PROMPT.format(slides_summary=slides_summary)

            for attempt in range(2):
                result_text = ''
                try:
                    if provider == 'openai':
                        api_key = os.getenv('OPENAI_API_KEY')
                        if api_key:
                            from openai import OpenAI
                            client = OpenAI(api_key=api_key)
                            resp = client.chat.completions.create(
                                model=OPENAI_TEXT_MODEL,
                                messages=[
                                    {"role": "system", "content": DESIGN_SYSTEM_PROMPT},
                                    {"role": "user", "content": user_prompt}
                                ]
                            )
                            result_text = resp.choices[0].message.content.strip()
                            if resp.usage:
                                total_tokens['input_tokens'] += resp.usage.prompt_tokens or 0
                                total_tokens['output_tokens'] += resp.usage.completion_tokens or 0
                                total_tokens['total_tokens'] += resp.usage.total_tokens or 0
                    else:
                        api_key = os.getenv('GEMINI_API_KEY')
                        if api_key:
                            from google import genai
                            from google.genai import types
                            client = genai.Client(api_key=api_key)
                            full_prompt = f"{DESIGN_SYSTEM_PROMPT}\n\n{user_prompt}"
                            resp = client.models.generate_content(
                                model=GEMINI_TEXT_MODEL,
                                contents=full_prompt,
                                config=types.GenerateContentConfig(temperature=0.4)
                            )
                            result_text = resp.text.strip()
                            if hasattr(resp, 'usage_metadata') and resp.usage_metadata:
                                total_tokens['input_tokens'] += getattr(resp.usage_metadata, 'prompt_token_count', 0)
                                total_tokens['output_tokens'] += getattr(resp.usage_metadata, 'candidates_token_count', 0)
                                total_tokens['total_tokens'] += getattr(resp.usage_metadata, 'total_token_count', 0)
                    # Extract JSON
                    start = result_text.find('[')
                    end = result_text.rfind(']')
                    if start != -1 and end != -1 and end > start:
                        result_text = result_text[start:end + 1]
                    if '```json' in result_text:
                        result_text = result_text.split('```json', 1)[1]
                    if '```' in result_text:
                        result_text = result_text.rsplit('```', 1)[0]
                    result_text = result_text.strip()
                    llm_designs = json.loads(result_text)
                    if isinstance(llm_designs, list) and len(llm_designs) == 3:
                        break
                    llm_designs = []
                except Exception as e:
                    logger.warning(f"LLM design attempt {attempt + 1} failed: {e}")
                    llm_designs = []
        except Exception as e:
            logger.warning(f"LLM design generation error: {e}")

        # --- 2. Random designs ---
        random_designs = [_generate_random_design(i) for i in range(3)]

        # --- 3. Merge: 3 LLM + 3 random = 6 designs ---
        all_designs = []
        if llm_designs:
            all_designs.extend(llm_designs)
        all_designs.extend(random_designs)

        # Validate & fix zones for each
        fixed_designs = []
        for i, design in enumerate(all_designs):
            try:
                design, was_fixed, conflicts = _validate_and_fix_zones(design)
                if was_fixed:
                    logger.warning(f"Design {i}: auto-fixed overlaps: {conflicts}")
                fixed_designs.append(design)
            except Exception as e:
                logger.warning(f"Design {i} failed validation: {e}. Adding raw.")
                fixed_designs.append(design)

        # Store designs to session
        for i, design in enumerate(fixed_designs):
            df = os.path.join(designs_dir, f'design_{i}.json')
            with open(df, 'w') as f:
                json.dump(design, f)

        # Update session state
        prev_state = _load_session_state()
        accumulated = prev_state.get('total_tokens', 0) + total_tokens.get('total_tokens', 0)
        state = {
            'has_designs': True,
            'has_images': False,
            'slide_count': slide_count,
            'designs': fixed_designs,
            'has_input': True,
            'selected_designs': [],
            'total_tokens': accumulated,
            'raw_input': raw_data,
        }
        _save_session_state(state)

        return jsonify({
            'designs': fixed_designs,
            'slide_count': slide_count,
            'tokens': total_tokens,
        })

    except Exception as e:
        logger.error(f"Generate designs error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@pptx_generator_bp.route('/api/generate-images', methods=['POST'])
@login_required
def generate_images():
    """Generate images for all slides that have image_description. SSE stream."""
    sdir = _get_session_dir()

    def stream():
        try:
            state = _load_session_state()
            input_file = os.path.join(sdir, 'input.json')
            if not os.path.exists(input_file):
                yield _sse_event('error', {'message': 'Немає вхідних даних. Спочатку згенеруйте дизайн.'})
                return

            with open(input_file, 'r') as f:
                slides = json.load(f)

            provider = request.args.get('provider', 'google').lower()
            if provider not in ['google', 'openai']:
                provider = 'google'

            images_dir = os.path.join(sdir, 'images')
            os.makedirs(images_dir, exist_ok=True)

            # Collect slides with image_description
            image_tasks = {}
            for i, slide in enumerate(slides):
                desc = slide.get('image_description', '')
                if desc:
                    image_tasks[i] = desc

            if not image_tasks:
                yield _sse_event('status', {'step': 'no_images', 'message': 'Немає слайдів з image_description'})
                state['has_images'] = True
                _save_session_state(state)
                yield _sse_event('done', {'message': 'Зображення не потрібні', 'filename': None, 'file_b64': None})
                return

            slide_count = len(slides)
            yield _sse_event('status', {
                'step': 'images_start',
                'message': f'Генерую {len(image_tasks)} зображень паралельно...',
                'slide_total': slide_count
            })

            from concurrent.futures import ThreadPoolExecutor, as_completed
            import threading

            completed_lock = threading.Lock()
            completed_count = [0]
            total_tokens = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}

            def _generate_one(idx, desc):
                if provider == 'openai':
                    return idx, *generate_image_with_openai(desc, images_dir, idx)
                else:
                    return idx, *generate_image_with_google(desc, images_dir, idx)

            max_workers = OPENAI_IMAGE_MAX_WORKERS if provider == 'openai' else GEMINI_IMAGE_MAX_WORKERS
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_generate_one, i, desc): i
                    for i, desc in image_tasks.items()
                }
                for future in as_completed(futures):
                    try:
                        idx, image_path, image_tokens = future.result()
                    except Exception as e:
                        idx = futures[future]
                        logger.warning(f"Image generation failed for slide {idx}: {e}")
                        yield _sse_event('warning', {
                            'message': f'Помилка зображення для слайду {idx + 1}: {e}'
                        })
                        continue

                    with completed_lock:
                        completed_count[0] += 1
                        done = completed_count[0]

                    if image_path:
                        if image_tokens:
                            total_tokens['input_tokens'] += image_tokens.get('input_tokens', 0)
                            total_tokens['output_tokens'] += image_tokens.get('output_tokens', 0)
                            total_tokens['total_tokens'] += image_tokens.get('total_tokens', 0)
                        yield _sse_event('status', {
                            'step': 'image_done',
                            'message': f'Зображення {done}/{len(image_tasks)} готово (слайд {idx + 1})',
                            'slide_index': idx,
                            'slide_total': slide_count
                        })
                    else:
                        yield _sse_event('warning', {
                            'message': f'Зображення для слайду {idx + 1} не згенеровано, буде заглушка'
                        })

            # Update state — reload fresh to avoid overwriting concurrent changes
            state = _load_session_state()
            state['has_images'] = True
            state['total_tokens'] = state.get('total_tokens', 0) + total_tokens.get('total_tokens', 0)
            _save_session_state(state)

            yield _sse_event('done', {
                'message': f'Зображення згенеровано: {completed_count[0]}/{len(image_tasks)}',
                'tokens': total_tokens
            })

        except Exception as e:
            logger.error(f"Generate images error: {e}", exc_info=True)
            yield _sse_event('error', {'message': f'Image generation failed: {str(e)}'})

    return Response(
        stream_with_context(stream()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@pptx_generator_bp.route('/api/generate-presentations', methods=['POST'])
@login_required
def generate_presentations():
    """
    Generate .pptx files for selected designs.
    Body: {'design_indices': [0, 2, 4]}
    Returns list of {filename, design_name} (no base64 — files served via download endpoint).
    """
    try:
        body = request.get_json(force=True)
        if not body or 'design_indices' not in body:
            return jsonify({'error': 'design_indices required'}), 400

        indices = body['design_indices']
        if not isinstance(indices, list) or not indices:
            return jsonify({'error': 'design_indices must be non-empty array'}), 400

        sdir = _get_session_dir()
        input_file = os.path.join(sdir, 'input.json')
        if not os.path.exists(input_file):
            return jsonify({'error': 'No input data found. Generate designs first.'}), 400

        with open(input_file, 'r') as f:
            slides = json.load(f)

        designs_dir = os.path.join(sdir, 'designs')
        images_dir = os.path.join(sdir, 'images')
        pres_dir = os.path.join(sdir, 'presentations')

        results = []
        for idx in indices:
            if not isinstance(idx, int):
                return jsonify({'error': f'Design index must be an integer, got {type(idx).__name__}'}), 400
            df = os.path.join(designs_dir, f'design_{idx}.json')
            if not os.path.exists(df):
                return jsonify({'error': f'Design {idx} not found'}), 400

            with open(df, 'r') as f:
                design_config = json.load(f)

            # Build slides_with_images — attach paths
            slides_with_images = []
            for si, slide in enumerate(slides):
                s = slide.copy()
                potential_img = os.path.join(images_dir, f'slide_{si}.png')
                if os.path.exists(potential_img):
                    s['image_path'] = potential_img
                slides_with_images.append(s)

            # Generate .pptx
            filename = f"presentation_design_{idx + 1}.pptx"
            output_path = os.path.join(pres_dir, filename)
            create_presentation(output_path, slides_with_images, images_dir, design_config=design_config)

            # Get design name
            design_name = design_config.get('name', f'Design #{idx + 1}')
            results.append({
                'index': idx,
                'filename': filename,
                'design_name': design_name,
            })

        logger.info(f"Generated {len(results)} presentations for designs: {indices}")
        return jsonify({'files': results, 'count': len(results)})

    except Exception as e:
        logger.error(f"Generate presentations error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@pptx_generator_bp.route('/api/download-presentation/<filename>')
@login_required
def download_presentation(filename):
    """Serve a single .pptx file from the session's presentations directory."""
    import re
    if not re.match(r'^[\w\.\-]+\.pptx$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    try:
        sdir = _get_session_dir()
        file_path = os.path.join(sdir, 'presentations', filename)
        if not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        return send_file(file_path, as_attachment=True, download_name=filename)
    except Exception as e:
        logger.error(f"Download presentation error: {e}")
        return jsonify({'error': str(e)}), 500


@pptx_generator_bp.route('/api/clear-session', methods=['POST'])
@login_required
def clear_session():
    """Clear all session data — designs, images, presentations, input."""
    try:
        sdir = _get_session_dir()
        if os.path.exists(sdir):
            shutil.rmtree(sdir, ignore_errors=True)
        # Remove session ID so next call starts fresh
        session.pop('pptx_session_id', None)
        logger.info(f"Session cleared: {sdir}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Clear session error: {e}")
        return jsonify({'error': str(e)}), 500