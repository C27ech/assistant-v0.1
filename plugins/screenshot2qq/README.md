# 截图发QQ

独立 Python CLI 插件：截取**所有显示器屏幕**，通过 NapCat 的 OneBot11 **正向 WebSocket** 通道，把截图以私聊图片消息发送到指定 QQ，并在发送后清理临时文件。

## 安装

无需新增依赖。本机需已安装：

- Python 3.6+
- Pillow（`PIL`）
- websocket-client（`import websocket`）

> 不引入 aiohttp / nonebot / websockets 等新依赖。

## 用法

```bash
# 使用 config.json 中的默认配置（默认发送给 user_id=<YOUR_QQ_ID>）
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

外部程序可依据退出码判断结果：

| 退出码 | 含义 |
| --- | --- |
| 0 | 发送成功 |
| 1 | 参数 / 配置错误 |
| 2 | 截图失败（没有任何可用屏幕截图） |
| 3 | WebSocket 连接失败 / 超时 |
| 4 | 发送失败（或读取截图数据失败） |
| 5 | NapCat 返回错误 |

## 配置文件

默认读取脚本同目录 `config.json`，可用 `--config` 覆盖。

```json
{
  "ws_url": "ws://127.0.0.1:3001",
  "token": "",
  "user_id": <YOUR_QQ_ID>,
  "delete_temp_files": true,
  "timeout": 10.0,
  "image_format": "PNG",
  "temp_dir": null
}
```

| 字段 | 说明 |
| --- | --- |
| `ws_url` | OneBot11 正向 WebSocket 地址 |
| `token` | access token；空字符串表示不发送鉴权头 |
| `user_id` | 私聊目标 QQ 号 |
| `delete_temp_files` | 截图发送后是否删除临时文件 |
| `timeout` | WS 连接 / 收发超时（秒） |
| `image_format` | 临时截图格式，默认 `PNG` |
| `temp_dir` | 临时文件目录；`null` 表示使用系统临时目录 |

优先级：**内置默认值 < config.json < CLI 参数**。

## NapCat 通道约定

- 正向 WebSocket：`ws://127.0.0.1:3001`。
- 连接后发送一条 OneBot11 action，然后关闭连接：

```json
{
  "action": "send_private_msg",
  "params": {
    "user_id": <YOUR_QQ_ID>,
    "message": ["[CQ:image,file=base64://...]"]
  },
  "echo": "screenshot2qq"
}
```

- OneBot11 配置为 **array 消息格式**，`message` 为数组。
- 图片段使用字符串形式：`[CQ:image,file=base64://...]`；截图先写入临时文件，再读取为 bytes 做 base64。
- 若配置了非空 `token`，以 `Authorization: Bearer <token>` 请求头发送；空 token 不发送鉴权头。

## 健壮性

- 截图：优先 `PIL.ImageGrab.grab(all_screens=True)`；若 `all_screens` 不被支持/整体失败，回退到单屏截取；单个屏幕保存失败会跳过并继续其它屏幕。
- 发送：WS 连接失败、超时、发送失败、NapCat 返回 `status != ok` 或 `retcode != 0` 都会打印明确错误并以非零退出码返回，不静默吞异常。
- 清理：无论发送成功与否，都会按 `delete_temp_files` 清理临时截图文件，避免残留。
