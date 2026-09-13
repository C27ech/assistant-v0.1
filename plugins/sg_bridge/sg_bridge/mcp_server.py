"""sg_bridge.mcp_server — MCP 服务（stdio，JSON-RPC 2.0，无第三方依赖）

给 AI 客户端（Claude Desktop / Cline / 其他 MCP 宿主）用。启动::

    python -m sg_bridge.mcp_server

MCP 配置示例（claude_desktop_config.json）::

    {
      "mcpServers": {
        "sg-bridge": { "command": "python", "args": ["-m", "sg_bridge.mcp_server"] }
      }
    }

对外暴露的工具名带 ``sg_`` 前缀（也接受不带前缀的写法）。
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import __version__, api

PROTOCOL_VERSION = "2024-11-05"
PREFIX = "sg_"

INSTRUCTIONS = (
    "通过本服务可以用接口操作 STEINS;GATE 系列游戏（等价于用户自己按键/点击）。"
    "推荐流程：list_games → attach(target) → game_action(action='advance')，"
    "需要精确操作时用 key_press / click_norm / scroll；"
    "不确定按键绑定就先调用 game_bindings 查看（可按 bindings.json 覆盖）。"
)


def _write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(msg_id: Any, result: dict) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> None:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _write({"jsonrpc": "2.0", "id": msg_id, "error": err})


def _strip_prefix(name: str) -> str:
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def _tools_payload() -> list[dict]:
    out = []
    for spec in api.tool_specs():
        out.append({
            "name": PREFIX + spec["name"],
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
        })
    return out


def handle(message: dict) -> "dict | None":
    """处理一条 JSON-RPC 消息；返回 None 表示无需应答（通知）。"""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "sg-bridge", "version": __version__},
            "instructions": INSTRUCTIONS,
        }}
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _tools_payload()}}
    if method == "tools/call":
        name = _strip_prefix(str(params.get("name", "")))
        args = params.get("arguments") or {}
        res = api.call(name, args)
        text = json.dumps(res, ensure_ascii=False, indent=2)
        payload = {"content": [{"type": "text", "text": text}],
                   "isError": not res.get("ok", False)}
        return {"jsonrpc": "2.0", "id": msg_id, "result": payload}
    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return {"jsonrpc": "2.0", "id": msg_id, "result": {key: []}}
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"未知方法: {method}"}}


def main() -> None:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    print(f"[sg-bridge] MCP stdio 已就绪，工具 {len(api.TOOLS)} 个", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _error(None, -32700, f"JSON 解析失败: {exc}")
            continue
        try:
            reply = handle(message)
        except Exception as exc:  # noqa: BLE001
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            _error(message.get("id"), -32603, f"内部错误: {exc}")
            continue
        if reply is not None:
            _write(reply)


if __name__ == "__main__":
    main()
