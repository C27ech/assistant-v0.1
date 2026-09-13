"""仓储层：对 users/tasks/subtasks/agents/events/messages/decisions 的存取。"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from .db import Database


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Repo:
    def __init__(self, db: Database):
        self.db = db

    # ---------- users ----------
    def upsert_user(self, user_id: str, wxid: str = "", remark: str = "", name: str = "") -> None:
        self.db.execute(
            """
            INSERT INTO users (user_id, wxid, remark, name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                wxid = excluded.wxid,
                remark = excluded.remark,
                name = excluded.name
            """,
            (user_id, wxid, remark, name),
        )

    def get_user(self, user_id: str) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    # ---------- messages ----------
    def add_message(self, user_id: str, direction: str, content: str) -> int:
        cur = self.db.execute(
            "INSERT INTO messages (user_id, direction, content) VALUES (?, ?, ?)",
            (user_id, direction, content),
        )
        return cur.lastrowid

    def get_history(self, user_id: str, limit: int = 20) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return list(reversed(rows))

    def search_history(
        self,
        user_id: str,
        query: str,
        k: int = 5,
        exclude_recent: int = 20,
        max_scan: int = 1000,
    ) -> list[dict]:
        """语义检索：返回与 query 内容最相关的历史消息（排除最近 exclude_recent 条）。

        基于 TF-IDF（字符 n-gram）在消息文本上做余弦相似度召回。
        """
        query = (query or "").strip()
        if not query:
            return []
        rows = self.db.query(
            "SELECT id, direction, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, max_scan),
        )
        if len(rows) <= exclude_recent:
            return []
        from .memory import search  # 延迟导入，避免循环引用

        candidates = rows[exclude_recent:]  # 排除最近 exclude_recent 条（这些已在上下文里）
        docs = [r["content"] or "" for r in candidates]
        idxs = search(docs, query, k)
        return [candidates[i] for i in idxs]

    # ---------- tasks ----------
    def create_task(self, user_id: str, title: str, description: str = "") -> str:
        task_id = _uid("task")
        self.db.execute(
            "INSERT INTO tasks (id, user_id, title, description, status) VALUES (?, ?, ?, ?, 'pending')",
            (task_id, user_id, title, description),
        )
        return task_id

    def update_task(self, task_id: str, **fields: Any) -> None:
        self._update("tasks", task_id, fields)

    def get_task(self, task_id: str) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))

    def list_tasks(self, user_id: Optional[str] = None) -> list[dict]:
        if user_id:
            return self.db.query(
                "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            )
        return self.db.query("SELECT * FROM tasks ORDER BY created_at DESC")

    # ---------- subtasks ----------
    def create_subtask(
        self,
        task_id: str,
        idx: int,
        title: str,
        description: str = "",
        approval_list: Optional[list] = None,
    ) -> str:
        sid = _uid("sub")
        self.db.execute(
            "INSERT INTO subtasks (id, task_id, idx, title, description, approval_list) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, task_id, idx, title, description, json.dumps(approval_list or [], ensure_ascii=False)),
        )
        return sid

    def update_subtask(self, sid: str, **fields: Any) -> None:
        self._update("subtasks", sid, fields)

    def get_subtask(self, sid: str) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM subtasks WHERE id = ?", (sid,))

    def list_subtasks(self, task_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM subtasks WHERE task_id = ? ORDER BY idx ASC", (task_id,)
        )

    # ---------- agents ----------
    def create_agent(
        self,
        task_id: str,
        subtask_id: str = "",
        model: str = "",
        pid: Optional[int] = None,
    ) -> str:
        aid = _uid("agent")
        self.db.execute(
            "INSERT INTO agents (id, task_id, subtask_id, pid, model, status) VALUES (?, ?, ?, ?, ?, 'running')",
            (aid, task_id, subtask_id, pid, model),
        )
        return aid

    def update_agent(self, aid: str, **fields: Any) -> None:
        self._update("agents", aid, fields)

    def get_agent(self, aid: str) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM agents WHERE id = ?", (aid,))

    def list_agents(self, task_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM agents WHERE task_id = ? ORDER BY created_at ASC", (task_id,)
        )

    # ---------- events ----------
    def add_event(self, agent_id: str, type_: str, payload: Optional[dict] = None) -> int:
        cur = self.db.execute(
            "INSERT INTO events (agent_id, type, payload) VALUES (?, ?, ?)",
            (agent_id, type_, json.dumps(payload, ensure_ascii=False) if payload is not None else None),
        )
        return cur.lastrowid

    def list_events(self, agent_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        if agent_id:
            return self.db.query(
                "SELECT * FROM events WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
                (agent_id, limit),
            )
        return self.db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # ---------- decisions ----------
    def add_decision(
        self,
        user_id: str,
        input_: str,
        output_: str,
        model: str,
        task_id: Optional[str] = None,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO decisions (user_id, task_id, input, output, model) VALUES (?, ?, ?, ?, ?)",
            (user_id, task_id, input_, output_, model),
        )
        return cur.lastrowid

    # ---------- helper ----------
    def _update(self, table: str, row_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())
        values.append(row_id)
        self.db.execute(
            f"UPDATE {table} SET {sets}, updated_at = datetime('now','localtime') WHERE id = ?",
            values,
        )
