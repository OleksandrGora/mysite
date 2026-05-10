#!/usr/bin/env python3
"""
Live test: витяг одного завдання з Moodle через moodle_session API.
Цей скрипт очікує конфігурацію в blueprints/moodle_session/config_manager.py
та наявність функцій для запиту Moodle API.
"""

from blueprints.moodle_session.config_manager import load_config
from blueprints.moodle_session.moodle_reporter import get_assignments, moodle_api_call
from typing import Optional


def fetch_one_live_task(course_id: int, token: str, moodle_url: str) -> Optional[dict]:
    # Реальний приклад: отримати завдання для курсу та повернути перше
    try:
        data = moodle_api_call(token, 'mod_assign_get_assignments', moodle_url, {'courseids[0]': course_id})
        courses = data.get('courses', [])
        if courses and courses[0]:
            assignments = courses[0].get('assignments', [])
            return assignments[0] if assignments else None
    except Exception:
        return None
    return None


def main():
    cfg = load_config()
    token = cfg.get('moodle_token') or cfg.get('moodle_password')
    moodle_url = cfg.get('moodle_url', 'https://moodle.example.edu')
    course_id = int(cfg.get('last_course_id', 1))

    task = fetch_one_live_task(course_id, token, moodle_url)
    print("Live task:", task)


if __name__ == '__main__':
    main()

