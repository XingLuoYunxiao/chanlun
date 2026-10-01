"""前端契约：「同步这个周期」按钮（Task 32）。

页面遇到「这个周期本地没数据」时，光印一行命令行（`python -m chanlun sync --period 30
--codes 600759`）等于把人赶回终端。契约是：

1. 404 的提示里要有一个**能点的按钮**，文案是「同步这个周期」；
2. 首次拉 5 分钟线实测 149 秒，所以点下去**不能等请求返回**：POST 之后按固定间隔轮询
   `/api/sync/status`，并且轮询有上限（不能无限转圈）；
3. 跑的时候按钮**禁用**，避免连点堆出好几个任务；
4. 失败要给「重试」+ 原因；而 `POST` 直接 400（例如「指数没有分钟线」）是确定性拒绝，
   再点一百次也一样，所以那种情况**不给**重试按钮；
5. `app.js` 必须能过 `node --check`：这份脚本一有语法错整页白屏，静态字符串断言看不出来。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "chanlun" / "web" / "static"


def _html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def _js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _css() -> str:
    return (STATIC / "styles.css").read_text(encoding="utf-8")


def _fn_body(js: str, header: str) -> str:
    """抠出一个函数的函数体（按花括号配平）。找不到就报错。"""
    start = js.index(header)
    i = js.index("{", start)
    depth = 0
    for j in range(i, len(js)):
        if js[j] == "{":
            depth += 1
        elif js[j] == "}":
            depth -= 1
            if depth == 0:
                return js[i : j + 1]
    raise AssertionError(f"函数没配平: {header}")


def test_app_js_parses():
    node = shutil.which("node")
    if node is None:  # pragma: no cover - 本机有 node
        pytest.skip("没有 node")
    proc = subprocess.run([node, "--check", str(STATIC / "app.js")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_notice_box_can_carry_an_action_button():
    js = _js()
    css = _css()
    assert "function showNotice(title, detail, command, action)" in js, "提示框要能挂一个按钮"
    assert "notice-action" in js
    assert ".notice-action" in css


def test_404_offer_a_sync_button():
    body = _fn_body(_js(), "async function load()")
    assert "syncThisPeriod" in body, "「没有本地数据」的提示必须给出路"
    assert "404" in body, "只有 404（这个周期真没数据）才给同步按钮"
    assert "同步这个周期" in _js()


def test_sync_button_posts_then_polls_instead_of_waiting():
    body = _fn_body(_js(), "async function syncThisPeriod()")
    js = _js()
    assert '"/api/sync"' in body and 'method: "POST"' in body
    assert "/api/sync/status" in js, "POST 只负责排队，结果靠轮询拿"
    assert re.search(r"SYNC_POLL_MS\s*=\s*\d+", js), "轮询间隔必须是显式常量"
    assert re.search(r"SYNC_MAX_MS\s*=\s*\d+", js), "轮询必须有上限"


def test_sync_button_is_disabled_while_running():
    body = _fn_body(_js(), "async function syncThisPeriod()")
    assert "disabled = true" in body
    assert "正在同步" in _js(), "跑的时候要说清在干什么、已经多久"


def test_a_rejected_sync_does_not_offer_retry():
    """`POST /api/sync` 400 = 确定性拒绝（如指数+分钟），重试没有意义。"""
    body = _fn_body(_js(), "async function syncThisPeriod()")
    assert "无法同步" in body
    assert "remove()" in body, "拒绝时要把按钮撤掉，别摆一个点了没用的东西"


def test_a_failed_sync_offers_retry_with_the_reason():
    body = _fn_body(_js(), "function finishSync(")
    assert "重试" in body
    assert "status.error" in body or "error" in body
    assert "skipped" in body, "源返回零行要说「没有返回数据」，不能画成成功"


def test_a_successful_sync_redraws_the_chart():
    body = _fn_body(_js(), "function finishSync(")
    assert "load()" in body, "数据到位了必须重画，不能停在提示上"
    assert "status.rows" in body, "同步完要说清楚拿回来多少根"
