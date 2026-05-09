"""
Moodle Reporter — портування reporter.js у Python.
Отримує завдання з Moodle через API і відправляє звіт у Telegram.
"""

import re
import time
import base64
import urllib.parse
from datetime import datetime
from typing import Optional

import requests
from loguru import logger

from . import moodle_session as ms
from .config_manager import load_config

# =============================================================
# Константи
# =============================================================

MAX_NAME_LEN = 20
TYPE_ORDER = ['lab', 'self', 'test', 'control', 'practical', 'coursework', 'essay', 'module', 'other']


# =============================================================
# Допоміжні функції
# =============================================================

def pad2(n: int) -> str:
    return ('0' + str(n))[-2:]


def fmt_date(dt: datetime) -> str:
    return f"{pad2(dt.day)}.{pad2(dt.month)} {pad2(dt.hour)}:{pad2(dt.minute)}"


def abbreviate_name(name: str) -> str:
    s = name
    s = re.sub(r'лабораторн[^\s]*(?:\s+робот[^\s]*|\s+робіт)?\s*№?\s*(\d+)', r'ЛР\1', s, flags=re.IGNORECASE)
    s = re.sub(r'самостійн[^\s]*(?:\s+робот[^\s]*|\s+робіт)?\s*№?\s*(\d+)', r'СР\1', s, flags=re.IGNORECASE)
    if s != name:
        s = re.sub(r'\s+', ' ', s).strip()
    return s


def trim_name(name: str, max_len: int = MAX_NAME_LEN) -> str:
    s = abbreviate_name(name)
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + '...'


def num_from_name(name: str) -> int:
    m = re.search(r'\d+', name)
    return int(m.group(0)) if m else 999999


def detect_type(name: str) -> str:
    n = name.lower()
    if 'лаборатор' in n:
        return 'lab'
    if 'самостійн' in n:
        return 'self'
    if 'тест' in n:
        return 'test'
    if 'контрольн' in n:
        return 'control'
    if 'модуль' in n:
        return 'module'
    if 'курсов' in n:
        return 'coursework'
    if 'реферат' in n:
        return 'essay'
    if 'практичн' in n:
        return 'practical'
    return 'other'


# =============================================================
# Семестрова логіка
# =============================================================

def get_semester() -> Optional[dict]:
    now = datetime.now()
    m, y = now.month, now.year
    if 1 <= m <= 6:
        return {
            'start': datetime(y, 1, 1),
            'end': datetime(y, 6, 30),
            'label': f'Весняний семестр {y}'
        }
    if 9 <= m <= 12:
        return {
            'start': datetime(y, 9, 1),
            'end': datetime(y, 12, 31),
            'label': f'Осінній семестр {y}'
        }
    return None


def is_in_semester(ts: Optional[int], semester: dict) -> bool:
    if not ts or ts == 0:
        return False
    d = datetime.fromtimestamp(ts)
    return semester['start'] <= d <= semester['end']


def days_until(ts: Optional[int]) -> float:
    if not ts or ts == 0:
        return float('inf')
    return (datetime.fromtimestamp(ts) - datetime.now()).total_seconds() / 86400


def is_open_soon(ts: Optional[int]) -> bool:
    if not ts or ts == 0:
        return False
    return days_until(ts) <= 7


# =============================================================
# Moodle API
# =============================================================

def get_moodle_token(email: str, password: str, moodle_url: str) -> Optional[str]:
    """
    Отримує Moodle API token.
    
    Спроба 1: Прямий запит до login/token.php (не потребує Playwright)
    Спроба 2: Через Playwright + Google OAuth + launch.php (якщо login/token.php не працює)
    """
    moodle_url = moodle_url.rstrip('/')  # Прибираємо trailing slash

    # --- Спроба: Через Playwright + Google OAuth ---
    logger.info("Moodle reporter: отримуємо сесію через Playwright + Google OAuth...")
    session_cookie = ms.get_moodle_session(email, password, moodle_url)
    if not session_cookie:
        logger.error("Moodle reporter: не вдалося отримати MoodleSession через Playwright")
        return None

    logger.info("Moodle reporter: сесію отримано, отримуємо sesskey...")
    headers = {'Cookie': f'MoodleSession={session_cookie}'}

    try:
        resp = requests.get(f'{moodle_url}/user/profile.php', headers=headers, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Moodle reporter: profile.php помилка: {e}")
        return None

    m = re.search(r'"sesskey":"([^"]+)"', resp.text)
    if not m:
        logger.error("Moodle reporter: sesskey не знайдено в HTML")
        logger.debug(f"Moodle reporter: HTML фрагмент: {resp.text[:500]}")
        return None
    sesskey = m.group(1)
    logger.info(f"Moodle reporter: sesskey отримано: {sesskey}")

    logger.info("Moodle reporter: отримуємо token через launch.php...")
    launch_url = (
        f'{moodle_url}/admin/tool/mobile/launch.php'
        f'?service=moodle_mobile_app&passport=12345&sesskey={sesskey}'
    )
    try:
        resp2 = requests.get(
            launch_url,
            headers=headers,
            allow_redirects=False,
            timeout=30,
        )
        location = resp2.headers.get('Location', '')
        logger.debug(f"Moodle reporter: launch.php Location: {location[:300]}")
        if 'token=' not in location:
            logger.error(f"Moodle reporter: токен не знайдено в Location")
            return None

        b64 = location.replace('moodlemobile://token=', '')
        decoded = urllib.parse.unquote(b64)
        try:
            raw_bytes = base64.b64decode(decoded)
            raw = raw_bytes.decode('utf-8')
        except Exception:
            raw = decoded

        token_parts = raw.split(':::')
        token = token_parts[1] if len(token_parts) > 1 else token_parts[0]
        logger.info("Moodle reporter: токен отримано через Playwright")
        return token
    except Exception as e:
        logger.error(f"Moodle reporter: launch.php помилка: {e}")
        return None


def moodle_api_call(token: str, wsfunction: str, moodle_url: str, params: Optional[dict] = None) -> dict:
    """Універсальний виклик Moodle REST API."""
    url = f'{moodle_url}/webservice/rest/server.php'
    data = {
        'wstoken': token,
        'wsfunction': wsfunction,
        'moodlewsrestformat': 'json',
    }
    if params:
        data.update(params)

    resp = requests.get(url, params=data, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and result.get('errorcode'):
        raise Exception(f"Moodle API error: {result.get('errorcode')}: {result.get('message', result.get('error', ''))}")
    return result


def get_courses(token: str, moodle_url: str) -> list:
    """Отримує список курсів користувача."""
    info = moodle_api_call(token, 'core_webservice_get_site_info', moodle_url)
    userid = info.get('userid')
    if not userid:
        raise Exception("Не вдалося отримати userid")
    courses = moodle_api_call(token, 'core_enrol_get_users_courses', moodle_url, {'userid': userid})
    return courses


def get_assignments(cid: int, token: str, moodle_url: str) -> list:
    """Отримує завдання для курсу."""
    data = moodle_api_call(token, 'mod_assign_get_assignments', moodle_url, {'courseids[0]': cid})
    courses = data.get('courses', [])
    if courses and courses[0]:
        return courses[0].get('assignments', [])
    return []


def get_quizzes(cid: int, token: str, moodle_url: str) -> list:
    """Отримує тести для курсу."""
    data = moodle_api_call(token, 'mod_quiz_get_quizzes_by_courses', moodle_url, {'courseids[0]': cid})
    return data.get('quizzes', [])


def is_submitted(assign_id: int, token: str, moodle_url: str) -> bool:
    """Перевіряє чи здано завдання."""
    try:
        data = moodle_api_call(token, 'mod_assign_get_submission_status', moodle_url, {'assignid': assign_id})
        lastattempt = data.get('lastattempt', {})
        submission = lastattempt.get('submission', {})
        return submission.get('status') == 'submitted'
    except Exception:
        return False


# =============================================================
# Класифікація
# =============================================================

def classify(assignment: dict, semester: dict, is_test: bool = False) -> dict:
    """Класифікує завдання/тест."""
    t_open = assignment.get('timeopen' if is_test else 'allowsubmissionsfromdate')
    t_close = assignment.get('timeclose' if is_test else 'duedate')
    has_dl = bool(t_close and t_close > 0 and is_in_semester(t_close, semester))
    dl_days = days_until(t_close) if has_dl else float('inf')

    return {
        'name': assignment.get('name', ''),
        'timeOpen': t_open or None,
        'timeClose': t_close if has_dl else None,
        'deadlineDays': dl_days,
        'hasDeadline': has_dl,
        'isTest': is_test,
        'isOpenSoon': is_open_soon(t_open),
    }


def classify_item(item: dict) -> str:
    """Повертає 'show', 'hide' або 'nodeadline'."""
    if item['isTest']:
        has_timing = item['timeOpen'] or item['timeClose']
        if not has_timing:
            return 'hide'
        if item['timeClose'] and item['timeClose'] * 1000 < datetime.now().timestamp() * 1000:
            return 'hide'
        is_open = not item['timeOpen'] or item['timeOpen'] * 1000 < datetime.now().timestamp() * 1000
        if is_open:
            return 'show'
        if item['isOpenSoon']:
            return 'show'
        if item['hasDeadline'] and item['deadlineDays'] <= 7:
            return 'show'
        return 'hide'
    if item['hasDeadline'] and item['deadlineDays'] <= 7:
        return 'show'
    if not item['hasDeadline']:
        return 'nodeadline'
    return 'hide'


# =============================================================
# Форматування
# =============================================================

def fmt_show(item: dict) -> str:
    """Форматує елемент для показу (з дедлайном або тест)."""
    label = trim_name(item['name'])
    if item['isTest']:
        parts = []
        if item['timeOpen'] and item['timeOpen'] * 1000 > datetime.now().timestamp() * 1000:
            parts.append('🔓 ' + fmt_date(datetime.fromtimestamp(item['timeOpen'])))
        else:
            parts.append('відкрито')
        if item['timeClose'] and item['timeClose'] * 1000 > datetime.now().timestamp() * 1000:
            parts.append('до ' + fmt_date(datetime.fromtimestamp(item['timeClose'])))
        return label + ' | ' + ' '.join(parts)
    if item['timeClose']:
        s = label + ' | ' + fmt_date(datetime.fromtimestamp(item['timeClose']))
        if item['deadlineDays'] < 0:
            s += ' ⚠️'
        else:
            s += f' ({int(item["deadlineDays"])}д)'
        return s
    return label + ' | (без д/н)'


def fmt_nodeadline(item: dict) -> str:
    """Форматує елемент без дедлайну."""
    return trim_name(item['name']) + ' | (без д/н)'


# =============================================================
# Побудова звіту
# =============================================================

def build_report(courses_data: list) -> str:
    """
    Будує текст звіту для Telegram.
    courses_data — список словників {name, items}.
    """
    lines = []
    total = 0
    now = datetime.now()
    lines.append('📚 НУБіП — звіт по завданнях')
    lines.append(f'{pad2(now.day)}.{pad2(now.month)}.{now.year}')
    lines.append('')

    for course in courses_data:
        items = course.get('items', [])
        show = []
        nodead = []

        for item in items:
            cls = classify_item(item)
            if cls == 'show':
                show.append(item)
            elif cls == 'nodeadline':
                nodead.append(item)

        if not show and not nodead:
            continue

        lines.append(f'▸ {course["name"]}')
        show.sort(key=lambda x: x['deadlineDays'])
        for item in show:
            lines.append(fmt_show(item))
            total += 1

        if nodead:
            nodead.sort(key=lambda x: num_from_name(x['name']))
            groups = {}
            for item in nodead:
                t = detect_type(item['name'])
                groups.setdefault(t, []).append(item)
            for t in TYPE_ORDER:
                g = groups.get(t)
                if not g:
                    continue
                n = min(len(g), 2)
                for i in range(n):
                    lines.append(fmt_nodeadline(g[i]))
                    total += 1
                if len(g) > 2:
                    lines.append('⋯')

        lines.append('')

    lines.append(f'✉️ {total} завдань показано')
    return '\n'.join(lines)


# =============================================================
# Telegram
# =============================================================

def send_telegram(text: str, bot_token: str, chat_ids: list[str]) -> None:
    """Відправляє повідомлення в Telegram."""
    if not bot_token or not chat_ids:
        logger.warning("Moodle reporter: Telegram не налаштовано, звіт тільки в лог")
        logger.info(f"Moodle reporter: звіт:\n{text}")
        return

    for chat_id in chat_ids:
        chat_id = chat_id.strip()
        if not chat_id:
            continue
        try:
            resp = requests.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': chat_id, 'text': text},
                timeout=30,
            )
            result = resp.json()
            if not result.get('ok'):
                logger.warning(f"Telegram помилка для {chat_id}: {result}")
        except Exception as e:
            logger.error(f"Telegram помилка для {chat_id}: {e}")


# =============================================================
# Головна функція
# =============================================================

def run_report(config: Optional[dict] = None) -> dict:
    """
    Головна функція запуску звіту.
    Приймає конфіг (або завантажує з файлу).
    Повертає словник з результатом.
    """
    if config is None:
        config = load_config()

    email = config.get('moodle_email', '')
    password = config.get('moodle_password', '')
    moodle_url = config.get('moodle_url', 'https://moodle.nubip.edu.ua').rstrip('/')
    bot_token = config.get('telegram_bot_token', '')
    chat_ids_raw = config.get('telegram_chat_ids', '')
    # Можна зберегти токен напряму (обхід Playwright)
    saved_token = config.get('moodle_token', '')

    # Валідація
    missing = []
    if not saved_token:
        if not email:
            missing.append('moodle_email')
        if not password:
            missing.append('moodle_password')
    if not bot_token:
        missing.append('telegram_bot_token')
    if not chat_ids_raw:
        missing.append('telegram_chat_ids')

    if missing:
        msg = f"Не заповнені налаштування: {', '.join(missing)}"
        logger.error(f"Moodle reporter: {msg}")
        return {'success': False, 'report': '', 'error': msg}

    chat_ids = [cid.strip() for cid in chat_ids_raw.split(',') if cid.strip()]

    semester = get_semester()
    if not semester:
        msg = 'Канікули — звіт не потрібен'
        logger.info(f"Moodle reporter: {msg}")
        send_telegram(f'📚 НУБіП — {msg}', bot_token, chat_ids)
        return {'success': True, 'report': msg, 'error': None}

    logger.info(f"Moodle reporter: {semester['label']}")

    try:
        # Отримуємо токен
        if saved_token:
            logger.info("Moodle reporter: використовуємо збережений токен")
            token = saved_token
        else:
            token = get_moodle_token(email, password, moodle_url)
            if not token:
                raise Exception('Не вдалося отримати Moodle API token')

        # Отримуємо курси
        courses = get_courses(token, moodle_url)
        if not courses:
            send_telegram('Курсів не знайдено', bot_token, chat_ids)
            return {'success': True, 'report': 'Курсів не знайдено', 'error': None}

        course_data = []
        for c in courses:
            if c.get('hidden'):
                continue
            items = []

            # Завдання
            assignments = get_assignments(c['id'], token, moodle_url)
            for a in assignments:
                item = classify(a, semester, False)
                if classify_item(item) == 'hide':
                    continue
                if is_submitted(a['id'], token, moodle_url):
                    continue
                items.append(item)

            # Тести
            try:
                quizzes = get_quizzes(c['id'], token, moodle_url)
                for q in quizzes:
                    items.append(classify(q, semester, True))
            except Exception:
                pass

            if items:
                course_data.append({
                    'name': trim_name(c.get('fullname', '')),
                    'items': items,
                })

        report_text = build_report(course_data)
        logger.info(f"Moodle reporter: звіт готовий, надсилаємо в Telegram...")
        send_telegram(report_text, bot_token, chat_ids)

        return {'success': True, 'report': report_text, 'error': None}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Moodle reporter: помилка — {error_msg}")
        try:
            send_telegram(f'❌ НУБіП — помилка: {error_msg}', bot_token, chat_ids)
        except Exception:
            pass
        return {'success': False, 'report': '', 'error': error_msg}
