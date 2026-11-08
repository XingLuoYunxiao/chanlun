"""前端契约：行业选择器（D-44）必须读后端**真的**返回的键、必须把非 2xx 当失败。

这个文件的由来是两次**静默故障**（2026-10-06，D-44 收尾）：

1. `app.js` 的行业清单提示读的是 `body.checked_on`，而 `/api/industries`
   返回的是 `body.as_of` —— 于是「清单核对于 2026-10-05」这行提示**从来没显示过**。
   后端测试全绿（`as_of` 有断言）、前端没有任何断言、`fetch` 也不报错，
   所以没有任何一条测试拦得住它。D-44 专门说过「悄悄过期比过期本身更坏」，
   结果这行提示自己先悄悄没了。
2. 更早一次：看盘页进程比 `api.py` 旧，`/api/industries` 回 404，
   而 `fetch` **只在网络层失败时 reject**，404 是正常 resolve ——
   拿 `{"detail":"Not Found"}` 去读 `items`，得到「一个选项都没有」的空白下拉。
   「后端没重启」看起来和「这个功能不存在」一模一样。
3. 第三种（2026-10-06 终审补）：进程**认识** `/api/industries`、却**不认识**
   `local`（路由比字段老）。状态码是 200，`res.ok` 拦不住；`it.local === true`
   恒为假 ⇒ 卡片全暗、页脚印出「本地已有 0 / N」—— 又是一句没人拦得住的
   **错话**：「后端比页面旧」与「本机真没有板块日线」长得一模一样。
4. 第四种（同一次终审补）：`app.js` 里**定义了却没被调用**。`tests/web/`
   只对 `app.js` 跑 `node --check`（语法），从不执行它 —— 删掉启动尾部那次
   `wireIndustryModal()`，弹窗永远打不开，而全部测试照样绿。

所以这里钉后端测试覆盖不到的四件事：**键名**、**状态码**、**接线**、
**「不知道」不许说成「没有」**。
断言只读 `web/static/` 与 `/api/industries` 的返回键，不钉任何 UI 布局。

2026-10-06（D-44 大弹窗，Task 2）：上面最后半句曾写着「选择器从下拉框换成弹窗时，
这个文件应当**不用改**」—— 实测不成立。换弹窗时这里新增了三条**结构**契约：
`<dialog>` 必须取代 `<select>`、弹窗那一块 HTML 里不许出现任何行业名、
卡片亮暗必须读响应里的 `item.local`。所以「不钉 UI 布局」只对**其中一部分**
成立；钉键名、钉状态码、钉接线的那几条，正是本文件的老本行。
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "chanlun" / "web" / "static"


def _js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _code_only() -> str:
    """去掉块注释与整行注释，只留代码。

    注释里提 `checked_on` 是**好事** —— 要解释清楚为什么不能用它。
    所以断言必须只看代码。只删「整行就是注释」的行，不碰行尾注释：
    免得把 `https://…` 里的 `//` 当成注释起点，把那一行后半截吃掉。
    """
    text = re.sub(r"/\*.*?\*/", "", _js(), flags=re.S)
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("//")
    )


def _function_body(name: str) -> str:
    """抽出一个 IIFE 顶层函数的函数体。

    收尾取 `\\n  }`（2 空格缩进的右花括号）—— IIFE 里函数体内的东西至少缩进
    4 格，所以 2 格的 `}` 只可能是这个函数自己的收尾。**不能**只用「下一个顶层
    `function`」收尾：`setCode` 之后隔着整个启动尾部才有下一个函数，
    那样抽出来的 4997 字符里混着启动代码，断言会假绿。
    """
    code = _code_only()
    m = re.search(r"(?:async\s+)?function\s+" + name + r"\s*\(", code)
    assert m, f"app.js 里没有 {name}()"
    rest = code[m.start():]
    end = re.search(r"\n  \}", rest)
    nxt = re.search(r"\n  (?:async\s+)?function\s+\w+\s*\(", rest)
    stops = [x.start() for x in (end, nxt) if x]
    return rest[: min(stops)] if stops else rest


def _boot_tail() -> str:
    """最后一个 IIFE 顶层函数**之后**的启动尾部。

    这是唯一会被漏掉的死角：启动尾部是「定义了却没被任何函数调用」的代码的
    落脚处，而 `tests/web/` 从不执行 `app.js`（只 `node --check` 语法）——
    删掉 `wireIndustryModal();`，弹窗永远打不开，而全部测试照样绿。
    """
    code = _code_only()
    starts = list(re.finditer(r"\n  (?:async\s+)?function\s+\w+\s*\(", code))
    assert starts, "app.js 里一个 IIFE 顶层函数都没有？"
    rest = code[starts[-1].start():]
    end = re.search(r"\n  \}", rest)
    assert end, "最后一个顶层函数没有 2 格缩进的收尾括号"
    return rest[end.end():]


def test_the_industry_note_reads_the_key_the_api_really_returns(client):
    """`as_of` 是 `/api/industries` 的返回键；`checked_on` 谁都不返回。"""
    keys = set(client.get("/api/industries").json())
    assert "as_of" in keys
    assert "checked_on" not in keys, "后端键名若真要改，这条断言和前端得一起改"

    body = _function_body("loadIndustries")
    assert "body.as_of" in body, "核对日期从 `as_of` 读，不是 `checked_on`"
    assert "checked_on" not in _code_only(), (
        "app.js 又在读后端不返回的 `checked_on` 了 —— 那行提示会静默消失，"
        "而 `fetch` 不会报错。"
    )


def test_the_industry_fetch_treats_a_non_ok_status_as_a_failure():
    """404/500 是**正常 resolve** 的，必须显式看 `res.ok`，且把状态码说出来。

    只写 `try/catch` 不够：它拦得住断网，拦不住「后端没重启」。
    失败文案里要有状态码，否则排查的人无法区分 404 与 500。
    """
    body = _function_body("loadIndustries")
    assert "res.ok" in body, "只看 catch 不够 —— 404 不会 reject"
    assert "${res.status}" in body, "失败文案要说清是哪个状态码"
    assert "catch" in body, "断网那条路也要有话说"


def _html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def _dialog_block() -> str:
    html = _html()
    start = html.index('<dialog id="industry-modal"')
    return html[start : html.index("</dialog>", start)]


def test_the_picker_is_a_dialog_not_a_select():
    """下拉框换成弹窗：155 项铺在一个网格里挑，而不是在 155 行的下拉里滚。"""
    html = _html()
    assert '<select id="industry"' not in html, "下拉框应当已经被弹窗取代"
    assert '<dialog id="industry-modal"' in html
    assert 'aria-labelledby="ind-modal-title"' in html, "弹窗要有可访问的名字"


def test_no_industry_name_is_hard_coded_in_the_frontend():
    """D-44 的硬约束：31 + 124 个名字只活在 `data/industry.py` 里。

    写死在 HTML / JS 里没人会去改，而后端 `industry --check` 才能把
    「这份清单过期了」变成一句话。

    只看 `app.js` 的代码 + 弹窗那一块 HTML，**不扫整个页面**：
    顶栏的 `placeholder="600000 / 浦发银行"` 里含「银行」，而「银行」正好是
    一个一级行业名 —— 全页扫描会稳定误报，那种测试只会被人加白名单加死。
    """
    from chanlun.data import industry

    blob = _code_only() + _dialog_block()
    # 防呆：这个测试的全部效力都押在「扫到的面够不够大」上。注释剥离或切片一旦
    # 悄悄退化成空串，下面的 `hits` 会恒为空 —— 那是最坏的情况：测试永远绿，
    # 而硬编码的名字就在里面。所以先证明扫描面真的覆盖到了该覆盖的地方。
    assert "setCode" in blob, "`_code_only()` 没扫到 app.js 的代码"
    assert 'id="ind-grid"' in blob, "`_dialog_block()` 没扫到弹窗里的网格容器"
    hits = sorted({b.name for b in industry.all_boards() if b.name in blob})
    assert not hits, f"前端写死了行业名：{hits[:5]}"


def test_the_modal_reads_the_local_flag_the_api_really_returns(client):
    """卡片亮还是暗靠 `item.local`；后端不返回它，卡片会全是暗的。"""
    items = client.get("/api/industries?level=1").json()["items"]
    # 先钉「列表非空」：空列表上 `all(...)` 恒真，这条断言会**假绿**。
    assert items, "`?level=1` 必须回一批行业，否则下面的 all(...) 什么都没测"
    assert all(isinstance(i["local"], bool) for i in items)
    body = _function_body("loadIndustries")
    assert "it.local" in body, "`local` 要从响应里读，不能在前端自己猜"


def test_the_tab_strip_is_driven_by_the_levels_the_api_really_returns(client):
    """tab 条的名字与计数来自 `body.levels`；这个键缺席，tab 条会静默塌成一条。

    `buildIndustryTabs(body.levels || [])` 的 `|| []` 把「后端没给这个键」与
    「这个键是空列表」变成同一件事：`keys[0] || "1"` 兜底成一级，
    「二级行业（124）」那一格凭空消失 —— 而后端测试与其余前端断言全绿。
    """
    levels = client.get("/api/industries").json()["levels"]
    assert isinstance(levels, list) and levels, "`levels` 不能是空列表（tab 条会塌）"
    assert [lv["level"] for lv in levels] == ["1", "2"], "tab 的顺序就是 `levels` 的顺序"
    assert all(isinstance(lv["name"], str) and lv["name"] for lv in levels)
    assert "body.levels" in _function_body("loadIndustries"), (
        "tab 条要从响应里长出来，不能在前端写死"
    )
    assert "levels.map" in _function_body("buildIndustryTabs")


def test_the_footer_admits_when_the_backend_is_older_than_the_page():
    """`local` 键**缺席** = 后端进程比页面旧；页脚要说实话，不能印「本地已有 0 / N」。

    这是第三种静默故障（2026-10-06 终审）：`/api/industries` 存在、items 里
    却没有 `local` 时状态码仍是 200，`res.ok` 拦不住，`it.local === true`
    恒为假 —— 卡片全暗，而「后端比页面旧」与「本机真没有板块日线」长得一模一样。
    """
    body = _function_body("loadIndustries")
    assert '"local" in it' in body, "先看后端到底给没给这个键，别只看它的值"
    # 只钉「这两句话在文件里」是不够的 —— 它拦不住 `if (stale)` 取反：
    # 取反后两句话都还在，但**会说反**，正常后端印「后端进程比页面旧」、
    # 旧后端印「本地已有 0 / N」，正是这次要消灭的那句错话。
    # （2026-10-06 收尾评审实测：取反后 10 passed 全绿，故把文案绑到分支上。）
    m = re.search(
        r"if \(stale\) \{\n(.*?)\n      \} else \{\n(.*?)\n      \}",
        body,
        re.S,
    )
    assert m, "loadIndustries 里找不到 `if (stale) { … } else { … }` 这对页脚分支"
    stale_arm, fresh_arm = m.group(1), m.group(2)
    assert "后端进程比页面旧" in stale_arm, "键缺席时页脚必须说清楚，不能装作本机没数据"
    assert "本地已有" not in stale_arm, "旧后端绝不能印一个自己算不出的计数"
    assert "本地已有 ${have}" in fresh_arm, "键在场时那条正常文案要留着"
    assert "后端进程比页面旧" not in fresh_arm, "正常后端不许说自己是旧的"
    assert re.search(
        r"const have = industryRows\.filter\(\(r\) => r\.local\)\.length", fresh_arm
    ), "`have` 必须是亮着的卡数，不是总数 —— 否则页脚恒报 N / N"


def test_the_rail_entry_is_a_button_that_opens_the_modal():
    """入口从下拉框变成按钮；点卡片即关闭弹窗并出图。"""
    html = _html()
    assert 'id="industry"' in html
    assert 'aria-haspopup="dialog"' in html, "要告诉辅助技术它会开一个弹窗"
    assert 'id="industry-name"' in html, "按钮要能显示当前选中的行业名"
    assert "showModal" in _function_body("openIndustryModal"), (
        "用原生模态，别自己手写遮罩与焦点圈禁"
    )
    assert "pickIndustry" in _code_only()


def test_the_modal_is_wired_and_booted_not_merely_defined():
    """`wireIndustryModal()` 必须**被调用**，而且要落在启动尾部。

    这是第四种静默故障（2026-10-06 终审）：`tests/web/` 只对 `app.js` 跑
    `node --check`，从不执行它 —— 删掉末尾那次 `wireIndustryModal();`，
    pytest 全绿，而入口按钮点了没反应、弹窗永远打不开。
    """
    code = _code_only()
    # 恰好两次：一次定义、一次调用。多了少了都说明接线变了。
    assert code.count("wireIndustryModal") == 2, (
        "`wireIndustryModal` 应当恰好出现两次（定义 + 启动调用），"
        f"实际 {code.count('wireIndustryModal')} 次"
    )
    body = _function_body("wireIndustryModal")
    assert '"click", openIndustryModal' in body, "入口按钮的点击要绑在弹窗那一处"
    assert re.search(r"\bwireIndustryModal\(\)", _boot_tail()), (
        "启动尾部必须真的调一次 —— 只定义不调用，弹窗永远打不开"
    )


def test_changing_the_current_code_keeps_the_entry_button_honest():
    """顶栏输入一只股票之后，按钮不能还留着上一个行业名 —— 那是在说反话。

    所以 `syncIndustryButton()` 必须挂在 `setCode` 上（改当前代码的唯一入口）。
    """
    body = _function_body("setCode")
    assert "syncIndustryButton()" in body
    assert '$("#code").value = code' in body, "前提：这里真的是唯一的入口"
