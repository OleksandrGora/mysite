import json
import os
import tempfile
import requests
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
from loguru import logger

COMMON_DIR = os.path.join(os.path.dirname(__file__), 'data', 'common')
LLM_CONFIG_PATH = os.path.join(COMMON_DIR, 'llm-config.json')

DEFAULT_SETTINGS = {
    "providers": [
        {
            "id": "openai",
            "type": "openai-responses",
            "name": "OpenAI",
            "apiUrl": "https://api.openai.com/v1/responses",
            "apiKey": "",
            "models": [
                {"apiModel": "gpt-4o-mini-2024-07-18"},
                {"apiModel": "gpt-4o-2024-08-06"},
                {"apiModel": "o1-mini-2024-09-12"}
            ]
        },
        {
            "id": "deepseek",
            "type": "openai-compatible",
            "name": "DeepSeek",
            "apiUrl": "https://api.deepseek.com/chat/completions",
            "apiKey": "",
            "models": [
                {"apiModel": "deepseek-chat"}
            ]
        },
        {
            "id": "gemini",
            "type": "gemini",
            "name": "Google Gemini",
            "apiUrl": "https://generativelanguage.googleapis.com/v1beta/models/",
            "apiKey": "",
            "models": [
                {"apiModel": "gemini-2.0-flash"},
                {"apiModel": "gemini-2.0-pro-exp-02-05"},
                {"apiModel": "gemini-1.5-pro-002"},
                {"apiModel": "gemini-1.5-flash-002"}
            ]
        }
    ],
    "activeProviderId": "openai",
    "activeModelId": "gpt-4o-mini-2024-07-18"
}


def load_llm_config():
    """Load LLM config from file. Returns default settings if file not found."""
    if os.path.exists(LLM_CONFIG_PATH):
        try:
            with open(LLM_CONFIG_PATH, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load LLM config: {e}")
    return get_default_settings()


def save_llm_config(settings):
    """Save LLM config to file atomically (write to temp then rename)."""
    os.makedirs(COMMON_DIR, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=COMMON_DIR, suffix='.json')
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, LLM_CONFIG_PATH)
    except Exception:
        os.unlink(tmp_path)
        raise


def sanitize_settings_for_client(settings):
    """Return a copy of settings with apiKey masked for client-side use."""
    safe = json.loads(json.dumps(settings))
    for p in safe.get('providers', []):
        key = p.get('apiKey', '')
        if key:
            p['apiKey'] = key[:4] + '\u2022\u2022\u2022\u2022' if len(key) > 4 else '\u2022\u2022\u2022\u2022'
        else:
            p['apiKey'] = ''
    return safe


def login_required(f):
    """Shared authentication decorator.

    Returns 401 JSON for API/multipart routes, redirects to login for page routes.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            if (request.is_json or '/api/' in request.path
                    or (request.content_type and 'multipart' in request.content_type)):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def get_default_settings():
    """Return deep copy of default settings."""
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def detect_provider_type(api_url, api_key):
    """
    Auto-detect provider type by testing supported API syntaxes.
    Tests in order: Responses API, Chat Completions, Gemini.

    Returns:
        "openai-responses" | "openai-compatible" | "gemini" | None
    """
    if not api_url or not api_key:
        return None

    test_timeout = 15

    # 1. Test OpenAI Responses API
    try:
        test_url = api_url.rstrip('/')
        if not test_url.endswith('/responses'):
            test_url = test_url.replace('/chat/completions', '/responses')
        response = requests.post(
            test_url,
            json={"model": "gpt-4o-mini", "input": "test"},
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=test_timeout
        )
        if response.status_code in (200, 400):
            data = response.json()
            if 'output_text' in data or 'error' in data:
                return "openai-responses"
    except (requests.RequestException, json.JSONDecodeError):
        pass

    # 2. Test OpenAI Chat Completions API
    try:
        test_url = api_url.rstrip('/')
        if not test_url.endswith('/chat/completions'):
            if test_url.endswith('/responses'):
                test_url = test_url.replace('/responses', '/chat/completions')
            else:
                test_url = test_url.rstrip('/') + '/chat/completions'
        response = requests.post(
            test_url,
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "test"}]},
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=test_timeout
        )
        if response.status_code in (200, 400, 401):
            data = response.json()
            if 'choices' in data or 'error' in data:
                return "openai-compatible"
    except (requests.RequestException, json.JSONDecodeError):
        pass

    # 3. Test Gemini API
    try:
        base_url = api_url.rstrip('/')
        if not base_url.endswith('/models/'):
            base_url = base_url.rstrip('/') + '/'
        test_url = f"{base_url}gemini-2.0-flash:generateContent?key={api_key}"
        response = requests.post(
            test_url,
            json={"contents": [{"parts": [{"text": "test"}]}]},
            headers={"Content-Type": "application/json"},
            timeout=test_timeout
        )
        if response.status_code in (200, 400):
            data = response.json()
            if 'candidates' in data or 'error' in data:
                return "gemini"
    except (requests.RequestException, json.JSONDecodeError):
        pass

    return None


# ========== API Integration Functions ==========

def _convert_to_responses_format(parts):
    """Convert Chat Completions content parts to Responses API format.

    Chat Completions: [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:..."}}]
    Responses API:    [{"type": "input_text", "text": "..."}, {"type": "input_image", "image_url": "data:..."}]
    """
    converted = []
    for part in parts:
        if part.get("type") == "text":
            converted.append({"type": "input_text", "text": part["text"]})
        elif part.get("type") == "image_url":
            converted.append({"type": "input_image", "image_url": part["image_url"]["url"]})
        else:
            converted.append(part)
    return converted


def call_openai_responses_api(input_data, api_key, model, instructions=None, previous_response_id=None):
    """Call OpenAI Responses API for GPT models.

    input_data can be:
    - A string (just the new user message) for continuation
    - An array of items (full context) for new conversations
    """
    url = "https://api.openai.com/v1/responses"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "model": model,
        "input": input_data
    }
    if instructions:
        payload["instructions"] = instructions
    if previous_response_id:
        payload["previous_response_id"] = previous_response_id

    response = requests.post(url, json=payload, headers=headers, timeout=120)
    response.raise_for_status()
    data = response.json()
    return data.get("output_text", ""), data.get("id")


def call_openai_compatible_api(context, api_url, api_key, model):
    """Call OpenAI-compatible API (Chat Completions syntax)."""
    payload = {
        "model": model,
        "messages": context,
        "max_completion_tokens": 1000,
        "top_p": 1,
        "frequency_penalty": 0,
        "presence_penalty": 0
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    response = requests.post(api_url, json=payload, headers=headers, timeout=120)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def call_gemini_api(context, provider, model):
    """Call Google Gemini API."""
    api_key = provider["apiKey"]
    model_name = model["apiModel"]
    base_url = provider["apiUrl"].rstrip('/')
    if not base_url.endswith('/models/'):
        base_url = base_url + '/'
    url = f"{base_url}{model_name}:generateContent?key={api_key}"

    contents = []
    system_instruction = None

    for msg in context:
        if msg["role"] == "system":
            system_instruction = msg["content"]
            continue

        if isinstance(msg["content"], list):
            parts = []
            for part in msg["content"]:
                if part["type"] == "text":
                    parts.append({"text": part["text"]})
                elif part["type"] == "image_url":
                    base64_data = part["image_url"]["url"].split(",")[1]
                    parts.append({
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64_data
                        }
                    })
            contents.append({
                "role": "user" if msg["role"] == "user" else "model",
                "parts": parts
            })
        else:
            contents.append({
                "role": "user" if msg["role"] == "user" else "model",
                "parts": [{"text": msg["content"]}]
            })

    payload = {"contents": contents}
    if system_instruction:
        payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

    headers = {"Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers, timeout=120)
    response.raise_for_status()
    data = response.json()

    return data["candidates"][0]["content"]["parts"][0]["text"]


def get_ai_response(context, provider, model, instructions=None, chat_data=None):
    """
    Route request to appropriate API based on provider type.

    Args:
        context: Array of {role, content} messages (OpenAI format)
        provider: Provider config dict
        model: Model config dict
        instructions: System message (for Responses API)
        chat_data: Chat data dict (for multi-turn Responses API)

    Returns:
        tuple: (response_text, previous_response_id)
    """
    provider_type = provider.get("type")

    if provider_type == "gemini":
        return call_gemini_api(context, provider, model), None

    elif provider_type == "openai-responses":
        previous_response_id = None
        if chat_data:
            previous_response_id = chat_data.get("previous_response_id")

        if previous_response_id:
            input_data = context[-1]["content"] if context else ""
            if isinstance(input_data, list):
                input_data = _convert_to_responses_format(input_data)
        else:
            input_data = []
            for m in context:
                if m["role"] == "system":
                    continue
                content = m["content"]
                if isinstance(content, list):
                    content = _convert_to_responses_format(content)
                input_data.append({"role": m["role"], "content": content})

        text, resp_id = call_openai_responses_api(
            input_data, provider["apiKey"], model["apiModel"],
            instructions=instructions,
            previous_response_id=previous_response_id
        )
        return text, resp_id

    elif provider_type == "openai-compatible":
        return call_openai_compatible_api(
            context, provider["apiUrl"], provider["apiKey"], model["apiModel"]
        ), None

    elif provider_type is None:
        detected_type = detect_provider_type(provider["apiUrl"], provider["apiKey"])
        if detected_type:
            provider["type"] = detected_type
            settings = load_llm_config()
            for p in settings["providers"]:
                if p["id"] == provider["id"]:
                    p["type"] = detected_type
                    break
            save_llm_config(settings)
            return get_ai_response(context, provider, model, instructions, chat_data)
        else:
            raise ValueError("Не вдалося визначити тип провайдера. Перевірте API URL та ключ.")

    else:
        raise ValueError(f"Невідомий тип провайдера: {provider_type}")