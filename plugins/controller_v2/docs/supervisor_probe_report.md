# Supervisor 调用链 + Watchdog 进程模型侦查报告

> 只读侦查报告。本报告未修改/移动/删除 `C:\path\to\assistant_v0.1\assistant\` 下任何现有文件，
> 也未修改旧控制器 `C:\Users\<你的用户名>\Desktop\controller\` 下任何现有文件。
> 唯一持久化产出是本报告及其所在 `controller_v2\docs\` 目录。

---

## 1. 关键结论摘要（TL;DR）

1. **supervisor 位置**：`C:\path\to\assistant_v0.1\assistant\`，核心调用文件是
   `supervisor\runtime.py`；入口是 `main.py`，工具定义在 `supervisor\decision.py`。
2. **调用 controller 的方式**：不是常驻服务调用，而是 `subprocess.run` **同步拉起子进程**：
   - `desktop_command` → `python C:\Users\<你的用户名>\Desktop\controller\main.py --command "<指令>" --yes`
   - `multimodal_task` → `python C:\Users\<你的用户名>\Desktop\controller\multimodal_bridge.py "<指令>" [--look|--plan]`
   两者都 `timeout=300`、`capture_output=True`，在 supervisor 主进程内**阻塞等待**。
3. **「未知错误」字面量**出现位置：
   - `supervisor\runtime.py:172`（`_run_desktop_command`）
   - `supervisor\runtime.py:209`（`_run_multimodal_task`）
   触发条件：子进程**退出码非 0**，且 `stderr` 与 `stdout` **都为空/None** 时，
   用 `"未知错误"` 兜底。
4. **watchdog 误杀确认**：`assistant_watchdog.ps1` 用
   `Where-Object { $_.CommandLine -match 'main\.py' }` 匹配所有 python 进程，
   **controller 子进程的 `main.py` 也会命中**；当 assistant 主进程与 controller 子进程同时存在时，
   watchdog 判定 duplicate 并强杀较新的 controller 进程。
   - 具体实现位置：`assistant_watchdog.ps1:19-22`（匹配）、`:56-77`（判定 duplicate 并强杀）。
   - `watchdog.log` 已记录 4 次强杀 PID：`7240`、`29212`、`31376`、`9676`。
5. **decision_server 127.0.0.1:8765**：是 `controller\decision\server.py` 的 FastAPI 服务，
   由 `python -m decision.server` 手动启动（`uvicorn.run(app, host="127.0.0.1", port=8765)`，
   `decision\server.py:970-971`）。**当前 supervisor 的 multimodal_task 链并不走 HTTP 调用 8765**，
   而是让 `multimodal_bridge.py` 在进程内 `import decision.server` 直接调用 `run_command/_run_single_step`。

---

## 2. 目录结构

### 2.1 Assistant（supervisor 主仓）

```
C:\path\to\assistant_v0.1\assistant\
├── main.py                    # 入口：python main.py --channel qq
├── assistant_watchdog.ps1     # 看门狗（重点审查对象）
├── watchdog.log               # 看门狗日志（记录了强杀 PID）
├── assistant_run.log / assistant_err.log
├── .env / .env.example        # 配置（含 CHANNEL、DESKTOP_CONTROLLER_DIR 等）
├── requirements.txt / README.md
├── config\
│   └── settings.py            # Settings.load() 读取 .env
├── supervisor\
│   ├── __init__.py
│   ├── runtime.py             # 核心：决策循环 + 工具执行器 + 拉起 controller
│   ├── decision.py            # 决策 AI 提示词 + DECISION_TOOLS 工具 schema
│   ├── orchestrator.py        # 代码 AI 子进程 spawn/stop/restart
│   └── monitor.py             # 代码 AI 状态监控（规则 + flash 二次确认）
├── agents\
│   ├── __init__.py
│   ├── worker.py              # 代码 AI 子进程入口：python -m agents.worker --agent-id ...
│   └── coding_agent.py
├── channel\
│   ├── base.py
│   ├── qq_onebot.py           # QQ/OneBot WebSocket 客户端（ws://127.0.0.1:3001）
│   ├── wechat_ferry.py
│   ├── wecom.py / wecom_crypto.py
├── llm\
│   └── deepseek.py            # DeepSeek/豆包统一 LLM 客户端
├── storage\
│   ├── db.py / models.py / repo.py
├── tools\
│   ├── base.py / approval.py / file_tools.py / shell_tools.py
├── logs\
│   └── agent_<id>.log         # 代码 AI 子进程日志
├── scripts\  tests\
└── __pycache__\
```

### 2.2 controller（旧桌面控制器，被 supervisor 调用）

```
C:\Users\<你的用户名>\Desktop\controller\
├── main.py                    # 单次闭环入口：python main.py --command "..." --yes
├── multimodal_bridge.py       # 多模态桥接入口：单行 JSON 输出
├── config.json                # 控制器自身配置（模型档位/确认/风险等）
├── README.md / install.bat / requirements.txt
├── decision\
│   ├── server.py              # FastAPI 决策服务（127.0.0.1:8765）+ run_command/_run_single_step
│   ├── decision.py / memory.py / experience.py / trial_loop.py
│   └── induction\             # 归纳/记忆/经验代理
├── perception\
│   ├── perception.py / screen_capture.py / vision.py
├── action\
│   ├── action.py / safety.py
├── common\
│   ├── config.py / text_utils.py
└── archive\ backup\ ...
```

---

## 3. supervisor 调用 controller 的代码与参数契约

### 3.1 工具入口定义

文件：`C:\path\to\assistant_v0.1\assistant\supervisor\decision.py`

- `desktop_command` 工具 schema：`decision.py:139-152`，必填参数 `instruction`。
- `multimodal_task` 工具 schema：`decision.py:153-167`，参数 `instruction`（必填）、
  `mode`（可选，`execute / look / plan`，默认 `execute`）。

执行器分发在 `supervisor\runtime.py` 的 `_executor()` 内：

- `runtime.py:125-126`：`desktop_command` → `_run_desktop_command(instruction)`
- `runtime.py:127-128`：`multimodal_task` → `_run_multimodal_task(instruction, mode or "execute")`

### 3.2 desktop_command → `_run_desktop_command`

文件：`C:\path\to\assistant_v0.1\assistant\supervisor\runtime.py`，函数 `runtime.py:143-174`。

- 空指令：`runtime.py:146-147` 返回 `"[桌面控制器] 指令为空"`。
- 读取配置：`runtime.py:148` 取 `settings.desktop_controller_dir`
  （来自 `.env` 的 `DESKTOP_CONTROLLER_DIR=C:\Users\<你的用户名>\Desktop\controller`，默认回退
  `Path.home()/Desktop/controller`，见 `config\settings.py` 的 `desktop_controller_dir` 加载行）。
- 未配置：`runtime.py:149-150` 返回 `"[桌面控制器不可用] 未配置 DESKTOP_CONTROLLER_DIR"`。
- 检查 `controller_dir\main.py` 存在：`runtime.py:151-153`。

**确切 subprocess 命令**（`runtime.py:155-164`）：

```python
subprocess.run(
    [sys.executable, str(main_py), "--command", instruction, "--yes"],
    cwd=str(controller_dir),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=300,
    env={**os.environ, "PYTHONUTF8": "1"},
)
```

即实际命令行：

```text
python C:\Users\<你的用户名>\Desktop\controller\main.py --command "<instruction>" --yes
```

参数契约：

| 参数 | 说明 |
| --- | --- |
| `--command <instruction>` | 自然语言桌面指令，原样透传（controller 侧 `main.py:206` 定义） |
| `--yes` | 跳过执行前交互确认（对应 controller `auto_confirm`，`main.py:219-221`） |
| `cwd` | controller 根目录，保证其相对 import（`display_scale`、`common.config` 等）可用 |
| `timeout=300` | 300 秒超时 |
| 环境 | 继承 `os.environ`，追加 `PYTHONUTF8=1` |

controller 侧返回契约：`controller\main.py:243-270`，`main()` 返回退出码
`0`（成功）/ `1`（失败）；成功时把多模态链路的阶段与结果打印到 stdout。

supervisor 侧返回值/退出码处理：

- 超时：`runtime.py:165-166` → `"[桌面控制器执行超时] 指令在 300 秒内未完成"`。
- 其它异常：`runtime.py:167-168` → `"[桌面控制器异常] {e}"`。
- 退出码非 0：`runtime.py:171-173`
  `detail = (err or out or "未知错误").strip()`，返回 `"[桌面控制器执行失败] {detail[-1200:]}"`。
- 退出码 0：`runtime.py:174` 返回 `out[-2000:]`；stdout 为空时返回
  `"（桌面控制器已执行，但无文本输出）"`。

**「未知错误」字面量：`runtime.py:172`**
触发条件：`returncode != 0` 且 `stderr`、`stdout` 均为空/None。

### 3.3 multimodal_task → `_run_multimodal_task`

文件：`C:\path\to\assistant_v0.1\assistant\supervisor\runtime.py`，函数 `runtime.py:176-211`。

- 空指令：`runtime.py:179-180` → `"[多模态AI] 任务描述为空"`。
- 未配置/未找到桥接脚本：`runtime.py:182-186`
  检查 `controller_dir\multimodal_bridge.py`。

**确切 subprocess 命令**（`runtime.py:193-202`）：

```python
subprocess.run(
    [sys.executable, str(bridge), instruction, *flag],
    cwd=str(controller_dir),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=300,
    env={**os.environ, "PYTHONUTF8": "1"},
)
```

其中 flag（`runtime.py:187-191`）：

- `mode == "look"` → `["--look"]`
- `mode == "plan"` → `["--plan"]`
- `mode == "execute"`（默认）→ 无 flag

即实际命令行：

```text
python C:\Users\<你的用户名>\Desktop\controller\multimodal_bridge.py "<instruction>"        # execute
python C:\Users\<你的用户名>\Desktop\controller\multimodal_bridge.py "<instruction>" --look # look
python C:\Users\<你的用户名>\Desktop\controller\multimodal_bridge.py "<instruction>" --plan # plan
```

返回处理：

- 超时：`runtime.py:203-204` → `"[多模态AI 超时] 300 秒内未完成"`。
- 其它异常：`runtime.py:205-206` → `"[多模态AI 异常] {e}"`。
- 退出码非 0：`runtime.py:208-210`
  `detail = ((proc.stderr or "") or out or "未知错误").strip()`，返回 `"[多模态AI 失败] {detail[-1200:]}"`。
- 退出码 0：`runtime.py:211` 返回 stdout（单行 JSON）；为空则返回
  `"（多模态AI 未返回内容）"`。

**「未知错误」字面量：`runtime.py:209`**
触发条件：`returncode != 0` 且 `stderr`、`stdout` 均为空/None。

### 3.4 multimodal_task 的桥接实现与返回结构

文件：`C:\Users\<你的用户名>\Desktop\controller\multimodal_bridge.py`

- 取 `argv[0]` 作为 instruction；`--look`/`--decide` → `mode="next"`，`--plan` → `mode="plan"`，
  否则 `mode="execute"`（`multimodal_bridge.py:26-37`）。
- 进程内导入 `decision.server`：`from decision.server import run_command, _run_single_step`
  （`multimodal_bridge.py:39-40`），**不通过 HTTP 调用 8765 服务**。
- `execute` → `run_command(instruction)`；`look/plan` → `_run_single_step(instruction, mode=mode)`
  （`multimodal_bridge.py:48-52`）。
- 输出：单行 JSON，且 `_shrink()` 把所有超过 500 字符的字符串截断
  （`multimodal_bridge.py:15-23, 56`）。

返回结构（来自 `controller\decision\server.py`）：

- `execute`（`run_command`，`decision\server.py:749-774`）：
  - 成功：`{"ok": true, "need_confirm": false, "results": [...], "decision": {...}}`
  - 安全/白名单拦截：`{"ok": false, "need_confirm": true, "message": "...", "actions": [], "decision": {...}}`
  - 其它失败：`{"ok": false, "need_confirm": false, "message": "...", "actions": [], "decision": {...}}`
- `look/plan`（`_run_single_step`，`decision\server.py:1033` 起）：
  - `{"ok": true, "mode": mode, "screen_text": ..., "vision_description": ...,
     "inventory": ..., "action": {...}, "actions": [{...}],
     "memory_hit"?: bool, "memory"?: {...}}`
  - 失败：`{"ok": false, "mode": mode, "screen_text": ..., "vision_description": ...,
     "inventory": ..., "message": "...", "action": null, "actions": []}`

---

## 4. watchdog 完整逻辑与误杀结论

文件：`C:\path\to\assistant_v0.1\assistant\assistant_watchdog.ps1`
日志：`C:\path\to\assistant_v0.1\assistant\watchdog.log`

### 4.1 完整逻辑

- 每 15 秒检查一次（`assistant_watchdog.ps1:9, 79`）。
- 检测 NapCat/OneBot 3001 端口是否在监听（`:33-37`），仅状态变化时写日志（`:46-54`），
  不负责重启 NapCat。
- 查找 "assistant 进程"（`:19-22`）：

```powershell
function Get-AssistantProc {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'main\.py' }
}
```

- 主循环（`:56-77`）：
  - `count == 0`：重启 assistant（`Start-Assistant`，`:24-31`），命令为
    `python main.py --channel qq`（`-WorkingDirectory $assistantDir`，输出重定向到
    `assistant_run.log`/`assistant_err.log`，`-WindowStyle Hidden`）。
  - `count > 1`：判定 duplicate。按 `CreationDate` 升序取**最老的** PID 保留（`:70`），
    其余进程 `Stop-Process -Id <pid> -Force` 强杀并写日志（`:71-75`）。

### 4.2 误杀根因（文件路径 + 行号）

| 位置 | 代码 | 问题 |
| --- | --- | --- |
| `C:\path\to\assistant_v0.1\assistant\assistant_watchdog.ps1:20-21` | `Name='python.exe'` + `CommandLine -match 'main\.py'` | 仅按「python.exe 且命令行含 `main.py` 子串」匹配，**无路径锚定、无 cwd/父进程校验** |
| `assistant_watchdog.ps1:68` | `elseif ($procs.Count -gt 1)` | 只要匹配到 >1 个就进入 duplicate 清理 |
| `assistant_watchdog.ps1:70` | `$keep = ($procs | Sort-Object CreationDate | Select-Object -First 1).ProcessId` | 保留最老的，其余视为 duplicate |
| `assistant_watchdog.ps1:72-74` | `Stop-Process -Id $p.ProcessId -Force` | 强杀非保留进程 |

**误杀链条**：

1. supervisor 调 `desktop_command` 时，`runtime.py` 用 `subprocess.run` 拉起
   `python C:\Users\<你的用户名>\Desktop\controller\main.py --command ... --yes`。
2. 该 controller 子进程的命令行**包含 `main.py`**，被
   `Get-AssistantProc` 的 `'main\.py'` 正则**误当成 assistant 主进程**。
3. 此时 `$procs.Count == 2`（assistant 主进程 + controller 子进程），watchdog 进入 duplicate 清理。
4. assistant 主进程通常更早创建，controller 子进程更新，因此**保留 assistant、强杀 controller**，
   导致桌面控制任务在 300 秒内被外力终止（表现为 `_run_desktop_command` 收到非 0 退出或异常）。
5. **反向风险**：若 controller 先启动（较老）而 assistant 后重启（较新），
   watchdog 会反过来保留 controller、强杀 assistant。
6. 说明：`python -m agents.worker ...` 的代码 AI 子进程命令行不含 `main.py`，不受此误伤。

### 4.3 watchdog.log 已记录的强杀 PID

文件 `C:\path\to\assistant_v0.1\assistant\watchdog.log`（时间均为 2026-09-08）：

| 行号 | 时间 | 内容 |
| --- | --- | --- |
| 3-4 | 17:38:01 | `Detected 2 assistant processes. Cleaning up duplicates...` / `Killed duplicate PID=7240` |
| 5-6 | 17:38:17 | `Killed duplicate PID=29212` |
| 7-8 | 17:41:54 | `Killed duplicate PID=31376` |
| 9-10 | 17:42:40 | `Killed duplicate PID=9676` |

> 注：日志只记 PID，不记命令行，无法从日志 100% 回溯这些 PID 是否都是 controller 子进程；
> 但结合匹配逻辑（`main\.py` 子串）与「2 个进程同时存在即清理」的判定，
> 这 4 次强杀高度疑似就是 controller `main.py` 子进程被误杀。

### 4.4 修复方向（供后续重构参考，本次未改动）

- 把 `Get-AssistantProc` 改为精确匹配 assistant 主进程，例如同时校验
  `$_.CommandLine` 匹配完整路径 `C:\path\to\assistant_v0.1\assistant\main.py` 且包含 `--channel qq`，
  或校验 `ExecutablePath`/`WorkingDirectory`/`ParentProcessId`。
- duplicate 判定应排除 controller 子进程（命令行含 `C:\Users\<你的用户名>\Desktop\controller\` 或
  `controller_v2\` 的进程不参与 assistant duplicate 清理）。
- 强杀前记录完整命令行，便于事后审计。
- 可考虑把 watchdog 换成 Python 版（新代码放 `controller_v2` 或独立目录），
  用 `psutil` 更精确地按命令行 + 工作目录 + 父子关系匹配。

---

## 5. 进程模型图（文字描述）

```text
[Windows 计划任务 / 手动]
        │ 启动 assistant_watchdog.ps1（PowerShell，每 15s 检查）
        ▼
┌─────────────────────────────────────────────────────────────┐
│ assistant_watchdog.ps1                                       │
│  - 检测 NapCat/OneBot 3001 是否存活（只记日志，不重启）        │
│  - 检测 python.exe 命令行含 "main.py" 的进程                  │
│  - count==0 → Start-Process python main.py --channel qq       │
│  - count>1  → 保留最老 PID，其余 Stop-Process -Force（误杀点） │
└─────────────────────────────────────────────────────────────┘
        │ 保活
        ▼
┌──────────────────────────────────────────────────────────────┐
│ Assistant 主进程（supervisor）                                │
│  python main.py --channel qq                                  │
│  cwd=C:\path\to\assistant_v0.1\assistant                          │
│  ├─ run_qq(): QQOneBotChannel → ws://127.0.0.1:3001 (NapCat) │
│  ├─ run_monitor_loop(): 后台线程每 3s 轮询 agent 状态          │
│  └─ Runtime.handle → DecisionAgent.run(工具循环, 最多 20 轮)   │
│        └─ _executor() 分发工具                                │
│             ├─ desktop_command  ──► subprocess.run(同步)      │
│             │     python <controller>\main.py --command ... --yes
│             │     （子进程，阻塞 ≤300s）                       │
│             ├─ multimodal_task ──► subprocess.run(同步)       │
│             │     python <controller>\multimodal_bridge.py ... [--look|--plan]
│             │     （子进程，阻塞 ≤300s；bridge 进程内 import    │
│             │      decision.server.run_command/_run_single_step）
│             └─ spawn_agent ──► Orchestrator.spawn_agent       │
│                   subprocess.Popen(异步)                      │
│                   python -m agents.worker --agent-id <id> ... │
│                   （子进程，日志 logs\agent_<id>.log）         │
└──────────────────────────────────────────────────────────────┘
        │ 独立可选服务（当前 supervisor 不通过 HTTP 调用它）
        ▼
┌──────────────────────────────────────────────────────────────┐
│ controller\decision\server.py（FastAPI decision_server）      │
│   python -m decision.server  →  uvicorn 127.0.0.1:8765        │
│   或 uvicorn decision.server:app --host 0.0.0.0 --port 8000   │
│  路由：/health /actions /command /confirm /decide-step /plan  │
│        /experience/*                                          │
└──────────────────────────────────────────────────────────────┘
```

父子关系：

- supervisor 主进程（`python main.py --channel qq`）由 watchdog 拉起。
- controller 子进程（`main.py` / `multimodal_bridge.py`）是 supervisor 主进程的**直接子进程**，
  且为 `subprocess.run` 同步阻塞式（最多 300s），期间 supervisor 的主决策循环被占用。
- 代码 AI 子进程（`agents.worker`）也是 supervisor 主进程的直接子进程，但为
  `subprocess.Popen` 异步启动，由 `Orchestrator.procs` 跟踪。
- `decision_server`（8765）是独立可选 FastAPI 服务，当前不是 supervisor→controller 链路的组成部分。

---

## 6. decision_server 127.0.0.1:8765 的启动与调用方式

- 定义：`C:\Users\<你的用户名>\Desktop\controller\decision\server.py`，
  `app = FastAPI(...)`（`decision\server.py:149`）。
- 显式启动（`if __name__ == "__main__"`）：`uvicorn.run(app, host="127.0.0.1", port=8765)`
  （`decision\server.py:970-971`）。即：

```text
python -m decision.server        # 开发调试，默认 127.0.0.1:8765
uvicorn decision.server:app --host 0.0.0.0 --port 8000   # 生产/局域网
```

- `controller\main.py --serve` 只打印上述启动说明，不真正启动服务
  （`main.py` 的 `print_server_instructions()` 与 `--serve` 分支）。
- 路由：`/health`、`/actions`、`/command`、`/confirm`、`/decide-step`、`/plan`、
  `/experience/*`（`decision\server.py:903-962, 1137-1143`）。
- **当前 supervisor 侧不通过 HTTP 调用 8765**：`multimodal_task` 走
  `multimodal_bridge.py` 进程内 `import decision.server` 直接调用
  `run_command` / `_run_single_step`，因此 8765 服务对现有 supervisor→controller 链
  是「独立存在、未接入」的状态。

---

## 7. 要彻底重构 supervisor + 修 watchdog 需要改哪些文件

### 7.1 修 watchdog（误杀修复）

| 文件 | 需改动 |
| --- | --- |
| `C:\path\to\assistant_v0.1\assistant\assistant_watchdog.ps1` | 必改。重写 `Get-AssistantProc`（`:19-22`）为精确匹配 assistant 主进程（完整路径/参数/工作目录/父进程），duplicate 判定（`:56-77`）排除 controller 子进程，强杀前记录完整命令行 |
| 建议新增（可选） | Python 版 watchdog，例如 `controller_v2\watchdog.py` 或独立目录，用 `psutil` 做精确匹配 |

### 7.2 重构 supervisor（换血）

| 文件 | 需改动 |
| --- | --- |
| `C:\path\to\assistant_v0.1\assistant\supervisor\runtime.py` | 核心。`_run_desktop_command`（`:143-174`）与 `_run_multimodal_task`（`:176-211`）改为调用新 controller_v2 契约；建议把同步阻塞 `subprocess.run` 改为异步/队列/长驻服务调用，处理「未知错误」兜底逻辑（`:172`、`:209`） |
| `C:\path\to\assistant_v0.1\assistant\supervisor\decision.py` | 工具 schema（`desktop_command` `:139-152`、`multimodal_task` `:153-167`）按新契约调整参数/描述 |
| `C:\path\to\assistant_v0.1\assistant\supervisor\orchestrator.py` | 若代码 AI 子进程模型一并换血，改 `_build_cmd`/`spawn_agent`/`stop_agent`/`restart_agent` |
| `C:\path\to\assistant_v0.1\assistant\supervisor\monitor.py` | 若监控语义随新 orchestrator 变化，同步改 |
| `C:\path\to\assistant_v0.1\assistant\config\settings.py` | `desktop_controller_dir` 默认值与加载逻辑，指向 `controller_v2` |
| `C:\path\to\assistant_v0.1\assistant\.env` | `DESKTOP_CONTROLLER_DIR` 改为 `C:\path\to\assistant_v0.1/plugins/controller_v2`（或新配置键） |
| `C:\path\to\assistant_v0.1\assistant\main.py` | 如入口/渠道选择/启动方式变化，同步改（`--channel qq`、`run_qq`） |
| `C:\path\to\assistant_v0.1\assistant\agents\worker.py` / `agents\coding_agent.py` | 如代码 AI 链路一并换血，改 worker 入口与 agent 逻辑 |

### 7.3 新 controller（新代码）

| 文件 | 说明 |
| --- | --- |
| `C:\path\to\assistant_v0.1/plugins/controller_v2\` | 新建桌面控制器 v2；建议保留 `main.py --command ... --yes` 与 `multimodal_bridge.py "<指令>" [--look|--plan]` 兼容契约，或与 supervisor 同步定义新契约 |
| `C:\path\to\assistant_v0.1/plugins/controller_v2\docs\supervisor_probe_report.md` | 本报告 |

---

## 8. 本次读取的所有文件路径

只读侦查，共读取/查看了以下文件与目录（未做任何修改）：

- `C:\path\to\assistant_v0.1\assistant\main.py`
- `C:\path\to\assistant_v0.1\assistant\supervisor\runtime.py`
- `C:\path\to\assistant_v0.1\assistant\supervisor\decision.py`
- `C:\path\to\assistant_v0.1\assistant\supervisor\orchestrator.py`
- `C:\path\to\assistant_v0.1\assistant\supervisor\monitor.py`
- `C:\path\to\assistant_v0.1\assistant\config\settings.py`
- `C:\path\to\assistant_v0.1\assistant\.env`（内容含 API key，报告中已脱敏处理，不复制其值）
- `C:\path\to\assistant_v0.1\assistant\assistant_watchdog.ps1`
- `C:\path\to\assistant_v0.1\assistant\watchdog.log`
- `C:\path\to\assistant_v0.1\assistant\agents\worker.py`
- `C:\path\to\assistant_v0.1\assistant\channel\qq_onebot.py`（部分）
- `C:\Users\<你的用户名>\Desktop\controller\main.py`
- `C:\Users\<你的用户名>\Desktop\controller\multimodal_bridge.py`
- `C:\Users\<你的用户名>\Desktop\controller\decision\server.py`
- `C:\Users\<你的用户名>\Desktop\controller\config.json`
- `C:\Users\<你的用户名>\Desktop\controller\README.md`（部分）
- `C:\Users\<你的用户名>\Desktop\controller\install.bat`
- 目录清单：`C:\path\to\assistant_v0.1\assistant\`、`...\supervisor\`、`C:\Users\<你的用户名>\Desktop\controller\`
  及其 `decision\`、`perception\`、`action\`、`common\` 子目录。

## 9. 本次创建的文件/文件夹路径

- `C:\path\to\assistant_v0.1/plugins/controller_v2\docs\`（新建目录）
- `C:\path\to\assistant_v0.1/plugins/controller_v2\docs\supervisor_probe_report.md`（本报告）

（侦查过程中在工作目录内产生的临时探针文件已删除，不保留任何其它文件。）
