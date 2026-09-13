"""编排器冒烟测试：spawn/restart/stop，用假启动命令（sleep 子进程）。

运行：`python tests/test_orchestrator.py`
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings  # noqa: E402
from storage.db import Database  # noqa: E402
from storage.repo import Repo  # noqa: E402
from supervisor.orchestrator import Orchestrator  # noqa: E402


def test_orchestrator() -> None:
    with tempfile.TemporaryDirectory() as d:
        settings = Settings()
        settings.db_path = str(Path(d) / "t.db")
        db = Database(settings.db_file)
        repo = Repo(db)

        def launch_cmd(agent_id: str):
            # 睡 30 秒的子进程，便于测试 stop/restart
            return [sys.executable, "-c", "import time; time.sleep(30)"]

        orch = Orchestrator(settings, repo, launch_cmd=launch_cmd, log_dir=Path(d) / "logs")

        tid = repo.create_task("u1", "测试任务")
        aid = orch.spawn_agent(tid, "写个文件", str(Path(d)))
        assert orch.is_running(aid) is True
        agent = repo.get_agent(aid)
        assert agent["status"] == "running"
        pid1 = agent["pid"]

        orch.restart_agent(aid)
        agent = repo.get_agent(aid)
        assert agent["status"] == "running"
        assert agent["pid"] != pid1
        assert orch.is_running(aid) is True

        orch.stop_agent(aid)
        assert orch.is_running(aid) is False
        assert repo.get_agent(aid)["status"] == "stopped"

        orch.shutdown()
        db.close()
        print("orchestrator 冒烟测试通过 OK")


if __name__ == "__main__":
    test_orchestrator()
