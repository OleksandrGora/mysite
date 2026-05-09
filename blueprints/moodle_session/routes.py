"""
Routes for Moodle Session Blueprint.
Сторінка керування: налаштування, тестовий запуск, розклад.
"""

import os
import json
from datetime import datetime
from flask import request, jsonify, render_template, session, redirect, url_for
from loguru import logger
from functools import wraps

from . import moodle_session_bp
from .config_manager import load_config, save_config
from .moodle_reporter import run_report


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            if request.is_json or '/api/' in request.path:
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# =============================================================
# Сторінка
# =============================================================

@moodle_session_bp.route('/')
@login_required
def index():
    """Головна сторінка Moodle Session."""
    logger.info(f"[MOODLE_SESSION] Головна сторінка для {session.get('email')}")
    config = load_config()
    # Додаємо допоміжні поля для шаблону
    config['moodle_password_set'] = bool(config.get('moodle_password'))
    config['moodle_token_set'] = bool(config.get('moodle_token'))
    return render_template('moodle_session/index-moodle.html', config=config)


# =============================================================
# Налаштування
# =============================================================

@moodle_session_bp.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    """Повертає поточні налаштування (без пароля/токена)."""
    config = load_config()
    safe = {k: v for k, v in config.items() if k not in ('moodle_password', 'moodle_token')}
    safe['moodle_password_set'] = bool(config.get('moodle_password'))
    safe['moodle_token_set'] = bool(config.get('moodle_token'))
    return jsonify(safe)


@moodle_session_bp.route('/api/settings', methods=['POST'])
@login_required
def save_settings():
    """Зберігає налаштування."""
    logger.info(f"[MOODLE_SESSION] Збереження налаштувань від {session.get('email')}")
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Немає даних'}), 400

    updates = {}

    # Moodle налаштування
    if 'moodle_email' in data:
        updates['moodle_email'] = data['moodle_email'].strip()
    if 'moodle_password' in data and data['moodle_password']:
        updates['moodle_password'] = data['moodle_password']
    if 'moodle_url' in data:
        updates['moodle_url'] = data['moodle_url'].strip()
    if 'moodle_token' in data:
        updates['moodle_token'] = data['moodle_token'].strip()

    # Telegram налаштування
    if 'telegram_bot_token' in data:
        updates['telegram_bot_token'] = data['telegram_bot_token'].strip()
    if 'telegram_chat_ids' in data:
        updates['telegram_chat_ids'] = data['telegram_chat_ids'].strip()

    # Розклад
    if 'schedule_time' in data:
        updates['schedule_time'] = data['schedule_time'].strip()
    if 'schedule_enabled' in data:
        updates['schedule_enabled'] = bool(data['schedule_enabled'])

    config = save_config(updates)
    logger.info(f"[MOODLE_SESSION] Налаштування збережено: {list(updates.keys())}")

    # Відповідь без пароля та токена
    safe = {k: v for k, v in config.items() if k not in ('moodle_password', 'moodle_token')}
    safe['moodle_password_set'] = bool(config.get('moodle_password'))
    safe['moodle_token_set'] = bool(config.get('moodle_token'))
    safe['message'] = 'Налаштування збережено'
    return jsonify(safe)


# =============================================================
# Тестовий запуск
# =============================================================

@moodle_session_bp.route('/api/test-run', methods=['POST'])
@login_required
def test_run():
    """
    Запускає звіт вручну (синхронно).
    Може зайняти 20-30 секунд через Playwright.
    """
    logger.info(f"[MOODLE_SESSION] Тестовий запуск від {session.get('email')}")

    config = load_config()
    result = run_report(config)

    # Оновлюємо статус останнього запуску в конфігу
    save_config({
        "last_run": datetime.now().isoformat(),
        "last_run_status": "success" if result.get("success") else "error",
    })

    status_code = 200 if result.get('success') else 500
    return jsonify(result), status_code


# =============================================================
# Статус
# =============================================================

@moodle_session_bp.route('/api/status', methods=['GET'])
@login_required
def get_status():
    """Повертає статус останнього запуску."""
    config = load_config()
    return jsonify({
        'last_run': config.get('last_run'),
        'last_run_status': config.get('last_run_status'),
        'schedule_enabled': config.get('schedule_enabled'),
        'schedule_time': config.get('schedule_time'),
    })
