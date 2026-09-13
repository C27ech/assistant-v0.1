# 用法说明：让 AI 替你操作游戏（自然语言 → 接口调用）

> **定位**：**你玩，AI 当你的手。** 你看着画面、用自然语言下指令，AI 通过 `sg_bridge` 接口替你把按键/点击做出来。
> 不是"让 AI 自己玩"，也不是让 AI 替你决定剧情 —— 决策权在你，AI 只执行。

---

## 一、怎么用（两种方式）

**方式 A：直接跟 AI 说话**（最省事）
> 你：「打开手机」「往下翻一封」「把这封念给我听」「快进这段」「存到 3 号档」
> AI：→ 调用对应接口 → 回报结果（必要时把屏幕文字读给你）

**方式 B：自己敲一条命令**（想自己动手时）
```powershell
cd C:\path\to\assistant_v0.1\plugins\sg_bridge
python -m sg_bridge.cli call game_action action=phone game=SG      # 打开手机
python -m sg_bridge.cli call key_press key=down                    # 下移
python -m sg_bridge.cli call read_screen target=SG                 # 把屏幕文字读出来
python -m sg_bridge.tools.look --game SG --click 收件箱             # 按文字点击
```

---

## 二、你说的话 → 实际调用（对照表）

### 对话 / 剧情
| 你会说 | 实际调用 |
|---|---|
| 继续 / 下一句 | `game_action advance`（= ENTER） |
| 快进这段 / 快进 30 秒 | `key_hold key=ctrl ms=30000`（强行快进＝按住 CTRL） |
| 打开/关闭快进模式 | `game_action skip_mode`（= Z）／`game_action skip`（按住 CTRL 一段） |
| 自动播放 / 关自动 | `game_action auto`（= F3） |
| 隐藏文字（截图用） | `key_down key=shift` … `key_up key=shift` |
| 现在屏幕上写了什么 | `read_screen`（OCR 读出来给你） |
| 把刚才这段念给我听 | `read_screen` ＋（可选）`sg_script_lookup` 对照剧本库 |

### 手机
| 你会说 | 实际调用 |
|---|---|
| 打开手机 / 拿手机 | `game_action phone`（= **Z**） |
| 收起手机 | `game_action phone_close`（= **X**） |
| 选下一项 / 上一项 | `key_press down` / `key_press up`（手机首页提示语会跟着变） |
| 确定 / 进入 | `game_action confirm`（= ENTER） |
| 返回上一层 | `game_action back`（= BACKSPACE） |
| 进收件箱 / 看邮件 | `game_action phone` → `confirm`（进首页）→ `confirm`（进收件箱） |
| 念一下这封邮件 | 上一步后 `read_screen`（含发件人/时间/正文） |
| 往下翻正文 | `key_press down` |
| 发件箱 | 手机首页 `key_press down`（提示变「打开发件箱」）→ `confirm` |

### 系统 / 存档
| 你会说 | 实际调用 |
|---|---|
| 打开系统菜单 | `game_action menu`（= 数字键 1） |
| 看按键说明 | `game_action help`（= F1） |
| 打开设置 / 音量 | `game_action config`（= F9） |
| 看用语辞典 | `game_action tips`（= F4） |
| 存档 / 读档 | `game_action save`（F8）/ `load`（F6）→ 槽位用 ↑↓ + ENTER |
| 快速存档 / 快速读档 | `game_action quick_save`（F5）/ `quick_load`（F7）⚠️ 会覆盖/立即回档 |
| 回到标题画面 | `game_action title`（F10）⚠️ |
| 退出游戏 | `game_action quit`（ESC）⚠️ |
| 游戏自带截图 | `game_action screenshot`（F12） |

> ⚠️ 标了警告的动作**会改变游戏状态**（退出 / 回标题 / 覆盖存档 / 切全屏）。
> 接口默认会**拒绝执行**并要求显式授权（`allow_dangerous=True`），AI 也应该先问你一句再执行。

### 通用
| 你会说 | 实际调用 |
|---|---|
| 屏幕上有没有「回复」 | `find_text text=回复`（模糊匹配，容忍 OCR 错字） |
| 点那个「回复」 | `click_text text=回复` |
| 点屏幕某个位置 | `click_norm nx=0.5 ny=0.8`（按窗口比例，分辨率无关） |
| 帮我记住现在这行 | `read_screen` + 写入日志/剧本对照 |

---

## 三、使用提示（重要）

1. **AI 操作时请不要同时动鼠标键盘** —— 接口走的是系统级输入注入，需要游戏保持前台；你抢焦点会把按键打到别处。
   （如果发生，AI 会在下一次输入前自动把游戏切回前台，你可以随时叫停。）
2. **危险动作先确认**：`quit / title / quick_save / quick_load / fullscreen` 建议由 AI 先问一句"确定吗"。
3. **速度**：单次按键约 0.1 秒；一次 OCR 读屏约 1~2 秒（所以"念这段"会稍慢）。
4. **中文输入法**：`attach`/`game_action` 会自动确保英文输入，避免中文 IME 吃掉 Z/X/ENTER。
5. **读屏不是"看图"**：用的是本机 Windows OCR（中文语言包），得到**文字 + 坐标**；有少量错字（如「指」→「扌旨」），查找用模糊匹配兜住。

---

## 四、当前可靠到什么程度

| 能力 | 状态 |
|---|---|
| 键盘/鼠标全键位（含长按、组合、归一化坐标点击） | ✅ 自检 10/10 |
| 34 个语义动作（推进/返回/菜单/帮助/配置/TIPS/存读档/手机/快进…） | ✅ 绑定来自游戏内 HELP + 实测 |
| 读屏幕文字（含坐标）、按文字点击、模糊查找 | ✅ 实测（读过收件箱 20+ 封邮件） |
| 手机：开/收/菜单导航/进收件箱/读邮件/滚动/返回 | ✅ 实测留证（`out/pv*.txt`） |
| 手机内「回复 → 发送」 | ⚠️ 只要邮件本身提供「回复」项，就是同一套操作（选到该项 + 确定）；暂未遇到可回复邮件 |
| 手机关联功能（发件箱/发新短信/来电/壁纸铃声设置） | ⏳ 未逐一验证（接口能力已具备） |
