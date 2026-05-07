# RAG Module - Rollback Guide

Якщо прийнято рішення згорнути RAG-функціонал, виконайте наступні дії:

## 1. Видалити нові файли

```bash
# Нова папка rag/
rm -rf blueprints/doc_generator/rag/

# ChromaDB дані (якщо потрібно повністю очистити)
rm -rf blueprints/doc_generator/data/chroma_db/
```

## 2. Відкотити зміни в існуючих файлах

### `blueprints/doc_generator/routes.py`
- Видалити:
  - Параметр `use_rag` в `generate_document()` (POST /api/tasks/<task_id>/generate)
  - Параметр `chunk_params`
  - Умову `if use_rag:` в `generate_final_document_task()`
  - Імпорт `generate_document_rag` якщо додано

### `blueprints/doc_generator/reasoning/generator.py`
- Видалити `RAG_DOCUMENT_GENERATION_PROMPT` якщо додано
- Видалити `build_rag_prompt()` якщо додано

### `blueprints/doc_generator/templates/doc_generator/index-doc-generator.html`
- Видалити:
  - Чекбокс `#use-rag`
  - Панель параметрів `#rag-params`
  - JavaScript код що передає `use_rag` та `chunk_params`

### `blueprints/doc_generator/config.py`
- Видалити RAG-конфігурацію якщо додано

## 3. Зупинити сервіси (якщо запускали)

```bash
# Зупинити Ollama (якщо запускали окремо)
sudo systemctl stop ollama
# або
pkill ollama

# Видалити модель nomic-embed-text
ollama rm nomic-embed-text
```

## 4. Видалити залежності (опціонально)

```bash
pip uninstall chromadb -y
```

## 5. Перевірити що залишилось незмінним

- `blueprints/doc_generator/reasoning/generator.py` — функція `generate_document_full()` має працювати без змін
- `blueprints/doc_generator/routes.py` — `generate_final_document_task()` має працювати в FULL режимі
- `blueprints/doc_generator/templates/doc_generator/index-doc-generator.html` — генерація без RAG має працювати

## Остаточна перевірка

```bash
# Перевірити що немає згадок RAG в коді
grep -r "use_rag\|generate_document_rag\|rag_" blueprints/doc_generator/routes.py
grep -r "RAG_DOCUMENT\|build_rag_prompt" blueprints/doc_generator/reasoning/generator.py
grep -r "use-rag\|rag-params" blueprints/doc_generator/templates/
```
