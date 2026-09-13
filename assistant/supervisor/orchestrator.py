"""编排器：启动/重启/关闭代码AI 子进程，并跟踪其状态。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from config.settings import Settings
from storage.repo import Repo

BASE_DIR = Path(__file__).resolve().parent.parent


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        repo: Repo,
        launch_cmd: Optional[Callable[[str], list]] = None,
        log_dir: Optional[str | Path] = None,
    ):
        self.settings = settings
        self.repo = repo
        self.launch_cmd = launch_cmd  # 测试时可注入自定义启动命令
        self.log_dir = Path(log_dir) if log_dir else BASE_DIR / "logs"
        self.procs: dict[str, tuple[subprocess.Popen, object]] = {}

    def _build_cmd(self, agent_id: str) -> list:
        return [
            sys.executable,
            "-m",
            "agents.worker",
            "--agent-id",
            agent_id,
            "--db-path",
            str(self.settings.db_file),
        ]

    def spawn_agent(
        self,
        task_id: str,
        task: str,
        workdir: str,
        subtask_id: str = "",
        approval_list: Optional[list] = None,
        model: str = "",
        max_steps: Optional[int] = None,
        max_tokens: Optional[int] = None,
        stuck_timeout: Optional[int] = None,
        heartbeat_interval: Optional[int] = None,
    ) -> str:
        """创建 agent 记录并启动子进程，返回 agent_id。"""
        agent_id = self.repo.create_agent(
            task_id, subtask_id=subtask_id, model=model or self.settings.coder_model
        )
        state = {
            "task": task,
            "workdir": str(workdir),
            "approval_list": approval_list or [],
            "max_steps": max_steps,
            "max_tokens": max_tokens,
            "stuck_timeout": stuck_timeout,
            "heartbeat_interval": heartbeat_interval,
        }
        self.repo.update_agent(agent_id, state=json.dumps(state, ensure_ascii=False))
        self._launch(agent_id)
        return agent_id

    def _launch(self, agent_id: str) -> subprocess.Popen:
        cmd = self.launch_cmd(agent_id) if self.launch_cmd else self._build_cmd(agent_id)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(self.log_dir / f"agent_{agent_id}.log", "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        self.procs[agent_id] = (proc, log_file)
        self.repo.update_agent(agent_id, pid=proc.pid, status="running")
        return proc

    def stop_agent(self, agent_id: str) -> None:
        """终止子进程并标记 stopped。"""
        entry = self.procs.pop(agent_id, None)
        if entry:
            proc, log_file = entry
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            try:
                log_file.close()
            except Exception:
                pass
        self.repo.update_agent(agent_id, status="stopped")

    def restart_agent(self, agent_id: str) -> None:
        """先停止再重新启动（worker 会从断点续跑）。"""
        self.stop_agent(agent_id)
        self._launch(agent_id)
        self.repo.update_agent(agent_id, status="running")

    def is_running(self, agent_id: str) -> bool:
        entry = self.procs.get(agent_id)
        return entry is not None and entry[0].poll() is None

    def shutdown(self) -> None:
        for agent_id in list(self.procs.keys()):
            self.stop_agent(agent_id)
