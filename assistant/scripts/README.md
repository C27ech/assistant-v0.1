# scripts/ —— 语料处理脚本（风格功能用）

这些脚本服务于**可选的风格系统**（见 [`../style/README.md`](../style/README.md)）。
不想要风格的话，完全可以忽略这个目录。

| 脚本 | 作用 | 用法 |
|------|------|------|
| `build_style_guide.py` | **蒸馏**：把语料分批喂给模型，归纳成「风格说明书」 | `python scripts\build_style_guide.py` |
| `clean_corpus.py` | 清洗语料：去重、去乱码、去日文假名残留等 | `python scripts\clean_corpus.py` |
| `fix_corpus_typos.py` | 修常见错字（把频次异常的可疑字交给模型分类，再批量替换） | `python scripts\fix_corpus_typos.py <语料文件>` |
| `e2e_test.py` | 端到端自测（不接渠道，跑通"消息 → 决策 → 回复"） | `python scripts\e2e_test.py` |
| `live_test.py` | 连真实模型的冒烟测试（会消耗 token） | `python scripts\live_test.py` |

## 典型流程

```bat
cd assistant

:: 1) 准备语料（每行一句，放 style\corpus.txt）
:: 2) 清洗
python scripts\clean_corpus.py

:: 3) 蒸馏出说明书
python scripts\build_style_guide.py

:: 4) 重启助手
```

## 语料格式

- **每行一句**；空行、`#` 开头的行会被忽略
- 建议**只保留目标角色一个人的台词**（去掉旁白、他人台词、时间轴、角色名前缀）
- 想合并多个来源（不同作品/不同场合）：按权重交错，避免"后半段全是另一个来源"

## 说明

- 本仓库**不包含任何第三方作品的语料**。你要模仿谁，请自备**你有权使用的**文本。
- 作者自用的"从游戏文本提取工程导入语料"的脚本**没有包含**（它绑定作者的私人提取流水线）；
  语料格式就是「每行一句」，你用任何方式生成 `style/corpus.txt` 都行。
- 蒸馏脚本会**分批**处理（默认每批 400 句），避免一次塞爆模型上下文；
  如果语料很多，跑之前先把 `corpus_distill.txt` 准备好。
