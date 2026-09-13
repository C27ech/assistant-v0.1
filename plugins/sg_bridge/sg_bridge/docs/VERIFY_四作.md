# 四作可用性验证报告

> 日期：2026-09-11　验证器：`sg_bridge/tools/verify_games.py`（桌面 `验证四作.cmd` 可直接重跑）
> 证据：`sg_bridge/out/verify/verify_result.json` + 每步截图 `out/verify/<作品>_*.png`

## 一、验证方法（每部作品跑同一套）

| 检查 | 做法 | 通过标准 |
|---|---|---|
| `window` | 按 exe 所在目录找到游戏窗口（必要时自动启动） | 找到且客户区 > 200px |
| `key_enter` | 截图 A → 按 ENTER → 截图 B | 像素变化 > 0.2%（说明按键进了游戏） |
| `ocr` | 对截图 B 做 OCR（中文/英文两种语言都试，重试 3 次取最佳） | 能读出至少一行文字 |
| `menu_key` | 按语义动作 `menu`（数字键 1）再对比 | 像素变化 > 0.2% |

另外会：自动切前台、**自动确保英文输入**（避免中文 IME 吃键）、
检测并点掉"退出确认"弹窗（有的作品 Alt+F4 会弹窗），最后稳健关闭游戏。

## 二、结果：四作全部 4/4 ✓

| 作品 | exe | window | key_enter | ocr | menu_key | OCR 语言 | 结论 |
|---|---|---|---|---|---|---|---|
| **SG**（STEINS;GATE 中文） | ✓ | ✓ | ✓ | ✓ | ✓ | zh-Hans-CN | **可用** ✓ |
| **SG0**（STEINS;GATE 0 中文） | ✓ | ✓ | ✓ | ✓ | ✓ | zh-Hans-CN | **可用** ✓ |
| **SGLBP**（Phenogram 英文） | ✓ | ✓ | ✓ | ✓ | ✓ | en-US | **可用** ✓ |
| **SGMDE**（My Darling's Embrace 英文） | ✓ | ✓ | ✓ | ✓ | ✓ | en-US | **可用** ✓ |

OCR 实测样例：
* SG：`62FPS / 7/已… / 我不会忘记、文件恩想…`
* SG0：`正在检测系统数据`（启动画面）
* SGLBP：`All unsaved game progress will be lost. / Exit Game? / Cancel`（退出确认弹窗）
* SGMDE：`System Data not found. / Created System Data.`

## 三、验证过程中发现的"作品差异"（已处理）

| 现象 | 说明 | 处理 |
|---|---|---|
| 英文作品读屏为 0 行 | OCR 默认用中文模型，英文文本识别不到 | 按作品语言选模型（`GAMES[g]["lang"]`），失败再换另一种重试 |
| SGLBP 的 Alt+F4 会弹「Exit Game?」 | 弹窗未确认 → 游戏没关掉，导致下一次验证的 ENTER 把弹窗确认了 | 开局先用 OCR 找「Cancel/取消」**点掉弹窗**；关闭改为「Alt+F4 → 仍在则 ENTER 确认」 |
| 标题/过场瞬间读不到文字 | 画面淡入淡出期间截图为黑屏 | OCR 检查重试 3 次取最佳 |
| 中文 IME 会吃掉按键 | 游戏窗口布局可能变成 zh-CN | `attach`/`game_action` 自动切 en-US（且已是英文时**不做任何切换**，避免反复发消息） |

## 四、结论

* **四部作品都能通过接口驱动**（窗口识别 → 按键生效 → 读屏 → 系统菜单），已验证 ✓
* 手机相关功能（开/收/收件箱/读信/滚动/返回）在 **SG** 上逐项实测通过 ✓（见 `NODE3_phone.md` §3.7）；
  SG0 也带手机系统（RINE），LBP/MDE 无手机系统（这两个是外传短篇合集）。
* 重跑验证：双击桌面 `SG-Bridge\验证四作.cmd`，或
  `python -m sg_bridge.tools.verify_games --games SG,SG0,SGLBP,SGMDE`
