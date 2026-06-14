# перезапуск сервера після змін sudo systemctl restart mysite.service
from blueprints.txt_to_docx_converter import converter_bp
from blueprints.doc_generator import doc_generator_bp
from blueprints.pptx_generator import pptx_generator_bp
from blueprints.ocr_processor import ocr_processor_bp
from blueprints.moodle_session import moodle_session_bp
from blueprints.moodle_session.moodle_reporter import get_moodle_token
from blueprints.doc_generator.celery_worker import celery_app
from blueprints.my_ai_chat import my_ai_chat_bp
from flask import Flask, render_template, redirect, url_for, session, jsonify, request, current_app
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import os
import logging
from loguru import logger
import sys
from common_config import load_llm_config, save_llm_config, detect_provider_type, sanitize_settings_for_client, login_required

load_dotenv()

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler()]
)

# Налаштування loguru для запису в файл
log_dir = '/home/openclaw/mysite/logs'
os.makedirs(log_dir, exist_ok=True)
logger.add(
    os.path.join(log_dir, "mysite_{time:YYYY-MM-DD}.log"),
    rotation="00:00",  # Новий файл щодня
    retention="7 days",  # Зберігати логи 7 днів
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    enqueue=True,  # Асинхронний запис для потокобезпеки
    backtrace=True,  # Детальні трейси
    diagnose=True
)
# Додатково логувати в stderr для systemd
logger.add(sys.stderr, level="INFO")

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True  # Disable template caching
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable static file caching
app.register_blueprint(converter_bp)
app.register_blueprint(doc_generator_bp)
app.register_blueprint(pptx_generator_bp)
app.register_blueprint(ocr_processor_bp)
app.register_blueprint(moodle_session_bp)
app.register_blueprint(my_ai_chat_bp)
app.secret_key = os.getenv('SECRET_KEY', 'temporary_secret_key_for_debug')

app.config['LLM_CONFIG'] = load_llm_config()


# =============================================================
# CORS для публічних endpoint'ів
# =============================================================

@app.after_request
def add_cors_headers(response):
    """Додає CORS заголовки для публічних endpoint'ів."""
    if request.path == '/moodle/get-token':
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# =============================================================
# Global LLM Settings Endpoints
# =============================================================

@app.route('/settings')
@login_required
def settings_page():
    """LLM settings management page."""
    return render_template('settings.html')


@app.route('/api/global-settings', methods=['GET'])
@login_required
def get_global_settings():
    """Load global LLM settings (apiKey masked for security)."""
    settings = current_app.config.get('LLM_CONFIG') or load_llm_config()
    return jsonify(sanitize_settings_for_client(settings))


@app.route('/api/global-settings', methods=['POST'])
@login_required
def save_global_settings():
    """Save global LLM settings. Preserves existing apiKey when client sends masked value."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        existing = current_app.config.get('LLM_CONFIG') or load_llm_config()
        existing_providers = {p['id']: p.get('apiKey', '') for p in existing.get('providers', [])}

        for p in data.get('providers', []):
            key = p.get('apiKey', '')
            if key and '\u2022' in key and p['id'] in existing_providers:
                existing_key = existing_providers[p['id']]
                if existing_key and '\u2022' not in existing_key:
                    p['apiKey'] = existing_key

        save_llm_config(data)
        current_app.config['LLM_CONFIG'] = data
        logger.info(f"[GlobalSettings] Settings saved. providers={[p['id'] for p in data.get('providers', [])]}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Failed to save global settings: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/detect-provider', methods=['POST'])
@login_required
def detect_provider():
    """Auto-detect provider type."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        api_url = data.get('api_url', '')
        api_key = data.get('api_key', '')
        if not api_url or not api_key:
            return jsonify({'error': 'API URL and API key are required'}), 400
        detected_type = detect_provider_type(api_url, api_key)
        return jsonify({'type': detected_type})
    except Exception as e:
        logger.error(f"Provider detection failed: {e}")
        return jsonify({'type': None, 'error': str(e)}), 200


@app.route('/api/providers')
@login_required
def list_providers():
    """List all providers (for model selector)."""
    settings = current_app.config.get('LLM_CONFIG') or load_llm_config()
    providers = []
    for p in settings.get('providers', []):
        providers.append({
            'id': p['id'],
            'name': p['name'],
            'type': p.get('type'),
        })
    return jsonify(providers)


@app.route('/api/providers/<provider_id>/models')
@login_required
def list_provider_models(provider_id):
    """List models for a provider."""
    settings = current_app.config.get('LLM_CONFIG') or load_llm_config()
    for p in settings.get('providers', []):
        if p['id'] == provider_id:
            return jsonify(p.get('models', []))
    return jsonify({'error': 'Provider not found'}), 404


logger.info("=== APP STARTUP ===")
logger.info(f"Blueprints registered: converter, doc_generator, pptx_generator, ocr_processor, moodle_session, my_ai_chat")
logger.info(f"Secret key set: {bool(app.secret_key)}")

# Celery and data configuration
app.config['DATA_DIR'] = os.getenv('DATA_DIR', '/home/openclaw/mysite/data')
celery_app.conf.update(app.config)

logger = logging.getLogger(__name__)

ALLOWED_EMAILS = [
    'openclaw.gora@gmail.com',
    'olexandr.gora@gmail.com',
    'andre.horbatuk@gmail.com',
    'tetbrovenko@gmail.com'
]

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
    'scope': 'openid email profile https://www.googleapis.com/auth/drive',
    },
    authorize_params={
        'access_type': 'offline',
        'prompt': 'consent',
    },
)

SERVICES = [
    {"name": "Конвертор тексту з формулами, таблицями → Google Doc, Microsoft Word", "url": "/converter", "desc": "Конвертація TXT/MD файлів"},
    {"name": "Генератор документів", "url": "/doc-generator", "desc": "Генерація документів з шаблонів і джерел"},
    {"name": "Генератор PowerPoint презентацій", "url": "/pptx-generator", "desc": "Створення презентацій з JSON"},
    {"name": "OCR Текстовий Розпізнавач", "url": "/ocr-processor", "desc": "Розпізнавання друкованого та рукописного тексту зі зображень"},
    {"name": "Moodle Assignment Reporter", "url": "/moodle-session", "desc": "Отримання завдань з Moodle НУБіП та надсилання у Telegram"},
    {"name": "AI Чат", "url": "/ai-chat", "desc": "Чат з AI через різних провайдерів (OpenAI, DeepSeek, Gemini)"},
]

@app.route('/')
def index():
    logger.info(f"[INDEX] Request from session: email={session.get('email')}")
    if 'email' not in session:
        logger.info("[INDEX] No email in session, redirecting to login")
        return redirect(url_for('login'))
    logger.info(f"[INDEX] Rendering index.html for {session['email']}")
    return render_template('index.html', services=SERVICES)

@app.route('/login')
def login():
    logger.info("[LOGIN] Redirecting to Google OAuth")
    return google.authorize_redirect('https://openclaw.tailf549b1.ts.net/auth/callback')

@app.route('/auth/callback')
def auth_callback():
    logger.info("[AUTH_CALLBACK] Received callback from Google")
    try:
        token = google.authorize_access_token()
        logger.info("[AUTH_CALLBACK] Token received successfully")
    except Exception as e:
        logger.error(f"[AUTH_CALLBACK] Failed to get token: {e}")
        return redirect(url_for('login'))
    
    # Validate token has required fields
    if not token.get('refresh_token'):
        logger.warning("[AUTH_CALLBACK] No refresh_token in token, redirecting to re-authorize")
        return redirect(url_for('login'))
    
    scope = token.get('scope', '')
    if isinstance(scope, list):
        scope = ' '.join(scope)
    
    if 'drive' not in scope.lower():
        logger.warning(f"[AUTH_CALLBACK] Token missing Drive scope. Scope: {scope}")
        return redirect(url_for('login'))
    
    user_info = token.get('userinfo')
    email = user_info.get('email') if user_info else None
    
    if not email:
        logger.error("[AUTH_CALLBACK] No email in token")
        return redirect(url_for('login'))
    
    logger.info(f"[AUTH_CALLBACK] User email: {email}")
    
    if email not in ALLOWED_EMAILS:
        logger.warning(f"[AUTH_CALLBACK] Access denied for {email}")
        return render_template('access_denied.html', email=email), 403
    
    # Store token with normalized scope
    token['scope'] = scope
    session['token'] = token
    session['email'] = email
    session.modified = True
    logger.info(f"[AUTH_CALLBACK] Session set for {email}, token validated with refresh_token and Drive scope")
    return redirect(url_for('index'))
    
@app.route('/logout')
def logout():
    email = session.get('email', 'unknown')
    logger.info(f"[LOGOUT] User {email} logging out")
    session.clear()
    return redirect(url_for('login'))


# =============================================================
# Публічний endpoint для отримання Moodle API token
# =============================================================

@app.route('/moodle/get-token', methods=['POST', 'OPTIONS'])
def moodle_get_token():
    """
    Публічний endpoint для отримання Moodle API token.
    Не потребує @login_required — автентифікація через email+password.
    
    Request:  {"email": "...", "password": "...", "moodle_url": "..."}
    Response: {"wstoken": "..."} або {"error": "..."}
    """
    if request.method == 'OPTIONS':
        return '', 200

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Request body is required'}), 400

    email = data.get('email', '').strip()
    password = data.get('password', '')
    moodle_url = data.get('moodle_url', 'https://elearn.nubip.edu.ua').rstrip('/')

    if not email or not password:
        return jsonify({'error': 'email and password are required'}), 400

    logger.info(f"[MOODLE_GET_TOKEN] Отримуємо токен для {email}")

    token = get_moodle_token(email, password, moodle_url)
    if not token:
        logger.warning(f"[MOODLE_GET_TOKEN] Не вдалося отримати токен для {email}")
        return jsonify({'error': 'Failed to obtain Moodle token. Check credentials.'}), 401

    logger.info(f"[MOODLE_GET_TOKEN] Токен успішно отримано для {email}")
    return jsonify({'wstoken': token})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)