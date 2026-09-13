# sg_bridge —— 游戏自动化插件（示例）

把「**按键 / 点鼠标 / OCR 读屏**」变成可被助手调用的接口，用来生成一个**能替你玩游戏**的桥。
这一版对接的是《STEINS;GATE》系列四作，但**这套思路跟具体游戏无关**，换一套按键映射就能复用到别的游戏。

> 📌 仓库里**不含任何游戏素材**（无贴图、无文本、无音频）。这里只有"键鼠 + OCR"的代码。

## 目录结构

```
sg_bridge/
├── README.md              ← 你正在看的这份
├── launchers/             ← 独立运行的便利脚本（不依赖助手）
│   ├── 1-start-server.cmd       起 HTTP 服务（端口 8765）
│   ├── 3-self-test.cmd          自检
│   ├── 4-verify-all-games.cmd   逐个作品验证
│   ├── sg.cmd                   命令行快捷入口
│   └── MCP配置示例.json         接到 MCP 客户端（如 Cline）的配置示例
└── sg_bridge/             ← Python 包（助手就是调这个）
    ├── api.py             34 个底层工具的实现（键盘/鼠标/视觉/语义动作）
    ├── cli.py             命令行入口：python -m sg_bridge.cli call <工具> [k=v ...]
    ├── http_server.py     HTTP 服务（/tools、/call/<工具>）
    ├── mcp_server.py      MCP 服务（34 个 sg_ 前缀工具）
    ├── core/              窗口/输入注入/IME/OCR/游戏定义
    ├── tools/             开发期辅助脚本（探键位、验证、截图对比等）
    └── docs/              按键表、手机界面、自然语言用法等文档
```

## 能力

| 类别 | 工具（节选） |
|------|-------------|
| 会话/窗口 | `list_games` `attach` `detach` `status` `focus` `window_info` |
| 键盘 | `key_press` `key_hold` `key_combo` `type_text` |
| 鼠标 | `click` `click_norm`（归一化坐标，分辨率无关）`drag` `scroll` |
| 视觉 | `read_screen`（OCR 返回文字+坐标）`find_text` `click_text` `screenshot` |
| 语义动作 | `game_action`：advance / confirm / back / menu / help / config / skip / auto / phone / save / load … |
| 编排 | `sequence`（一次调用批量执行多步）`sleep` `ping` |

**安全设计**：

- 每次输入前**自动把游戏切前台 + 切英文输入法**（否则中文 IME 会吃掉按键）
- 危险动作（`quit` / `title` / `quick_save` / `quick_load`）默认拒绝，需显式 `allow_dangerous=true`，
  助手侧还要求**先问过用户**
- 不读游戏内存、不改存档文件，纯"像玩家一样操作"

## 助手怎么用它

`assistant/plugins.json` 里注册了 7 个 `sg_*` 工具（刻意只暴露高层语义动作，34 个底层工具太多会让模型选错）。
它们最终都执行 `python -m sg_bridge.cli call <工具> ...`，**cwd 指向本目录**（这样包能被 import）。

> 关键经验：**给模型的工具数要克制**。暴露 34 个底层工具时，模型经常选错；
> 收敛成 7 个语义动作（查状态/附着/读屏/按文字点/按键/动作）后明显稳了。

## 自己用起来要改什么

1. **游戏可执行文件路径**：`sg_bridge/core/games.py` 里每作都写了 `exe` 路径
   （形如 `C:\Program Files (x86)\Steam\steamapps\common\...`），按你机器上的实际路径改。
2. **按键映射**：`sg_bridge/config/bindings.json` + `docs/HELP_keytable.md`
   （不同版本/汉化版键位可能不同，用 `tools/probe_keybindings.py` 实测校准）。
3. **OCR（可选）**：`read_screen` 系列依赖 Tesseract。
   ```bat
   :: 装 Tesseract-OCR 后在 config 或环境里指定路径，并装中文语言包 chi_sim
   pip install pytesseract
   ```

## 独立跑（不经过助手）

```bat
cd plugins\sg_bridge

python -m sg_bridge.cli call list_games
python -m sg_bridge.cli call attach target=SG
python -m sg_bridge.cli call game_action action=advance
python -m sg_bridge.cli call read_screen

:: 或者起 HTTP 服务
launchers\1-start-server.cmd        :: → http://127.0.0.1:8765
```

更多细节见 `sg_bridge/docs/`（按键表、手机界面流程、自然语言用法）。
