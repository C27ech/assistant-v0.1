# SG-Bridge —— 让 AI 替你操作 STEINS;GATE 系列

**这个插件叫什么**：**SG-Bridge**（项目里是 `sg_bridge\` 目录，桌面入口就是本文件夹 `SG-Bridge\`）
**它是干什么的**：把"按键 / 点鼠标 / 读屏幕文字"变成**可被外部调用的接口**。**你玩、你决定，AI（我，或任何支持 MCP/HTTP 的 AI）替你把操作做出来。**
**支持作品**：STEINS;GATE、STEINS;GATE 0、SG: Linear Bounded Phenogram、SG: My Darling's Embrace（自动识别窗口，无需配置）

---

## 一、30 秒上手（三种玩法，任选）

### 玩法 1：直接跟这个对话里的我说话（最省事）
1. 把游戏开着（**窗口模式**最稳），别关掉
2. 直接说，例如：
   - 「**打开手机**」→ 我按 Z
   - 「**念一下现在屏幕上是什么**」→ 我 OCR 读给你
   - 「**往下翻一封**」→ 我按 ↓
   - 「**快进 20 秒**」→ 我按住 CTRL
   - 「**存档**」→ 我按 F8（会先问你确认）
3. 我操作的那几秒**别同时动鼠标键盘**；想中断就说「**停**」

### 玩法 2：自己点着测（图形界面）
双击 **1-start-server.cmd**（窗口别关）→ 双击 **2-open-workbench.cmd**
浏览器会打开 `http://127.0.0.1:8765/docs`，每个接口都能点开、填参数、直接执行。

### 玩法 3：让别的 AI 调用（MCP / HTTP）
见第四、五节。

---

## 二、本文件夹里有什么

| 文件 | 作用 |
|---|---|
| **1-start-server.cmd** | 启动常驻 HTTP 服务（**双击后别关窗口**） |
| **2-open-workbench.cmd** | 打开浏览器工作台（Swagger，可点着测） |
| **3-self-test.cmd** | 10 项自检：键盘/鼠标/窗口/DPI/坐标（会用记事本当替身，约 10 秒） |
| **4-verify-all-games.cmd** | 自动逐部验证四作：启动→附着→测按键→测读屏→关闭 |
| **sg.cmd** | 命令行入口：`sg.cmd games`、`sg.cmd action phone --game SG0` … |
| **MCP配置示例.json** | 给支持 MCP 的 AI 客户端用（复制里面的段落即可） |
| **docs\\** | 完整文档（键位表、手机流程、验证报告、自然语言口令表…） |
| **README.md** | 本说明 |

---

## 三、常用命令（想自己敲时）

```bat
sg.cmd games                               :: 看四作是否安装/在运行
sg.cmd attach SG0                          :: 附着到 SG0（自动切前台 + 切英文输入法）
sg.cmd action phone --game SG0             :: 打开手机（Z）
sg.cmd action phone_close --game SG0       :: 收起手机（X）
sg.cmd action advance --game SG0           :: 推进对话（ENTER）
sg.cmd press down                          :: 下移
sg.cmd hold ctrl 20000                     :: 按住 CTRL 20 秒（强行快进）
sg.cmd call read_screen target=SG0         :: 把屏幕文字读出来（OCR，带坐标）
sg.cmd call find_text text=回复 target=SG0  :: 屏幕上找「回复」
sg.cmd call click_text text=收件箱 target=SG0 :: 按文字点击
```

---

## 四、让别的 AI 调用（MCP，推荐）

把 `MCP配置示例.json` 的内容合并进 AI 客户端的配置（Claude Desktop 的 `claude_desktop_config.json`、Cline 的 MCP 设置等），重启客户端后该 AI 就能看到 **34 个工具**（名字都带 `sg_` 前缀）：

```jsonc
{
  "mcpServers": {
    "sg-bridge": {
      "command": "python",
      "args": ["-m", "sg_bridge.mcp_server"],
      "cwd": "C:\path\to\assistant_v0.1\plugins\sg_bridge",
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

工具（共 34 个，节选）：`sg_list_games` `sg_attach` `sg_status` `sg_focus`
`sg_key_press` `sg_key_hold` `sg_click` `sg_click_norm` `sg_scroll`
`sg_game_action`（34 个语义动作）`sg_game_bindings`
**`sg_read_screen`** `sg_find_text` `sg_click_text` `sg_sequence` `sg_screenshot`
`sg_ime_status` `sg_ime_ensure_english` …

---

## 五、让别的程序调用（HTTP）

双击 **1-start-server.cmd** 后：

| 请求 | 作用 |
|---|---|
| `GET  /health` | 健康检查（返回工具数） |
| `GET  /tools` | 34 个工具 + 参数 schema |
| `GET  /docs` | Swagger 面板（浏览器点着用） |
| `GET  /openapi.json` | OpenAPI 规范（可直接喂给 AI 当函数定义） |
| `POST /call/<工具名>` | 调用，body 是参数对象 |

```bash
# 读屏幕
curl -X POST http://127.0.0.1:8765/call/read_screen -H "Content-Type: application/json" -d "{\"target\":\"SG0\"}"
# 打开手机
curl -X POST http://127.0.0.1:8765/call/game_action -d "{\"action\":\"phone\",\"game\":\"SG0\"}"
```
可选口令：`python -m sg_bridge.http_server --port 8765 --token 你的口令`（之后请求带 `X-Token` 头）。

---

## 六、你会说的话 → 实际做的事（口令表）

| 你说 | 我做 |
|---|---|
| 继续 / 再推两句 | ENTER 推进对话 |
| 快进这段 / 快进 20 秒 | 按住 CTRL（强行快进） |
| 隐藏文字 | 按住 SHIFT |
| 现在屏幕上是什么 / 念一下这段 | OCR 读出屏幕文字 |
| 打开系统菜单 / 看帮助 / 打开设置 / 看用语辞典 | 数字键 1 / F1 / F9 / F4 |
| 存档 / 读档 / 快速存档 / 快速读取 | F8 / F6 / F5 / F7（覆盖存档会先问你） |
| 回到标题 / 退出游戏 | F10 / ESC（**先问你确认**） |
| **打开手机 / 收起手机** | **Z / X** |
| 进收件箱 / 看发件箱 | 手机首页 ENTER / ↓ 再 ENTER |
| 往下翻一封 / 往上翻 / 返回 | ↓ / ↑ / BACKSPACE |
| 念一下这封邮件 | OCR 读出 发件人 + 时间 + 正文 |
| 选第 2 个 / 选「…」那个 | 移动到该项 + 确定 |
| 停 | 立刻停止操作 |

完整版：`docs\USAGE_自然语言操作.md`

---

## 七、注意事项 / 限制

1. **必须和游戏在同一台电脑上**（本插件是"代你按键"，不是远程控制）。
2. **操作瞬间游戏要在前台**，避免同时抢鼠标键盘；每次输入前会自动把游戏切回前台。
3. **危险动作默认拒绝**（退出/回标题/覆盖存档/切全屏），需显式授权 —— 换别的 AI 来调也不会误退游戏。
4. **中文输入法会吃掉按键**：附着/动作前会自动切英文并回报状态。
5. **读屏是 OCR**（本机 Windows 中文/英文模型），给出文字 + 坐标；个别错字用模糊匹配兜住。
6. **读不到**游戏内部变量（flag/好感度/路线进度）—— 不读内存、不改文件，只是"像玩家一样"操作。
7. **视频/OP 的字幕**烧在画面里，读不出来。

---

## 八、验证结果（2026-09-11 实测）

| 作品 | 窗口识别 | 按键生效 | 读屏 | 系统菜单 | 结论 |
|---|---|---|---|---|---|
| SG（中文） | ✓ | ✓ | ✓ | ✓ | 可用 |
| SG0（中文） | ✓ | ✓ | ✓ | ✓ | 可用 |
| SGLBP（英文） | ✓ | ✓ | ✓ | ✓ | 可用 |
| SGMDE（英文） | ✓ | ✓ | ✓ | ✓ | 可用 |

手机功能（开/收/收件箱/读信/滚动/返回）已在 SG 上逐项实测通过；SG0 也有手机系统（RINE）；LBP/MDE 无手机系统。
证据：`sg_bridge\out\verify\verify_result.json`；报告：`docs\VERIFY_四作.md`

---

## 九、故障排查

| 现象 | 处理 |
|---|---|
| 「无法连接远程服务器」/ 工作台打不开 | 服务没跑：双击 **1-start-server.cmd**，窗口保持开着；访问 `http://127.0.0.1:8765/health` 看是否 `{"ok":true,...}` |
| 按键没反应 | 游戏不在前台 → 让游戏窗口在前；或先 `sg.cmd attach SG` 再试 |
| 中文输入法吃掉按键 | `sg.cmd call ime_ensure_english target=SG` |
| 打开手机没反应 | 过场剧情中手机不可用；回到对话（画面稳定、有对话框）再试 |
| 读屏读不到字 | 该画面可能是纯图片/过渡黑屏，换个时机再读 |
| 菜单被裁掉 / 窗口比屏幕大 | 该作被 125% DPI 缩放放大 → 给对应 `Game.exe` 加 DPI 兼容标记（见 `docs\NODE2_keybindings.md` §5） |
| 想停服务 | 关掉 1-start-server.cmd 的黑窗口即可 |

---

## 十、代码与完整文档

```
C:\path\to\assistant_v0.1\plugins\sg_bridge\sg_bridge\
  api.py        34 个接口的唯一定义（HTTP 与 MCP 共用）
  core\         输入注入 / 窗口 / DPI / 输入法 / OCR
  tools\        手机流程、自动推进、读屏、四作验证…（19 个脚本）
  docs\         全部说明文档（本文件夹 docs\ 是副本）
  out\          截图、OCR 记录、验证证据
```

