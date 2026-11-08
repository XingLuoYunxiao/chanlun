"""前端契约：换票/换指数时自动补最新（D-49）。

`app.js` 没有构建步骤、也没有前端测试框架，所以契约像
`tests/web/test_frontend_sync_button.py` 一样用**静态断言**守：咬住"规矩"而不是
"实现长相"——「判据来自后端」「同时只飞一个」「轮询要问发起时那一只」这些
断言在重构后仍然应该成立。

静态断言必须**先去掉注释**再咬，否则解释性注释会被当成代码：写一句
「这里原先读的是画面上的当前 code/period」就会把「函数体里不许出现它」咬红，
那种红是**假红**，而假红会训练后来的人删掉注释。`_strip_comments` 就是这个用途，
它自己也有一条测试（`test_strip_comments_removes_comments_but_keeps_code`）。
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


def _strip_comments(src: str) -> str:
    """去掉 `//` 与 `/* */` 注释，字符串/模板串里的内容原样保留。

    够用就行的扫描器：它只需要在 `app.js` 这种没有正则字面量歧义的脚本上正确地
    "把注释变空、把代码留住"。**留着字符串**，因为断言里那些文案（`"已补到"`）
    正是要咬住的东西。
    """
    out: list[str] = []
    i, n = 0, len(src)
    quote: str | None = None
    while i < n:
        ch = src[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:      # 转义：下一个字符原样带走
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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


def _fn_code(js: str, header: str) -> str:
    """函数体，且**只看真实代码**（注释已去掉）。"""
    return _strip_comments(_fn_body(js, header))


def test_strip_comments_removes_comments_but_keeps_code():
    """先证明这把尺子本身是准的：注释变空、代码和字符串一字不动。"""
    got = _strip_comments('a(); // b();\n/* c() */ d("// 不是注释");')
    assert "b()" not in got and "c()" not in got
    assert "a();" in got and "d(" in got
    assert "// 不是注释" in got, "字符串里的内容不是注释，不许被吃掉"


def test_app_js_parses():
    node = shutil.which("node")
    if node is None:  # pragma: no cover - 本机有 node
        pytest.skip("没有 node")
    proc = subprocess.run(
        [node, "--check", str(STATIC / "app.js")], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


def test_the_chip_lives_inside_main_not_in_the_body_grid():
    """`body` 网格行数必须等于 `body` 的直接子元素数（`styles.css:51-58`，
    既有测试 `test_body_grid_rows_cover_every_body_child` 守着）。chip 只能待在
    `<main class="stage">` 里 —— 那是个 flex 列，绝对定位的子元素不占格。"""
    html = _html()
    at = html.index('id="sync-chip"')
    assert html.index('<main class="stage">') < at < html.index("</main>")


def test_the_chip_does_not_take_up_flow():
    css = _css()
    at = css.index(".sync-chip {")
    block = css[at : css.index("}", at)]
    assert "position: absolute" in block, "占流的元素会把图表挤小"
    assert "pointer-events: none" in block, "平时不许挡住图上的交互"


def test_the_chip_is_a_separate_box_from_the_notice_panel():
    """`#notice` 是 `inset: 56px 24px 24px` 盖满图表的面板（`styles.css:343`），
    它表达的是"这个周期没有数据"。"有数据、正在后台补"必须另有一处，否则图被糊住。"""
    html = _html()
    assert 'id="sync-chip"' in html and 'id="notice"' in html
    assert "sync-chip" not in _fn_code(_js(), "function showNotice(")


def test_the_chip_is_hidden_by_default():
    html = _html()
    at = html.index('id="sync-chip"')
    assert "hidden" in html[max(0, at - 120) : at + 120]


def test_poll_asks_about_the_job_it_started_not_whatever_is_on_screen():
    """同步途中换票，轮询**不许**跟着换成新票：`/api/sync/status` 对未知任务回 `idle`，
    会被读成"同步成功、0 根"，而真正那次还在跑。"""
    body = _fn_code(_js(), "async function pollSync(")
    assert "job.code" in body and "job.period" in body
    assert "state.code" not in body and "state.period" not in body


def test_the_verdict_comes_from_the_backend():
    """前端**不许**自己比日期，也不许自己按号段猜指数 —— 只用后端给的结论。"""
    body = _fn_code(_js(), "function noteFreshness(")
    assert "body.stale !== true" in body
    assert "body.syncable === false" in body
    assert body.index("stale") < body.index("requestAutoSync"), "先判落后再谈同步"
    assert "new Date" not in body and "toISOString" not in body, "前端不比日期"
    assert 'chipState("fresh"' in body, "不落后也要如实说一句「已是最新」，不许静默"
    assert 'chipState("blocked"' in body and "body.sync_hint" in body, (
        "后端说补不了时要说明为什么，而不是什么都不显示"
    )


def test_a_stale_structure_kicks_off_a_fetch_on_its_own():
    load = _fn_code(_js(), "async function load(")
    assert "noteFreshness(body)" in load, "结构到手后就要把后端结论写到浮标上"


def test_an_empty_period_also_kicks_off_a_fetch():
    """「本地一条都没有」是最该自己去补的情形，不该只留给用户点按钮。"""
    load = _fn_code(_js(), "async function load(")
    at = load.index("404")
    assert "requestAutoSync(" in load[at:], "404 分支也要自动发起"


def test_auto_sync_is_attempted_at_most_once_per_session():
    """失败/零行之后不自动重试（退避），只留手动按钮；否则每次 `load()` 都去撞一次。"""
    body = _fn_code(_js(), "function runAutoSync(")
    assert "autoTried.add" in body
    assert body.index("autoTried.add") < body.index("finishAutoSync"), (
        "必须在收尾之前记：成功后 finishAutoSync 会 load() 回来，不先记就会自己触发自己"
    )
    req = _fn_code(_js(), "function requestAutoSync(")
    assert "autoTried.has" in req


def test_the_pending_slot_keeps_only_the_latest_job():
    req = _fn_code(_js(), "function requestAutoSync(")
    assert "autoBusy" in req, "同时只飞一个"
    assert "autoPending = job" in req, "待办槽是一个，不是队列：只保留最新一只"
    js = _js()
    assert "autoPending.push" not in _strip_comments(js), "别把它做成队列：用户只想看手里这一只"
    assert "autoTried = new Set()" in js


def test_a_finished_fetch_only_redraws_if_we_are_still_looking_at_it():
    body = _fn_code(_js(), "function finishAutoSync(")
    assert "state.key === job.code" in body and "state.period === job.period" in body
    assert "state.code" not in body, (
        "带前缀的书签（`?code=sz.600759`）下 `state.code` 与落库键不同形态，"
        "拿它比会把「还看着」误判成「已经换走」，于是补完不重画"
    )
    assert "load()" in body


def test_code_identity_is_always_the_stored_key_never_the_raw_input():
    """全站不变量：比"这是不是同一只"一律用落库键 `state.key`。

    `state.code` 是**用户输入的原样**：书签可以是 `?code=sz.002898`，而服务端回的
    落库键是 `002898`（指数反过来必须带前缀，否则和 `sz.000001` 平安银行撞号）。
    拿输入原样比身份，2026-10-10 实测出三处同根因缺陷：浮标一个字都不显示、
    补完不重画（顶栏停在 10-08 而浮标说"已补到 10-09"）、自选栏高亮失效。
    """
    code = _strip_comments(_js())
    assert "=== state.code" not in code and "!== state.code" not in code, (
        "身份比较必须用 state.key；请改用 state.key，不要新增 state.code 的比较"
    )
    assert "state.key = body.code" in code, "成功响应到了要升级成落库键"
    assert "state.key = state.code" in code, "404 没有落库键可回，只能按请求原样记"
    # **赋值形态也要咬**：`const code = state.code` 不是比较，上面那条 `=== state.code`
    # 断言咬不到它，可它同样把「这是哪一只」判错 —— 2026-10-10 真浏览器实测：
    # `?code=sh.600759`（自选栏里存的是裸 `600759`）自选按钮一直显示「☆ 加自选」、
    # 自选行不高亮，点一下还会把同一只票加第二遍。这条断言是后来补的，
    # 补它之前那个变异（`state.key` → `state.code`）**能全绿通过**。
    assert "const code = state.code" not in code, (
        "自选按钮（`renderFav` 与点击处理器）解析「这一只是谁」必须用落库键 `state.key`"
    )
    assert code.count("const code = state.key;") >= 2, (
        "自选按钮与它的点击处理器两处都要用落库键"
    )


def test_the_chip_is_a_persistent_readout_with_a_real_button():
    """浮标是**常驻状态读数**，不是"有事才冒出来的一闪"。

    D-49 的浮标 4 秒后自己消失、失败态整块可点；D-50 改成常驻 + 里面放真 button。
    理由（用户 2026-10-11 原话）：「它也没有自动拉取，也没有手动拉取的按钮」——
    有本地数据但落后时整个 UI 没有任何可点入口；而"没有浮标"既可能是已是最新、
    也可能是功能没跑，用户分不出来。
    """
    body = _fn_code(_js(), "function chipState(")
    assert "正在补最新数据" in body and "排队" in body and "已补到" in body
    assert "已是最新" in body, "不落后时也要常驻说一句，不许静默"
    assert "补数据没成功" in body and "重试" in body
    assert "is-action" in body, "可点的两态（落后可补 / 失败重试）用同一个类"
    assert "setTimeout" not in body, "常驻：不许再挂自动消失的定时器"
    assert "el.textContent" not in body, (
        "状态文字必须写进 #sync-chip-text：往容器上写 textContent 会把按钮一起抹掉"
    )
    assert "el.onclick" not in body, (
        "动作不许挂回整块浮标：整块仍然 pointer-events: none，挂了也点不到"
    )
    assert "btn.hidden = !actionable" in body, "按钮只在可点的两态出现"
    assert "btn.textContent" in body and "btn.onclick = () =>" in body, (
        "可点态要挂真按钮的句柄与文案"
    )


def test_the_chip_only_ever_talks_about_the_stock_you_are_looking_at():
    """换票之后浮标必须马上收起来。

    这里咬的是一个**真浏览器实测出来的缺陷**（2026-10-10）：在 `sz.002898`
    （退市整理，补不到）上浮标变成「没补到新数据…点这里重试」，随后换到已经最新的
    `600000`，浮标**仍然可见**、还写着 002898 的失败、**仍然可点** —— 点下去补的是
    002898。D-50 起浮标常驻，这条约束更重要了：一句「已补到」或一条失败文案
    只要挂错了票，就会一直挂在图上，不再有 4 秒后自动消失来兜底。
    """
    js = _js()
    helper = _fn_code(js, "function chipIsAbout(")
    assert "job.code === state.key" in helper and "job.period === state.period" in helper
    assert "state.code" not in helper, (
        "要比**落库键**，不能比用户输入的原样：请求可以写 `sz.002898`、服务端回的是 "
        "`002898`，拿 `state.code` 比会让浮标一个字都不显示（2026-10-10 真浏览器实测）"
    )
    assert "!job" in helper, "没有归属（还没开张）的浮标不算别人的"
    load = _fn_code(js, "async function load(")
    assert "chipIsAbout(chipJob)" in load, "换票时得拿「浮标现在讲的是谁」去比"
    assert load.index("chipIsAbout(chipJob)") < load.index("fetch("), (
        "必须在发请求之前就收，否则换票后那几百毫秒里旧结论还挂在图上"
    )
    chip = _fn_code(js, "function chipState(")
    assert "chipIsAbout(job)" in chip, "在后台补完的别人，一个字都不许写到当前这张图上"
    assert "chipJob = job" in chip
    drop = _fn_code(js, "function dropChip(")
    assert "el.hidden = true" in drop and "btn.onclick = null" in drop, (
        "收起来要连按钮句柄一起收，否则点到的是上一只票的 job"
    )
    assert "chipJob = null" in drop


def test_a_stock_that_already_tried_keeps_a_retry_on_itself():
    """自动补只做一次，但「能不能手动再来」必须还看得见。

    收走浮标之后若只是 `return`，就等于把失败一起藏了：图是落后的，
    却一点提示都没有，也没有任何可点的东西。
    """
    req = _fn_code(_js(), "function requestAutoSync(")
    assert "autoTried.has(key)" in req
    assert "autoTried.has(key)) return;" not in req, (
        "静默 return 会把失败一起藏掉：图落后却没有任何提示，也没有可点的重试"
    )
    after = req[req.index("autoTried.has(key)") :]
    assert 'chipState("stale"' in after, "已经自动试过的那一支要给出可点的「点这里补」"
    assert 'chipState("syncing"' in after and 'chipState("queued"' in after, (
        "在跑/在排队时要显示真实状态，不许显示成失败"
    )
    assert "manual" in req, "点浮标是手动路径：只绕过「自动只试一次」这一道闸门"


def test_the_chip_button_bypasses_only_the_once_per_session_gate():
    """点浮标 = 手动补。它只该绕过「自动只试一次」，别的（同时只飞一个、
    共用 runAutoSync、轮询问发起时那一只）一条都不许绕过。"""
    chip = _fn_code(_js(), "function chipState(")
    assert "requestAutoSync(job, true)" in chip, "手动路径要显式标出来"
    assert "runAutoSync(" not in chip, (
        "手动路径不许绕开 requestAutoSync 直接飞：那样就没有「同时只飞一个」了"
    )
    req = _fn_code(_js(), "function requestAutoSync(")
    assert "if (!manual && autoTried.has(key))" in req, "闸门必须只对自动路径生效"


def test_an_empty_period_lets_the_notice_panel_speak_alone():
    """本地一根都没有（404）时浮标整条让位给 `#notice`。

    实测（2026-10-11，`?code=000033&period=30`）两处会同时说话：notice 说
    「没有这只票的本地数据…」+ 一个「同步这个周期」按钮，浮标同时说
    「正在补最新数据：本地截至 ?，最新交易日 2026-10-09…」—— 那个 `?` 是
    `local` 为 `null` 漏出来的。两处讲同一件事只会更乱，所以 404 分支显式带 `quiet`。
    """
    load = _fn_code(_js(), "async function load(")
    at = load.index("404")
    assert "quiet: true" in load[at:], "404 的 job 要显式标 quiet，不靠 local===null 猜"
    chip = _fn_code(_js(), "function chipState(")
    assert "job.quiet" in chip and "dropChip()" in chip, "quiet 的 job 一个字都不写"


def test_the_chip_has_a_text_span_and_a_real_button():
    """状态文字与动作必须分开：动作是**真 button**（键盘可达、读屏可读），
    文字写在 span 里 —— 这样一次 `textContent` 不会把按钮一起抹掉。"""
    html = _html()
    at = html.index('id="sync-chip"')
    chip = html[at : html.index("</div>", at)]
    assert "<span" in chip and 'id="sync-chip-text"' in chip
    assert "<button" in chip and 'id="sync-chip-go"' in chip
    assert 'type="button"' in chip, "在表单里默认是 submit：必须显式写 type"
    assert "hidden" in chip, "按钮默认藏起来"


def test_the_chip_button_is_clickable_while_the_box_stays_transparent_to_the_mouse():
    """浮标整块不吃鼠标（图上的十字线/缩放照常），只有里面那个按钮吃。

    子元素在 `pointer-events: none` 的父元素下**仍然可点** —— 这正是要的：
    状态文字不挡图，按钮可点。
    """
    css = _strip_comments(_css())
    at = css.index(".sync-chip {")
    box = css[at : css.index("}", at)]
    assert "pointer-events: none" in box, "浮标整块不许挡住图上的交互"
    assert "is-action" in css and "is-retry" not in css, (
        "可点态改名为 is-action：它现在涵盖「落后可补」与「失败重试」两种"
    )
    go = css[css.index(".sync-chip-go {") :]
    go = go[: go.index("}")]
    assert "pointer-events: auto" in go, "按钮必须可点"
    assert "cursor: pointer" in go
