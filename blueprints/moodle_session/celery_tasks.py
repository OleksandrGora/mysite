"""
Celery tasks for Moodle Session Blueprint.
Містить задачу для запуску звіту та періодичну перевірку розкладу.
"""

from datetime import datetime
from celery import shared_task
from loguru import logger

from .moodle_reporter import (
    run_report, get_courses, get_quizzes, get_assignments, is_item_open,
    detect_type, send_telegram, fmt_date, get_moodle_token,
    classify, classify_item, is_quiz_attempted, is_submitted,
    get_semester, _should_skip,
)
from .config_manager import load_config, save_config


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def report_assignments_task(self):
    """
    Celery задача: запускає звіт із поточних налаштувань конфігу.
    """
    logger.info("Celery task: запуск report_assignments_task")
    config = load_config()
    result = run_report(config)
    save_config({
        "last_run": datetime.now().isoformat(),
        "last_run_status": "success" if result.get("success") else "error",
    })
    logger.info(f"Celery task: завершено, success={result.get('success')}")
    return result


@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def check_and_run_scheduled_report(self):
    """
    Celery Beat задача: перевіряє чи настав час для щоденного звіту.
    Запускається щохвилини, виконує звіт коли настав запланований час.
    
    Використовує окремий лічильник last_scheduled_date щоб ручні запуски
    через веб-інтерфейс не блокували автоматичний запуск за розкладом.
    """
    config = load_config()

    if not config.get("schedule_enabled"):
        return {"skipped": True, "reason": "Schedule disabled"}

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # Перевіряємо чи вже запускались за розкладом сьогодні
    # Використовуємо last_scheduled_date (не last_run, який оновлюється і ручними запусками)
    last_scheduled_date = config.get("last_scheduled_date", "")
    if last_scheduled_date == today_str:
        return {"skipped": True, "reason": "Already ran by schedule today"}

    target_time = config.get("schedule_time", "08:00")
    try:
        target_hour, target_min = map(int, target_time.split(":"))
    except (ValueError, TypeError):
        logger.error(f"Celery beat: некоректний час розкладу: {target_time}")
        return {"skipped": True, "reason": f"Invalid schedule_time: {target_time}"}

    # Перевіряємо чи настав (або вже минув) час запуску
    # Використовуємо >= а не ==, щоб не пропустити запуск якщо worker
    # було перезапущено після запланованого часу
    if now.hour > target_hour or (now.hour == target_hour and now.minute >= target_min):
        logger.info(f"Celery beat: настав час запуску ({target_time}), запускаємо звіт")
        # Зберігаємо last_scheduled_date ДО запуску звіту, щоб уникнути
        # дублювання: якщо звіт виконується довше 1 хвилини, наступний
        # тік beat-а побачить що сьогодні вже запускались і пропустить.
        save_config({"last_scheduled_date": today_str})
        result = run_report(config)
        save_config({
            "last_run": now.isoformat(),
            "last_run_status": "success" if result.get("success") else "error",
        })
        return result

    return {"skipped": True, "reason": "Not the right time"}


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def check_new_open_tests_task(self):
    """
    Celery Beat задача: перевіряє нові відкриті тести та завдання (other type).
    Запускається щогодини, відправляє Telegram сповіщення про нововідкриті елементи.
    """
    logger.info("Celery task: запуск check_new_open_tests_task")
    config = load_config()

    email = config.get('moodle_email', '')
    password = config.get('moodle_password', '')
    moodle_url = config.get('moodle_url', 'https://elearn.nubip.edu.ua').rstrip('/')
    saved_token = config.get('moodle_token', '')
    bot_token = config.get('telegram_bot_token', '')
    chat_ids_raw = config.get('telegram_chat_ids', '')

    if not bot_token or not chat_ids_raw:
        logger.warning("Celery task: Telegram не налаштовано, пропускаємо")
        return {"skipped": True, "reason": "Telegram not configured"}

    chat_ids = [cid.strip() for cid in chat_ids_raw.split(',') if cid.strip()]

    # Семестр
    semester = get_semester()
    if not semester:
        logger.info("Celery task: канікули, пропускаємо")
        return {"skipped": True, "reason": "Holidays"}

    # Отримуємо токен
    if saved_token:
        token = saved_token
    else:
        token = get_moodle_token(email, password, moodle_url)
        if not token:
            msg = "Не вдалося отримати Moodle API token"
            logger.error(f"Celery task: {msg}")
            return {"success": False, "error": msg}

    # Завантажуємо попередній стан
    state = config.get('test_notification_state', {})
    seen_items = state.get('seen_items', {})

    current_items = {}
    newly_opened = []

    try:
        courses = get_courses(token, moodle_url)
    except Exception as e:
        logger.error(f"Celery task: помилка отримання курсів: {e}")
        return {"success": False, "error": str(e)}

    now_ts = datetime.now().timestamp()

    for c in courses:
        if c.get('hidden'):
            continue

        course_id = c['id']
        course_name = c.get('fullname', '')

        # Quizzes (all are tests)
        try:
            quizzes = get_quizzes(course_id, token, moodle_url)
            for q in quizzes:
                name = q.get('name', '')

                # Skip "навчальні" tests
                if 'навчальн' in name.lower():
                    continue

                # Skip "самоконтроль" tests
                if _should_skip(q):
                    continue

                # Classify and filter
                item = classify(q, semester, True)
                if classify_item(item) == 'hide':
                    continue

                # Skip if already attempted
                if is_quiz_attempted(q['id'], token, moodle_url):
                    continue

                # Check if currently open using item timing
                time_open = item.get('timeOpen')
                time_close = item.get('timeClose')
                item_open = (
                    (time_open is None or time_open <= now_ts)
                    and (time_close is None or time_close > now_ts)
                )

                item_id = q['id']
                key = f"{course_id}_{item_id}"

                current_items[key] = {
                    'course_id': course_id,
                    'item_id': item_id,
                    'course_name': course_name,
                    'item_name': name,
                    'item_type': 'quiz',
                    'time_close': time_close or 0,
                    'was_open': item_open,
                }

                prev = seen_items.get(key)
                if item_open and (prev is None or not prev.get('was_open')):
                    newly_opened.append(current_items[key])
        except Exception as e:
            logger.warning(f"Celery task: помилка отримання тестів для курсу {course_id}: {e}")

        # Assignments — only 'other' type
        try:
            assignments = get_assignments(course_id, token, moodle_url)
            for a in assignments:
                if detect_type(a.get('name', '')) != 'other':
                    continue

                # Classify and filter
                item = classify(a, semester, False)
                if classify_item(item) == 'hide':
                    continue

                # Skip if nosubmissions
                if a.get('nosubmissions'):
                    continue

                # Skip if already submitted
                if is_submitted(a['id'], token, moodle_url):
                    continue

                # Check if currently open using item timing
                time_open = item.get('timeOpen')
                time_close = item.get('timeClose')
                item_open = (
                    (time_open is None or time_open <= now_ts)
                    and (time_close is None or time_close > now_ts)
                )

                item_id = a['id']
                key = f"{course_id}_{item_id}"

                current_items[key] = {
                    'course_id': course_id,
                    'item_id': item_id,
                    'course_name': course_name,
                    'item_name': a.get('name', ''),
                    'item_type': 'assignment',
                    'time_close': time_close or 0,
                    'was_open': item_open,
                }

                prev = seen_items.get(key)
                if item_open and (prev is None or not prev.get('was_open')):
                    newly_opened.append(current_items[key])
        except Exception as e:
            logger.warning(f"Celery task: помилка отримання завдань для курсу {course_id}: {e}")

    # Зберігаємо оновлений стан (видаляємо елементи, яких більше немає)
    save_config({
        'test_notification_state': {
            'seen_items': current_items,
        }
    })

    if not newly_opened:
        logger.info("Celery task: нових відкритих елементів немає")
        return {"success": True, "new_items": 0}

    # Формуємо повідомлення
    lines = ['🔔 Нові відкриті тести/завдання:']
    for item in newly_opened:
        tc = item.get('time_close')
        if tc and tc > 0:
            dt = datetime.fromtimestamp(tc)
            lines.append(f"{item['course_name']}, {item['item_name']} відкрито до {fmt_date(dt)}")
        else:
            lines.append(f"{item['course_name']}, {item['item_name']} відкрито (без дедлайну)")

    message = '\n'.join(lines)
    logger.info(f"Celery task: надсилаємо сповіщення про {len(newly_opened)} нових елементів")
    send_telegram(message, bot_token, chat_ids)

    return {"success": True, "new_items": len(newly_opened)}
