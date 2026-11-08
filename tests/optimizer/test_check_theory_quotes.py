"""原文引用校验器：真条目必须绿，**转述必须被抓住**。

后一半才是重点。一个"永远满分"的校验器没有在测量任何东西 ——
所以这里每一条护栏都配一个**反向对照**：改一个字、挂错课号、编一句原文，
都必须报出来。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "optimizer" / "tools"))

import check_theory_quotes as mod  # noqa: E402


@pytest.fixture()
def theory(tmp_path, monkeypatch):
    """把校验器的扫描目录换到临时目录，别动真的 theory/。"""
    d = tmp_path / "theory"
    d.mkdir()
    monkeypatch.setattr(mod, "THEORY", d)
    return d


# ---------------- 真条目：绿 ----------------
def test_real_theory_quotes_are_all_verbatim():
    n, problems, notes = mod.check()
    assert n >= 25, f"只收上来 {n} 条引用，归属规则可能把该收的漏掉了"
    assert problems == [], "理论条目里有原文对不上：\n" + "\n".join(problems)
    # 边界标点只记 NOTE，不算失败；但数量要看得见，免得它悄悄涨成"随便改标点"。
    assert len(notes) <= 8, f"末尾标点被放宽的条数涨到 {len(notes)}，值得看一眼"


def test_recursion_theory_is_covered():
    """递归那条（D-51 的唯一依据）必须在被检查的集合里，不能整体漏收。"""
    got = {(p.name, course) for p, _l, course, _q in mod.collect()}
    assert ("L84-RECURSION-F2.md", 84) in got
    assert ("L84-RECURSION-F2.md", 35) in got
    assert ("L84-RECURSION-F2.md", 63) in got


# ---------------- 反向对照：必须抓得住 ----------------
def test_a_single_changed_character_is_caught(theory):
    """把第 84 课那句「级别，本质上与时间无关」改成「与时间**有关**」。"""
    (theory / "X.md").write_text(
        "第 84 课：\n\n> 级别，本质上与时间有关，级别也不是什么时间结构。\n",
        encoding="utf-8",
    )
    n, problems, _notes = mod.check()
    assert n == 1
    assert problems, "改了一个字却没人报警 —— 这个校验器没有在测量任何东西"
    assert "84" in problems[0]


def test_a_paraphrase_is_caught(theory):
    """AGENTS.md §3 点名禁止的那种转述。"""
    (theory / "X.md").write_text(
        "第 33 课：\n\n> 禅师说过中枢不能延伸太久，最多 8 段。\n",
        encoding="utf-8",
    )
    _n, problems, _notes = mod.check()
    assert problems, "转述被放过了"


def test_a_wrong_course_number_is_caught(theory):
    """同一句话挂到错课号上 —— 摘录是对的，归属是错的，同样要报。"""
    (theory / "X.md").write_text(
        "第 35 课：\n\n> 级别，本质上与时间无关，级别也不是什么时间结构。\n",
        encoding="utf-8",
    )
    _n, problems, _notes = mod.check()
    assert problems, "归属挂错课号却没人报警"


def test_a_fabricated_course_is_caught(theory):
    (theory / "X.md").write_text(
        "第 199 课：\n\n> 这段原文根本不存在，纯属编造出来的句子。\n",
        encoding="utf-8",
    )
    _n, problems, _notes = mod.check()
    assert problems and "存档里没有" in problems[0]


def test_boundary_punctuation_is_tolerated_but_reported(theory):
    """节选边界的标点替换**放行**，但必须如实记一条 NOTE，不能装作没发生。

    用例取自真实的边界：第 17 课原文是「…这里有一个递归的问题**，**就是这次级别
    不能无限下去…」，摘录截到「问题」就只能补个句号。
    """
    (theory / "X.md").write_text(
        "第 17 课：\n\n> 这里有一个递归的问题。\n", encoding="utf-8"
    )
    _n, problems, notes = mod.check()
    assert problems == []
    assert notes and "末尾标点" in notes[0]


def test_an_exact_match_produces_no_note(theory):
    """反向对照：原文本来就在那里收句的，不该被记成「标点不同」。

    否则 NOTE 会变成恒响的噪音 —— 而那正是「没在测量任何东西」的另一种形态。
    """
    (theory / "X.md").write_text(
        "第 84 课：\n\n> 级别，本质上与时间无关，级别也不是什么时间结构。\n",
        encoding="utf-8",
    )
    n, problems, notes = mod.check()
    assert n == 1 and problems == [] and notes == []


# ---------------- 归属规则本身 ----------------
def test_a_course_number_is_disarmed_by_the_next_paragraph(theory):
    """课号只能归属**紧跟其后**的引用块。

    这条规则是踩出来的：初版用「最近出现过的课号」归属，于是
    `第 060 课 … 。第 027 课（…）的措辞更直接：` 后面那段确属第 027 课的引用
    被算到第 060 课头上，而文档的归属其实是对的。**判据写错方向比没判据更坏**。
    """
    (theory / "X.md").write_text(
        "第 60 课提到过某件事。\n"
        "\n"
        "这里是文档自己的行文，不是引用。\n"
        "\n"
        "> 级别，本质上与时间无关，级别也不是什么时间结构。\n",
        encoding="utf-8",
    )
    assert mod.check()[0] == 0, "课号被别的内容打断后仍归给了它"


def test_a_quote_block_may_span_several_lines(theory):
    (theory / "X.md").write_text(
        "第 84 课：\n\n> 级别，本质上与时间无关，\n> 级别也不是什么时间结构。\n",
        encoding="utf-8",
    )
    n, problems, _notes = mod.check()
    assert n == 1 and problems == [], "跨行的引用块没被拼起来"


def test_ellipsis_splits_into_independently_checked_fragments(theory):
    """省略号是有意删节，两段各自核对；**改了其中一段也要报**。"""
    (theory / "X.md").write_text(
        "第 84 课：\n\n> 级别，本质上与时间无关……编造的半句在这里。\n",
        encoding="utf-8",
    )
    _n, problems, _notes = mod.check()
    assert problems, "省略号后接的编造内容被放过了"
