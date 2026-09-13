"""sg_bridge.http_server — HTTP 接口（FastAPI，自带 Swagger UI）

启动::

    python -m sg_bridge.http_server            # 默认 127.0.0.1:8765
    python -m sg_bridge.http_server --port 9000 --host 0.0.0.0 --token secret

接口
----
* ``GET  /``                  → 简要说明与工具数
* ``GET  /tools``             → 全部工具（含 JSON Schema）
* ``POST /call/{tool}``       → 调用工具，body 即参数对象
* ``POST /call``              → body 为 ``{"tool": "...", "args": {...}}``
* ``GET  /health``            → 健康检查
* ``GET  /docs``              → Swagger UI（浏览器里直接点着测）
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__, api

app = FastAPI(
    title="SG Bridge",
    version=__version__,
    description="把 STEINS;GATE 系列四部作品的**操作**暴露成外部接口（按键/鼠标/语义动作）。"
                "只做操作，不做画面识别。",
)

TOKEN: "str | None" = None


def _check_token(token: "str | None") -> None:
    if TOKEN and token != TOKEN:
        raise HTTPException(status_code=401, detail="token 不正确")


@app.get("/")
def root() -> dict:
    return {"name": "SG Bridge", "version": __version__, "tools": len(api.TOOLS),
            "docs": "/docs", "usage": "POST /call/{tool}  body=参数对象"}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "tools": len(api.TOOLS)}


@app.get("/tools")
def tools(x_token: "str | None" = Header(default=None)) -> dict:
    _check_token(x_token)
    return {"count": len(api.TOOLS), "tools": api.tool_specs()}


@app.post("/call/{tool_name}")
def call_tool(tool_name: str, payload: dict = Body(default={}),
              x_token: "str | None" = Header(default=None)) -> JSONResponse:
    _check_token(x_token)
    return JSONResponse(api.call(tool_name, payload))


@app.post("/call")
def call_generic(payload: dict = Body(...),
                 x_token: "str | None" = Header(default=None)) -> JSONResponse:
    _check_token(x_token)
    name = payload.get("tool")
    if not name:
        raise HTTPException(status_code=400, detail="缺少 tool 字段")
    return JSONResponse(api.call(name, payload.get("args") or {}))


@app.exception_handler(Exception)
async def on_error(_request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    return JSONResponse({"ok": False, "error": {"code": "EINTERNAL", "message": str(exc)}},
                        status_code=500)


def main() -> None:
    global TOKEN
    parser = argparse.ArgumentParser(description="SG Bridge HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=None, help="可选：要求 X-Token 头")
    parser.add_argument("--dump-tools", action="store_true", help="只打印工具清单后退出")
    args = parser.parse_args()

    if args.dump_tools:
        print(json.dumps(api.tool_specs(), ensure_ascii=False, indent=2))
        return

    TOKEN = args.token
    import uvicorn

    print(f"SG Bridge HTTP: http://{args.host}:{args.port}  (docs: /docs, 工具数 {len(api.TOOLS)})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
