"""工具层冒烟测试：文件工具、路径安全、审批闸门、shell 工具。

运行：`python tests/test_tools.py`
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.approval import ApprovalGate  # noqa: E402
from tools.file_tools import NEED_APPROVAL_PREFIX, make_file_tools  # noqa: E402
from tools.shell_tools import make_shell_tools  # noqa: E402


def _find(tools, name):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"缺少工具 {name}")


def test_file_tools() -> None:
    with tempfile.TemporaryDirectory() as d:
        tools = make_file_tools(d)
        write = _find(tools, "write_file")
        read = _find(tools, "read_file")
        ls = _find(tools, "list_dir")

        r = write.func(path="a.txt", content="你好")
        assert r.success, r.content
        r = read.func(path="a.txt")
        assert r.success and r.content == "你好"
        r = ls.func(path=".")
        assert "a.txt" in r.content

        # 路径越界应被拒绝
        r = read.func(path="../etc/passwd")
        assert not r.success
        print("file tools ok OK")


def test_approval_gate() -> None:
    gate = ApprovalGate(["secret.txt", "*.env", "deploy/**"])
    assert gate.requires_approval("secret.txt")
    assert gate.requires_approval("prod.env")
    assert gate.requires_approval("deploy/main.py")
    assert not gate.requires_approval("src/main.py")

    with tempfile.TemporaryDirectory() as d:
        tools = make_file_tools(d, gate)
        write = _find(tools, "write_file")
        r = write.func(path="secret.txt", content="x")
        assert not r.success and r.content.startswith(NEED_APPROVAL_PREFIX)
        assert not (Path(d) / "secret.txt").exists()
    print("approval gate ok OK")


def test_shell_tools() -> None:
    with tempfile.TemporaryDirectory() as d:
        tools = make_shell_tools(d)
        run = _find(tools, "run_command")
        r = run.func(command="echo hello")
        assert r.success and "hello" in r.content
    print("shell tools ok OK")


if __name__ == "__main__":
    test_file_tools()
    test_approval_gate()
    test_shell_tools()
