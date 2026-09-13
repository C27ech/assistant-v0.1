# 节点 2 总结：按键绑定（从"猜"到"实测确认"）

> 2026-09-10。前置节点见 `NODE1_input_bridge.md`。
> 本节点目标：**把 12 个推测动作升级为真实绑定**，并让"动作名"在真实游戏里可用。

---

## 1. 结论速览

* 拿到了**权威来源**：游戏内 `系统菜单 → HELP → KEYBOARD & MOUSE` 页面**直接画着完整键表**（第 1/2 页），
  这是官方文档，比任何猜测都可靠。
* 绑定表从 12 项 → **34 项**（含帮助/配置/TIPS/存档/手机/快进/隐藏文字/滚动等）。
* 修掉 **3 个错误默认值**，其中 1 个是**危险错误**：

| 动作 | 旧默认（错） | 正确值 | 依据 |
|---|---|---|---|
| `menu` 系统菜单 | **ESC** ⚠️ | **数字键 1** | HELP 画面标注 + 实测（9.64% 画面变化，BACKSPACE 可关） |
| `cancel` 取消/返回 | ESC | **BACKSPACE** | HELP 画面（ESC 是「游戏结束」！） |
| `auto` 自动模式 | A | **F3** | HELP 画面 |
| `backlog` | 滚轮上（猜） | 无此键；改为 `scroll_up/scroll_down` | HELP 画面 |
| `skip` 快进 | CTRL（猜对） | **按住 CTRL** | HELP 画面确认 |

* 新增**危险动作守卫**：`quit`(ESC) / `title`(F10) / `fullscreen`(F11) / `quick_save`(F5) / `quick_load`(F7)
  必须显式传 `allow_dangerous=True` 才执行，否则返回 `EPERM`（已实测拦截成功）。

---

## 2. 用的四种方法（以及为什么要四种）

| # | 方法 | 工具 | 结果 |
|---|---|---|---|
| A | **exe 字符串挖掘** | `tools/probe_keybindings.py` | 发现游戏用 `DirectInput8Create`；配置文件名 `config.dat`；启动器里写着配置路径模板 `%s\My Games` + `%s\mages_steam`；**HELP 的按键文案不在 exe 里**（是画面内容，所以搜不到） |
| B | **在已提取文本里搜界面措辞** | `tools/find_ui_text.py` | 命中关键线索：`MACROSYS2.SCX` →「**可以在系统菜单的ＨＥＬＰ中查看按键设置。**」→ 指明去 HELP 找 |
| C | **进游戏读 HELP 画面**（最权威） | 桥接自身（`attach/click_norm/key_press/screenshot`）+ `tools/view_shot.py` 裁剪放大 | 拿到完整键表（见下） |
| D | **数值差异实测**（不看图也能判断） | `tools/shot_diff.py`、`tools/probe_keys_live.py` | 在真实游戏里验证每个键；含**稳定性守卫**（先确认"无操作漂移 < 0.8%"再测，避开 Bink 影片/动画造成的假阳性） |

> 说明：C 依赖"读图"，而本次会话读图额度用尽后，D 成为主力手段 —— 这也顺带证明
> **桥接可以在"完全无视觉"的情况下工作**（靠像素差判断状态变化）。

---

## 3. 完整绑定表（来源：游戏内 HELP + 实测）

| 语义动作 | 按键 | 置信度 | 备注 |
|---|---|---|---|
| `advance` 推进对话 | ENTER | verified | 左键点击等效；实测对话框区域产生变化 |
| `confirm` 确定 | ENTER | verified | |
| `back` / `cancel` 返回 | BACKSPACE | verified | 实测可关闭 HELP 与系统菜单 |
| `click_confirm` | 鼠标左键 | help | |
| `right_click` | 鼠标右键 | help | 游戏中＝返回/系统菜单 |
| `menu` 系统菜单 | **1** | verified | ⚠️ 不要用 ESC |
| `help` | F1 | help | |
| `config` | F9 | help | |
| `tips` | F4 | help | |
| `backup_log` | F2 / INSERT | help | |
| `skip` / `skip_hold` 强行快进 | **按住 CTRL** | help | 可用 `ms` 覆盖时长 |
| `skip_mode` 快进模式切换 | Z | help | 未读文本不会被跳过，故实测时无明显变化 |
| `auto` 自动模式 | F3 / DELETE | help | |
| `hide_text` 隐藏文字 | **按住 SHIFT** | help | |
| `phone` 手机触发器 | E | verified | 实测打开大界面（约 80% 画面变化），需 `back` 关闭 |
| `save` / `load` | F8 / F6 | help | 打开存档/读档界面 |
| `quick_save` / `quick_load` | F5 / F7 | help · **dangerous** | 直接覆盖/回到快速存档 |
| `quit` 游戏结束 | ESC | help · **dangerous** | 弹确认（ENTER 确认 / BACKSPACE 取消） |
| `title` 回标题 | F10 | help · **dangerous** | |
| `fullscreen` 全屏切换 | F11 | help · **dangerous** | |
| `screenshot` 游戏截图 | F12 | help | 与桥接的 `screenshot` 工具不同 |
| `choice_up/down/left/right` | ↑ ↓ ← → | help | 项目选择 |
| `scroll_up` / `scroll_down` | 滚轮 ±120 | help | 列表/正文滚动、备份日志 |

---

## 4. 实测证据（数字全部来自本次会话）

| 检验 | 命令/方式 | 结果 |
|---|---|---|
| 稳定性守卫（关键） | `probe_keys_live` 先测"无操作漂移" | **0.08%** → 判定画面静止，可开始测键（首次没加守卫时被影片干扰，全部误判） |
| `advance` 推进 | 标题画面按 ENTER | 进入主菜单（65.78% 变化 = 场景切换）✓ |
| `advance` 连续推进 | ENTER 后再 ENTER | **0.63%** 变化（对话框文字推进）✓ |
| `back` 关界面 | HELP 画面按 BACKSPACE | **91.72%** 变化（回到标题菜单）✓ |
| `menu` 系统菜单 | 按 `1` | **9.64%** 变化；再按 BACKSPACE → **0.08%**（复位）✓ |
| `phone` 手机 | 按 E | **80.28%** 变化（打开大界面）；BACKSPACE 不复位（需专门关闭）✓ |
| `skip_mode` | 按 Z | **0.07%**（无可见变化，符合"未读不跳"预期）✓ |
| 语义层（真游戏） | `game_action menu` → 截图 vs 基线 | **9.64%**（与裸按 `1` 完全一致）✓ |
| 语义层（真游戏） | 再 `game_action back` | 回到基线 **0.08%** ✓ |
| 危险守卫 | `game_action quit`（不带 allow） | `ok:false` / **EPERM** ✓ |

证据文件：`sg_bridge/out/`（`p_*.png`、`v_*.png`、`liveprobe/result.json`、`sg_05_help2.png` 及放大图 `zoom_keys*.jpg`）。

---

## 5. 环境改动（⚠️ 请你知悉，都可一键还原）

为了能让桥接稳定工作，本次动了两处**本机设置**：

1. **游戏改成窗口模式**（启动器 → 设定画面 → SCREEN MODE = 窗口模式）
   原因：窗口模式下截图即窗口内容、坐标不受分辨率/全屏切换影响。
   还原：启动器 → 设定画面 → 选回「全屏」。

2. **给 `Game.exe` 加了 DPI 兼容标记 `~ HIGHDPIAWARE`**（注册表
   `HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers`）
   原因：本机显示缩放 **125%**，游戏不感知 DPI 时会被 Windows 放大 1.25 倍 →
   窗口变成 2400×1350（超出屏幕、菜单被裁掉）。加标记后恢复为 **1920×1080、1:1 像素**。
   还原：
   ```powershell
   Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers' -Name 'C:\Program Files (x86)\Steam\steamapps\common\STEINS;GATE\Game.exe'
   ```
   （另外三作若也要用桥接，建议同样加一次，把路径换成各自的 `Game.exe`。）

---

## 6. 新增工具（都在 `sg_bridge/tools/`）

| 工具 | 作用 |
|---|---|
| `probe_keybindings.py` | 扫 exe 里的按键/输入/配置相关字符串 |
| `find_ui_text.py` | 在我们已提取的全文本里搜界面措辞（`--kw=a,b,c`） |
| `view_shot.py` | 把大截图缩小/裁剪成可读小图（`--crop x,y,w,h --scale`） |
| `shot_diff.py` | 两张截图的差异（比例、像素数、变化区域包围盒）—— **无视觉判断的核心** |
| `probe_keys_live.py` | 在真实游戏里逐键实测：含稳定性守卫、复位检验、结果写 JSON |

桥接本体也增加了：`screenshot` 支持 `max_width`（缩小查看）、`game_action` 支持
`allow_dangerous`（危险动作守卫）。

---

## 7. 未决项 / 已知不完全确定

1. **HELP 图上 Z / C / E 的连线指向同一标签「手机触发器」** —— 实测 **E 确实能打开界面**；
   Z 无明显反应（符合"未读不跳"）；C 尚未单独确认（当时的界面状态被 E 打开后污染了基线）。
   → 想彻底定死：关掉界面回到对话，再单独测 `c`（`probe_keys_live --keys c --restore-key backspace`）。
2. **HELP 第 2 页**（1/2 指示器）没能翻过去（翻页键疑似手柄 L/R），内容大概率是**手柄**键位，对键鼠方案无影响。
3. **Phenogram / My Darling's Embrace 未单独实测**：它们同引擎、同为 Steam 版，推测键位一致
   （`backup_log`/`auto` 等文案在英文版 HELP 里未在我们的文本库中搜到，可能 UI 文案在贴图里）。
   → 需要时各跑一次 `probe_keys_live --game SGLBP --keys 1,escape,backspace,f3` 即可确认。
4. 手机界面内部（邮件列表、回复选项）的**导航键**尚未逐项验证（应同为方向键+ENTER+BACKSPACE）。

---

## 8. 下一步候选

| 编号 | 内容 | 价值 |
|---|---|---|
| N3-1 | 定死手机界面内部操作（打开→选邮件→选回复→发送） | 让"用手机/发消息"真正可用 |
| N3-2 | 加"状态识别"（用 `shot_diff` 指纹区分：对话/选项/菜单/手机/存档界面） | AI 无需看图就知道当前在哪个界面 |
| N3-3 | 选项识别与点击（配合我们已有的全文本库做区域定位） | 可以自动做选择 |
| N3-4 | 对 SGLBP / SGMDE 各跑一次键位实测 | 四作统一 |
| N3-5 | 把 `shot_diff` 升级为"区域指纹库"（每个界面的特征区域） | 更稳的状态机 |

---

## 9. 本节点改动文件

* 改：`sg_bridge/core/games.py`（绑定表 12 → 34 项，加 `dangerous` 标记）
* 改：`sg_bridge/api.py`（`game_action` 加 `allow_dangerous` + `EPERM`；`screenshot` 加 `max_width`）
* 改：`sg_bridge/core/wininput.py`（`screenshot(max_width/quality)`）
* 新：`sg_bridge/tools/{probe_keybindings,find_ui_text,view_shot,shot_diff,probe_keys_live}.py`
* 新：`sg_bridge/docs/NODE2_keybindings.md`（本文）
* 证据：`sg_bridge/out/` 下若干截图与 `liveprobe/result.json`

---

## 10. 复现命令

```powershell
cd C:\path\to\assistant_v0.1\plugins\sg_bridge

python -m sg_bridge.tools.probe_keybindings                  # exe 字符串
python -m sg_bridge.tools.find_ui_text --kw=系统菜单,手机触发器    # 文本库检索
python -m sg_bridge.tools.probe_keys_live --game SG --keys 1,e,c --restore-key backspace
python -m sg_bridge.tools.shot_diff a.png b.png               # 两张图差异
python -m sg_bridge.cli call game_bindings game=SG            # 查看当前绑定表
```

