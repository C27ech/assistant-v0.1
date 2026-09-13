# Assistant v0.1 —— 住在聊天软件里的 AI 助手

> 她**不自己写代码**。她是「统筹者」：理解你的需求 → 判断**现成插件能不能干** → 能干就直接调插件；干不了才**派代码 AI 去写** → 盯进度 → 把真实结果汇报给你。
>
> 支持三种聊天渠道：**QQ（OneBot / NapCat）**、**个人微信（WeChatFerry）**、**企业微信（自建应用回调）**。

---

## 目录

- [一、这是什么](#一这是什么)
- [二、核心设计：三个 AI 分工](#二核心设计三个-ai-分工)
- [三、功能一览](#三功能一览)
- [四、代码结构](#四代码结构)
- [五、快速开始](#五快速开始)
- [六、配置说明（.env）](#六配置说明env)
- [七、插件系统](#七插件系统)
- [八、风格系统（可选）](#八风格系统可选)
- [九、安全设计](#九安全设计)
- [十、测试](#十测试)
- [十一、已知限制](#十一已知限制)
- [十二、许可](#十二许可)

---

## 一、这是什么

一个可自托管的 AI 助手。跟「聊天机器人」的区别在于：**她有手**。

- **有插件**：能操作 Windows 桌面、能联网查资料、能读写本机文件、能截图发到你的聊天窗口、能操控游戏。
- **有手下**：遇到「要写新代码」的活，她会**派子进程里的代码 AI** 去干（可并行多只），自己盯进度。
- **有记忆**：历史全部落库，每轮用 **TF-IDF 语义检索**召回相关旧事，不会聊两句就失忆。
- **有人格**：说话风格由**你自己的语料**蒸馏出来（可选），也能跑纯功能模式。
- **有护栏**：使用者白名单、敏感文件硬拦截、危险动作需审批、防「幻觉派活」。

一句话：**你提需求 → 她拆解、调工具、派人、汇报，并且不骗你。**

---

## 二、核心设计：三个 AI 分工

```
                    ┌──────────────────────────────┐
   你（聊天软件） ──▶ │  渠道层 channel/             │  QQ / 微信 / 企业微信
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │  决策 AI（助手本体）          │  supervisor/decision.py
                    │  · 听懂需求 · 判断要不要插件  │  模型可配（默认 deepseek-v4-flash）
                    │  · 调度代码 AI · 汇报         │
                    └───────┬──────────────┬───────┘
                            │              │
             工具调用（插件） │              │ 派活
                            ▼              ▼
        ┌───────────────────────┐  ┌──────────────────────────┐
        │ 插件层 plugins.json    │  │ 代码 AI agents/worker.py │
        │ = 外部 CLI 子进程      │  │ 独立子进程，可多只并行    │
        │ 桌面/联网/文件/游戏…   │  │ 工具：读写文件、跑命令     │
        └───────────────────────┘  └────────────┬─────────────┘
                                                │ 事件流
                                   ┌────────────▼─────────────┐
                                   │ 监控 AI supervisor/      │
                                   │ monitor.py（规则+可选LLM）│
                                   │ 判卡死 / 异常 → 上报      │
                                   └──────────────────────────┘
                                                │
                    ┌───────────────────────────▼──────────────┐
                    │ 存储 storage/  SQLite：用户/消息/任务/    │
                    │ 子任务/代码AI/事件 + TF-IDF 语义检索      │
                    └──────────────────────────────────────────┘
```

**为什么要拆成三个 AI？**

| 角色 | 干什么 | 为什么单独拆出来 |
|------|--------|-----------------|
| **决策 AI** | 跟你对话、拆需求、调插件、派活、汇报 | 需要「人设 + 全局视野」，是唯一直接跟你说话的 |
| **代码 AI** | 在子进程里写代码/跑命令，直到任务完成 | 干脏活会污染上下文、也会崩；放子进程里崩了不影响主进程，还能并行多只 |
| **监控 AI** | 看代码 AI 有没有卡死/异常/跑偏 | 主 AI 在等结果时无法自检；用规则兜底 + 可选小模型判断 |

**消息流程（一次对话）**：

1. 渠道层收到消息 → **白名单校验** → 取历史（最近 20 条 + 语义检索 30 条）→ 入库
2. 决策 AI 进入**工具循环**（最多 `DECISION_MAX_ITER` 轮）：调模型 → 要调工具就执行 → 结果喂回去 → 再调模型……直到它给出纯文本回复
3. 插件 = 外部 CLI 子进程（`config/plugins.py` 统一执行：占位符替换、落盘日志、统一 UTF-8）
4. 派活 = 起一个 `agents/worker.py` 子进程，状态写入 `events` 表
5. 代码 AI 状态变化 → 触发决策 AI「接着推进」（不用你催）
6. 回复发回渠道；同时做**幻觉护栏**：嘴上说「已派发」但没真调 `spawn_agent` → 自动追加纠正提示

---

## 三、功能一览

### 助手本体

| 功能 | 说明 |
|------|------|
| 多渠道 | QQ（OneBot/NapCat）、个人微信（WeChatFerry）、企业微信（回调 + 加解密） |
| 工具循环 | 自主多轮调工具，`DECISION_MAX_ITER` 可配（默认 200） |
| 长期记忆 | SQLite 全量落库；每轮用 **TF-IDF 字符 n-gram 语义检索**召回 30 条相关旧事（`MEMORY_RETRIEVAL_K`） |
| 代码 AI 调度 | 派活/停止/重启/列表；可并行多只；工作目录隔离 |
| 卡死监控 | 规则（超时无事件）+ 可选 LLM 判断 → 主动汇报或重启 |
| 主动汇报 | 上线/卡住/完成 三类通知，均可开关 |
| 风格系统 | 语料蒸馏「风格说明书」+ 每轮动态检索例句（可选功能） |
| 多模态 | QQ 收图 → 转 base64 给视觉模型（超大图自动压缩） |
| 幻觉护栏 | 假派发检测 + 提示词层「诚实原则」 |
| 使用者白名单 | 只有名单内的账号能指挥她（其他人静默忽略、不入库） |
| 看门狗 | `assistant_watchdog.ps1`：挂了自动拉起、清理重复实例、可开机自启 |
| 单实例闸门 | 全局 Mutex，防止多实例抢消息 |
| 可观测 | 消息/任务/事件全落 SQLite；插件子进程 stdout/stderr 逐个落盘 |

### 随仓库附带的 5 个插件（合计 15 个工具）

| 插件 | 工具 | 干什么 |
|------|------|--------|
| **file_tools** | `read_file` `list_dir` `find_files` `grep_files` | 读本机文本文件 / 列目录 / 按名找 / 搜内容（**敏感文件硬拦截**） |
| **web_tools** | `web_search` `fetch_page` | 联网搜索（学术自动改道 arXiv/OpenAlex/Crossref + 垃圾站过滤）+ 无头浏览器读正文 |
| **controller_v2** | `desktop_task_v2` | 用自然语言操作 Windows 桌面（接口优先 + 视觉兜底） |
| **screenshot2qq** | `send_screenshot` | 截全屏并直接发到指定 QQ 私聊 |
| **sg_bridge** | `sg_games` `sg_attach` `sg_status` `sg_action` `sg_read_screen` `sg_click_text` `sg_key` | 把游戏键鼠操作 + OCR 读屏变成可调用接口（游戏自动化示例） |

---

## 四、代码结构

```
assistant_v0.1/
├── README.md                    ← 你正在看的这份
├── LICENSE  .gitignore  requirements.txt
│
├── docs/
│   ├── ARCHITECTURE.md          ← 架构与数据流详解
│   ├── PLUGIN_PROTOCOL.md       ← 插件协议（plugins.json 规范）
│   └── SECURITY.md              ← 安全设计（白名单 / 敏感文件 / 审批）
│
├── assistant/                   ← 助手本体
│   ├── README.md                ← 本模块说明
│   ├── main.py                  ← 入口：单实例闸门 + 渠道选择 + 消息路由
│   ├── plugins.json             ← 插件清单（工具名/命令模板/参数）
│   ├── .env.example             ← 配置模板（复制成 .env 再填）
│   ├── assistant_watchdog.ps1   ← 看门狗（自动重启 + 开机自启）
│   │
│   ├── config/
│   │   ├── settings.py          ← .env → Settings（含使用者白名单校验）
│   │   ├── plugins.py           ← 插件执行器：占位符 / 子进程 / 日志 / 超时
│   │   └── style.py             ← 风格引擎：说明书 + 动态例句检索
│   ├── channel/                 ← 渠道适配（统一成 IncomingMessage）
│   │   ├── base.py  qq_onebot.py  wechat_ferry.py  wecom.py  wecom_crypto.py
│   ├── supervisor/              ← 大脑
│   │   ├── runtime.py           ← 消息主流程 + 工具执行器 + 护栏
│   │   ├── decision.py          ← 决策 AI（系统提示词 / 工具 schema / 工具循环）
│   │   ├── monitor.py           ← 监控 AI（规则 + 可选 LLM）
│   │   └── orchestrator.py      ← 代码 AI 生命周期
│   ├── agents/                  ← 代码 AI
│   │   ├── coding_agent.py      ← 一轮 think → 调工具 → 观察
│   │   └── worker.py            ← 子进程入口（事件 → 数据库）
│   ├── tools/                   ← 代码 AI 的工具箱（≠ 插件！）
│   │   ├── base.py  approval.py ← 工具基类 + 审批闸门
│   │   ├── file_tools.py        ← 读写/追加/列目录（限制在工作目录内）
│   │   └── shell_tools.py       ← 跑命令（统一 UTF-8，防中文乱码）
│   ├── storage/                 ← 存储
│   │   ├── db.py  models.py     ← SQLite 连接 + 表结构
│   │   ├── repo.py              ← 增删查（含 search_history 语义召回）
│   │   └── memory.py            ← TF-IDF 检索（纯 Python，零依赖）
│   ├── llm/deepseek.py          ← 模型客户端（OpenAI 兼容，带超时+重试）
│   ├── scripts/                 ← 语料处理脚本（风格功能用）
│   ├── style/                   ← 语料 + 风格说明书（仓库里是占位样例）
│   └── tests/                   ← 单元/集成测试
│
└── plugins/                     ← 插件（每个都是独立可跑的 CLI）
    ├── README.md                ← 插件总览 + 怎么写新插件
    ├── file_tools/  web_tools/  controller_v2/  screenshot2qq/
    └── sg_bridge/               ← 含 launchers/（.cmd 启动器）
```

> ⚠️ **`assistant/tools/` 和 `plugins/` 是两回事**：
> `tools/` 是**代码 AI 自己用的**工具箱（在子进程里，受工作目录限制）；
> `plugins/` 是**助手本体调用的**外部 CLI（由 `plugins.json` 注册成她的工具）。

---

## 五、快速开始

### 1. 环境要求

- **Python 3.11+**（开发环境用的是 3.14）
- **Windows**（桌面控制 / 截图 / 微信 hook 依赖 Windows；纯聊天+联网+读文件在 Linux 也能跑）
- 一个 DeepSeek API Key（<https://platform.deepseek.com>）

### 2. 装依赖

```bat
cd assistant_v0.1
pip install -r requirements.txt
```

### 3. 配置

```bat
cd assistant
copy .env.example .env
```

编辑 `assistant\.env`，**至少填这三样**：

```ini
DEEPSEEK_API_KEY_DECISION=sk-你的key
CHANNEL=qq
ALLOWED_USERS=你的QQ号          # 强烈建议填！否则任何给你机器人发私聊的人都能指挥她
```

完整配置项见[第六节](#六配置说明env)。

### 4. 接渠道

**QQ（推荐）**

1. 装一个 QQ 机器人框架（如 **NapCat**），登录你的机器人 QQ 号
2. 在框架里开一个**反向 WebSocket**，地址 `ws://127.0.0.1:3001`
3. `.env` 里保持 `QQ_ONEBOT_URL=ws://127.0.0.1:3001`（默认值）
4. 用**你自己**的 QQ 私聊机器人号即可（只处理私聊，群消息会被忽略）

**个人微信（WeChatFerry）**：`.env` 里 `CHANNEL=wechat`，`WCF_HOST` 留空表示本地启动。
需要匹配版本的微信 PC 客户端 + WeChatFerry 的 DLL。

**企业微信（自建应用）**：`.env` 里 `CHANNEL=wecom`，填
`WECOM_CORP_ID / WECOM_AGENT_ID / WECOM_SECRET / WECOM_TOKEN / WECOM_AES_KEY`，
回调地址指向 `http://你的地址:8080/callback`。

### 5. 启动

```bat
cd assistant
python main.py --channel qq
```

看到这几行就对了：

```
已获得单实例 Mutex：Global\Assistant.Main.qq.channel.v1
[白名单] 已启用使用者白名单：['<YOUR_QQ_ID>']（其他人私聊会被静默忽略）
QQ 渠道启动，连接 OneBot WebSocket: ws://127.0.0.1:3001 ...
```

然后在 QQ 里给她发一句话试试（`NOTIFY_ON_START=true` 时她也会主动发一条上线通知）。

### 6. 后台常驻 + 开机自启（可选，但推荐）

```powershell
powershell -ExecutionPolicy Bypass -File assistant\assistant_watchdog.ps1
```

看门狗做的事：每 15 秒检查一次 → 助手挂了自动拉起 → 清理重复实例 → 全过程记 `watchdog.log`。

开机自启：把启动脚本（或它的快捷方式）丢进启动文件夹
（`Win+R` → 输入 `shell:startup`）。

### 7. 让她干活的例子

| 你说 | 她会做 |
|------|--------|
| 「我桌面上有哪些文件夹？」 | 调 `list_dir` → 报给你 |
| 「读一下桌面那个 `笔记.md`，总结一下」 | 调 `read_file` → 总结（长文件自动分块续读）|
| 「帮我查一下 XX 是什么」 | 调 `web_search`（学术词自动改道 arXiv/OpenAlex/Crossref）→ 必要时 `fetch_page` 读全文 |
| 「截个屏发我」 | 调 `send_screenshot` → 截图直接进你 QQ |
| 「把记事本打开，输入 hello」 | 调 `desktop_task_v2` 操作桌面 |
| 「帮我写个脚本，把某目录的图片批量改名」 | **插件做不到 → 派代码 AI** → 盯进度 → 汇报结果 |
| 「那只代码 AI 现在什么状态？」 | 调 `list_agents` / `list_tasks` → 如实汇报 |

---

## 六、配置说明（.env）

| 变量 | 默认 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY_DECISION` | — | **必填**，助手本体用的 key |
| `DEEPSEEK_API_KEY_MONITOR` | — | 监控 AI 用的 key（可留空，留空则监控只走规则） |
| `DEEPSEEK_API_KEY_CODER` | — | 代码 AI 用的 key（留空则由决策 key 兜底） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点，可换成任何兼容服务 |
| `DECISION_MODEL` | `deepseek-v4-flash` | 助手本体模型 |
| `MONITOR_MODEL` | `deepseek-v4-flash` | 监控模型 |
| `CODER_MODEL` | `deepseek-v4-flash` | 代码 AI 默认模型 |
| `DOUBAO_API_KEY` / `DOUBAO_BASE_URL` | — | 火山方舟（豆包）key；配了之后派活可选豆包模型 |
| `CHANNEL` | `wechat` | `qq` / `wechat` / `wecom` |
| `QQ_ONEBOT_URL` | `ws://127.0.0.1:3001` | QQ 渠道的 OneBot WebSocket 地址 |
| `WCF_HOST` / `WCF_PORT` / `WCF_DEBUG` / `WCF_BLOCK` | 空 / `10086` / `false` / `true` | 个人微信（WeChatFerry） |
| `WECOM_*` | — | 企业微信自建应用（corp_id / agent_id / secret / token / aes_key / port） |
| **`ALLOWED_USERS`** | 空 | **使用者白名单**（逗号/空格分隔）。留空 = 任何人私聊都能指挥她 ⚠️ |
| `USER_MAP` | 空 | 备注名 → user_id 映射，如 `老板:boss,同事:colleague` |
| `MEMORY_RETRIEVAL_K` | `30` | 每轮语义召回的历史条数（越大越记事儿，也越费 token） |
| `DECISION_MAX_ITER` | `200` | 每轮最多工具调用轮数（工具多/任务复杂就调大） |
| `DB_PATH` | `assistant.db` | SQLite 文件名（相对项目根目录） |
| `NOTIFY_ON_START` / `NOTIFY_ON_STUCK` / `NOTIFY_ON_DONE` | `true` | 主动汇报：上线 / 卡住 / 完成 |

---

## 七、插件系统

插件 = **一个能被命令行调用的程序**。助手通过 `plugins.json` 把它注册成自己的工具，
调用时由 `config/plugins.py` 起子进程执行，把 stdout 当工具结果喂回模型。

### 注册格式（`assistant/plugins.json`）

```json
{
  "web_search": {
    "description": "给大模型看的工具说明：什么时候该用、返回什么、有什么坑",
    "command": ["python", "main.py", "search", "{query}", "--source={source}"],
    "cwd": "plugins/web_tools",
    "timeout": 120,
    "params": {
      "query":  {"type": "string", "description": "查询词", "required": true},
      "source": {"type": "string", "description": "auto / web / academic（可选）"}
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| 键名 | 工具名（大模型看到的名字） |
| `description` | **最重要**：写好它，模型才知道何时调用；可以在这里写纪律（「每次最多抓 3 页」） |
| `command` | 数组形式的命令模板，`{参数名}` 会被替换成实参 |
| `cwd` | 子进程工作目录。**可以是相对路径**（相对 `assistant/` 解析），也支持绝对路径 |
| `timeout` | 秒；超时会终止子进程，并把已产生的输出落盘 |
| `params` | 参数 schema，会转成 OpenAI function-calling 的 JSON Schema |
| `required` | 不在 `required` 里的参数是**可选**的 |

### 三个容易踩的坑（本仓库已处理）

1. **可选参数的整段跳过**：写 `["--source", "{source}"]` 时，如果模型没传 `source`，
   就会留下一个光秃秃的 `--source` → argparse 报错。
   **正确写法**：`["--start={start_line}"]`（flag 和值放同一段，整段一起被跳过）。
2. **布尔值**：`str(False)` 会变成字符串 `"False"`，在命令行里反而是**真值**。
   执行器已统一转成小写 `true/false`。危险开关务必用 `{allow_dangerous}` 这种形式。
3. **编码**：子进程统一以 UTF-8 读写，并注入 `PYTHONUTF8=1`，
   否则中文 Windows 下 `text=True` 会用 GBK 解码，中文输出直接崩。

### 写一个新插件（3 步）

```bat
:: 1) 在 plugins/ 下建目录，写一个能命令行跑的程序
mkdir plugins\my_plugin
echo print("hello from plugin") > plugins\my_plugin\main.py

:: 2) 在 assistant/plugins.json 里注册（照抄上面格式，cwd 用相对路径）
:: 3) 重启助手 → 她就多了一个 my_plugin 工具
```

**建议**：一个工具干一件事；`description` 里写清"什么时候用、返回什么、有什么禁区"；
输出用纯文本（模型读起来最省事）。

随仓库附带的 5 个插件见 [`plugins/README.md`](plugins/README.md) 与各自的 README。

---

## 八、风格系统（可选）

**想让助手用特定角色的语气说话**，但你没有能力微调模型 —— 可以走「**离线蒸馏 + 在线检索**」这条路：

```
你的语料（每行一句台词）
      │  ① 蒸馏：分批喂给模型，归纳成「风格说明书」（口癖/句式/语气分寸/禁忌）
      ▼
style/style_guide.md（常驻提示词，约 1~2 千字）
      │  ② 每轮对话时，用 TF-IDF 从语料里检索出与当下话题最相关的 8 句原话
      ▼
注入系统提示词 → 说话像那个人
```

| 部件 | 文件 | 作用 |
|------|------|------|
| 风格引擎 | `config/style.py` | 读说明书 + 读语料 + 按当前消息检索例句 + 拼装风格块 |
| 说明书 | `style/style_guide.md` | 风格常量（**仓库里是我写的通用占位版**） |
| 语料 | `style/corpus.txt` | 检索语料（每行一句） |
| 蒸馏语料 | `style/corpus_distill.txt` | 可选：蒸馏用的更大语料（可中英混排） |
| 蒸馏脚本 | `scripts/build_style_guide.py` | 分批归纳 → 合并 → 生成说明书 |
| 清洗脚本 | `scripts/clean_corpus.py`、`fix_corpus_typos.py` | 去重/去乱码/修常见错字 |

**用法**：

```bat
cd assistant

:: 1) 把你的语料放进 style\corpus.txt（每行一句，空行和 # 开头的行会被忽略）
:: 2) 生成风格说明书（约几分钟）
python scripts\build_style_guide.py
:: 3) 重启助手
```

**不想要风格**：删掉 `style/style_guide.md`，`config/style.py` 会退回一段极简兜底
（也可以直接把系统提示词里的 `{STYLE}` 占位符去掉）。

> 📌 **仓库里不带任何第三方作品语料**。`style/corpus.txt` 里放的是几行我随手写的示例。
> 你要用谁的语气，请自备**你有权使用的**文本。

---

## 九、安全设计

这套东西**连着聊天软件，还有操作本机的权限**，所以护栏是设计的一部分（详见 [`docs/SECURITY.md`](docs/SECURITY.md)）。

| 防线 | 在哪 | 作用 |
|------|------|------|
| **使用者白名单** | `settings.py` + `runtime.py` | `ALLOWED_USERS` 非空时，名单外的人发消息 → **静默忽略**（不回复、不入库，只记一行日志） |
| **敏感文件硬拦截** | `plugins/file_tools` | `.env`、`config.json`、私钥/证书、SSH key、浏览器 Cookie/Login Data、云凭据、QQ 登录态、Windows 凭据库… 一律拒绝，**规则内置在代码里，只能追加不能取消** |
| **可读范围** | `plugins/file_tools/config.json` | `roots` 控制能读哪些目录树（`"*"` = 全盘，建议按需收窄） |
| **危险动作需审批** | `tools/approval.py` + 插件描述 | 如游戏「退出/回标题/覆盖存档」需要 `allow_dangerous=true`，且提示词要求**先问过你** |
| **代码 AI 沙箱** | `tools/file_tools.py` | 代码 AI 的读写被限制在**它的工作目录**内，越界直接报错 |
| **单实例闸门** | `main.py` | 全局 Mutex，避免多实例抢同一条消息 |
| **错误不再静默** | `supervisor/runtime.py` | 模型调用失败（余额 402 / 限流 / 超时）会**回一条能看懂的原因**，而不是让你干等 |

**建议**：`ALLOWED_USERS` 一定要填；`roots` 不要无脑给全盘；
给助手开新插件时，先想一遍「别人能不能借它读我的密钥」。

---

## 十、测试

```bat
cd assistant
python -m pytest tests -q          # 或者逐个 python tests\test_xxx.py
```

覆盖：存储/检索、决策工具、编排器、监控、渠道解析、企业微信加解密、代码 AI 循环、shell 工具编码。

---

## 十一、已知限制

- **Windows 优先**：桌面控制、截图、微信 hook 都依赖 Windows。核心（对话/记忆/联网/读文件）跨平台。
- **没有重放机制**：助手进程在你发消息时恰好挂了，那条消息不会自动补答（重启后她也不记得要回）。
- **风格不是微调**：只能"很像"，到不了 100% 复刻；检索是 TF-IDF 关键词匹配，认话题不认同义词。
- **读文件只读文本**：图片/PDF/Office/数据库会提示是二进制（想支持可以加 `pypdf` / `python-docx`）。
- **联网插件有反爬天花板**：登录墙、复杂验证码、纯视频页抓不到；部分站点（如维基百科）在特定网络下不可达。
- **余额/限流会直接打断她**：现在至少会告诉你原因，但不会自动切备用 key。
- **插件是子进程**：每次调用有进程启动开销；调用越多回合越慢（相关经验写在 `plugins/web_tools/README.md`）。

---

## 十二、许可

MIT License，见 [`LICENSE`](LICENSE)。

仓库内**不包含**任何第三方作品的文本、图像或游戏资源；
`plugins/sg_bridge` 只是「键鼠 + OCR」的自动化示例，不含游戏素材。




