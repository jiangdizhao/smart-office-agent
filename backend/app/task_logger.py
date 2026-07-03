import json
from pathlib import Path
from threading import RLock
from typing import Any

from app.state_store import utc_now


LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_lock = RLock()


def log_task_record(task_id: str, record_type: str, data: dict[str, Any]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"task_{task_id}.jsonl"
    record = {
        "timestamp": utc_now().isoformat(),
        "task_id": task_id,
        "type": record_type,
        "data": data,
    }

    with _lock:
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    return path
