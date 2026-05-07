"""
Configuration for document generator service.
"""
import os
import json
from dotenv import load_dotenv

load_dotenv()


# LLM Configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
REASONING_MODEL = os.getenv('REASONING_MODEL', 'gemini-2.5-pro')
EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'models/text-embedding-004')

# Available Models (hardcoded list)
AVAILABLE_MODELS = [
    'gemini-2.5-pro',
    'gemini-2.5-flash',
    'gemini-2.5-flash-lite',
    'gpt-5-nano',
    'deepseek-v4-flash'
]

# Model settings file path
MODEL_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'model_settings.json')

# Generation Settings
MAX_TOKENS_PER_SECTION = 4000
MAX_TOKENS_FOR_TABLE = 2000
TEMPERATURE = 0.3  # Lower for more consistent output

# Model Provider Detection
def is_gemini_model(model_name: str) -> bool:
    """Check if model is from Google Gemini."""
    return model_name.startswith('gemini-')

def is_openai_model(model_name: str) -> bool:
    """Check if model is from OpenAI."""
    return model_name.startswith('gpt-')

def is_deepseek_model(model_name: str) -> bool:
    """Check if model is from DeepSeek."""
    return model_name.startswith('deepseek-')

def get_model_provider(model_name: str) -> str:
    """Get the provider name for the model."""
    if is_gemini_model(model_name):
        return 'gemini'
    elif is_openai_model(model_name):
        return 'openai'
    elif is_deepseek_model(model_name):
        return 'deepseek'
    else:
        return 'unknown'


def needs_max_completion_tokens(model_name: str) -> bool:
    """
    Check if the model requires 'max_completion_tokens' instead of 'max_tokens'.
    
    Newer OpenAI models (GPT-4o, GPT-5, o-series) deprecated 'max_tokens'
    and require 'max_completion_tokens' instead.
    """
    model_lower = model_name.lower()
    # Models that require max_completion_tokens:
    # - gpt-4o* (gpt-4o, gpt-4o-mini, gpt-4o-audio, etc.)
    # - gpt-4.1* (gpt-4.1, gpt-4.1-mini, gpt-4.1-nano)
    # - gpt-5* (gpt-5, gpt-5-nano, etc.)
    # - o-series (o1, o1-mini, o3, o3-mini, etc.)
    # - chatgpt-* models
    return (
        model_lower.startswith('gpt-4o')
        or model_lower.startswith('gpt-4.1')
        or model_lower.startswith('gpt-5')
        or model_lower.startswith('o1')
        or model_lower.startswith('o3')
        or model_lower.startswith('chatgpt-')
    )


def _is_temperature_restricted_model(model_name: str) -> bool:
    """
    Check if the model only supports temperature=1 (the default).
    
    Newer models like GPT-5 and o-series only accept the default
    temperature value (1.0) and reject custom values.
    """
    model_lower = model_name.lower()
    return (
        model_lower.startswith('gpt-5')
        or model_lower.startswith('o1')
        or model_lower.startswith('o3')
        or model_lower.startswith('chatgpt-')
    )


def get_openai_completion_kwargs(model_name: str, max_tokens: int, temperature: float) -> dict:
    """
    Get kwargs for OpenAI/DeepSeek chat.completions.create, handling compatibility:
    - 'max_tokens' vs 'max_completion_tokens' parameter naming
    - Temperature restrictions on newer models
    
    Args:
        model_name: The model identifier
        max_tokens: Desired max tokens / max completion tokens value
        temperature: Temperature value
        
    Returns:
        Dict of kwargs to pass to chat.completions.create
    """
    kwargs = {}
    
    # DeepSeek: uses standard OpenAI-compatible API (max_tokens + temperature)
    if is_deepseek_model(model_name):
        kwargs['temperature'] = temperature
        kwargs['max_tokens'] = max_tokens
        return kwargs
    
    # Temperature: skip for models that only support default (1.0),
    # otherwise pass the custom value
    if not _is_temperature_restricted_model(model_name):
        kwargs['temperature'] = temperature
    
    # Max tokens:
    # - Reasoning models (gpt-5*, o1*, o3*): max_completion_tokens includes
    #   internal reasoning tokens. Setting a small value causes empty output
    #   because all tokens are consumed by reasoning. Better to NOT set it
    #   and let the model use its own default allocation.
    # - Other new models (gpt-4o*, gpt-4.1*): use max_completion_tokens
    # - Legacy models: use max_tokens
    if _is_temperature_restricted_model(model_name):
        # Reasoning models: skip max_completion_tokens to avoid empty responses
        pass
    elif needs_max_completion_tokens(model_name):
        kwargs['max_completion_tokens'] = max_tokens
    else:
        kwargs['max_tokens'] = max_tokens
    
    return kwargs

# Feature Flags
USE_RAG = os.getenv('USE_RAG', 'false').lower() == 'true'

# Prompt Templates
ANALYSIS_PROMPT = """Проаналізуй структуру документа: виведи розділи, підрозділи та таблиці у порядку розташування.

ФОРМАТ ВІДПОВІДІ — виключно JSON масив об'єктів, без markdown блоків (без ```):

Для текстових розділів:
{{
  "id": N,
  "type": "text",
  "title": "назва розділу",
  "level": рівень,
  "words": "від X до Y слів",
  "description": {{
    "general": "загальний опис призначення розділу",
    "specific": "конкретний опис з документа"
  }}
}}

Для таблиць:
{{
  "id": N,
  "type": "table",
  "title": "назва таблиці",
  "level": рівень,
  "rows": кількість_рядків,
  "columns": ["стовпець 1", "стовпець 2", "стовпець 3"],
  "first_column_data": ["дані1", "дані2", "дані3"]
}}

ПРАВИЛА АНАЛІЗУ:

РІВНІ: [0] головний заголовок, [1] розділ, [2] підрозділ, [3][4] далі за вкладеністю. Таблиця — на рівень глибше розділу де знаходиться.

РОЗМІР РОЗДІЛУ:
- Порахувати кількість слів у тексті розділу (не включаючи підрозділи та таблиці)
- X = round(кількість * 0.85), Y = round(кількість * 1.15)
- Формат: "від X до Y слів"

ОПИС РОЗДІЛУ:
- Загальний опис: призначення цього типу розділу без жодних слів з документа, підходить для будь-якої галузі. Починати одразу з дієслова або іменника
- Specific: конкретний зміст з деталями з документа

ТАБЛИЦІ:
- Назва таблиці — це підпис типу 'Таблиця 1', 'Таблиця 2' розташований над або під таблицею. Якщо підпису немає — використати перший рядок шапки як назву
- rows: точна кількість рядків у таблиці (включно з заголовком)
- columns: масив назв усіх стовпців з шапки
- first_column_data: масив даних з першого стовпця (окрім заголовка), максимум 10 перших значень
- Якщо таблиця має однакові стовпці що повторюються — включати лише унікальні назви

ТЕКСТ ДОКУМЕНТА:
{text_content}

Відповідь українською."""

# NOTE: Old architecture prompts moved to old_architecture/config_old.py:
# - SECTION_GENERATION_PROMPT
# - TABLE_GENERATION_PROMPT
# - PLANNER_ANALYSIS_PROMPT
# - PLANNER_GENERATION_PROMPT
# - LLM_ANALYSIS_PROMPT

FULL_DOCUMENT_GENERATION_PROMPT = """Ти — експерт з написання документації українською мовою.

## ЗАВДАННЯ

Згенеруй повний документ в форматі Markdown на основі структури шаблону та наданих джерел.

## ІНСТРУКЦІЇ ДО РОЗДІЛІВ

Кожен елемент структури має поле description.general — це твоя приватна інструкція що писати в цьому розділі. Нижче наведено всі інструкції:

{instructions}

## СТРУКТУРА ДОКУМЕНТА

{structure}

## ДЖЕРЕЛА

{sources}

## ВИМОГИ

1. **Формат Markdown:**
   - Використовуй заголовки (# для рівня 0, ## для рівня 1, ### для рівня 2, тощо) відповідно до level
   - Таблиці в форматі Markdown:
     ```
     | Колонка 1 | Колонка 2 | Колонка 3 |
     |-----------|-----------|-----------|
     | Дані 1    | Дані 2    | Дані 3    |
     ```
   - Списки з `- ` або `1. `
   - Жирний текст з `**текст**`, курсив з `*текст*`

2. **Зміст розділів:**
   - **ЗАГОЛОВОК (title):** Залишай оригінальну назву розділу без змін
   - **ІНСТРУКЦІЇ:** Використовуй інструкції з розділу "ІНСТРУКЦІЇ ДО РОЗДІЛІВ" вище, щоб зрозуміти що саме писати в кожному розділі. Текст інструкцій НЕ включай у документ.
   - **ПРИКЛАД (description → specific):** Це лише приклад формату/стилю. Не копіюй його, використовуй як орієнтир.
   - Якщо в джерелах немає інформації — напиши "Дані відсутні"
   - Зберігай науковий/діловий стиль

3. **Таблиці:**
   - **ЗАГОЛОВОК (title):** Залишай оригінальну назву таблиці без змін (це окремий заголовок перед таблицею)
   - **КОЛОНКИ (columns):** Використовуй вказані колонки для шапки таблиці
   - **Формат таблиці:**
     ```
     | Колонка 1 | Колонка 2 | Колонка 3 |
     |-----------|-----------|-----------|
     | Дані 1    | Дані 2    | Дані 3    |
     ```
   - ПЕРШИЙ РЯДОК таблиці - це ЗАГОЛОВКИ колонок (шапка)
   - Другий рядок - це роздільник `|---|---|---|`
   - Наступні рядки - це дані
   - Заповни таблиці реальними даними з джерел
   - Якщо даних недостатньо — залиш порожні клітинки або напиши "—"

4. **Мова:**
   - Тільки українська мова
   - Діловий/науковий стиль

5. **Структура:**
   - Дотримуйся вказаної ієрархії рівнів (level)
   - Таблиці розміщуй між відповідними розділами
   - Не змінюй порядок елементів

## ВІДПОВІДЬ

Поверни ТІЛЬКИ Markdown документ, без додаткових пояснень.
"""

# Validate API key
def validate_config():
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not set in .env file")


def save_selected_model(model: str):
    """Save selected model to settings file."""
    try:
        with open(MODEL_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'selected_model': model}, f)
    except Exception as e:
        print(f"Failed to save selected model: {e}")


def get_selected_model() -> str:
    """Get selected model from settings file, fallback to REASONING_MODEL env."""
    try:
        if os.path.exists(MODEL_SETTINGS_FILE):
            with open(MODEL_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                model = data.get('selected_model')
                if model and model in AVAILABLE_MODELS:
                    return model
    except Exception as e:
        print(f"Failed to load selected model: {e}")
    return REASONING_MODEL
