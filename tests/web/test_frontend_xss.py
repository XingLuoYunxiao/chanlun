"""前端契约：`innerHTML` 不许拼接变量，错误文案一律走 `textContent`。

`app.js` 里 6 处 `innerHTML` 有 5 处是**纯常量串**（`= ""` 或写死的 `<div>`），
这些是安全的：常量串不随任何输入变化。

只有一处把**跨进程边界来的文本**插进模板串再交给 `innerHTML` ——
`/api/watchlist` 响应体里的 `body.detail`。它不是本文件里的常量，
而是 HTTP 响应体里的一个字段。把它交给 `innerHTML` 等于让后端
（以及任何能影响该响应内容的东西）决定页面解析并执行什么标记。

护栏分两层，缺一不可：

1. **全局静态规则** —— 任何一行同时出现 `innerHTML` 与 `${` 就失败。
   这条盯的是"以后再写一处"，不针对某个具体分支；
2. **定点行为规则** —— 自选股读取失败那条分支必须用 `textContent` +
   `replaceChildren`。只靠第 1 条拦不住把插值拆成字符串拼接
   （`innerHTML = "…" + body.detail`）的写法。
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "chanlun" / "web" / "static"

#: 自选股栏读到一份没有 `items` 的响应体时走的那条分支。
#: 它渲染的正是 `body.detail` —— 本文件唯一一处非常量 `innerHTML`。
WATCH_FAIL_BRANCH = "if (!body.items) {"


def _js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _code_lines() -> list[tuple[int, str]]:
    """`(行号, 行内容)`，**整行注释**先剔掉。

    注释里出现 `innerHTML` 不是违规 —— 把注释算进去，只会逼着后来人
    把解释为什么不能用它的那句话删掉，护栏反而变弱。

    只剔**整行**注释，不剔行尾注释：要正确切掉行尾注释就得处理字符串里的
    `//`（本文件有 `http://` 这样的字面量），而"某行既有 `innerHTML`
    又有 `${`、还正好被 `//` 挡在后面"这种组合不现实。
    """
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(_js().splitlines(), 1):
        head = line.strip()
        if head.startswith("//") or head.startswith("*") or head.startswith("/*"):
            continue
        out.append((lineno, line))
    return out


def _watch_failure_branch() -> str:
    js = _js()
    try:
        start = js.index(WATCH_FAIL_BRANCH)
    except ValueError:  # pragma: no cover - 分支被删掉时给出可读的失败原因
        raise AssertionError(
            f"`app.js` 里找不到 `{WATCH_FAIL_BRANCH}`：这条护栏钉的分支已经不存在了，"
            "要么它被改名/挪走，要么整段被删。两种情况都要重新确认护栏钉在哪。"
        ) from None
    end = js.index("\n    }", start)
    return js[start:end]


def test_no_line_interpolates_a_variable_into_innerhtml():
    """任何一行同时出现 `innerHTML` 与 `${` 都算违规 —— 不看它有没有被转义。"""
    offenders = [
        f"app.js:{lineno}: {line.strip()}"
        for lineno, line in _code_lines()
        if "innerHTML" in line and "${" in line
    ]
    assert offenders == [], (
        "`innerHTML` 不许接模板插值：插进来的东西会被当 HTML 解析并执行。\n"
        "改用 `document.createElement` + `textContent`（或 `replaceChildren`）：\n"
        + "\n".join(offenders)
    )


def test_watch_failure_branch_renders_detail_as_text_not_html():
    """`body.detail` 是响应体里的文本，只能当文本写进 DOM。"""
    branch = _watch_failure_branch()
    # 钉的是**赋值**而不是那个词：注释里提到 `innerHTML` 是允许的。
    assign = re.search(r"\.innerHTML\s*=", branch)
    assert assign is None, (
        "自选股读取失败这条分支又在往 `innerHTML` 赋值了。\n"
        "`body.detail` 来自 HTTP 响应体，不是本文件里的常量：\n"
        f"{branch}"
    )
    assert "textContent" in branch, (
        "这条分支必须把 `body.detail` 写进 `textContent`，否则它会被当 HTML 解析。\n"
        f"{branch}"
    )
    assert "replaceChildren" in branch, (
        "这条分支要替换掉上一次的失败文案（旧请求的提示必须整段丢弃），"
        "用 `replaceChildren` 一次完成清空 + 挂载。\n"
        f"{branch}"
    )


def test_watch_failure_branch_keeps_the_user_visible_wording():
    """护栏钉的是"怎么渲染"，不许顺手把用户看的那句话改掉或删掉。"""
    branch = _watch_failure_branch()
    assert "自选股读取失败：" in branch
    assert "接口返回了无法解析的内容。" in branch, "`body.detail` 为空时的兜底文案没了"
    assert "body.detail" in branch, "后端给的原因必须继续显示，不能被兜底文案顶掉"
