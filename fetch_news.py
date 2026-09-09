#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平阳新闻定时抓取：wzpy.cn（县融媒） + zjpy.gov.cn（县政府）→ data/news.json
采用通用启发式解析（文章URL模式 + 标题 + 相邻日期），对政务CMS改版有较强容错。
"""
import re, json, os, hashlib, traceback
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TIMEOUT = 25
PER_SOURCE_LIMIT = 40
TOTAL_LIMIT = 60

SOURCES = [
    {
        "key": "wzpy",
        "name": "平阳新闻网",
        "urls": [
            "http://www.wzpy.cn/",
            "http://www.wzpy.cn/kx/",
            "https://www.wzpy.cn/",
            "http://pingyang.cn/",
        ],
    },
    {
        "key": "zjpy",
        "name": "平阳县人民政府网",
        "urls": [
            "http://www.zjpy.gov.cn/",
            "https://www.zjpy.gov.cn/",
        ],
    },
]

# 浙江政务站群 /art/2026/9/9/art_xxx_yyy.html 及常见 CMS 文章路径
ARTICLE_RE = re.compile(
    r"(?:/art/\d{4}/\d{1,2}/\d{1,2}/art_\d+_\d+\.html"
    r"|/art/\d{4}/\d{1,2}/\d{1,2}/\d+\.s?html"
    r"|/\d{4}-\d{1,2}/\d{1,2}/\d+\.s?html"
    r"|/\d{4}/\d{1,4}/\d{1,4}\.s?html"
    r"|/node\d+/\d+\.s?html"
    r"|/news/\d+\.s?html)",
    re.I,
)
DATE_RE = re.compile(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)")
FW = str.maketrans("０１２３４５６７８９", "0123456789")


def norm_date(s):
    s = (s or "").translate(FW)
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if not m:
        return ""
    return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def fetch(url):
    for scheme in (url, url.replace("http://", "https://", 1) if url.startswith("http://") else url):
        try:
            r = requests.get(scheme, headers=UA, timeout=TIMEOUT)
            if r.status_code == 200 and r.text:
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding
                print("[fetch ok]", scheme, len(r.text))
                return r.text
            print("[fetch skip]", scheme, r.status_code)
        except Exception as e:
            print("[fetch err]", scheme, str(e)[:120])
    return None


def parse_page(html, base, source_name):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    root = re.match(r"^(https?://[^/]+)", base)
    root = root.group(1) if root else base.rstrip("/")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not ARTICLE_RE.search(href):
            continue
        title = (a.get("title") or a.get_text() or "").strip()
        title = re.sub(r"\s+", " ", title)
        if len(title) < 6 or title.lower().endswith(("更多", "more")):
            continue
        if href.startswith("//"):
            href = "http:" + href
        elif href.startswith("/"):
            href = root + href
        elif not href.startswith("http"):
            href = base.rstrip("/") + "/" + href
        date = ""
        node = a
        for _ in range(4):  # 在祖先元素里找相邻日期
            node = node.parent
            if node is None:
                break
            dm = DATE_RE.search(node.get_text(" ", strip=True)[:300])
            if dm:
                date = norm_date(dm.group(1))
                break
        items.append({
            "id": hashlib.md5((href + title).encode("utf-8")).hexdigest()[:12],
            "title": title,
            "url": href,
            "date": date,
            "source": source_name,
        })
    return items


def dedup_sort(items):
    seen, out = set(), []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        out.append(it)
    out.sort(key=lambda x: x["date"] or "0000-00-00", reverse=True)
    return out


def fetch_article_content(url, max_paras=15):
    """抓文章页正文：启发式取最大文本块的所有 <p>/<div> 段落"""
    html = fetch(url)
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "iframe"]):
            tag.decompose()
        paras = []
        for p in soup.find_all(["p", "div"]):
            txt = p.get_text(" ", strip=True)
            txt = re.sub(r"\s+", " ", txt)
            if len(txt) >= 30 and not DATE_RE.fullmatch(txt):
                # 去重相邻段落
                if not paras or paras[-1] != txt:
                    paras.append(txt)
            if len(paras) >= max_paras:
                break
        return paras
    except Exception as e:
        print("[content err]", str(e)[:120])
        return []


def main():
    now = datetime.now(timezone(timedelta(hours=8)))
    all_items, report = [], []
    for src in SOURCES:
        got = 0
        for url in src["urls"]:
            html = fetch(url)
            if not html:
                continue
            try:
                items = parse_page(html, url, src["name"])
            except Exception as e:
                print("[parse err]", url, str(e)[:120])
                items = []
            all_items.extend(items)
            got += len(items)
            if got >= PER_SOURCE_LIMIT:
                break
        report.append({"source": src["name"], "ok": got > 0, "count": got})
        print("[report]", src["name"], got)
    all_items = dedup_sort(all_items)[:TOTAL_LIMIT]
    # 抓正文（前 30 条，控制时长）
    for i, it in enumerate(all_items[:30]):
        content = fetch_article_content(it["url"])
        it["content"] = content
        print("[content]", i + 1, it["title"][:24], len(content), "paras")
    data = {"updatedAt": now.strftime("%Y-%m-%d %H:%M"), "report": report, "items": all_items}
    os.makedirs("data", exist_ok=True)
    with open("data/news.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("[done] total", len(all_items))
    if not all_items:
        raise SystemExit(1)  # 全部失败时让 Action 报红，便于发现源站改版


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
