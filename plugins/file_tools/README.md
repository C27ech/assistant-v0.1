# file_tools —— 读文件插件

给助手 AI 用的「读本机文件」工具：**读文本文件** / **列目录** / **按文件名找** / **搜文件内容**。

## 用法

```bat
cd /d C:\path\to\assistant_v0.1/plugins/file_tools

:: 读文件（相对路径以第一个根目录为准；也可传绝对路径）
python main.py read "笔记.md"
python main.py read "C:\Users\<你的用户名>\Desktop\笔记.md" --start 200 --lines 100

:: 列目录
python main.py list "C:\Users\<你的用户名>\Desktop"
python main.py list . --all

:: 按文件名找
python main.py find "*.md"
python main.py find "*报告*" --root "C:\Users\<你的用户名>\Desktop" --n 40

:: 在文件内容里搜（正则，返回 文件:行号: 内容）
python main.py grep "TODO" --root "C:\Users\<你的用户名>\Desktop" --ext .py,.md --n 30
```

## 安全设计（三层，这是重点）

这个接口连着 QQ，而且现在**可读整台电脑**，所以「敏感文件硬拦截」这层绝不能松。

| 层 | 规则 | 说明 |
|----|------|------|
| **① 可读范围** | `config.json` → `roots`：`["*"]` = **整台电脑**（自动枚举所有盘，实测识别到 C/D/E/F）；也可写成具体目录列表 | 想收窄就改成 `["C:\Users\<你的用户名>\Desktop"]` |
| **② 敏感文件黑名单** | 命中即拒，**即使文件在被允许的盘上也读不到** | 内置在代码里，**只可追加、不可取消** |
| **③ 体积 / 类型限制** | 跳过 >8MB 与二进制文件；输出截断到 12000 字并可分块续读 | 防止读爆上下文、拖慢回复 |

**② 的具体清单**（防的是「有人诱导助手把密钥发到聊天里」）：

| 类别 | 拦截的子串 / 文件名 |
|------|-------------------|
| 配置与凭据 | `.env`、`config.json`、`credential(s)`、`secret`、`password`、`passwd`、`apikey`、`api_key`、`access_token`、`token`、`cookie`、`login data`、`web data`、`keychain`、`netrc`、`kubeconfig`、`.aws`、`.docker`、`.npmrc`、`.pypirc`、`.git-credentials` |
| 私钥 / 证书 | `id_rsa`、`id_ed25519`、`id_dsa`、`.ppk`、`.pem`、`.pfx`、`.p12`、`.key`、`.keystore`、`.jks`、`.kdbx`、`.ovpn`、`unattend.xml` |
| 版本库 / 登录态 | `.git`、`.ssh`、`Tencent Files`、`nt_qq`、`napcat` |
| 系统凭据 | `\Microsoft\Credentials`、`\config\SAM`、`\config\SYSTEM` |

**实测（全盘模式下）**：

| 请求 | 结果 |
|------|------|
| `read C:\Windows\win.ini` | ✅ 正常读到（桌面以外也能读）|
| `read C:\Python3xx\LICENSE.txt` | ✅ 正常 |
| `read Assistant\.env` | ⛔ 拦截「.env」|
| `read C:\Users\<USER>\.ssh\id_rsa` | ⛔ 拦截「id_rsa」|
| `read Documents\Tencent Files\nt_qq\x.txt` | ⛔ 拦截「tencent files」|
| `read Chrome\User Data\Default\Login Data` | ⛔ 拦截「login data」|
| `read C:\Users\<USER>\.aws\credentials` | ⛔ 拦截「credentials」|
| `list C:\Python3xx` | ✅ 15 项 |
| `grep "Copyright" --root C:\Python3xx --ext .txt` | ✅ 命中 30 处 |


## 分块读大文件

输出超过 `max_output_chars`（默认 12000 字）会自动截断，并给出续读提示：

```
……（本次已截断；文件还有内容。用 --start 512 继续读下一段，或先用 --lines 指定更小的行数。）
```

助手看到提示就能自己用 `--start 512` 接着读，不用你操心。

## 想扩大可读范围

现在默认就是**整台电脑**（`roots: ["*"]`）。想**收窄**范围更安全的话，改成具体目录：

```json
{
  "roots": [
    "C:\Users\<你的用户名>\Desktop",
    "D:/我的项目"
  ]
}
```

`default_path` 是「不给路径时的默认起点」（`list`/`find`/`grep` 和相对路径都以它为准），默认是桌面 —— 保持它是桌面，整盘搜索就不会因为误触而跑满全盘。

## 调参数

| 配置项 | 默认 | 作用 |
|--------|------|------|
| `roots` | `["*"]` | 可读范围；`"*"`=整台电脑，也可写具体目录列表 |
| `default_path` | `C:\Users\<你的用户名>\Desktop` | 不给路径时的默认起点 |
| `max_file_mb` | 8 | 超过就不读 |
| `max_output_chars` | 12000 | 单次输出上限（超出截断，可分块续读）|
| `max_list_entries` | 200 | 列目录最多显示多少项 |
| `max_find_results` | 40 | 找文件最多返回多少条 |
| `max_grep_matches` | 30 | 搜内容最多返回多少处 |
| `grep_max_files` | 3000 | 搜内容最多扫多少个文件 |
| `deny_patterns` | `[]` | 追加禁读子串（只增不减）|

## 限制

- 只能读**文本**：图片、PDF(扫描件)、Office 文档、数据库读不了（会提示是二进制）。
  需要读 PDF/Word 的话得另加依赖（`pypdf` / `python-docx`），要的话说一声。
- 编码自动识别 `utf-8-sig / utf-8 / gbk`，纯 UTF-16 文件可能读成乱码。
- 没有写入/删除能力（只有读），这是有意的。
