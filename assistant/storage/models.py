"""领域常量：任务/子任务/agent/事件的状态与类型。"""
from __future__ import annotations

# --- 任务状态 ---
TASK_PENDING = "pending"        # 待拆解
TASK_PLANNING = "planning"      # 决策AI 拆解中
TASK_RUNNING = "running"        # 执行中
TASK_STUCK = "stuck"            # 有子任务卡住
TASK_NEEDS_USER = "needs_user"  # 等待用户确认
TASK_DONE = "done"
TASK_FAILED = "failed"

# --- 子任务状态 ---
SUBTASK_QUEUED = "queued"
SUBTASK_RUNNING = "running"
SUBTASK_STUCK = "stuck"
SUBTASK_DONE = "done"
SUBTASK_FAILED = "failed"

# --- agent 状态 ---
AGENT_RUNNING = "running"
AGENT_STUCK = "stuck"
AGENT_DONE = "done"
AGENT_STOPPED = "stopped"
AGENT_RESTARTING = "restarting"

# --- 事件类型 ---
EVENT_STEP_START = "step_start"
EVENT_TOOL_CALL = "tool_call"
EVENT_OUTPUT = "output"
EVENT_HEARTBEAT = "heartbeat"
EVENT_ERROR = "error"
EVENT_STUCK = "stuck"
EVENT_DONE = "done"
EVENT_NEED_APPROVAL = "need_approval"
