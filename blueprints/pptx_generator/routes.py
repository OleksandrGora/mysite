import os
import json
import uuid
import tempfile
import shutil
import threading
from datetime import datetime
from flask import request, jsonify, render_template, current_app, send_file, session, redirect, url_for, Response, stream_with_context
from loguru import logger
from functools import wraps
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

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            if request.is_json or '/api/' in request.path:
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


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


def _validate_and_fix_zones(design):
    zones = ['title', 'body', 'image']
    conflicts_log = []
    was_fixed = False
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
- font_family — тільки: Calibri, Arial, Georgia, Trebuchet MS, Verdana, Times New Roman, Tahoma
- font_size для title: 24-44, для body: 14-26
- shapes: від 1 до 4 фігур (rectangle, oval, right_triangle)
- Якщо є слайди з content_type "table" або "chart" — додай table_style / chart_style

ФОРМАТ ВІДПОВІДІ (JSON масив з 3 об'єктів):
[
  {{
    "id": "...",
    "name": "...",
    "rationale": "...",
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
        file_path = os.path.join(temp_dir, file.filename)
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