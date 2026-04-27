#!/usr/bin/env python3
"""
Celery Worker Setup for Doc Generator Service
Run with: celery -A celery_worker worker --loglevel=info
"""
from celery import Celery
from dotenv import load_dotenv
import os

load_dotenv()

# Celery configuration
celery_app = Celery(
    'doc_generator',
    broker=os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'),
    include=['blueprints.doc_generator.routes']
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    worker_prefetch_multiplier=1,
)

if __name__ == '__main__':
    celery_app.start()
