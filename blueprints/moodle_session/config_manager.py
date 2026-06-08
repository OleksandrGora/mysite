"""
Config Manager for Moodle Session Blueprint.
Зберігає налаштування у JSON-файлі data/moodle_config.json.
"""

import os
import json
import threading
from datetime import datetime
from loguru import logger

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data')
CONFIG_PATH = os.path.join(DATA_DIR, 'moodle_config.json')

_lock = threading.Lock()

DEFAULT_CONFIG = {
    "moodle_email": "",
    "moodle_password": "",
    "moodle_url": "https://elearn.nubip.edu.ua",
    "moodle_token": "",  # Збережений API токен (обхід Playwright)
    "telegram_bot_token": "",
    "telegram_chat_ids": "",
    "schedule_time": "08:00",
    "schedule_enabled": False,
    "last_run": None,
    "last_run_status": None,
    "last_scheduled_date": None,  # Дата останнього запуску за розкладом (YYYY-MM-DD)
    "last_report_hash": "",       # Хеш останнього звіту — для порівняння змін
}


def _ensure_data_dir():
    """Створює data/ якщо не існує."""
    os.makedirs(DATA_DIR, exist_ok=True)


def load_config() -> dict:
    """
    Читає JSON-конфіг з диска.
    Якщо файлу немає — повертає DEFAULT_CONFIG.
    """
    _ensure_data_dir()
    if not os.path.exists(CONFIG_PATH):
        logger.info(f"Moodle config: файл {CONFIG_PATH} не знайдено, повертаємо default")
        return dict(DEFAULT_CONFIG)

    try:
        with _lock:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
        # Додаємо відсутні ключі з дефолту (на випадок додавання нових полів)
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except Exception as e:
        logger.error(f"Moodle config: помилка читання {CONFIG_PATH}: {e}")
        return dict(DEFAULT_CONFIG)


def save_config(updates: dict) -> dict:
    """
    Оновлює JSON-конфіг.
    Приймає словник з полями для оновлення.
    Повертає повний оновлений конфіг.
    """
    _ensure_data_dir()
    config = load_config()

    with _lock:
        config.update(updates)
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.info(f"Moodle config: збережено оновлення: {list(updates.keys())}")
        except Exception as e:
            logger.error(f"Moodle config: помилка запису {CONFIG_PATH}: {e}")

    return config
