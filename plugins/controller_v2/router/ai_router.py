"""AI 语义路由层（方案乙：一步输出通道 + 参数）。

职责：
1. 把「当前可用接口通道清单 + 每个通道参数要求」组装成软限定 prompt；
2. 调用 deepseek-v4.1-flash-expires-on-0910 做纯文本语义分类；
3. 要求模型只输出一个结构化 JSON：
   - {"channel": "<接口通道名>", "params": {...}}
   - {"channel": "vision"}（接口无法单独完成时降级视觉）

本模块只做「选哪条」，不执行任何接口 / 键鼠动作。危险操作硬护栏由 router.py
在进入本层之前拦截；本层对模型返回的 destructive 通道不做放行。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from core.vision_client import chat_text, extract_json_object

# 与视觉同源的 deepseek 路由判断模型（要求：AI 路由必须使用该模型）。
ROUTER_MODEL = "deepseek-v4.1-flash-expires-on-0910"

# 危险操作硬护栏：AI 路由层也再次兜底拦截，不允许这些通道从模型输出中放行。
# 字段与 router.channels.ChannelSpec 的 destructive 标记保持一致。
_DESTRUCTIVE_CHANNELS = {
    "file_remove",
    "process_kill",
    "dir_create",
    "window_close",
}

_ROUTE_PROMPT = """你是 Windows 桌面任务路由器。请根据用户任务，在「接口通道」和「视觉执行」之间做选择。

当前可用的接口通道清单（只能从这些通道名里选，参数要求见每个通道）：
{manifest}

规则：
1. 若某个接口通道能**独立完整地**完成该任务，输出：
   {{"channel":"<接口通道名>","params":{{...}}}}
   params 必须严格按该通道的参数要求从任务文本中提取；若通道无需参数则填 {{}}。
2. 若没有任何接口通道能独立完成该任务（例如需要在应用界面里点击、输入、搜索、滚动，或必须观察屏幕才能完成），输出：
   {{"channel":"vision"}}
3. 删除文件、结束进程、创建目录、关闭窗口等危险操作已被系统硬护栏拦截，绝不允许输出这些通道。
4. 只输出一个 JSON 对象，不要 Markdown 代码块，不要任何额外文字。

任务：{task}"""


def build_manifest(specs: Iterable[Any]) -> str:
    """把可用接口通道列表组装成给 AI 的软限定清单。"""
    lines: List[str] = []
    for spec in specs:
        params_text = str(getattr(spec, "param_requirements", "") or "{}").strip()
        if not params_text:
            params_text = "{}"
        lines.append(
            f"- 通道名: {spec.name}\n"
            f"  说明: {spec.description}\n"
            f"  底层: {spec.channel}\n"
            f"  参数要求: {params_text}"
        )
    if not lines:
        return "（当前没有可用接口通道）"
    return "\n".join(lines)


def route_to_channel(
    task: str,
    specs: Iterable[Any],
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """调用 AI 路由模型，返回解析后的 JSON dict；失败返回 None。

    :param task: 自然语言任务文本（已通过危险操作硬护栏）。
    :param specs: 当前可用的非 destructive 接口通道列表。
    :param cfg: 已加载配置；None 时自动加载。
    :returns: {"channel": str, "params": dict}；任何失败均返回 None，由调用方降级视觉。
    """
    try:
        manifest = build_manifest(specs)
        prompt = _ROUTE_PROMPT.format(manifest=manifest, task=task)
        text = chat_text(prompt, model=ROUTER_MODEL, cfg=cfg)
        return extract_json_object(text)
    except Exception:  # noqa: BLE001 - AI 路由失败必须稳定降级 vision。
        return None


__all__ = ["ROUTER_MODEL", "build_manifest", "route_to_channel"]
