#!/usr/bin/env python3
"""
Test script: extract one task from a sample dataset and print all its properties with brief explanations.
"""

from datetime import datetime


def get_task_by_id(tasks, task_id):
    for t in tasks:
        if t.get("id") == task_id:
            return t
    return None


def explain_properties(task):
    explanations = {
        "id": "Унікальний ідентифікатор завдання в системі.",
        "title": "Коротка назва завдання.",
        "description": "Детальний опис завдання.",
        "status": "Поточний стан завдання (наприклад: todo, in_progress, done).",
        "priority": "Важливість/терміновість завдання (low, medium, high).",
        "due_date": "Троки виконання; дата через яку завдання має бути завершене.",
        "assignee": "Користувач, відповідальний за виконання.",
        "tags": "Додаткові категорії або ключові слова для фільтрації.",
    }
    lines = []
    for key in task.keys():
        val = task[key]
        expl = explanations.get(key, "Значення завдання.")
        lines.append(f"{key}: {val}  # {expl}")
    return lines


def main():
    # Приклад тестових даних завдань
    tasks = [
        {
            "id": 101,
            "title": "Витягнути одне завдання з бази",
            "description": "Показати всі властивості вибраного завдання з прикладу.",
            "status": "todo",
            "priority": "high",
            "due_date": (datetime.utcnow().date().isoformat()),
            "assignee": "tester",
            "tags": ["sample", "testing"],
        }
    ]

    task = get_task_by_id(tasks, 101)
    if not task:
        print("Завдання не знайдено.")
        return

    print("Знайдено завдання:")
    for k, v in task.items():
        print(f"{k}: {v}")

    print("\nПояснення властивостей:")
    for line in explain_properties(task):
        print(line)


if __name__ == "__main__":
    main()

