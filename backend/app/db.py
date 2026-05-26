from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def find_project_root(path: str) -> str:
    try:
        current = Path(path).resolve()
        for parent in [current, *current.parents]:
            # Check for common project markers
            if (parent / ".git").exists() or \
               (parent / ".hg").exists() or \
               (parent / "package.json").exists() or \
               (parent / "pyproject.toml").exists() or \
               (parent / "go.mod").exists() or \
               (parent / "Cargo.toml").exists():
                return str(parent)
        return str(current)
    except Exception:
        return path


class Store:
    def __init__(self, sqlite_path: str, session_gap_seconds: int) -> None:
        self.sqlite_path = sqlite_path
        self.session_gap = timedelta(seconds=session_gap_seconds)
        self.lock = threading.RLock()
        with self.lock:
            Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(sqlite_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._init_schema()

    def is_ready(self) -> bool:
        with self.lock:
            try:
                self.conn.execute("SELECT 1")
                return True
            except Exception:
                return False

    def _init_schema(self) -> None:
        with self.lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cwd TEXT NOT NULL,
                    project_root TEXT,
                    started_at TEXT NOT NULL,
                    last_event_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    command TEXT NOT NULL,
                    output TEXT NOT NULL,
                    exit_code INTEGER NOT NULL,
                    duration_ms INTEGER,
                    cwd TEXT NOT NULL,
                    project_root TEXT,
                    category TEXT NOT NULL,
                    root_cause TEXT,
                    captured_at TEXT NOT NULL,
                    env_json TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                """
            )
            # Migration: Add project_root if it doesn't exist
            try:
                self.conn.execute("ALTER TABLE sessions ADD COLUMN project_root TEXT")
            except sqlite3.OperationalError:
                pass # Already exists
            try:
                self.conn.execute("ALTER TABLE events ADD COLUMN project_root TEXT")
            except sqlite3.OperationalError:
                pass # Already exists
            try:
                self.conn.execute("ALTER TABLE events ADD COLUMN root_cause_confidence TEXT")
            except sqlite3.OperationalError:
                pass # Already exists

            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS failure_fixes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    failure_event_id INTEGER NOT NULL,
                    success_event_id INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id),
                    FOREIGN KEY(failure_event_id) REFERENCES events(id),
                    FOREIGN KEY(success_event_id) REFERENCES events(id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
                CREATE INDEX IF NOT EXISTS idx_events_captured_at ON events(captured_at);
                CREATE INDEX IF NOT EXISTS idx_failure_fixes_session_id ON failure_fixes(session_id);
                """
            )
            self.conn.commit()
            self.conn.commit()

    def _find_open_session(self, project_root: str, event_time: datetime) -> int | None:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT id, last_event_at
                FROM sessions
                WHERE project_root = ?
                ORDER BY last_event_at DESC
                LIMIT 1
                """,
                (project_root,),
            ).fetchone()

            if row is None:
                return None

            last_event_at = parse_iso(row["last_event_at"])
            if event_time - last_event_at <= self.session_gap:
                return int(row["id"])
            return None

    def _create_session(self, cwd: str, project_root: str, event_time: datetime) -> int:
        with self.lock:
            cursor = self.conn.execute(
                """
                INSERT INTO sessions (cwd, project_root, started_at, last_event_at)
                VALUES (?, ?, ?, ?)
                """,
                (cwd, project_root, to_iso(event_time), to_iso(event_time)),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def _touch_session(self, session_id: int, event_time: datetime) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE sessions SET last_event_at = ? WHERE id = ?",
                (to_iso(event_time), session_id),
            )
            self.conn.commit()

    def add_event(
        self,
        command: str,
        output: str,
        exit_code: int,
        duration_ms: int | None,
        cwd: str,
        project_root: str,
        category: str,
        root_cause: str | None,
        root_cause_confidence: str | None,
        event_time: datetime,
        env: dict[str, str] | None,
    ) -> tuple[int, int]:
        with self.lock:
            session_id = self._find_open_session(project_root, event_time)
            if session_id is None:
                session_id = self._create_session(cwd, project_root, event_time)
            else:
                self._touch_session(session_id, event_time)

            cursor = self.conn.execute(
                """
                INSERT INTO events (
                    session_id,
                    command,
                    output,
                    exit_code,
                    duration_ms,
                    cwd,
                    project_root,
                    category,
                    root_cause,
                    root_cause_confidence,
                    captured_at,
                    env_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    command,
                    output,
                    exit_code,
                    duration_ms,
                    cwd,
                    project_root,
                    category,
                    root_cause,
                    root_cause_confidence,
                    to_iso(event_time),
                    json.dumps(env or {}),
                ),
            )
            self.conn.commit()
            event_id = int(cursor.lastrowid)
            return event_id, session_id

    def recent_session_events(self, session_id: int, limit: int = 50) -> list[sqlite3.Row]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT *
                FROM events
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            return list(reversed(rows))

    def add_failure_fix(self, session_id: int, failure_event_id: int, success_event_id: int, summary: str) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO failure_fixes (session_id, failure_event_id, success_event_id, summary, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, failure_event_id, success_event_id, summary, to_iso(utc_now())),
            )
            self.conn.commit()

    def find_recent_failure_for_command(self, session_id: int, command: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(
                """
                SELECT *
                FROM events
                WHERE session_id = ?
                  AND command = ?
                  AND exit_code != 0
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_id, command),
            ).fetchone()

    def find_recent_failure_cross_session(self, project_root: str, command: str, hours_lookback: int = 48) -> sqlite3.Row | None:
        with self.lock:
            cutoff = to_iso(utc_now() - timedelta(hours=hours_lookback))
            return self.conn.execute(
                """
                SELECT *
                FROM events
                WHERE project_root = ?
                  AND command = ?
                  AND exit_code != 0
                  AND captured_at >= ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (project_root, command, cutoff),
            ).fetchone()

    def get_event(self, event_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    def update_event_correction(
        self,
        event_id: int,
        category: str | None = None,
        root_cause: str | None = None,
    ) -> sqlite3.Row | None:
        with self.lock:
            event = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                return None

            updates: list[str] = []
            params: list[str | int] = []
            if category is not None:
                updates.append("category = ?")
                params.append(category)
            if root_cause is not None:
                updates.append("root_cause = ?")

                params.append(root_cause)

            if not updates:
                return event

            params.append(event_id)
            self.conn.execute(
                f"UPDATE events SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            self.conn.commit()
            return self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    def search_events_like(self, query: str, limit: int) -> list[sqlite3.Row]:
        with self.lock:
            like_query = f"%{query}%"
            rows = self.conn.execute(
                """
                SELECT *
                FROM events
                WHERE command LIKE ? OR output LIKE ? OR category LIKE ? OR IFNULL(root_cause, '') LIKE ?
                ORDER BY captured_at DESC
                LIMIT ?
                """,
                (like_query, like_query, like_query, like_query, limit),
            ).fetchall()
            return list(rows)

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()

    def find_session_by_query(self, query: str) -> sqlite3.Row | None:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT s.id, s.cwd, s.started_at, s.last_event_at, COUNT(e.id) AS total_hits
                FROM sessions s
                JOIN events e ON s.id = e.session_id
                WHERE e.command LIKE ? OR e.output LIKE ? OR e.category LIKE ?
                GROUP BY s.id
                ORDER BY total_hits DESC, s.last_event_at DESC
                LIMIT 1
                """,
                (f"%{query}%", f"%{query}%", f"%{query}%"),
            ).fetchone()
            return row

    def weekly_stats(self, days: int = 7) -> dict[str, Any]:
        with self.lock:
            cutoff = to_iso(utc_now() - timedelta(days=days))

            aggregate = self.conn.execute(
                """
                SELECT
                    COUNT(*) AS total_events,
                    SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END) AS total_failures
                FROM events
                WHERE captured_at >= ?
                """,
                (cutoff,),
            ).fetchone()

            category_rows = self.conn.execute(
                """
                SELECT
                    category,
                    COUNT(*) AS total,
                    SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END) AS failures
                FROM events
                WHERE captured_at >= ?
                GROUP BY category
                ORDER BY total DESC
                LIMIT 5
                """,
                (cutoff,),
            ).fetchall()

            recurring_rows = self.conn.execute(
                """
                SELECT command, COUNT(*) AS failures
                FROM events
                WHERE captured_at >= ? AND exit_code != 0
                GROUP BY command
                HAVING COUNT(*) > 1
                ORDER BY failures DESC
                LIMIT 5
                """,
                (cutoff,),
            ).fetchall()

            total_events = int(aggregate["total_events"] or 0)
            total_failures = int(aggregate["total_failures"] or 0)

            return {
                "period_days": days,
                "total_events": total_events,
                "total_failures": total_failures,
                "failure_rate": (float(total_failures) / total_events) if total_events else 0.0,
                "top_categories": [
                    {
                        "category": row["category"],
                        "total": int(row["total"]),
                        "failures": int(row["failures"] or 0),
                    }
                    for row in category_rows
                ],
                "recurring_failures": [row["command"] for row in recurring_rows],
            }

    def preflight_warnings(self, task: str, commands: list[str]) -> list[dict[str, Any]]:
        with self.lock:
            terms = [task, *commands]
            warnings: list[dict[str, Any]] = []

            for term in terms:
                rows = self.conn.execute(
                    """
                    SELECT id, command, root_cause, output
                    FROM events
                    WHERE exit_code != 0 AND (command LIKE ? OR output LIKE ?)
                    ORDER BY captured_at DESC
                    LIMIT 3
                    """,
                    (f"%{term}%", f"%{term}%"),
                ).fetchall()
                if not rows:
                    continue

                cause = next((row["root_cause"] for row in rows if row["root_cause"]), "prior failures detected")
                warnings.append(
                    {
                        "severity": "medium",
                        "message": f"Historical failures found for '{term}': {cause}",
                        "evidence_event_ids": [int(row["id"]) for row in rows],
                    }
                )

            unique = {}
            for warning in warnings:
                unique[warning["message"]] = warning
            return list(unique.values())
