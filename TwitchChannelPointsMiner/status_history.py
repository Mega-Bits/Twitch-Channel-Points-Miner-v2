"""SQLite-backed status and event history for the miner dashboard."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class StatusHistory:
    """Thread-safe history store using one SQLite database per Twitch account."""

    def __init__(self, path: Path, retention_days: int = 90) -> None:
        self.path = path
        self.retention_days = max(1, int(retention_days))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(path), timeout=30, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._last_snapshot_payload: dict[str, str] = {}
        self._open_sessions: dict[str, int] = {}
        self._last_cleanup = 0
        self._create_schema()
        self._close_stale_sessions()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT
                );
                CREATE INDEX IF NOT EXISTS events_timestamp_idx
                    ON events(timestamp DESC);
                CREATE INDEX IF NOT EXISTS events_event_idx
                    ON events(event, timestamp DESC);

                CREATE TABLE IF NOT EXISTS status_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS status_snapshots_kind_idx
                    ON status_snapshots(kind, timestamp DESC);

                CREATE TABLE IF NOT EXISTS watch_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    source TEXT NOT NULL,
                    campaign_id TEXT,
                    campaign_name TEXT,
                    started_at INTEGER NOT NULL,
                    ended_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS watch_sessions_started_idx
                    ON watch_sessions(started_at DESC);
                CREATE INDEX IF NOT EXISTS watch_sessions_channel_idx
                    ON watch_sessions(channel, started_at DESC);
                """
            )

    def _close_stale_sessions(self) -> None:
        now = int(time.time())
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE watch_sessions SET ended_at = ? WHERE ended_at IS NULL",
                (now,),
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def record_event(
        self,
        timestamp: int,
        event: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO events(timestamp, event, message, payload_json) VALUES (?, ?, ?, ?)",
                (
                    int(timestamp),
                    str(event),
                    str(message),
                    self._json(payload) if payload else None,
                ),
            )
        self.cleanup_if_due()

    def record_snapshot(self, kind: str, payload: dict[str, Any]) -> bool:
        serialized = self._json(payload)
        with self._lock:
            if self._last_snapshot_payload.get(kind) == serialized:
                return False
            self._last_snapshot_payload[kind] = serialized
            with self._connection:
                self._connection.execute(
                    "INSERT INTO status_snapshots(timestamp, kind, payload_json) VALUES (?, ?, ?)",
                    (int(time.time()), kind, serialized),
                )
        self.cleanup_if_due()
        return True

    def record_watch_slots(self, slots: list[dict[str, Any]]) -> None:
        now = int(time.time())
        current = {slot["username"]: slot for slot in slots}
        with self._lock, self._connection:
            for channel, session_id in list(self._open_sessions.items()):
                if channel in current:
                    continue
                self._connection.execute(
                    "UPDATE watch_sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                    (now, session_id),
                )
                self._open_sessions.pop(channel, None)

            for channel, slot in current.items():
                if channel in self._open_sessions:
                    continue
                campaign = slot.get("campaign") or {}
                cursor = self._connection.execute(
                    """
                    INSERT INTO watch_sessions(
                        channel, reason, source, campaign_id, campaign_name, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        channel,
                        slot.get("reason", "Priority"),
                        slot.get("source", "list"),
                        campaign.get("id"),
                        campaign.get("name"),
                        now,
                    ),
                )
                self._open_sessions[channel] = int(cursor.lastrowid)
        self.cleanup_if_due()

    def close_watch_sessions(self) -> None:
        now = int(time.time())
        with self._lock, self._connection:
            if self._open_sessions:
                self._connection.executemany(
                    "UPDATE watch_sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                    [
                        (now, session_id)
                        for session_id in self._open_sessions.values()
                    ],
                )
                self._open_sessions.clear()

    def cleanup_if_due(self) -> None:
        now = int(time.time())
        if now - self._last_cleanup < 24 * 60 * 60:
            return
        cutoff = now - self.retention_days * 24 * 60 * 60
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM events WHERE timestamp < ?", (cutoff,)
            )
            self._connection.execute(
                "DELETE FROM status_snapshots WHERE timestamp < ?", (cutoff,)
            )
            self._connection.execute(
                "DELETE FROM watch_sessions WHERE COALESCE(ended_at, started_at) < ?",
                (cutoff,),
            )
        self._last_cleanup = now

    def recent_events(
        self, limit: int = 100, event: str | None = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        query = "SELECT id, timestamp, event, message, payload_json FROM events"
        parameters: list[Any] = []
        if event:
            query += " WHERE event = ?"
            parameters.append(event)
        query += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [self._event_row(row) for row in rows]

    def recent_claims(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.recent_events(limit=limit, event="DROP_CLAIM")

    def recent_snapshots(
        self, kind: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        query = "SELECT id, timestamp, kind, payload_json FROM status_snapshots"
        parameters: list[Any] = []
        if kind:
            query += " WHERE kind = ?"
            parameters.append(kind)
        query += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def recent_watch_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, channel, reason, source, campaign_id, campaign_name,
                       started_at, ended_at
                FROM watch_sessions
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            event_count = self._connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
            snapshot_count = self._connection.execute(
                "SELECT COUNT(*) FROM status_snapshots"
            ).fetchone()[0]
            session_count = self._connection.execute(
                "SELECT COUNT(*) FROM watch_sessions"
            ).fetchone()[0]
        return {
            "path": str(self.path),
            "retention_days": self.retention_days,
            "events": event_count,
            "snapshots": snapshot_count,
            "watch_sessions": session_count,
        }

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = (
            json.loads(row["payload_json"]) if row["payload_json"] else None
        )
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "event": row["event"],
            "message": row["message"],
            "payload": payload,
        }

    def close(self) -> None:
        self.close_watch_sessions()
        with self._lock:
            self._connection.close()
