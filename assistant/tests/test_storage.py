"""存储层冒烟测试：直接运行 `python tests/test_storage.py`。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.db import Database  # noqa: E402
from storage.repo import Repo  # noqa: E402


def test_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "test.db")
        repo = Repo(db)

        # user
        repo.upsert_user("u1", wxid="wxid_1", remark="老板", name="张三")
        assert repo.get_user("u1")["remark"] == "老板"

        # task / subtask / agent / event
        tid = repo.create_task("u1", "做一个计算器", "带 GUI")
        sid = repo.create_subtask(tid, 0, "写 UI", "用 tkinter", approval_list=["ui.py"])
        aid = repo.create_agent(tid, subtask_id=sid, model="deepseek-v4-pro", pid=123)
        repo.add_event(aid, "step_start", {"step": 1})
        repo.update_agent(aid, status="done")
        repo.update_subtask(sid, status="done", agent_id=aid)
        repo.update_task(tid, status="done")

        assert repo.get_agent(aid)["status"] == "done"
        assert len(repo.list_events(aid)) == 1
        assert len(repo.list_subtasks(tid)) == 1

        # message history
        repo.add_message("u1", "in", "你好")
        repo.add_message("u1", "out", "你好，我是监工")
        assert len(repo.get_history("u1")) == 2

        # decision
        repo.add_decision("u1", "你好", "你好，我是监工", "deepseek-v4-pro", task_id=tid)

        db.close()
        print("storage 冒烟测试通过")


if __name__ == "__main__":
    test_roundtrip()
