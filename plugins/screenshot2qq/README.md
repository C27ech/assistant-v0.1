# 截图发QQ

独立 Python CLI 插件：截图（默认**所有显示器**，也可以只截**某个窗口 / 某台显示器 / 一块矩形区域**），
通过 NapCat 的 OneBot11 **正向 WebSocket** 通道，把截图以私聊图片消息发送到指定 QQ，并在发送后清理临时文件。
还支持只查询不截图：`--list-windows`（当前有哪些窗口）/ `--list-monitors`（有几台显示器）。

## 安装

无需新增依赖（窗口截图用标准库 `ctypes` 调 Win32 API，**不需要 pywin32 / mss**）。本机需已安装：

- Python 3.6+
- Pillow（`PIL`）
- websocket-client（`import websocket`）

目录里两个文件要放在一起：`main.py`（CLI + 发消息）和 `win_capture.py`（窗口/显示器枚举与截图）。

> 不引入 aiohttp / nonebot / websockets 等新依赖。

## 用法

```bash
# 使用 config.json 中的默认配置（把 config.json 里的 user_id 改成你自己的 QQ 号），截所有显示器
python main.py

# 覆盖目标 QQ 号
python main.py --user-id 123456789

# 覆盖 WebSocket 地址
python main.py --ws-url ws://127.0.0.1:3001

# 覆盖 token（默认空，空 token 不发送鉴权头）
python main.py --token "your-token"

# 指定其它配置文件
python main.py --config /path/to/config.json

# 保留临时截图文件（默认发送后删除）
python main.py --keep-temp
```

### 指定截图目标

```bash
# 1) 当前前台窗口（用户正在看的那个）
python main.py --target active

# 2) 指定窗口：标题片段（不区分大小写；找不到会列出候选窗口）
python main.py --target window --window "记事本"
python main.py --target window --window "chrome"            # 也能按进程名匹配
python main.py --target window --window "^.*- Notepad$" --window-regex
python main.py --target window --hwnd 0x00340CCA            # 精确指定某一个窗口

# 3) 只截窗口内容区（去掉标题栏/边框）
python main.py --target window --window "微信" --client-area

# 4) 某台显示器（序号见 --list-monitors；all = 每台各截一张）
python main.py --target screen --monitor 1
python main.py --target screen --monitor all

# 5) 屏幕上一块矩形：x,y,w,h（左上角 + 宽高，物理像素）
python main.py --target region --region 100,100,800,600

# 6) 图片压小一点再发（缩放 / 限宽 / 换 JPEG）
python main.py --target active --scale 0.5
python main.py --target active --max-width 1600 --format JPEG --quality 70

# 7) 只截图不发 QQ，并另存一份到指定目录（文件会保留）
python main.py --target window --window "微信" --no-send --out "C:\Users\<你的用户名>\Desktop"
python main.py --target active --out "C:\temp\shot.png"

# 8) 截图前先把窗口切到前台（默认不动用户桌面）
python main.py --target window --window "记事本" --activate --delay 0.5

# 9) 只查询：
python main.py --list-windows
python main.py --list-windows --filter 微信 --include-minimized
python main.py --list-monitors
```

结果打印在 **stdout**（给人/给AI 看的摘要，加 `--json` 变成 JSON）；过程日志在 **stderr**。

主要参数一览：

| 参数 | 说明 |
| --- | --- |
| `--target all\|screen\|active\|window\|region` | 截哪里，默认 `all`（全部显示器） |
| `--window TEXT` / `--hwnd N` | 窗口标题片段（可加 `--window-regex`）或窗口句柄 |
| `--monitor N` 或 `all` | 显示器序号（1 起）；`target=screen` 时用 |
| `--region x,y,w,h` | 屏幕矩形区域；单独给 `--region` 也会按区域截 |
| `--client-area` | 只截窗口客户区（去掉标题栏/边框） |
| `--activate` | 截图前把窗口切到前台（会动用户桌面） |
| `--no-printwindow` | 不走 PrintWindow，直接按屏幕区域截窗口（调试用） |
| `--delay 秒` | 截图前先等一会儿 |
| `--scale 倍数` / `--max-width N` / `--max-height N` | 缩放后再发（只缩不放；显式给定则跳过 auto_fit） |
| `--format PNG\|JPEG\|WEBP\|BMP` / `--quality N` | 覆盖图片格式与质量 |
| `--out 路径` | 另存一份到该文件/目录（保留，不随临时文件删除） |
| `--auto-fit` / `--no-auto-fit` | 开/关「超大图自动缩到可发送尺寸」（默认按 config.json 的 `auto_fit`） |
| `--no-send` | 只截图/落盘，不连 NapCat |
| `--json` | stdout 用 JSON 输出（含 `screen` 分辨率/缩放段） |
| `--list-windows` / `--list-monitors` | 只列出窗口 / 显示器（含每屏分辨率与缩放），不截图 |
| `--filter TEXT` / `--filter-regex` / `--include-minimized` / `--all-windows` | `--list-windows` 的过滤选项 |

外部程序可依据退出码判断结果：

| 退出码 | 含义 |
| --- | --- |
| 0 | 发送成功 |
| 1 | 参数 / 配置错误 |
| 2 | 截图失败（没有任何可用屏幕截图） |
| 3 | WebSocket 连接失败 / 超时 |
| 4 | 发送失败（或读取截图数据失败） |
| 5 | NapCat 返回错误 |
| 6 | 截图目标无效（窗口找不到 / 显示器序号越界 / 区域越界） |
| 7 | 当前系统不支持该目标（非 Windows 的窗口截图） |

## 配置文件

默认读取脚本同目录 `config.json`，可用 `--config` 覆盖。

```json
{
  "ws_url": "ws://127.0.0.1:3001",
  "token": "",
  "user_id": 0,
  "delete_temp_files": true,
  "timeout": 10.0,
  "image_format": "PNG",
  "temp_dir": null,
  "jpeg_quality": 85,
  "auto_fit": {
    "enabled": true,
    "max_side": 2560,
    "max_pixels": 8000000
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `ws_url` | OneBot11 正向 WebSocket 地址 |
| `token` | access token；空字符串表示不发送鉴权头 |
| `user_id` | 私聊目标 QQ 号（**要发图必须填**；为 `0` 时绝不会发送——正式发送会直接报错退出，`--no-send` 只截图不受影响） |
| `delete_temp_files` | 截图发送后是否删除临时文件 |
| `timeout` | WS 连接 / 收发超时（秒） |
| `image_format` | 临时截图格式，默认 `PNG` |
| `temp_dir` | 临时文件目录；`null` 表示使用系统临时目录 |
| `jpeg_quality` | JPEG/WEBP 质量 1~100，默认 `85`（`--quality` 可临时覆盖） |
| `auto_fit.enabled` | 图超过限制时自动缩小（默认 `true`；`--no-auto-fit` 可关） |
| `auto_fit.max_side` | 最长边上限，默认 `2560` |
| `auto_fit.max_pixels` | 总像素上限，默认 `8000000`（8MP） |

优先级：**内置默认值 < config.json < CLI 参数**。

## 分辨率 / 缩放

- **DPI 感知**：进程启动时置 Per-Monitor-V2，失败则退回 `shcore` / `SetProcessDPIAware`。
  窗口矩形、`--region`、虚拟桌面坐标和截图尺寸都按物理像素处理；在 125% / 150% 缩放下，
  截出来的仍是 1920x1200 这样的实际分辨率。
- **屏幕画像**：`wc.screen_profile()` 返回物理分辨率、Windows 缩放百分比、dpi、逻辑分辨率、
  每台显示器的 dpi/scale、虚拟桌面 rect。`--list-monitors` 头部、stderr 日志和 `--json` 的
  `screen` 字段都会打印它；分辨率 / 缩放 / 显示器数量变化后，下次调用重新采样。
- **auto_fit**：最长边超过 `max_side` 或总像素超过 `max_pixels` 时，等比缩小到限制内（只缩不放）。
  默认限制下 1920x1200（长边 1920 / 2.3MP）不会触发；3840x2160 会缩到 2560x1440。
  传了 `--scale/--max-width/--max-height` 时按传入值处理，跳过 auto_fit；`--no-auto-fit` 关闭该功能。
- 触发 auto_fit 时，文本摘要里写 `超大图自适应缩放 3840x2160 → 2560x1440（auto_fit）`，
  `--json` 里是 `shots[].auto_fit` 字段。

## NapCat 通道约定

- 正向 WebSocket：`ws://127.0.0.1:3001`。
- 连接后发送一条 OneBot11 action，然后关闭连接：

```json
{
  "action": "send_private_msg",
  "params": {
    "user_id": 123456789,
    "message": ["[CQ:image,file=base64://...]"]
  },
  "echo": "screenshot2qq"
}
```

- OneBot11 配置为 **array 消息格式**，`message` 为数组。
- 图片段使用字符串形式：`[CQ:image,file=base64://...]`；截图先写入临时文件，再读取为 bytes 做 base64。
- 若配置了非空 `token`，以 `Authorization: Bearer <token>` 请求头发送；空 token 不发送鉴权头。

## 健壮性

- 截图（全部显示器）：优先 `PIL.ImageGrab.grab(all_screens=True)`；若 `all_screens` 不被支持/整体失败，回退到单屏截取；单张保存失败会跳过并继续其它张。
- 截图（窗口）：优先 `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)`，窗口被别的窗口挡住也能截到，不需要先切前台；若返回失败或只拿到单色空白图（部分程序用 GPU 渲染时会出现），自动退回「按窗口矩形截屏幕」，此时截到的是屏幕上的实际内容（被遮挡的部分就是遮挡物），结果里会写明用的是哪种方式。
- 窗口最小化时截不到内容，会报错并提示「先还原窗口或改用 --target all」。
- 坐标与 DPI：窗口矩形、`--region`、多屏虚拟桌面坐标统一按物理像素，多显示器缩放不同时也按物理像素对齐；DPI 感知见上文「分辨率 / 缩放」。
- 裁剪：窗口截图默认按 DWM 可见边框（`DWMWA_EXTENDED_FRAME_BOUNDS`）裁掉不可见的调整边框；`--client-area` 再裁到客户区（去掉标题栏/边框）。
- 过滤：窗口列表默认跳过不可见窗口、无标题窗口、工具窗口和被 DWM 隐藏的“幽灵”窗口（UWP 后台窗口），可用 `--all-windows` / `--include-minimized` 放开。
- 区域越界：`--region` 超出屏幕时会自动裁剪到屏幕内并提示；完全在屏幕外则报错（退出码 6）。
- 发送：WS 连接失败、超时、发送失败、NapCat 返回 `status != ok` 或 `retcode != 0` 都会打印错误并以非零退出码退出。
- 清理：发送成功与否都会按 `delete_temp_files` 清理临时截图文件；`--out` 指定的文件会保留（留档或给其它程序用）。
- 跨平台：本插件依赖 Pillow 的 `ImageGrab` 与 Win32 窗口 API，只能在 Windows 上使用；非 Windows 上 `import win_capture` 不会报错，但截图 / 窗口功能会以退出码 7 报「不支持」。
