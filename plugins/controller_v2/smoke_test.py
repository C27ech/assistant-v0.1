# -*- coding: utf-8 -*-
"""临时连通性冒烟测试（只读，不执行任何键鼠动作）。用完即删。"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
print("stage=start", flush=True)

from core.screen import capture_screenshot  # noqa: E402

print("stage=import_screen_ok", flush=True)
p = capture_screenshot()
print("stage=shot", p, flush=True)

from core.config import load_config, resolve_ark_api_key  # noqa: E402
from core import vision_client as vc  # noqa: E402

cfg = load_config()
print("stage=key_ok", bool(resolve_ark_api_key(cfg)), flush=True)

r = vc.multimodal_decide(p, "观察当前屏幕，用一句话说明大致内容，不需要任何点击。", model_tier="turbo")
print("RESULT", r, flush=True)
print("stage=done", flush=True)
