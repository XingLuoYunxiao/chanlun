#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 chanshi.vip 上《教你炒股票》108 课。

用法（在 chanlun108/ 目录下）：

    python3 tools/crawl_chanshi.py catalog          # 建目录：课号 → tid/标题
    python3 tools/crawl_chanshi.py articles         # 抓 108 课原文 → 原文/*.md
    python3 tools/crawl_chanshi.py replies --cookie cookie.txt   # 抓评论回复（需登录）
    python3 tools/crawl_chanshi.py check            # 覆盖度核对

设计要点
--------
* **缓存优先**：每个 URL 的原始 HTML 落在 `cache/`，重跑不再打网络。想重抓先删缓存。
* **慢**：默认每次请求间隔 1 秒。这是别人的站，不并发、不重试轰炸。
* **原文与回复分开**：原文（1 楼）游客可见；回复（2 楼起）要登录 —— 见 README。
* 站点是 **GBK** 编码，且部分页面是"UTF-8 字节当 GBK 读"的双重编码，两种都试。
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
ART = ROOT / "原文"
REP = ROOT / "回复"
SITE = "https://chanshi.vip"
LISTING = SITE + "/forum.php?mod=forumdisplay&fid=198&filter=typeid&typeid=369&page={}"
THREAD = SITE + "/forum.php?mod=viewthread&tid={}&page={}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
DELAY = 1.0
GATE = "阅读权限不足"
# 游客可见的横幅，不属于正文
BANNERS = (
    "缠迷您好，关注了这么久，何不登入享用更多功能？",
    "您需要 登录 才可以下载或查看，没有账号？加入",
    "您需要登录才可以下载或查看",
)


def _decode(raw: bytes) -> str:
    """GBK 优先；解不出来再退 UTF-8。站点大量页面是 GBK。"""
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", "replace")


def fetch(url: str, cookie: str | None = None, refresh: bool = False) -> str:
    """带磁盘缓存的 GET。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1((url + "|" + (cookie or "")[:20]).encode()).hexdigest()[:16]
    slug = re.sub(r"[^A-Za-z0-9]+", "_", url.split("chanshi.vip")[-1])[:70]
    path = CACHE / f"{slug}.{key}.html"
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                              "Accept-Language": "zh-CN,zh;q=0.9"})
    if cookie:
        req.add_header("Cookie", cookie)
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                text = _decode(resp.read())
            path.write_text(text, encoding="utf-8")
            time.sleep(DELAY)
            return text
        except (urllib.error.URLError, TimeoutError) as exc:  # 网络抖一下再试
            last = exc
            time.sleep(2 + attempt * 3)
    raise SystemExit(f"抓取失败：{url}（{last}）")


# ---------------------------------------------------------------- 解析

def strip_tags(fragment: str) -> str:
    frag = re.sub(r"<script.*?</script>", "", fragment, flags=re.S)
    frag = re.sub(r"<style.*?</style>", "", frag, flags=re.S)
    frag = re.sub(r"<br\s*/?>", "\n", frag)
    frag = re.sub(r"</(p|div|li|tr)>", "\n", frag)
    frag = re.sub(r"<[^>]+>", "", frag)
    frag = html_mod.unescape(frag).replace("\u3000", " ").replace("\xa0", " ")
    lines = [ln.rstrip() for ln in frag.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln.strip() in BANNERS or "才可以下载或查看" in ln:
            continue
        if not ln.strip() and (not out or not out[-1].strip()):
            continue
        out.append(ln)
    return "\n".join(out).strip()


# 站点加在正文上的壳：游客登录提示的关闭按钮（会留一个孤零零的 "x"）、
# 末尾的「相关帖子」导航（thread-links 锚点），都不属于原文。
ATIPS_RE = re.compile(r'<span class="atips_close".*?</span>', re.S)
# 锚点自身不能允许嵌套 <a>，否则 `.*?` 会一路吞到最后一个 </a>，把正文一起吃掉。
_NAV_ANCHOR = (r'<a class="thread-links"[^>]*>'
               r'(?:[^<]|<(?!/?a[\s>])[^>]*>)*</a>')
TAIL_NAV_RE = re.compile(
    r'(?:\s|<br\s*/?>|&nbsp;|</?(?:font|strong|b|span|div)[^>]*>'
    r'|' + _NAV_ANCHOR + r')+\Z', re.S)
NAV_MARKERS = ("<strong>浏览", "更多文章请点击")


def _clean_post_html(cell: str) -> str:
    """剥掉正文外层由站点追加的导航/提示，避免混进原文。"""
    cell = ATIPS_RE.sub("", cell)
    for mk in NAV_MARKERS:
        cut = cell.find(mk)
        if cut != -1:
            cell = cell[:cut]
            break
    while True:                      # 从尾部一段段啃掉导航锚点
        m = TAIL_NAV_RE.search(cell)
        if not m or "thread-links" not in m.group(0):
            break
        cell = cell[:m.start()]
    return cell


def parse_posts(page: str) -> list[dict]:
    """把一个帖子页切成楼层。"""
    chunks = re.split(r'(?=<div id="post_\d+")', page)
    posts: list[dict] = []
    for chunk in chunks[1:]:
        m = re.match(r'<div id="post_(\d+)"', chunk)
        pid = m.group(1) if m else ""
        auth = re.search(r'class="authi">(.*?)</div>', chunk, re.S)
        who, when = "", ""
        if auth:
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", auth.group(1)))
            txt = txt.replace("&nbsp;", " ").strip()
            who = re.sub(r"\s*(楼主|\|).*$", "", txt).strip()
            d = re.search(r"发表于\s*([0-9]{4}-[0-9]{1,2}-[0-9]{1,2}[^|]*)", txt)
            if d:
                when = d.group(1).strip()
        body = re.search(r'<td class="t_f"[^>]*>(.*?)</td>', chunk, re.S)
        text = strip_tags(_clean_post_html(body.group(1))) if body else ""
        floor = re.search(r'class="pi">\s*<strong>\s*<a[^>]*>(\d+)</a>', chunk)
        posts.append({
            "pid": pid,
            "author": who,
            "date": when,
            "floor": int(floor.group(1)) if floor else None,
            "gated": GATE in text,
            "text": text,
        })
    return posts


def thread_pages(tid: str, cookie: str | None) -> int:
    first = fetch(THREAD.format(tid, 1), cookie)
    pages = [int(x) for x in re.findall(r"page=(\d+)", first)]
    return max(pages) if pages else 1


# ---------------------------------------------------------------- 子命令

def load_lessons() -> dict[int, dict]:
    path = ROOT / "index.json"
    if not path.exists():
        raise SystemExit("先跑 `catalog` 建目录")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw["lessons"].items()}


def cmd_catalog(_args) -> int:
    threads: dict[str, str] = {}
    for page in range(1, 7):
        h = fetch(LISTING.format(page))
        found = re.findall(r'<tbody[^>]*id="normalthread_(\d+)"(.*?)</tbody>', h, re.S)
        for tid, blk in found:
            t = re.search(r'class="s xst"[^>]*>(.*?)</a>', blk, re.S)
            threads[tid] = strip_tags(t.group(1)).strip() if t else ""
        print(f"  目录第 {page} 页：本页 {len(found)}，累计 {len(threads)}")
    lessons: dict[str, dict] = {}
    for tid, title in threads.items():
        m = re.match(r"教你炒股票\s*(\d+)\s*[:：]\s*(.*)", title)
        if m:
            lessons[str(int(m.group(1)))] = {"tid": tid, "title": m.group(2).strip(),
                                             "full_title": title}
    missing = [n for n in range(1, 109) if str(n) not in lessons]
    doc = {"source": SITE, "listing": LISTING.format(1), "count": len(lessons),
           "missing": missing, "lessons": dict(sorted(lessons.items(), key=lambda kv: int(kv[0])))}
    (ROOT / "index.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"目录：{len(lessons)} 课，缺号 {missing or '无'}")
    return 0


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip()[:70]


def cmd_articles(args) -> int:
    lessons = load_lessons()
    ART.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    rows = []
    for num in sorted(lessons):
        info = lessons[num]
        tid = info["tid"]
        pages = thread_pages(tid, None)
        posts: list[dict] = []
        for pg in range(1, pages + 1):
            posts += parse_posts(fetch(THREAD.format(tid, pg)))
        head = next((p for p in posts if p["text"] and not p["gated"]), None)
        if head is None:
            print(f"  !! 第 {num} 课没有可读的正文（tid={tid}）")
            rows.append({"num": num, "tid": tid, "chars": 0, "pages": pages, "gated": True})
            continue
        title = info["full_title"]
        body = head["text"]
        lines = [
            f"# {title}",
            "",
            f"- 课号：{num}",
            f"- 标题：{info['title']}",
            f"- 原博客发表：{head['date'] or '（未标）'}",
            f"- 作者：缠中说禅",
            f"- 来源：{THREAD.format(tid, 1)}",
            f"- 抓取日期：{stamp}",
            f"- 原文（1 楼）字数：{len(body)}",
            "",
            "---",
            "",
            body,
            "",
        ]
        out = ART / f"{num:03d}-{_safe(info['title'])}.md"
        out.write_text("\n".join(lines), encoding="utf-8")
        rows.append({"num": num, "tid": tid, "chars": len(body), "pages": pages,
                     "replies_on_page": len(posts) - 1, "gated": False})
        print(f"  {num:3d} tid={tid:5s} 页数={pages} 正文={len(body):5d}字 回复={len(posts)-1}")
    (ROOT / "articles_report.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = [r for r in rows if not r["gated"]]
    print(f"\n原文：{len(ok)}/{len(lessons)} 课落盘，最短 {min((r['chars'] for r in ok), default=0)} 字，"
          f"最长 {max((r['chars'] for r in ok), default=0)} 字")
    return 0


def cmd_replies(args) -> int:
    cookie = pathlib.Path(args.cookie).read_text(encoding="utf-8").strip() if args.cookie else None
    if not cookie:
        raise SystemExit("抓回复要登录态：--cookie 给一个含 `Cookie:` 头的文件（见 README）")
    lessons = load_lessons()
    REP.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    rows = []
    for num in sorted(lessons):
        info = lessons[num]
        tid = info["tid"]
        pages = thread_pages(tid, cookie)
        posts: list[dict] = []
        for pg in range(1, pages + 1):
            posts += parse_posts(fetch(THREAD.format(tid, pg), cookie))
        gated = sum(1 for p in posts if p["gated"])
        out_lines = [f"# {info['full_title']} —— 评论与回复", "",
                     f"- 课号：{num}",
                     f"- 来源：{THREAD.format(tid, 1)}",
                     f"- 抓取日期：{stamp}",
                     f"- 楼层：{len(posts)}（其中读不到的 {gated}）", "", "---", ""]
        for p in posts:
            tag = "（未登录读不到）" if p["gated"] else ""
            out_lines += [f"## {p['floor'] or '?'} 楼 · {p['author']} · {p['date']} {tag}", "",
                          p["text"] or "（空）", ""]
        (REP / f"{num:03d}-{_safe(info['title'])}.md").write_text("\n".join(out_lines), encoding="utf-8")
        rows.append({"num": num, "tid": tid, "floors": len(posts), "gated": gated})
        print(f"  {num:3d} tid={tid:5s} 楼层={len(posts):3d} 读不到={gated:3d}")
    (ROOT / "replies_report.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [r for r in rows if r["gated"]]
    print(f"\n回复：{len(rows)} 课落盘；{len(bad)} 课仍有读不到的楼层")
    return 0


COMPILE_TID = "5688"   # 「教你炒股票单帖整理版」：原文 + 原博客评论（游客可读）
COMMENT_RE = re.compile(
    r"^[ \t]*([^\n]{0,32}?)[ \t:：]*[ \t]*(20\d\d-\d{1,2}-\d{1,2} \d{1,2}:\d{2}:\d{2})[ \t]*$", re.M)


def _is_chan(author: str) -> bool:
    """作者是不是缠中说禅本人。

    必须**完全相等**：站内另有「缠中说禅技术基地」这类账号，用 startswith 会误判。
    作者串里偶尔会把日期一起带进来（表格串行），这里先摘掉。
    """
    a = strip_tags(author).strip().strip("：:").strip()
    a = re.sub(r"\s+\d{4}-\d{1,2}-\d{1,2}.*$", "", a).strip()
    return a in {"缠中说禅", "缠师"}


def _lesson_sections(whole: str, lessons: dict[int, dict]) -> dict[int, str]:
    """按"行首的课标题"切段。

    不能见到「教你炒股票N：」就切 —— 正文里常有「在"教你炒股票29：转折的力度与级别"中」
    这种引用，那样会把第 43 课之类切成 139 字。所以要求：行首 + 标题对得上。
    """
    pat = re.compile(r"^[ \t]*教你炒股票\s*(\d+)\s*[:：][ \t]*([^\n]{0,40})", re.M)
    hits: list[tuple[int, int]] = []
    for m in pat.finditer(whole):
        num = int(m.group(1))
        info = lessons.get(num)
        if not info:
            continue
        tail, title = m.group(2).strip(), info["title"]
        if title[:6] in tail or tail[:6] in title:      # 标题核对，挡掉正文引用
            hits.append((num, m.start()))
    sections: dict[int, str] = {}
    for i, (num, start) in enumerate(hits):
        end = hits[i + 1][1] if i + 1 < len(hits) else len(whole)
        sections.setdefault(num, whole[start:end])
    return sections


def _article_body(num: int, title: str) -> str:
    """取已经抓好的分课原文正文（比汇编里的转载可靠）。"""
    path = ART / f"{num:03d}-{_safe(title)}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    parts = text.split("\n---\n", 1)
    return parts[1].strip() if len(parts) == 2 else text.strip()


# ---- 评论来源三：新浪博客原站评论接口（最权威，带 UID）----
SINA_COMMENT = "https://blog.sina.com.cn/s/comment_{art}_{pg}.html"
SINA_CACHE = CACHE / "sina"
SINA_MAXPAGE = 80
SINA_HTML = re.compile(r"comment_([0-9a-z]+)_(\d+)_html")
SINA_LI = re.compile(r'<li[^>]*id="cmt_(\d+)"(.*?)</li>', re.S)


def load_art_ids() -> dict[int, str]:
    """从博客存档文件名里取每课的新浪文章 ID：<序号>-<文章ID>-<课号>.md"""
    out = {}
    for path in PLUS_DIR.glob("*.md"):
        m = re.match(r"(\d+)-([0-9a-z]+)-(\d{3})\.md$", path.name)
        if m:
            out[int(m.group(3))] = m.group(2)
    return out


def _parse_sina_data(data: str) -> list[dict]:
    """把一页评论 HTML 解析成结构化列表。

    注册用户的昵称是 `<a href=".../u/<uid>">昵称</a>`，所以 uid 要同时看
    `userid="…"` 和 href 里的 `/u/<uid>`，昵称要去标签。
    """
    out = []
    for pid, blk in SINA_LI.findall(data):
        n = re.search(r'id="nick_cmt_%s">(.*?)</span>' % pid, blk, re.S)
        d = re.search(r'<em class="SG_txtc">(.*?)</em>', blk, re.S)
        u = re.search(r'userid="(\d+)"', blk)
        b = re.search(r'id="body_cmt_%s">(.*?)</div>' % pid, blk, re.S)
        if not (n and d):
            continue
        body = html_mod.unescape(re.sub(r"<[^>]+>", "", b.group(1))) if b else ""
        body = body.replace("\u3000", " ").replace("&nbsp;", " ")
        raw_nick = n.group(1)
        href_uid = re.search(r"/u/(\d+)", raw_nick)
        out.append({"pid": pid,
                    "author": html_mod.unescape(re.sub(r"<[^>]+>", "", raw_nick)).strip(),
                    "uid": (u.group(1) if u else (href_uid.group(1) if href_uid else "0")),
                    "date": d.group(1).strip(),
                    "text": re.sub(r"[ \t]+", " ", body).strip()})
    return out


def _sina_page(art: str, pg: int) -> list[dict]:
    """取一页新浪评论。A00001 是限流假空，必须重试，否则会漏评论。"""
    cache_file = SINA_CACHE / f"{art}_{pg}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    url = SINA_COMMENT.format(art=art, pg=pg)
    for attempt, wait in enumerate([0, 1, 2, 3, 5, 8, 12]):
        if wait:
            time.sleep(wait)
        try:
            raw = fetch(url, refresh=(attempt > 0))
        except SystemExit:
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        data = doc.get("data") or ""
        if doc.get("code") != "A00006" or not data:
            continue
        out = _parse_sina_data(data)
        SINA_CACHE.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    return []          # 不缓存假空，下次还能重试


def sina_comments(num: int, art: str | None = None) -> list[dict]:
    """把一课的新浪原站评论全部翻页取回（50 条/页）。"""
    art = art or load_art_ids().get(num)
    if not art:
        return []
    out, misses = [], 0
    for pg in range(1, SINA_MAXPAGE + 1):
        page = _sina_page(art, pg)
        if not page:
            misses += 1
            if misses >= 2:          # 连续两页确认为空才收工
                break
            continue
        misses = 0
        out += page
        if len(page) < 50:           # 不满 50 条 = 最后一页，不用再探
            break
    return [dict(c, is_chan=(c["uid"] == CHAN_UID), src="新浪原站") for c in out]


def cmd_sina(args) -> int:
    """预热新浪原站评论缓存（慢，可断点续跑：命中缓存不重抓）。"""
    lessons = load_lessons()
    arts = load_art_ids()
    nums = [int(x) for x in args.codes.split(",")] if args.codes else sorted(lessons)
    total = 0
    for num in nums:
        got = sina_comments(num, arts.get(num))
        total += len(got)
        print(f"  {num:3d} 新浪原站 {len(got):4d} 条"
              f"（禅师 {sum(1 for c in got if c['is_chan'])}）")
    print(f"\n新浪原站评论缓存完成：{len(nums)} 课，共 {total} 条")
    return 0


# ---- 评论来源二：chzhshch-108-plus 博客存档（含禅师回复）----
PLUS_DIR = ROOT / "sources" / "chzhshch-108-plus" / "108"
PLUS_CMT = re.compile(r"UID:\[(\d*)\]\s*昵称：(.*?)\s*日期：\((.*?)\)")
CHAN_UID = "1215172700"
PLUS_COMMIT = "d2a87d5bd9c84baac2918edca668b9f82a20be52"
PLUS_URL = "https://github.com/stockServ/chzhshch-108-plus"


def _plus_comments(num: int) -> list[dict]:
    """读博客存档里第 num 课的评论。禅师回复带 UID，据此判定，不靠昵称猜。"""
    path = next(iter(sorted(PLUS_DIR.glob(f"*-{num:03d}.md"))), None)
    if path is None:
        return []
    text = path.read_text(encoding="utf-8")
    cut = text.find("**本文评论")
    if cut < 0:
        return []
    out = []
    for block in text[cut:].split("```"):
        m = PLUS_CMT.match(block.strip())
        if not m:
            continue
        uid, nick = m.group(1), m.group(2).strip()
        out.append({"author": nick, "date": m.group(3).strip(),
                    "text": block.strip()[m.end():].strip(),
                    "is_chan": uid == CHAN_UID or _is_chan(nick),
                    "src": "博客存档"})
    return out


def _stamp_key(date: str) -> tuple:
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}):(\d{2}))?", date)
    if not m:
        return (9999, 0, 0, 0, 0, 0)
    g = [int(x) if x else 0 for x in m.groups()]
    return tuple(g)


def _merge_comments(*sources: list[dict]) -> list[dict]:
    """三份来源互补（实测重合极少），按「时间 + 正文前 30 字」去重后按时间排序。

    传入顺序即优先级：新浪原站 > 博客存档 > 论坛汇编。
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for src in sources:                  # 优先级从高到低
        keep = [c for c in src
                if (c["date"], re.sub(r"\s+", "", c["text"])[:30]) not in seen]
        seen.update((c["date"], re.sub(r"\s+", "", c["text"])[:30]) for c in keep)
        out += keep                      # 同源内部重复一律保留，只跨源去重
    return sorted(out, key=lambda c: _stamp_key(c["date"]))


def cmd_comments(args) -> int:
    """生成每课的「原文 + 评论」，评论取两份公开来源的并集。

    汇编帖排版是 `[第N课标题+原文] [第N课的评论] [第N+1课标题+原文] …`，
    按标题切出来的每一段正好是「原文 + 它下面的评论」。原文一律改用 `原文/` 里
    的分课版本（汇编的转载偶有截断），只从汇编里取评论。
    """
    lessons = load_lessons()
    pages = thread_pages(COMPILE_TID, None)
    posts: list[dict] = []
    for pg in range(1, pages + 1):
        posts += parse_posts(fetch(THREAD.format(COMPILE_TID, pg)))
    gated = sum(1 for p in posts if p["gated"])
    whole = "\n".join(p["text"] for p in posts[2:])   # 去掉标题帖、目录帖
    sections = _lesson_sections(whole, lessons)

    REP.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    rows = []
    for num in sorted(lessons):
        info = lessons[num]
        section = sections.get(num)
        forum: list[dict] = []
        if section is None:
            print(f"  !! 第 {num} 课在汇编里没找到")
        else:
            parts = COMMENT_RE.split(section)
            for j in range(1, len(parts) - 1, 3):
                who, when, body = parts[j].strip(), parts[j + 1], parts[j + 2].strip()
                forum.append({"author": who, "date": when, "text": body,
                              "is_chan": _is_chan(who), "src": "论坛汇编"})
        plus = _plus_comments(num)
        sina = sina_comments(num)
        comments = _merge_comments(sina, plus, forum)
        chan = sum(1 for c in comments if c["is_chan"])
        body = _article_body(num, info["title"])
        lines = [
            f"# {info['full_title']} —— 原文与评论",
            "",
            f"- 课号：{num}",
            f"- 原文来源：{THREAD.format(info['tid'], 1)}",
            "- 评论来源（三份公开来源取并集）：",
            f"  - 新浪原站：{SINA_COMMENT.format(art=load_art_ids().get(num, '?'), pg=1).rsplit('_', 1)[0]}_N.html（原博客评论接口）",
            f"  - 博客存档：{PLUS_URL}（提交 `{PLUS_COMMIT[:7]}`，游客可读）",
            f"  - 论坛汇编：{THREAD.format(COMPILE_TID, 1)}（原新浪博客评论汇编，游客可读）",
            f"- 抓取日期：{stamp}",
            f"- 原文：{len(body)} 字",
            f"- 评论：{len(comments)} 条（缠中说禅本人 {chan} 条；"
            + " + ".join(f"{k} {sum(1 for c in comments if c['src'] == k)} 条"
                         for k in ("新浪原站", "博客存档", "论坛汇编")) + "）",
            "",
            "---",
            "",
            "## 原文",
            "",
            body,
            "",
            "## 评论",
            "",
        ]
        for c in comments:
            mark = "**缠中说禅**" if c["is_chan"] else c["author"]
            lines += [f"### {mark} · {c['date']} · {c['src']}", "", c["text"] or "（空）", ""]
        (REP / f"{num:03d}-{_safe(info['title'])}.md").write_text("\n".join(lines), encoding="utf-8")
        rows.append({"num": num, "body": len(body), "comments": len(comments), "chan": chan,
                     "sina": sum(1 for c in comments if c["src"] == "新浪原站"),
                     "plus": sum(1 for c in comments if c["src"] == "博客存档"),
                     "forum": sum(1 for c in comments if c["src"] == "论坛汇编")})
        if num % 20 == 0 or num <= 2:
            print(f"  {num:3d} 原文{len(body):5d}字 评论{len(comments):3d}条(禅师{chan:2d})")
    (ROOT / "comments_report.json").write_text(
        json.dumps({"sources": [{"kind": "新浪原站", "url": SINA_COMMENT.format(art="<文章ID>", pg="<页>")},
                                {"kind": "博客存档", "url": PLUS_URL, "commit": PLUS_COMMIT},
                                {"kind": "论坛汇编", "tid": COMPILE_TID, "pages": pages,
                                 "floors": len(posts), "gated_floors": gated}],
                    "lessons": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    total = sum(r["comments"] for r in rows)
    thin = [r["num"] for r in rows if r["body"] < 500]
    print(f"\n评论：{len(rows)} 课落盘，共 {total} 条，其中缠中说禅本人 "
          f"{sum(r['chan'] for r in rows)} 条；汇编帖 {pages} 页 {len(posts)} 楼（权限墙 {gated}）")
    print(f"原文 <500 字的课：{thin or '无'}")
    return 0


QA = ROOT / "问答"
CLOSED_COMMENTS = {34, 72, 80}   # 新浪接口返回「博主已关闭评论」的课


def _forum_sections() -> dict:
    """把汇编帖切成「课号 → 该课段落」。"""
    pages = thread_pages(COMPILE_TID, None)
    posts: list[dict] = []
    for pg in range(1, pages + 1):
        posts += parse_posts(fetch(THREAD.format(COMPILE_TID, pg)))
    whole = "\n".join(p["text"] for p in posts[2:])
    return _lesson_sections(whole, load_lessons())


def _render_qa(text: str) -> list[str]:
    """禅师回复常把读者提问原样引在开头，用一整行 `=` 分隔再作答。提问排成引用块。

    少数回复是「多问多答」（提问A / === / 回答A 提问B / === / 回答B），分隔行没法
    可靠地区分「回答结束」和「下一个提问开始」，所以只处理第一处，其余原样保留。
    """
    m = re.search(r"\n=+[ \t]*\n", text)
    if m:
        head, answer = text[:m.start()], text[m.end():]
        quoted = [ln.strip().lstrip("\t") for ln in head.splitlines() if ln.strip()]
        if quoted:
            return ["> " + q for q in quoted] + ["", answer.strip()]
    return [text.strip()]


def cmd_qa(_args) -> int:
    """把缠中说禅本人的回复单独抽出来 → `问答/` 与《禅师回复合集》。"""
    lessons = load_lessons()
    sections = _forum_sections()
    QA.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    whole_doc, rows = [], []
    for num in sorted(lessons):
        info = lessons[num]
        forum = []
        sec = sections.get(num)
        if sec:
            parts = COMMENT_RE.split(sec)
            for j in range(1, len(parts) - 1, 3):
                forum.append({"author": parts[j].strip(), "date": parts[j + 1],
                              "text": parts[j + 2].strip(), "is_chan": _is_chan(parts[j].strip()),
                              "src": "论坛汇编"})
        merged = _merge_comments(sina_comments(num), _plus_comments(num), forum)
        qa = [c for c in merged if c["is_chan"]]
        lines = [f"# {info['full_title']} —— 缠中说禅回复", "",
                 f"- 课号：{num}",
                 f"- 禅师回复：{len(qa)} 条（该课评论共 {len(merged)} 条）",
                 f"- 原文来源：{THREAD.format(info['tid'], 1)}",
                 f"- 抓取日期：{stamp}", "", "---", "", "## 缠中说禅回复", ""]
        for c in qa:
            lines += [f"### {c['date']} · {c['src']}", ""] + _render_qa(c["text"]) + [""]
        if not qa:                      # 空文件要说明原因，否则读者以为是漏抓
            if num in CLOSED_COMMENTS:
                why = ("这一课当年**博主关闭了评论**（新浪接口原文返回「博主已关闭评论」），"
                       f"现存的 {len(merged)} 条全部来自第三方转载汇编。")
            else:
                why = (f"这一课评论区里没有缠中说禅本人（该课 {len(merged)} 条评论全部出自读者）。"
                       "他本人的回复存档只到 2007-08-09；从第 101 课《答疑1》起，"
                       "他改成**在正文里集中答疑**，那部分问答在 `原文/` 的正文中。")
            lines += ["> " + why, ""]
        (QA / f"{num:03d}-{_safe(info['title'])}.md").write_text("\n".join(lines), encoding="utf-8")
        if qa:
            whole_doc += [f"## 第 {num} 课　{info['title']}", ""]
            for c in qa:
                whole_doc += [f"### {c['date']}"] + _render_qa(c["text"]) + [""]
        rows.append({"num": num, "qa": len(qa), "comments": len(merged)})
    head = ["# 缠中说禅《教你炒股票》108 课 —— 禅师回复合集", "",
            f"- 来源：新浪博客原站评论接口 + 公开存档并集（见 README）",
            f"- 抓取日期：{stamp}",
            f"- 禅师回复合计：{sum(r['qa'] for r in rows)} 条，覆盖 "
            f"{sum(1 for r in rows if r['qa'])} 课",
            "- 说明：禅师回复里常把读者提问原样引用在开头，这里排成引用块（`>`）以便对照；",
            "  提问原文未做改写，只去掉了行首制表符。", "", "---", ""]
    (ROOT / "禅师回复合集-全108课.md").write_text("\n".join(head + whole_doc), encoding="utf-8")
    (ROOT / "qa_report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    print(f"问答：{sum(1 for r in rows if r['qa'])} 课有禅师回复，共 {sum(r['qa'] for r in rows)} 条"
          f" → 问答/ 与 禅师回复合集-全108课.md")
    empty = [r["num"] for r in rows if not r["qa"]]
    print(f"无禅师回复的课 {len(empty)} 门：{empty}")
    return 0


def cmd_bundle(_args) -> int:
    """把「单帖超链接版」的全文落成一个文件，方便 Ctrl+F 检索。"""
    tid = "11345"
    posts = parse_posts(fetch(THREAD.format(tid, 1)))
    body = max((p for p in posts if not p["gated"]), key=lambda p: len(p["text"]), default=None)
    if not body:
        raise SystemExit("单帖版没抓到正文")
    stamp = time.strftime("%Y-%m-%d")
    out = ROOT / "原文合集-全108课-单文件.md"
    out.write_text("\n".join([
        "# 教你炒股票 108 课（原文全文·单文件）",
        "",
        f"- 来源：{THREAD.format(tid, 1)}",
        f"- 抓取日期：{stamp}",
        "- 说明：本站汇编帖，全文一篇，便于全文检索；分课版本见 `原文/`。",
        "",
        "---",
        "",
        body["text"],
        "",
    ]), encoding="utf-8")
    print(f"单文件全文：{out.name}，{len(body['text'])} 字")
    return 0


def cmd_index(_args) -> int:
    """汇总成可检索的索引（Markdown 表 + CSV）。"""
    lessons = load_lessons()
    dates: dict[int, str] = {}
    for path in ART.glob("*.md"):
        head = path.read_text(encoding="utf-8")[:600]
        d = re.search(r"- 原博客发表：(.*)", head)
        if d:
            dates[int(path.name[:3])] = d.group(1).strip()
    crep = {}
    if (ROOT / "comments_report.json").exists():
        doc = json.loads((ROOT / "comments_report.json").read_text(encoding="utf-8"))
        crep = {r["num"]: r for r in doc["lessons"]}
    rows = []
    for num in sorted(lessons):
        info = lessons[num]
        c = crep.get(num, {})
        rows.append({
            "num": num, "title": info["title"], "date": dates.get(num, ""),
            "tid": info["tid"], "chars": c.get("body", c.get("chars", 0)),
            "comments": c.get("comments", 0), "chan": c.get("chan", 0),
            "file": f"原文/{num:03d}-{_safe(info['title'])}.md",
            "reply_file": f"回复/{num:03d}-{_safe(info['title'])}.md" if c else "",
            "qa_file": f"问答/{num:03d}-{_safe(info['title'])}.md" if c else "",
        })
    md = ["# 《教你炒股票》108 课索引", "",
          f"- 来源：{SITE}（缠师讲坛）",
          f"- 生成日期：{time.strftime('%Y-%m-%d')}",
          f"- 原文：108 课全；评论：{sum(r['comments'] for r in rows)} 条，"
          f"其中缠中说禅本人 {sum(r['chan'] for r in rows)} 条",
          "- 「评论」为该课全部评论条数，「禅师回复」为其中缠中说禅本人所写；",
          "- 一字未删，都是站点原文，没有任何改写或解读", "",
          "| 课 | 标题 | 原博客发表 | 原文 | 评论 | 禅师回复 | 禅师回复单篇 |",
          "|---:|---|---|---:|---:|---:|---|"]
    for r in rows:
        ch = f"{r['chan']}" if r["chan"] else "—"
        md.append(f"| {r['num']} | [{r['title']}](<{r['file']}>) | {r['date']} | "
                  f"{r['chars']} | {r['comments']} | {ch} | "
                  f"{('[问答](<' + r['qa_file'] + '>)') if r['chan'] else '—'} |")
    (ROOT / "索引.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    import csv
    with (ROOT / "索引.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    gap = [r["num"] for r in rows if not r["comments"]]
    print(f"索引：{len(rows)} 课 → 索引.md / 索引.csv；公开汇编缺评论的课 {len(gap)} 门：{gap}")
    return 0


def cmd_check(_args) -> int:
    lessons = load_lessons()
    rep = ROOT / "articles_report.json"
    rows = {r["num"]: r for r in json.loads(rep.read_text(encoding="utf-8"))} if rep.exists() else {}
    files = {int(p.name[:3]): p for p in ART.glob("*.md")} if ART.exists() else {}
    missing = [n for n in lessons if n not in files]
    short = [(n, rows[n]["chars"]) for n in rows if not rows[n]["gated"] and rows[n]["chars"] < 300]
    print(f"目录 {len(lessons)} 课 | 原文文件 {len(files)} 个 | 缺 {missing or '无'}")
    print(f"正文 <300 字的课：{short or '无'}")
    # 存档卫生：正文里不该残留游客横幅 / 权限墙字样
    dirty = []
    for n, path in sorted(files.items()):
        text = path.read_text(encoding="utf-8")
        if GATE in text or "才可以下载或查看" in text:
            dirty.append(n)
    sizes = sorted(len(p.read_text(encoding="utf-8")) for p in files.values())
    print(f"残留横幅/权限墙的文件：{dirty or '无'} | 文件大小 {sizes[0]}–{sizes[-1]} 字节")
    rp = ROOT / "comments_report.json"
    if rp.exists():
        doc = json.loads(rp.read_text(encoding="utf-8"))
        crep = doc["lessons"]
        files_r = len(list(REP.glob("*.md"))) if REP.exists() else 0
        total = sum(r["comments"] for r in crep)
        chan = sum(r["chan"] for r in crep)
        gap = [r["num"] for r in crep if not r["comments"]]
        print(f"评论：{files_r} 个文件 | {total} 条（禅师 {chan} 条）| "
              f"无评论的课 {len(gap)} 门：{gap}")
        labels = {"新浪原站": ("新浪原站（原博客评论接口）", "sina"),
                  "博客存档": ("公开博客存档", "plus"),
                  "论坛汇编": ("论坛汇编帖", "forum")}
        for s_ in doc.get("sources", []):
            title, field = labels.get(s_["kind"], (s_["kind"], "plus"))
            extra = ""
            if s_.get("commit"):
                extra = f" @{s_['commit'][:7]}"
            elif s_.get("tid"):
                extra = (f" tid={s_['tid']} {s_.get('pages', '?')} 页 "
                         f"{s_.get('floors', '?')} 楼，权限墙 {s_.get('gated_floors', 0)} 楼")
            print(f"  来源·{title}{extra}，贡献 {sum(r.get(field, 0) for r in crep)} 条")
        diff = []
        for r in crep:
            path = next(iter(sorted(PLUS_DIR.glob(f"*-{r['num']:03d}.md"))), None)
            if not path:
                continue
            t = path.read_text(encoding="utf-8")
            cut = t.find("**本文评论")
            plen = len(t[:cut].strip()) if cut > 0 else len(t)
            if r["body"] and abs(plen - r["body"]) / r["body"] > 0.2:
                diff.append((r["num"], r["body"], plen))
        print(f"原文交叉核对（本归档 vs 博客存档，差 >20% 的课）："
              f"{[d[0] for d in diff] or '无'}")
        for n, a, b in diff:
            print(f"    {n:3d}: 本归档 {a} 字 / 博客存档 {b} 字")
    else:
        print("评论：还没抓（见 README）")
    rrep = ROOT / "replies_report.json"
    if rrep.exists():
        rrows = json.loads(rrep.read_text(encoding="utf-8"))
        gated = [r["num"] for r in rrows if r["gated"]]
        print(f"登录态回复：{len(rrows)} 课 | 仍有权限墙的课 {len(gated)} 门：{gated or '无'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取《教你炒股票》108 课")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("catalog").set_defaults(fn=cmd_catalog)
    sub.add_parser("articles").set_defaults(fn=cmd_articles)
    p = sub.add_parser("replies")
    p.add_argument("--cookie", help="含 Cookie 头的文本文件")
    p.set_defaults(fn=cmd_replies)
    sub.add_parser("comments").set_defaults(fn=cmd_comments)
    p = sub.add_parser("sina")
    p.add_argument("--codes", default="", help="只抓这些课，逗号分隔；缺省全 108 课")
    p.set_defaults(fn=cmd_sina)
    sub.add_parser("bundle").set_defaults(fn=cmd_bundle)
    sub.add_parser("index").set_defaults(fn=cmd_index)
    sub.add_parser("qa").set_defaults(fn=cmd_qa)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
