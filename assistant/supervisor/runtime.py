"""运行时：把决策AI、编排器、监控AI、渠道接起来，形成完整闭环。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from config.plugins import build_tools, load_plugins, run_plugin
from config.settings import Settings
from llm.deepseek import DeepSeekClient
from storage.db import Database
from storage.repo import Repo
from supervisor.decision import DecisionAgent
from supervisor.monitor import Monitor
from supervisor.orchestrator import Orchestrator


class Runtime:
    def __init__(self, settings: Settings, channel=None):
        self.settings = settings
        self.db = Database(settings.db_file)
        self.repo = Repo(self.db)
        self.channel = channel
        self.orchestrator = Orchestrator(settings, self.repo)

        # 监控AI：规则 + 可选 flash 模型判断卡住
        flash_client = None
        if settings.api_key_monitor:
            flash_client = DeepSeekClient(
                settings.api_key_monitor,
                settings.monitor_model,
                settings.deepseek_base_url,
            )
        self.monitor = Monitor(self.repo, stuck_timeout=60, llm_client=flash_client)
        self.user_ids: dict[str, str] = {}  # user_id -> 该用户在渠道里的标识（QQ 号）
        self._decision_lock = threading.Lock()  # 串行化「agent 状态变化」触发的决策，避免并发重复派活

        # 外部 CLI 插件（plugins.json）：每个插件自动生成一个可调用工具
        self.plugins = load_plugins()

        # 启动时把白名单状态打进日志，方便排查「为什么她不回我」
        # （写 stderr 并 flush：stdout 被重定向成文件时是块缓冲，日志会迟迟不落盘）
        if settings.allowed_users:
            print(f"[白名单] 已启用使用者白名单：{settings.allowed_users}（其他人私聊会被静默忽略）",
                  file=sys.stderr, flush=True)
        else:
            print("[白名单] 未配置 ALLOWED_USERS：任何人都能私聊指挥助手（建议尽快设置）",
                  file=sys.stderr, flush=True)

        if settings.api_key_decision:
            self.decision = DecisionAgent(
                DeepSeekClient(
                    settings.api_key_decision,
                    settings.decision_model,
                    settings.deepseek_base_url,
                ),
                extra_tools=build_tools(self.plugins),
            )
        else:
            self.decision = None

    # ---------- 消息处理 ----------
    def handle(self, sender_id: str, remark: str, content: str, images: list | None = None) -> str:
        # 使用者白名单：不在名单里的一律静默忽略（不入库、不回复、只记日志，避免被陌生人使唤）
        if not self.settings.is_allowed(sender_id, remark):
            snip = (content or "[图片]").replace("\n", " ")[:40]
            print(f"[白名单] 已忽略未授权消息：sender={sender_id} 内容前40字={snip!r}",
                  file=sys.stderr, flush=True)
            return ""

        user_id = self.settings.resolve_user(remark) or remark
        self.user_ids[user_id] = sender_id
        # users.wxid 列沿用旧列名（兼容已有数据库），存的就是渠道内的用户标识（QQ 号）
        self.repo.upsert_user(user_id, wxid=sender_id, remark=remark)

        # 先取历史（此时本条还没入库，上下文里就不会重复出现本条），再落库
        history = self._history(user_id, content)
        self.repo.add_message(user_id, "in", content or ("[图片]" if images else ""))

        calls: list[str] = []
        if self.decision is None:
            reply = f"[未配置 DeepSeek key] 收到：{content}"
        else:
            try:
                reply = self.decision.run(
                    history,
                    content,
                    self._executor(user_id, calls),
                    max_iter=self.settings.decision_max_iter,
                    images=images,
                )
            except Exception as e:  # noqa: BLE001
                # 模型调用整体失败（余额不足 402 / 限流 / 网络 / 认证失败…）：
                # 以前这里直接把异常抛出去 → 渠道层啥也发不出去，用户只看到「她不回话」。
                # 现在改成：记日志 + 回一条能看懂的原因，并且不入库（免得污染她的记忆）。
                detail = f"{type(e).__name__}: {e}"
                print(f"[错误] 决策AI 调用失败：{detail}", file=sys.stderr, flush=True)

                low = detail.lower()
                if "402" in detail or "insufficient balance" in low or "余额" in detail:
                    why = "DeepSeek 账户余额不足（402）。请去 platform.deepseek.com 充值，或换一把有余额的 key（改 .env 的 DEEPSEEK_API_KEY_DECISION 后重启）。"
                elif "401" in detail or "authentication" in low or "invalid api key" in low:
                    why = "API key 无效或已失效（401）。请检查 .env 里的 DEEPSEEK_API_KEY_DECISION。"
                elif "429" in detail or "rate limit" in low:
                    why = "被限流了（429）。等一会儿再发一次。"
                elif "timeout" in low or "timed out" in low:
                    why = "调用超时（可能是网络或服务端慢）。可以再发一次。"
                else:
                    why = f"调用出错：{detail[:160]}"

                reply = f"⚠️ 我这边 API 出问题了，暂时没法正常回复。\n原因：{why}"
                reply = self._guard_fake_dispatch(reply, calls)
                return reply
        if not reply or not reply.strip():
            reply = "（助手AI 本轮没有返回内容，可能思考超时或出错，请再试一次。）"
        reply = self._guard_fake_dispatch(reply, calls)

        self.repo.add_message(user_id, "out", reply)
        return reply

    _DISPATCH_CLAIM_MARKERS = ("📋 派发", "📋 已派发", "已派发任务给代码AI", "已派出")

    def _guard_fake_dispatch(self, reply: str, calls: list) -> str:
        """防幻觉派发：回复里声称已派发、但本轮根本没调 spawn_agent → 追加纠正提示。"""
        if "spawn_agent" in calls:
            return reply
        if not any(m in reply for m in self._DISPATCH_CLAIM_MARKERS):
            return reply
        print("[guard] 检测到疑似幻觉派发（本轮未调用 spawn_agent），已追加纠正提示")
        return (
            reply
            + "\n\n（⚠️ 系统提示：上面那段「派发」**本轮并未真正执行**，没有创建任何代码AI。"
            "真实派发会有带编号的系统确认行「📋 已派发任务给代码AI（agent-xxxx…）」。）"
        )

    def _history(self, user_id: str, content: str = "", limit: int = 20) -> list[dict]:
        recent = self.repo.get_history(user_id, limit)  # 最近 limit 条（旧→新）
        relevant = self.repo.search_history(user_id, content, k=self.settings.memory_retrieval_k) if content else []
        merged: dict[int, dict] = {}
        for r in list(relevant) + list(recent):
            merged[r["id"]] = r
        ordered = sorted(merged.values(), key=lambda r: r["id"])
        return [
            {
                "role": "user" if r["direction"] == "in" else "assistant",
                "content": r["content"] or "",
            }
            for r in ordered
        ]

    # ---------- 决策AI 的工具执行器 ----------
    def _executor(self, user_id: str, calls: list | None = None):
        def exec_tool(name: str, args: dict) -> str:
            if calls is not None:
                calls.append(name)  # 记录本轮调用过的工具，供「防幻觉派发」校验
            if name == "spawn_agent":
                task_desc = (args.get("task") or "")[:100]
                model = args.get("model") or self.settings.coder_model
                task_id = args.get("task_id") or self.repo.create_task(
                    user_id, (args.get("task") or "")[:80]
                )
                # 助手AI 发布任务时，监控AI 也收到任务，决定如何监控这个代码AI
                plan = self.monitor.plan_monitoring(args.get("task") or "", model=model)
                agent_id = self.orchestrator.spawn_agent(
                    task_id=task_id,
                    task=args.get("task") or "",
                    workdir=args.get("workdir") or ".",
                    subtask_id=args.get("subtask_id") or "",
                    approval_list=args.get("approval_list") or [],
                    model=model,
                    max_steps=args.get("max_steps"),
                    max_tokens=args.get("max_tokens"),
                    stuck_timeout=plan.get("stuck_timeout"),
                    heartbeat_interval=plan.get("heartbeat_interval"),
                )
                print(f"[monitor] 监控AI 为 {agent_id} 规划监控参数：{plan}")
                # 真实派发成功后，主动告知用户（而不是只靠助手的口头汇报）
                self._send(user_id, f"📋 已派发任务给代码AI（{agent_id}，{model}）：{task_desc}")
                return f"已启动代码AI：{agent_id}（{model}）"
            if name == "stop_agent":
                self.orchestrator.stop_agent(args["agent_id"])
                return f"已停止 {args['agent_id']}"
            if name == "restart_agent":
                self.orchestrator.restart_agent(args["agent_id"])
                return f"已重启 {args['agent_id']}"
            if name == "list_agents":
                agents = self.repo.list_agents(args.get("task_id") or "")
                return json.dumps(agents, ensure_ascii=False, default=str)
            if name == "list_tasks":
                tasks = self.repo.list_tasks(user_id)
                return json.dumps(tasks, ensure_ascii=False, default=str)
            if name == "send_message":
                self._send(user_id, args.get("content") or "")
                return "已发送消息给用户"
            if name == "request_approval":
                path = args.get("path") or ""
                self._send(user_id, f"请求你的同意以操作：{path}（请回复 同意/拒绝）")
                return f"已请求用户同意：{path}"
            if name in self.plugins:
                return run_plugin(name, self.plugins[name], args)
            return f"未知工具: {name}"

        return exec_tool

    def _send(self, user_id: str, content: str) -> None:
        self.repo.add_message(user_id, "out", content)
        if self.channel:
            receiver = self.user_ids.get(user_id)
            if receiver:
                self.channel.send_text(receiver, content)
        else:
            # 控制台模式：直接打印，便于看到监控/汇报消息
            print(f"[推送] {content}")

    # ---------- 监控循环 ----------
    def run_monitor_loop(self, interval: int = 3):
        """后台线程：轮询运行中的 agent，状态变化时通知用户。"""

        def loop():
            while True:
                try:
                    self._tick()
                except Exception as e:
                    print(f"[monitor] {e}")
                time.sleep(interval)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return t

    def _tick(self) -> None:
        for task in self.repo.list_tasks():
            for agent in self.repo.list_agents(task["id"]):
                if agent["status"] not in ("running", "stuck", "needs_user"):
                    continue
                check = self.monitor.check_agent(agent["id"])
                new_status = check["status"]

                # 进程存活检测：running 但子进程已死（且无 done 事件）→ 崩溃
                if (
                    new_status == "running"
                    and agent["status"] == "running"
                    and not self.orchestrator.is_running(agent["id"])
                ):
                    new_status = "failed"
                    check["reason"] = "进程异常退出（崩溃）"

                if new_status != agent["status"]:
                    self.repo.update_agent(agent["id"], status=new_status)
                    if new_status == "done":
                        fresh = self.repo.get_agent(agent["id"]) or {}
                        result = json.loads(fresh.get("state") or "{}").get("result", "")
                        msg = f"✅ 代码AI（{agent['id']}）已完成"
                        if result:
                            msg += f"\n{result}"
                        self._send(task["user_id"], msg)
                        # 完成后自动让助手AI 继续推进任务（读代码→动手），直到整个任务完成
                        threading.Thread(
                            target=self._trigger_decision_on_agent_event,
                            args=(task["user_id"], agent["id"], "done", result or ""),
                            daemon=True,
                        ).start()
                    elif new_status in ("stuck", "needs_user", "failed"):
                        self._send(
                            task["user_id"],
                            f"⚠️ [代码AI {agent['id']}] {check['reason']}",
                        )
                        # stuck / failed 时，同时反馈给助手AI，让它决定重启/停止并汇报
                        if new_status in ("stuck", "failed"):
                            threading.Thread(
                                target=self._trigger_decision_on_agent_event,
                                args=(task["user_id"], agent["id"], new_status, check["reason"]),
                                daemon=True,
                            ).start()

    def _trigger_decision_on_agent_event(self, user_id: str, agent_id: str, status: str, reason: str) -> None:
        """代码AI 状态变化时，让助手AI 决策下一步（继续/重启/停止/收尾）并把结果汇报给用户。"""
        if self.decision is None:
            return
        if status == "done":
            msg = (
                f"[系统事件] 代码AI（{agent_id}）已完成，结果如下：\n"
                f"{reason[:2000] or '（无文本结果）'}\n\n"
                "请继续推进用户的原始任务：如果整个任务已全部完成，就简洁总结最终成果并汇报；"
                "如果还有剩余工作，就直接用 spawn_agent 继续派下一个代码AI 完成，不要停下来等用户。"
            )
        else:
            msg = (
                f"[系统事件] 代码AI（{agent_id}）状态变为 {status}，原因：{reason}。"
                "请决定如何处理：restart_agent 重启、stop_agent 停止，或什么都不做；"
                "然后用简短的话告诉我你的处理结果。"
            )
        with self._decision_lock:
            history = self._history(user_id, msg, limit=20)
            calls: list[str] = []
            try:
                reply = self.decision.run(
                    history,
                    msg,
                    self._executor(user_id, calls),
                    max_iter=self.settings.decision_max_iter,
                )
                reply = self._guard_fake_dispatch(reply, calls)
                self._send(user_id, reply)  # _send 内部已 add_message，这里不要再重复存
            except Exception as e:
                print(f"[decision-on-event] {e}")
