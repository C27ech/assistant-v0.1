# plugins/ —— 插件总览

插件是**助手本体能调用的外部程序**（跟 `assistant/tools/` 不是一回事：那是代码 AI 自己的工具箱）。
每个插件都是**独立可跑的 CLI**，可以脱离助手单独测试。

注册方式见 [`../docs/PLUGIN_PROTOCOL.md`](../docs/PLUGIN_PROTOCOL.md)。

## 一、随仓库附带的 5 个插件（15 个工具）

| 插件 | 目录 | 工具 | 依赖 | 说明 |
|------|------|------|------|------|
| **file_tools** | [`file_tools/`](file_tools/) | `read_file` `list_dir` `find_files` `grep_files` | 纯标准库 | 读本机文本 / 列目录 / 找文件 / 搜内容；**敏感文件硬拦截** |
| **web_tools** | [`web_tools/`](web_tools/) | `web_search` `fetch_page` | requests, bs4, playwright | 联网搜索（学术自动改道 + 垃圾站过滤）+ 无头浏览器读正文 |
| **controller_v2** | [`controller_v2/`](controller_v2/) | `desktop_task_v2` | requests, pillow, pyautogui, pynput, pywin32 | 自然语言操作 Windows 桌面（接口优先 + 视觉兜底） |
| **screenshot2qq** | [`screenshot2qq/`](screenshot2qq/) | `send_screenshot` | pillow, pyautogui | 截全屏并直接发到你的 QQ 私聊 |
| **sg_bridge** | [`sg_bridge/`](sg_bridge/) | `sg_games` `sg_attach` `sg_status` `sg_action` `sg_read_screen` `sg_click_text` `sg_key` | pynput, pywin32, pytesseract(可选) | 把游戏键鼠操作 + OCR 读屏做成接口（游戏自动化示例） |

> 每个插件目录里都有自己的 README，写清了用法、参数、踩过的坑。

## 二、共同约定

1. **命令行接口**：`python main.py <子命令> [参数]`，输出**纯文本**给模型读
2. **stdout 是结果，stderr 是日志**（别把日志打到 stdout，模型会当成结果）
3. **编码固定 UTF-8**（`sys.stdout.reconfigure(encoding="utf-8")`）
4. **失败要给原因**：打印得能让人看懂，比如「[错误] 安全策略拦截：…」
5. **要有上限**：条数 / 字数 / 超时都要设默认保底，别让它无限输出（会撑爆模型上下文，也拖慢回复）

## 三、性能经验（重要）

**插件调用是助手最慢的环节**。实测（本机）：

| 操作 | 耗时 |
|------|------|
| 普通回复（不用插件） | 10~25 秒 |
| `list_dir` / `grep`（小目录） | < 1 秒 |
| `web_search`（只搜网页） | ~1 秒 |
| `web_search`（走 3 个学术库） | ~6 秒 |
| `fetch_page`（无头浏览器读一页） | 4~7 秒 |
| **`fetch_page` 抓不可达站点** | **~70 秒**（45s 浏览器超时 + 25s HTTP 超时）|

结论与对策：

- ❌ **上下文大小几乎不影响单次调用速度**（实测 0K/10K/40K/80K 字输入，都是 0.5~1.3 秒）；
  真正决定耗时的是**插件调用次数**和**模型输出的长度**
- ✅ 每调一次插件，助手就要**多跑一整轮模型回合** → 调用次数直接线性放大延迟
- 🔧 所以：**在工具 `description` 里写死上限**（"最多抓 3 页"）、
  给不可达的域名做**快速失败**、给插件设**合理的 timeout**
- 🔧 把「一次能干多步」的批量接口暴露出来（如 `sequence` 类），比让模型循环调 50 次强得多

## 四、自己加插件

```bat
:: 1) 建目录写程序
mkdir plugins\my_plugin
::（写 main.py，见 docs/PLUGIN_PROTOCOL.md 第 5 节的骨架）

:: 2) 在 assistant\plugins.json 注册（cwd 用相对路径 "plugins/my_plugin"）

:: 3) 重启助手
```

**开工前先想三个问题**：

1. 这个能力**该不该**给助手？（想想别人能不能借它读你的密钥）
2. `description` 里怎么让模型**只在正确的场景**调用它？
3. 输出会不会**太长**？（长输出 = 贵 + 慢；必要时截断并提示续读方式）
