"""文档行号引用校验器的护栏用例。

这个校验器存在的理由：``ARCHITECTURE.md`` 里写死了大量 ``文件.py:行号`` 引用，
它们是**文档的一部分**，却没人能保证它们跟着代码一起动。此前靠人肉复审，
结果是「转抄的行号是错的」这类事故反复发生（本项目已发生过 14 次）。

所以这里的用例不是「实现说明书」，而是**判据的边界**：
哪些算一条引用、哪些桶只算建议、什么情况下必须红。特别地，
``test_negative_control_*`` 是**反向对照**：把一份干净的文档弄坏一处，
校验器必须从绿变红 —— 否则「全绿」什么都证明不了。

``optimizer/tools/`` 不是包（没有 ``__init__.py``，也不在 ``src/`` 下），
所以这里按路径加载模块，而不是 ``import chanlun...``。
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

CHANLUN = Path(__file__).resolve().parents[2]
TOOL = CHANLUN / "optimizer" / "tools" / "check_doc_line_refs.py"
SUMMARY_RE = re.compile(
    r"共 (\d+) 条，通过 (\d+) 条，失败 (\d+) 条，路径歧义 (\d+) 条，"
    r"符号漂移告警 (\d+) 条，缺符号无法校验 (\d+) 条"
    r"（其中 ±2 行内有符号的 J2 (\d+) 条），非仓库路径 (\d+) 条，首末行空行 (\d+) 条"
)


@pytest.fixture(scope="module")
def tool():
    """按路径加载被测工具（它不是包内模块）。"""
    assert TOOL.is_file(), f"校验器不存在：{TOOL}"
    spec = importlib.util.spec_from_file_location("check_doc_line_refs_under_test", TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _mini(tmp_path: Path, files: dict[str, str], doc: str) -> tuple[Path, Path]:
    """造一个最小仓库：``root/src/chanlun/<files>`` + ``root/ARCHITECTURE.md``。"""
    root = tmp_path / "chanlun"
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    doc_path = root / "ARCHITECTURE.md"
    doc_path.write_text(doc, encoding="utf-8")
    return root, doc_path


def _run(tool, root: Path, doc: Path, *args: str) -> tuple[int, str]:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = tool.main(["--root", str(root), "--doc", str(doc), *args])
    return code, buf.getvalue()


FOO = "\n".join(
    [
        '"""最小模块。"""',
        "",
        "def alpha():",
        "    return 1",
        "",
        "def bar():",
        "    return 2",
        "",
        "BAZ = 3",
        "",
    ]
)  # 10 行

DOC_HEAD = "### 3.4 口径\n\n"


# --------------------------------------------------------------------------- #
# 解析层：什么算一条引用
# --------------------------------------------------------------------------- #
def test_continuation_segments_merge_into_one_ref(tool):
    """`foo.py:3-4, 7` 是**一条**引用、两个范围（续段合并，不新起一条）。"""
    refs = tool.parse_refs(1, "见 `foo.py:3-4, 7` 与 `bar.py:1`。")
    assert [r.filename for r in refs] == ["foo.py", "bar.py"]
    assert refs[0].ranges == ((3, 4), (7, 7))
    assert refs[1].ranges == ((1, 1),)


def test_double_colon_symbol_notation_is_not_a_reference(tool):
    """`state.py::backtestable` 是**符号引用**，不是行号引用（冒号后必须跟数字）。"""
    refs = tool.parse_refs(1, "入口是 `state.py::backtestable`（见 `state.py:69`）。")
    assert len(refs) == 1
    assert refs[0].filename == "state.py" and refs[0].ranges == ((69, 69),)


def test_non_py_reference_is_parsed(tool):
    """头部正则不许硬编码 `.py` —— 否则 `app.js:NN` 会被静默跳过。"""
    refs = tool.parse_refs(1, "见 `app.js:36` 与 `app.js:117-120`。")
    assert [(r.ext, r.ranges) for r in refs] == [("js", ((36, 36),)), ("js", ((117, 120),))]


# --------------------------------------------------------------------------- #
# 硬判据
# --------------------------------------------------------------------------- #
def test_normal_reference_passes(tool, tmp_path):
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "失败 0 条" in out
    assert "通过 1 条" in out


def test_out_of_range_reference_fails(tool, tmp_path):
    """行号超出文件总行数 ⇒ 硬失败（退出码非 0）。"""
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:99-100`。\n")
    code, out = _run(tool, root, doc)
    assert code != 0, out
    assert "失败 1 条" in out
    assert "越界" in out


def test_inverted_range_in_bounds_fails(tool, tmp_path):
    """区间写反（起 > 止）⇒ 硬失败，哪怕两端都落在文件行数之内。

    只查上界的旧实现会**静默放行**它：`6-3` 的终点 3 和起点 6 都不越界，
    于是进 `通过` 桶、退出码 0 —— 一个写反的区间被记成了绿。
    """
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:6-3`。\n")
    code, out = _run(tool, root, doc)
    assert code != 0, out
    assert "失败 1 条" in out
    assert "通过 0 条" in out
    assert "区间倒置" in out
    assert "6-3" in out


def test_inverted_range_with_out_of_range_start_gives_verdict_not_traceback(tool, tmp_path):
    """起点越界、终点不越界的倒置区间 ⇒ 可读判定，不是 IndexError。

    `99-3` 在上界判据眼里「终点 3 ≤ 10 行」是合格的，于是旧实现一路走到
    `res.lines[s - 1]` 抛 IndexError —— 退出码 1 来自崩溃而非判据，
    输出里也没有任何一行说明文档哪里写错了。
    """
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:99-3`。\n")
    code, out = _run(tool, root, doc)  # 崩溃会在这里冒泡成异常 ⇒ 本用例直接红
    assert code != 0, out
    assert "失败 1 条" in out
    assert "区间倒置" in out
    assert "99-3" in out
    assert "Traceback" not in out


def test_inverted_continuation_segment_fails(tool, tmp_path):
    """续段写反同样要拦：`3-4, 7-5` 的第二段起 > 止。"""
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4, 7-5`。\n")
    code, out = _run(tool, root, doc)
    assert code != 0, out
    assert "失败 1 条" in out
    assert "区间倒置" in out
    assert "7-5" in out


def test_line_number_zero_fails_instead_of_wrapping_to_file_tail(tool, tmp_path):
    """行号 0 ⇒ 硬失败。旧实现 `res.lines[0 - 1]` 是 Python 负索引，静默指到末行。"""
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:0-0`。\n")
    code, out = _run(tool, root, doc)
    assert code != 0, out
    assert "失败 1 条" in out
    assert "通过 0 条" in out
    assert "低于下界" in out


def test_missing_file_fails(tool, tmp_path):
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `nope.py:1`。\n")
    code, out = _run(tool, root, doc)
    assert code != 0, out
    assert "失败 1 条" in out
    assert "找不到" in out


def test_non_repo_path_is_not_a_failure(tool, tmp_path):
    """仓库外路径（历史记录里的临时探针）单列一桶，不算失败、不许去改。"""
    root, doc = _mini(
        tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "历史：`/tmp/exp/fresh_trigger.py:49`。\n"
    )
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "非仓库路径 1 条" in out
    assert "失败 0 条" in out


def test_ambiguous_path_goes_to_ambiguity_bucket(tool, tmp_path):
    """同名 basename 两个候选 ⇒ 建议桶，不算失败，并列出全部候选。"""
    files = {
        "src/chanlun/chan/types.py": FOO,
        "src/chanlun/data/types.py": FOO,
    }
    root, doc = _mini(tmp_path, files, DOC_HEAD + "见 `types.py:3`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "路径歧义 3 条" not in out  # 只有一条引用
    assert "路径歧义 1 条" in out
    assert "chan/types.py" in out and "data/types.py" in out
    assert "失败 0 条" in out


def test_resolution_prefers_src_chanlun_over_repo_root(tool, tmp_path):
    """解析顺序钉死：`src/chanlun/<路径>` 先于仓库根 `<路径>`。

    先例：`optimizer/agent.py` 在仓库根是个 41 行壳，真文件在 `src/chanlun/` 下 781 行。
    顺序反了会把合法引用误报成越界。
    """
    files = {
        "src/chanlun/optimizer/agent.py": FOO,
        "optimizer/agent.py": "x = 1\n",
    }
    root, doc = _mini(tmp_path, files, DOC_HEAD + "见 `optimizer/agent.py:8`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "失败 0 条" in out


# --------------------------------------------------------------------------- #
# 建议桶：符号漂移
# --------------------------------------------------------------------------- #
def test_dotted_span_counts_both_sides_of_the_dot_as_candidates(tool, tmp_path):
    """`` `Klass.method` `` 的点号**两侧都算候选**（这是「点号前面那个」的防回归）。

    简报的措辞是「任一侧的定义行落在范围内即算通过」，但控制者探针的实际行为
    是**逐 token 计对、逐 token 告警**：±1 报出的 6 个 (行, 符号) 对里就有
    ``L620 Snapshot``（``engine.py:61``，范围 ``75-106``），而同一跨度的
    ``clipped_to``（``engine.py:87``）在范围内 —— 若「任一侧通过即整对通过」，
    L620 根本不该出现在告警清单里，±1 的对数也会从 12 掉到 6（跨度数）。
    所以这里钉的是：**两侧都被数进来**，右边那个不许被丢掉（丢掉就会把
    ``clipped_to@87`` 的通过项一起丢掉，±1 的对数会从 12 变 7）。
    """
    src = "\n".join(
        [
            "class Klass:",  # 1
            "    pass",  # 2
            "",  # 3
            "    def method(self):",  # 4
            "        return 1",  # 5
            "",
        ]
    )
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": src}, DOC_HEAD + "`Klass.method` 见 `foo.py:4-5`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "检查 2 对" in out, out
    assert "符号漂移告警 1 条" in out, out
    assert "Klass -> foo.py def@1" in out, out
    assert "method" not in out.split("=== 符号漂移扫描")[1], out


def test_symbol_in_range_is_not_reported_as_drift(tool, tmp_path):
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "`bar` 见 `foo.py:6-7`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "符号漂移告警 0 条" in out
    assert "检查 1 对" in out


def test_symbol_out_of_range_is_drift_warning_only(tool, tmp_path):
    """符号在 ±1 行窗口内、定义行不在被引范围内 ⇒ 告警桶；**不影响退出码**。"""
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "`bar` 见 `foo.py:1-2`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "符号漂移告警 1 条" in out
    assert "bar -> foo.py def@6" in out


def test_strict_symbols_flag_upgrades_warning_to_failure(tool, tmp_path):
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "`bar` 见 `foo.py:1-2`。\n")
    code, out = _run(tool, root, doc, "--strict-symbols")
    assert code != 0, out
    assert "符号漂移告警 1 条" in out


def test_symbol_without_any_symbol_in_window_is_reported_uncheckable(tool, tmp_path):
    """窗口内一个符号都没有 ⇒ 记「缺符号无法校验」，并另报 ±2 行内是否有符号。"""
    root, doc = _mini(
        tmp_path,
        {"src/chanlun/chan/foo.py": FOO},
        DOC_HEAD + "见 `foo.py:3-4`。\n\n`bar` 在别处。\n",
    )
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "缺符号无法校验 1 条" in out
    assert "其中 ±2 行内有符号的 J2 1 条" in out
    # 简报 §4 第 3 条要求：J2 **必须逐行列出来**，只给计数等于没披露。
    assert "其中 ±2 行内有符号 J2 = 1（逐行列出" in out, out
    assert "±2 窗口符号: bar" in out, out


def test_double_colon_span_supplies_a_symbol_to_the_window(tool, tmp_path):
    """`state.py::backtestable` 必须能给窗口供符号 —— 否则 `:887` 会被误判成「无符号」。

    这正是探针 30 → 29 那次修正（旧实现用 `".py:" in s` 子串跳过跨度，把它一起跳掉了）。
    """
    files = {"src/chanlun/chan/state.py": FOO, "src/chanlun/chan/foo.py": FOO}
    doc = DOC_HEAD + "入口是 `state.py::backtestable`。\n见 `foo.py:3-4`。\n"
    root, doc_path = _mini(tmp_path, files, doc)
    code, out = _run(tool, root, doc_path)
    assert code == 0, out
    assert "缺符号无法校验 0 条" in out


# --------------------------------------------------------------------------- #
# 非 .py 桶 + 首末行空行 + 汇总行
# --------------------------------------------------------------------------- #
def test_non_py_reference_is_enumerated_with_lines(tool, tmp_path):
    """非 .py 桶只枚举：被引文件 + 总行数 + 范围内每一行原文。"""
    app_js = "\n".join(f"line{i}" for i in range(1, 21)) + "\n"
    root, doc = _mini(
        tmp_path,
        {"src/chanlun/web/static/app.js": app_js},
        DOC_HEAD + "见 `app.js:2-3`。\n",
    )
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "非 .py 引用（1 处 / 1 行）" in out
    assert "（20 行）" in out
    assert "line2" in out and "line3" in out
    assert "引用总数（.py 桶）：全文 0 处 / 0 行" in out


def test_blank_first_or_last_line_is_suggestion_only(tool, tmp_path):
    """首行/末行是空行 ⇒ 只进建议桶（真实文档有 23 处，当硬判据会把干净文档判死）。"""
    foo = "x = 1\n\ndef bar():\n    return 2\n"
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": foo}, DOC_HEAD + "见 `foo.py:2-3`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "首末行空行 1 条" in out
    assert "失败 0 条" in out


def test_summary_line_has_every_field(tool, tmp_path):
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    last = [ln for ln in out.splitlines() if ln.startswith("共 ")][-1]
    assert SUMMARY_RE.search(last), last


def test_summary_states_which_buckets_can_change_the_exit_code(tool, tmp_path):
    """输出必须显式说明退出码归属 —— 尤其「缺符号无法校验」不在其内。

    这条只钉**披露**，不动判据：同一个夹具下 `code == 0` 就证明缺符号桶
    仍然不影响退出码（它过去、现在都只披露）。
    """
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4`。\n\n`bar` 在别处。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "缺符号无法校验 1 条" in out
    assert "退出码归属" in out
    assert "仅 --strict-symbols 时 exit 1" in out
    assert "不接入退出码" in out


def test_missing_symbol_bucket_never_changes_exit_code(tool, tmp_path):
    """反向对照：三条「缺符号无法校验」，退出码仍是 0（这一桶不许接进退出码）。"""
    doc = DOC_HEAD + "见 `foo.py:3-4`。\n\n见 `foo.py:6-7`。\n\n见 `foo.py:8-9`。\n"
    root, doc_path = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, doc)
    code, out = _run(tool, root, doc_path)
    assert code == 0, out
    assert "失败 0 条" in out
    assert "缺符号无法校验 3 条" in out
    assert "退出码归属" in out


def test_help_states_exit_code_ownership(tool):
    """`--help` 同样要说清哪些桶能改退出码（否则只有读输出的人知道）。"""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        tool.main(["--help"])
    text = buf.getvalue()
    assert "退出码归属" in text, text
    assert "缺符号无法校验" in text, text


# --------------------------------------------------------------------------- #
# 反向对照：坏输入必须变红
# --------------------------------------------------------------------------- #
def test_negative_control_corrupt_reference_turns_red(tool, tmp_path):
    """同一份文档：干净时绿，把一处引用改坏后必须红。

    没有这条对照，「校验器全绿」不构成任何证据 —— 它可能只是什么都看不见。
    """
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4`。\n")
    code_ok, out_ok = _run(tool, root, doc)
    assert code_ok == 0, out_ok
    assert "失败 0 条" in out_ok

    doc.write_text(DOC_HEAD + "见 `foo.py:3-99`。\n", encoding="utf-8")
    code_bad, out_bad = _run(tool, root, doc)
    assert code_bad != 0, out_bad
    assert "失败 1 条" in out_bad


def test_negative_control_blank_symbol_window_turns_red_under_strict(tool, tmp_path):
    """反向对照之二：把符号挪出窗口，「检查的对数」必须掉下来（不是恒绿）。"""
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "`bar` 见 `foo.py:6-7`。\n")
    _, out_with = _run(tool, root, doc)
    assert "检查 1 对" in out_with

    doc.write_text(DOC_HEAD + "见 `foo.py:6-7`。\n", encoding="utf-8")
    _, out_without = _run(tool, root, doc)
    assert "检查 0 对" in out_without


# --------------------------------------------------------------------------- #
# 引用行无可校验符号（人工复核清单）
# --------------------------------------------------------------------------- #
def test_own_line_without_symbols_is_listed_per_line(tool, tmp_path):
    """本行反引号里没有可校验符号 ⇒ 逐行给出「文档行号 | 引用文本 | 该行符号数 | 判定」。"""
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "=== 引用行无可校验符号" in out, out
    assert "  文档行号 | 引用文本 | 该行符号数 | 判定" in out, out
    assert "  L3 | foo.py:3-4 | 0 | ±1 窗口亦无符号" in out, out
    assert "共 1 行：±1 窗口亦无符号 1 行" in out, out


def test_own_line_with_symbol_is_not_listed(tool, tmp_path):
    """反向对照：本行有符号（`alpha`）就不进这张清单 —— 否则清单等于「所有引用行」。"""
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "`alpha` 见 `foo.py:3-4`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "  无（每条引用所在的行都至少有一个可校验符号）" in out, out


def test_neighbour_symbol_is_marked_circumstantial_only(tool, tmp_path):
    """邻行有符号 ⇒ 判定记「可旁证」，且必须写明**不代表引用正确**（它仍是人工复核项）。"""
    doc = DOC_HEAD + "见 `foo.py:3-4`。\n`bar` 在别处。\n\n见 `foo.py:6-7`。\n"
    root, doc_path = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, doc)
    code, out = _run(tool, root, doc_path)
    assert code == 0, out
    assert "  L3 | foo.py:3-4 | 0 | ±1 窗口有符号可旁证：bar" in out, out
    assert "  L6 | foo.py:6-7 | 0 | ±1 窗口亦无符号" in out, out
    assert "共 2 行：±1 窗口亦无符号 1 行" in out, out
    assert "邻行有符号可旁证 1 行" in out, out
    assert "不代表引用正确" in out, out
    # 窗口 0 个符号 ⇒ 必然也在「覆盖度偏弱点」桶里（同一把尺子，只差窗口大小）。
    assert "  L6: 引用 1 条 / 窗口符号 0 个" in out, out
    assert "  L3: 引用" not in out, out


def test_unverifiable_bucket_is_disclosure_only_even_under_strict(tool, tmp_path):
    """本桶不许接进退出码：两条「±1 窗口连符号都没有」+ `--strict-symbols`，仍须 exit 0。"""
    doc = DOC_HEAD + "见 `foo.py:3-4`。\n\n见 `foo.py:6-7`。\n"
    root, doc_path = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, doc)
    code, out = _run(tool, root, doc_path, "--strict-symbols")
    assert code == 0, out
    assert "引用行无可校验符号 2" in out, out
    assert "符号漂移告警 0 条" in out, out  # 一个符号都没有 ⇒ 严格档也没东西可报警
    assert "不接入退出码" in out, out


# --------------------------------------------------------------------------- #
# 真文档
# --------------------------------------------------------------------------- #
def test_real_architecture_doc_is_green_and_enumerates_app_js(tool):
    """真文档：硬判据全绿，且 `app.js` / `styles.css` 引用被逐条枚举（此前从未被任何校验器看过）。"""
    code, out = _run(tool, CHANLUN, CHANLUN / "ARCHITECTURE.md")
    assert code == 0, out
    assert "失败 0 条" in out
    # 2026-10-04：D-41 在 ARCHITECTURE.md 里新增了 2 处 `.md` 引用
    #   （`ARCHITECTURE.md:648`（D-26）与 `ARCHITECTURE.md:1016`（D-35）），
    #   非 .py 桶 5 → 7。这两个数是这次工具的实际输出，不是硬加出来的：
    #   改判据前是 5/5（旧断言在这里变红即为证），改后是 7/7。
    # 2026-10-04 同日第三改：D-41 的「前端同步」段落新增 3 处 `app.js` 引用
    #   （`app.js:642` 判定 / `app.js:662` 标签 / `app.js:992` 图例），
    #   非 .py 桶 7 → 10（10 处落在 8 行上 —— `L1671` 那一行有 3 处）。
    #   旧的 `7 处 / 7 行` 断言在这里变红即为「数字确实变了」的证。
    # 2026-10-04 同日第四改：D-41 的「与 D-35 的关系」段落把 6 处指向
    #   ARCHITECTURE.md 自身的历史位置从裸 `:NNN` 改成显式 `ARCHITECTURE.md:NNN`
    #   （裸 `:NNN` 工具看不见，等于没有校验），非 .py 桶 10 → 17（17 处 / 12 行）。
    #   三条断言都是工具的实际输出，不是硬加出来的。
    # 2026-10-04 同日第五改：同一段落补上 `CHANGELOG.md:931-932`（`[0.2.0]` 里
    #   `THIRD_TOL` 的历史记载），非 .py 桶 17 → 18（18 处 / 13 行）。
    # 2026-10-04 同日第六改：同一段落补上浏览器复测（`app.js:642` 与 `app.js:992`
    #   各再引一次 + 探针脚本名），非 .py 桶 18 → 20（20 处 / 15 行）。
    #   `（1500 行）` 也随之 8 → 10。
    # 2026-10-04 同日第七改：复测时发现 `.row-meta`（`<span>`，行内盒子）的
    #   `overflow: hidden` / `text-overflow: ellipsis` 根本不生效，清单每一行都冲出右栏
    #   （实测三只票 12–15 行全中，最多 560.8px）。修法是 `styles.css` 加
    #   `display: block`；同时把 `capped` 标记补到清单标题行的小标签上
    #   （因为同一个原因，` · 段数到顶（…）` 后缀在窄栏里会被省略号吃掉）。
    #   这一段于是新增 4 处 `styles.css` 引用（`:415` / `:435` / `:397` / `:415`）
    #   与 2 处 `app.js` 引用（`:1057-1066` / `:1060`），
    #   非 .py 桶 20 → 26（26 处 / 20 行）；`app.js` 1487 → 1511（+11，插入点在
    #   第 1057 行）⇒ `（1511 行）` 12 处、`styles.css（737 行）` 4 处。
    #   旧断言在这里变红即为「数字确实变了」的证，不是硬加出来的。
    # 2026-10-04 同日第八改（D-42）：D-42 段落引了 1 处 `app.js`
    #   （`app.js:757`，线段确认标记 —— 用来论证它属于**另一个概念**、不在本次
    #   口径变更范围内），非 .py 桶 26/20 → 27/21，`（1511 行）` 12 → 13。
    #   `styles.css` 没动，仍是 4。旧断言在这里变红即为「数字确实变了」的证。
    # 2026-10-05 第九改（D-43，港股/美股 + 名称搜索 + 加自选按钮）：
    #   `app.js` 1511 → 1650（+139：代码/名称解析、联想下拉、加自选按钮），
    #   `styles.css` 737 → 754（+17：`.fav` / `.fav-err`）。被引用的**文件行数**
    #   变了，`（N 行）` 这个后缀随之从 `（1511 行）` 变成 `（1650 行）`、
    #   `（737 行）` 变成 `（754 行）`。
    #   ★ 处数也变了：`（1650 行）` 13 → **18**（D-43 新增 5 处 `app.js` 引用：
    #   `:1341` ×2、`:1455-1466` ×2、`:281-284` ×1），
    #   `（754 行）` 4 → **5**（新增 1 处 `styles.css:303-315`）。
    #   两个数字都是跑一遍 checker 从输出里数出来的，不是拿增量加出来的。
    #   ★ 非 .py 桶同理：27 处 / 21 行 → 36 处 / 29 行（D-43），
    #     再 → 35 处 / 28 行（同日 D-43 收尾：把 D-41 段里那个会腐烂的
    #     `CHANGELOG.md:931-932` 绝对行号换成按段名 + 引文定位，该引用
    #     就不再是校验器能解析的 `路径:行号` 形状了）。
    #   这 9 处是拿 `check_doc_line_refs.scan()` 对 `HEAD:chanlun/ARCHITECTURE.md`
    #   与当前文件各跑一遍、比目标集数出来的（不是把 +N 加到旧号上）：
    #   `index.html:85-87` ×3、`app.js:1341` ×2、`app.js:1455-1466` ×2、
    #   `styles.css:303-315` ×1、`app.js:281-284` ×1 —— 前三项是 D-43 段落自己引的
    #   （股票页上的加自选按钮、代码/名称分流），后两项是 §4.2 骨架行与
    #   「A 股口径注释不再当通用口径」那条。旧断言在此变红即为「数字确实变了」的证。
    # 2026-10-05 第十一改（D-44，A 股一级/二级行业指数）：
    #   `app.js` 1650 → 1715（+65：行业选择器 `loadIndustries()` 与 `change` 处理），
    #   `styles.css` 754 → 785（+31：`.ind-pick` 一族），`index.html` 130 → 143
    #   （+13：`.ind-pick` 那一块）。被引用的**文件行数**变了，`（N 行）` 后缀
    #   随之从 `（1650 行）` 变成 `（1715 行）`、`（754 行）` 变成 `（785 行）`。
    #   ★ 处数：`（1715 行）` 18 → **20**（D-44 新增 2 处 `app.js` 引用：
    #   `:1448` ×1、`:1623` ×1）；`（785 行）` 仍是 **5**（D-44 没引 `styles.css`）；
    #   `index.html` 后缀变成 `（143 行）`，实测 **4** 处。
    #   ★ 非 .py 桶：35 处 / 28 行 → **39 处 / 31 行**（+4：`pyproject.toml:3`、
    #   `app.js:1448`、`app.js:1623`、`index.html:83`；这 4 条是拿
    #   `check_doc_line_refs.scan()` 对 `HEAD:chanlun/ARCHITECTURE.md` 与当前文件
    #   各跑一遍、比目标集数出来的，不是把 +N 加到旧号上）。
    #   旧断言在此变红即为「数字确实变了」的证。
    # 2026-10-06 第十二改（D-44 收尾的 `PATCH`：行业清单提示读错键 + 静默 404）：
    #   `app.js` 1715 → **1737**（+22：`loadIndustries()` 里加了 `res.ok` 分支、
    #   失败文案与网络层 `catch`，插入点在原第 1448 行之后），故 `（1715 行）`
    #   变成 `（1737 行）`。**处数仍是 20**（`app.js` 的引用条数没变，只是
    #   指向的行区间按内容重锚：`app.js:1623` → `app.js:1646`）。
    #   `styles.css` 785 / `index.html` 143 / 「非 .py 引用（39 处 / 31 行）」
    #   **都不变**（这次没动 css/html 的引用）。
    #   ★ 顺带查出：非 `.py` 桶**没有任何自动护栏**，早已静默腐烂 ——
    #   D-44 插行后 5 条引用集体指错（`index.html:85-87` → 实为 `:97-100`、
    #   `app.js:1455-1466` → 实为 `:1523-1526`），D-41 段另有 6 条漂了一位到
    #   十几行，8 条 `ARCHITECTURE.md` 自引用漂了 280~297 行。全部按**内容**重锚，
    #   并把指纹基线从只覆盖 `.py` 扩到**全部**引用（version 1 → 2，265 → 305 条），
    #   那个桶从此有护栏。证据：拿 `git archive HEAD chanlun` 导出的 HEAD 快照
    #   各跑一遍本工具，比目标集数出来的。
    # 2026-10-06 第十三改（D-44 大弹窗，Task 2：行业选择器 `<select>` → `<dialog>`）：
    #   `index.html` 143 → **174**（+5：`.ind-pick` 换成按钮；+26：body 末尾的弹窗骨架），
    #   于是 `（143 行）` 后缀变成 `（174 行）`。**处数仍是 4**（这次没增删
    #   `index.html` 的引用条数，只是把 4 条按**内容**重锚）：
    #   `index.html:97-99` → `:102-104`（`.tools` + `#fav-btn`）、
    #   `index.html:100` → `:105`（`#fav-error`，两处）、
    #   `src/chanlun/web/static/index.html:83` → `:85`（入口元素本身：
    #   带 `id="industry"` 的那一行从 `<select>` 变成 `<button>` 的开标签）。
    #   前三条靠**指纹**反查（拿基线里的 sha256 前 16 位去新旧两份文件里逐个
    #   行区间比对，命中唯一）；第四条的被引内容**已经不存在**，没有行号能满足
    #   旧指纹 —— 按内容重锚到新的入口元素后才 `--update-baseline`（顺序不能反：
    #   先重锚再刷新基线，否则基线会把漂掉的行号也记成"对的"）。
    #   ★ 这三条断言**一个都没变**：`（1737 行）` 仍是 20、`（785 行）` 仍是 5、
    #   「非 .py 引用（39 处 / 31 行）」仍是 39/31（条数没动，动的只是行号）。
    #   旧断言在这里**没有**变红，所以这次没有"数字确实变了"的证 —— 如实记下来，
    #   不为了凑一条红去改数字。
    # 2026-10-06 第十四改（D-44 大弹窗，Task 3：弹窗铺 tab + 网格 + 卡片）：
    #   `app.js` 1737 → **1886**（+149：行业小节整段换成 `loadIndustries()` 一族
    #   210 行，替换掉原来 64 行的 `<select>` 版；另有 +3 行是 tab 点击后补
    #   `syncIndustryTabs()`），`styles.css` 785 → **962**（+177：`.ind-open`
    #   一族 21 行 + 弹窗/卡片/网格 167 行，插入点在原第 651 行与第 667 行）。
    #   于是 `（1737 行）` 后缀变成 `（1886 行）`、`（785 行）` 变成 `（962 行）`。
    #   ★ 处数一个没动：`（1886 行）` 仍是 **20**、`（962 行）` 仍是 **5**、
    #   「非 .py 引用（39 处 / 31 行）」仍是 39/31 —— 这次没增删任何引用条数。
    #   ★ 6 条引用的行号按**内容**重锚（顺序：先重锚，再 `--update-baseline`，
    #   最后才改下面这两条断言）：
    #   `app.js:1523-1526` ×2 → `:1672-1675`（`renderFav()` 里读 `state.watch`
    #   推 `aria-pressed` 那 4 行）、`app.js:1664` → `:1813`（「结构必须画在同一
    #   口径上」那条注释）、`app.js:1728-1734` → `:1877-1883`（取数不等渲染那一段）、
    #   `src/chanlun/web/static/app.js:1448` → `:1455`（`async function
    #   loadIndustries() {`）、`:1646` → `:1795`（旧的
    #   `$("#industry").addEventListener("change", …)` —— 这一行 Task 4 会删，
    #   届时它连同这条引用要一起处理）。
    #   前两条断言在这里**变红了**（`（1737 行）` 数到 0），这就是"数字确实变了"的证。
    # 2026-10-06 第十五改（D-44 大弹窗，Task 4：开关 / 跨级搜索 / 键盘导航 / 点卡片出图）：
    #   `app.js` 1886 → **1948**（+92 / −30：删掉旧 `<select>` 的 `change` 监听 23 行、
    #   以及两处内联的 `.period` 高亮 `for` 循环（各 5 行）改成 `setPeriodUI()`；
    #   新增 `openIndustryModal` / `pickIndustry` / `setPeriodUI` /
    #   `industryGridColumns` / `moveIndustryFocus` / `wireIndustryModal` 一族，
    #   并在 `setCode` 里补 `syncIndustryButton()`）。
    #   `styles.css` 行数**没变**（仍是 962：只把 `::backdrop` 的
    #   `rgba(9,11,14,.74)` 换成 `color-mix(in srgb, var(--ink) 74%, transparent)`，
    #   一行换一行），`index.html` 仍是 174 行 —— 所以 `（962 行）` / `（174 行）`
    #   这两条断言这次**没变红**，如实记成 no-op，不为了凑红去动它们。
    #   于是 `（1886 行）` 后缀变成 `（1948 行）`。
    #   ★ 处数一个没动：`（1948 行）` 仍是 **20**、`（962 行）` 仍是 **5**、
    #   「非 .py 引用（39 处 / 31 行）」仍是 39/31 —— 这次没增删任何引用条数。
    #   ★ 5 条引用的行号按**内容**重锚（顺序：先重锚，再 `--update-baseline`，
    #   最后才改下面这条断言）：
    #   `app.js:1672-1675` ×2 → `:1759-1762`（`renderFav()` 里读 `state.watch`
    #   推 `aria-pressed` 那 4 行）、`app.js:1813` → `:1878`（「结构必须画在同一
    #   口径上」那条注释）、`app.js:1877-1883` → `:1938-1945`（取数不等渲染那一段，
    #   末尾多了新插进去的 `wireIndustryModal();`）、
    #   `src/chanlun/web/static/app.js:1795` → `:1704`（旧的
    #   `$("#industry").addEventListener("change", …)` —— **这一行 Task 4 删了**，
    #   逻辑搬进 `wireIndustryModal()`，被引内容已不存在，只能按内容重锚到新家）。
    #   第一条断言在这里**变红了**（`（1886 行）` 数到 0），这就是"数字确实变了"的证。
    # 2026-10-06 第十六改（D-44 Task 5：文档收尾 + 版本 `0.6.0` → `0.7.0`）：
    #   D-44 的「后果 / 变更历史」新增第 9 项，决策第 9 / 10 条里的旧 `<select>`
    #   入口也改成「按钮 + 原生 `<dialog>`」，于是 `app.js` / `index.html` 又多引了
    #   一批区间（`index.html:85-90`、`index.html:151`、`app.js:1644-1653`、
    #   `app.js:1655-1668`、`app.js:1693-1701`、`app.js:1704-1728`、
    #   `app.js:1732-1739`、`app.js:1807-1813`，两条决策与后果项里各出现一次）。
    #   非 .py 桶：39/31 → **51 处 / 43 行**（+12 处 / +12 行）；
    #   `（1948 行）` 20 → **28**（+8 条 `app.js` 引用，同时旧的
    #   `app.js:1704` 被 `:1704-1728` 换掉、旧的 `index.html:85` 被 `:85-90` 换掉）。
    #   `（962 行）` 仍是 **5** —— 本次没引任何新的 `styles.css` 行，如实记成 no-op，
    #   不为了凑红去动它。
    # 2026-10-06 第十七改（D-44 Task 5 修复轮：删掉一条与现状矛盾的历史引用）：
    #   `ARCHITECTURE.md:548`（D-44「后果 / 变更历史」里的版本那一项）原本引
    #   `pyproject.toml:3`，而 `:610` 引的是**同一条单值行**却声称另一个值
    #   （`548` 说 `0.5.0 → 0.6.0`、`610` 说 `0.6.0 → 0.7.0`；那行只有一个**当前**
    #   值）。单值行证明不了两个历史断言，所以**删掉历史那条的引用**、留现状那条：
    #   非 .py 桶 51 处 / 43 行 → **50 处 / 42 行**（−1 处 / −1 行）。
    #   `.py` 桶（271 处 / 246 行）、`（1948 行）`、`（962 行）`、
    #   `引用行无可校验符号 151` 均未变，如实记成 no-op。
    #   指纹基线 321 → **320 条**（`pyproject.toml:3-3#1` 先成基线残留，
    #   `--update-baseline` 后清掉）。顺序仍是：先删/按**内容**重锚引用 →
    #   `--update-baseline` → 最后才改下面的断言；下面这条就是本次**变红**的证
    #   （`（51 处 / 43 行）` 数到 0）。
    # 2026-10-06 第十八改（D-44 终审补丁：`local` 键缺席时不许报假计数 + 四条护栏）：
    #   `app.js` 1948 → **1964**（+16，全部插在 `loadIndustries` 里：识别 `local`
    #   键缺席、页脚改口）；`ARCHITECTURE.md` 2634 → **2652**（+18，D-44
    #   「后果 / 变更历史」第 9 项下新增一条子项，插在当时的第 645 行）。
    #   于是**被引目标**整体平移：13 条 `app.js` 引用 +16、9 条本文件自引用 +18
    #   （自引用全在插入点之后）。全部按**内容**整段重锚（把旧区间的原文块在新
    #   文件里整段查找，命中位置唯一），不是拿 +N 硬加；改完 `--update-baseline`，
    #   22 个键等价替换（320 → 320 条，无值变）。
    #   **引用条数与各桶计数一个没动**：非 .py 桶仍是 50 处 / 42 行、`.py` 桶
    #   271 处 / 246 行、`（962 行）` 仍是 5、`引用行无可校验符号` 仍是 151、
    #   `覆盖度偏弱点` 仍是 87 —— 本次只改数字、不增删引用，如实记成 no-op。
    #   下面第二条断言就是本次**变红**的证：改完引用、还没改断言时
    #   `out.count("（1948 行）")` 数到 0（见本次 pytest 的失败输出）。
    # 2026-10-06 第十九改（D-45：自选股读取失败文案改走 `textContent`）：
    #   `app.js` 1964 → **1969**（+5，失败分支由 1 行展开成
    #   `createElement` + `textContent` + `replaceChildren` 6 行，插入点在原第
    #   1122 行）；`ARCHITECTURE.md` 2652 → **2716**（+64：§2.5 末尾新增 D-45 条目
    #   插在原第 674 行，§5 新增 1 行已知空白表行插在原第 2642 行）。
    #   ⇒ 被引目标整体平移：**16 处** `app.js` 引用 +5、**9 处**本文件自引用 +64，
    #   全部按**内容**整段重锚（把旧行号的原文在新文件里整段查找、命中唯一），
    #   不是拿 +N 硬加；改完 `--update-baseline`（320 → 330 条）。
    #   ★ 与第十八改不同，这次**条数真的变了**（D-45 新增 10 处 `app.js` 引用）：
    #   非 .py 桶 50 处 / 42 行 → **60 处 / 48 行**（+10 处 / +6 行）；
    #   `（1964 行）` → `（1969 行）`，处数 28 → **38**（+10）。
    #   `.py` 桶 271 处 / 246 行、§3.4 79 处 / 73 行、`（962 行）` 5 处、
    #   `（174 行）`、`引用行无可校验符号` 151、`覆盖度偏弱点` 87 **一个都没动**
    #   —— D-45 不碰 `.py`、不碰 `styles.css`、不碰 `index.html`。
    #   下面第一条与第二条断言就是本次**变红**的证：改完引用、还没改断言时
    #   `"非 .py 引用（50 处 / 42 行）"` 与 `out.count("（1964 行）") == 28`
    #   都数到 0（见本次 pytest 的失败输出）。
    # 2026-10-06 第二十改（D-46：`normalize` 剔除倒挂行、越界只警告）：
    #   `data/types.py` 79 → **116**（+37，新增两档处置插在原第 63 行之后）；
    #   `ARCHITECTURE.md` 2720 → **2799**（+79：§2.5 末尾新增 D-46 条目
    #   插在原第 739 行的 `---` 之前 = 78 行，§5 新增 1 行已知空白表行 = +1）。
    #   ⇒ 被引目标整体平移：`data/types.py:64` → `:101`（1 处）、
    #   `data/types.py:69-79` → `:106-116`（5 处，落在 `ARCHITECTURE.md` ×4、
    #   `CHANGELOG.md` ×1、`docs/superpowers/specs/…-hk-us-markets-design.md` ×1、
    #   `docs/evidence/2026-10-05-申万行业指数数据源.md` ×1，共 7 处）；
    #   另有 **9 处**本文件自引用（`:1077`、`:1447`、`:1449-1453`、`:1452`、
    #   `:1457-1458`、`:1472`、`:1485`、`:1529`×2）整体 +78，同样按**内容**
    #   整段重锚（拿旧行号的原文在新文件里查找、命中唯一），不是拿 +78 硬加。
    #   ★ 这次**条数真的变了**：D-46 新引 12 条 `.py`/测试文件引用
    #   （`types.py` ×3、`fractal.py`/`include.py` ×2/signal.py/divergence.py/
    #   `engine.py`/`store.py`、`test_sina_source.py`/`test_tencent_source.py`）
    #   ⇒ `.py` 桶 271 处 / 246 行 → **283 处 / 258 行**（+12 处 / +12 行）；
    #   `--update-baseline` 330 → **342 条**（+12）；
    #   `引用行无可校验符号` 151 → **160**（+9）、首末行空行 14 → **15**。
    #   非 .py 桶 60 处 / 48 行、§3.4 79 处 / 73 行、`（1969 行）` 38、
    #   `（962 行）` 5 **一个都没动** —— D-46 不碰 `app.js` / `styles.css` /
    #   `index.html`，也不在 §3.4 里。
    #   下面第三、第四条断言就是本次**变红**的证：改完引用、还没改断言时
    #   `"引用总数（.py 桶）：全文 271 处 / 246 行…"` 与
    #   `assert int(m.group(1)) == 151` 都数到旧值（见本次 pytest 的失败输出：
    #   实测 `283 处 / 258 行`、`160 行`）。
    # 2026-10-09 第二十一改（D-47：baostock 会话必须带超时；连续失败必须停批）：
    #   `ARCHITECTURE.md` 2799 → **2903**（+104：§2.5 末尾新增 D-47 条目
    #   （`:819-920`，含收尾 `---`）= 102 行，§5 新增 2 行已知空白表行 = +2）。
    #   ★ 这次**条数也真的变了**：D-47 新引 8 条 `.py` 引用
    #   （`baostock_source.py` ×3、`__main__.py` ×4、`calendar.py` ×1）
    #   ⇒ `.py` 桶 283 处 / 258 行 → **291 处 / 266 行**（+8 处 / +8 行）；
    #   `--update-baseline` 342 → **350 条**（+8）；
    #   `引用行无可校验符号` 160 → **164**（+4）；
    #   非 .py 桶 60 处 / 48 行 → **63 处 / 50 行**（+3 处 / +2 行：
    #   `deploy/com.chanlun.daily.plist` ×2 与
    #   `docs/evidence/2026-10-08-定时任务静默挂死.md` ×1，
    #   后者与前者的第二处落在同一行上）。
    #   §3.4 79 处 / 73 行、`（1969 行）` 38、`（962 行）` 5 **一个都没动** ——
    #   D-47 不碰 `app.js` / `styles.css` / `index.html`，也不在 §3.4 里。
    #   下面三条断言就是本次**变红**的证：改完引用、还没改断言时
    #   `"引用总数（.py 桶）：全文 283 处 / 258 行…"`、
    #   `"非 .py 引用（60 处 / 48 行）"` 与 `assert int(m.group(1)) == 160`
    #   都数到旧值（见本次 pytest 的失败输出：实测 `291 处 / 266 行`、
    #   `63 处 / 50 行`、`164 行`）。
    #   2026-10-09 同日第二十二改（D-48）：`ARCHITECTURE.md` 2903 → **3016**
    #   （+111：D-48 条目 109 行插在 §3 之前，§5 再新增 2 行）。
    #   非 `.py` 桶 63/50、`（1969 行）` 38、`（962 行）` 5 **依旧一个都没动**
    #   （D-48 也不碰 `app.js` / `styles.css` / `index.html`，也不在 §3.4 里）。
    #   本文件自己的 9 处自引用（指向 D-26 / D-35）按内容重锚 +111，
    #   详见下面第二十二改的说明。
    # 2026-10-10 第二十三改（D-49：数据的「新不新」由后端一处判断，页面落后就自己去补）：
    #   `ARCHITECTURE.md` 3018 → **3141**（+123：D-49 条目插在 §3 之前，§5 再新增 3 行）；
    #   `app.js` 1969 → **2156**（+187）、`styles.css` 962 → **993**（+31）、
    #   `index.html` 174 → **177**（+3）。这是 D-49 之后第一次同时动到
    #   `app.js` / `styles.css` / `index.html` 三个文件 ⇒ 下面三个数**一起**变了：
    #   非 `.py` 桶 63/50 → **80 处 / 62 行**（+17 处 / +12 行：D-49 条目里
    #   `app.js` ×12、`index.html` ×1、`styles.css` ×1，§5 第 3 行再引
    #   `app.js:233` ×2 与 `app.js:2014`）；
    #   `（1969 行）` 38 → `（2309 行）` **59**（2026-10-17 再更新：1 分按钮、零行回退文案、
    #   「已证伪」series 都改在 app.js 里，总行数变了；CSS 同理由 993 → 1013）、
    #   那条 CSS 规则）。§3.4 79 处 / 73 行**依旧没动**（D-49 不在 §3.4 里）。
    #   下面三条断言就是本次**变红**的证：改完引用、还没改断言时
    #   `"非 .py 引用（63 处 / 50 行）"`、`（1969 行）` 与 `（962 行）` 都数到旧值。
    assert "非 .py 引用（90 处 / 69 行）" in out
    assert out.count("（1026 行）") == 9, out
    assert out.count("（1026 行）") == 9, out
    # ★ 这里**故意不钉文档行号**（`L478` 之类）：文档一增删就漂，而这类
    #   「把行号抄进另一份文件」正是本任务要治的病。钉**被引用的 app.js 行区间**
    #   —— 它们才是这些引用各自的身份证。
    # 2026-10-03：顶栏布局稳定化改了 app.js（1461 → 1483，+22 行），后三处区间随之整体
    #   下移 +9，这里和 ARCHITECTURE.md §4.2 同步更新（前三处在该插入点之前，未动）。
    #   区间是按**内容**重锚的，不是拿 +9 硬加出来的。
    # 2026-10-03 同日第二改：D-37/D-38 之后图注「GG/DD 不含离开段」不再无条件成立，
    #   `TREND_BASIS` 及其注释块改写，app.js 1483 → 1487（+4，插入点在第 46 行）。
    #   5 处区间再次按**内容**逐条重锚（`grep -n` 定位原句，不是把 +4 加到旧号上）；
    #   其中 `:1066-1068` 实测原就多含了一行（真身是 2 行注释），这次收成 `:1070-1071`。
    # 2026-10-04 同日第四改：D-41 给 `capped` 中枢加视觉标记，`app.js` 1487 → 1500
    #   （+13，插入点在原第 636 行与第 975 行两处）。后三处区间再次按**内容**逐条重锚
    #   （把旧区间的原文块在新文件里整段查找，命中的位置唯一）：
    #   `:1070-1071` → `:1083-1084`、`:1414-1415` → `:1427-1428`、
    #   `:1479-1484` → `:1492-1497`。前两处在该插入点之前，未动。
    # 2026-10-04 同日第七改：`app.js` 1500 → 1511（+11，插入点在第 1057 行）。
    #   按**内容**整段重锚（旧区间的原文块在新文件里查找，命中位置唯一）：
    #   `:1083-1084` → `:1094-1095`、`:1427-1428` → `:1438-1439`、
    #   `:1492-1497` → `:1503-1508`；`app.js:213-216` 在插入点之前，未动。
    # 2026-10-06 第十二改（D-44 收尾的 `PATCH`）：上面这 13 条区间里，非 `.py`
    #   那些**早已漂了**（非 `.py` 桶当时没有指纹护栏），这次全部按**内容**重锚：
    #   `app.js:642` → `:643`、`app.js:1060` → `:1061`（各差一位，原号落在注释上）、
    #   `app.js:1438-1439` → `:1664`、`app.js:1503-1508` → `:1728-1734`（漂了几百行）、
    #   `styles.css:397` → `:414`、`styles.css:415` → `:432`、`styles.css:435` → `:452`。
    #   未动的 6 条是 `app.js:58` / `app.js:213-216` / `app.js:1094-1095` /
    #   `app.js:662` / `app.js:992` / `app.js:1057-1066`（实测本就指对）。
    # 2026-10-06 第十五改（D-44 Task 4）：上面这个元组里两条跟着一起动 ——
    #   `app.js:1813` → `:1878`、`app.js:1877-1883` → `:1938-1945`（同 §3.4 的重锚）。
    #   其余 11 条实测未动（插入点都在它们之后），不跟着改号。
    # 2026-10-06 第十八改（D-44 终审补丁）：上面这个元组里两条跟着一起动 ——
    #   `app.js:1878` → `:1894`、`app.js:1938-1945` → `:1954-1961`（同 §3.4 的重锚，
    #   插入点在它们之前）。其余 11 条实测未动，不跟着改号。
    # 2026-10-06 第十九改（D-45）：上面这个元组里两条跟着一起动 ——
    #   `app.js:1894` → `:1899`、`app.js:1954-1961` → `:1959-1966`
    #   （`app.js` 在**它们之前**的原第 1122 行插了 5 行）。其余 11 条实测未动
    #   （`app.js:58` / `:213-216` / `:1094-1095` / `:643` / `:662` / `:992` /
    #   `:1057-1066` / `:1061` 全在插入点之前，`styles.css` 三条不受影响）。
    # 2026-10-10 第二十三改（D-49）：上面这个元组里 10 条 `app.js` 引用全部跟着动
    #   （`app.js` 在它们**之前**插了 10 行：`state.key` 那三行注释与占位、
    #   `load()` 的 404 分支、`renderWatch()` 高亮；又在它们之后插了 D-49 的
    #   5 个函数与浮标）。按**内容**逐条重锚（旧区间的原文块在新文件里查找，
    #   13 条全部命中唯一位置）：
    #   `:213-216` → `:223-226`（+10）、`:1094-1095` → `:1280-1281`、
    #   `:1899` → `:2086`、`:1959-1966` → `:2146-2153`、`:643` → `:829`、
    #   `:662` → `:848`、`:992` → `:1178`、`:1057-1066` → `:1243-1252`、
    #   `:1061` → `:1247`（后 8 条都在 +186 那一段里）。
    #   `app.js:58` 与 `styles.css` 三条（`:414` / `:432` / `:452`）实测未动
    #   —— `styles.css` 的新规则加在 `:972` 之后，在它们之后。
    for ref in ("src/chanlun/web/static/app.js:58", "app.js:223-226",
                "app.js:1280-1281", "app.js:2086", "app.js:2146-2153",
                "app.js:829", "app.js:848", "app.js:1178",
                "app.js:1243-1252", "app.js:1247",
                "styles.css:414", "styles.css:432", "styles.css:452"):
        assert ref in out, ref
    # 枚举行必须带文档行号字段（只钉形状，不钉值）
    assert len(re.findall(r"^  L\d+: ", out, re.M)) >= 8, out


def test_real_architecture_doc_reports_py_totals(tool):
    """.py 桶总数：全文 205 处 / 191 行；§3.4 79 处 / 73 行（与探针逐项一致）。

    2026-10-04：D-41 段落新增 3 处 `signal.py` / `divergence.py` 引用
    （`signal.py:217`、`signal.py:286`、`divergence.py:182`），并把 D-35 顶部
    的作废提示接进 D-41 —— 全文 186/176 → 189/179，§3.4 60/58 → 63/61。
    这三个数是工具的实际输出；旧断言在此变红即为「数字确实变了」的证。

    2026-10-04 同日第八改（D-42）：D-42 段落新增 14 处 `.py` 引用
    （`divergence.py` 的 `extreme_ts` / 落点 / 排序，`signal.py` 的落点与排序，
    `engine.py:152`、`types.py`、`state.py`、`runner.py`、`watchlist.py`、
    `scanner.py`），并且 D-42 是插在 `### 3.5` 之前的（§3.4 的窗口因此
    从 2136 行涨到 2250 行）。全文 189/179 → 205/191，§3.4 63/61 → 79/73。
    这三个数是工具的实际输出；旧断言在此变红即为「数字确实变了」的证。

    2026-10-05 第九改（D-43）：D-43 段落（新增 28 处 `.py` 引用：行情源分派、
    复权口径、名称搜索、加自选按钮，见 D-43 的 依据 与 后果/变更历史）
    插在 `### 2.5` 的 `D-13` 之后，§3.4 的窗口不受影响。
    全文 205/191 → 233/212，§3.4 79/73 **不变**。
    这三个数是工具的实际输出；旧断言在此变红即为「数字确实变了」的证。

    2026-10-05 第十改（D-43 收尾的 `PATCH`）：§4.3 补了一条
    `scan/watchlist.py` 的 `--codes` 取名说明、D-43 的「变更历史」补了第 10 项，
    两处各引一次 `watchlist.py:266`，§4.3 那条还引了一次
    `tests/scan/test_watchlist.py:144` —— 全文 233/212 → **236/215**，
    §3.4 79/73 **仍然不变**（两处都在 §3.4 的窗口之外）。
    这三个数是工具的实际输出；旧断言在此变红即为「数字确实变了」的证。

    2026-10-05 第十一改（D-44）：D-44 段落（A 股一级/二级行业指数，
    见 D-44 的 依据 与 后果/变更历史）插在 §2.5 的 `D-43` 之后，
    §3.4 的窗口不受影响。全文 236/215 → **266/241**，§3.4 79/73 **不变**。
    这三个数是工具的实际输出；旧断言在此变红即为「数字确实变了」的证。

    2026-10-06 第十二改（D-44 收尾的 `PATCH`）：D-44 的「后果 / 变更历史」
    新增第 8 项（行业清单提示读错键 + 静默 404），其中 `src/chanlun/web/api.py:654`
    是**新引的一行**，故全文 266/241 → **267/242**（+1 处 / +1 行）。
    §3.4 79/73 **不变**（新引用在 §2.5 的 D-44 段里，窗口外）。
    这三个数是工具的实际输出；旧断言在此变红即为「数字确实变了」的证。

    2026-10-06 第十六改（D-44 Task 5：文档收尾 + 版本 `0.6.0` → `0.7.0`）：
    决策第 9 条里的响应形状从「三个键」改成「四个键」，新引
    `src/chanlun/web/api.py:671`（`local` 的来源行）与
    `src/chanlun/web/api.py:659-663`（`local` 的判据）；「后果 / 变更历史」
    新增第 9 项又各引了一次。全文 267/242 → **271 处 / 246 行**（+4 处 / +4 行）。
    §3.4 79/73 **不变**（新引用仍在 §2.5 的 D-44 段里，窗口外）。
    这三个数是工具的实际输出；旧断言在此变红即为「数字确实变了」的证。

    2026-10-09（D-47：baostock 会话必须带超时；连续失败必须停批）：
    D-47 段新引 8 条 `.py` 引用（`baostock_source.py` ×3、`__main__.py` ×4、
    `calendar.py` ×1）⇒ 全文 283/258 → **291 处 / 266 行**（+8 处 / +8 行）。
    §3.4 79/73 **不变**（新引用仍在 §2.5 的 D-47 段里，窗口外）。
    `--update-baseline` 342 → **350 条**。旧断言在此变红即为「数字确实变了」的证。

    2026-10-09 同日第二十二改（D-48：零行同步不许抹掉已有水位）：
    D-48 段新引 7 条 `.py` 引用（D-48 条目里 `sync.py` ×2、`scanner.py` ×1、
    `__main__.py` ×2；§5 两行新增表行里 `__main__.py` ×1、`sync.py` ×1）
    ⇒ 全文 291/266 → **298 处 / 273 行**（+7 处 / +7 行）。
    §3.4 79/73 **不变**（新引用都在 §2.5 与 §5，窗口外）。
    `--update-baseline` 350 → **357 条**。
    **另有一件 D-48 特有的事**：D-48 条目插在 §3 之前（`ARCHITECTURE.md` 2903 →
    **3016**，+111：D-48 条目 109 行 + §5 新增 2 行），于是**本文件自己的 9 处
    自引用**（指向 D-26 / D-35 记录）整体下移 +111 ⇒ 指纹报 `不符 10 条`。
    这 9 处按**内容**重锚（拿基线里那条指纹去验新位置，9/9 逐条命中），
    不是拿 +111 硬加：`:1155` → `:1266`、`:1525` → `:1636`、
    `:1527-1531` → `:1638-1642`、`:1530` → `:1641`、`:1535-1536` → `:1646-1647`、
    `:1550` → `:1661`、`:1563` → `:1674`、`:1607` → `:1718`（×2）。
    第 10 条不符是**故意的内容变更**：`__main__.py:607-631` 里那句
    「零行…说明会话是活的」被 D-48 证伪，注释就地改掉 ⇒ 该区间指纹真的变了。
    旧断言在此变红即为「数字确实变了」的证。

    2026-10-10 第二十三改（D-49：数据的「新不新」由后端一处判断，页面落后就自己去补）：
    D-49 段新引 23 条引用 —— `.py` 桶 9 条（`web/api.py` ×6：`:993` / `:958` /
    `:693` / `:745` / `:721-730` / `:408`，`calendar.py` ×2、`sync.py` ×1），
    非 `.py` 桶 14 条（`app.js` ×12、`index.html` ×1、`styles.css` ×1）；
    §5 新增 3 行表行里再引 `.py` 2 条（`sync.py:52-53` / `sync.py:37-53`）
    与非 `.py` 3 条（`app.js:233` ×2 / `app.js:2014`）；收尾给 §5 第 3 行
    「指数零覆盖」补了 `__main__.py:1148-1154` 与 `universe.py:62` 两条 `.py`
    引用（原先只写了不带行号的裸路径，按 §6「每条论断带 `文件:行号`」补上）。
    合计 `.py` 桶 298/273 → **322 处 / 293 行**（+24 处 / +20 行）、非 `.py` 桶
    63/50 → **80 处 / 62 行**（+17 处 / +12 行）。§3.4 79/73 **不变**
    （新引用都在 §2.5 与 §5，窗口外）。`--update-baseline` 357 → **387 条**。
    **D-49 特有的事**：D-49 条目插在 §3 之前（`ARCHITECTURE.md` 3018 →
    **3141**，+123），于是**本文件自己的 9 处自引用**整体下移 +120 ⇒ 指纹报
    `不符 66 条`（含 `app.js` / `index.html` 在 D-49 期间自身被改动带来的位移）。
    可重锚的 61 处按**内容**重锚（拿基线里那条指纹去验新位置，逐条命中），
    不是拿 +120 硬加。另 4 处是**故意的内容变更**，位置由人读代码判定：
    `app.js:1780-1783` → `:1966-1969`（`renderFav()` 的按压态改用落库键）、
    `app.js:1753-1760` → `:1939-1946`（`syncIndustryButton()` 同上）、
    `app.js:1828-1834` → `:2014-2021`（`setCode()` 里多了 `state.key` 占位）、
    `app.js:233`（×2，D-45 那行与 D-49 新行**同一个区间文本**：D-45 那行指向的
    旧内容已漂到 `:258`，而 D-49 新行指向的 404 兜底行**恰好就是现在的 `:233`**
    ⇒ 指纹基线的键不含文档行号，两处撞成同一个键 `app.js:233-233#0`，
    工具报「内容变了」；按内容核对后两处各归其位）。
    旧断言在此变红即为「数字确实变了」的证。
    """
    _, out = _run(tool, CHANLUN, CHANLUN / "ARCHITECTURE.md")
    assert "引用总数（.py 桶）：全文 322 处 / 293 行；§3.4 79 处 / 73 行" in out


def test_real_architecture_doc_discloses_unverifiable_lines(tool):
    """真文档：新桶逐行披露、条数与列出的行数一致，且**不接退出码**。

    129 是 2026-10-03（Task 10a-R2）实测值，133 是 2026-10-04 D-42 之后的实测值，
    137 是 2026-10-05 D-43 之后的实测值，140 是同日 D-43 收尾 `PATCH` 之后的实测值
    （+3：§4.3 与 D-43 变更历史第 10 项新引的三处行号本身没有可校验符号），
    150 是同日 D-44 之后的实测值（净 +10 = 新增 20 行 − 消失 10 行：重锚的
    10 条 `web/api.py` 引用换了地址、旧的 10 行随之消失，D-44 又新增 20 行），
    151 是同日 D-44 Task 5 收尾之后的实测值（+1：新引的
    `src/chanlun/web/api.py:659-663` 落在「判据」那一行，该行自己的反引号里
    只有引用、没有可校验符号；紧邻的上一行有 `error` 可旁证，所以工具把它
    列成「±1 窗口有符号可旁证」，但它仍进这张人工复核清单）；
    160 是 2026-10-06 D-46 之后的实测值（+9：D-46 新增 12 条引用，
    其中 3 条落在有符号可校验的行上，另 9 条落进这张清单）；
    164 是 2026-10-09 D-47 之后的实测值（+4 行：D-47 新增 8 条 `.py` 引用，
    其中 4 条落在有符号可校验的行上（`__main__.py` 的 `:78-84` / `:607-631` /
    `:903-918` / `:484-487`），另 4 条各占一行、行内没有可校验符号，于是
    落进这张清单（`ARCHITECTURE.md` 的 `:825` / `:828` / `:829` / `:835`）——
    这 4 行就是上面第二十一改里那 +4 的来源，不是另一次增删）；
    **167 是同日 D-48 之后的实测值**（+3 行：D-48 新增 7 条 `.py` 引用，
    其中 4 条落在有符号可校验的行上（D-48 条目里的 `sync.py:73-117` /
    `sync.py:37-53` / `scanner.py:139-140` / `__main__.py:1032-1033`），
    另 3 条落进这张清单：D-48 条目里 `__main__.py:626-629` 那一行
    （行内只有路径、没有符号），以及 §5 那两行新增表行
    （`__main__.py:626-629`、`sync.py:73-117`）—— 就是上面第二十二改那 +3）：
    **172 是 2026-10-10 D-49 之后的实测值**（+5 行：D-49 新增 13 条 `.py` 引用，
    其中 8 条落在有符号可校验的行上，另 5 条落进这张清单 —— 全在 D-49 条目里，
    §5 那三行新表行没有落进来的：`ARCHITECTURE.md` 的 `:1034`
    （`web/api.py:993`，±1 窗口有符号可旁证：`expected_as_of`）、`:1037`
    （`calendar.py:111-115`，±1 有符号可旁证）、`:1038`（`calendar.py:123`，
    ±1 窗口亦无符号）、`:1039`（`web/api.py:958`，±1 窗口亦无符号）、`:1073`
    （`web/api.py:408`，±1 有符号可旁证：`last_ts`）—— 就是上面第二十三改那 +5）：
    文档一增删就该有人重看这张清单 —— 这正是它存在的意义
    （它是一份人工复核清单，不是一个恒绿的指标）。
    """
    code, out = _run(tool, CHANLUN, CHANLUN / "ARCHITECTURE.md")
    assert code == 0, out
    m = re.search(r"引用行无可校验符号 (\d+)", out)
    assert m, out
    assert int(m.group(1)) == 175, f"实测 {m.group(1)} 行；重排/增删后请人工复核这张清单"
    rows = re.findall(r"^  L\d+ \| .+ \| \d+ \| ", out, re.M)
    assert len(rows) == 175, f"打印了 {len(rows)} 行，与计数不符"
    assert "不接入退出码" in out, out


# --------------------------------------------------------------------------- #
# 引用行内容指纹：整文件行号平移检测
# --------------------------------------------------------------------------- #
def _prepend_junk(path: Path, n: int = 15) -> None:
    body = path.read_text(encoding="utf-8")
    head = "\n".join(f"# 扰动填充 {i}" for i in range(1, n + 1))
    path.write_text(head + "\n" + body, encoding="utf-8")


def test_fingerprint_baseline_catches_whole_file_shift_that_old_criteria_miss(tool, tmp_path):
    """反向对照：头部插 15 行 ⇒ 旧四类判据 exit 0，指纹桶必须 exit 1。

    这是这一桶存在的唯一理由：整文件平移后，「行号在范围内」与「符号定义行在
    范围内」两个问题的答案都还是「在」，所以旧判据**在退出码上完全无反应**。
    """
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4`。\n")
    code, out = _run(tool, root, doc, "--update-baseline")
    assert code == 0, out
    assert (root / "optimizer" / "tools" / "doc_refs_baseline.json").is_file()

    _prepend_junk(root / "src" / "chanlun" / "chan" / "foo.py")

    old_code, old_out = _run(tool, root, doc, "--no-fingerprints")
    assert old_code == 0, old_out
    assert "失败 0 条" in old_out  # 旧判据：看不见平移

    new_code, new_out = _run(tool, root, doc)
    assert new_code == 1, new_out
    assert "不符 1 条" in new_out, new_out
    assert "foo.py:3-4#0" in new_out, new_out


def test_fingerprint_baseline_covers_non_py_refs_too(tool, tmp_path):
    """非 `.py` 桶也有指纹：它没有硬判据，指纹是它唯一的护栏。

    这条挡的是一次真实事故（2026-10-06）：那个桶原先只枚举、不判硬绿，也**不进
    指纹基线**，于是在 D-44 给 `index.html` / `app.js` 插行之后，5 条引用集体
    指向了别的函数/元素，而**全套测试仍然全绿** —— 静默腐烂了很久。
    反向对照：`app.js` 头部插行后，旧四类判据（`--no-fingerprints`）对非 `.py`
    引用**恒 exit 0**，指纹桶必须 exit 1。
    """
    js = "// 第一行\nconst A = 1;\nconst B = 2;\n"
    root, doc = _mini(
        tmp_path,
        {"src/chanlun/web/static/app.js": js},
        DOC_HEAD + "见 `app.js:2-3`。\n",
    )
    assert _run(tool, root, doc, "--update-baseline")[0] == 0

    app_js = root / "src" / "chanlun" / "web" / "static" / "app.js"
    app_js.write_text("// 插两行\n// 再插一行\n" + js, encoding="utf-8")

    old_code, old_out = _run(tool, root, doc, "--no-fingerprints")
    assert old_code == 0, old_out
    assert "非 .py 引用（1 处 / 1 行）" in old_out, old_out

    new_code, new_out = _run(tool, root, doc)
    assert new_code == 1, new_out
    assert "不符 1 条" in new_out, new_out
    assert "app.js:2-3#0" in new_out, new_out


def test_fingerprint_baseline_is_blind_to_doc_line_shift(tool, tmp_path):
    """负对照：只动**文档**（前面插一段话）⇒ 指纹必须仍是 0 条不符。

    键里不含文档行号就是为了这个：否则每次改 ARCHITECTURE.md 都会假警报一片。
    """
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4`。\n")
    assert _run(tool, root, doc, "--update-baseline")[0] == 0
    body = doc.read_text(encoding="utf-8")
    doc.write_text("<!-- 插一段，文档行号整体下移 -->\n\n" + body, encoding="utf-8")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "不符 0 条 / 未入基线 0 条 / 基线残留 0 条" in out, out


def test_fingerprint_baseline_is_blind_to_eof_append(tool, tmp_path):
    """负对照：在**被引文件末尾**追加一行 ⇒ 不动任何被引行，指纹必须仍是 0 条不符。

    这条挡的是「拿文件总行数当指纹」那种偷懒实现：那样每次 EOF 追加都会假红，
    而 EOF 追加恰恰是本项目推荐的写法（插在中间会移动后面所有被引行）。
    """
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4`。\n")
    assert _run(tool, root, doc, "--update-baseline")[0] == 0
    src = root / "src" / "chanlun" / "chan" / "foo.py"
    src.write_text(src.read_text(encoding="utf-8") + "# EOF 追加\n", encoding="utf-8")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "不符 0 条 / 未入基线 0 条 / 基线残留 0 条" in out, out


def test_fingerprint_covers_lines_inside_the_range_not_just_the_edges(tool, tmp_path):
    """区间**中段**改一个字也必须被抓住 —— 只钉首末两行的实现会漏掉它。"""
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-5`。\n")
    assert _run(tool, root, doc, "--update-baseline")[0] == 0
    src = root / "src" / "chanlun" / "chan" / "foo.py"
    lines = src.read_text(encoding="utf-8").splitlines()
    assert lines[3].strip() == "return 1"  # 第 4 行，落在区间 (3,5) 的**中段**
    lines[3] = "    return 999"
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code, out = _run(tool, root, doc)
    assert code == 1, out
    assert "不符 1 条" in out, out


def test_fingerprint_baseline_update_is_deterministic(tool, tmp_path):
    """同一份代码跑两次 --update-baseline，文件必须逐字节相同；重复引用各占一键。"""
    doc = DOC_HEAD + "见 `foo.py:3-4`。\n\n见 `foo.py:3-4`。\n"
    root, doc_path = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, doc)
    baseline = root / "optimizer" / "tools" / "doc_refs_baseline.json"
    assert _run(tool, root, doc_path, "--update-baseline")[0] == 0
    first = baseline.read_bytes()
    assert "foo.py:3-4#0" in first.decode("utf-8")
    assert "foo.py:3-4#1" in first.decode("utf-8")
    assert _run(tool, root, doc_path, "--update-baseline")[0] == 0
    assert baseline.read_bytes() == first


def test_fingerprint_baseline_missing_is_disclosed_as_not_running(tool, tmp_path):
    """没有基线文件时不许静默全绿：必须打印「这一桶没有在跑」。

    退出码仍由旧判据决定（0）是有意的：没有基线是「还没建」，不是「引用漂了」；
    但输出必须让人看见这一桶没跑 —— 否则它就是本项目最恨的那种恒绿指标。
    """
    root, doc = _mini(tmp_path, {"src/chanlun/chan/foo.py": FOO}, DOC_HEAD + "见 `foo.py:3-4`。\n")
    code, out = _run(tool, root, doc)
    assert code == 0, out
    assert "这一桶没有在跑" in out, out


def test_help_names_the_fingerprint_bucket_as_exit_code_owner(tool):
    """`--help` 也要说清指纹桶能改退出码（它和旧的「只披露」桶不是一类）。"""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        tool.main(["--help"])
    text = buf.getvalue()
    assert "引用行内容指纹不符" in text, text
    assert "--update-baseline" in text, text


def test_real_repo_fingerprint_baseline_is_committed_and_matches(tool):
    """真仓库：基线随仓库提交，且与当前被引行逐条对上 —— 这是整项硬化的总闸。

    任何「改了被引代码行却不重记指纹」的提交都会在这里变红；它跑在全量测试里，
    比 CLI 的退出码更难绕过。
    """
    baseline = CHANLUN / "optimizer" / "tools" / "doc_refs_baseline.json"
    assert baseline.is_file(), "指纹基线没提交；跑 --update-baseline 生成它"
    code, out = _run(tool, CHANLUN, CHANLUN / "ARCHITECTURE.md")
    assert code == 0, out
    assert "不符 0 条 / 未入基线 0 条 / 基线残留 0 条" in out, out
