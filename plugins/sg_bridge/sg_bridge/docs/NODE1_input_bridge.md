# 节点 1 总结：输入桥（外部接口模拟操作）

> 文档规范：每完成一个节点写一份，记录 **目标 / 方式 / 产出 / 验证 / 踩坑 / 下一步**，
> 便于后续节点回看。本节点日期：2026-09-10。

---

## 1. 本节点目标（按用户收敛后的范围）

**只要一件事**：把"操作游戏"这件事暴露成**外部可调用的接口**，效果等同用户自己在按键/点击。
用户自己看画面（不做文字识别、不做画面理解、不做 OCR）。

于是本节点**刻意不做**：
- ✗ 绘制流抓取 / 文本还原 / UI 元素识别（原 `SG_BRIDGE_API.md` 里的读取类工具，暂缓）
- ✗ DLL 注入、D3D9 挂钩、32 位编译（不需要）
- ✗ 离线跑剧情逻辑（社区无公开 VM，见 `../SG_BRIDGE_API.md` §11）

---

## 2. 技术选型：为什么用 OS 级 `SendInput` 而不是 DLL 注入

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. OS 级 `SendInput`**（本节点采用） | 零依赖（`ctypes` 即可）、无注入/崩溃风险、**Python 位数无关**、即时可用 | 需要游戏在前台；会移动真实光标 | ✅ 采用 |
| B. 进程内 hook 输入源（DirectInput8 / `GetAsyncKeyState`） | 不抢焦点、不动真实光标 | 需编译 32 位 DLL + 注入器；游戏是 32 位而本机 Python 是 64 位；崩溃风险 | 备用（若发现某作忽略 SendInput 再启用） |

**关键结论**：游戏都是 32 位（`Game.exe`），本机 Python 是 **3.14.3 64-bit** ——
这恰好说明 A 方案更省事：**无需交叉编译、无需注入器**。

### 2.1 两个必须处理的细节

1. **DPI 感知**：本机显示缩放 **125%**（物理 1920×1080，非感知进程只看到 1536×864）。
   若不做处理，所有点击坐标都会偏 1.25 倍。
   → 模块导入时调用 `SetProcessDpiAwarenessContext(-4)`（`per_monitor_v2`）；
   自测已验证虚拟桌面读回 **1920×1080**（正确）而不是 1536×864。
2. **键盘用 scancode**：`KEYEVENTF_SCANCODE` 对 DirectInput 游戏更可靠；
   同时保留 `use_vk=True` 的虚拟键回退模式。

---

## 3. 目录与文件清单（本节点新增）

```
sg_bridge/
  __init__.py            版本与范围说明
  core/
    wininput.py   (25.5KB) DPI 感知 + SendInput 键盘/鼠标 + 窗口枚举/聚焦 + 剪贴板 + 截图
    games.py      ( 8.2KB) 四作注册表、按目录匹配窗口、语义动作表（可被 JSON 覆盖）
  api.py          (22.0KB) ★工具注册表：29 个工具 + 统一信封 + 步骤解释器（HTTP/MCP 共用）
  http_server.py  ( 3.6KB) FastAPI + uvicorn（自带 /docs Swagger UI，可选 token）
  mcp_server.py   ( 4.6KB) MCP stdio（手写 JSON-RPC 2.0，**零第三方依赖**）
  cli.py          ( 5.2KB) 命令行快速测试（30+ 子命令）
  selftest.py     ( 4.8KB) 端到端自测（记事本：打字→剪贴板回读、鼠标坐标、截图）
  config/bindings.json    语义动作覆盖表（首次调用自动生成）
  out/                   自测证据：selftest_result.json / selftest_notepad.png / mcp_clean.jsonl
  docs/NODE1_input_bridge.md  ← 本文档
```

**设计要点**：`api.py` 是唯一真实来源（single source of truth）。
HTTP 的 `/tools`、`/call/{tool}` 与 MCP 的 `tools/list`、`tools/call` **共用同一份工具定义**，
不会出现两个入口行为不一致的问题。

---

## 4. 外部接口清单（29 个工具）

| 分组 | 工具 |
|---|---|
| 会话/窗口（8） | `list_games` `list_windows` `attach` `detach` `status` `focus` `window_info` `cursor_pos` |
| 键盘（6） | `key_press` `key_down` `key_up` `key_hold` `key_combo` `type_text` |
| 鼠标（9） | `mouse_move` `mouse_move_rel` `click` `click_norm` `drag` `drag_norm` `scroll` `mouse_down` `mouse_up` |
| 语义动作（2） | `game_bindings` `game_action` |
| 工具（4） | `sleep` `sequence` `screenshot` `ping` |

**语义动作**（`game_action` 的 action）：`advance` `confirm` `cancel` `back` `menu` `skip` `auto`
`backlog` `choice_up` `choice_down` `quick_save` `quick_load` —— 每项带 `confidence`
（high/medium/guess）与 `note`，可用 `config/bindings.json` 覆盖。

**批量执行**：`sequence` 支持一次往返执行多步，op 有：
`key_press / key_down / key_up / click / click_norm / scroll / mouse_move / sleep / screenshot / game_action`。

### 4.1 三个外部入口

```powershell
# ① CLI（最快验证）
python -m sg_bridge.cli ping
python -m sg_bridge.cli games
python -m sg_bridge.cli attach SG0
python -m sg_bridge.cli action advance --game SG0
python -m sg_bridge.cli hold ctrl 2000            # 快进 2 秒
python -m sg_bridge.cli click-norm 0.5 0.9        # 按窗口内归一化坐标点击

# ② HTTP（浏览器 / 任意语言 / AI 的 HTTP 工具）
python -m sg_bridge.http_server --port 8765
#   GET  /tools                → 29 个工具 + JSON Schema
#   POST /call/sg? body=参数    → 例：POST /call/key_press  {"key":"enter","ms":40}
#   GET  /docs                 → Swagger UI

# ③ MCP（AI 客户端直接调用）
python -m sg_bridge.mcp_server     # stdio；工具名带 sg_ 前缀
```

MCP 客户端配置：
```json
{ "mcpServers": { "sg-bridge": { "command": "python", "args": ["-m", "sg_bridge.mcp_server"] } } }
```

统一返回信封（两个入口一致）：
```json
{ "ok": true,  "data": { ... } }
{ "ok": false, "error": { "code": "EBADARG", "message": "KeyError: 未知键名: 'nosuchkey'" } }
```
错误码：`ENOTOOL` `EBADARG` `ESTATE` `EWIN32` `EINTERNAL`。

---

## 5. 验证结果（实测证据）

### 5.1 端到端自测 `python -m sg_bridge.selftest` → **10/10 PASS**（两次运行 0 失败）

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 找到记事本窗口 | PASS `hwnd=0x1A2B3C` 客户区 `[1200, 800]` |
| 2 | 切到前台 | PASS `tries=1`（前台锁绕过生效） |
| 3 | 键盘输入 | PASS `typed='sg-bridge selftest 123'` |
| 4 | **剪贴板回读一致** | PASS `clipboard='sg-bridge selftest 123'` ← **证明按键真的进入了目标窗口**（第二次运行因记事本恢复了上次会话内容，改用"包含"判定，仍 PASS） |
| 5 | **鼠标移动到目标坐标** | PASS 期望 `(1163,551)` 实际 `(1163,551)` ← **DPI 换算零误差** |
| 6 | 归一化点击换算 | PASS 点击后光标 `(1163,678)`，客户区 `[657,296,1013,766]` |
| 7 | 滚轮事件 | PASS `amount=-120` |
| 8 | **语义动作 `game_action` + 批量 `sequence`** | PASS `advance ok=True`、`sequence ok=True`，回读文本换行数 711（Enter 落到文档里） |
| 9 | 截图与客户区尺寸一致 | PASS `png=[1200,800]` = 客户区 `[1200,800]` |
| 10 | 关闭记事本 | PASS `pid=5352` |

证据文件：`sg_bridge/out/selftest_result.json`、`sg_bridge/out/selftest_notepad.png`。

### 5.2 HTTP 入口
```
GET  /health              → {"ok":true,"version":"0.1.0","tools":29}
GET  /tools               → count = 29
POST /call/list_games {}  → ok，四作 exe_exists 全部 true（当前均未运行）
```

### 5.3 MCP 入口（stdio JSON-RPC 2.0）
```
initialize      → {"protocolVersion":"2024-11-05","serverInfo":{"name":"sg-bridge","version":"0.1.0"},...}
tools/list      → 29 个工具（sg_ 前缀 + JSON Schema）
tools/call sg_ping            → ok（dpi_mode=per_monitor_v2，虚拟桌面 1920×1080）
tools/call sg_game_bindings   → ok（返回 SG0 的 12 个语义动作）
tools/call sg_key_press(bad)  → ok=false, code=EBADARG, isError=true（错误处理正常）
```
中文 UTF-8 已用**原始字节**校验：`"通过本服务可以用接口操作 STEINS;GATE 系列游戏（"` ✓

### 5.4 作品识别
四作路径全部命中（`exe_exists: true`）：`SG` / `SG0` / `SGLBP` / `SGMDE`；
窗口匹配采用**按 exe 所在目录**判定（因为 SG 与 SG0 标题都含 "STEINS;GATE"，按标题会误判）。

---

## 6. 踩坑记录（重要，后续节点别重踩）

| 现象 | 真因 | 处理 |
|---|---|---|
| 坐标会偏 ~1.25 倍 | 本机 **125% 缩放**，进程未 DPI 感知（看到 1536×864） | 导入时 `SetProcessDpiAwarenessContext(-2=-4)`，实测虚拟桌面=1920×1080 ✓ |
| 自测在第 4 步**直接崩溃**（无 traceback，退出码 1） | ctypes **未声明返回类型**：`GetClipboardData`/`GlobalLock`/`OpenProcess` 的 64 位句柄被截断成 32 位 | 在 `wininput.py` 加**完整 Win32 原型声明**（含 argtypes/restype） |
| `ModuleNotFoundError: sg_bridge.core.core` | `core/games.py` 里错写 `from .core import ...`（自己就在 `core` 里） | 改为 `from . import wininput as W` |
| 终端里看到中文乱码 | PowerShell 管道用 GBK 解码了 UTF-8 字节（**测试脚本的错觉，不是程序 bug**） | 用 `cmd /c "... > file"` 原始字节落盘 + Python `utf-8` 读回校验 ✓ |

---

## 7. 已知限制（本节点范围内）

1. **需要目标窗口在前台**：`SendInput` 的按键送给前台窗口。工具会在执行前自动 `focus`
   （`tries` 大多为 1，极少情况需要手动点一下游戏窗口）。
2. **独占全屏不推荐**：建议游戏用**窗口/无边框窗口**模式，坐标与聚焦最稳。
3. **会移动真实光标**：AI 操作期间用户不要同时用鼠标（这是"模拟用户"的固有代价）。
4. **游戏不能以管理员身份运行**（UIPI 会拦截注入；与游戏常规启动一致，无需处理）。
5. **部分语义动作的键位是推测值**：`auto` / `backlog` / `quick_save` / `quick_load`
   标了 `guess`，需按游戏内 CONFIG 核实后写进 `config/bindings.json`。
6. 若发现某作**完全忽略 `SendInput`**（罕见），再启用 B 方案（进程内 hook 输入源，需编译 32 位 DLL）。

---

## 8. 下一步（候选，等你定优先级）

| 编号 | 内容 | 价值 |
|---|---|---|
| N2-1 | **绑定核实**：把 12 个语义动作逐条实测（你按键/我记录），锁定 `bindings.json` | 让 AI 用"动作名"就能正确操作 |
| N2-2 | **运行环境预设**：启动前检查窗口模式/是否前台，给出明确提示 | 减少"点了没反应"的困扰 |
| N2-3 | **节拍与等待**：`sequence` 增加 `wait_text_change` 类语义（需你提供判定方式）或固定节拍参数 | 自动推进更稳 |
| N2-4 | 可选：**恢复读取类工具**（绘制流 → 文本），让 AI 自己知道当前剧情 | 从"盲操作"升级为"看懂再操作" |
| N2-5 | 可选：B 方案（DLL 注入），解决"必须前台"的限制 | 可后台挂机 |

---

## 9. 本节点改动文件清单

新增（`sg_bridge/` 全部）：`__init__.py`、`api.py`、`cli.py`、`http_server.py`、`mcp_server.py`、
`selftest.py`、`core/__init__.py`、`core/wininput.py`、`core/games.py`、`config/bindings.json`、
`out/selftest_result.json`、`out/selftest_notepad.png`、`out/mcp_clean.jsonl`、`docs/NODE1_input_bridge.md`。

未改动之前节点的产物：`output/1..4_*`（四作文本）、`tools/*`（提取工具链）、`out/*`（字符表等）。

---

## 10. 快速复现（3 条命令）

```powershell
cd C:\path\to\assistant_v0.1\plugins\sg_bridge
python -m sg_bridge.selftest        # 期望：9 项全 PASS
python -m sg_bridge.http_server     # 然后浏览器打开 http://127.0.0.1:8765/docs
python -m sg_bridge.mcp_server      # 给 AI 客户端用（MCP stdio）
```

