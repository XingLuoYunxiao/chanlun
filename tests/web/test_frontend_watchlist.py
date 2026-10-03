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


def _guarded_block(body: str, header: str) -> str:
    """抠出 `header` 那个 if 后面的块（按花括号配平）。"""
    at = body.index(header)
    return _fn_body(body[at:], header)


def _call_args(js: str, name: str, must_contain: str | None = None) -> str:
    """抠出 `name(...)` 的实参（跳过 `function name(...)` 定义处）。

    同名函数常有多处调用（`setStamp` 就有「加载中…」「—」和主图注三处），
    所以可以用 `must_contain` 指定实参里必须出现的标记来选中目标那一处。
    """
    start = 0
    while True:
        at = js.index(f"{name}(", start)
        if js[:at].rstrip().endswith("function"):
            start = at + 1
            continue
        i = js.index("(", at)
        depth = 0
        for j in range(i, len(js)):
            if js[j] == "(":
                depth += 1
            elif js[j] == ")":
                depth -= 1
                if depth == 0:
                    args = js[i + 1 : j]
                    break
        else:
            raise AssertionError(f"调用没配平: {name}")
        if must_contain is None or must_contain in args:
            return args
        start = at + 1


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


# ---------------- 周线 / 月线（Request A） ----------------
def test_period_buttons_include_week_and_month():
    html = _html()
    js = _js()
    for period in ("week", "month"):
        assert f'data-period="{period}"' in html, f"{period} 没有切换按钮"
        assert f'{period}:' in js or f'"{period}"' in js, f"PERIOD_CN 少了 {period}"
    for word in ("周线", "月线"):
        assert word in html
    # 按钮上的字和 PERIOD_CN 必须一致，否则图注和按钮会各说各的
    assert "周线" in js and "月线" in js


def test_derived_periods_say_they_come_from_day_bars():
    """周/月由日线聚合，图注要说出来；最后一根没走完时必须标「未走完」。"""
    js = _js()
    assert "DERIVED_CN" in js or "base_period" in js
    assert "未走完" in js
    assert "body.partial" in js
    assert "body.base_period" in js


# ---------------- 图注：代码 + 名称（Request A） ----------------
def test_stamp_shows_the_name_next_to_the_code():
    """只给 6 位数字，看盘时认不出是哪只票 —— 名称必须出现在图注里。"""
    js = _js()
    assert "body.name" in js
    line = [l for l in js.splitlines() if "截至" in l and "body.code" in l]
    assert line, "找不到图注那一行"
    assert "body.name" in line[0], f"图注里没带名称：{line[0]!r}"


# ---------------- 顶栏那一行必须恒定（Request B） ----------------
def test_topbar_stamp_is_pinned_to_two_short_lines():
    """切日/周/月、切严格/非严格时，顶栏那一行不许自适应变动。

    顶栏是 flex 行：里面任何一个元素的**宽度或行数**一变，就会去挤 `.period`
    按钮；按钮是 `flex: 0 1 auto`，被挤到内容宽度以下就折行，整行高度跟着跳
    （实测 36px → 56px，顶栏 93px → 110px）。

    所以判据是：**会变长、会时有时无的文案不许进顶栏**。
    「全史」计数与口径说明必须写在顶栏之外的 `#stamp-detail` 里。
    """
    js, html = _js(), _html()
    args = _call_args(js, "setStamp", must_contain="body.code")
    assert "basisLine" not in args, f"口径说明又跑回顶栏了：{args!r}"
    assert "全史" not in args, f"「全史」计数又跑回顶栏了：{args!r}"
    # 源码里正好一个 `\n` 分隔 -> 恒定 2 行（多一行就多一次高度跳的机会）
    assert args.count("\\n") == 1, f"顶栏图注不是恒定 2 行：{args!r}"
    # 详情行必须在 topbar **之外**，否则它照样是那一行的一部分
    assert 'id="stamp-detail"' in html, "index.html 里没有详情行"
    assert html.index('id="stamp-detail"') > html.index("</header>"), "详情行还在顶栏里面"
    assert "stamp-detail" in js, "app.js 没有往详情行写东西"


# ---------------- 趋势口径标注（决策 #2） ----------------
def test_trend_basis_is_annotated_on_the_page():
    """趋势判据只判 ZG/ZD 单调，第20课定理二说的是 [DD,GG]；页面上不许出现光秃秃的「趋势」。"""
    js = _js()
    assert "TREND_BASIS" in js
    assert "[ZD,ZG]" in js and "[DD,GG]" in js
    assert "TREND_BASIS" in js.split("setStamp(")[1] or "basisLine" in js


# ---------------- 自选股行：中枢日期区间 + 未确认买卖点（决策 #3 / G11e） ----------------
def test_watch_row_prints_the_last_pivot_with_its_date_range():
    """「中枢 3.40–3.86」配现价 8.28，不写日期读的人会以为是当下的中枢。"""
    js = _js()
    assert "piv.start_ts" in js and "piv.end_ts" in js
    assert "dateRange" in js


def test_watch_row_marks_tentative_signals():
    js = _js()
    assert "last.status" in js
    assert "（未确认）" in js


# ---------------- 自选池 CRUD（Request B） ----------------
def test_watch_rows_have_up_and_down_buttons():
    js = _js()
    css = _css()
    assert "watch-move" in js and "watch-mv" in js
    assert 'method: "PATCH"' in js
    assert "delta" in js
    assert ".watch-move" in css
    # 首尾置灰：边界上后端是空操作，界面不能做出"点了会动"的样子
    assert "disabled" in js


def test_watch_foot_no_longer_claims_only_day_bars_are_local():
    html = _html()
    assert "默认是七个大盘指数" in html

def test_a_row_without_data_is_still_clickable():
    """「这个周期没数据」只该影响那一行显示的价格，**不该把点击一起关掉**。

    自选池现在全是 7 个大盘指数，而 baostock 对指数不返回分钟线 —— 所以一切到
    30分/5分，整栏每一行都是「未同步」。如果换票的绑定写在 `if (!it.missing)` 里面，
    这时整栏就点不动了：用户点了自选股，页面纹丝不动，看起来像"看不了自选股"。
    （实测：`?period=30` 下点第 3 行，输入框仍是 sh.000001，图表不变。）
    """
    body = _fn_body(_js(), "function finishWatchRow(")
    assert "setCode(it.code)" in body, "自选行点一下必须能换票"
    if "if (!it.missing)" in body:
        guarded = _guarded_block(body, "if (!it.missing)")
        assert "setCode" not in guarded, "换票的绑定不能被 !missing 关掉"


def test_a_row_without_data_still_looks_clickable():
    """视觉上也不能把"没数据"画成"不能点"：光标要是手型。"""
    css = _css()
    at = css.index(".watch-row.is-missing")
    rule = css[at : css.index("}", at)]
    assert "cursor: default" not in rule, "没数据的行仍然可以点开看"


def test_a_failed_load_still_moves_the_rail_highlight():
    """404（这个周期没数据）也要更新自选栏高亮 —— 否则点完票，输入框已经是新票，
    左栏却还高亮着上一只，「图上是哪一只」就成了假的。"""
    load = _fn_body(_js(), "async function load(")
    bad = load[load.index("if (!resp.ok)") : load.index("state.data = body")]
    assert "renderWatch()" in bad, "404 分支也要把左栏高亮挪过去"

