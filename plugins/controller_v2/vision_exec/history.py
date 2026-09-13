"""agent loop 历史持久化（标准库 SQLite，无重依赖）。

- 历史存到 ``vision_exec/agent_history.db``；
- 截图缩略图存到 ``vision_exec/frames/``，历史只保存 frame_path + 文本描述，
  不保存全量 base64；
- 进程重启后仍可读取历史，并用 BM25 做检索，实现可续跑的记忆。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .retrieval import BM25

_FRAMES_SUBDIR = "frames"
_DB_FILENAME = "agent_history.db"


def _utcnow_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return datetime.now().isoformat(timespec="seconds")


def _project_root() -> Path:
    """返回项目根目录（vision_exec 的上一级）。"""
    return Path(__file__).resolve().parent.parent


def _db_path() -> Path:
    return Path(__file__).resolve().parent / _DB_FILENAME


def frames_dir() -> Path:
    """截图缩略图落盘目录，不存在时自动创建。"""
    d = Path(__file__).resolve().parent / _FRAMES_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """创建历史表与索引（幂等）。"""
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                round_no INTEGER,
                kind TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                frame_path TEXT,
                meta TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_history_session
                ON agent_history(session_id, id);
            CREATE INDEX IF NOT EXISTS idx_history_round
                ON agent_history(session_id, round_no);
            """
        )
        conn.commit()
    finally:
        conn.close()


def new_session(task: str, session_id: Optional[str] = None) -> str:
    """创建一条 session 记录，返回 session_id。"""
    init_db()
    sid = session_id or uuid.uuid4().hex
    now = _utcnow_iso()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO sessions
                (session_id, task, status, created_at, updated_at)
            VALUES (?, ?, 'running', ?, ?)
            """,
            (sid, task, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


def update_session_status(session_id: str, status: str) -> None:
    """更新 session 状态（running / done / stuck / fail / hard_limit）。"""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            (str(status), _utcnow_iso(), session_id),
        )
        conn.commit()
    finally:
        conn.close()


def add_history(
    session_id: str,
    kind: str,
    text: str,
    round_no: Optional[int] = None,
    frame_path: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    """追加一条历史记录，返回自增 id。"""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO agent_history
                (session_id, round_no, kind, text, frame_path, meta, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                round_no,
                str(kind),
                str(text or ""),
                frame_path,
                json.dumps(meta or {}, ensure_ascii=False),
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _row_to_entry(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "session_id": str(row["session_id"]),
        "round_no": row["round_no"],
        "kind": str(row["kind"]),
        "text": str(row["text"]),
        "frame_path": row["frame_path"],
        "meta": _safe_json(row["meta"]),
        "created_at": str(row["created_at"]),
    }


def _safe_json(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def count_history(session_id: Optional[str] = None) -> int:
    """返回历史条数；session_id 为 None 时返回全库条数。"""
    init_db()
    conn = _connect()
    try:
        if session_id:
            cur = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_history WHERE session_id = ?",
                (session_id,),
            )
        else:
            cur = conn.execute("SELECT COUNT(*) AS c FROM agent_history")
        row = cur.fetchone()
        return int(row["c"]) if row else 0
    finally:
        conn.close()


def list_history(
    session_id: Optional[str] = None,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    """按 id 升序返回历史条目；session_id 为 None 时返回全库。"""
    init_db()
    conn = _connect()
    try:
        if session_id:
            cur = conn.execute(
                """
                SELECT * FROM agent_history
                WHERE session_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, int(limit)),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM agent_history ORDER BY id ASC LIMIT ?",
                (int(limit),),
            )
        return [_row_to_entry(row) for row in cur.fetchall()]
    finally:
        conn.close()


def resolve_frame_path(frame_path: Optional[str]) -> Optional[Path]:
    """把历史里存的 frame_path 解析为本地绝对路径。

    - 绝对路径直接返回；
    - 相对路径按「项目根目录」解析。
    """
    if not frame_path:
        return None
    p = Path(str(frame_path)).expanduser()
    if p.is_absolute():
        return p
    return _project_root() / p


def search_history(
    query: str,
    top_k: int = 50,
    session_id: Optional[str] = None,
) -> List[Tuple[Dict[str, Any], float]]:
    """用 BM25 在历史条目上检索，返回 [(entry, score), ...] 降序。"""
    entries = list_history(session_id=session_id)
    if not entries:
        return []

    index = BM25()
    for entry in entries:
        # 检索文本：kind 作为前缀，叠加正文；有截图路径时也把路径文件名纳入。
        searchable = f"[{entry['kind']}] {entry['text']}"
        if entry.get("frame_path"):
            searchable += f" frame:{Path(str(entry['frame_path'])).name}"
        index.add(int(entry["id"]), searchable)

    results = index.search(query, top_k=top_k)
    by_id = {int(e["id"]): e for e in entries}
    out: List[Tuple[Dict[str, Any], float]] = []
    for doc_id, score in results:
        entry = by_id.get(doc_id)
        if entry is not None:
            out.append((entry, float(score)))
    return out


def update_history_text(entry_id: int, text: str) -> None:
    """更新一条历史记录的文本（例如给截图条目补上模型观察描述）。"""
    init_db()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE agent_history SET text = ? WHERE id = ?",
            (str(text or ""), int(entry_id)),
        )
        conn.commit()
    finally:
        conn.close()
