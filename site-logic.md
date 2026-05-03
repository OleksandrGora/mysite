Як працює сайт загалом
Головний Flask-додаток у app.py реєструє 3 blueprint-сервіси: converter, doc_generator, pptx_generator.
Перед доступом до сервісів іде Google OAuth + whitelist email (ALLOWED_EMAILS), сесія зберігає email.
Для важких задач документації використовується Celery (celery_worker.py + celery.service) через Redis.
На головній сторінці (/) відображаються саме 3 сервіси:
Конвертер LaTeX → Google Doc, Microsoft Word
Генератор документів
Генератор PowerPoint
Сервіс 1: converter (/converter)
Що робить: перетворює markdown/текст у .docx, далі або дає файл на скачування, або заливає в Google Drive як Google Doc.

Ключові функції (вхід → вихід):

preprocess(text: str) -> str

Вхід: сирий текст (із форми або файлу)
Вихід: очищений/нормалізований markdown (заміни LaTeX-команд, тире, списки, формули тощо)
run_pandoc(src: str, dst: str) -> tuple[bool, list[str], str|None]

Вхід: шлях до вхідного .md, шлях до цільового .docx
Вихід: (success, warnings, error_text) після виклику pandoc
postprocess_docx(path: str) -> None

Вхід: шлях до згенерованого .docx
Вихід: файл модифіковано на місці (стилі, таблиці, відступи)
get_drive_service() -> googleapiclient.discovery.Resource

Вхід: токен у session['token']
Вихід: авторизований клієнт Google Drive API
get_or_create_folder(drive, folder_name: str) -> str

Вхід: Drive client + ім’я папки
Вихід: folder_id
converter() (роут GET/POST /converter)

Вхід (POST): text_input або файл, action=download|...
Вихід: HTML з лінком/помилкою або send_file(.docx)
Сервіс 2: doc_generator (/doc-generator)
Що робить: таск-орієнтована генерація документів (аналіз шаблону, генерація контенту, фінальний .docx) через API + Celery.

Ключові API точки:

POST /api/task

Вхід: JSON з template_file, analysis_prompt, force
Вихід: task_id, шлях/назва task-папки
POST /api/tasks/<task_id>/analysis

Вхід: analysis_prompt
Вихід: запуск Celery analyze_document_task, повертає celery_task_id
POST /api/tasks/<task_id>/generate

Вхід: generation_prompt, список source_files
Вихід: запуск Celery generate_final_document_task
GET /api/tasks/<task_id>/download

Вхід: task_id
Вихід: готовий .docx (якщо status=completed)
Ключові функції (вхід → вихід):

create_task_info(...) -> dict

Вхід: task metadata
Вихід: структура task-info.json
save_task_info(task_folder, task_info) -> None / load_task_info(task_folder) -> dict|None

analyze_document_task(...) -> dict (Celery)

Вхід: task_id, folder_path, analysis_prompt
Вихід: збережений analysis.txt, оновлений статус task
generate_final_document_task(...) -> dict (Celery)

Вхід: task_id, folder_path, generation_prompt, source_files
Вихід: фінальний .docx у task-папці + status=completed
build_document_from_analysis(...) -> str

Вхід: analysis_text + parsed sources
Вихід: шлях до тимчасового .docx
Сервіс 3: pptx_generator (/pptx-generator)
Що робить: генерує презентацію з JSON, підтримує автопоправку JSON через LLM, генерацію зображень (Gemini/OpenAI), стрімить прогрес через SSE.

Ключові функції (вхід → вихід):

generate_pptx() (роут POST /api/generate)

Вхід: body (JSON або текст), query: provider, generate_images
Вихід: text/event-stream (status/error/done), у done повертає file_b64
fix_json_with_llm(raw_text, provider='google') -> tuple[list|None, dict|None]

Вхід: невалідний JSON/текст
Вихід: валідний масив слайдів + токени
generate_image_with_google(...) -> tuple[str|None, dict|None]

Вхід: description, output_dir, slide_index
Вихід: шлях до PNG або None + токени
generate_image_with_openai(...) -> tuple[str|None, dict|None]

Вхід/вихід: аналогічно Gemini-варіанту
create_presentation(filename, slides_data, images_dir=None) -> str

Вхід: фінальні дані слайдів
Вихід: збережений .pptx за filename
validate_json() (POST /api/validate)

Вхід: raw body
Вихід: valid/errors/warnings/slide_count, опційно fixed_data
Що відслідкував по “старому коду” (legacy)
Є явний legacy-шар у doc_generator:

Папка blueprints/doc_generator/old_architecture/ містить:
planner.py
cleanup.py
rag/*
У routes.py є try/except ImportError на старі модулі (.reasoning.planner, .rag.*, .cleanup) — тобто код уже частково мігрований і тримає backward-compatible гілки.
Також є backup-версія blueprints/pptx_generator/routes.py.backup (старий варіант без SSE потоку, з тестовими заглушками).
Ризики/ознаки техборгу, які видно одразу:

У reasoning/generator.py у build_table_prompt() використовується table_name, але змінна не визначена (ймовірний runtime bug, якщо цей шлях буде викликаний).
Частина “old architecture” коду присутня, але частина імпортів уже не збігається з реальною структурою файлів.
