# -*- coding: utf-8 -*-
"""只读屏幕描述助手（不执行任何键鼠动作）。复用 controller_v2 既有豆包视觉客户端。

用法：python diag.py [描述需求文件]
描述需求文件为 UTF-8 文本；缺省使用内置通用描述。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.screen import capture_screenshot  # noqa: E402
from core.config import load_config, resolve_ark_api_key  # noqa: E402
from core import vision_client as vc  # noqa: E402

cfg = load_config()
p = capture_screenshot()
print("SHOT", p, flush=True)

_, vision = vc._read_vision_config(cfg)
key = resolve_ark_api_key(cfg)
if not key:
    print("NO_KEY", flush=True)
    sys.exit(1)
model = vc._resolve_model_name("pro", cfg, vision)

prompt_file = sys.argv[1] if len(sys.argv) > 1 else None
if prompt_file:
    with open(prompt_file, "r", encoding="utf-8-sig") as fh:
        prompt = fh.read().strip()
else:
    prompt = (
        "你是 Windows 屏幕观察助手。请详细描述当前截图内容，逐条列出："
        "1) 当前最前面的应用窗口是什么（是否微信/WeChat）；"
        "2) 微信窗口的聊天列表里能看到哪些会话/联系人（按从上到下的顺序给出名字，以及各自大致在屏幕的横向/纵向位置，可用整张图 0~1000 的纵向比例描述）；"
        "3) 是否能看到名为「程风扬」的会话，在什么位置；"
        "4) 屏幕总体布局。"
    )

text = vc._post_chat(p, prompt, model, key, cfg, vision)
print("DESC", text, flush=True)
print("stage=done", flush=True)
