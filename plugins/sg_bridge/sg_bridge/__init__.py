"""sg_bridge —— 把 4 部 SciADV 作品的**操作**暴露成外部可调用的接口。

范围（按用户要求收敛）
----------------------
* 只做 **操作**：按键 / 鼠标（点击、拖动、滚轮）/ 语义动作（推进、快进、开菜单…）。
* **不做** 文字识别、不做画面理解（用户自己看画面）。
* 通过 HTTP / MCP 从外部调用，效果等同"用户自己在操作电脑"。

入口
----
* ``python -m sg_bridge.http_server``  → HTTP + Swagger UI（默认 8765 端口）
* ``python -m sg_bridge.mcp_server``   → MCP（stdio，给 AI 客户端用）
* ``python -m sg_bridge.cli``          → 命令行快速测试
* ``python -m sg_bridge.selftest``     → 记事本端到端自测（验证注入真的生效）
"""

__version__ = "0.1.0"
