"""监控AI：消费代码AI 的事件流，判断进度/卡住/完成/需用户同意。

规则优先（超时、错误、事件类型）；超时判卡时用 LLM 二次确认（模型走 .env，可通配），避免误判慢任务。
监控AI 还会按任务复杂度自行决定「多久无进展判卡住（stuck_timeout）」与
「代码AI 每隔多久输出一次进展（heartbeat_interval）」，两者都由监控AI 控制。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from storage.models import (
    AGENT_DONE,
    AGENT_RUNNING,
    AGENT_STOPPED,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_NEED_APPROVAL,
)


def _parse_ts(ts) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(ts, fmt)
            except ValueError:
                continue
    return None


class Monitor:
    def __init__(self, repo, stuck_timeout: int = 60, llm_client=None, startup_grace: int = 30):
        self.repo = repo
        self.stuck_timeout = stuck_timeout  # 兜底默认超时（秒），实际以监控AI 定的为准
        self.llm_client = llm_client  # 可选：LLM 客户端（模型由 .env 配，可通配），用于判断卡住与规划监控参数
        self.startup_grace = startup_grace  # 新 agent 启动宽限期（秒），期间无事件不判"无事件"

    def plan_monitoring(self, task: str, model: str = "") -> dict:
        """监控AI 收到助手AI 发布的任务后，自行决定如何监控该代码AI：
        - stuck_timeout：多久无进展就判定卡住；
        - heartbeat_interval：要求代码AI 每隔多久输出一次进展来证明没卡死。
        无 flash 模型或调用失败时返回默认值。"""
        default = {"stuck_timeout": self.stuck_timeout, "heartbeat_interval": 30}
        if self.llm_client is None:
            return default
        try:
            hint = f"，使用模型 {model}" if model else ""
            prompt = (
                "你是一个监控 AI，负责监控代码 AI 执行任务。\n"
                f"任务描述：{task[:500]}{hint}\n\n"
                "请根据任务复杂度，决定两个监控参数，只输出一个 JSON 对象：\n"
                '{"stuck_timeout": <秒>, "heartbeat_interval": <秒>}\n\n'
                "规则：\n"
                "1. stuck_timeout：代码 AI 多久没有进展就判定它卡住。"
                "简单任务可短（60~120 秒），复杂/大任务要长（300 秒以上，最多 900 秒）。\n"
                "2. heartbeat_interval：要求代码 AI 每隔多久输出一次进展，"
                "建议 15~120 秒，任务越慢间隔越长，但要明显小于 stuck_timeout。\n"
                "只输出 JSON，不要输出任何解释或多余文字。"
            )
            result = self.llm_client.chat(
                [{"role": "user", "content": prompt}], max_tokens=512
            )
            raw = (result.content or "").strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return default
            data = json.loads(match.group(0))
            return {
                "stuck_timeout": self._to_positive_int(data.get("stuck_timeout"), self.stuck_timeout),
                "heartbeat_interval": self._to_positive_int(data.get("heartbeat_interval"), 30),
            }
        except Exception as e:
            print(f"[monitor] plan_monitoring flash 调用失败（{type(e).__name__}: {e}），使用默认监控参数")
            return default

    @staticmethod
    def _to_positive_int(value, fallback: int) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return fallback
        return n if n > 0 else fallback

    @staticmethod
    def _agent_timeout(agent: Optional[dict], fallback: int) -> int:
        """从 agent 的 state 里读监控AI 定下的 stuck_timeout；没有则用默认。"""
        if not agent:
            return fallback
        try:
            state = json.loads(agent.get("state") or "{}")
            t = int(state.get("stuck_timeout") or 0)
            return t if t > 0 else fallback
        except Exception:
            return fallback

    @staticmethod
    def _error_detail(events: list) -> str:
        """从事件流提取最近一次 error 事件的具体原因。"""
        for e in events:
            if e.get("type") != EVENT_ERROR:
                continue
            raw = e.get("payload")
            data = raw
            if isinstance(raw, str):
                try:
                    data = json.loads(raw)
                except Exception:
                    data = raw
            if isinstance(data, dict):
                msg = data.get("error") or data.get("reason") or data.get("message")
                if msg:
                    return str(msg)
            elif data:
                return str(data)
        return "未知原因"

    def check_agent(self, agent_id: str, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now()
        agent = self.repo.get_agent(agent_id)
        if not agent:
            return {"agent_id": agent_id, "status": "unknown", "reason": "agent 不存在"}
        events = self.repo.list_events(agent_id, limit=50)
        timeout = self._agent_timeout(agent, self.stuck_timeout)

        # 新派发的 agent 有启动空窗期：worker 子进程要几秒才发出首个事件，
        # 这期间先判「刚启动」，不急着判「无事件」，避免误杀刚起步的 agent。
        if not events and agent.get("status") == AGENT_RUNNING:
            created = _parse_ts(agent.get("created_at"))
            if created is not None and (now - created).total_seconds() < self.startup_grace:
                result = {"status": "running", "reason": "刚启动，等待首个事件"}
            else:
                result = self.infer(agent.get("status"), events, now=now, stuck_timeout=timeout)
        else:
            result = self.infer(agent.get("status"), events, now=now, stuck_timeout=timeout)

        result["agent_id"] = agent_id
        return result

    def infer(
        self,
        status: str,
        events: list,
        now: Optional[datetime] = None,
        stuck_timeout: Optional[int] = None,
    ) -> dict:
        """根据 agent 状态 + 事件列表（按 id 倒序，events[0] 最新）推断状态。"""
        now = now or datetime.now()
        timeout = stuck_timeout if stuck_timeout is not None else self.stuck_timeout
        types = [e.get("type") for e in events]

        if status in (AGENT_DONE, AGENT_STOPPED):
            return {"status": status, "reason": status}
        if EVENT_DONE in types:
            return {"status": "done", "reason": "已发出完成事件"}
        if EVENT_NEED_APPROVAL in types:
            return {"status": "needs_user", "reason": "需要用户同意"}
        if EVENT_ERROR in types:
            return {"status": "stuck", "reason": f"出现错误事件：{self._error_detail(events)}"}

        if events:
            ts = _parse_ts(events[0].get("created_at"))
            if ts is not None:
                idle = (now - ts).total_seconds()
                if idle <= timeout:
                    # 最近有事件（有进展）：无论之前是 running 还是 stuck，都恢复为 running
                    return {"status": "running", "reason": "进行中"}
                # 超时无进展：running 用 flash 二次确认，避免误判慢任务
                if status == AGENT_RUNNING:
                    judged = self._judge_stuck(events)
                    if judged == "running":
                        return {"status": "running", "reason": "进行中（LLM 判断未卡住）"}
                return {"status": "stuck", "reason": f"无进展超过 {int(idle)} 秒"}
            return {"status": status, "reason": "进行中"}

        if status == AGENT_RUNNING:
            return {"status": "stuck", "reason": "无任何事件"}
        return {"status": status, "reason": status}

    def _judge_stuck(self, events: list) -> str:
        """用 flash 模型判断最近事件是否真的卡住；无 LLM 或失败时保守返回 stuck。"""
        if self.llm_client is None:
            return "stuck"
        try:
            recent = events[:10]
            text = "\n".join(f"{e.get('type')}: {e.get('payload')}" for e in recent)
            prompt = (
                "你是一个监控 AI，下面是某个代码 AI 最近的事件列表：\n"
                f"{text}\n\n"
                "请判断这个代码 AI 当前状态，只回复一个英文单词："
                "stuck（卡住/无进展）、running（正常进行中）或 done（已完成）。"
            )
            result = self.llm_client.chat([{"role": "user", "content": prompt}], max_tokens=512)
            answer = result.content.strip().lower()
            if "running" in answer:
                return "running"
            if "done" in answer:
                return "done"
            return "stuck"
        except Exception as e:
            print(f"[monitor] _judge_stuck flash 调用失败（{type(e).__name__}: {e}），回退判为 stuck")
            return "stuck"
