"""
Celery tasks for Moodle Session Blueprint.
Містить задачу для запуску звіту та періодичну перевірку розкладу.
"""

from datetime import datetime
from celery import shared_task
from loguru import logger

from .moodle_reporter import run_report
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
    Запускається щохвилини, але реально виконує звіт тільки в заданий час.
    """
    config = load_config()

    if not config.get("schedule_enabled"):
        return {"skipped": True, "reason": "Schedule disabled"}

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    last_run = config.get("last_run", "")

    # Перевіряємо чи вже запускали сьогодні
    if last_run and last_run.startswith(today_str):
        return {"skipped": True, "reason": "Already ran today"}

    target_time = config.get("schedule_time", "08:00")
    try:
        target_hour, target_min = map(int, target_time.split(":"))
    except (ValueError, TypeError):
        logger.error(f"Celery beat: некоректний час розкладу: {target_time}")
        return {"skipped": True, "reason": f"Invalid schedule_time: {target_time}"}

    if now.hour == target_hour and now.minute == target_min:
        logger.info(f"Celery beat: настав час запуску ({target_time}), запускаємо звіт")
        result = run_report(config)
        save_config({
            "last_run": now.isoformat(),
            "last_run_status": "success" if result.get("success") else "error",
        })
        return result

    return {"skipped": True, "reason": "Not the right time"}
