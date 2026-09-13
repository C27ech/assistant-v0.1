"""vision_exec：有状态视觉 agent loop 执行器（A 层）。

对外主入口：

    from vision_exec import run_vision_task
    result = run_vision_task("打开记事本", max_rounds=20)

实现链路：截图 -> 缩略图落盘 frames/ -> SQLite 历史 + BM25 检索 -> 多帧注入
prompt -> 视觉决策（done/action/unsure/stuck） -> core.guard 护栏 -> action 执行
-> 再次截图 verify -> 结果追加历史 -> 下一轮。历史持久化，进程重启可续跑。
"""

from .executor import run_vision_task

__all__ = ["run_vision_task"]
