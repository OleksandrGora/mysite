"""
Source Processor - Celery tasks for file processing with progress tracking.
Handles: text extraction → chunking → vectorization (embedding) → ChromaDB storage.

Progress is stored in a JSON file and can be polled via API.
"""
import os
import json
import time
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List
from pathlib import Path
from loguru import logger
from celery import shared_task

from .task_manager import SOURCES_DIR

# ── Status file ──────────────────────────────────────────────────────────────
# Stored outside SOURCES_DIR to avoid appearing in file listings
SOURCES_STATUS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '.sources_status.json'
)


def _load_status() -> Dict[str, Dict[str, Any]]:
    """Load source processing status from JSON file."""
    if os.path.exists(SOURCES_STATUS_FILE):
        try:
            with open(SOURCES_STATUS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load sources status: {e}")
    return {}


def _save_status(status: Dict[str, Dict[str, Any]]):
    """Save source processing status to JSON file."""
    os.makedirs(os.path.dirname(SOURCES_STATUS_FILE), exist_ok=True)
    with open(SOURCES_STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


def _update_source_progress(filename: str, progress: int, message: str,
                            status: str = 'processing', error: str = None,
                            extra: dict = None):
    """Update progress for a single source file and persist."""
    status_dict = _load_status()
    entry = status_dict.get(filename, {})
    entry['filename'] = filename
    entry['progress'] = progress
    entry['message'] = message
    entry['status'] = status
    entry['updated_at'] = datetime.utcnow().isoformat()
    if error:
        entry['error'] = error
    if extra:
        entry.update(extra)
    if 'started_at' not in entry:
        entry['started_at'] = datetime.utcnow().isoformat()
    status_dict[filename] = entry
    _save_status(status_dict)


def get_source_status(filename: str = None) -> Dict:
    """
    Get processing status for one or all source files.
    
    Args:
        filename: If provided, returns status for that file only.
                  If None, returns all statuses.
    
    Returns:
        Dict of filename -> status info
    """
    status = _load_status()
    
    # Also index source files that exist but have no status entry
    if os.path.exists(SOURCES_DIR):
        for f in os.listdir(SOURCES_DIR):
            fpath = os.path.join(SOURCES_DIR, f)
            if os.path.isfile(fpath) and not f.startswith('.'):
                if f not in status:
                    # Check if there's a ChromaDB collection for this source
                    from .rag.embedder import Embedder, compute_sources_hash
                    try:
                        sources_hash = compute_sources_hash([fpath])
                        embedder = Embedder()
                        if embedder.collection_exists(sources_hash):
                            status[f] = {
                                'filename': f,
                                'status': 'ready',
                                'progress': 100,
                                'message': 'Готово (з кешу)',
                                'updated_at': datetime.utcnow().isoformat()
                            }
                        else:
                            status[f] = {
                                'filename': f,
                                'status': 'pending',
                                'progress': 0,
                                'message': 'Очікування обробки',
                                'updated_at': datetime.utcnow().isoformat()
                            }
                    except Exception:
                        status[f] = {
                            'filename': f,
                            'status': 'pending',
                            'progress': 0,
                            'message': 'Очікування обробки',
                            'updated_at': datetime.utcnow().isoformat()
                        }
        _save_status(status)
    
    if filename:
        return status.get(filename, {
            'filename': filename,
            'status': 'pending',
            'progress': 0,
            'message': 'Очікування обробки'
        })
    return status


# ── Progress callback for embedder ───────────────────────────────────────────

class ProgressTracker:
    """
    Callback class that the embedder calls during processing.
    Allows tracking sub-steps for fine-grained progress reporting.
    """
    
    def __init__(self, filename: str, total_steps: int = 100):
        self.filename = filename
        self.total_steps = total_steps
        self.base_progress = 0
        self.step_range = 100
    
    def set_phase(self, phase_start: int, phase_end: int):
        """Set the percentage range for the current phase."""
        self.base_progress = phase_start
        self.step_range = phase_end - phase_start
    
    def update(self, sub_progress: float, message: str):
        """
        Update progress. sub_progress is 0.0 to 1.0 within the current phase.
        """
        overall = self.base_progress + int(sub_progress * self.step_range)
        overall = min(max(overall, 0), 100)
        _update_source_progress(self.filename, overall, message)


# ── Celery Task ──────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=1)
def process_source_file_task(self, file_path: str, filename: str):
    """
    Celery task to process a single source file:
    text extraction → chunking → embedding → ChromaDB storage.
    
    Reports progress at each step.
    """
    from loguru import logger
    
    logger.info(f"[SOURCE-PROC] Starting processing: {filename}")
    
    tracker = ProgressTracker(filename)
    
    try:
        # ── Step 0: Initialize ────────────────────────────────────────────
        _update_source_progress(filename, 0, 'Ініціалізація...', 'processing')
        
        # ── Step 1: Extract text (0% → 10%) ───────────────────────────────
        tracker.set_phase(0, 10)
        _update_source_progress(filename, 2, 'Витяг тексту з файлу...', 'processing')
        
        from .parsers.source_parser import parse_sources_with_metadata
        sources_data = parse_sources_with_metadata([file_path])
        
        if not sources_data or not any(d.get('text', '').strip() for d in sources_data.values()):
            raise ValueError(f"Не вдалося витягти текст з файлу {filename}")
        
        text_data = sources_data.get(filename, {})
        char_count = text_data.get('char_count', 0)
        word_count = text_data.get('word_count', 0)
        
        logger.info(f"[SOURCE-PROC] Extracted {char_count} chars, {word_count} words from {filename}")
        _update_source_progress(filename, 10, f'Текст витягнуто: {char_count} символів, {word_count} слів',
                                'processing', extra={'char_count': char_count, 'word_count': word_count})
        
        # ── Step 2: Chunk documents (10% → 35%) ───────────────────────────
        tracker.set_phase(10, 35)
        _update_source_progress(filename, 12, 'Чанкування документа...', 'processing')
        
        # Log source text stats before chunking
        text_data = sources_data.get(filename, {})
        source_text = text_data.get('text', '')
        logger.info(f"[SOURCE-PROC] Chunking '{filename}': {len(source_text)} chars, {len(source_text.split())} words")
        
        from .rag.chunker import chunk_documents, format_chunks_for_log
        chunks = chunk_documents(
            sources_data=sources_data,
            min_chunk_size=50,
            max_chunk_size=250
        )
        
        chunk_count = len(chunks)
        logger.info(f"[SOURCE-PROC] Created {chunk_count} chunks from {filename}")
        
        # Log chunk details
        if chunks:
            word_counts = [c.word_count for c in chunks]
            logger.info(f"[SOURCE-PROC] Chunk stats for '{filename}': min={min(word_counts)}, max={max(word_counts)}, avg={sum(word_counts)/len(word_counts):.0f} words")
            logger.debug(f"[SOURCE-PROC] Chunks for '{filename}':\n{format_chunks_for_log(chunks)}")
        else:
            logger.warning(f"[SOURCE-PROC] No chunks created from '{filename}'!")
        
        _update_source_progress(filename, 35, f'Чанкування завершено: {chunk_count} чанків',
                                'processing', extra={'chunks': chunk_count})
        
        # ── Step 3: Build embeddings (35% → 90%) ──────────────────────────
        tracker.set_phase(35, 90)
        
        from .rag.embedder import Embedder, compute_sources_hash
        embedder = Embedder()
        sources_hash = compute_sources_hash([file_path])
        
        # Check cache first
        _update_source_progress(filename, 38, 'Перевірка кешу...', 'processing')
        
        if embedder.collection_exists(sources_hash):
            collection_name = embedder.get_collection_name(sources_hash)
            logger.info(f"[SOURCE-PROC] Cache HIT for {filename}")
            _update_source_progress(filename, 100,
                                    f'Готово (з кешу): {chunk_count} чанків',
                                    'ready',
                                    extra={
                                        'chunks': chunk_count,
                                        'char_count': char_count,
                                        'collection': collection_name,
                                        'cached': True
                                    })
            return {
                'status': 'success',
                'filename': filename,
                'chunks': chunk_count,
                'cached': True
            }
        
        # Build with progress reporting
        _update_source_progress(filename, 40, 'Створення ембеддингів...', 'processing')
        
        # We need to modify embedder to accept a progress callback
        # But for now, we can estimate progress based on batch processing
        collection_name = embedder.build_with_progress(
            chunks=chunks,
            sources_hash=sources_hash,
            source_files=[filename],
            progress_callback=lambda p, msg: _update_source_progress(
                filename,
                int(35 + p * 0.55),
                msg,
                'processing'
            )
        )
        
        logger.info(f"[SOURCE-PROC] Built ChromaDB collection '{collection_name}' for {filename}")
        
        # ── Done ───────────────────────────────────────────────────────────
        _update_source_progress(filename, 100,
                                f'Готово: {chunk_count} чанків, {char_count} символів',
                                'ready',
                                extra={
                                    'chunks': chunk_count,
                                    'char_count': char_count,
                                    'collection': collection_name,
                                    'cached': False
                                })
        
        return {
            'status': 'success',
            'filename': filename,
            'chunks': chunk_count,
            'cached': False
        }
        
    except Exception as e:
        logger.error(f"[SOURCE-PROC] Failed to process {filename}: {e}")
        import traceback
        traceback.print_exc()
        
        _update_source_progress(filename, 0, f'Помилка: {str(e)[:200]}',
                                'error', error=str(e))
        
        return {'status': 'error', 'filename': filename, 'error': str(e)}


@shared_task(bind=True, max_retries=1)
def process_all_pending_sources_task(self):
    """Process all source files that are in 'pending' status."""
    status = get_source_status()
    pending = [
        fn for fn, info in status.items()
        if info.get('status') in ('pending', 'error')
    ]
    
    results = []
    for filename in pending:
        file_path = os.path.join(SOURCES_DIR, filename)
        if os.path.isfile(file_path):
            logger.info(f"[SOURCE-PROC] Processing pending source: {filename}")
            # This will be processed synchronously (one by one)
            result = process_source_file_task(file_path, filename)
            results.append(result)
        else:
            logger.warning(f"[SOURCE-PROC] File not found: {file_path}")
    
    return {'processed': len(results), 'results': results}
