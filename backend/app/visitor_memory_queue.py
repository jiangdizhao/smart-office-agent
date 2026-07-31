from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.visitor_memory_store import visitor_memory_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VisitorMemoryJob:
    job_id: str
    identity_id: str
    display_name: str
    memory_summary: str
    recent_messages: list[dict[str, Any]]
    visit_id: str | None


class VisitorMemoryQueue:
    """Single-writer, idempotent background persistence for registered visitors."""

    def __init__(self) -> None:
        self._queue: queue.Queue[VisitorMemoryJob] = queue.Queue(maxsize=256)
        self._lock = threading.RLock()
        self._queued_keys: set[tuple[str, str]] = set()
        self._completed_keys: set[tuple[str, str]] = set()
        self._thread = threading.Thread(
            target=self._worker,
            name="visitor-memory-writer",
            daemon=True,
        )
        self._thread.start()

    def enqueue(
        self,
        *,
        identity_id: str,
        display_name: str,
        memory_summary: str,
        recent_messages: list[dict[str, Any]],
        visit_id: str | None,
    ) -> tuple[bool, str | None]:
        clean_identity = str(identity_id or "").strip()
        clean_visit = str(visit_id or "").strip()
        if not clean_identity:
            return False, None
        key = (clean_identity, clean_visit)
        with self._lock:
            if key in self._queued_keys or key in self._completed_keys:
                return True, None
            job = VisitorMemoryJob(
                job_id=f"memory_{uuid4().hex}",
                identity_id=clean_identity,
                display_name=str(display_name or clean_identity),
                memory_summary=str(memory_summary or ""),
                recent_messages=list(recent_messages or []),
                visit_id=clean_visit or None,
            )
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                logger.error(
                    "visitor_memory_queue_full",
                    extra={"identity_id": clean_identity, "visit_id": clean_visit},
                )
                return False, None
            self._queued_keys.add(key)
            return True, job.job_id

    def status(self) -> dict[str, int]:
        with self._lock:
            return {
                "pending": self._queue.qsize(),
                "queued_keys": len(self._queued_keys),
                "completed_keys": len(self._completed_keys),
            }

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            key = (job.identity_id, str(job.visit_id or ""))
            try:
                visitor_memory_store.save(
                    identity_id=job.identity_id,
                    display_name=job.display_name,
                    memory_summary=job.memory_summary,
                    recent_messages=job.recent_messages,
                    visit_id=job.visit_id,
                )
                with self._lock:
                    self._completed_keys.add(key)
                logger.info(
                    "visitor_memory_saved",
                    extra={
                        "job_id": job.job_id,
                        "identity_id": job.identity_id,
                        "visit_id": job.visit_id,
                    },
                )
            except Exception:
                logger.exception(
                    "visitor_memory_save_failed",
                    extra={
                        "job_id": job.job_id,
                        "identity_id": job.identity_id,
                        "visit_id": job.visit_id,
                    },
                )
            finally:
                with self._lock:
                    self._queued_keys.discard(key)
                self._queue.task_done()


visitor_memory_queue = VisitorMemoryQueue()
