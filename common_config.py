import json
import os
import tempfile
import requests
import base64
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
    ]
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


# ========== Model Utility Functions ==========

def is_gemini_model(model_name: str) -> bool:
    """Check if model is from Google Gemini."""
    return model_name.startswith('gemini-')


def is_openai_model(model_name: str) -> bool:
    """Check if model is from OpenAI."""
    return model_name.startswith('gpt-') or model_name.startswith('gpt-image-')


def is_deepseek_model(model_name: str) -> bool:
    """Check if model is from DeepSeek."""
    return model_name.startswith('deepseek-')


def get_available_models_from_config(config):
    """Return list of 'providerId::apiModel' strings from config."""
    models = []
    for provider in config.get('providers', []):
        pid = provider.get('id')
        for model in provider.get('models', []):
            api_model = model.get('apiModel')
            if pid and api_model:
                models.append(f"{pid}::{api_model}")
    return models


def get_model_provider(config, model_name):
    """Determine provider (id, provider_dict) for a given model name.
    
    Returns:
        tuple: (provider_id, provider_dict) or (None, None)
    """
    for provider in config.get('providers', []):
        for model in provider.get('models', []):
            if model.get('apiModel') == model_name:
                pid = provider.get('id', '')
                ptype = provider.get('type', '')
                if pid == 'deepseek':
                    return 'deepseek', provider
                if ptype and 'openai' in ptype:
                    return 'openai', provider
                if ptype == 'gemini' or pid == 'gemini':
                    return 'gemini', provider
                return 'openai', provider
    # Fallback prefix-based detection
    if is_gemini_model(model_name):
        return 'gemini', None
    elif is_openai_model(model_name):
        return 'openai', None
    elif is_deepseek_model(model_name):
        return 'deepseek', None
    return 'openai', None


def get_provider_config_for_model(config, model_name):
    """Get the full provider config dict for a given model name."""
    _, provider = get_model_provider(config, model_name)
    return provider or {}


def get_api_key_for_provider(provider):
    """Get API key for a provider, with env var fallback."""
    key = provider.get('apiKey', '') if provider else ''
    if not key and provider:
        pid = provider.get('id', '')
        if pid == 'gemini':
            key = os.getenv('GEMINI_API_KEY', '')
        elif pid == 'openai':
            key = os.getenv('OPENAI_API_KEY', '')
        elif pid == 'deepseek':
            key = os.getenv('DEEPSEEK_API_KEY', '')
    return key


def needs_max_completion_tokens(model_name: str) -> bool:
    """
    Check if the model requires 'max_completion_tokens' instead of 'max_tokens'.
    Newer OpenAI models (GPT-4o, GPT-5, o-series) deprecated 'max_tokens'
    and require 'max_completion_tokens' instead.
    """
    model_lower = model_name.lower()
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

    if is_deepseek_model(model_name):
        kwargs['temperature'] = temperature
        kwargs['max_tokens'] = max_tokens
        return kwargs

    if not _is_temperature_restricted_model(model_name):
        kwargs['temperature'] = temperature

    if _is_temperature_restricted_model(model_name):
        pass
    elif needs_max_completion_tokens(model_name):
        kwargs['max_completion_tokens'] = max_tokens
    else:
        kwargs['max_tokens'] = max_tokens

    return kwargs


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


def call_openai_responses_api(input_data, api_key, model, instructions=None, previous_response_id=None, api_url=None):
    """Call OpenAI Responses API for GPT models."""
    url = api_url or "https://api.openai.com/v1/responses"
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

    logger.info(f"[call_openai_responses_api] Sending request: model={model}, input_data={json.dumps(input_data, ensure_ascii=False)[:500]}")
    response = requests.post(url, json=payload, headers=headers, timeout=120)
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        try:
            error_body = response.content.decode('utf-8', errors='replace')
        except Exception:
            error_body = str(response.content)
        raise requests.HTTPError(f"{e}. Response body: {error_body}", response=response)
    data = response.json()
    logger.info(f"[call_openai_responses_api] Raw response keys: {list(data.keys())}")
    output_text = data.get("output_text")
    if output_text is None:
        output = data.get("output", [])
        logger.info(f"[call_openai_responses_api] output items count={len(output)}")
        for i, item in enumerate(output):
            logger.info(f"[call_openai_responses_api] output[{i}]: type={item.get('type')}, role={item.get('role')}, content_count={len(item.get('content', []) or [])}")
            if isinstance(item, dict) and item.get("type") == "message":
                content = item.get("content", []) or []
                for j, part in enumerate(content):
                    logger.info(f"[call_openai_responses_api] output[{i}].content[{j}]: type={part.get('type')}, has_text={'text' in part}")
                if item.get("role") == "assistant":
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "output_text":
                            output_text = part.get("text", "")
                            break
                    if output_text is None:
                        for part in content:
                            if isinstance(part, dict) and "text" in part:
                                output_text = part["text"]
                                break
        if output_text is None:
            output_text = ""
    logger.info(f"[call_openai_responses_api] Response text (first 200): {output_text[:200]!r}")
    return output_text, data.get("id")


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
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        try:
            error_body = response.content.decode('utf-8', errors='replace')
        except Exception:
            error_body = str(response.content)
        raise requests.HTTPError(f"{e}. Response body: {error_body}", response=response)
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


def get_ai_response(context, provider, model, instructions=None, chat_data=None, max_retries=3):
    """
    Route request to appropriate API based on provider type.

    Args:
        context: Array of {role, content} messages (OpenAI format)
        provider: Provider config dict
        model: Model config dict
        instructions: System message (for Responses API)
        chat_data: Chat data dict (for multi-turn Responses API)
        max_retries: Number of retries on transient errors

    Returns:
        tuple: (response_text, previous_response_id)
    """
    provider_type = provider.get("type")

    if provider_type == "gemini":
        for attempt in range(max_retries):
            try:
                result = call_gemini_api(context, provider, model), None
                return result
            except requests.HTTPError as e:
                if '503' in str(e) and attempt < max_retries - 1:
                    logger.warning(f"Gemini 503 error, retrying ({attempt + 1}/{max_retries})...")
                    import time
                    time.sleep(10 * (attempt + 1))
                    continue
                raise

    elif provider_type == "openai-responses":
        previous_response_id = None
        if chat_data:
            previous_response_id = chat_data.get("previous_response_id")

        for attempt in range(max_retries):
            try:
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
                    previous_response_id=previous_response_id,
                    api_url=provider.get("apiUrl")
                )
                return text, resp_id
            except requests.HTTPError as e:
                if '503' in str(e) and attempt < max_retries - 1:
                    logger.warning(f"OpenAI 503 error, retrying ({attempt + 1}/{max_retries})...")
                    import time
                    time.sleep(10 * (attempt + 1))
                    continue
                raise

    elif provider_type == "openai-compatible":
        for attempt in range(max_retries):
            try:
                result = call_openai_compatible_api(
                    context, provider["apiUrl"], provider["apiKey"], model["apiModel"]
                ), None
                return result
            except requests.HTTPError as e:
                if '503' in str(e) and attempt < max_retries - 1:
                    logger.warning(f"OpenAI-compatible 503 error, retrying ({attempt + 1}/{max_retries})...")
                    import time
                    time.sleep(10 * (attempt + 1))
                    continue
                raise

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
            return get_ai_response(context, provider, model, instructions, chat_data, max_retries)
        else:
            raise ValueError("Не вдалося визначити тип провайдера. Перевірте API URL та ключ.")

    else:
        raise ValueError(f"Невідомий тип провайдера: {provider_type}")


# ========== Image Generation Functions ==========

OPENAI_IMAGE_QUALITY = "medium"
OPENAI_IMAGE_SIZE = "1024x1024"
GEMINI_IMAGE_TEMPERATURE = 0.7
GEMINI_IMAGE_THINKING_BUDGET = 0


def generate_image_with_google(description, output_dir, slide_index, model=None, api_key=None):
    """
    Generate image using Gemini AI based on description.
    
    Args:
        description: Text description of the image
        output_dir: Directory to save the image
        slide_index: Index for filename
        model: Model name (falls back to config)
        api_key: API key (falls back to env var or config)
        
    Returns:
        tuple: (image_path, tokens_used_dict) or (None, None)
    """
    try:
        if not api_key:
            api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            config = load_llm_config()
            for p in config.get('providers', []):
                if p.get('id') == 'gemini' and p.get('apiKey'):
                    api_key = p['apiKey']
                    break
        if not api_key:
            logger.warning("GEMINI_API_KEY not set, skipping image generation")
            return None, None

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        enhanced_prompt = f"""Create a clean, professional illustration for a presentation slide.

Description: {description}

Requirements:
- Simple, minimalist design
- Suitable for educational/business presentation
- Clear visual elements
- No text in the image
- Professional color palette"""

        actual_model = model or "gemini-2.5-flash-image-preview"
        logger.info(f"Generating Gemini image for slide {slide_index}: {description[:50]}...")

        response = client.models.generate_content(
            model=actual_model,
            contents=enhanced_prompt,
            config=types.GenerateContentConfig(
                temperature=GEMINI_IMAGE_TEMPERATURE,
                response_modalities=['Text', 'Image'],
                thinking_config=types.ThinkingConfig(thinking_budget=GEMINI_IMAGE_THINKING_BUDGET)
            )
        )

        image_data = None
        if response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]
            for part in candidate.content.parts:
                if hasattr(part, 'inline_data') and part.inline_data is not None:
                    image_data = part.inline_data.data
                    break
                elif hasattr(part, 'inlineData') and part.inlineData is not None:
                    image_data = part.inlineData.data
                    break

        if image_data is None:
            logger.warning(f"No image data returned from Gemini for slide {slide_index}")
            return None, None

        image_path = os.path.join(output_dir, f"slide_{slide_index}.png")
        with open(image_path, 'wb') as f:
            f.write(base64.b64decode(image_data) if isinstance(image_data, str) else image_data)

        tokens_used = None
        try:
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                tokens_used = {
                    'input_tokens': getattr(response.usage_metadata, 'prompt_token_count', 0),
                    'output_tokens': getattr(response.usage_metadata, 'candidates_token_count', 0),
                    'total_tokens': getattr(response.usage_metadata, 'total_token_count', 0)
                }
        except Exception:
            pass

        logger.info(f"Gemini image saved to {image_path}")
        return image_path, tokens_used

    except Exception as e:
        logger.error(f"Gemini image generation failed: {e}")
        return None, None


def generate_image_with_openai(description, output_dir, slide_index, model=None, api_key=None):
    """
    Generate image using OpenAI DALL-E / GPT Image model.
    
    Args:
        description: Text description of the image
        output_dir: Directory to save the image
        slide_index: Index for filename
        model: Model name (falls back to config)
        api_key: API key (falls back to env var or config)
        
    Returns:
        tuple: (image_path, tokens_used_dict) or (None, None)
    """
    try:
        if not api_key:
            api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            config = load_llm_config()
            for p in config.get('providers', []):
                if p.get('id') == 'openai' and p.get('apiKey'):
                    api_key = p['apiKey']
                    break
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, skipping image generation")
            return None, None

        from openai import OpenAI
        import httpx

        client = OpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(120.0, connect=10.0)
        )

        enhanced_prompt = f"""Create a clean, professional illustration for a presentation slide.

Description: {description}

Requirements:
- Simple, minimalist design
- Suitable for educational/business presentation
- Clear visual elements
- No text in the image
- Professional color palette"""

        actual_model = model or "gpt-image-1-mini"
        logger.info(f"Generating OpenAI image for slide {slide_index}: {description[:50]}...")

        response = client.images.generate(
            model=actual_model,
            prompt=enhanced_prompt,
            n=1,
            size=OPENAI_IMAGE_SIZE,
            quality=OPENAI_IMAGE_QUALITY
        )

        if response.data and len(response.data) > 0:
            image_data = response.data[0].b64_json
            if image_data:
                image_path = os.path.join(output_dir, f"slide_{slide_index}.png")
                with open(image_path, 'wb') as f:
                    f.write(base64.b64decode(image_data))

                tokens_used = None
                try:
                    if hasattr(response, 'usage') and response.usage:
                        tokens_used = {
                            'input_tokens': getattr(response.usage, 'prompt_tokens', 0),
                            'output_tokens': getattr(response.usage, 'completion_tokens', 0),
                            'total_tokens': getattr(response.usage, 'total_tokens', 0)
                        }
                except Exception:
                    pass

                logger.info(f"OpenAI image saved to {image_path}")
                return image_path, tokens_used

        logger.warning(f"No image data returned from OpenAI for slide {slide_index}")
        return None, None

    except Exception as e:
        logger.error(f"OpenAI image generation failed: {e}", exc_info=True)
        return None, None


def generate_image(provider, description, output_dir, slide_index, model_name=None):
    """
    Universal image generation dispatcher.
    
    Args:
        provider: Provider config dict
        description: Text description
        output_dir: Output directory
        slide_index: Index for filename
        model_name: Optional model name override
        
    Returns:
        tuple: (image_path, tokens_used_dict) or (None, None)
    """
    provider_type = provider.get('type', '')
    provider_id = provider.get('id', '')
    api_key = get_api_key_for_provider(provider)
    model = model_name or (provider.get('models', [{}])[0].get('apiModel') if provider.get('models') else None)

    if provider_type in ('openai-responses', 'openai-compatible') or provider_id == 'openai':
        return generate_image_with_openai(description, output_dir, slide_index, model=model, api_key=api_key)
    elif provider_type == 'gemini' or provider_id == 'gemini':
        return generate_image_with_google(description, output_dir, slide_index, model=model, api_key=api_key)
    else:
        logger.warning(f"Unsupported provider for image generation: {provider_id} ({provider_type})")
        return None, None