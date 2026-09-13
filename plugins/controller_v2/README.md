# controller_v2

Windows 桌面控制器的完全重构版。新架构为 **「C 接口优先 + A 纯视觉端到端」**，
彻底移除老 `controller` 的经验规则引擎（`experience.py` 关键词子串匹配那一套），
不再有 `memory.json` / `experience.json` 的知识匹配层。

> 本仓库当前为「第一棒：地基 + 核心库 + 动作层」。高层编排
> （`router` C 层、`vision_exec` A 层、`main.py`）由下一棒接入。

## 项目定位

- 输入：自然语言桌面任务（例如「打开记事本」「登录某网站」）。
- 决策：豆包视觉模型（Ark / OpenAI ChatCompletions 兼容），纯视觉端到端。
- 执行：接口优先（win32 / cli / http），接口不可用时键鼠物理事件兜底。
- 坐标：全项目统一 **0~1000 千分比归一化坐标**，执行前才换算物理像素，杜绝高 DPI 错位。

## 目录结构

```text
controller_v2/
├── core/                     # 核心库
│   ├── __init__.py
│   ├── config.py             # 配置加载 + resolve_ark_api_key 三级解析
│   ├── screen.py             # DPI 感知 + 物理屏幕尺寸 + 截图
│   ├── coord.py              # 千分比 <-> 物理像素换算
│   ├── vision_client.py      # 豆包多模态决策 + 预期验证
│   └── guard.py              # 动作白名单 / 坐标 / 危险组合键拦截
├── action/                   # 动作层
│   ├── __init__.py
│   └── action.py             # 键鼠物理执行 + execute_interface 接口执行器骨架
├── config.json               # 运行时配置（key 留空，走环境变量）
├── requirements.txt          # 依赖清单
├── README.md
└── docs/
    └── architecture.md       # 新架构说明
```

## 启动方式

本棒为库层，不提供 `main.py` CLI 入口。作为库使用示例：

```python
from core.vision_client import multimodal_decide
from action.action import execute

# 1) 截图
from core.screen import capture_screenshot
shot = capture_screenshot()

# 2) 豆包决策（返回 0~1000 归一化坐标）
decision = multimodal_decide(shot, "打开记事本", model_tier="pro")
print(decision)

# 3) 执行第一个候选
if decision["status"] == "确定":
    cand = decision["candidates"][0]
    result = execute({
        "type": "mouse_click",
        "x": cand["x"],
        "y": cand["y"],
    })
    print(result)
```

## 配置与凭证

- 豆包 API Key 三级解析（见 `core/config.py`）：
  1. 环境变量 `DOUBAO_API_KEY`（真实 key 以 `ark-` 开头、长度 >= 20），
     兼容别名 `ARK_VISION_API_KEY` / `VISION_API_KEY` / `ARK_API_KEY`；
  2. 项目根 `secrets.json`（字段 `vision_api_key` / `doubao_api_key` / `ark_api_key`）；
  3. `config.json` 的 `vision.vision_api_key`（兜底，当前留空）。
- `base_url` 默认 `https://ark.cn-beijing.volces.com/api/v3`。
- 模型档位：`mini` / `turbo` / `pro`，默认决策档位 `pro`。

## 冒烟自测

```bash
python -m py_compile core/*.py action/*.py
python -c "import core.coord, core.guard, core.config, core.screen, core.vision_client, action.action"
```
