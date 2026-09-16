# controller_v2 新架构说明

> 第一棒交付范围：`core/` 核心库 + `action/` 动作层 + 项目根配置/文档。
> 本棒不做 `router` / `vision_exec` / `main.py` 高层编排，但已把对外接口定义清楚，
> 可直接被下一棒调用。

## 1. 总体架构

```text
                     ┌─────────────────────────────────────────────┐
   自然语言任务 ────▶ │  router（C 层，下一棒）                      │
                     │  - 接口优先：win32 / cli / http              │
                     │  - 接口不可用 → 键鼠兜底                     │
                     └───────────────┬─────────────────────────────┘
                                     │ 调用
                     ┌───────────────▼─────────────────────────────┐
                     │  vision_exec（A 层，下一棒）                 │
                     │  截图 → 豆包决策 → 执行 → verify 闭环         │
                     └───────────────┬─────────────────────────────┘
                                     │ 调用
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
        core.vision_client     action.action          core.guard
        multimodal_decide      execute / 键鼠原语       check_action
        verify_expectation     execute_interface
              │                      │
              ▼                      ▼
        core.screen / core.coord / core.config（地基）
```

- **C 层 router（接口优先、键鼠兜底）**：先尝试调用 win32/cli/http 三类接口通道；
  接口未实现/失败时，回退到键鼠物理动作。
- **A 层 vision_exec（纯视觉端到端）**：`截图 -> 豆包决策 -> 执行 -> verify 闭环`，
  不做经验规则/记忆匹配。
- **core 库**：配置、屏幕、坐标、视觉客户端、安全护栏。
- **action 层**：键鼠物理执行 + 接口执行器骨架。

## 2. 豆包视觉接入

- API Key 三级解析（`core.config.resolve_ark_api_key`）：
  1. 环境变量 `DOUBAO_API_KEY`（`ark-` 前缀且长度 >= 20）→ 别名
     `ARK_VISION_API_KEY` / `VISION_API_KEY` / `ARK_API_KEY`；
  2. 项目根 `secrets.json`（`vision_api_key` / `doubao_api_key` / `ark_api_key`）；
  3. `config.json` 的 `vision.vision_api_key`（兜底）。
- endpoint：`{base_url}/chat/completions`，默认
  `https://ark.cn-beijing.volces.com/api/v3`。
- 模型档位（`vision.model_tiers`）：
  - `mini`  = `doubao-seed-2-0-mini-260428`
  - `turbo` = `doubao-seed-2-1-turbo-260628`
  - `pro`   = `doubao-seed-2-1-pro-260628`
- 请求体：OpenAI ChatCompletions 兼容、非流式；
  `content` 为 `[image_url, text]`，图片为 `data:image/jpeg;base64,...`。
- 图片采样：短边 640、JPEG 质量 80。
- 超时：`(connect=15, read=180)` 秒，使用 `requests`。
- 响应：`choices[0].message.content`，兼容 `str` 与 `list` 两种形态。

### 2.1 多模态决策输出契约

```json
{
  "status": "确定|不确定",
  "expectation": null,
  "candidates": [{"name": "...", "x": 0, "y": 0, "confidence": 0.9}],
  "reason": "..."
}
```

- `x` / `y` 为 0~1000 整数（千分比归一化）。
- `status == "不确定"` 时，`candidates` 必为空、`expectation` 必为 `null`。

### 2.2 预期验证三态

`verify_expectation(screenshot_path, expectation) -> True | False | None`

- `True`：截图明确显示预期已实现。
- `False`：截图明确显示预期未实现（出现反证）。
- `None`：证据不足 / 验证失败。

## 3. 坐标换算（重点）

全项目统一使用 **0~1000 千分比归一化坐标**，执行前才换算物理像素：

```text
px = round(norm_x / 1000 * physical_width)
py = round(norm_y / 1000 * physical_height)
# 再 clamp 到 [0, width-1] / [0, height-1]
```

反向换算：

```text
norm_x = round(px / physical_width * 1000)
norm_y = round(py / physical_height * 1000)
```

物理分辨率必须 DPI-aware 后获取（`core.screen.get_physical_screen_size`）：

1. `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` 或
   `SetProcessDpiAwareness(PER_MONITOR_DPI_AWARE)`；
2. `EnumDisplayMonitors + GetMonitorInfo` 取主屏 `rcMonitor` 宽高；
3. 回退 `GetSystemMetrics(SM_CXSCREEN / SM_CYSCREEN)`；
4. 非 Windows / 全部失败：`pyautogui.size()`。

> 本机实测物理分辨率 `1920x1200`（125% DPI，逻辑 `1536x960`）。
> 必须明确区分「归一化坐标」与「物理像素」两套语义，禁止混用。

## 4. 安全护栏

`core.guard.check_action(action) -> (ok, reason)`：

- 动作白名单：只允许键鼠物理事件
  `mouse_move / mouse_click / mouse_double_click / mouse_right_click /
   mouse_drag / scroll / key_input / hotkey / type`。
- 禁止 `command / shell / file / network / api / exec / subprocess` 等「作弊通道」
  被当成动作执行，也拦截动作对象携带的作弊字段。
- 坐标合法性：坐标字段必须是有限数值（非 NaN/Inf）；越界值由 `core.coord` 统一 clamp。
- 危险组合键拦截：`ctrl+alt+del`、`ctrl+shift+esc`、`win+l`、`alt+f4` 等。
- 危险文本拦截：`type` / `key_input` 文本命中明显 shell 命令特征时拒绝。

## 5. 动作层

- 物理原语：`move / click / double_click / right_click / drag / scroll / key / hotkey / type_text`。
  - 坐标入参一律为 0~1000 归一化坐标，执行前经 `core.coord.norm_to_physical` 换算。
- `execute(action)`：对单个动作 dict 做 `check_action` 后分发执行。
- `execute_interface(action)`：接口优先骨架，维护 `win32 / cli / http` 三类通道注册表；
  处理器未注册时返回 `status="not_implemented"`，供 router C 层回退键鼠兜底。

## 6. 与老代码的边界

- 新代码只使用标准库 + `requests / pillow / pyautogui / pynput`。
- 禁止 import 老 `controller` 任何模块。
- 不依赖 `memory.json` / `experience.json` / 经验规则引擎。

## 7. 屏幕几何自适应（分辨率 / 缩放 / 多显示器）

- **统一来源**：`core.screen`。`ensure_dpi_awareness()` 置 Per-Monitor-V2 之后，
  物理分辨率、每屏 dpi、虚拟桌面 rect、窗口 rect、截图尺寸都按物理像素处理。
- **屏幕画像**：`get_screen_profile()` 返回
  `physical_size / logical_size / dpi / scale / max_scale / monitors[] / virtual_rect /
  monitor_count / multi_monitor / dpi_awareness / fingerprint`；
  每次读取用一次廉价指纹（虚拟桌面 rect + 显示器数 + 系统 DPI）判断是否需要重新采样，
  改分辨率 / 改缩放 / 插拔显示器后在下次调用生效。
  （指纹要在 DPI 感知之后采样，否则未感知的进程读到的是逻辑尺寸，缓存会一直失效。）
- **截图覆盖范围**：`screen.all_screens = auto` → 多显示器时抓整块虚拟桌面（否则其它显示器上的
  窗口不在模型看到的画面里），单屏时与旧行为相同。
- **送入模型的帧**：`capture_frame()` 按 `target = clamp(0.6667 * 逻辑长边, 768, 2048)`
  等比采样后落 JPEG（本机 1920x1200@125% → 1024x640）。
- **模型图片采样**：`core.config.resolve_image_sample()` 把短边按
  `clamp(0.6667 * 物理短边 / 缩放, 384, 1080)` 计算（本机 640，4K@150% 为 960，小屏为 512）。
- **坐标帧（frame）**：`core.coord.set_active_frame(rect)` 由执行器每轮截图后绑定，
  `norm_to_physical` 以该 rect 为参照系换算（原点可为负），
  因此副屏在左 / 上方的布局也能点到对应显示器；未设置时退回主屏 `(0, 0, w, h)`。
- **prompt 注入**：决策 / verify prompt 里带一段 `【屏幕几何信息】`（`describe_screen()`），
  内容是物理分辨率、Windows 缩放、显示器数量、本轮截图覆盖的矩形，
  以及 0~1000 对应的物理矩形和右上角像素。
- **结果可观测**：`run_vision_task()` 返回值里有 `screen` 段
  （`physical_size / logical_size / scale / scale_percent / dpi / dpi_awareness /
  monitor_count / multi_monitor / virtual_rect / frame{rect,size,source_size,capture_scale,all_screens}`），
  每轮的 `round_details[i]["screen"]` 同样记录；启动时 stderr 打印一行屏幕画像。
- **自检**：`python -m core.screen` 打印画像、各分辨率下的帧尺寸，并实拍一帧。
