import os
import json
import uuid
import shutil
from datetime import datetime, timezone
from flask import request, jsonify, render_template, current_app, send_file
from loguru import logger
from . import my_ai_chat_bp
from common_config import load_llm_config, get_ai_response, sanitize_settings_for_client, login_required

CHATS_ROOT = os.path.join(
    os.getenv('DATA_DIR', '/home/openclaw/mysite/data'),
    'ai-chats-history'
)

ALLOWED_UPLOAD_MIMES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'text/plain', 'text/markdown', 'text/csv', 'text/html',
    'application/json',
}
ALLOWED_UPLOAD_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.txt', '.md', '.csv', '.html', '.json'}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


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


def _get_settings():
    """Get LLM settings from app config or file."""
    return current_app.config.get('LLM_CONFIG') or load_llm_config()


def _get_active_provider_and_model(settings):
    """Get active provider and model from settings."""
    provider_id = settings.get('activeProviderId')
    model_api_name = settings.get('activeModelId')
    provider = None
    model = None
    for p in settings.get('providers', []):
        if p['id'] == provider_id:
            provider = p
            for m in p.get('models', []):
                if m['apiModel'] == model_api_name:
                    model = m
                    break
            break
    return provider, model


@my_ai_chat_bp.route('/')
@login_required
def index():
    """Render chat UI."""
    settings = _get_settings()
    return render_template('my_ai_chat/index.html', settings=sanitize_settings_for_client(settings))


@my_ai_chat_bp.route('/api/chats', methods=['GET'])
@login_required
def list_chats_api():
    """List all chats."""
    try:
        chats = _list_chats()
        return jsonify({'chats': chats})
    except Exception as e:
        logger.error(f"Failed to list chats: {e}")
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats/<chat_id>', methods=['GET'])
@login_required
def get_chat(chat_id):
    """Load a specific chat."""
    try:
        chat = _load_chat(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404
        return jsonify(chat)
    except Exception as e:
        logger.error(f"Failed to load chat {chat_id}: {e}")
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats', methods=['POST'])
@login_required
def create_chat():
    """Create a new chat."""
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
        return jsonify(chat), 201
    except Exception as e:
        logger.error(f"Failed to create chat: {e}")
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats/<chat_id>', methods=['PUT'])
@login_required
def update_chat(chat_id):
    """Update chat (rename, system message)."""
    try:
        chat = _load_chat(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404
        data = request.get_json() or {}
        if 'name' in data:
            chat['name'] = data['name']
        if 'system_message' in data:
            chat['system_message'] = data['system_message']
        chat['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save_chat(chat_id, chat)
        return jsonify(chat)
    except Exception as e:
        logger.error(f"Failed to update chat {chat_id}: {e}")
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats/<chat_id>', methods=['DELETE'])
@login_required
def delete_chat(chat_id):
    """Delete a chat (remove entire folder)."""
    try:
        chat_dir = _get_chat_dir(chat_id)
        if os.path.exists(chat_dir):
            shutil.rmtree(chat_dir)
            _rebuild_index()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Failed to delete chat {chat_id}: {e}")
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/chats/<chat_id>/messages', methods=['POST'])
@login_required
def send_message(chat_id):
    """Send a message and get AI response."""
    try:
        chat = _load_chat(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404

        data = request.get_json() or {}
        user_text = data.get('text', '')
        file_ids = data.get('file_ids', [])

        if not user_text and not file_ids:
            return jsonify({'error': 'Text or files required'}), 400

        settings = _get_settings()
        provider, model = _get_active_provider_and_model(settings)
        if not provider:
            return jsonify({'error': 'Не вибрано провайдера. Спочатку налаштуйте провайдера на сторінці /settings'}), 400
        if not model:
            return jsonify({'error': 'Не вибрано модель'}), 400
        if not provider.get('apiKey'):
            return jsonify({'error': f'Не вказано API ключ для {provider["name"]}'}), 400

        # Build context
        context = []
        if chat.get('system_message'):
            context.append({'role': 'system', 'content': chat['system_message']})

        for msg in chat['messages']:
            role = 'user' if msg['sender'] == 'user' else 'assistant'
            context.append({'role': role, 'content': msg['text']})

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
                    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                        has_images = True
                        import base64
                        with open(file_path, 'rb') as f:
                            b64 = base64.b64encode(f.read()).decode('utf-8')
                        mime = 'image/jpeg' if ext in ('.jpg', '.jpeg') else ('image/png' if ext == '.png' else ('image/gif' if ext == '.gif' else 'image/webp'))
                        parts.append({
                            'type': 'image_url',
                            'image_url': {'url': f'data:{mime};base64,{b64}'}
                        })
                    else:
                        with open(file_path, 'r', errors='ignore') as f:
                            content = f.read()
                        parts.append({'type': 'text', 'text': f'\n\n--- {fid} ---\n{content}'})

            if has_images:
                parts.insert(0, {'type': 'text', 'text': user_text})
                user_content = parts
            else:
                file_text = ''
                for p in parts:
                    if p['type'] == 'text':
                        file_text += p['text']
                user_content = user_text + file_text

        context.append({'role': 'user', 'content': user_content})

        # Add user message to chat
        chat['messages'].append({
            'sender': 'user',
            'text': user_text if not file_ids else f'{user_text}\n[Файли: {", ".join(file_ids)}]',
            'type': 'text'
        })

        # Get AI response
        instructions = chat.get('system_message') or None
        response_text, resp_id = get_ai_response(
            context, provider, model,
            instructions=instructions,
            chat_data=chat
        )

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
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Failed to send message in chat {chat_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/upload', methods=['POST'])
@login_required
def upload_file():
    """Upload a file for the current message. Validates type and size."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        chat_id = request.form.get('chat_id', '')

        if not chat_id:
            return jsonify({'error': 'chat_id is required'}), 400

        if not file or not file.filename:
            return jsonify({'error': 'Empty file'}), 400

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            return jsonify({'error': f'File type "{ext}" not allowed. Supported: {", ".join(sorted(ALLOWED_UPLOAD_EXTS))}'}), 400

        import tempfile as tmpfile_module
        pos = file.tell()
        try:
            file.seek(0, 2)
        except Exception:
            file.seek(0)
            content = file.read(MAX_UPLOAD_SIZE + 1)
            file.seek(0)
            if len(content) > MAX_UPLOAD_SIZE:
                return jsonify({'error': f'File exceeds maximum size of {MAX_UPLOAD_SIZE // (1024*1024)} MB'}), 400
        else:
            size = file.tell()
            file.seek(pos)
            if size > MAX_UPLOAD_SIZE:
                return jsonify({'error': f'File exceeds maximum size of {MAX_UPLOAD_SIZE // (1024*1024)} MB'}), 400

        chat = _load_chat(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404

        from werkzeug.utils import secure_filename
        safe_name = secure_filename(file.filename) or 'file'
        file_id = f"{uuid.uuid4().hex}_{safe_name}"
        chat_dir = _get_chat_dir(chat_id)
        files_dir = os.path.join(chat_dir, 'files')
        os.makedirs(files_dir, exist_ok=True)
        file_path = os.path.join(files_dir, file_id)
        file.save(file_path)

        return jsonify({
            'file_id': file_id,
            'filename': file.filename,
            'size': os.path.getsize(file_path)
        })

    except Exception as e:
        logger.error(f"Failed to upload file: {e}")
        return jsonify({'error': str(e)}), 500


@my_ai_chat_bp.route('/api/files/<chat_id>/<file_id>')
@login_required
def serve_file(chat_id, file_id):
    """Serve an uploaded file. Forces download to prevent XSS via uploaded HTML/JS."""
    try:
        safe_id = os.path.basename(os.path.normpath(file_id))
        chat_dir = _get_chat_dir(chat_id)
        file_path = os.path.join(chat_dir, 'files', safe_id)
        if not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logger.error(f"Failed to serve file: {e}")
        return jsonify({'error': str(e)}), 500