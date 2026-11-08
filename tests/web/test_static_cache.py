"""静态资源与首页必须带 `cache-control: no-cache`。

2026-10-11 实测（`curl -sI http://127.0.0.1:8888/static/app.js`）：只有
`last-modified` + `etag`，**没有** `Cache-Control`。Chrome 对没有显式新鲜度信息的
响应可以按启发式规则（`(now - last_modified) * 0.1`）自己算一个窗口直接拿缓存 ——
于是"前端改了、用户看的是旧页面"。这正是 D-49 上线后用户报
「也没有自动拉取，也没有手动拉取的按钮」的成因之一。

`no-cache` **不是**"不缓存"：浏览器照存，但每次带 `If-None-Match` 回来校验一句，
没变还是 304（省流量），变了立刻拿到新的。所以第三条测试必须存在 ——
它证明"加了 no-cache 之后 304 这条省钱的路还在"。
"""

from __future__ import annotations


def test_static_files_are_revalidated_every_time(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache", (
        "没有显式新鲜度信息时，Chrome 可以按启发式规则直接拿缓存里的旧 app.js"
    )


def test_the_index_is_revalidated_every_time(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_a_revalidated_static_file_still_gets_a_304(client):
    """`no-cache` 不许把 304 这条省钱的路堵死：没变就该只回一个头。"""
    etag = client.get("/static/app.js").headers["etag"]
    r = client.get("/static/app.js", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.headers.get("cache-control") == "no-cache", (
        "304 也要带 no-cache：Starlette 的 NotModifiedResponse 只白名单几个头，"
        "在 get_response 之后补才不会被它过滤掉"
    )
