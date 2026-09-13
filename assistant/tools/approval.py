"""审批闸门：判断某文件/操作是否需要用户同意。

默认全自主；仅命中 approval_list 中的路径/操作时需要用户同意。
approval_list 每项支持：精确路径、相对路径、通配符（如 *.env、src/**）。
"""
from __future__ import annotations

from fnmatch import fnmatch
from typing import Iterable


class ApprovalGate:
    def __init__(self, approval_list: Iterable[str] | None = None):
        self._patterns = list(approval_list or [])

    def requires_approval(self, path: str, op: str = "write") -> bool:
        if not self._patterns:
            return False
        norm = path.replace("\\", "/")
        for pat in self._patterns:
            p = pat.replace("\\", "/")
            if norm == p or fnmatch(norm, p) or fnmatch(norm, p.rstrip("/") + "/*"):
                return True
        return False

    @property
    def patterns(self) -> list[str]:
        return list(self._patterns)
