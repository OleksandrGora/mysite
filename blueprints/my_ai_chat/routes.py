import os
import re
import json
import uuid
import base64
import shutil
import traceback
from datetime import datetime, timezone
from flask import request, jsonify, render_template, current_app, send_file, session
from loguru import logger
from . import my_ai_chat_bp
from common_config import load_llm_config, get_ai_response, sanitize_settings_for_client, login_required
from txt_processor import extract_text, extract_content, IMAGE_EXTENSIONS

CHATS_ROOT = os.path.join(
    os.getenv('DATA_DIR', '/home/openclaw/mysite/data'),
    'ai-chats-history'
)

MODEL_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'model_settings.json')

ALLOWED_UPLOAD_MIMES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'text/plain', 'text/markdown', 'text/csv', 'text/html',
    'application/json',
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
ALLOWED_UPLOAD_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.txt', '.md', '.csv', '.html', '.json',
                       '.pdf', '.doc', '.docx', '.pptx', '.xlsx'}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


@my_ai_chat_bp.before_request
def log_request_info():
    if request.method == 'POST':
        logger.info(f"[AI_CHAT] {request.method} {request.path}")
        if request.is_json:
            data_preview = request.get_data(as_text=True)[:500]
            logger.info(f"[AI_CHAT] Request data: {data_preview}")
    else:
        logger.info(f"[AI_CHAT] {request.method} {request.path}")


def _get_chat_dir(chat_id):
    """Get chat directory, sanitizing chat_id to prevent path traversal."""
    safe_id = os.path.basename(os.path.normpath(chat_id))
    return os.path.join(CHATS_ROOT, safe_id)


def _load_chat(chat_id):
    """Load chat.json from disk."""
    chat_dir = _get_chat_dir(chat_id)
    chat_file = os.path.join(chat_dir, 'chat.json')
    if os.path.exists(chat_file):
        with open(chat_file, 'r') as f:
            return json.load(f)
    return None


def _save_chat(chat_id, data):
    """Save chat.json to disk and update the lightweight index."""
    chat_dir = _get_chat_dir(chat_id)
    os.makedirs(chat_dir, exist_ok=True)
    chat_file = os.path.join(chat_dir, 'chat.json')
    with open(chat_file, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    try:
        _rebuild_index()
    except Exception:
        logger.warning(f"Failed to rebuild chat index", exc_info=True)


def _list_chats():
    """List all chat folders and return sorted by updated_at desc.

    Uses a lightweight _index.json sidecar to avoid reading every chat.json on each request.
    Falls back to full directory scan if index is missing.
    """
    os.makedirs(CHATS_ROOT, exist_ok=True)
    chats = []
    index_path = os.path.join(CHATS_ROOT, '_index.json')

    if os.path.exists(index_path):
        try:
            with open(index_path, 'r') as f:
                index = json.load(f)
            for entry in index:
                chat_file = os.path.join(CHATS_ROOT, entry['id'], 'chat.json')
                if os.path.exists(chat_file):
                    try:
                        with open(chat_file, 'r') as f:
                            chat = json.load(f)
                        chats.append(chat)
                    except (json.JSONDecodeError, IOError):
                        continue
            return chats
        except (json.JSONDecodeError, IOError):
            pass

    for name in os.listdir(CHATS_ROOT):
        if name.startswith('_'):
            continue
        chat_dir = os.path.join(CHATS_ROOT, name)
        chat_file = os.path.join(chat_dir, 'chat.json')
        if os.path.isdir(chat_dir) and os.path.exists(chat_file):
            try:
                with open(chat_file, 'r') as f:
                    chat = json.load(f)
                chats.append(chat)
            except (json.JSONDecodeError, IOError):
                continue
    chats.sort(key=lambda c: c.get('updated_at', ''), reverse=True)
    return chats


def _rebuild_index():
    """Rebuild the lightweight chat index from all chat.json files."""
    index = []
    if os.path.exists(CHATS_ROOT):
        for name in os.listdir(CHATS_ROOT):
            if name.startswith('_'):
                continue
            chat_file = os.path.join(CHATS_ROOT, name, 'chat.json')
            if os.path.exists(chat_file):
                try:
                    with open(chat_file, 'r') as f:
                        chat = json.load(f)
                    index.append({
                        'id': chat['id'],
                        'name': chat.get('name', ''),
                        'updated_at': chat.get('updated_at', ''),
                    })
                except (json.JSONDecodeError, IOError):
                    continue
    index.sort(key=lambda c: c.get('updated_at', ''), reverse=True)
    index_path = os.path.join(CHATS_ROOT, '_index.json')
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)


def _load_model_settings():
    """Load blueprint-local model selection."""
    if os.path.exists(MODEL_SETTINGS_FILE):
        try:
            with open(MODEL_SETTINGS_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_model_settings(data):
    """Save blueprint-local model selection."""
    os.makedirs(os.path.dirname(MODEL_SETTINGS_FILE), exist_ok=True)
    with open(MODEL_SETTINGS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def _get_settings():
    """Get LLM providers from global config."""
    settings = current_app.config.get('LLM_CONFIG') or load_llm_config()
    return settings


def _get_active_provider_and_model(settings):
    """Get active provider and model from blueprint-local settings + global providers."""
    model_settings = _load_model_settings()
    selected = model_settings.get('selectedModel', '')
    provider = None
    model = None
    if selected and '::' in selected:
        provider_id, model_api_name = selected.split('::', 1)
        logger.debug(f"[AI_CHAT] Looking up provider_id={provider_id}, model_api_name={model_api_name}")
        for p in settings.get('providers', []):
            if p['id'] == provider_id:
                provider = p
                logger.debug(f"[AI_CHAT] Found provider: {p['id']} ({p.get('name')}), available models: {[m['apiModel'] for m in p.get('models', [])]}")
                for m in p.get('models', []):
                    if m['apiModel'] == model_api_name:
                        model = m
                        break
                if not model:
                    logger.warning(f"[AI_CHAT] Model '{model_api_name}' not found in provider '{provider_id}'. Available models: {[m['apiModel'] for m in p.get('models', [])]}")
                break
        if not provider:
            logger.warning(f"[AI_CHAT] Provider '{provider_id}' not found in settings. Available providers: {[p['id'] for p in settings.get('providers', [])]}")
    else:
        logger.warning(f"[AI_CHAT] No model selected in blueprint settings")
    return provider, model


@my_ai_chat_bp.route('/')
@login_required
def index():
    """Render chat UI."""
    logger.info(f"[AI_CHAT] Rendering chat page for {session.get('email', 'unknown')}")
    settings = _get_settings()
    model_settings = _load_model_settings()
    selected_model = model_settings.get('selectedModel', '')
    return render_template('my_ai_chat/index.html', settings=sanitize_settings_for_client(settings), selected_model=selected_model)


@my_ai_chat_bp.route('/api/model-settings', methods=['POST'])
@login_required
def save_model_settings():
    """Save blueprint-local model selection."""
    try:
        data = request.get_json()
        if not data or 'selectedModel' not in data:
            return jsonify({'error': 'selectedModel is required'}), 400
        _save_model_settings({'selectedModel': data['selectedModel']})
        logger.info(f"[AI_CHAT] Model settings saved: {data['selectedModel']}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"[AI_CHAT] Failed to save model settings: {e}")
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats', methods=['GET'])
@login_required
def list_chats_api():
    """List all chats."""
    logger.info(f"[AI_CHAT] Listing all chats")
    try:
        chats = _list_chats()
        logger.info(f"[AI_CHAT] Found {len(chats)} chats")
        return jsonify({'chats': chats})
    except Exception as e:
        logger.error(f"[AI_CHAT] Failed to list chats: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats/<chat_id>', methods=['GET'])
@login_required
def get_chat(chat_id):
    """Load a specific chat."""
    logger.info(f"[AI_CHAT] Loading chat {chat_id}")
    try:
        chat = _load_chat(chat_id)
        if not chat:
            logger.warning(f"[AI_CHAT] Chat {chat_id} not found")
            return jsonify({'error': 'Chat not found'}), 404
        logger.debug(f"[AI_CHAT] Chat {chat_id} loaded: {chat.get('name')}, {len(chat.get('messages', []))} messages")
        return jsonify(chat)
    except Exception as e:
        logger.error(f"[AI_CHAT] Failed to load chat {chat_id}: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats', methods=['POST'])
@login_required
def create_chat():
    """Create a new chat."""
    logger.info(f"[AI_CHAT] Creating new chat")
    try:
        data = request.get_json() or {}
        chat_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        num_existing = sum(1 for n in os.listdir(CHATS_ROOT) if os.path.isdir(os.path.join(CHATS_ROOT, n)) and not n.startswith('_'))
        chat = {
            'id': chat_id,
            'name': data.get('name', f'Чат {num_existing + 1}'),
            'updated_at': now,
            'system_message': data.get('system_message', ''),
            'messages': [],
            'previous_response_id': None
        }
        _save_chat(chat_id, chat)
        logger.info(f"[AI_CHAT] Chat created: {chat_id} ({chat['name']})")
        return jsonify(chat), 201
    except Exception as e:
        logger.error(f"[AI_CHAT] Failed to create chat: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats/<chat_id>', methods=['PUT'])
@login_required
def update_chat(chat_id):
    """Update chat (rename, system message)."""
    logger.info(f"[AI_CHAT] Updating chat {chat_id}")
    try:
        chat = _load_chat(chat_id)
        if not chat:
            logger.warning(f"[AI_CHAT] Chat {chat_id} not found for update")
            return jsonify({'error': 'Chat not found'}), 404
        data = request.get_json() or {}
        if 'name' in data:
            chat['name'] = data['name']
        if 'system_message' in data:
            chat['system_message'] = data['system_message']
        chat['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save_chat(chat_id, chat)
        logger.info(f"[AI_CHAT] Chat {chat_id} updated")
        return jsonify(chat)
    except Exception as e:
        logger.error(f"[AI_CHAT] Failed to update chat {chat_id}: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats/<chat_id>', methods=['DELETE'])
@login_required
def delete_chat(chat_id):
    """Delete a chat (remove entire folder)."""
    logger.info(f"[AI_CHAT] Deleting chat {chat_id}")
    try:
        chat_dir = _get_chat_dir(chat_id)
        if os.path.exists(chat_dir):
            shutil.rmtree(chat_dir)
            _rebuild_index()
            logger.info(f"[AI_CHAT] Chat {chat_id} deleted")
        else:
            logger.warning(f"[AI_CHAT] Chat {chat_id} directory not found for deletion")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"[AI_CHAT] Failed to delete chat {chat_id}: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats/<chat_id>/messages', methods=['POST'])
@login_required
def send_message(chat_id):
    """Send a message and get AI response."""
    user_email = session.get('email', 'unknown')
    logger.info(f"[AI_CHAT] send_message: chat={chat_id}, user={user_email}")
    try:
        chat = _load_chat(chat_id)
        if not chat:
            logger.warning(f"[AI_CHAT] Chat {chat_id} not found for send_message")
            return jsonify({'error': 'Chat not found'}), 404

        data = request.get_json() or {}
        user_text = data.get('text', '')
        file_ids = data.get('file_ids', [])

        logger.info(f"[AI_CHAT] Message from {user_email}: chat={chat_id}, text_len={len(user_text)}, files={len(file_ids)}")

        if not user_text and not file_ids:
            logger.warning(f"[AI_CHAT] Empty message rejected for chat {chat_id}")
            return jsonify({'error': 'Text or files required'}), 400

        settings = _get_settings()
        provider, model = _get_active_provider_and_model(settings)
        if not provider:
            model_settings = _load_model_settings()
            logger.warning(f"[AI_CHAT] No active provider for chat {chat_id}. selectedModel={model_settings.get('selectedModel')}")
            return jsonify({'error': 'Не вибрано провайдера. Спочатку налаштуйте провайдера на сторінці /settings'}), 400
        if not model:
            model_settings = _load_model_settings()
            logger.warning(f"[AI_CHAT] No active model for chat {chat_id}. selectedModel={model_settings.get('selectedModel')}")
            return jsonify({'error': 'Не вибрано модель'}), 400
        if not provider.get('apiKey'):
            logger.warning(f"[AI_CHAT] No API key for provider {provider['id']} ({provider.get('name')})")
            return jsonify({'error': f'Не вказано API ключ для {provider["name"]}'}), 400

        logger.info(f"[AI_CHAT] Sending message via provider={provider['id']}, model={model['apiModel']}")

        # Build context
        context = []
        if chat.get('system_message'):
            context.append({'role': 'system', 'content': chat['system_message']})

        FILE_MARKER_RE = re.compile(r'^\[Файли:\s*(.+?)\]\s*$', re.MULTILINE)

        for msg in chat['messages']:
            role = 'user' if msg['sender'] == 'user' else 'assistant'
            text = msg['text']
            marker_match = FILE_MARKER_RE.search(text)
            if marker_match and role == 'user':
                user_text_before = FILE_MARKER_RE.sub('', text).strip()
                file_ids_str = marker_match.group(1)
                hist_file_ids = [fid.strip() for fid in file_ids_str.split(',') if fid.strip()]
                chat_dir = _get_chat_dir(chat_id)
                files_dir = os.path.join(chat_dir, 'files')
                parts = []
                has_images = False
                for fid in hist_file_ids:
                    safe_fid = os.path.basename(os.path.normpath(fid))
                    file_path = os.path.join(files_dir, safe_fid)
                    if os.path.exists(file_path):
                        ext = os.path.splitext(fid)[1].lower()
                        if ext in IMAGE_EXTENSIONS:
                            has_images = True
                            with open(file_path, 'rb') as f:
                                b64 = base64.b64encode(f.read()).decode('utf-8')
                            mime = 'image/jpeg' if ext in ('.jpg', '.jpeg') else ('image/png' if ext == '.png' else ('image/gif' if ext == '.gif' else 'image/webp'))
                            parts.append({
                                'type': 'image_url',
                                'image_url': {'url': f'data:{mime};base64,{b64}'}
                            })
                        else:
                            display_name = fid.split('_', 1)[1] if '_' in fid else fid
                            if ext in ('.pdf', '.docx'):
                                try:
                                    result = extract_content(file_path, chat_dir, display_name)
                                    text_content = result["text"]
                                    image_paths = result["image_paths"]
                                    parts.append({'type': 'text', 'text': f'\n\n--- {display_name} ---\n{text_content}'})
                                    for img_path in image_paths:
                                        if os.path.exists(img_path):
                                            with open(img_path, 'rb') as img_file:
                                                b64 = base64.b64encode(img_file.read()).decode('utf-8')
                                            parts.append({
                                                'type': 'image_url',
                                                'image_url': {'url': f'data:image/png;base64,{b64}'}
                                            })
                                    has_images = True
                                except Exception as e:
                                    logger.warning(f"[AI_CHAT] Failed to extract content from historical file {fid}: {e}")
                                    parts.append({'type': 'text', 'text': f'\n\n--- {display_name} ---\n[Failed to extract content from this file]'})
                            else:
                                try:
                                    content = extract_text(file_path)
                                    parts.append({'type': 'text', 'text': f'\n\n--- {display_name} ---\n{content}'})
                                except Exception as e:
                                    logger.warning(f"[AI_CHAT] Failed to extract text from historical file {fid}: {e}")
                                    parts.append({'type': 'text', 'text': f'\n\n--- {display_name} ---\n[Failed to extract text from this file]'})
                if has_images:
                    parts.insert(0, {'type': 'text', 'text': user_text_before})
                    context.append({'role': role, 'content': parts})
                else:
                    file_text = ''
                    for p in parts:
                        if p['type'] == 'text':
                            file_text += p['text']
                    context.append({'role': role, 'content': user_text_before + file_text})
            else:
                context.append({'role': role, 'content': text})

        # Build user content with files
        user_content = user_text
        if file_ids:
            chat_dir = _get_chat_dir(chat_id)
            files_dir = os.path.join(chat_dir, 'files')
            parts = []
            has_images = False
            for fid in file_ids:
                safe_fid = os.path.basename(os.path.normpath(fid))
                file_path = os.path.join(files_dir, safe_fid)
                if os.path.exists(file_path):
                    ext = os.path.splitext(fid)[1].lower()
                    if ext in IMAGE_EXTENSIONS:
                        has_images = True
                        with open(file_path, 'rb') as f:
                            b64 = base64.b64encode(f.read()).decode('utf-8')
                        mime = 'image/jpeg' if ext in ('.jpg', '.jpeg') else ('image/png' if ext == '.png' else ('image/gif' if ext == '.gif' else 'image/webp'))
                        parts.append({
                            'type': 'image_url',
                            'image_url': {'url': f'data:{mime};base64,{b64}'}
                        })
                    else:
                        display_name = fid.split('_', 1)[1] if '_' in fid else fid
                        ext = os.path.splitext(fid)[1].lower()

                        if ext in ('.pdf', '.docx'):
                            try:
                                result = extract_content(file_path, chat_dir, display_name)
                                text = result["text"]
                                image_paths = result["image_paths"]

                                parts.append({'type': 'text', 'text': f'\n\n--- {display_name} ---\n{text}'})

                                for img_path in image_paths:
                                    if os.path.exists(img_path):
                                        with open(img_path, 'rb') as img_file:
                                            b64 = base64.b64encode(img_file.read()).decode('utf-8')
                                        parts.append({
                                            'type': 'image_url',
                                            'image_url': {'url': f'data:image/png;base64,{b64}'}
                                        })
                                has_images = True
                            except Exception as e:
                                logger.warning(f"[AI_CHAT] Failed to extract content from {fid}: {e}")
                                parts.append({'type': 'text', 'text': f'\n\n--- {display_name} ---\n[Failed to extract content from this file]'})
                        else:
                            try:
                                content = extract_text(file_path)
                                parts.append({'type': 'text', 'text': f'\n\n--- {display_name} ---\n{content}'})
                            except Exception as e:
                                logger.warning(f"[AI_CHAT] Failed to extract text from {fid}: {e}")
                                parts.append({'type': 'text', 'text': f'\n\n--- {display_name} ---\n[Failed to extract text from this file]'})

            if has_images:
                parts.insert(0, {'type': 'text', 'text': user_text})
                user_content = parts
            else:
                file_text = ''
                for p in parts:
                    if p['type'] == 'text':
                        file_text += p['text']
                user_content = user_text + file_text

            logger.debug(f"[AI_CHAT] Files processed for chat {chat_id}: {len(file_ids)} files, has_images={has_images}")

        context.append({'role': 'user', 'content': user_content})

        # Add user message to chat
        chat['messages'].append({
            'sender': 'user',
            'text': user_text if not file_ids else f'{user_text}\n[Файли: {", ".join(file_ids)}]',
            'type': 'text'
        })

        # Get AI response
        instructions = chat.get('system_message') or None
        logger.debug(f"[AI_CHAT] Calling get_ai_response for chat {chat_id}, context_len={len(context)}")
        logger.debug(f"[AI_CHAT] Request context: {json.dumps(context, ensure_ascii=False)[:1000]}")
        response_text, resp_id = get_ai_response(
            context, provider, model,
            instructions=instructions,
            chat_data=chat
        )

        logger.info(f"[AI_CHAT] AI response received for chat {chat_id}: text_len={len(response_text)}, resp_id={resp_id}")
        logger.debug(f"[AI_CHAT] Response text (first 500): {response_text[:500]!r}")

        # Add bot message
        chat['messages'].append({
            'sender': 'bot',
            'text': response_text,
            'type': 'text'
        })

        if resp_id:
            chat['previous_response_id'] = resp_id

        chat['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save_chat(chat_id, chat)

        return jsonify({
            'response': response_text,
            'chat': chat
        })

    except ValueError as e:
        logger.warning(f"[AI_CHAT] Validation error for chat {chat_id}: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"[AI_CHAT] Failed to send message in chat {chat_id}: {e}", exc_info=True)
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/upload', methods=['POST'])
@login_required
def upload_file():
    """Upload a file for the current message. Validates type and size."""
    logger.info(f"[AI_CHAT] File upload request from {session.get('email', 'unknown')}")
    try:
        if 'file' not in request.files:
            logger.warning("[AI_CHAT] Upload: No file in request")
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        chat_id = request.form.get('chat_id', '')

        if not chat_id:
            logger.warning("[AI_CHAT] Upload: No chat_id provided")
            return jsonify({'error': 'chat_id is required'}), 400

        if not file or not file.filename:
            logger.warning("[AI_CHAT] Upload: Empty file")
            return jsonify({'error': 'Empty file'}), 400

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            logger.warning(f"[AI_CHAT] Upload: Invalid extension '{ext}' for {file.filename}")
            return jsonify({'error': f'File type "{ext}" not allowed. Supported: {", ".join(sorted(ALLOWED_UPLOAD_EXTS))}'}), 400

        logger.info(f"[AI_CHAT] Upload: file={file.filename}, ext={ext}, chat_id={chat_id}")

        import tempfile as tmpfile_module
        pos = file.tell()
        try:
            file.seek(0, 2)
        except Exception:
            file.seek(0)
            content = file.read(MAX_UPLOAD_SIZE + 1)
            file.seek(0)
            if len(content) > MAX_UPLOAD_SIZE:
                logger.warning(f"[AI_CHAT] Upload: File too large ({len(content)} bytes)")
                return jsonify({'error': f'File exceeds maximum size of {MAX_UPLOAD_SIZE // (1024*1024)} MB'}), 400
        else:
            size = file.tell()
            file.seek(pos)
            if size > MAX_UPLOAD_SIZE:
                logger.warning(f"[AI_CHAT] Upload: File too large ({size} bytes)")
                return jsonify({'error': f'File exceeds maximum size of {MAX_UPLOAD_SIZE // (1024*1024)} MB'}), 400

        chat = _load_chat(chat_id)
        if not chat:
            logger.warning(f"[AI_CHAT] Upload: Chat {chat_id} not found")
            return jsonify({'error': 'Chat not found'}), 404

        from werkzeug.utils import secure_filename
        safe_name = secure_filename(file.filename) or 'file'
        file_id = f"{uuid.uuid4().hex}_{safe_name}"
        chat_dir = _get_chat_dir(chat_id)
        files_dir = os.path.join(chat_dir, 'files')
        os.makedirs(files_dir, exist_ok=True)
        file_path = os.path.join(files_dir, file_id)
        file.save(file_path)

        file_size = os.path.getsize(file_path)
        logger.info(f"[AI_CHAT] Upload saved: file_id={file_id}, size={file_size}, path={file_path}")

        return jsonify({
            'file_id': file_id,
            'filename': file.filename,
            'size': file_size
        })

    except Exception as e:
        logger.error(f"[AI_CHAT] Failed to upload file: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/files/<chat_id>/<file_id>')
@login_required
def serve_file(chat_id, file_id):
    """Serve an uploaded file. Forces download to prevent XSS via uploaded HTML/JS."""
    logger.info(f"[AI_CHAT] Serving file: chat={chat_id}, file={file_id}")
    try:
        safe_id = os.path.basename(os.path.normpath(file_id))
        chat_dir = _get_chat_dir(chat_id)
        file_path = os.path.join(chat_dir, 'files', safe_id)
        if not os.path.exists(file_path):
            logger.warning(f"[AI_CHAT] File not found: {file_path}")
            return jsonify({'error': 'File not found'}), 404
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logger.error(f"[AI_CHAT] Failed to serve file: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500