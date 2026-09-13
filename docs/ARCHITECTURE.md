# 架构与实现

## 1. 一次对话的完整链路

```
QQ / 微信 / 企微
   │  ① 收到消息
   ▼
channel/*.py ──► IncomingMessage(sender, content, images)
   │  ② runtime.handle()
   ▼
supervisor/runtime.py
   ├─ 白名单校验（不在名单 → 静默忽略，不入库）
   ├─ _history()：最近 20 条 + TF-IDF 召回 30 条 → 拼成 messages
   ├─ repo.add_message(in)
   ├─ decision.run(history, msg, executor, max_iter)   ← 工具循环
   │      ┌──────────────────────────────────────────┐
   │      │  loop:  client.chat(system+messages+tools)│
   │      │      ├─ 有 tool_calls → executor 执行 → 把  │
   │      │      │   结果 append 进 messages → 继续     │
   │      │      └─ 没 tool_calls → 纯文本 = 回复，跳出 │
   │      └──────────────────────────────────────────┘
   ├─ 空回复 → 兜底文案；失败 → 回一条能看懂的原因（余额/限流/超时）
   ├─ _guard_fake_dispatch()：说"已派发"但没调 spawn_agent → 追加纠正
   └─ repo.add_message(out) → 返回
   │  ③ 渠道发回
   ▼
channel.send_text(...)
```

## 2. 三个 AI 各自的实现

### 决策 AI（`supervisor/decision.py`）

- `SYSTEM_PROMPT`：一段模板，含 `{PLUGINS}`（插件清单，动态生成）和 `{STYLE}`（风格块，动态）两个占位符
- 内置工具（7 个）：`spawn_agent` `stop_agent` `restart_agent` `list_agents` `list_tasks` `send_message` `request_approval`
- 插件工具：由 `config/plugins.py` 从 `plugins.json` 转成 OpenAI function schema，追加进工具表
- `run()` 是工具循环；`max_iter` 由 `DECISION_MAX_ITER` 控制
- 提示词里几条关键约束（都是踩坑换来的）：
  - **判断顺序**：先语义判断插件能不能干，能干就直接干；干不了才派代码 AI
  - **诚实原则**：只汇报真正调用工具执行过的事；没调工具不许说"已完成"
  - **汇报说人话**：工具名/PID/协议字段/毫秒数不要写进给用户的回复
  - **表达清楚**：别自我加码、讲不清就别写

### 代码 AI（`agents/coding_agent.py` + `agents/worker.py`）

- `worker.py` 是子进程入口：跑一个 `CodingAgent`，把过程写成 `events` 行
- `coding_agent.py`：`think → 调工具 → 观察 → 再 think`，直到完成或到步数上限
- 它的工具来自 `tools/`（`file_tools.py` 读写文件、`shell_tools.py` 跑命令）+ 审批闸门
- **两条纪律**：
  - 越界保护：文件操作限制在自己的工作目录里
  - 编码统一 UTF-8（`encoding="utf-8", errors="replace"` + 子进程注入 `PYTHONUTF8=1`），
    否则中文 Windows 上 `text=True` 会拿 GBK 解码 UTF-8 输出，中文命令结果全部丢失、agent 直接崩

### 监控 AI（`supervisor/monitor.py`）

- 规则层：距上次事件超过 `stuck_timeout` 秒 → 判定可能卡住
- 可选 LLM 层：把最近事件给一个小模型，让它判断「卡住 / 正常 / 出错」
- 结论通过 `runtime._trigger_decision_on_agent_event()` 交给决策 AI 处理（继续/重启/停）

## 3. 插件执行器（`config/plugins.py`）

```python
cmd = []
for part in spec["command"]:
    text = part
    for k, v in args.items():
        text = text.replace("{" + k + "}", fmt(v))     # 布尔 → true/false
    if "{" in text and "}" in text:
        continue          # 还有未提供的可选参数占位符 → 整段跳过
    cmd.append(text)

cwd = resolve(spec["cwd"])                             # 相对路径按 BASE_DIR 解析
subprocess.run(cmd, cwd=cwd, capture_output=True,
               encoding="utf-8", errors="replace",
               env={**os.environ, "PYTHONUTF8": "1"},
               timeout=spec["timeout"])
```

三个细节都是坑，改之前先读[根 README 第七节](../README.md#七插件系统)：

1. **可选参数必须写成 `--flag={value}`**，否则 flag 会被留下变成裸参数
2. **布尔必须转小写 `true/false`**（`str(False)` = `"False"`，命令行里是真值）
3. **编码固定 UTF-8** + `PYTHONUTF8=1`

插件子进程的 stdout/stderr 会逐个落盘到 `logs/plugin_<名字>_<时间戳>.{out,err}.log`，
超时/被杀也能留下最后的输出，便于排查。

## 4. 存储与检索（`storage/`）

SQLite 表：`users` `messages` `tasks` `subtasks` `agents` `events` `decisions`。

- `repo.py` 提供 CRUD；`search_history()` 做「相关内容召回」
- `memory.py` 是**纯 Python TF-IDF**（无任何依赖）：
  - 切词：中文按「单字 + 相邻双字」，英文按词
  - 权重：TF × IDF（高频虚词权重自动接近 0）
  - 相似度：余弦；返回 Top-K
- 为什么不用向量：DeepSeek 没有 embedding 接口；本地 embedding 要装 torch。
  TF-IDF 的定位是「认话题」而不是「认语义」，但对"翻旧账"够用且零成本。

## 5. 单实例 / 常驻 / 看门狗

- `main.py` 用全局 Mutex（`Global\Assistant.Main.<channel>.v1`）保证只有一个实例在处理消息；
  拿不到就退出（**退出码 0**，否则看门狗会当成崩溃而疯狂重启）
- `assistant_watchdog.ps1`：每 15 秒检查 → 挂了拉起 → 检测到多实例只记录不擅杀 → 写 `watchdog.log`
- `runtime/assistant.pid.json`：PID 名册，供看门狗识别

## 6. 数据流：代码 AI 事件怎么回到你

```
worker 子进程 → events 表（status/reason）
      │  runtime.run_monitor_loop() 轮询
      ▼
监控判定 → _trigger_decision_on_agent_event()
      ▼
决策 AI（带着「请继续推进用户的原始任务」的指令）→ 可能再派下一只 → 或收尾汇报
      ▼
_send() → 渠道 → 你手机上看到
```
