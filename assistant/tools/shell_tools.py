"""Shell 工具：在工作目录内执行命令（带超时）。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .approval import ApprovalGate
from .base import Tool, ToolResult


def make_shell_tools(
    workdir: str | Path,
    gate: ApprovalGate | None = None,
    timeout: int = 60,
) -> list[Tool]:
    workdir = Path(workdir)

    def run_command(command: str) -> ToolResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0:
                return ToolResult(True, out if out.strip() else "（执行成功，无输出）")
            return ToolResult(False, f"退出码 {proc.returncode}\n{out}")
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"命令超时（>{timeout}s）")
        except Exception as e:
            return ToolResult(False, f"执行失败: {e}")

    return [
        Tool(
            "run_command",
            "在工作目录内执行 shell 命令并返回输出",
            {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的命令"}},
                "required": ["command"],
            },
            run_command,
        )
    ]
