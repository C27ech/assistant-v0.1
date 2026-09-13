"""代码AI 的 worker 子进程入口。

由编排器通过 `python -m agents.worker --agent-id <id>` 启动，从数据库读取
任务与断点，执行代码 AI，并把事件写回数据库，供监控AI 消费。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.coding_agent import CodingAgent  # noqa: E402
from config.settings import Settings  # noqa: E402
from llm.deepseek import DeepSeekClient  # noqa: E402
from storage.db import Database  # noqa: E402
from storage.repo import Repo  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()

    settings = Settings.load()
    db = Database(Path(args.db_path) if args.db_path else settings.db_file)
    repo = Repo(db)

    agent = repo.get_agent(args.agent_id)
    if not agent:
        print(f"agent 不存在: {args.agent_id}")
        sys.exit(1)

    state = json.loads(agent.get("state") or "{}")
    task = state.get("task", "")
    workdir = state.get("workdir", ".")
    approval_list = state.get("approval_list", [])
    max_steps = state.get("max_steps")
    max_tokens = state.get("max_tokens")
    heartbeat_interval = state.get("heartbeat_interval")

    agent_model = agent.get("model") or settings.coder_model
    api_key, base_url = settings.llm_credentials(agent_model, role="coder")
    client = DeepSeekClient(
        api_key,
        agent_model,
        base_url,
    )

    def emit(type_: str, payload: dict) -> None:
        repo.add_event(args.agent_id, type_, payload)

    def checkpoint(cp: dict) -> None:
        cur = json.loads(agent.get("state") or "{}")
        cur["resume"] = cp
        repo.update_agent(args.agent_id, state=json.dumps(cur, ensure_ascii=False))

    coding = CodingAgent(client, workdir, approval_list=approval_list, emit=emit, max_steps=max_steps, max_tokens=max_tokens, heartbeat_interval=heartbeat_interval)
    resume = state.get("resume")
    result = coding.run(task, resume=resume, on_checkpoint=checkpoint)

    # 保存最终结果，供监控在"完成"时把说明一并汇报给用户
    cur = json.loads(agent.get("state") or "{}")
    cur["result"] = result
    repo.update_agent(args.agent_id, state=json.dumps(cur, ensure_ascii=False))

    # 终态由监控AI 根据事件流判定（done / needs_user / stuck），此处不直接覆盖状态
    db.close()


if __name__ == "__main__":
    main()
