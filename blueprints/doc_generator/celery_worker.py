#!/usr/bin/env python3
"""
Celery Worker Setup for Doc Generator Service
Run with: celery -A celery_worker worker --loglevel=info
"""
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv
import os

load_dotenv()

# Celery configuration
celery_app = Celery(
    'doc_generator',
    broker=os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'),
    include=[
        'blueprints.doc_generator.routes',
        'blueprints.doc_generator.source_processor',
        'blueprints.moodle_session.celery_tasks',  # Moodle Assignment Reporter
    ]
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Europe/Kyiv',
    enable_utc=False,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    worker_prefetch_multiplier=1,
)

# Beat schedule — щохвилинна перевірка розкладу Moodle звіту
celery_app.conf.beat_schedule = {
    'check-moodle-schedule': {
        'task': 'blueprints.moodle_session.celery_tasks.check_and_run_scheduled_report',
        'schedule': crontab(minute='*'),  # щохвилини
    },
}

if __name__ == '__main__':
    celery_app.start()
