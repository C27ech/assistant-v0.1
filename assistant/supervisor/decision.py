"""决策AI：作为助手，理解需求、拆解任务、决定派活、汇报，并可调用工具管理代码AI。"""
from __future__ import annotations

import json
from typing import Optional

from config.style import build_style_block
from llm.deepseek import DeepSeekClient

SYSTEM_PROMPT = """你是「助手」—— 一个 AI 项目的统筹者，在用户与实际执行者（插件 / 代码 AI）之间协调。

你的职责：
1. 理解用户的需求；
2. **先判断现有插件能不能直接完成**：能就用插件，不能才拆解成子任务、启动「代码 AI」；
3. 用 stop_agent / restart_agent 管理代码 AI（卡住/失败时重启或关闭）；
4. 用 request_approval 就「需用户同意」的文件/操作向用户请示；
5. 用简洁、条理清晰的中文向用户汇报进展。

## 可用的外部插件（优先使用，别急着派代码 AI）
下列插件能直接完成对应的事。当用户需求能被某个插件完成时，**直接调用该插件**，不要 spawn_agent：
{PLUGINS}

## 判断顺序（务必遵守）
1. 先做语义判断：用户要做的这件事，上面某个插件能不能直接搞定？（别只看字面，要理解用户的真实意图）
2. 能 → 直接调用那个插件；拿到返回结果后，把**真实结果**汇报给用户；
3. 不能（插件确实做不到，比如要写新代码 / 做新功能 / 改动项目源码）→ 才用 spawn_agent 启动代码 AI。

## 诚实原则（最重要，务必严格遵守）
- **只汇报你真正通过工具执行了的事情**。没调用工具，就绝不要说「已经派发了 / 已经启动了 / 已经打开了 / 已经完成了」；
- 如果你只是在提议或说明计划，必须用「我打算…」「要不要我…」这类说法，不能用完成时态；
- 工具返回失败 / 超时 / 报错时，如实告诉用户失败原因，绝不谎报成功；
- 不要凭空编造 agent_id、文件路径、执行结果；不确定就说不确定。
- **绝不要自己写「📋 已派发任务给代码AI（agent-xxx）」这类系统确认行**——那是系统在你**真正调用 spawn_agent 之后**自动生成的。你照抄这个格式写进回复，会让用户以为真派了，这是严重违规。要派发就**必须实际调用 spawn_agent 工具**，不要在文字里"描述"派发。

{STYLE}

## 汇报风格（重要：对用户说人话）
- 用户是**普通人**，不是你的同事。**默认用大白话汇报**，以下东西**不要**写进回复：
  工具名（`sg_action`、`send_screenshot`）、内部编号（`agent-xxxx`、`PID`、`hwnd`）、
  协议字段（`retcode`、`message_id`、`EXITCODE`）、参数与单位（`hold_ms=40`、`41.4ms`）、
  文件路径、原始命令行原文（`powershell -NoProfile -Command ...`）。
- 说清「**做了什么、成没成、结果如何**」就够了。对比：
  - ✅ 好的：「按了一下回车推进一句，截图已经发你 QQ 了」
  - ❌ 差的：「`sg_action advance`（enter 41.4ms）✅ verified，`send_screenshot` 已发」
- **只有用户明确要技术细节**时（「你调的什么工具」「把命令原文贴出来」「PID 多少」「贴日志」），才给上面那些信息。
- 读屏／OCR 结果如果是明显乱码或错字（如「一一一0」「让我个呆」「欠」），**不要原文照抄**——
  要么说明「这行识别糊了」，要么转述能看懂的部分。

## 表达清楚（避免语义不通）
- **别自我加码**：如果前几轮你已经写得很抽象，这一轮要**往回收**，别继续往上堆——越堆越容易写出语义不通的句子。
- **宁可少说一句**：如果一句话你自己都讲不清是什么意思，就**别写**。写不出来的漂亮话，不如一句大白话。
- 长回答用**短句 + 分点**说清楚，不要写成一整段绕着讲。

## 看图能力
- 用户可能直接发来图片（截图、照片、界面图）——**你也能看到图**。本轮带了图就先仔细看图，再结合他的文字回答；图里的文字/报错/界面元素都可以直接读。
- 看完图要基于**图里真实内容**回答，不要凭想象描述图里没有的东西。

需求确认（务必严格执行）：
- 准备用 spawn_agent 派代码 AI 之前，逐项审视用户需求；只要有任何一处没明说、含糊、或可能有歧义，就必须停下来，逐条列出疑问向用户提问，绝不自己脑补默认值，也绝不急着 spawn_agent；
- 宁可多问、也不要猜：宁可被说啰嗦，也不要擅自假设；
- 重点检查并主动询问：目标（具体做什么）、运行平台/环境（Windows/Linux/Web/手机）、技术栈（指定语言/框架否）、功能范围（做哪些、不做哪些）、约束（性能/兼容/依赖）、期望产出（文件形式与数量）、验收标准（怎样算完成）。

规则：
- 需要多个代码 AI 时可以并行；每个代码 AI 只负责一个清晰、独立的子任务，并分配不同的 workdir（子目录），避免它们互相覆盖文件。
- 统一使用 deepseek-v4-flash；复杂/大任务把 max_steps、max_tokens 调大（写大文件/整个游戏就加大输出上限）。
- 启动代码 AI 后，简短说明这一步做了什么、下一步计划即可（后台监控会自动汇报进度，不要反复调用 list_agents 轮询）。
- 代码 AI 完成后，系统会自动把结果反馈给你并让你继续：此时要么继续 spawn_agent 派下一个子任务，要么在整体任务都完成时给出最终总结；务必持续推进，不要停下来等用户。
- 派发任务时，在任务描述（spawn_agent 的 task 参数）里明确要求代码 AI 完成后列出它改动/创建的所有文件与文件夹路径。
- 完成后给用户一个简短总结。

请始终用中文、严格遵守上面的风格说明书说话、简短分点回答。"""


DECISION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": "启动一个代码AI（独立子进程）来执行某个子任务。可多次调用以并行启动多个代码AI。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务ID（可留空，系统会自动创建）"},
                    "subtask_id": {"type": "string", "description": "子任务ID（可留空）"},
                    "task": {"type": "string", "description": "给代码AI 的任务描述"},
                    "workdir": {"type": "string", "description": "工作目录（绝对路径）"},
                    "approval_list": {"type": "array", "items": {"type": "string"}, "description": "需用户同意的文件/路径列表"},
                    "model": {"type": "string", "description": "模型：deepseek-v4-flash（默认）、doubao-seed-2-1-pro-260628、doubao-seed-2-1-turbo-260628、doubao-seed-2-0-mini-260428"},
                    "max_steps": {"type": "integer", "description": "该代码AI 最多执行步数（默认 30，大任务可加大）"},
                    "max_tokens": {"type": "integer", "description": "单次最大输出 token 数（默认 131072，写大文件可加大）"},
                },
                "required": ["task", "workdir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_agent",
            "description": "停止（关闭）某个代码AI 子进程",
            "parameters": {
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_agent",
            "description": "重启某个卡住/失败的代码AI（从断点续跑）",
            "parameters": {
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "列出某个任务下当前所有代码AI 及其状态",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "列出当前用户的所有任务",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "主动向用户发送一条消息（汇报进展或请示）",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID"},
                    "content": {"type": "string", "description": "消息内容"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_approval",
            "description": "就某个文件/操作向用户请求同意",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "op": {"type": "string", "description": "操作类型，如 write/run"},
                },
                "required": ["path"],
            },
        },
    },
]


class DecisionAgent:
    def __init__(
        self,
        client: DeepSeekClient,
        extra_tools: Optional[list] = None,
        style_examples: int = 8,
    ):
        self.client = client
        self.extra_tools = list(extra_tools or [])
        self.style_examples = style_examples

    def _plugin_lines(self) -> str:
        """把插件工具列表渲染成提示词里的清单文本。"""
        lines = []
        for t in self.extra_tools:
            f = t.get("function") if isinstance(t, dict) else None
            if not isinstance(f, dict):
                continue
            name = f.get("name")
            if name:
                lines.append(f"- {name}：{f.get('description', '')}")
        return "\n".join(lines) if lines else "（当前没有注册任何插件）"

    def _system_prompt(self, query: str = "") -> str:
        """系统提示词：注入当前插件清单 + 角色风格块（风格说明书 + 动态检索的例句）。"""
        prompt = SYSTEM_PROMPT.replace("{PLUGINS}", self._plugin_lines())
        style = build_style_block(query, k=self.style_examples)
        return prompt.replace("{STYLE}", style)

    @staticmethod
    def _finalize(content: str) -> str:
        """返回非空回复；若模型把额度花在思考上导致正式内容为空，给兜底文案。"""
        text = (content or "").strip()
        if text:
            return text
        return "（我这边思考到一半卡壳了，没吐出完整内容。你再说一次，我重新来。）"

    def reply(self, history: list[dict], user_message: str) -> str:
        """简单对话（不调用工具）。"""
        messages = [{"role": "system", "content": self._system_prompt(user_message)}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        result = self.client.chat(messages)
        return self._finalize(result.content)

    def run(
        self,
        history: list[dict],
        user_message: str,
        executor,
        max_iter: int = 20,
        images: list | None = None,
    ) -> str:
        """带工具调用的完整决策循环。executor(name: str, args: dict) -> str。

        images: 可选的图片 data URL 列表；有值时本轮用户消息按多模态（文字+图片）发送。
        """
        messages = [{"role": "system", "content": self._system_prompt(user_message)}]
        messages.extend(history)
        if images:
            parts: list = [
                {"type": "text", "text": user_message or "（用户发来一张图片，请先看图再回答）"}
            ]
            parts += [{"type": "image_url", "image_url": {"url": u}} for u in images]
            messages.append({"role": "user", "content": parts})
        else:
            messages.append({"role": "user", "content": user_message})

        for _ in range(max_iter):
            result = self.client.chat(messages, tools=DECISION_TOOLS + self.extra_tools)
            if not result.tool_calls:
                return self._finalize(result.content)

            messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in result.tool_calls
                    ],
                }
            )

            for tc in result.tool_calls:
                try:
                    args = json.loads(tc.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                try:
                    output = executor(tc.name, args)
                except Exception as e:
                    output = f"[工具执行异常] {e}"
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": output})

        return "决策AI 达到最大工具调用次数，请稍后重试。"
