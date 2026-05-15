#!/usr/bin/env python3
"""
Тест для діагностики: перевіряє курс "Автоматизація виробничих процесів"
і показує всі лабораторні з їхнім статусом submission.
"""
import sys
import json
sys.path.insert(0, '/home/openclaw/mysite')

from blueprints.moodle_session.config_manager import load_config
from blueprints.moodle_session.moodle_reporter import (
    get_moodle_token, get_courses, get_assignments,
    moodle_api_call, classify, classify_item, is_submitted,
    get_semester, abbreviate_name, extract_number,
    _item_name_with_deadline,
)
from loguru import logger
logger.remove()  # відключаємо loguru для чистоти виводу


def debug_course(course_name_substring="Автоматизація"):
    config = load_config()
    email = config.get('moodle_email', '')
    password = config.get('moodle_password', '')
    moodle_url = config.get('moodle_url', 'https://elearn.nubip.edu.ua').rstrip('/')
    saved_token = config.get('moodle_token', '')

    if saved_token:
        token = saved_token
        print("Використовуємо збережений токен")
    else:
        print("Отримуємо новий токен через Playwright...")
        token = get_moodle_token(email, password, moodle_url)
        if not token:
            print("❌ Не вдалося отримати токен!")
            return
        print("✅ Токен отримано")

    semester = get_semester()
    print(f"\nСеместр: {semester['label'] if semester else 'None (канікули)'}")

    print(f"\n=== Отримуємо курси ===")
    courses = get_courses(token, moodle_url)
    target_course = None
    for c in courses:
        name = c.get('fullname', '')
        if course_name_substring.lower() in name.lower():
            target_course = c
            print(f"  Знайдено курс: [{c['id']}] {name}")
    if not target_course:
        print(f"❌ Курс '{course_name_substring}' не знайдено!")
        print("Доступні курси:")
        for c in courses:
            print(f"  [{c['id']}] {c.get('fullname', '?')}")
        return

    print(f"\n=== Отримуємо завдання для курсу [{target_course['id']}] {target_course.get('fullname', '')} ===")
    assignments = get_assignments(target_course['id'], token, moodle_url)
    print(f"Всього завдань: {len(assignments)}")
    print()

    results = []
    for a in assignments:
        name = a.get('name', '?')
        aid = a.get('id', '?')
        duedate = a.get('duedate', 0)
        allowsub = a.get('allowsubmissionsfromdate', 0)
        cmid = a.get('cmid', '?')

        item = classify(a, semester, is_test=False)
        cls = classify_item(item)

        # Перевірка submission статусу
        sub_status = None
        sub_detail = None
        try:
            data = moodle_api_call(token, 'mod_assign_get_submission_status', moodle_url, {'assignid': aid})
            lastattempt = data.get('lastattempt', {})
            submission = lastattempt.get('submission', {})
            sub_status = submission.get('status', '(none)')
            sub_id = submission.get('id')
            # Перевіряємо feedback
            fb = data.get('feedback', {})
            fb_grade = fb.get('grade') if isinstance(fb, dict) else None
            grading = lastattempt.get('grading')
            sub_detail = {
                'status': sub_status,
                'has_submission_id': bool(sub_id),
                'has_feedback_grade': bool(fb_grade) if fb_grade else False,
                'has_grading': bool(grading),
            }
        except Exception as e:
            sub_detail = {'error': str(e)}

        abbr = abbreviate_name(name)
        num = extract_number(name)

        results.append({
            'id': aid,
            'name': name,
            'abbr': abbr,
            'num': num,
            'duedate': duedate,
            'classify': cls,
            'submission': sub_detail,
            'hasDeadline': item['hasDeadline'],
            'deadlineDays': item['deadlineDays'],
        })

    # Вивід
    print(f"{'ID':>5} | {'Назва':40s} | {'Скор.':10s} | {'№':6s} | {'Клас':10s} | {'Статус':15s} | {'Дедлайн':8s} | Деталі")
    print("-" * 110)
    for r in results:
        status_str = r['submission'].get('status', r['submission'].get('error', '?')) if isinstance(r['submission'], dict) else '?'
        dl_str = f"{int(r['deadlineDays'])}д" if r['hasDeadline'] else '-'
        details = ""
        if isinstance(r['submission'], dict):
            sd = r['submission']
            if 'error' in sd:
                details = f"⚠️ {sd['error']}"
            else:
                if sd.get('has_feedback_grade'):
                    details += " ✅оцінено"
                if sd.get('has_grading'):
                    details += " 📝перевірено"
                if sd.get('status') == 'submitted':
                    details += " ✔️здано"
        print(f"{r['id']:>5} | {r['name']:40s} | {r['abbr']:10s} | {r['num']:6s} | {r['classify']:10s} | {status_str:15s} | {dl_str:8s} | {details}")

    # Підрахунок
    submitted_but_shown = [r for r in results if r['classify'] != 'hide' and r['submission'].get('status') == 'submitted']
    graded_but_shown = [r for r in results if r['classify'] != 'hide' and r['submission'].get('has_feedback_grade')]
    print(f"\n=== Аналіз ===")
    print(f"Всього завдань: {len(results)}")
    print(f"Показуються у звіті: {len([r for r in results if r['classify'] != 'hide'])}")
    print(f"З них 'submitted' але показуються: {len(submitted_but_shown)}")
    print(f"З них мають оцінку але показуються: {len(graded_but_shown)}")

    if submitted_but_shown:
        print(f"\n⚠️ Завдання зі статусом 'submitted' які все ще показуються:")
        for r in submitted_but_shown:
            print(f"  {r['abbr']:10s} (id={r['id']}) — submission: {r['submission']}")

    if graded_but_shown:
        print(f"\n⚠️ Завдання з оцінкою які все ще показуються:")
        for r in graded_but_shown:
            print(f"  {r['abbr']:10s} (id={r['id']})")


if __name__ == '__main__':
    debug_course()
