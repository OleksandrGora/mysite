"""
Cleanup and maintenance utilities for Stage 7.
"""
import os
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from loguru import logger


def get_task_dirs(data_dir: str) -> List[Path]:
    """Get all task directories."""
    tasks_dir = Path(data_dir) / 'tasks'
    if not tasks_dir.exists():
        return []
    
    return [d for d in tasks_dir.iterdir() if d.is_dir()]


def load_task_json(task_dir: Path) -> Optional[Dict]:
    """Load task.json from task directory."""
    task_json = task_dir / 'task.json'
    if not task_json.exists():
        return None
    
    try:
        with open(task_json, 'r') as f:
            return json.load(f)
    except:
        return None


def cleanup_old_tasks(
    data_dir: str,
    max_age_days: int = 7,
    dry_run: bool = False
) -> Dict[str, int]:
    """
    Clean up old completed tasks.
    
    Args:
        data_dir: Data directory path
        max_age_days: Delete tasks older than this
        dry_run: If True, don't actually delete
        
    Returns:
        Stats dict with counts
    """
    stats = {'scanned': 0, 'deleted': 0, 'skipped': 0, 'errors': 0}
    
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    
    for task_dir in get_task_dirs(data_dir):
        stats['scanned'] += 1
        
        task_data = load_task_json(task_dir)
        if not task_data:
            logger.warning(f"No task.json in {task_dir.name}")
            stats['errors'] += 1
            continue
        
        # Check if completed
        status = task_data.get('status')
        if status not in ['completed', 'failed']:
            stats['skipped'] += 1
            continue
        
        # Check age
        completed_at = task_data.get('completed_at')
        if not completed_at:
            # Use file modification time as fallback
            mtime = datetime.fromtimestamp(task_dir.stat().st_mtime)
            if mtime > cutoff:
                stats['skipped'] += 1
                continue
        else:
            # Parse ISO timestamp
            try:
                completed_time = datetime.fromisoformat(completed_at.replace('Z', '+00:00').replace('+00:00', ''))
                if completed_time > cutoff:
                    stats['skipped'] += 1
                    continue
            except:
                stats['skipped'] += 1
                continue
        
        # Delete old task
        logger.info(f"Cleaning up old task: {task_dir.name} (status={status})")
        
        if not dry_run:
            try:
                shutil.rmtree(task_dir)
                stats['deleted'] += 1
            except Exception as e:
                logger.error(f"Failed to delete {task_dir.name}: {e}")
                stats['errors'] += 1
        else:
            logger.info(f"[DRY RUN] Would delete {task_dir.name}")
            stats['deleted'] += 1
    
    return stats


def cleanup_chroma_collections(
    data_dir: str,
    max_age_days: int = 7,
    dry_run: bool = False
) -> int:
    """Clean up orphaned ChromaDB collections."""
    from .rag.embedder import CHROMA_DB_DIR
    
    deleted = 0
    
    # Get active task IDs
    active_tasks = set()
    for task_dir in get_task_dirs(data_dir):
        active_tasks.add(f"task_{task_dir.name}")
    
    # Check ChromaDB collections
    try:
        import chromadb
        from chromadb.config import Settings
        
        client = chromadb.Client(Settings(
            persist_directory=CHROMA_DB_DIR,
            anonymized_telemetry=False
        ))
        
        for collection in client.list_collections():
            name = collection.name if hasattr(collection, 'name') else str(collection)
            
            # Skip non-task collections
            if not name.startswith('task_'):
                continue
            
            # Check if task exists
            if name not in active_tasks:
                logger.info(f"Deleting orphaned Chroma collection: {name}")
                
                if not dry_run:
                    try:
                        client.delete_collection(name)
                        deleted += 1
                    except Exception as e:
                        logger.error(f"Failed to delete collection {name}: {e}")
                else:
                    logger.info(f"[DRY RUN] Would delete collection {name}")
                    deleted += 1
    except Exception as e:
        logger.error(f"Failed to cleanup Chroma collections: {e}")
    
    return deleted


def get_disk_usage(data_dir: str) -> Dict[str, int]:
    """Get disk usage by task."""
    usage = {}
    
    for task_dir in get_task_dirs(data_dir):
        try:
            total_size = sum(f.stat().st_size for f in task_dir.rglob('*') if f.is_file())
            usage[task_dir.name] = total_size
        except:
            usage[task_dir.name] = 0
    
    return usage


def get_task_stats(data_dir: str) -> Dict[str, any]:
    """Get statistics about all tasks."""
    stats = {
        'total': 0,
        'by_status': {},
        'total_size': 0,
        'oldest_task': None,
        'newest_task': None
    }
    
    for task_dir in get_task_dirs(data_dir):
        task_data = load_task_json(task_dir)
        if not task_data:
            continue
        
        stats['total'] += 1
        
        # Count by status
        status = task_data.get('status', 'unknown')
        stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
        
        # Created time
        created = task_data.get('created_at')
        if created:
            try:
                dt = datetime.fromisoformat(created.replace('Z', '+00:00').replace('+00:00', ''))
                if not stats['oldest_task'] or dt < stats['oldest_task']:
                    stats['oldest_task'] = dt
                if not stats['newest_task'] or dt > stats['newest_task']:
                    stats['newest_task'] = dt
            except:
                pass
    
    # Calculate total size
    usage = get_disk_usage(data_dir)
    stats['total_size'] = sum(usage.values())
    stats['total_size_mb'] = stats['total_size'] / (1024 * 1024)
    
    return stats


if __name__ == '__main__':
    # Test cleanup
    data_dir = '/home/openclaw/mysite/data'
    print("Task stats:", get_task_stats(data_dir))
    print("Disk usage:", get_disk_usage(data_dir))
