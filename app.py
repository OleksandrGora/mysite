# перезапуск сервера після змін sudo systemctl restart mysite.service
from blueprints.txt_to_docx_converter import converter_bp
from blueprints.doc_generator import doc_generator_bp
from blueprints.pptx_generator import pptx_generator_bp
from blueprints.ocr_processor import ocr_processor_bp
from blueprints.moodle_session import moodle_session_bp
from blueprints.doc_generator.celery_worker import celery_app
from flask import Flask, render_template, redirect, url_for, session, jsonify
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import os
import logging
from loguru import logger
import sys

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
app.secret_key = os.getenv('SECRET_KEY', 'temporary_secret_key_for_debug')

logger.info("=== APP STARTUP ===")
logger.info(f"Blueprints registered: converter, doc_generator, pptx_generator, ocr_processor, moodle_session")
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
    {"name": "Конвертер LaTeX → Google Doc, Microsoft Word", "url": "/converter", "desc": "Конвертація TXT/MD файлів"},
    {"name": "Генератор документів", "url": "/doc-generator", "desc": "Генерація документів з шаблонів і джерел"},
    {"name": "Генератор PowerPoint презентацій", "url": "/pptx-generator", "desc": "Створення презентацій з JSON"},
    {"name": "OCR Текстовий Розпізнавач", "url": "/ocr-processor", "desc": "Розпізнавання друкованого та рукописного тексту зі зображень"},
    {"name": "Moodle Assignment Reporter", "url": "/moodle-session", "desc": "Отримання завдань з Moodle НУБіП та надсилання у Telegram"},
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
    
    user_info = token.get('userinfo')
    email = user_info.get('email')
    logger.info(f"[AUTH_CALLBACK] User email: {email}")
    
    if email not in ALLOWED_EMAILS:
        logger.warning(f"[AUTH_CALLBACK] Access denied for {email}")
        return render_template('access_denied.html', email=email), 403
    
    # Converter flow depends on OAuth token in session
    session['token'] = token
    session['email'] = email
    session.modified = True
    logger.info(f"[AUTH_CALLBACK] Session set for {email}, redirecting to index")
    return redirect(url_for('index'))
    
@app.route('/logout')
def logout():
    email = session.get('email', 'unknown')
    logger.info(f"[LOGOUT] User {email} logging out")
    session.clear()
    return redirect(url_for('login'))



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)