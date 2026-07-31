from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any


def _database_path() -> Path:
    configured = os.getenv("SMART_OFFICE_VISITOR_MEMORY_DB", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "data" / "visitor_memory.sqlite3"


class VisitorMemoryStore:
    """Persistent conversation summaries keyed only by a consented identity_id."""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or _database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS registered_visitor_memory (
                    identity_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    memory_summary TEXT NOT NULL DEFAULT '',
                    recent_messages_json TEXT NOT NULL DEFAULT '[]',
                    last_visit_id TEXT,
                    last_visit_ended_at_unix REAL,
                    updated_at_unix REAL NOT NULL
                );
                """
            )

    def load(self, identity_id: str | None) -> dict[str, Any] | None:
        clean_identity_id = str(identity_id or "").strip()
        if not clean_identity_id:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT identity_id, display_name, memory_summary,
                       recent_messages_json, last_visit_id,
                       last_visit_ended_at_unix, updated_at_unix
                FROM registered_visitor_memory
                WHERE identity_id = ?
                """,
                (clean_identity_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            recent_messages = json.loads(str(row["recent_messages_json"]) or "[]")
        except json.JSONDecodeError:
            recent_messages = []
        if not isinstance(recent_messages, list):
            recent_messages = []
        return {
            "identity_id": str(row["identity_id"]),
            "display_name": str(row["display_name"]),
            "memory_summary": str(row["memory_summary"] or ""),
            "recent_messages": recent_messages[-8:],
            "last_visit_id": row["last_visit_id"],
            "last_visit_ended_at_unix": row["last_visit_ended_at_unix"],
            "updated_at_unix": float(row["updated_at_unix"]),
        }

    def save(
        self,
        *,
        identity_id: str,
        display_name: str,
        memory_summary: str,
        recent_messages: list[dict[str, Any]],
        visit_id: str | None,
    ) -> dict[str, Any]:
        clean_identity_id = str(identity_id or "").strip()
        if not clean_identity_id:
            raise ValueError("identity_id is required for persistent visitor memory")
        clean_name = " ".join(str(display_name or "").strip().split())[:120]
        if not clean_name:
            clean_name = clean_identity_id
        clean_summary = " ".join(str(memory_summary or "").strip().split())[:8_000]
        normalized_messages: list[dict[str, str]] = []
        for item in recent_messages[-8:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()[:20]
            text = " ".join(str(item.get("text") or "").strip().split())[:1_000]
            if role and text:
                normalized_messages.append({"role": role, "text": text})
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO registered_visitor_memory(
                    identity_id, display_name, memory_summary,
                    recent_messages_json, last_visit_id,
                    last_visit_ended_at_unix, updated_at_unix
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    memory_summary = excluded.memory_summary,
                    recent_messages_json = excluded.recent_messages_json,
                    last_visit_id = excluded.last_visit_id,
                    last_visit_ended_at_unix = excluded.last_visit_ended_at_unix,
                    updated_at_unix = excluded.updated_at_unix
                """,
                (
                    clean_identity_id,
                    clean_name,
                    clean_summary,
                    json.dumps(normalized_messages, ensure_ascii=False, separators=(",", ":")),
                    str(visit_id or "").strip() or None,
                    now,
                    now,
                ),
            )
        return self.load(clean_identity_id) or {
            "identity_id": clean_identity_id,
            "display_name": clean_name,
            "memory_summary": clean_summary,
            "recent_messages": normalized_messages,
            "last_visit_id": visit_id,
            "last_visit_ended_at_unix": now,
            "updated_at_unix": now,
        }


visitor_memory_store = VisitorMemoryStore()
