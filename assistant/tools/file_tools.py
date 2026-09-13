"""文件工具：读写文件、列目录，限制在工作目录内，并接入审批闸门。"""
from __future__ import annotations

from pathlib import Path

from .approval import ApprovalGate
from .base import Tool, ToolResult

NEED_APPROVAL_PREFIX = "NEED_APPROVAL"


def _safe(workdir: Path, rel: str) -> Path:
    """把相对路径解析到工作目录内，越界则抛错。"""
    base = Path(workdir).resolve()
    path = (base / rel).resolve()
    if base != path and base not in path.parents:
        raise ValueError(f"路径越出工作目录: {rel}")
    return path


def make_file_tools(workdir: str | Path, gate: ApprovalGate | None = None) -> list[Tool]:
    workdir = Path(workdir)

    def read_file(path: str) -> ToolResult:
        try:
            p = _safe(workdir, path)
            if not p.exists():
                return ToolResult(False, f"文件不存在: {path}")
            content = p.read_text(encoding="utf-8", errors="replace")
            return ToolResult(True, content)
        except Exception as e:
            return ToolResult(False, f"读取失败: {e}")

    def write_file(path: str, content: str) -> ToolResult:
        try:
            if gate is not None and gate.requires_approval(path, "write"):
                return ToolResult(False, f"{NEED_APPROVAL_PREFIX}:{path}")
            p = _safe(workdir, path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return ToolResult(True, f"已写入 {p}（{len(content)} 字符）")
        except Exception as e:
            return ToolResult(False, f"写入失败: {e}")

    def append_file(path: str, content: str) -> ToolResult:
        try:
            if gate is not None and gate.requires_approval(path, "write"):
                return ToolResult(False, f"{NEED_APPROVAL_PREFIX}:{path}")
            p = _safe(workdir, path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(True, f"已追加 {len(content)} 字符到 {p}")
        except Exception as e:
            return ToolResult(False, f"追加失败: {e}")

    def list_dir(path: str = ".") -> ToolResult:
        try:
            p = _safe(workdir, path)
            if not p.exists():
                return ToolResult(False, f"目录不存在: {path}")
            if p.is_file():
                return ToolResult(True, f"{p.name}（文件）")
            entries = []
            for child in sorted(p.iterdir()):
                entries.append(child.name + "/" if child.is_dir() else child.name)
            return ToolResult(True, "\n".join(entries) if entries else "（空目录）")
        except Exception as e:
            return ToolResult(False, f"列目录失败: {e}")

    return [
        Tool(
            "read_file",
            "读取工作目录内指定文件的内容",
            {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对工作目录的文件路径"}},
                "required": ["path"],
            },
            read_file,
        ),
        Tool(
            "write_file",
            "在工作目录内写入/创建文件（会覆盖已有内容）",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作目录的文件路径"},
                    "content": {"type": "string", "description": "文件完整内容"},
                },
                "required": ["path", "content"],
            },
            write_file,
        ),
        Tool(
            "append_file",
            "向工作目录内的文件追加内容（用于分块写大文件，需先用 write_file 创建第一段）",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作目录的文件路径"},
                    "content": {"type": "string", "description": "要追加的内容"},
                },
                "required": ["path", "content"],
            },
            append_file,
        ),
        Tool(
            "list_dir",
            "列出目录内容",
            {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对路径，默认 .", "default": "."}},
            },
            list_dir,
        ),
    ]
