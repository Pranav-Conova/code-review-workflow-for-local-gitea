"""Shared in-process state for the PR reviewer dashboard."""

import json
import os
import threading
import time
from queue import Queue

from config import DATA_DIR, REVIEWED_FILE

REVIEWS_FILE = os.path.join(DATA_DIR, "reviews.json")

# --- Shared state ---
review_queue = Queue()
review_history = {}  # pr_key -> review metadata dict
worker_status = {}   # thread_name -> {"status": "idle"|"reviewing", "pr_key": ..., "started_at": ...}
start_time = time.time()

_lock = threading.Lock()


def load_history():
    """Load review history from disk. Auto-migrates old reviewed.json format."""
    global review_history

    # Try new format first
    if os.path.exists(REVIEWS_FILE):
        with open(REVIEWS_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            review_history = data
            return

    # Fall back to old format (flat array of PR keys)
    if os.path.exists(REVIEWED_FILE):
        with open(REVIEWED_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            review_history = {
                key: {
                    "pr_key": key,
                    "status": "done",
                    "queued_at": None,
                    "started_at": None,
                    "completed_at": None,
                    "duration_seconds": None,
                    "triggered_by": "poller",
                    "error": None,
                }
                for key in data
            }
            save_history()
            return

    review_history = {}


def save_history():
    """Persist review history to disk (atomic write)."""
    tmp = REVIEWS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(review_history, f, indent=2)
    os.replace(tmp, REVIEWS_FILE)


def update_review(pr_key, status, metadata=None):
    """Create or update a review entry. Thread-safe."""
    with _lock:
        if pr_key not in review_history:
            review_history[pr_key] = {
                "pr_key": pr_key,
                "status": status,
                "queued_at": None,
                "started_at": None,
                "completed_at": None,
                "duration_seconds": None,
                "triggered_by": "poller",
                "error": None,
            }
        entry = review_history[pr_key]
        entry["status"] = status

        if metadata:
            entry.update(metadata)

        if status == "queued":
            entry["queued_at"] = entry.get("queued_at") or time.time()
        elif status == "in-progress":
            entry["started_at"] = time.time()
        elif status in ("done", "failed"):
            entry["completed_at"] = time.time()
            if entry.get("started_at"):
                entry["duration_seconds"] = round(
                    entry["completed_at"] - entry["started_at"], 1
                )

        save_history()


def set_worker_status(thread_name, status, pr_key=None):
    """Update a worker thread's current status. Thread-safe."""
    with _lock:
        worker_status[thread_name] = {
            "status": status,
            "pr_key": pr_key,
            "started_at": time.time() if status == "reviewing" else None,
        }


def get_status_snapshot():
    """Return a snapshot of system status for the API."""
    with _lock:
        history_copy = dict(review_history)
        workers_copy = dict(worker_status)

    by_status = {}
    for entry in history_copy.values():
        s = entry.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "uptime_seconds": round(time.time() - start_time),
        "queue_size": review_queue.qsize(),
        "workers": [
            {
                "name": name,
                "status": info.get("status", "unknown"),
                "current_pr": info.get("pr_key"),
                "reviewing_since": info.get("started_at"),
            }
            for name, info in workers_copy.items()
        ],
        "total_reviews": len(history_copy),
        "reviews_by_status": by_status,
    }
