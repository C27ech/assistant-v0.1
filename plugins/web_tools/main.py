#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联网查资料插件 —— 搜索 + 抓网页正文，输出纯文本供助手AI 阅读。

用法：
    python main.py search "<查询词>" [--n 5] [--source auto|web|academic]
    python main.py fetch  "<url>"     [--max 4000]

相对最初版本的三项改进：
    1) 学术自动改道 —— 识别到学术/论文类查询，直接打 arXiv / OpenAlex / Crossref 的 API，
       不经过通用搜索引擎，拿到的都是带编号的一手文献。
    2) 多引擎交叉 —— Bing（中文网页）+ 三个学术库，结果按标题/链接去重后合并。
    3) 域名评分 —— 白名单加分、黑名单扣分，可疑模式（长数字域名、多连字符、标题含导航/大全）
       再扣分，低于阈值的直接丢弃；并检测「Bing 把英文技术查询退化成词典查询」的情况，
       自动丢弃并改走学术库。
"""
from __future__ import annotations

import argparse
import re
import sys
from urllib.parse import urlparse

try:  # 保证 Windows 控制台/管道下中文不乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

try:  # arXiv 返回的是 XML，用 html 解析器会告警（不影响结果），静音掉
    import warnings

    from bs4 import XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except Exception:  # noqa: BLE001
    pass

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
AJ_HEADERS = {**HEADERS, "Accept": "application/json"}


# --------------------------------------------------------------- 域名评分表
# 分成三档，避免「百科」跟「一手权威来源」同分（百科门槛低、错漏多）。

# 一档（+4）：一手 / 权威来源——学术库、官方文档、代码仓库
AUTHORITY_DOMAINS = (
    "arxiv.org", "openalex.org", "doi.org", "crossref.org", "nature.com",
    "science.org", "acm.org", "ieee.org", "springer.com", "sciencedirect.com",
    "pubmed", "ncbi.nlm.nih.gov", "semanticscholar.org", "openreview.net",
    "aclweb.org", "neurips.cc", "icml.cc", "jmlr.org", "distill.pub",
    "openai.com", "anthropic.com", "huggingface.co", "paperswithcode.com",
    "docs.python.org", "developer.mozilla.org", "python.org", "pypi.org",
    "github.com", "gitlab.com", "readthedocs.io", "docs.rs", "rust-lang.org",
    "nodejs.org", "kernel.org", "microsoft.com/learn", "learn.microsoft.com",
)

# 二档（+1）：技术社区 / 实践经验——有参考价值但非权威
COMMUNITY_DOMAINS = (
    "stackoverflow.com", "stackexchange.com", "zhihu.com", "juejin.cn",
    "cnblogs.com", "csdn.net", "segmentfault.com", "v2ex.com", "bilibili.com",
    "youtube.com", "medium.com", "dev.to",
)

# 三档（0）：百科类——门槛低、人工校对少、错漏较多，
# 只当「线索」用，不当依据；所以不给加分，只在输出里标注可信度低。
ENCYCLOPEDIA_DOMAINS = (
    "baike.baidu.com", "baike.sogou.com", "baike.weixin.qq.com",
    "baike.so.com", "baike.com", "moegirl.org.cn", "moegirl.uk",
    "wikiwand.com", "baike.baidu.com",
)

# 黑名单：SEO 导航站 / 下载站 / 内容农场（中文搜索结果里的常客）
BAD_DOMAINS = (
    "hao123.com", "2345.com", "360.com", "so.com", "sogou.com/web",
    "xiazai", "cr173.com", "pc6.com", "onlinedown.net", "piaodown",
    "downza", "ddooo.com", "xdowns.com", "xiazaizhijia", "downg.com",
    "3dmgame.com", "youxia", "ali213.net", "gamersky.com",
    "wenku.baidu.com", "jingyan.baidu.com", "zhidao.baidu.com",
    "baijiahao.baidu.com", "sohu.com/a/", "toutiao.com",
)

# 词典站：英文技术查询时 Bing 会退化成「查单词」，这类会刷屏
JUNK_DOMAINS = (
    "dictionary.", "iciba.com", "dict.", "eudic", "collinsdictionary",
    "dictionary.net", "danci.", "hujiang.", "youdao.", "chazidian",
    "cidian", "zdic.net", "hgcha", "kekenet.com",
)

# 本机（中国大陆网络）打不开的域名：结果直接剔除，抓取直接快速失败。
# 实测：wikipedia.org 全系（en/zh/www）ReadTimeout / 浏览器 45s 超时，
# 而失败一次要烧掉「浏览器 45s + HTTP 25s ≈ 70 秒」，白白拖慢回复。
UNREACHABLE_DOMAINS = (
    "wikipedia.org", "wikimedia.org", "wiktionary.org", "wikidata.org",
)

# 学术查询特征词（命中就走学术库）
ACADEMIC_HINTS = (
    "论文", "文献", "综述", "研究", "进展", "学术", "实证", "期刊", "会议",
    "前沿", "实验", "数据集", "机制", "原理",
    "arxiv", "paper", "papers", "survey", "benchmark", "study", "research",
    "state of the art", "sota", "catastrophic", "fine-tun", "llm",
    "transformer", "embedding", "gradient", "pretrain",
)


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""


def domain_score(url: str, title: str = "") -> int:
    """域名打分（分档）：
        +4 一手/权威（学术库、官方文档、代码仓库、.gov/.edu）
        +1 技术社区/实践经验
         0 百科类（可返回，但不优先——门槛低、错漏多）
        -6 黑名单（导航站/下载站/内容农场）、可再叠可疑模式扣分
     合计 <= -3 的结果会被丢弃。
    """
    h = _host(url)
    if is_unreachable(url):
        return -99                     # 本机打不开的站，直接剔除

    s = 0
    if any(d in h for d in AUTHORITY_DOMAINS):
        s += 4
    elif any(d in h for d in COMMUNITY_DOMAINS):
        s += 1
    elif any(d in h for d in ENCYCLOPEDIA_DOMAINS):
        s += 0                         # 百科：仅作线索，不给加分
    # 政府 / 教育域名算权威
    if h.endswith((".gov", ".edu", ".gov.cn", ".edu.cn", ".ac.cn")):
        s += 4

    if any(b in h or b in (url or "").lower() for b in BAD_DOMAINS):
        s -= 6
    if re.search(r"\d{4,}", h):           # 内容农场常用长数字域名
        s -= 1
    if h.count("-") >= 3:                 # 多连字符域名
        s -= 1
    if re.search(r"导[航]|大全|下载站|网址导航|官方入口", title or ""):
        s -= 3
    return s


def confidence_tag(url: str, src: str = "web") -> str:
    """给结果打可信度标签，方便助手（和用户）分辨来源分量。"""
    h = _host(url)
    if src == "academic":
        return "文献"
    if is_unreachable(url):
        return "不可达"
    if any(d in h for d in ENCYCLOPEDIA_DOMAINS):
        return "百科·可信度低"
    if any(d in h for d in AUTHORITY_DOMAINS) or h.endswith((".gov", ".edu", ".gov.cn", ".edu.cn", ".ac.cn")):
        return "权威"
    if any(d in h for d in COMMUNITY_DOMAINS):
        return "社区经验"
    return ""


def junk_ratio(results: list) -> float:
    """结果里「词典站」占比。英文技术查询被 Bing 退化时会接近 1.0。"""
    if not results:
        return 0.0
    n = sum(1 for r in results if any(j in _host(r["url"]) for j in JUNK_DOMAINS))
    return n / len(results)


def _latin_query(q: str) -> str:
    """学术库都是英文库：只保留拉丁词条，中文词丢掉（否则会命中一堆无关英文论文）。"""
    return " ".join(t for t in re.split(r"\s+", q) if re.search(r"[A-Za-z]", t)).strip()


def looks_academic(q: str) -> bool:
    low = q.lower()
    return any(h in low for h in ACADEMIC_HINTS)


def is_unreachable(url: str) -> bool:
    """本机网络下打不开的域名（抓一次要等 ~70 秒才失败）。"""
    h = _host(url)
    return any(d in h for d in UNREACHABLE_DOMAINS)


# ------------------------------------------------------------------- 引擎
def _mk(title, url, snippet="", src="web", year=""):
    return {
        "title": re.sub(r"\s+", " ", (title or "")).strip(),
        "url": (url or "").strip(),
        "snippet": re.sub(r"\s+", " ", (snippet or "")).strip(),
        "src": src,
        "year": str(year or ""),
    }


def search_bing(q: str, n: int) -> list:
    url = "https://www.bing.com/search?q=" + requests.utils.quote(q) + f"&count={max(n, 5)}&setlang=zh-CN"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            continue
        p = li.select_one("p")
        out.append(_mk(a.get_text(" ", strip=True), a.get("href") or "",
                       p.get_text(" ", strip=True) if p else ""))
    return out


def _arxiv_query(q: str) -> str:
    """把自然语言查询转成 arXiv 的 search_query 语法（最多取 6 个词，AND 连接）。"""
    terms = [t.strip('"') for t in re.findall(r'"[^"]+"|\S+', q) if len(t.strip('"')) > 1][:6]
    return " AND ".join(f'all:"{t}"' for t in terms)


def search_arxiv(q: str, n: int) -> list:
    url = ("http://export.arxiv.org/api/query?search_query=" + requests.utils.quote(_arxiv_query(q))
           + f"&start=0&max_results={max(n, 5)}")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")   # 不依赖 lxml；XML 标签照样能取
    out = []
    for e in soup.find_all("entry"):
        ti, ii, su, pu = e.find("title"), e.find("id"), e.find("summary"), e.find("published")
        out.append(_mk(ti.get_text() if ti else "", ii.get_text() if ii else "",
                       (su.get_text() if su else "")[:300], "academic",
                       (pu.get_text()[:4] if pu else "")))
    return out


def search_openalex(q: str, n: int) -> list:
    url = "https://api.openalex.org/works?search=" + requests.utils.quote(q) + f"&per-page={max(n, 5)}"
    r = requests.get(url, headers=AJ_HEADERS, timeout=30)
    r.raise_for_status()
    out = []
    for w in (r.json().get("results") or []):
        oa = ((w.get("open_access") or {}).get("oa_url") or "")
        out.append(_mk(w.get("title") or "", w.get("doi") or w.get("id") or "",
                       (f"开放获取全文：{oa}" if oa and ".pdf" not in oa.lower() else ""),
                       "academic", w.get("publication_year") or ""))
    return out


def search_crossref(q: str, n: int) -> list:
    url = "https://api.crossref.org/works?query=" + requests.utils.quote(q) + f"&rows={max(n, 5)}"
    r = requests.get(url, headers=AJ_HEADERS, timeout=30)
    r.raise_for_status()
    items = ((r.json().get("message") or {}).get("items")) or []
    out = []
    for it in items:
        ti = (it.get("title") or [""])[0]
        u = it.get("URL") or (("https://doi.org/" + it["DOI"]) if it.get("DOI") else "")
        abs_ = re.sub(r"<[^>]+>", " ", it.get("abstract") or "")
        yr = ""
        for k in ("published-print", "published-online", "issued", "created"):
            dp = ((it.get(k) or {}).get("date-parts") or [[None]])[0]
            if dp and dp[0]:
                yr = dp[0]
                break
        out.append(_mk(ti, u, abs_[:300], "academic", yr))
    return out


def _norm_title(t: str) -> str:
    t = re.sub(r"[\s\-—_|·・,，.。:：;；!！?？\"'“”‘’()（）\[\]【】<>《》]", "", (t or "").lower())
    return t[:40]


def dedup(results: list) -> list:
    """按「归一化标题」和「去锚点链接」双重去重，保留先出现的。"""
    seen_t, seen_u, out = set(), set(), []
    for r in results:
        kt = _norm_title(r["title"])
        ku = r["url"].split("#")[0].rstrip("/").lower()
        if (kt and kt in seen_t) or (ku and ku in seen_u):
            continue
        seen_t.add(kt)
        seen_u.add(ku)
        out.append(r)
    return out


def cmd_search(args) -> int:
    q, n, src = args.query, max(1, args.n), args.source
    notes, results = [], []

    aq = _latin_query(q)      # 学术库是英文库，只把拉丁词条传过去
    want_web = src in ("auto", "web")
    want_acad = (src == "academic") or (src == "auto" and looks_academic(q) and bool(aq))

    # ① 通用网页引擎
    if want_web:
        try:
            web = search_bing(q, n * 2)
        except Exception as e:  # noqa: BLE001
            notes.append(f"Bing 失败（{type(e).__name__}）")
            web = []
        ratio = junk_ratio(web)
        if web and ratio >= 0.5:
            notes.append(f"Bing 结果 {int(ratio * 100)}% 是词典站（英文技术查询的典型退化），已丢弃")
            web = []
            if src == "auto":
                want_acad = True
        results += web

    # ② 学术库：arXiv / OpenAlex / Crossref
    if want_acad:
        for name, fn in (("arXiv", search_arxiv), ("OpenAlex", search_openalex),
                         ("Crossref", search_crossref)):
            try:
                results += fn(aq, n)
            except Exception as e:  # noqa: BLE001
                notes.append(f"{name} 失败（{type(e).__name__}）")

    # ③ 一条都没有 → 拿学术库兜底
    if not results:
        for name, fn in (("OpenAlex", search_openalex), ("Crossref", search_crossref)):
            try:
                got = fn(aq or q, n)
                if got:
                    results += got
                    notes.append(f"已兜底改查 {name}")
                    break
            except Exception:  # noqa: BLE001
                pass

    # ④ 去重 + 域名评分 + 过滤 + 排序
    results = dedup(results)
    scored = [(domain_score(r["url"], r["title"]) + (1 if r["src"] == "academic" else 0), r)
              for r in results]
    scored = [(sc, r) for sc, r in scored if sc > -3]   # 丢掉垃圾站
    scored.sort(key=lambda x: -x[0])                    # 稳定排序：同分保持引擎顺序
    final = [r for _, r in scored][:n]

    if not final:
        print(f"（没有搜到「{q}」的结果）")
        if notes:
            print("（过程提示：" + "；".join(notes) + "）")
        return 0

    print(f"查询：{q}｜返回 {len(final)} 条")
    if notes:
        print("（过程提示：" + "；".join(notes) + "）")
    print()
    has_enc = False
    for i, r in enumerate(final, 1):
        note = confidence_tag(r["url"], r["src"])
        tag = ""
        if note == "文献":
            tag = f"［文献 {r['year']}］" if r["year"] else "［文献］"
        elif note:
            tag = f"［{note}］"
        if note.startswith("百科"):
            has_enc = True
        print(f"{i}. {tag}{r['title']}")
        print(f"   {r['url']}")
        if r["snippet"]:
            print(f"   {r['snippet'][:220]}")
        print()

    if has_enc:
        print("（注意：标「百科·可信度低」的是用户可自由编辑的百科站，门槛低、错漏较多，"
              "只能当线索。人名 / 时间 / 数字 / 结论这类关键事实，请用［文献］或［权威］来源"
              "交叉验证，或换个关键词再搜一次确认；拿不准就如实告诉用户「这条来自百科，"
              "不够可靠」。）")
    return 0


MAIN_SELECTORS = [
    "article",
    "main",
    "[role=main]",
    "#mw-content-text",          # 维基/萌娘
    ".markdown-body",            # GitHub / 技术文档
    ".J-lemma-content",          # 百度百科正文
    ".lemma-summary",
    "#content",
    ".article-content",
    ".entry-content",
    ".post-content",
    ".RichText",
]


def extract_main_text(soup) -> str:
    """优先取「正文容器」，取文字最多的那个；都没有就退回 body，并过滤掉短行（导航项）。"""
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    best = ""
    for sel in MAIN_SELECTORS:
        for el in soup.select(sel):
            t = el.get_text("\n", strip=True)
            if len(t) > len(best):
                best = t
    if not best:
        body = soup.find("body") or soup
        for tag in body(["nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        best = body.get_text("\n", strip=True)

    # 过滤短行（导航/按钮通常是 2~3 个字）
    lines = [ln.strip() for ln in best.splitlines()]
    lines = [ln for ln in lines if len(ln) >= 4]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text)


BROWSER_CHANNELS = (None, "msedge", "chrome")  # None = Playwright 自带 Chromium；失败再退回系统 Edge/Chrome


def _browser_text(url: str, timeout_s: int = 20):
    """用无头浏览器渲染页面（能拿到 JS 动态内容）；返回 (标题, 正文)。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        last_err = None
        for ch in BROWSER_CHANNELS:
            kwargs = {"headless": True}
            if ch:
                kwargs["channel"] = ch
            try:
                browser = p.chromium.launch(**kwargs)
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
            try:
                page = browser.new_page(user_agent=UA, locale="zh-CN")
                page.goto(url, timeout=timeout_s * 1000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)  # 等 JS 把正文渲染出来
                title = page.title()
                text = ""
                for sel in MAIN_SELECTORS:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            t = el.inner_text() or ""
                            if len(t) > len(text):
                                text = t
                    except Exception:  # noqa: BLE001
                        pass
                if not text.strip():
                    text = page.inner_text("body") or ""
                return title, text
            finally:
                browser.close()
        raise RuntimeError(f"没有可用浏览器（试过 {BROWSER_CHANNELS}）：{last_err}")


def _requests_text(url: str):
    """退回方案：普通 HTTP 抓取 + 正文抽取（对 JS 站点无效）。"""
    r = requests.get(url, headers=HEADERS, timeout=12)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    return title, extract_main_text(soup)


def _tidy(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if len(ln) >= 4]  # 过滤导航/按钮短行
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def cmd_fetch(args) -> int:
    # 本机打不开的域名：立刻返回，别白等 70 秒
    if is_unreachable(args.url):
        print(
            f"（跳过：{_host(args.url)} 在本机网络下无法访问——维基系域名在中国大陆连不上，"
            "抓它只会白等约 70 秒后失败。请换来源：百度百科 / 萌娘百科 / 搜狗百科 / arXiv / "
            "OpenAlex / 知乎 / GitHub，或改用 web_search 换个关键词重新找。）"
        )
        return 0

    title, text = "", ""
    try:
        title, text = _browser_text(args.url, 20)
    except Exception as e:  # noqa: BLE001
        print(f"[提示] 浏览器渲染失败（{type(e).__name__}: {e}），改用普通 HTTP 抓取…", file=sys.stderr)
        try:
            title, text = _requests_text(args.url)
        except Exception as e2:  # noqa: BLE001
            print(f"[错误] 抓取失败：{type(e2).__name__}: {e2}")
            return 1

    text = _tidy(text)
    if not text:
        print("（抓到了页面但没提取到正文——可能是登录墙/验证码/纯视频页，换一个来源试试）")
        return 0
    total = len(text)
    if total > args.max:
        text = text[: args.max] + f"\n\n……（已截断，网页正文共 {total} 字）"
    if title:
        print(f"标题：{title}\n网址：{args.url}\n")
    print(text)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="web_tools", description="联网查资料：搜索 + 抓网页正文")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("search", help="搜索")
    s1.add_argument("query", help="查询词")
    s1.add_argument("--n", type=int, default=5, help="返回条数（默认 5）")
    s1.add_argument("--source", choices=("auto", "web", "academic"), default="auto",
                    help="auto=自动路由（默认）；web=只搜网页；academic=只查学术库")
    s2 = sub.add_parser("fetch", help="抓取网页正文")
    s2.add_argument("url", help="网页链接")
    s2.add_argument("--max", type=int, default=4000, help="最多输出多少字（默认 4000）")
    args = ap.parse_args(argv)
    return cmd_search(args) if args.cmd == "search" else cmd_fetch(args)


if __name__ == "__main__":
    raise SystemExit(main())
