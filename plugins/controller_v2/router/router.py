"""C 层路由：AI 语义路由 + 危险操作硬护栏。

策略（方案乙）：
1. 危险操作硬护栏（不交给 AI）：删除文件、结束进程、创建目录、关闭窗口等
   破坏性动作，先按硬关键词 / 正则拦截，命中即返回 ``channel="blocked"``；
2. AI 语义路由：用 deepseek-v4.1-flash-expires-on-0910 对任务文本做语义分类，
   一步输出结构化 JSON ``{"channel": "<接口通道名>", "params": {...}}``；
   接口无法单独完成时输出 ``{"channel": "vision"}``；
3. AI 输出会再经过可用性校验：通道存在、非 destructive、handler 已注册且探测
   通过，才允许走 ``channel="interface"``；否则降级 vision，绝不硬走接口。

本模块只做判定，不发起任何键鼠动作，也不执行接口 handler（避免副作用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.config import load_config

from . import ai_router, channels


@dataclass
class RouteDecision:
    """路由判定结果。

    - channel == "interface"：interface_name / params 必有值；
    - channel == "vision"：prompt 必有值（给视觉执行器的指令）；
    - channel == "blocked"：危险操作被硬护栏拦截，interface_name 为命中的破坏通道名。
    """

    channel: str
    reason: str
    interface_name: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    prompt: Optional[str] = None


def _build_vision_prompt(task: str) -> str:
    """生成交给视觉执行器的自然语言指令。"""
    return (
        f"请通过键鼠视觉闭环完成以下桌面任务：{task}。"
        "先截图观察当前界面，再逐步执行鼠标/键盘操作，并在完成后验证结果。"
    )


def _vision_decision(reason: str, task: str) -> RouteDecision:
    return RouteDecision(
        channel="vision",
        reason=reason,
        prompt=_build_vision_prompt(task),
    )


def _guard_dangerous(task_text: str) -> Optional[RouteDecision]:
    """危险操作硬护栏：AI 判断前先过这道闸。

    只匹配 destructive 通道的硬关键词 / 正则，不做可用性判断；命中即拦截。
    """
    for spec in channels.CHANNELS:
        if not getattr(spec, "destructive", False):
            continue
        if spec.matches(task_text):
            return RouteDecision(
                channel="blocked",
                reason=(
                    f"危险操作被硬护栏拦截：命中 {spec.name}（{spec.description}），"
                    "不交给 AI 判断，也不执行接口。"
                ),
                interface_name=spec.name,
            )
    return None


def _route_ai(
    task_text: str,
    spec_list: List[channels.ChannelSpec],
    cfg: Dict[str, Any],
) -> Optional[RouteDecision]:
    """AI 语义路由；返回 None 表示 AI 未给出可执行的接口决策。"""
    # 只给 AI 看「当前可用且非破坏性」的通道，危险通道由硬护栏先行拦截。
    ai_specs = [
        spec
        for spec in spec_list
        if not getattr(spec, "destructive", False) and spec.available()
    ]
    if not ai_specs:
        return _vision_decision("当前没有可用接口通道，降级视觉执行", task_text)

    raw = ai_router.route_to_channel(task_text, ai_specs, cfg=cfg)
    if not isinstance(raw, dict):
        return _vision_decision("AI 路由未返回可解析结果，降级视觉执行", task_text)

    channel_name = raw.get("channel")
    if not isinstance(channel_name, str) or not channel_name.strip():
        return _vision_decision("AI 路由结果缺少 channel，降级视觉执行", task_text)

    channel_name = channel_name.strip()
    if channel_name == "vision":
        return _vision_decision("AI 判断接口无法独立完成，降级视觉执行", task_text)

    spec = channels.get_channel(channel_name)
    if spec is None:
        return _vision_decision(
            f"AI 返回未知通道 {channel_name}，降级视觉执行", task_text
        )

    # 防御性兜底：即使硬护栏漏过，也不放行 destructive 通道。
    if getattr(spec, "destructive", False):
        return RouteDecision(
            channel="blocked",
            reason=(
                f"AI 试图路由到危险通道 {spec.name}（{spec.description}），"
                "已被硬护栏兜底拦截。"
            ),
            interface_name=spec.name,
        )

    if not spec.available():
        return _vision_decision(
            f"AI 选择接口通道 {spec.name} 但 handler 不可用（not_implemented / "
            "探测失败），降级视觉执行",
            task_text,
        )

    params = raw.get("params")
    if not isinstance(params, dict):
        params = {}

    # AI 漏参时用通道的确定性参数提取器补齐（仅补缺失 / 空值，不覆盖 AI 输出）。
    if spec.build_params:
        try:
            built = spec.build_params(task_text)
        except Exception:  # noqa: BLE001 - 参数提取失败不影响路由。
            built = {}
        if isinstance(built, dict):
            for key, value in built.items():
                if key not in params or params[key] in (None, ""):
                    params[key] = value

    return RouteDecision(
        channel="interface",
        reason=f"AI 语义路由到接口通道 {spec.name}（{spec.description}），且可用",
        interface_name=spec.name,
        params=params,
    )


def route(task: str) -> RouteDecision:
    """对一条自然语言桌面任务做路由判定。

    只依赖 ``task`` 字符串、config.json（可选 ``router`` 配置节，例如
    ``router.enabled_channels`` 白名单）与 AI 语义判断；不发起键鼠动作。
    """
    if not isinstance(task, str) or not task.strip():
        return _vision_decision("任务为空或不是字符串，降级视觉执行", str(task or ""))

    task_text = task.strip()

    # 读取 config；config.json 当前没有 router 节，因此默认使用全部通道。
    # 保留这个读取点，便于后续在 config.json 里做通道白名单等扩展。
    cfg = load_config()
    router_cfg = cfg.get("router") if isinstance(cfg.get("router"), dict) else {}
    enabled = router_cfg.get("enabled_channels")

    # 1) 危险操作硬护栏：先于一切 AI 判断。
    blocked = _guard_dangerous(task_text)
    if blocked is not None:
        return blocked

    # 2) AI 语义路由：一步给出通道 + 参数。
    spec_list: List[channels.ChannelSpec] = channels.get_channels(enabled)
    decision = _route_ai(task_text, spec_list, cfg)
    if decision is not None:
        return decision

    # 3) 兜底：AI 未给出可执行接口决策时降级视觉。
    return _vision_decision("AI 未给出可执行接口决策，降级视觉执行", task_text)


__all__ = ["RouteDecision", "route"]
