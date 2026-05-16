"""
Task management system for document generator.
Handles task creation, saving, loading from Tasks-data folder.
"""
import os
import sys
import json
import re
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path
from loguru import logger
from celery import current_app as celery_app


TASKS_DATA_DIR = '/home/openclaw/mysite/blueprints/doc_generator/Tasks-data'
SOURCES_DIR = '/home/openclaw/mysite/blueprints/doc_generator/Tasks-data/Sources'


def sanitize_filename(filename):
    """
    Sanitize filename while preserving Unicode characters.
    - Replace spaces with underscores
    - Remove dangerous filesystem characters: / \\ : * ? " < > |
    - Keep Cyrillic, parentheses, brackets, commas, plus
    - Limit length to 200 chars
    """
    filename = filename.replace(' ', '_')
    filename = re.sub(r'[^\w\-\.\(\)\[\]\,\+\=]', '', filename, flags=re.UNICODE)
    filename = re.sub(r'\.{2,}', '.', filename)
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200 - len(ext)] + ext
    if not filename or filename == '.':
        filename = 'file'
    return filename


def ensure_directories():
    """Ensure all required directories exist."""
    for dir_path in [TASKS_DATA_DIR, SOURCES_DIR]:
        os.makedirs(dir_path, exist_ok=True)


def get_unique_task_folder(template_filename: str) -> str:
    """
    Create unique task folder name from template filename.
    If folder exists, adds number suffix (e.g., "file 2", "file 3").
    Returns full path to task folder.
    """
    base_name = Path(template_filename).stem
    task_folder = os.path.join(TASKS_DATA_DIR, base_name)
    
    if not os.path.exists(task_folder):
        os.makedirs(task_folder)
        return task_folder
    
    # Find next available number
    counter = 2
    while True:
        new_name = f"{base_name} {counter}"
        task_folder = os.path.join(TASKS_DATA_DIR, new_name)
        if not os.path.exists(task_folder):
            os.makedirs(task_folder)
            return task_folder
        counter += 1


def create_task_info(
    task_id: str,
    template_file: str,
    analysis_prompt: str = "",
    generation_prompt: str = "",
    source_files: List[str] = None,
    status: str = "idle"
) -> Dict[str, Any]:
    """Create task info dictionary."""
    return {
        "task_id": task_id,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "status": status,  # idle, analyzing, generating, completed, failed
        "template_file": template_file,
        "source_files": source_files or [],
        "analysis_prompt": analysis_prompt,
        "generation_prompt": generation_prompt,
        "analysis_result": None,  # Path to analysis.txt or None
        "final_document": None,  # Path to result.docx or None
        "error_message": None,
        "celery_task_ids": []  # Celery task IDs for cancellation on delete
    }


def save_task_info(task_folder: str, task_info: Dict[str, Any]):
    """Save task info to task-info.json."""
    task_info["updated_at"] = datetime.utcnow().isoformat()
    info_path = os.path.join(task_folder, "task-info.json")
    with open(info_path, 'w', encoding='utf-8') as f:
        json.dump(task_info, f, ensure_ascii=False, indent=2)
    logger.info(f"Task info saved: {task_folder}")


def load_task_info(task_folder: str) -> Optional[Dict[str, Any]]:
    """Load task info from task-info.json."""
    info_path = os.path.join(task_folder, "task-info.json")
    if not os.path.exists(info_path):
        return None
    with open(info_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_all_tasks() -> List[Dict[str, Any]]:
    """Get list of all tasks from Tasks-data folder."""
    ensure_directories()
    tasks = []
    
    if not os.path.exists(TASKS_DATA_DIR):
        return tasks
    
    for item in os.listdir(TASKS_DATA_DIR):
        # Skip system folders
        if item in ['Sources']:
            continue
            
        task_folder = os.path.join(TASKS_DATA_DIR, item)
        if os.path.isdir(task_folder):
            task_info = load_task_info(task_folder)
            if task_info:
                # Add folder name for display (remove surrounding quotes if any)
                folder_name = item.strip("'\"")
                task_info["folder_name"] = folder_name
                task_info["folder_path"] = task_folder
                tasks.append(task_info)
    
    # Sort by created_at descending
    tasks.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return tasks


def get_task_by_id(task_id: str) -> Optional[Dict[str, Any]]:
    """Find task by ID across all task folders."""
    tasks = get_all_tasks()
    for task in tasks:
        if task.get("task_id") == task_id:
            return task
    return None


def update_task_status(task_folder: str, status: str, error_message: str = None):
    """Update task status. Clears progress when entering a running state."""
    task_info = load_task_info(task_folder)
    if task_info:
        task_info["status"] = status
        if error_message:
            task_info["error_message"] = error_message
        if status in ('analyzing', 'generating'):
            task_info.pop('progress', None)
        save_task_info(task_folder, task_info)


def update_task_progress(task_folder: str, message: str, percent: int = None):
    """Update task progress message (for real-time UI feedback)."""
    task_info = load_task_info(task_folder)
    if task_info:
        task_info["progress"] = {
            "message": message,
            "percent": percent
        }
        save_task_info(task_folder, task_info)


def save_analysis_result(task_folder: str, analysis_text: str) -> str:
    """Save analysis result to {folder_name} аналіз.txt. Returns filename."""
    folder_name = os.path.basename(task_folder)
    result_filename = f"{folder_name} аналіз.txt"
    analysis_path = os.path.join(task_folder, result_filename)
    
    # Debug info: file path and content size
    logger.info(f"[DEBUG] Saving analysis to: {analysis_path}")
    logger.info(f"[DEBUG] Analysis text length: {len(analysis_text)} chars")
    logger.info(f"[DEBUG] Analysis word count: {len(analysis_text.split())} words")
    logger.info(f"[DEBUG] Task folder exists: {os.path.exists(task_folder)}")
    logger.info(f"[DEBUG] Task folder path: {task_folder}")
    
    try:
        with open(analysis_path, 'w', encoding='utf-8') as f:
            f.write(analysis_text)
        
        # Debug info: file saved successfully
        file_size = os.path.getsize(analysis_path)
        logger.info(f"[DEBUG] Analysis file saved successfully")
        logger.info(f"[DEBUG] File size: {file_size} bytes")
        logger.info(f"[DEBUG] File exists after save: {os.path.exists(analysis_path)}")
        
    except Exception as e:
        logger.error(f"[DEBUG] Failed to save analysis file: {e}")
        raise
    
    # Update task info
    task_info = load_task_info(task_folder)
    if task_info:
        task_info["analysis_result"] = result_filename
        save_task_info(task_folder, task_info)
        logger.info(f"[DEBUG] Task info updated with analysis_result: {result_filename}")
    else:
        logger.warning(f"[DEBUG] Could not load task info to update analysis_result")
    
    logger.info(f"Analysis saved: {analysis_path}")
    return result_filename


def load_analysis_result(task_folder: str) -> Optional[str]:
    """Load analysis result from {folder_name} аналіз.txt."""
    folder_name = os.path.basename(task_folder)
    result_filename = f"{folder_name} аналіз.txt"
    analysis_path = os.path.join(task_folder, result_filename)
    
    # Debug info: file path and existence check
    logger.info(f"[DEBUG] Loading analysis from: {analysis_path}")
    logger.info(f"[DEBUG] Task folder path: {task_folder}")
    logger.info(f"[DEBUG] Task folder exists: {os.path.exists(task_folder)}")
    logger.info(f"[DEBUG] Analysis file exists: {os.path.exists(analysis_path)}")
    
    # Try new filename first, fallback to old name for backwards compatibility
    if not os.path.exists(analysis_path):
        # Try old filename "analysis.txt"
        old_path = os.path.join(task_folder, "analysis.txt")
        if os.path.exists(old_path):
            logger.info(f"[DEBUG] Found old analysis file: {old_path}")
            analysis_path = old_path
        else:
            logger.warning(f"[DEBUG] Analysis file not found: {analysis_path}")
            return None
    
    try:
        with open(analysis_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Debug info: file loaded successfully
        file_size = os.path.getsize(analysis_path)
        logger.info(f"[DEBUG] Analysis file loaded successfully")
        logger.info(f"[DEBUG] File size: {file_size} bytes")
        logger.info(f"[DEBUG] Content length: {len(content)} chars")
        logger.info(f"[DEBUG] Word count: {len(content.split())} words")
        logger.info(f"[DEBUG] Content preview: {content[:100]}...")
        
        return content
        
    except Exception as e:
        logger.error(f"[DEBUG] Failed to load analysis file: {e}")
        raise


def save_final_document(task_folder: str, document_path: str):
    """Copy final document to task folder with proper name."""
    task_info = load_task_info(task_folder)
    if not task_info:
        return
    
    folder_name = os.path.basename(task_folder)
    result_filename = f"{folder_name} результат.docx"
    result_path = os.path.join(task_folder, result_filename)
    
    shutil.copy2(document_path, result_path)
    
    # Update task info
    task_info["final_document"] = result_filename
    save_task_info(task_folder, task_info)
    
    logger.info(f"Final document saved: {result_path}")


def register_celery_task(folder_path: str, celery_task_id: str):
    """Store a Celery task ID in task-info.json for later cancellation."""
    task_info = load_task_info(folder_path)
    if task_info:
        if 'celery_task_ids' not in task_info:
            task_info['celery_task_ids'] = []
        if celery_task_id not in task_info['celery_task_ids']:
            task_info['celery_task_ids'].append(celery_task_id)
            save_task_info(folder_path, task_info)
            logger.debug(f"Registered celery task {celery_task_id} for {folder_path}")


def _revoke_celery_tasks(task_folder: str):
    """Revoke all Celery tasks associated with a task folder."""
    task_info = load_task_info(task_folder)
    if not task_info:
        return
    celery_ids = task_info.get('celery_task_ids', [])
    if not celery_ids:
        return
    for celery_id in celery_ids:
        try:
            celery_app.control.revoke(celery_id, terminate=True, signal='SIGTERM')
            logger.info(f"Revoked celery task {celery_id} for {task_folder}")
        except Exception as e:
            logger.warning(f"Failed to revoke celery task {celery_id}: {e}")


def delete_task(task_folder: str) -> bool:
    """Delete task folder and all its contents. Revokes associated Celery tasks first."""
    try:
        if os.path.exists(task_folder):
            _revoke_celery_tasks(task_folder)
            shutil.rmtree(task_folder)
            logger.info(f"Task deleted: {task_folder}")
            return True
    except Exception as e:
        logger.error(f"Failed to delete task {task_folder}: {e}")
    return False


# File upload management
def handle_file_upload(file, upload_type: str) -> Dict[str, Any]:
    """
    Handle file upload with uniqueness check.
    upload_type: 'template' or 'source'
    Both types saved to Sources directory.
    Returns: {"status": "success|exists|copied", "path": "...", "filename": "..."}
    """
    ensure_directories()
    
    safe_name = sanitize_filename(file.filename)
    target_path = os.path.join(SOURCES_DIR, safe_name)
    
    # Check if file exists
    if os.path.exists(target_path):
        return {
            "status": "exists",
            "path": target_path,
            "filename": safe_name
        }
    
    # Save file
    file.save(target_path)
    
    return {
        "status": "success",
        "path": target_path,
        "filename": safe_name
    }


def get_available_files(file_type: str = 'all') -> Dict[str, List[str]]:
    """
    Get list of available files.
    file_type: 'template', 'source', or 'all'
    All files are stored in Sources directory.
    """
    ensure_directories()
    result = {}
    
    file_list = [
        f for f in os.listdir(SOURCES_DIR)
        if os.path.isfile(os.path.join(SOURCES_DIR, f)) and not f.startswith('.')
    ]
    
    if file_type in ['source', 'all']:
        result['sources'] = file_list
    
    if file_type in ['template', 'all']:
        result['templates'] = file_list
    
    return result


def get_unique_filename(directory: str, filename: str) -> str:
    """Generate unique filename by adding 'копія' suffix if needed."""
    base_name = Path(filename).stem
    ext = Path(filename).suffix
    
    target_path = os.path.join(directory, filename)
    if not os.path.exists(target_path):
        return filename
    
    counter = 2
    while True:
        new_filename = f"{base_name} копія {counter}{ext}"
        target_path = os.path.join(directory, new_filename)
        if not os.path.exists(target_path):
            return new_filename
        counter += 1
