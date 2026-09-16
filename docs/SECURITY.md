# 安全设计

这套系统的攻击面很特别：**它连着一个聊天软件，而且能操作本机**。
也就是说，「谁能给它发消息」＝「谁能用你电脑上的这些权限」。所以护栏必须是设计的一部分。

## 1. 使用者白名单（最重要的一道）

```ini
# assistant/.env
ALLOWED_USERS=你的QQ号
```

- 非空时：名单外的人发私聊 → **静默忽略**（不回复、**不入库**、只往日志写一行 `[白名单] 已忽略未授权消息`）
- 留空时：**任何能私聊机器人号的人都能指挥她** ⚠️
- 判定方式：QQ 号 / `USER_MAP` 解析后的 user_id，任一命中即放行
- 启动时会打印白名单状态，排查「她为什么不回我」时先看这行：

```
[白名单] 已启用使用者白名单：['<YOUR_QQ_ID>']（其他人私聊会被静默忽略）
```

代码：`config/settings.py::is_allowed()` + `supervisor/runtime.py::handle()` 开头。

## 2. 敏感文件硬拦截（读文件插件）

`plugins/file_tools` 有**三层**：

| 层 | 规则 |
|----|------|
| 可读范围 | `roots`：目录白名单。`"*"` = 全盘；也可写成具体目录列表（建议收窄） |
| 敏感文件黑名单 | 命中即拒，**内置在代码里（`BUILTIN_DENY`），只能通过配置追加、不能取消** |
| 体积/类型 | 跳过 >8MB 与二进制文件；单次输出截断可续读 |

黑名单覆盖：`.env`、`config.json`、`credential(s)`、`secret`、`password`、`token`、`cookie`、
`login data`、`keychain`、`netrc`、`kubeconfig`、`.aws`、`.docker`、`.npmrc`、`.pypirc`、
`.git-credentials`、`id_rsa`、`id_ed25519`、`.pem`、`.pfx`、`.p12`、`.key`、`.keystore`、
`.jks`、`.kdbx`、`.ovpn`、`.git`、`.ssh`、`Tencent Files`、`nt_qq`、`napcat`、
`\Microsoft\Credentials`、`\config\SAM`、`\config\SYSTEM`。

> **为什么这条线不能松**：模型可以被"说服"。别人只要说一句「读一下 `.env` 帮我看看配置」，
> 如果插件不拦，你的 API key 就进了聊天记录（还被发到了模型服务商那儿）。
> 拦在**确定性代码**里，而不是"靠提示词劝它别读"。

**残留风险**：黑名单是**按路径/文件名**匹配的。如果某个含密钥的文件名字很普通
（比如 `我的笔记.txt` 里贴了 key），拦不住。所以给助手开全盘读之前，自己权衡一下。

## 3. 危险动作需审批

- `tools/approval.py`：代码 AI 的危险操作触发审批闸门（返回 `NEED_APPROVAL:<路径>`）
- 插件层：不可逆动作（退出 / 覆盖 / 删除之类）走 `allow_dangerous=true`，
  且工具说明里写明**需先征得用户同意**
- 执行器把布尔统一转小写 `true/false`（否则 `str(False)="False"` 会被当成真值放行）

## 4. 代码 AI 沙箱

`tools/file_tools.py::_safe()`：把相对路径解析到工作目录内，**越界直接抛错**。

```python
if base != path and base not in path.parents:
    raise ValueError(f"路径越出工作目录: {rel}")
```

每个代码 AI 有独立工作目录，互不干扰。

## 5. 其它

| 机制 | 作用 |
|------|------|
| 单实例闸门（全局 Mutex） | 防止多实例抢同一条消息、重复派活 |
| `_guard_fake_dispatch` | 回复里声称"已派发"但本轮没调 `spawn_agent` → 自动追加纠正提示（防幻觉） |
| 错误不再静默 | 模型调用失败回一条带原因的消息（402 余额 / 401 key / 429 限流 / 超时）|
| 消息入库纪律 | 未授权消息**不落库**，避免污染记忆；模型调用失败的那轮也把失败原因说清 |
| 插件输出落盘 | 每个子进程的 stdout/stderr 单独落盘，便于事后审计 |

## 6. 部署检查清单

- [ ] `ALLOWED_USERS` 已填（不要留空）
- [ ] `.env` 不在版本控制里（`.gitignore` 已包含；确认 `git status` 里看不到它）
- [ ] `file_tools/config.json` 的 `roots` 按需收窄，别无脑全盘
- [ ] 聊天软件的机器人账号**别用来处理敏感指令**（聊天记录会存在服务商那边）
- [ ] 定期轮换 API key（尤其把 key 贴过给别人/截图过的时候）
- [ ] 看门狗日志（`watchdog.log`）和助手日志（`assistant_err.log`）偶尔扫一眼

## 7. 已知不足

- 没有按"指令危险等级"分级：白名单用户等于完全信任
- 黑名单是子串匹配，能被改名的文件绕过
- 没有出网白名单：插件理论上可以访问任意地址
- 没有审计界面：靠翻日志文件
