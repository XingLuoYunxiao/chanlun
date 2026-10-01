"""前端契约：左侧常驻自选股栏 + 均线开关 + 复权三态按钮。

页面是「自选股点一下就看结构」的入口，所以这些断言盯的都是**会让人看错数据**的地方：

1. 自选股栏必须是**常驻左栏**（`#watchlist` 在 `main.stage` 之前），而不是页面底部的
   一条带子 —— 看盘时要在不滚动页面的前提下换票；
2. 均线六档（5/10/20/60/120/250）**每一档都有独立开关**：长周期线在 1200 根窗口里
   只是背景噪声，不能替用户决定；
3. 复权必须能切三态（不复权/前复权/后复权），且按钮上的字就是**实际生效**的口径；
4. `app.js` 必须能通过 `node --check`：这份脚本一有语法错，整页就是白屏，
   而静态字符串断言完全看不出来。
"""
from __future__ import annotations

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


# ---------------- 左侧常驻自选股栏 ----------------
def test_watchlist_is_a_left_rail_before_the_chart():
    html = _html()
    assert 'id="watchlist"' in html
    assert 'class="watch-rail"' in html
    watch = html.index('class="watch-rail"')
    stage = html.index('class="stage"')
    layers = html.index('class="rail"')
    assert layers < watch < stage, "自选股栏要在图层开关右边、主图左边"


def test_watchlist_rail_owns_the_add_form_and_the_rows():
    html = _html()
    start = html.index('class="watch-rail"')
    end = html.index('class="stage"')
    rail = html[start:end]
    assert 'id="watch-rows"' in rail
    assert 'id="watch-code"' in rail
    assert 'id="watch-add"' in rail


def test_bottom_watchstrip_is_removed():
    """底部那条自选带子必须删掉：两处渲染同一个自选池，早晚一处新一处旧。"""
    html = _html()
    assert "watchstrip" not in html
    assert "watch-cards" not in html


def test_watchlist_has_a_name_search_datalist():
    html = _html()
    assert 'list="universe-options"' in html
    assert 'id="universe-options"' in html


# ---------------- 均线开关 ----------------
def test_ma_toggles_exist_for_the_six_periods():
    html = _html()
    assert "均线" in html
    assert html.count('class="ma-toggle"') == 6
    for period in (5, 10, 20, 60, 120, 250):
        assert f'data-period="{period}"' in html, f"MA{period} 没有开关"
    assert html.count('type="checkbox"') >= 6


def test_ma_toggles_carry_their_own_colour():
    html = _html()
    assert html.count("--c:") >= 6, "每档均线要有自己的颜色点，否则开关只是个勾选框"


# ---------------- 复权三态按钮 ----------------
def test_adjust_button_exists_and_the_three_modes_are_named():
    html = _html()
    js = _js()
    assert 'id="adjust-btn"' in html
    for word in ("不复权", "前复权", "后复权"):
        assert word in js, f"复权口径少了「{word}」"
    assert "ADJUST_ORDER" in js
    for mode in ("qfq", "hfq", "raw"):
        assert f'"{mode}"' in js


def test_adjust_button_label_comes_from_the_backend_effective_mode():
    """按钮上的字必须来自响应的 `adjust_effective`，不能只显示请求值。

    没有除权记录的票，请求「前复权」与「不复权」价格完全一样；这时按钮仍写「前复权」
    会让人以为价差是被复权算掉的。
    """
    js = _js()
    assert "adjust_effective" in js
    assert "adjust_note" in js


# ---------------- app.js 契约 ----------------
def test_requests_carry_adjust_and_ma():
    js = _js()
    assert "/api/structure?" in js
    assert "maQuery" in js
    assert 'set("adjust"' in js or "adjust=" in js
    assert 'set("ma"' in js or "ma=" in js


def test_watch_rows_are_clickable_and_load_the_chart():
    js = _js()
    assert "watch-row" in js
    assert "dataset.code" in js
    assert "/api/watchlist/structure" in js
    assert "setCode(it.code)" in js


def test_ma_lines_are_drawn_from_the_backend_payload():
    """均线只算一次：序列直接取后端的 `ma`，前端不许自己 rolling。"""
    js = _js()
    assert "d.ma" in js
    assert "MA_COLORS" in js
    # 找的是"调用"（rolling() ），不是这个词本身：注释里说明为什么不该自己算，
    # 恰恰是希望被读到的；真正要拦住的是前端自己 close.rolling(5)。
    assert "rolling(" not in js
    assert ".rolling" not in js


def test_choices_are_remembered():
    js = _js()
    assert "localStorage" in js
    assert "chanlun.ma" in js
    assert "chanlun.adjust" in js


def test_watchlist_request_follows_the_adjust_mode():
    js = _js()
    assert "adjust=${state.adjust}" in js


# ---------------- 布局与语法 ----------------
def test_css_has_four_columns_and_a_scrolling_rail():
    css = _css()
    assert ".watch-rail" in css
    assert "overflow-y: auto" in css
    assert ".watch-row" in css
    assert ".ma-toggle" in css
    work = css.split(".work {")[1].split("}")[0]
    assert work.count(" ") >= 3, f".work 应当是四栏：{work!r}"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 做语法检查")
def test_app_js_parses():
    proc = subprocess.run(["node", "--check", str(STATIC / "app.js")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_watchlist_rail_renders_the_unadjusted_price():
    """自选栏那一格用 `rail_close`（不复权成交价），不是图表口径的 `close`。

    两个字段名字很像，前端一旦取错，用户在后复权模式下会看到中信证券报 31.82 ——
    和券商里的 25.86 对不上，且没有任何提示。
    """
    js = _js()
    assert "it.rail_close" in js
    assert "it.rail_change_pct" in js
