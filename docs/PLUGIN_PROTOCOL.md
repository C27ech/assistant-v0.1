# 插件协议

插件 = **任何能被命令行调用的程序**。助手把 `plugins.json` 里的每一条注册成一个工具，
调用时起子进程执行，把 **stdout 当工具结果**喂回模型。

## 1. 注册格式

`assistant/plugins.json`：

```json
{
  "工具名": {
    "description": "给大模型看的说明",
    "command": ["python", "main.py", "子命令", "{参数1}", "--opt={参数2}"],
    "cwd": "plugins/某个插件目录",
    "timeout": 120,
    "params": {
      "参数1": {"type": "string",  "description": "说明", "required": true},
      "参数2": {"type": "integer", "description": "可选参数（没有 required 就是可选）"}
    }
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| （键名） | string | 工具名。模型看到的就是它，用英文小写下划线 |
| `description` | string | **最关键的字段**。写清：什么时候用、返回什么、有什么禁区。可以写纪律（"每次最多抓 3 页"） |
| `command` | string[] | 命令模板数组。**不要把整条命令写成一个大字符串**——数组才能正确处理含空格的参数 |
| `cwd` | string | 子进程工作目录。**相对路径按 `assistant/` 解析**（便于整仓库搬家），绝对路径也支持 |
| `timeout` | number | 超时秒数。超时 → 杀子进程 → 已产生的输出落盘 → 返回超时提示 |
| `params` | object | 参数表；转成 OpenAI function-calling 的 JSON Schema |
| `params.<名>.type` | string | `string` / `integer` / `number` / `boolean` / `array` |
| `params.<名>.required` | bool | 省略即**可选** |

## 2. 参数替换规则

- `{参数名}` 会被替换成实际值
- **可选参数没传时**：**包含该占位符的整个数组元素被丢弃**
  - ✅ 正确：`["--start={start_line}"]` → 没传时整段消失，命令行干净
  - ❌ 错误：`["--start", "{start_line}"]` → 没传时留下光秃秃的 `--start`，argparse 直接报错
- **布尔值**统一格式化成小写 `true` / `false`
  （Python 的 `str(False)` 是 `"False"`，在命令行里会被当成**真值**——这是安全隐患，尤其危险开关）

## 3. 执行环境

| 项目 | 约定 |
|------|------|
| 编码 | 固定 UTF-8（`encoding="utf-8", errors="replace"`），并给子进程注入 `PYTHONUTF8=1` |
| 输出 | stdout = 结果；stderr = 日志（也会落盘，不喂给模型） |
| 退出码 | 非 0 会被标注为「执行失败」，并把 stdout 一并返回（方便看错误信息） |
| 日志 | `assistant/logs/plugin_<名字>_<时间戳>.{out,err}.log` |
| 环境变量 | 继承父进程 + `PYTHONUTF8=1` |

## 4. 写好 `description` 的技巧

`description` 决定模型**什么时候会调、怎么调**。经验：

1. **写触发场景**：「需要看某个文件的内容时调用」
2. **写返回格式**：「返回 标题 + 链接 + 摘要」——模型才知道拿到结果后怎么用
3. **写清限制和后果**：
   - 「一次查询抓 3 个页面左右即可，抓十几个页面要等好几分钟」
   - 「quit / title / quick_save / quick_load 属于危险动作，需先征得用户同意，并传 `allow_dangerous=true`」
   - 「含密钥 / 凭据的文件会被安全策略拦截，被拦时换其它文件」
4. **可选参数要说明省略时的行为**：「省略则从默认目录开始」

## 5. 插件实现的通用骨架（Python）

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一句话说明 + 用法示例。"""
from __future__ import annotations

import argparse
import sys

try:  # Windows 控制台/管道下中文不乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="my_plugin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("do", help="干某件事")
    p.add_argument("input")
    p.add_argument("--n", type=int, default=5)
    args = ap.parse_args(argv)

    print("结果……")          # stdout → 模型看到的内容
    return 0                  # 0=成功；非 0 会被标为失败


if __name__ == "__main__":
    raise SystemExit(main())
```

要点：**输出纯文本、别打日志到 stdout**（日志走 stderr）、**失败要有可读的原因**。

## 6. 调试

```bat
:: 1) 单独命令行跑一遍，确认能出结果
cd plugins\my_plugin && python main.py do hello

:: 2) 让助手调一次，然后看落盘日志
dir assistant\logs\plugin_my_plugin_*.log

:: 3) 检查参数替换是否符合预期（最容易出问题的一步）
::    ——确认可选参数没传时命令行里没有裸 flag
```

## 7. 现有插件一览

见 [`../plugins/README.md`](../plugins/README.md)。
