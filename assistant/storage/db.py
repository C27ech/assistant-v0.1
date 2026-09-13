"""SQLite 数据库：连接 + 建表。"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    wxid        TEXT,
    remark      TEXT,
    name        TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    plan        TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS subtasks (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    idx           INTEGER NOT NULL DEFAULT 0,
    title         TEXT,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'queued',
    approval_list TEXT,
    agent_id      TEXT,
    created_at    TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    subtask_id  TEXT,
    pid         INTEGER,
    status      TEXT NOT NULL DEFAULT 'running',
    state       TEXT,
    model       TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT,
    type        TEXT NOT NULL,
    payload     TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    direction   TEXT NOT NULL,
    content     TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT,
    task_id     TEXT,
    input       TEXT,
    output      TEXT,
    model       TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_subtasks_task ON subtasks(task_id);
CREATE INDEX IF NOT EXISTS idx_agents_task ON agents(task_id);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._lock = threading.Lock()
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            cur = self.conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params=()) -> Optional[dict]:
        with self._lock:
            cur = self.conn.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()
