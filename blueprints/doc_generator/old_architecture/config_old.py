"""
OLD ARCHITECTURE - Prompts that are no longer used in the new workflow.

The new architecture uses:
- ANALYSIS_PROMPT: For template structure analysis
- FULL_DOCUMENT_GENERATION_PROMPT: For one-pass document generation

These prompts were used for section-by-section generation which has been deprecated.
"""

# Old prompts for section-by-section generation
SECTION_GENERATION_PROMPT = """Відтвори текст секції документа на основі наданих джерел українською мовою.

СЕКЦІЯ: {section_name}
ОПИС: {section_description}

ДЖЕРЕЛА:
{sources}

Вимоги:
- Використовуй тільки інформацію з джерел
- Зберігай структуру та стиль оригінального документа
- Якщо в джерелах немає інформації — напиши "Дані відсутні"
- Відповідь українською мовою"""

TABLE_GENERATION_PROMPT = """Згенеруй дані для таблиці на основі джерел.

НАЗВА ТАБЛИЦІ: {table_name}
СТОВПЦІ: {columns}

ДЖЕРЕЛА:
{sources}

Вимоги:
- Заповни таблицю даними з джерел
- Якщо даних немає — залиште порожнім або напишіть "—"
- Відповідь українською мовою"""

# Old prompts for planner module
PLANNER_ANALYSIS_PROMPT = """Ти — експерт з аналізу структури документів.

Проаналізуй структуру шаблону та джерел.

ШАБЛОН:
{template_structure}

ДЖЕРЕЛА:
{sources_structure}

Визнач які секції та таблиці потрібно заповнити даними з джерел."""

PLANNER_GENERATION_PROMPT = """Ти — експерт з аналізу документів і створення планів генерації.

Твоє завдання: проаналізувати шаблон документа та вміст джерел, і створити детальний план для генерації нового документа.

## ШАБЛОН ДОКУМЕНТА

Заголовок шаблону: {template_title}

Секції шаблону (рівень вкладеності [X]):
{sections_text}

Таблиці в шаблоні:
{tables_text}

## ДЖЕРЕЛА ДАНИХ

Загальний обсяг тексту: {total_chars} символів

Джерела:
{sources_text}

## ЗАВДАННЯ

Створи JSON план (DocumentPlan) який визначає:

1. **document_title** — згенеруй відповідну назву документа на основі шаблону і джерел
2. **document_type** — тип документа (report, letter, act, contract, etc.)
3. **target_audience** — хто буде читати цей документ
4. **overall_tone** — загальний тон (formal, business, technical, legal)
5. **sections** — для кожної секції шаблону:
   - content_type: що це за частина (introduction, body, conclusion, summary)
   - key_points: ключові моменти які тут мають бути (3-5 пунктів)
   - tone: тон для цієї секції
   - max_length_words: приблизна кількість слів (100-500)
   - source_references: які джерела використати

6. **tables** — для кожної таблиці:
   - purpose: що має бути в таблиці
   - estimated_rows: скільки рядків потрібно (1-20)
   - data_source_hint: звідки брати дані

7. **sources_summary** — короткий опис кожного джерела у форматі словника: {{"filename.pdf": "опис джерела", ...}}

ВАЖЛИВО:
- Всі тексти мають бути українською мовою (діловий стиль)
- План має бути реалістичним — не більше 3-5 рядків в таблиці, 100-300 слів в секції
- Якщо даних в джерелах не вистачає — вкажи це в notes
- Поверни ТІЛЬКИ JSON, без додаткових пояснень"""

# Old architecture prompt for pre-planning LLM analysis
LLM_ANALYSIS_PROMPT = """Проаналізуй структуру шаблону та джерел.

Шаблон має {num_sections} секцій та {num_tables} таблиць.

Користувацький запит: {user_prompt}

Поверни детальний план заповнення кожної секції та таблиці даними з джерел."""
