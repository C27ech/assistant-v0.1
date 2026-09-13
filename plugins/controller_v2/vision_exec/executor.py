"""有状态 agent loop 执行器（A 层核心，重构版）。

与旧版「无状态 for 循环」不同，本版维护一份跨轮消息历史，并把每轮
截图缩略图、决策、动作、验证结果持久化到 SQLite（vision_exec/agent_history.db）。
每轮用 BM25（中文 2-gram）从历史中检索 top50 相似条目，连同最近关键帧一起
注入 prompt，让长任务不再失忆。

链路：
    截图 -> 缩略图落盘 frames/ -> 追加历史 -> BM25 检索 -> 多帧注入 prompt
    -> 视觉决策（done/action/unsure/stuck） -> core.guard 护栏 -> action 执行
    -> 再次截图 verify -> 结果追加历史 -> 下一轮。

终止条件：
    1. 模型自判「任务完成」；
    2. 模型自判「卡死无法完成」；
    3. 连续不确定 / 连续决策失败 / 连续画面无变化触发兜底卡死；
    4. 80 轮硬上限兜底，强制收尾并明确标注。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from action.action import execute as execute_action
from core import vision_client as vc
from core.config import load_config, resolve_vision_api_key
from core.guard import check_action
from core.screen import capture_screenshot

from . import history as history_store
from .prompt import (
    build_agent_decision_prompt,
    build_verify_prompt,
    parse_decision_response,
    parse_verify_response,
)

DEFAULT_MAX_ROUNDS = 80
MAX_ROUNDS_HARD_LIMIT = 80
DEFAULT_SETTLE_DELAY = 0.8
DEFAULT_DIFF_THRESHOLD = 0.01

# 有状态 loop 相关常量。
MAX_HISTORY_ITEMS = 50          # BM25 检索 top50。
MAX_RECENT_FRAMES = 3           # 注入 prompt 的最近关键帧数量（不含当前帧）。
MAX_FRAME_SIDE = 1024           # 缩略图最长边。
MAX_RECENT_CONTEXT_MESSAGES = 8 # 从 messages 历史里取最近多少条组成 query。
MAX_CONSECUTIVE_UNSURE = 3      # 连续不确定 -> 卡死。
MAX_CONSECUTIVE_ERRORS = 3      # 连续决策失败 -> 卡死。
MAX_NO_CHANGE_STREAK = 5        # 连续动作后画面无变化 -> 卡死。

_SYSTEM_PROMPT = (
    "你是 Windows 桌面自动化控制器。你通过多轮截图、执行键鼠动作、验证结果来完成"
    "任务。你会收到历史记忆与最近关键帧（最后一张为当前截图）。你必须只输出结构化 "
    "JSON 决策，不要输出 Markdown 代码块或额外文字。"
)

# 档位别名：与 core.vision_client 保持一致；本执行器只使用 pro / turbo。
_TIER_ALIASES = {
    "min": "mini",
    "mini": "mini",
    "t": "turbo",
    "fast": "turbo",
    "turbo": "turbo",
    "p": "pro",
    "precise": "pro",
    "pro": "pro",
}


def _resolve_model_tier(cfg: Optional[Dict[str, Any]] = None) -> str:
    """从 config 解析本执行器使用的模型档位（pro 或 turbo）。"""
    loaded = cfg if cfg is not None else load_config()
    tier = loaded.get("multimodal_model_tier")
    if not tier:
        vision = loaded.get("vision")
        if isinstance(vision, dict):
            tier = vision.get("default_tier")

    tier = _TIER_ALIASES.get(str(tier or "pro").strip().lower(), "pro")
    if tier == "mini":
        tier = "turbo"
    return tier


def _chat_single(
    screenshot_path: str,
    prompt: str,
    cfg: Optional[Dict[str, Any]] = None,
    tier: str = "pro",
) -> str:
    """单图调用：复用 core.vision_client._post_chat。"""
    loaded = cfg if cfg is not None else load_config()
    _, vision = vc._read_vision_config(loaded)

    api_key = resolve_vision_api_key(loaded)
    if not api_key:
        raise RuntimeError("缺少视觉 API Key（env -> secrets.json -> config.json 三级解析均失败）")

    model = vc._resolve_model_name(tier, loaded, vision)
    return vc._post_chat(screenshot_path, prompt, model, api_key, loaded, vision)


def _chat_messages(
    messages: List[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
    tier: str = "pro",
) -> str:
    """多消息（可多图）调用：复用 core.vision_client.post_chat_messages。"""
    loaded = cfg if cfg is not None else load_config()
    _, vision = vc._read_vision_config(loaded)

    api_key = resolve_vision_api_key(loaded)
    if not api_key:
        raise RuntimeError("缺少视觉 API Key（env -> secrets.json -> config.json 三级解析均失败）")

    model = vc._resolve_model_name(tier, loaded, vision)
    return vc.post_chat_messages(messages, model, api_key, loaded, vision)


def _capture_thumbnail(
    session_id: str,
    round_no: int,
    kind: str = "screenshot",
) -> str:
    """截图并保存为 JPEG 缩略图，返回相对项目根目录的路径字符串。

    原图只作为临时 PNG，压缩成缩略图后立即删除；历史只存缩略图路径。
    """
    frames = history_store.frames_dir()
    raw = frames / f"raw_{uuid.uuid4().hex}.png"
    capture_screenshot(str(raw))

    thumb = frames / f"{session_id}_{round_no}_{kind}_{uuid.uuid4().hex[:8]}.jpg"
    try:
        from PIL import Image

        with Image.open(raw) as im:
            image = im.convert("RGB")
            width, height = image.size
            max_side = max(width, height)
            if max_side > MAX_FRAME_SIDE:
                scale = MAX_FRAME_SIDE / float(max_side)
                new_size = (
                    max(1, int(round(width * scale))),
                    max(1, int(round(height * scale))),
                )
                image = image.resize(new_size, Image.LANCZOS)
            image.save(thumb, format="JPEG", quality=80)
    finally:
        try:
            raw.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    try:
        project_root = Path(__file__).resolve().parent.parent
        return str(thumb.resolve().relative_to(project_root))
    except ValueError:
        return str(thumb.resolve())


def _image_diff_score(before: str, after: str) -> float:
    """返回前后两张截图的归一化平均像素差（0.0~1.0）。异常时返回 0.0。"""
    try:
        from PIL import Image, ImageChops, ImageStat

        with Image.open(before) as im1, Image.open(after) as im2:
            a = im1.convert("RGB").resize((160, 100), Image.LANCZOS)
            b = im2.convert("RGB").resize((160, 100), Image.LANCZOS)
            diff = ImageChops.difference(a, b)
            stat = ImageStat.Stat(diff)
            mean = sum(stat.mean) / max(1, len(stat.mean))
            return float(mean) / 255.0
    except Exception:
        return 0.0


def _history_text(entry: Dict[str, Any]) -> str:
    """把历史条目格式化为注入 prompt 的一行文本。"""
    round_no = entry.get("round_no")
    round_label = f"第{round_no}轮" if round_no is not None else "历史"
    frame_name = ""
    if entry.get("frame_path"):
        frame_name = f" 帧:{Path(str(entry['frame_path'])).name}"
    return f"{round_label} [{entry.get('kind', '?')}] {entry.get('text', '')}{frame_name}"


def _recent_context(messages: List[Dict[str, str]], limit: int = MAX_RECENT_CONTEXT_MESSAGES) -> str:
    """取最近若干条 messages 组成 BM25 query / prompt 上下文。"""
    recent = messages[-limit:] if limit > 0 else messages
    lines = [f"{m.get('role', '?')}: {m.get('content', '')}" for m in recent]
    return "\n".join(lines)


def _build_decision_messages(
    prompt: str,
    recent_frames: List[str],
    current_frame: str,
) -> List[Dict[str, Any]]:
    """构造多帧决策请求：system + user（文本 + 最近关键帧 + 当前截图）。"""
    parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for idx, frame_path in enumerate(recent_frames, start=1):
        parts.append({"type": "text", "text": f"[历史关键帧 {idx}]"})
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": frame_path},
            }
        )
    parts.append({"type": "text", "text": "[当前截图]"})
    parts.append(
        {
            "type": "image_url",
            "image_url": {"url": current_frame},
        }
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": parts},
    ]


def _resolve_frame_abs(frame_path: Optional[str]) -> Optional[str]:
    """把历史里的相对/绝对 frame_path 解析为绝对路径字符串。"""
    p = history_store.resolve_frame_path(frame_path)
    if p is None or not p.exists():
        return None
    return str(p)


class AgentLoop:
    """有状态视觉 agent loop。

    :ivar messages: 内存中的消息历史（system + task + 每轮决策/动作/验证/截图描述）。
    """

    def __init__(
        self,
        task: str,
        max_rounds: int,
        cfg: Optional[Dict[str, Any]] = None,
        tier: str = "pro",
        session_id: Optional[str] = None,
    ) -> None:
        self.task = task
        self.max_rounds = max_rounds
        self.cfg = cfg if cfg is not None else load_config()
        self.tier = tier
        self.session_id = history_store.new_session(task, session_id=session_id)
        self.messages: List[Dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"任务：{task}"},
        ]

        self.round_details: List[Dict[str, Any]] = []
        self.actions: List[Dict[str, Any]] = []
        self.last_error: Optional[str] = None
        self.last_image_diff: Optional[Dict[str, Any]] = None
        self.consecutive_unsure = 0
        self.consecutive_errors = 0
        self.no_change_streak = 0

    # ------------------------------------------------------------------
    # 决策
    # ------------------------------------------------------------------
    def _decide(
        self,
        current_frame: str,
        current_frame_rel: str,
        round_no: int,
        recent_frames: List[str],
        retrieved_entries: List[Tuple[Dict[str, Any], float]],
    ) -> Dict[str, Any]:
        """构造 prompt 并调用模型，返回结构化决策。"""
        recent_context = _recent_context(self.messages)
        retrieved_text = "\n".join(
            _history_text(entry) + f"  [score={score:.4f}]"
            for entry, score in retrieved_entries
        ) or "（暂无）"

        prompt = build_agent_decision_prompt(
            task=self.task,
            round_no=round_no,
            max_rounds=self.max_rounds,
            recent_context=recent_context,
            retrieved_history=retrieved_text,
        )

        messages = _build_decision_messages(prompt, recent_frames, current_frame)

        fallback_note = ""
        try:
            text = _chat_messages(messages, cfg=self.cfg, tier=self.tier)
        except Exception as exc:  # noqa: BLE001 - 多图失败降级为单当前帧，保证 loop 可跑。
            fallback_note = f"（多图注入失败已降级单帧：{exc}）"
            try:
                text = _chat_single(current_frame, prompt, cfg=self.cfg, tier=self.tier)
            except Exception as exc2:  # noqa: BLE001
                self.consecutive_errors += 1
                return {
                    "status": "unsure",
                    "action": None,
                    "expectation": None,
                    "reason": f"视觉决策失败: {exc2}",
                    "error": str(exc2),
                }

        decision = parse_decision_response(text, task=self.task)
        if fallback_note:
            decision.setdefault("fallback", fallback_note)
            if decision.get("reason"):
                decision["reason"] = fallback_note + " " + str(decision["reason"])
        return decision

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------
    def _verify(
        self,
        after_frame: str,
        expectation: Optional[str],
    ) -> Dict[str, Any]:
        """单图 verify，返回三态 + 原因。"""
        if not expectation or not str(expectation).strip():
            return {"result": None, "reason": "决策未提供可验证预期"}

        try:
            prompt = build_verify_prompt(str(expectation).strip())
            text = _chat_single(after_frame, prompt, cfg=self.cfg, tier=self.tier)
        except Exception as exc:  # noqa: BLE001
            return {"result": None, "reason": f"视觉验证失败: {exc}"}

        result = parse_verify_response(text)
        if result is None:
            return {"result": None, "reason": "模型未返回可解析的验证结论"}
        return {"result": result, "reason": ""}

    # ------------------------------------------------------------------
    # 结果组装
    # ------------------------------------------------------------------
    def _result(self, status: str, reason: str) -> Dict[str, Any]:
        verify_status = "ok" if status == "ok" else "fail"
        return {
            "status": status,
            "rounds": len(self.round_details),
            "actions": self.actions,
            "verify": {
                "status": verify_status,
                "reason": reason,
                "image_diff": self.last_image_diff,
            },
            "error": None if status == "ok" else reason,
            "round_details": self.round_details,
            "session_id": self.session_id,
            "history_count": history_store.count_history(session_id=self.session_id),
        }

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        for round_no in range(1, self.max_rounds + 1):
            detail: Dict[str, Any] = {
                "round": round_no,
                "screenshot": None,
                "retrieved_count": 0,
                "decision": None,
                "action": None,
                "guard": None,
                "execution": None,
                "verify": None,
                "image_diff": None,
                "reason": None,
            }

            # 1) 截图 -> 缩略图落盘 -> 追加历史（只存路径 + 描述）。
            try:
                current_frame_rel = _capture_thumbnail(self.session_id, round_no, "screen")
                current_frame_abs = _resolve_frame_abs(current_frame_rel)
                if not current_frame_abs:
                    raise RuntimeError("截图缩略图未生成")
                screenshot_entry_id = history_store.add_history(
                    self.session_id,
                    kind="screenshot",
                    text=f"第{round_no}轮当前屏幕截图",
                    round_no=round_no,
                    frame_path=current_frame_rel,
                    meta={"round": round_no, "role": "current"},
                )
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"截图失败: {exc}"
                detail["reason"] = self.last_error
                self.round_details.append(detail)
                break

            detail["screenshot"] = current_frame_rel

            # 2) BM25 检索 top50。
            query = f"{self.task}\n{_recent_context(self.messages)}"
            retrieved = history_store.search_history(
                query,
                top_k=MAX_HISTORY_ITEMS,
                session_id=None,
            )
            detail["retrieved_count"] = len(retrieved)

            # 3) 最近关键帧（排除当前帧）。
            recent_frames = self._recent_keyframes(exclude=current_frame_rel)

            # 4) 模型决策。
            decision = self._decide(
                current_frame_abs,
                current_frame_rel,
                round_no,
                recent_frames,
                retrieved,
            )
            detail["decision"] = {
                "status": decision.get("status"),
                "reason": decision.get("reason"),
                "expectation": decision.get("expectation"),
                "error": decision.get("error"),
                "fallback": decision.get("fallback"),
            }

            # 补上截图描述：模型对当前帧的观察 reason。
            try:
                history_store.update_history_text(
                    screenshot_entry_id,
                    f"第{round_no}轮截图；模型观察：{decision.get('reason') or '无'}",
                )
            except Exception:  # noqa: BLE001
                pass

            # 5) 终止判定：完成 / 卡死。
            if decision.get("status") == "done":
                reason = "模型判定任务已完成"
                detail["reason"] = reason
                self.messages.append(
                    {"role": "assistant", "content": f"第{round_no}轮决策：{reason}；{decision.get('reason') or ''}"}
                )
                self.round_details.append(detail)
                history_store.add_history(
                    self.session_id,
                    kind="decision",
                    text=reason,
                    round_no=round_no,
                )
                history_store.update_session_status(self.session_id, "done")
                return self._result("ok", reason)

            if decision.get("status") == "stuck":
                reason = "模型判定卡死无法完成：" + str(decision.get("reason") or "无进一步理由")
                detail["reason"] = reason
                self.messages.append(
                    {"role": "assistant", "content": f"第{round_no}轮决策：{reason}"}
                )
                self.round_details.append(detail)
                history_store.add_history(
                    self.session_id,
                    kind="decision",
                    text=reason,
                    round_no=round_no,
                )
                history_store.update_session_status(self.session_id, "stuck")
                self.last_error = reason
                return self._result("fail", reason)

            if decision.get("status") != "action":
                self.consecutive_unsure += 1
                if decision.get("error"):
                    self.consecutive_errors += 1
                else:
                    self.consecutive_errors = 0
                self.last_error = decision.get("reason") or "模型无法确定下一步动作"
                detail["reason"] = self.last_error
                self.messages.append(
                    {"role": "assistant", "content": f"第{round_no}轮决策：不确定；{self.last_error}"}
                )
                history_store.add_history(
                    self.session_id,
                    kind="decision",
                    text=self.last_error,
                    round_no=round_no,
                )
                self.round_details.append(detail)

                if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    reason = f"连续 {self.consecutive_errors} 轮视觉决策失败，判定为卡死"
                    self.last_error = reason
                    history_store.add_history(
                        self.session_id,
                        kind="terminate",
                        text=reason,
                        round_no=round_no,
                    )
                    history_store.update_session_status(self.session_id, "stuck")
                    return self._result("fail", reason)

                if self.consecutive_unsure >= MAX_CONSECUTIVE_UNSURE:
                    reason = f"连续 {self.consecutive_unsure} 轮模型无法确定下一步，判定为卡死"
                    self.last_error = reason
                    history_store.add_history(
                        self.session_id,
                        kind="terminate",
                        text=reason,
                        round_no=round_no,
                    )
                    history_store.update_session_status(self.session_id, "stuck")
                    return self._result("fail", reason)
                continue

            # 有动作：重置不确定 / 错误计数。
            self.consecutive_unsure = 0
            self.consecutive_errors = 0
            action = decision.get("action")
            self.actions.append(action)
            detail["action"] = action
            self.messages.append(
                {
                    "role": "assistant",
                    "content": (
                        f"第{round_no}轮决策：动作 {action.get('type')}；"
                        f"期望={decision.get('expectation') or '无'}；"
                        f"理由={decision.get('reason') or '无'}"
                    ),
                }
            )
            history_store.add_history(
                self.session_id,
                kind="decision",
                text=(
                    f"动作 {action.get('type')}；"
                    f"期望={decision.get('expectation') or '无'}；"
                    f"理由={decision.get('reason') or '无'}"
                ),
                round_no=round_no,
                meta={"action": action},
            )

            # 6) 护栏校验。
            guard_ok, guard_reason = check_action(action)
            detail["guard"] = {"ok": guard_ok, "reason": guard_reason}
            if not guard_ok:
                self.last_error = f"护栏拦截: {guard_reason}"
                detail["reason"] = self.last_error
                self.messages.append(
                    {"role": "user", "content": f"第{round_no}轮护栏拦截：{guard_reason}"}
                )
                history_store.add_history(
                    self.session_id,
                    kind="guard_blocked",
                    text=self.last_error,
                    round_no=round_no,
                    meta={"action": action},
                )
                self.round_details.append(detail)
                continue

            # 7) 执行动作。
            exec_result = execute_action(action)
            detail["execution"] = exec_result
            if not exec_result.get("ok"):
                self.last_error = f"动作执行失败: {exec_result.get('reason')}"
                detail["reason"] = self.last_error
                self.messages.append(
                    {"role": "user", "content": f"第{round_no}轮执行失败：{exec_result.get('reason')}"}
                )
                history_store.add_history(
                    self.session_id,
                    kind="action_result",
                    text=self.last_error,
                    round_no=round_no,
                    meta={"action": action, "result": exec_result},
                )
                self.round_details.append(detail)
                continue

            history_store.add_history(
                self.session_id,
                kind="action_result",
                text=f"动作执行成功：{exec_result.get('reason')}",
                round_no=round_no,
                meta={"action": action, "result": exec_result},
            )

            # 8) 等待 UI 稳定后再次截图 verify。
            time.sleep(DEFAULT_SETTLE_DELAY)
            try:
                after_rel = _capture_thumbnail(self.session_id, round_no, "verify")
                after_abs = _resolve_frame_abs(after_rel)
                if not after_abs:
                    raise RuntimeError("验证截图缩略图未生成")
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"验证截图失败: {exc}"
                detail["reason"] = self.last_error
                self.round_details.append(detail)
                break

            history_store.add_history(
                self.session_id,
                kind="screenshot",
                text=f"第{round_no}轮动作后验证截图",
                round_no=round_no,
                frame_path=after_rel,
                meta={"round": round_no, "role": "after_action"},
            )

            diff_score = _image_diff_score(current_frame_abs, after_abs)
            changed = bool(diff_score >= DEFAULT_DIFF_THRESHOLD)
            self.last_image_diff = {"changed": changed, "score": round(diff_score, 6)}
            detail["image_diff"] = self.last_image_diff

            ver = self._verify(after_abs, decision.get("expectation"))
            detail["verify"] = {"result": ver.get("result"), "reason": ver.get("reason")}
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                        f"第{round_no}轮验证：截图描述={ver.get('reason') or '无'}；"
                        f"画面变化={'有' if changed else '无'}；"
                        f"验证结果={ver.get('result')}"
                    ),
                }
            )
            history_store.add_history(
                self.session_id,
                kind="verify",
                text=(
                    f"验证结果={ver.get('result')}；"
                    f"画面变化={'有' if changed else '无'}；"
                    f"理由={ver.get('reason') or '无'}"
                ),
                round_no=round_no,
                meta={"result": ver.get("result"), "image_diff": self.last_image_diff},
            )

            # 9) 依据 verify 三态决定成功 / 重试。
            if ver.get("result") is True:
                reason = f"第 {round_no} 轮动作后，视觉验证确认任务完成"
                detail["reason"] = reason
                self.round_details.append(detail)
                history_store.add_history(
                    self.session_id,
                    kind="terminate",
                    text=reason,
                    round_no=round_no,
                )
                history_store.update_session_status(self.session_id, "done")
                return self._result("ok", reason)

            if ver.get("result") is False:
                self.last_error = "视觉验证明确反证：任务预期尚未实现"
                detail["reason"] = self.last_error
                self.no_change_streak = 0
                self.round_details.append(detail)
                continue

            # verify 证据不足：用画面变化作为辅助信号。
            if changed:
                self.last_error = "画面有变化，但模型无法确认任务预期是否实现（证据不足）"
                self.no_change_streak = 0
            else:
                self.last_error = "画面无变化，动作可能未生效"
                self.no_change_streak += 1
            detail["reason"] = self.last_error
            self.round_details.append(detail)

            if self.no_change_streak >= MAX_NO_CHANGE_STREAK:
                reason = f"连续 {self.no_change_streak} 轮动作后画面无变化，判定为卡死"
                self.last_error = reason
                history_store.add_history(
                    self.session_id,
                    kind="terminate",
                    text=reason,
                    round_no=round_no,
                )
                history_store.update_session_status(self.session_id, "stuck")
                return self._result("fail", reason)

        # 80 轮硬上限兜底。
        reason = f"达到 {self.max_rounds} 轮硬上限，强制收尾（最后错误：{self.last_error or '无'}）"
        self.last_error = reason
        history_store.add_history(
            self.session_id,
            kind="terminate",
            text=reason,
            round_no=self.max_rounds,
        )
        history_store.update_session_status(self.session_id, "hard_limit")
        return self._result("fail", reason)

    # ------------------------------------------------------------------
    # 最近关键帧
    # ------------------------------------------------------------------
    def _recent_keyframes(self, exclude: Optional[str] = None) -> List[str]:
        """返回当前 session 最近若干帧的绝对路径（排除指定相对路径）。"""
        entries = history_store.list_history(session_id=self.session_id, limit=200)
        frames: List[str] = []
        seen: set = set()
        for entry in reversed(entries):
            fp = entry.get("frame_path")
            if not fp or fp == exclude:
                continue
            abs_path = _resolve_frame_abs(fp)
            if not abs_path or abs_path in seen:
                continue
            seen.add(abs_path)
            frames.append(abs_path)
            if len(frames) >= MAX_RECENT_FRAMES:
                break
        # list_history 升序，我们 reversed 后取的是最近优先；保持时间顺序再反转。
        return list(reversed(frames))


def run_vision_task(
    task: str,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行有状态视觉 agent loop。

    :param task: 自然语言任务描述。
    :param max_rounds: 最大尝试轮数，默认 20，硬上限 80。
    :param session_id: 可选；不传则每次新建 session。历史仍会持久化到 SQLite。
    :returns:
        {
          "status": "ok" | "fail",
          "rounds": int,
          "actions": list[dict],
          "verify": dict,
          "error": str | None,
          "round_details": list[dict],
          "session_id": str,
          "history_count": int,
        }
    """
    task = str(task or "").strip()
    if not task:
        return {
            "status": "fail",
            "rounds": 0,
            "actions": [],
            "verify": {"status": "fail", "reason": "任务为空", "image_diff": None},
            "error": "任务为空",
            "round_details": [],
            "session_id": None,
            "history_count": history_store.count_history(),
        }

    try:
        max_rounds = int(max_rounds)
    except (TypeError, ValueError):
        max_rounds = DEFAULT_MAX_ROUNDS
    max_rounds = max(1, min(max_rounds, MAX_ROUNDS_HARD_LIMIT))

    cfg = load_config()
    tier = _resolve_model_tier(cfg)

    if not resolve_vision_api_key(cfg):
        reason = "缺少视觉 API Key（env -> secrets.json -> config.json 三级解析均失败）"
        return {
            "status": "fail",
            "rounds": 0,
            "actions": [],
            "verify": {"status": "fail", "reason": reason, "image_diff": None},
            "error": "missing_vision_api_key",
            "round_details": [],
            "session_id": session_id,
            "history_count": history_store.count_history(),
        }

    loop = AgentLoop(
        task=task,
        max_rounds=max_rounds,
        cfg=cfg,
        tier=tier,
        session_id=session_id,
    )
    return loop.run()


__all__ = ["run_vision_task", "AgentLoop"]
