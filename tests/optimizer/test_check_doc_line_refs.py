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
    """真文档：硬判据全绿，且 5 处 `app.js` 引用被逐条枚举（此前从未被任何校验器看过）。"""
    code, out = _run(tool, CHANLUN, CHANLUN / "ARCHITECTURE.md")
    assert code == 0, out
    assert "失败 0 条" in out
    assert "非 .py 引用（5 处 / 5 行）" in out
    assert out.count("（1487 行）") == 5, out
    # ★ 这里**故意不钉文档行号**（`L478` 之类）：文档一增删就漂，而这类
    #   「把行号抄进另一份文件」正是本任务要治的病。钉**被引用的 app.js 行区间**
    #   —— 它们才是这 5 条引用各自的身份证。
    # 2026-10-03：顶栏布局稳定化改了 app.js（1461 → 1483，+22 行），后三处区间随之整体
    #   下移 +9，这里和 ARCHITECTURE.md §4.2 同步更新（前三处在该插入点之前，未动）。
    #   区间是按**内容**重锚的，不是拿 +9 硬加出来的。
    # 2026-10-03 同日第二改：D-37/D-38 之后图注「GG/DD 不含离开段」不再无条件成立，
    #   `TREND_BASIS` 及其注释块改写，app.js 1483 → 1487（+4，插入点在第 46 行）。
    #   5 处区间再次按**内容**逐条重锚（`grep -n` 定位原句，不是把 +4 加到旧号上）；
    #   其中 `:1066-1068` 实测原就多含了一行（真身是 2 行注释），这次收成 `:1070-1071`。
    for ref in ("src/chanlun/web/static/app.js:58", "app.js:213-216",
                "app.js:1070-1071", "app.js:1414-1415", "app.js:1479-1484"):
        assert ref in out, ref
    # 枚举行必须带文档行号字段（只钉形状，不钉值）
    assert len(re.findall(r"^  L\d+: ", out, re.M)) >= 5, out


def test_real_architecture_doc_reports_py_totals(tool):
    """.py 桶总数：全文 186 处 / 176 行；§3.4 60 处 / 58 行（与探针逐项一致）。"""
    _, out = _run(tool, CHANLUN, CHANLUN / "ARCHITECTURE.md")
    assert "引用总数（.py 桶）：全文 186 处 / 176 行；§3.4 60 处 / 58 行" in out


def test_real_architecture_doc_discloses_unverifiable_lines(tool):
    """真文档：新桶逐行披露、条数与列出的行数一致，且**不接退出码**。

    129 是 2026-10-03（Task 10a-R2）实测值：文档一增删就该有人重看这张清单 ——
    这正是它存在的意义（它是一份人工复核清单，不是一个恒绿的指标）。
    """
    code, out = _run(tool, CHANLUN, CHANLUN / "ARCHITECTURE.md")
    assert code == 0, out
    m = re.search(r"引用行无可校验符号 (\d+)", out)
    assert m, out
    assert int(m.group(1)) == 129, f"实测 {m.group(1)} 行；重排/增删后请人工复核这张清单"
    rows = re.findall(r"^  L\d+ \| .+ \| \d+ \| ", out, re.M)
    assert len(rows) == 129, f"打印了 {len(rows)} 行，与计数不符"
    assert "不接入退出码" in out, out
