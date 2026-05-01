# перезапуск сервера після змін sudo systemctl restart mysite.service
from converter import converter_bp
from blueprints.doc_generator import doc_generator_bp
from blueprints.pptx_generator import pptx_generator_bp
from celery_worker import celery_app
from flask import Flask, render_template, redirect, url_for, session
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
app.register_blueprint(converter_bp)
app.register_blueprint(doc_generator_bp)
app.register_blueprint(pptx_generator_bp)
app.secret_key = os.getenv('SECRET_KEY')

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
    {"name": "Генератор PowerPoint", "url": "/pptx-generator", "desc": "Створення презентацій з JSON"},
]

@app.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', services=SERVICES)

@app.route('/login')
def login():
    return google.authorize_redirect('https://openclaw.tailf549b1.ts.net/auth/callback')

@app.route('/auth/callback')
def auth_callback():
    try:
        token = google.authorize_access_token()
    except Exception:
        return redirect(url_for('login'))
    
    user_info = token.get('userinfo')
    email = user_info.get('email')
    
    if email not in ALLOWED_EMAILS:
        return render_template('access_denied.html', email=email), 403
    
    session['email'] = email
    return redirect(url_for('index'))
    
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)