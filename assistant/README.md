# assistant/ —— 助手本体

一句话：**入口 + 三个 AI + 存储 + 渠道**。整体架构见 [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)。

## 目录速查

| 目录/文件 | 作用 | 关键点 |
|-----------|------|--------|
| `main.py` | 入口 | 单实例 Mutex → 选渠道 → 注册 `on_message` → 进循环 |
| `plugins.json` | 插件清单 | 每个插件 = 一个工具；`cwd` 相对本目录解析 |
| `.env.example` | 配置模板 | 复制成 `.env` 再填；`.env` 永不入库 |
| `assistant_watchdog.ps1` | 看门狗 | 挂了自动拉起 / 清理重复实例 / 可开机自启 |
| `config/settings.py` | 配置加载 | `.env` → `Settings`；含**使用者白名单**校验 |
| `config/plugins.py` | 插件执行器 | 占位符替换 / 子进程 / 日志落盘 / 超时 |
| `config/style.py` | 风格引擎 | 说明书 + 按当前消息检索例句 |
| `channel/` | 渠道适配 | 全部统一成 `IncomingMessage`；QQ 支持图/文 |
| `supervisor/runtime.py` | **主流程** | 白名单 → 取历史 → 决策 → 护栏 → 回复 |
| `supervisor/decision.py` | 决策 AI | 系统提示词 / 内置工具 / 工具循环 |
| `supervisor/monitor.py` | 监控 AI | 规则 + 可选 LLM，判代码 AI 是否卡死 |
| `supervisor/orchestrator.py` | 代码 AI 生命周期 | 起 / 停 / 重启 / 状态 |
| `agents/` | 代码 AI | `coding_agent.py` 单轮循环，`worker.py` 子进程入口 |
| `tools/` | 代码 AI 的工具箱 | 读写文件（限工作目录）/ 跑命令（统一 UTF-8）/ 审批闸门 |
| `storage/` | 存储 | SQLite + `memory.py` 的 TF-IDF 检索（零依赖） |
| `llm/deepseek.py` | 模型客户端 | OpenAI 兼容；模型名通配（`*` = 不指定型号）；带超时与重试 |
| `scripts/` | 语料处理脚本 | 风格功能用，见 [`scripts/README.md`](scripts/README.md) |
| `style/` | 风格数据 | 见 [`style/README.md`](style/README.md) |
| `tests/` | 测试 | `python -m pytest tests -q` |

## 跑起来

```bat
cd assistant
copy .env.example .env      :: 然后填 DEEPSEEK_API_KEY_DECISION / CHANNEL / ALLOWED_USERS
python main.py --channel qq
```

```bat
:: 控制台模式（不接渠道，本地调试用，豁免单实例闸门）
python main.py --console
```

## 排查手册

| 症状 | 先看哪里 |
|------|---------|
| 她**不回消息** | ① `assistant_err.log` 里搜 `402` / `Insufficient Balance`（余额）② 搜 `[错误] 决策AI 调用失败`（会带原因）③ `watchdog.log` 看她是不是刚重启过 |
| 她**回了别人不回我** | 搜 `[白名单] 已忽略未授权消息` —— 你发消息的账号不在 `ALLOWED_USERS` 里 |
| 她**说她做了但没做** | 这是"幻觉派发"；运行时有护栏会追加纠正提示；提示词里也有「诚实原则」 |
| 她**做了但很慢** | 看 `logs/plugin_*.log` 的执行时刻 —— 基本是插件调用次数堆出来的（见 [plugins/README.md 第三节](../plugins/README.md)） |
| 她**老派代码 AI 却不调插件** | 检查插件 `description` 是否写清楚了触发场景（模型只能靠它判断） |
| 代码 AI **频繁失败** | 看 `logs/agent_*.log`；中文 Windows 下的编码问题已在 `tools/shell_tools.py` 修掉 |

## 改动时注意

- **别在 `main.py` 里 `sys.exit(1)`**：看门狗会因为非 0 退出码判定"崩溃"而疯狂重启；
  单实例冲突这种情况要用**退出码 0**
- **给子进程设 `encoding="utf-8"` 和 `PYTHONUTF8=1`**：这是本项目踩过最多次的坑
- **提示词改动要重跑一遍真实对话**：加了约束常会有副作用（比如"诚实原则"一度让助手把一堆
  技术细节堆给用户，后来才补上「汇报说人话」）
