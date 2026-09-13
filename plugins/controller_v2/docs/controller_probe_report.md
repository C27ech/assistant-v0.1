# Controller 接口契约侦查报告

> 目标：为 `controller_v2` 完全重构提供准确的接口契约。
> 本次全程只读，未修改 `controller\` 及任何现有业务代码；唯一写入是本报告文件。

---

## 0. 侦查结论速览

1. **豆包视觉凭证可直接复用**：真实 API Key 不落在 `config.json` 明文里，而是按
   `环境变量 DOUBAO_API_KEY（及别名）→ secrets.json → config.json 旧值兜底` 三级解析。
   本机环境变量 `DOUBAO_API_KEY` 已存在（`ark-...`）。controller_v2 只要调用同一套
   `_resolve_ark_api_key()` 逻辑或读同名环境变量，即可复用同一套豆包凭证。
2. **坐标体系是 0~1000 千分比（permille）归一化坐标**，执行前统一按物理像素换算：
   `px = round(norm/1000 * physical_width)`、`py = round(norm/1000 * physical_height)`。
   物理分辨率来自 `display_scale.get_physical_screen_size()`（DPI-aware，本机实测 1920×1080）。
3. **已知 bug「规则锚点 (60,940) 实际执行成 (115,1128)」已定位**：不是随机偏移，而是
   `experience.py` 把相对锚点 `region={x:"left", y:"bottom"}` 确定性实例化为千分比 `(60,940)`，
   执行层再用物理分辨率 1920×1080 换算：`60/1000*1920=115.2→115`、`940/1000*1200=1128`。
4. **经验规则引擎的子串误命中**在 `decision/experience.py::_any_in()` 与 `_score_experience()`
   的 `str(keyword).lower() in text`；其中 `control_dialog_confirm_button` 关键词表含单字 `"是"`，
   几乎任何含「是」的中文任务都会命中。
5. **precheck/verify 为三态 AND**：`True/False/None`；post-verify 对 None 判「未验证」不判失败，
   precheck 对 None 判 fail-closed（按未通过处理，直接中止/放弃）。
6. **CLI 入口** `main.py --command "<任务>" --yes`；supervisor 以 `subprocess.run([sys.executable,
   main.py, "--command", instruction, "--yes"], cwd=controller_dir, timeout=300)` 调用，另用
   `multimodal_bridge.py` 走 `--look/--plan` 只读模式。

---

## 1. 目录结构树

控制器本体位于：`C:\Users\<你的用户名>\Desktop\controller\`

```text
controller\
├── main.py                     # CLI 主入口（--command/--serve/--yes/--dry-run/--max-attempts/--model-tier）
├── multimodal_bridge.py        # 多模态 AI 桥接入口（execute/--look/--plan，输出单行 JSON）
├── display_scale.py            # Windows DPI 感知 + 物理像素分辨率（全项目坐标唯一时钟源）
├── config.json                 # 运行时配置（vision/model_tiers/防逞强阈值/全局规则常量等）
├── experience.json             # 跨应用通用经验库（schema v2，pattern 只存锚点不存坐标）
├── memory.json                 # 应用级操作流程模板（v2，不存 x/y 坐标，槽位化）
├── requirements.txt            # Python 依赖清单
├── install.bat                 # 一键安装依赖
├── README.md                   # 项目说明
├── takeover_state.json         # decide_multimodal 写出的接管状态（人工接管/下一档位）
├── task_state.json             # 断点续跑任务状态
├── action\
│   ├── action.py               # 执行层：动作注册表 + 键鼠物理执行 + 归一化→物理像素换算
│   └── safety.py               # 动作前置安全校验（纯函数）
├── common\
│   ├── config.py               # 统一 config.json 加载（utf-8-sig、跳过 _ 注释键、不抛异常）
│   └── text_utils.py           # OCR 快照文字汇总公共函数
├── perception\
│   ├── perception.py           # 感知层统一出口（get_screen_text 契约）
│   ├── screen_capture.py       # pyautogui 截图 + Tesseract OCR，输出文字快照
│   └── vision.py               # 豆包视觉：describe/compare/verify + multimodal_decide
├── decision\
│   ├── decision.py             # 决策主引擎 decide_multimodal（memory→experience→AI，precheck/verify）
│   ├── experience.py           # 跨应用经验库：关键词匹配、打分、propose 兜底动作
│   ├── memory.py               # 应用级模板库：software+intent 匹配、槽位实例化
│   ├── server.py               # FastAPI 决策服务 + multimodal_bridge 底层 run_command/_run_single_step
│   ├── trial_loop.py           # 单步试错闭环（最多 5 次，回写 memory）
│   └── induction\              # 离线归纳：dispatch / experience_agent / memory_agent / selftest
├── docs\                       # 设计/规则/验证文档（global_rules.md 等）
├── archive\                    # 历史备份与临时补丁脚本（非运行链路）
├── backup\                     # 备份
└── __pycache__\                # 字节码缓存（非源码）
```

外部 supervisor（调用 controller 的进程）位于：
`C:\path\to\assistant_v0.1\assistant\supervisor\runtime.py`，配置在
`C:\path\to\assistant_v0.1\assistant\config\settings.py`（`DESKTOP_CONTROLLER_DIR`）。

---

## 2. 每个模块职责（一句话）

- `main.py`：命令行入口，`--command` 走「截图→decide_multimodal→执行」闭环并做连续失败兜底。
- `multimodal_bridge.py`：给外部 AI 的单行 JSON 桥接入口（execute / look / plan）。
- `display_scale.py`：把进程设为 DPI-aware，提供物理像素主屏/多屏/虚拟屏尺寸与边界。
- `config.json`：集中保存视觉/模型档位/防逞强阈值/全局等待重试常量等配置。
- `experience.json`：跨应用通用经验数据（18 条，pattern 只存相对锚点 region 与动作共性）。
- `memory.json`：应用级操作流程模板（software+intent 主键，槽位化，不存坐标）。
- `requirements.txt` / `install.bat`：依赖清单与一键安装脚本。
- `action/action.py`：动作注册表、键鼠物理执行、`execute()` 白名单入口与坐标换算。
- `action/safety.py`：执行前安全护栏（危险命令、系统组合键、坐标合法性、字段校验）。
- `common/config.py`：统一配置加载工具（utf-8-sig、容错、跳过 `_` 开头注释键）。
- `common/text_utils.py`：汇总 OCR 快照中所有文字。
- `perception/perception.py`：感知层对外统一出口，屏蔽底层实现。
- `perception/screen_capture.py`：截图 + Tesseract OCR，输出文字/词条/千分比 bbox 快照。
- `perception/vision.py`：豆包视觉请求（采样/Base64/HTTP）+ `multimodal_decide` 决策 + 预期验证。
- `decision/decision.py`：决策主引擎，编排 memory/experience/视觉决策/执行/验证与防逞强。
- `decision/experience.py`：跨应用经验的匹配、打分、兜底动作 propose 与半自动归纳。
- `decision/memory.py`：应用级模板的检索、槽位填充、计划实例化与回写。
- `decision/server.py`：FastAPI 服务（`/command`、`/decide-step`、`/plan` 等）与动作 schema 适配。
- `decision/trial_loop.py`：单步动作试错闭环（≤5 次，成功回写 memory）。
- `decision/induction/*`：离线归纳候选生成、审核队列与自检。
- `docs/*`：设计文档与规则（global_rules.md 是 prompt 硬约束来源）。

---

## 3. a) 豆包视觉模型接入（多模态视觉决策）

### 3.1 调用链路
`decision.decide_multimodal()` → `perception.vision.multimodal_decide()` →
`_sample_image()`（短边 640、JPEG 80）→ `_image_content()`（data URI Base64）→
`_post_multimodal_messages()`（HTTP POST）→ `_extract_json_object()` 解析 →
`_normalize_multimodal_result()` 规整。

### 3.2 API endpoint / 模型名
- **endpoint**：`{vision_base_url}/chat/completions`
  - `vision_base_url` 默认 `https://ark.cn-beijing.volces.com/api/v3`
  - 位置：`config.json:14`；`perception/vision.py:64`
  - 拼接位置：`perception/vision.py:592`（`_post_vision_messages`）、`perception/vision.py:1059`（`_post_multimodal_messages`）
- **模型名（pro/turbo/mini）**：
  - 决策入口 `multimodal_decide(model_tier="turbo")`，最终模型名从 `config.json` 顶层 `model_tiers` 读取（`vision.py:981-1016`）。
  - `config.json:61-65`：
    - `mini`  → `doubao-seed-2-0-mini-260428`
    - `turbo` → `doubao-seed-2-1-turbo-260628`
    - `pro`   → `doubao-seed-2-1-pro-260628`
  - 档位别名归一化：`vision.py:68`（`min→mini, t/fast→turbo, p/precise→pro`）。
  - 决策主链默认档位：`decision.py:571` `_DEFAULT_START_TIER = "mini"`，失败逐级升 `mini→turbo→pro`（`decision.py:569`）。
  - `multimodal_decide` 自身默认 `model_tier="turbo"`（`vision.py:1207`）；但 `decide_multimodal` 显式传 `model_tier=tier`。
  - 图片采样固定按 `tier="mini"`（短边 640）执行：`vision.py:1260`（与 `config.json` `tiers.mini.short_side=640` 一致）。
- **请求格式**（OpenAI ChatCompletions 兼容，非流式）：
  ```json
  POST {base_url}/chat/completions
  Authorization: Bearer <api_key>
  Content-Type: application/json
  {
    "model": "doubao-seed-2-1-turbo-260628",
    "messages": [
      {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
        {"type": "text", "text": "<prompt>"}
      ]}
    ]
  }
  ```
  - 代码位置：`vision.py:1059-1067`（多模态）、`vision.py:592-600`（普通视觉）。
- **响应格式**：OpenAI 兼容 `data["choices"][0]["message"]["content"]`（`vision.py:1112`），
  内部再解析为决策 JSON；增强信号（`finish_reason`/`reasoning_content`/`usage`）在
  `vision.py:514-542`、`vision.py:1108-1110` 提取。
- **超时**：连接超时 `vision.connect_timeout`（默认 15s，`vision.py:72`）；读取超时
  `vision.request_timeout`（默认 180s，`vision.py:71`），以 `(connect, read)` 元组传给 requests（`vision.py:1071-1076`）。

### 3.3 API key 来源（可直接复用）
统一入口 `perception/vision.py:193-229` `_resolve_ark_api_key(cfg, vision)`，三级顺序：
1. **环境变量**（`vision.py:200-204`）：
   - 首选 `DOUBAO_API_KEY`
   - 别名 `ARK_VISION_API_KEY` / `VISION_API_KEY` / `ARK_API_KEY`
   - 校验函数 `_looks_like_real_ark_key`（`ark-` 前缀且长度 ≥20，`vision.py:176-179`）。
2. **独立 secrets 文件**（`vision.py:206-221`）：
   - 默认项目根 `secrets.json`，字段 `vision_api_key` / `doubao_api_key` / `ark_api_key`
   - 可用环境变量 `CONTROLLER_SECRETS_FILE` 覆盖路径。
3. **config.json 旧明文兜底**（`vision.py:223-228`）：
   - 仅当 `vision.vision_api_key` 形如真实 ark key 时采用（`config.json:13` 当前为 `""`）。

> 结论：controller_v2 复用豆包凭证 = 直接读环境变量 `DOUBAO_API_KEY`（或复用
> `_resolve_ark_api_key` 的三级解析），**无需**动 `controller\config.json`。
> 本机实测 `DOUBAO_API_KEY` 已存在且以 `ark-` 开头。

### 3.4 multimodal_decide 输出契约（`vision.py:1204-1309`）
```json
{"status": "确定|不确定",
 "expectation": "可证伪预期" | null,
 "candidates": [{"name": "...", "x": 0..1000, "y": 0..1000, "confidence": 0..1}],
 "reason": "..."}
```
- 坐标 `x/y` 是 0~1000 归一化整数（整张图片，左上 (0,0)、右下 (1000,1000)），prompt 明确要求（`vision.py:903`）。
- `status != 确定` 或 `candidates` 无有效坐标时强制降级为「不确定」且 `candidates=[]`（`vision.py:1160-1186`）。

---

## 4. b) 坐标换算逻辑（截图坐标 ↔ 屏幕真实坐标）

### 4.1 全项目坐标体系
- 统一使用 **Windows 物理像素坐标**；唯一时钟源 `display_scale.py`。
- `ensure_dpi_awareness()`（`display_scale.py:217-256`）把进程设为 Per-Monitor v2 / System DPI-aware，
  使 `pyautogui`、`GetWindowRect`、`GetSystemMetrics` 返回物理像素，避免高 DPI 逻辑/物理混淆。
- 屏幕尺寸来源：`display_scale.get_physical_screen_size()`（`display_scale.py:274-306`），
  优先 `EnumDisplayMonitors+GetMonitorInfo` 找主屏，回退 `GetSystemMetrics`。
- **实测本机**：DPI-aware 后 `get_physical_screen_size() = (1920, 1200)`；
  非 aware 逻辑分辨率为 1536×864，即 **125% DPI 缩放**（1920/1536 = 1200/960 = 1.25）。

### 4.2 换算公式
决策/经验/视觉层一律产出 **0~1000 千分比归一化坐标**，执行前换算为物理像素：

```text
px = round(norm_x / 1000 * physical_width)
py = round(norm_y / 1000 * physical_height)
```

实现位置（三处同公式，保持一致）：
- `decision/decision.py:1057-1076` `_normalized_to_pixel()`（AI 路径、knowledge 路径使用；再 clamp 到 `[0, w-1]/[0, h-1]`）。
- `decision/server.py:322-338` `_permille_to_pixel()`（服务适配层，只 round 不 clamp，交给执行层）。
- `action/action.py:1894-1923` `_normalized_to_physical()`（执行层白名单 `execute()` 入口）。

执行层 `get_screen_size()`（`action/action.py:461-476`）直接返回 `display_scale.get_physical_screen_size()`，
不再回退 `pyautogui.size()`；`server.py:286-319` 仍保留 pyautogui 兜底（仅 Windows API 不可用时）。

### 4.3 截图坐标 → 归一化（OCR bbox 方向）
`perception/screen_capture.py:321-328` 把 OCR 词条的像素 bbox 转成 0~1000 千分比：
`x = left/width*1000`、`y = top/height*1000`（`_clamp_permille` 钳到 [0,1000]）。
该 `width/height` 来自 `pyautogui.screenshot()` 图像本身尺寸（即 DPI-aware 后的物理尺寸）。

### 4.4 已知 bug「规则锚点 (60,940) → 实际 (115,1128)」定位

**完整代码链**：
1. 经验锚点表把相对区域实例化为千分比：
   - `decision/experience.py:46-47`：
     ```python
     _ANCHOR_X = {"left": 60, "center": 500, "right": 960}
     _ANCHOR_Y = {"top": 30, "center": 500, "bottom": 940}
     ```
   - `experience.py:335-342` `_anchor_region_to_coords()` 读取 `anchor.region` 返回 `(x, y)`；
     例如 `region={x:"left", y:"bottom"}` → `(60, 940)`。
   - `experience.py:561` `_materialize_pattern_action()` 用该千分比坐标生成兜底动作。
2. 决策层同样的锚点表（与 experience 保持一致）：
   - `decision/decision.py:2530-2531` `_ANCHOR_X_NORM/_ANCHOR_Y_NORM`；
     `decision.py:2534-2548` `_anchor_region_to_norm()`。
3. 知识动作适配时把千分比换算成物理像素：
   - `decision.py:2738-2746` `_norm_to_pixel()` → `decision.py:1057-1076` `_normalized_to_pixel()`。
4. 换算结果（本机物理分辨率 1920×1080）：
   - `px = round(60/1000*1920) = round(115.2) = 115`
   - `py = round(940/1000*1200) = round(1128.0) = 1128`
   - 即规则锚点 `(60, 940)` 是**千分比**，执行层按物理分辨率放大成 **物理像素 (115, 1128)**。

**结论**：`(60,940) → (115,1128)` 的「偏移」不是 DPI 计算错误，而是**坐标约定不一致**——
经验/记忆的锚点存的是 0~1000 千分比坐标（`left=60`、`bottom=940`），而执行层把它按
**物理分辨率 1920×1080** 放大为物理像素。若误把 `(60,940)` 当绝对像素，就会看到「错位」。
触发该现象的典型规则是 `exp_control_taskbar_start_button`（`experience.json` 中 `region={x:"left",y:"bottom"}`）。

---

## 5. c) 经验规则引擎（experience.py）

### 5.1 关键词匹配 / 打分 / propose
- 文本收集：`collect_text()`（`experience.py:254-271`）拼接 instruction/software/interface/inventory/OCR 文本。
- **子串误命中实现位置**：`experience.py:274-278` `_any_in()`：
  ```python
  def _any_in(text, keywords):
      for keyword in keywords:
          if keyword and str(keyword).lower() in text:   # ← 纯子串 in，无词边界
              return True
      return False
  ```
  同样逻辑在打分处再次出现：`experience.py:802`：
  ```python
  hits = [kw for kw in keywords if kw and str(kw).lower() in text]
  ```
- 关键词表：`experience.py:116-168` `_RULE_TYPE_KEYWORDS`。
  **误命中高风险点**：`control_dialog_confirm_button` 关键词表（`experience.py:162-164`）含单字
  `"是"`；由于是子串匹配，几乎任何含「是」的中文任务/OCR 文本都会命中该规则。
- 匹配流程 `ExperienceStore.match()`（`experience.py:1183-1221`）：
  - 只匹配 `status == active` 的经验；
  - `_score_experience()`（`experience.py:792-820`）打分：
    - 基础分 `0.6 + min(0.2, 0.05*len(hits))`（`experience.py:806`）；
    - 共性描述 token 命中加分 `min(0.2, 0.04*len(common_hits))`（`experience.py:811-815`）；
    - 再加 `confidence*0.1 + min(success_count,10)*0.01`（`experience.py:817-819`）；
    - 无关键词命中返回 `0.0`（`experience.py:803-804`）。
  - 结果按 `(score, confidence, success_count)` 降序（`experience.py:1213-1220`）。
- **propose 逻辑** `propose_experience_fallback()`（`experience.py:696-737`）：
  - 把所有命中经验的 `_pattern_action_candidates()` 展平成候选列表；
  - 按 `attempt_no` 轮换（`start = (attempt_no-1) % len(candidates)`）；
  - 跳过与 `attempted_actions` 完全重复的动作（`_action_already_attempted`，`experience.py:688-693`）；
  - 命中动作带 `source="experience_fallback"` 与 `experience_id` 返回；
  - 全部重复时仍返回轮换到的那条。
- 动作实例化 `_materialize_pattern_action()`（`experience.py:551-607`）：坐标只能由 `anchor.region`
  确定性实例化（`_anchor_region_to_coords`），pattern 本身不存具体坐标。

---

## 6. d) precheck / verify（decision.py 三态 AND + fail-closed）

### 6.1 三态语义
`fulfilled` 取值 `True`（明确兑现）/ `False`（明确反证）/ `None`（证据不足/无法判定）。

### 6.2 post-verify 三态 AND（`decision.py:2158-2204`，函数 `_verify_expectation_fulfilled`）
```python
all_checks = ocr_anchor_results + visual_anchor_results
explicit_true  = [c for c in all_checks if c.get("fulfilled") is True]
explicit_false = [c for c in all_checks if c.get("fulfilled") is False]
undecided      = [c for c in all_checks if c.get("fulfilled") is None]

if explicit_false:
    fulfilled = False                                   # 任一 False → 失败
elif explicit_true and len(explicit_true) == len(all_checks):
    fulfilled = True                                    # 全部 True → 成功
else:
    fulfilled = None                                    # 有 None → 未验证（不判失败）
```
- `decision.py:2164-2182`；通道级合并 `_channel_fulfilled`（`decision.py:2184-2191`）。

### 6.3 precheck 三态 AND + fail-closed（`decision.py:2207-2348`，函数 `_verify_precheck_fulfilled`）
- 合并逻辑同 post-verify，但**对 None 采取 fail-closed**：
  ```python
  else:
      fulfilled = None
      reason = "precheck 证据不足：... 按未通过处理（fail-closed）"   # decision.py:2324-2327
  ```
- 返回值 `passed = (fulfilled is True)`（`decision.py:2342`）。
- 任一 `False` → `passed=False`（`decision.py:2309-2319`）；存在 `None` → `passed=False`（fail-closed）。
- precheck 为空 → `skipped=True / passed=True`（向后兼容，`decision.py:2227-2241`）。

### 6.4 「fail-closed 放弃」的实现位置
- 统一执行器 `_run_precheck()`（`decision.py:2351-2363`）→ `_verify_precheck_fulfilled()`。
- AI 路径：`decide_multimodal` 内 `_run_ai_precheck()`（`decision.py:4356-4393`）→ 未通过则
  `_finalize_failure(...)` 放弃执行（`decision.py:4644-4656`）。
- memory 路径：`_run_memory_path` 内 precheck 未通过 → `_finalize_failure(..., "放弃", ...)`（`decision.py:3667-3694`）。
- experience 路径：`_run_experience_path` 内 precheck 未通过 → 放弃（`decision.py:3960-3980`）。
- `_precheck_escalate_to_user()`（`decision.py:3569-3597`）当前为 **stub，恒返回 None**，
  `precheck_escalation.enabled` 不生效，仍直接中止。

---

## 7. e) CLI 入口 main.py 与 supervisor 调用方式

### 7.1 main.py 命令行参数（`main.py:188-240`）
互斥模式组（必选其一）：
- `--command <指令>`：单次闭环执行（截图→decide_multimodal→执行）。
- `--serve`：仅打印 FastAPI 服务启动说明，不真正启动。

可选参数：
- `--config <路径>`：指定 config.json（默认与 main.py 同目录）。
- `--yes`：跳过执行前交互确认（仅 `--command` 生效）。
- `--dry-run`：只截图 + 多模态决策预览，不执行键鼠。
- `--max-attempts N`：同任务连续失败重试上限（默认读 `multimodal_max_attempts`，再缺省 3）。
- `--model-tier {mini|turbo|pro}`：显式模型档位（支持别名 min/t/pro）。

入口：`main.py:243-270` `main(argv)` → `run_multimodal_task()`（`main.py:444-623`）；
`if __name__ == "__main__": sys.exit(main())`（`main.py:626-627`）。

### 7.2 supervisor 如何以 subprocess 调用
位置：`C:\path\to\assistant_v0.1\assistant\supervisor\runtime.py`。

1. **执行命令（真正键鼠执行）** `_run_desktop_command()`（`runtime.py:143-174`）：
   ```python
   subprocess.run(
       [sys.executable, str(main_py), "--command", instruction, "--yes"],
       cwd=str(controller_dir),
       capture_output=True, text=True, encoding="utf-8",
       errors="replace", timeout=300,
       env={**os.environ, "PYTHONUTF8": "1"},
   )
   ```
   - `main_py = controller_dir/main.py`；`controller_dir` 来自 `settings.desktop_controller_dir`。
   - 返回码非 0 → 取 stderr/stdout 末 1200 字符作为失败摘要；成功 → stdout 末 2000 字符。

2. **多模态桥接（look/plan 只读）** `_run_multimodal_task()`（`runtime.py:176-211`）：
   ```python
   subprocess.run(
       [sys.executable, str(bridge), instruction, *flag],   # bridge=multimodal_bridge.py
       cwd=str(controller_dir), ..., timeout=300, env={..., "PYTHONUTF8": "1"},
   )
   ```
   - `mode=="look"` → `--look`；`mode=="plan"` → `--plan`；`execute` → 无 flag。
   - `multimodal_bridge.py` 内部 `execute` 调用 `decision.server.run_command`，look/plan 调用
     `_run_single_step`（`multimodal_bridge.py:40-52`）。

3. **工具分发**：`Runtime._executor()`（`runtime.py:80-131`）把助手 AI 的工具
   `desktop_command` / `multimodal_task` 映射到上述两个函数。

4. **配置来源**：`Assistant/config/settings.py:129`
   `desktop_controller_dir = _env("DESKTOP_CONTROLLER_DIR","") or str(Path.home()/ "Desktop"/"controller")`；
   即 `.env` 的 `DESKTOP_CONTROLLER_DIR`，缺省回退 `~/Desktop/controller`。

---

## 8. 豆包模型凭证获取方式（总结）

| 优先级 | 来源 | 字段/变量 | 代码位置 |
|---|---|---|---|
| 1 | 环境变量 | `DOUBAO_API_KEY`；别名 `ARK_VISION_API_KEY`/`VISION_API_KEY`/`ARK_API_KEY` | `vision.py:200-204` |
| 2 | `secrets.json`（路径可用 `CONTROLLER_SECRETS_FILE` 覆盖） | `vision_api_key`/`doubao_api_key`/`ark_api_key` | `vision.py:206-221` |
| 3 | `config.json` `vision.vision_api_key`（仅当形如真实 ark key） | `vision_api_key` | `vision.py:223-228` |

Key 校验：`ark-` 前缀且长度 ≥ 20（`vision.py:176-179`）。缺 Key 时一次性 stderr 提示并返回
`error="missing_vision_api_key"`（`vision.py:585-590`、`vision.py:1052-1057`）。

**controller_v2 复用建议**：新代码直接读 `DOUBAO_API_KEY`（本机已配置），并复用
`model_tiers` 的 mini/turbo/pro 模型名与 `vision_base_url`，即可与现网凭证无缝切换。

---

## 9. 坐标换算公式（总结）

```text
归一化层（视觉/经验/记忆/OCR bbox 均用 0~1000 千分比）:
    norm_x ∈ [0,1000],  norm_y ∈ [0,1000]

物理像素换算（DPI-aware 物理分辨率）:
    px = round(norm_x / 1000 * physical_width)
    py = round(norm_y / 1000 * physical_height)

像素边界钳制:
    px = clamp(px, 0, physical_width - 1)
    py = clamp(py, 0, physical_height - 1)

物理分辨率来源:
    display_scale.get_physical_screen_size()   # 本机实测 (1920, 1200)，125% DPI
```

反向（像素→千分比，OCR bbox）：`norm = pixel / axis_size * 1000`，钳 [0,1000]（`screen_capture.py:321-328`）。

---

## 10. 读取的文件路径清单（本次侦查）

以下为本次实际读取/分析的文件（全部只读）：

- `C:\Users\<你的用户名>\Desktop\controller\config.json`
- `C:\Users\<你的用户名>\Desktop\controller\main.py`
- `C:\Users\<你的用户名>\Desktop\controller\multimodal_bridge.py`
- `C:\Users\<你的用户名>\Desktop\controller\display_scale.py`
- `C:\Users\<你的用户名>\Desktop\controller\requirements.txt`
- `C:\Users\<你的用户名>\Desktop\controller\install.bat`
- `C:\Users\<你的用户名>\Desktop\controller\experience.json`（顶层结构/条目抽样）
- `C:\Users\<你的用户名>\Desktop\controller\common\config.py`
- `C:\Users\<你的用户名>\Desktop\controller\common\text_utils.py`
- `C:\Users\<你的用户名>\Desktop\controller\perception\perception.py`
- `C:\Users\<你的用户名>\Desktop\controller\perception\screen_capture.py`
- `C:\Users\<你的用户名>\Desktop\controller\perception\vision.py`（全文 1494 行）
- `C:\Users\<你的用户名>\Desktop\controller\decision\decision.py`（关键段落：防逞强/坐标/锚点/AND/precheck/decide_multimodal）
- `C:\Users\<你的用户名>\Desktop\controller\decision\experience.py`（关键段落：关键词/打分/匹配/propose/锚点）
- `C:\Users\<你的用户名>\Desktop\controller\decision\memory.py`（头部职责说明）
- `C:\Users\<你的用户名>\Desktop\controller\decision\server.py`（run_command/_run_single_step/坐标适配/路由）
- `C:\Users\<你的用户名>\Desktop\controller\decision\trial_loop.py`（头部职责说明）
- `C:\Users\<你的用户名>\Desktop\controller\action\action.py`（坐标换算/execute 白名单/屏幕尺寸）
- `C:\Users\<你的用户名>\Desktop\controller\action\safety.py`（头部职责说明）
- `C:\path\to\assistant_v0.1\assistant\supervisor\runtime.py`（subprocess 调用点，全文）
- `C:\path\to\assistant_v0.1\assistant\config\settings.py`（desktop_controller_dir 配置段）
- `C:\Users\<你的用户名>\Desktop\迁移说明.md`（迁移说明，确认目录关系）

（目录遍历还枚举了 `controller`、`controller_v2`、`Assistant` 下所有相关文件以定位，
未对系统内任何业务文件做修改。）

## 11. 创建的文件/文件夹路径清单

- 创建目录：`C:\path\to\assistant_v0.1/plugins/controller_v2\docs\`
- 创建文件：`C:\path\to\assistant_v0.1/plugins/controller_v2\docs\controller_probe_report.md`（本报告）
