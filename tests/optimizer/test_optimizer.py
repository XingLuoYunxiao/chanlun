"""7x24 优化器：理论库校验、补丁强制引用、日志与熔断。

这一组用例是「自治优化器」的安全带。优化器会自己找问题、自己写补丁，
所以必须有一条机器可执行的底线：**改动缠论算法的补丁，必须引用 theory/
里的条目 id 并附原文摘录，否则一律拒绝**。这条规则不能靠人自觉，
要靠测试和 `validate_patch` 一起钉死。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from chanlun.optimizer.agent import Optimizer, Probe, default_probes
from chanlun.optimizer.journal import (
    ADOPTED,
    CIRCUIT_BREAK,
    INCONCLUSIVE,
    PROPOSED,
    REJECTED,
    RETIRED,
    CircuitBreaker,
    JournalEntry,
    is_effective,
    read_all,
    write_entry,
)
from chanlun.optimizer.theory import (
    parse_header,
    ALGO_PREFIXES,
    is_algo_path,
    TheoryEntry,
    load_theory,
    search,
    touched_paths,
    validate_patch,
)

CHANLUN = Path(__file__).resolve().parents[2]
THEORY_DIR = CHANLUN / "optimizer" / "theory"
PATCH_DIR = CHANLUN / "optimizer" / "patches"
JOURNAL_DIR = CHANLUN / "optimizer" / "journal"

REQUIRED_ENTRY_IDS = {
    "L17-TREND-DEF",
    "L20-PIVOT-DEFINITION",
    "L20-PIVOT-EXTENSION",
    "L20-THIRD-POINT-POSITION",
    "L24-MACD-AREA-ABC",
    "L27-DIVERGENCE-UNIT",
    "L29-PIVOT-LEVEL-EXPANSION",
    "L67-SEGMENT-CHAR-SEQ",
}
JOURNAL_FIELDS = {
    "round", "started_at", "kind", "finding", "evidence", "theory_id",
    "patch_file", "before", "after", "tests", "status",
}


# --------------------------------------------------------------- 夹具


def _algo_patch(body: str = "+    return 1\n", path: str = "src/chanlun/chan/signal.py") -> str:
    """一个只碰缠论算法文件的合法 unified diff。"""
    return textwrap.dedent(
        f"""\
        diff --git a/{path} b/{path}
        --- a/{path}
        +++ b/{path}
        @@ -1,1 +1,2 @@
         # -*- coding: utf-8 -*-
        {body}"""
    )


def _header(**kw: str) -> str:
    lines = []
    for key, value in kw.items():
        for i, part in enumerate(str(value).splitlines() or [""]):
            lines.append(f"# {key}: {part}" if i == 0 else f"#    {part}")
    return "\n".join(lines) + "\n"


@pytest.fixture()
def theory() -> dict[str, TheoryEntry]:
    return load_theory(THEORY_DIR)


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """一个最小可跑的「主干」：一个缠论算法文件 + optimizer 目录。"""
    (tmp_path / "src" / "chanlun" / "chan").mkdir(parents=True)
    (tmp_path / "src" / "chanlun" / "chan" / "signal.py").write_text(
        "# -*- coding: utf-8 -*-\nx = 0\n", encoding="utf-8"
    )
    (tmp_path / "src" / "chanlun" / "__init__.py").write_text("", encoding="utf-8")
    for name in ("theory", "patches", "journal"):
        (tmp_path / "optimizer" / name).mkdir(parents=True)
    (tmp_path / "optimizer" / "theory" / "t.md").write_text(
        "---\nid: T-TEST\nsource: 《测试》第1课\napplies_to: chan/signal.py\n---\n\n"
        "## 原文摘录\n\n> 测试用的原文摘录。\n\n## 适用算法\n\n- chan/signal.py\n",
        encoding="utf-8",
    )
    return tmp_path


# =============================================================== 1. 强制引用


def test_patch_without_theory_reference_is_rejected(theory):
    """**核心契约**：改缠论算法却没引用 theory/ 条目的补丁，必须被拒绝。"""
    verdict = validate_patch(_algo_patch(), theory)
    assert verdict.ok is False
    assert verdict.kind == "theory"
    assert "theory" in " ".join(verdict.reasons)
    assert verdict.theory_id is None


def test_patch_with_empty_header_is_rejected(theory):
    verdict = validate_patch(_algo_patch(), theory)
    assert not verdict.ok
    assert verdict.reasons


def test_patch_with_theory_reference_is_accepted(theory):
    entry = theory["L24-MACD-AREA-ABC"]
    patch = _header(
        kind="theory", theory=entry.id, quote=entry.quote.splitlines()[0]
    ) + _algo_patch()
    verdict = validate_patch(patch, theory)
    assert verdict.ok, verdict.reasons
    assert verdict.kind == "theory"
    assert verdict.theory_id == entry.id


def test_patch_with_unknown_theory_id_is_rejected(theory):
    patch = _header(
        kind="theory", theory="L99-NOT-A-REAL-ID", quote="随便一句"
    ) + _algo_patch()
    verdict = validate_patch(patch, theory)
    assert not verdict.ok
    assert any("L99-NOT-A-REAL-ID" in r for r in verdict.reasons)


def test_patch_with_out_of_context_quote_is_rejected(theory):
    """引用了真 id，但摘录跟条目原文对不上 —— 同样拒绝（防编造原文）。"""
    patch = _header(
        kind="theory", theory="L24-MACD-AREA-ABC", quote="这句原文并不存在"
    ) + _algo_patch()
    verdict = validate_patch(patch, theory)
    assert not verdict.ok
    assert any("quote" in r for r in verdict.reasons)


def test_algo_patch_declared_engineering_is_rejected(theory):
    patch = _header(kind="engineering", evidence="跑了一遍，看起来更快") + _algo_patch()
    verdict = validate_patch(patch, theory)
    assert not verdict.ok
    assert verdict.kind == "theory"


def test_engineering_patch_requires_evidence(theory):
    patch = _header(kind="engineering") + _algo_patch(path="src/chanlun/data/store.py")
    verdict = validate_patch(patch, theory)
    assert not verdict.ok
    assert verdict.kind == "engineering"


def test_engineering_patch_with_evidence_is_accepted(theory):
    patch = _header(
        kind="engineering",
        evidence="$ cmd\n 真实原始输出一行",
    ) + _algo_patch(path="src/chanlun/data/store.py")
    verdict = validate_patch(patch, theory)
    assert verdict.ok, verdict.reasons
    assert verdict.kind == "engineering"
    assert verdict.theory_id is None


def test_patch_touching_no_file_is_rejected(theory):
    assert not validate_patch("# kind: theory\n", theory).ok


@pytest.mark.parametrize(
    "path",
    [
        "src/chanlun/chan/signal.py",
        "src/chanlun/scan/universe.py",
        "src/chanlun/backtest/runner.py",
        "src/chanlun/web/api.py",
        "chanlun/src/chanlun/chan/pivot.py",
    ],
)
def test_every_algo_prefix_needs_theory(theory, path):
    assert not validate_patch(_algo_patch(path=path), theory).ok


def test_algo_prefixes_cover_the_contract(theory):
    """算法路径 = 会改「算出来是什么」的代码；展示层不算。

    判据（G11d，已采纳）：``web/static/`` 是 HTML/CSS/JS，改一句文案、给未确认的
    买卖点补一个标记，都不该被要求先伪造一条 theory 条目 —— 否则验收端会逼着
    提案人写假原文。反过来，``web/api.py`` / ``web/app.py`` 决定 level、复权、
    确认口径怎么暴露给用户，仍然是算法路径。
    """
    assert set(ALGO_PREFIXES) >= {
        "src/chanlun/chan/",
        "src/chanlun/scan/",
        "src/chanlun/backtest/",
        "src/chanlun/web/api.py",
        "src/chanlun/web/app.py",
    }
    # 服务端的 web 模块算算法路径（口径从这儿出去）
    assert is_algo_path("src/chanlun/web/api.py")
    assert is_algo_path("src/chanlun/web/app.py")
    # 纯展示层不算：这四条一旦变红，说明又有人把整个 web/ 目录当成算法路径了
    assert not is_algo_path("src/chanlun/web/static/app.js")
    assert not is_algo_path("src/chanlun/web/static/styles.css")
    assert not is_algo_path("src/chanlun/web/static/index.html")
    assert not is_algo_path("src/chanlun/web/__init__.py")


# =============================================================== 2. 理论库


def test_theory_library_is_populated_and_well_formed(theory):
    assert len(theory) >= len(REQUIRED_ENTRY_IDS)
    for entry in theory.values():
        assert entry.id, entry
        assert entry.source, entry
        assert entry.quote.strip(), f"{entry.id} 缺原文摘录"
        assert entry.applies_to, f"{entry.id} 缺适用算法"
        assert entry.lesson, f"{entry.id} 缺课号"


def test_theory_library_has_the_required_entries(theory):
    assert REQUIRED_ENTRY_IDS <= set(theory)


def test_theory_entry_is_hashable_and_frozen(theory):
    entry = next(iter(theory.values()))
    assert isinstance(hash(entry), int)
    with pytest.raises(Exception):
        entry.id = "changed"  # type: ignore[misc]


def test_search_finds_the_divergence_unit_entry(theory):
    hits = search(theory, "背驰 比较 走势类型 中枢", limit=3)
    assert hits
    assert "L27-DIVERGENCE-UNIT" in {e.id for e in hits}


def test_search_finds_the_nine_segment_entry(theory):
    hits = search(theory, "中枢 延伸 九段 级别扩张", limit=3)
    assert "L29-PIVOT-LEVEL-EXPANSION" in {e.id for e in hits}


def test_search_on_empty_library_returns_empty():
    assert search({}, "背驰") == []


def test_load_theory_rejects_a_malformed_entry(tmp_path: Path):
    bad = tmp_path / "bad.md"
    bad.write_text("---\nid: X\n---\n\n没有摘录也没有适用算法\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_theory(tmp_path)


def test_load_theory_rejects_duplicate_ids(tmp_path: Path):
    body = "---\nid: DUP\nsource: s\napplies_to: a\nlesson: '1'\n---\n\n## 原文摘录\n\n> q\n"
    (tmp_path / "a.md").write_text(body, encoding="utf-8")
    (tmp_path / "b.md").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_theory(tmp_path)


# =============================================================== 3. 交付物自检


def test_real_patches_all_pass_validation(theory):
    patches = sorted(PATCH_DIR.glob("*.patch"))
    assert patches, "optimizer/patches/ 里应当有提案补丁"
    for path in patches:
        verdict = validate_patch(path.read_text(encoding="utf-8"), theory)
        assert verdict.ok, f"{path.name}: {verdict.reasons}"


def _is_adopted(patch_text: str) -> bool:
    """补丁头里 `# status: adopted` = 提案已被主干采纳。

    采纳的定义就是「补丁已经变成主干的一部分」，所以它当然 apply 不上去；
    再拿「能不能 apply」当自检，会在采纳当天把正常流程报成故障。
    """
    return parse_header(patch_text).get("status", "").strip().lower() == ADOPTED


def _is_retired(patch_text: str) -> bool:
    """补丁头里 `# status: retired` = 提案的**前提**已被证伪。

    证伪与采纳的区别：采纳是补丁进了主干，证伪是补丁的判据本身不成立。两者
    都从「待评审」里退出，但证伪的补丁**继续留在盘上**当证据 —— 它的前提、
    证伪理由、以及证伪当时的 before/after 都在补丁头里，
    `test_real_retired_probe_is_recorded_as_rejected` 负责钉住这一点。
    证伪的补丁同样不该再要求「能 apply」：它的前提既然不成立，主干往前走之后
    它自然会对不上（round-004-G2a 就是这样 —— 它的 9 段上限被 round-012-G11b
    的 8 段取代，同一个延伸循环已经不是原来那几行了）。
    """
    return parse_header(patch_text).get("status", "").strip().lower() == RETIRED


def test_real_patches_apply_and_revert_cleanly(tmp_path: Path):
    """每个**待评审**提案补丁都必须能在主干副本上 apply → 反 apply，且内容逐字复原。

    已采纳（`# status: adopted`）与已证伪（`# status: retired`）的提案不在其列：
    前者的判据已经搬进常驻回归测试，见 `test_adopted_patches_are_regression_tested_in_trunk`；
    后者只作证据留档，见 `test_real_retired_probe_is_recorded_as_rejected`。
    """
    patches = [p for p in sorted(PATCH_DIR.glob("*.patch"))
               if not (_is_adopted(p.read_text(encoding="utf-8"))
                       or _is_retired(p.read_text(encoding="utf-8")))]
    assert patches
    for path in patches:
        touched = touched_paths(path.read_text(encoding="utf-8"))
        assert touched, f"{path.name} 没有触及任何文件"
        root = tmp_path / path.stem
        originals: dict[str, str] = {}
        for rel in touched:
            src = CHANLUN / rel
            if not src.is_file():
                continue
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            originals[rel] = dst.read_text(encoding="utf-8")
        assert originals, f"{path.name} 没有触及任何真实存在的文件"
        ahead = subprocess.run(
            ["git", "apply", "-p1", str(path)], cwd=root, capture_output=True, text=True
        )
        assert ahead.returncode == 0, f"{path.name} 无法 apply: {ahead.stderr}"
        assert any(
            (root / rel).read_text(encoding="utf-8") != text
            for rel, text in originals.items()
        ), f"{path.name} apply 后文件内容没变"
        back = subprocess.run(
            ["git", "apply", "-p1", "-R", str(path)], cwd=root, capture_output=True, text=True
        )
        assert back.returncode == 0, f"{path.name} 无法反 apply: {back.stderr}"
        for rel, text in originals.items():
            assert (root / rel).read_text(encoding="utf-8") == text, (
                f"{path.name} 反 apply 后 {rel} 没有逐字复原"
            )


def test_real_journal_entries_have_every_required_field():
    entries = read_all(JOURNAL_DIR)
    assert entries, "optimizer/journal/ 里应当有每轮的记录"
    for entry in entries:
        payload = entry.to_dict()
        assert JOURNAL_FIELDS <= set(payload), f"round {entry.round} 缺字段"
        assert entry.status in {PROPOSED, REJECTED, INCONCLUSIVE, CIRCUIT_BREAK, ADOPTED}
        assert entry.kind in {"theory", "engineering"}
        assert entry.evidence.strip(), f"round {entry.round} 缺实测证据"
        if entry.kind == "theory" and entry.patch_file:
            assert entry.theory_id, f"round {entry.round} 理论类补丁必须带 theory_id"


# =============================================================== 4. 日志


def _entry(round_no: int, **over) -> JournalEntry:
    base = dict(
        round=round_no,
        started_at="2026-01-01T00:00:00",
        kind="engineering",
        finding="f",
        evidence="e",
        theory_id=None,
        patch_file=None,
        before={"signals": 0},
        after={"signals": 0},
        tests={"before": "green", "after": "green"},
        status=PROPOSED,
    )
    base.update(over)
    return JournalEntry(**base)


def test_journal_roundtrip(tmp_path: Path):
    entry = _entry(3, before={"signals": 0}, after={"signals": 2})
    path = write_entry(tmp_path, entry)
    assert path.name == "round-003.json"
    assert path.read_text(encoding="utf-8").endswith("\n")
    back = read_all(tmp_path)
    assert len(back) == 1
    assert back[0].round == 3
    assert back[0].after == {"signals": 2}


def test_journal_read_all_is_sorted_and_skips_junk(tmp_path: Path):
    write_entry(tmp_path, _entry(2))
    write_entry(tmp_path, _entry(1))
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    assert [e.round for e in read_all(tmp_path)] == [1, 2]


def test_journal_rejects_unknown_status():
    with pytest.raises(ValueError, match="status"):
        _entry(1, status="perfect")


def test_journal_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind"):
        _entry(1, kind="vibes")


def test_journal_rejects_missing_evidence():
    with pytest.raises(ValueError, match="evidence"):
        _entry(1, evidence="  ")


def test_is_effective_requires_numbers_that_actually_moved():
    assert is_effective(_entry(1, before={"signals": 0}, after={"signals": 2}))
    assert not is_effective(_entry(2, before={"signals": 0}, after={"signals": 0}))
    assert not is_effective(_entry(3, status=INCONCLUSIVE, after={"signals": 2}))
    assert not is_effective(_entry(4, status=CIRCUIT_BREAK, after={"signals": 2}))


# =============================================================== 5. 熔断


def test_circuit_breaker_trips_after_three_barren_rounds():
    entries = [_entry(i, status=INCONCLUSIVE) for i in range(1, 4)]
    state = CircuitBreaker().evaluate(entries)
    assert state.should_break is True
    assert "3" in state.reason


def test_circuit_breaker_does_not_trip_on_two_barren_rounds():
    entries = [_entry(i, status=INCONCLUSIVE) for i in range(1, 3)]
    assert CircuitBreaker().evaluate(entries).should_break is False


def test_circuit_breaker_resets_the_streak_on_an_effective_round():
    entries = [
        _entry(1, status=INCONCLUSIVE),
        _entry(2, status=INCONCLUSIVE),
        _entry(3, before={"signals": 0}, after={"signals": 1}),
        _entry(4, status=INCONCLUSIVE),
    ]
    state = CircuitBreaker().evaluate(entries)
    assert state.should_break is False
    assert state.barren_streak == 1


def test_circuit_breaker_trips_on_two_green_to_red_transitions():
    entries = [
        _entry(1, tests={"before": "green", "after": "red", "summary": "1 failed"}),
        _entry(2, before={"signals": 0}, after={"signals": 1},
               tests={"before": "green", "after": "red", "summary": "2 failed"}),
    ]
    state = CircuitBreaker().evaluate(entries)
    assert state.should_break is True
    assert state.red_transitions == 2


def test_circuit_breaker_stops_reading_after_a_break_entry():
    entries = [
        _entry(1, status=CIRCUIT_BREAK, tests={"before": "green", "after": "red"}),
        _entry(2, status=INCONCLUSIVE),
        _entry(3, status=INCONCLUSIVE),
    ]
    state = CircuitBreaker().evaluate(entries)
    # 第一轮就已经断了，后面不该再累计
    assert state.red_transitions == 1
    assert state.should_break is True


def test_cli_exits_non_zero_on_circuit_break(tmp_path: Path, fake_repo: Path):
    """熔断必须是非零退出码，否则 systemd 会当成正常结束而不报警。"""
    from chanlun.optimizer.cli import EXIT_CIRCUIT_BREAK, main

    for i in (1, 2, 3):
        write_entry(
            fake_repo / "optimizer" / "journal",
            _entry(i, status=INCONCLUSIVE),
        )
    code = main(["--root", str(fake_repo), "--rounds", "1", "--propose-only"])
    assert code == EXIT_CIRCUIT_BREAK


# =============================================================== 6. 代理行为


def test_optimizer_never_writes_inside_trunk(fake_repo: Path):
    """提案模式：跑一轮，主干源码一个字节都不能变。"""
    theory = load_theory(fake_repo / "optimizer" / "theory")
    entry = theory["T-TEST"]
    patch = (
        _header(kind="theory", theory=entry.id, quote=entry.quote.splitlines()[0])
        + _algo_patch()
    )
    probe = Probe(
        rid="T1",
        kind="theory",
        finding="测试用的发现",
        theory_id=entry.id,
        evidence="cat src/chanlun/chan/signal.py\n# -*- coding: utf-8 -*-\nx = 0",
        patch_text=patch,
        before={"x": 0},
        after={"x": 1},
        tests={"before": "skip", "after": "skip", "summary": "unit"},
    )
    opt = Optimizer(fake_repo, propose_only=True)
    trunk = fake_repo / "src" / "chanlun" / "chan" / "signal.py"
    digest = trunk.read_bytes()
    before_all = {
        str(p.relative_to(fake_repo)): p.read_bytes()
        for p in (fake_repo / "src").rglob("*") if p.is_file()
    }

    recorded = opt.run_round(1, probe)

    assert recorded.status == PROPOSED
    assert trunk.read_bytes() == digest
    after_all = {
        str(p.relative_to(fake_repo)): p.read_bytes()
        for p in (fake_repo / "src").rglob("*") if p.is_file()
    }
    assert after_all == before_all
    assert (fake_repo / "optimizer" / "patches" / "round-001-T1.patch").exists()
    assert (fake_repo / "optimizer" / "journal" / "round-001.json").exists()


def test_optimizer_refuses_to_record_an_invalid_patch(fake_repo: Path):
    """没有理论引用的补丁，代理不能记成 proposed。"""
    probe = Probe(
        rid="T2",
        kind="theory",
        finding="没引用原文的改动",
        theory_id=None,
        evidence="none",
        patch_text=_algo_patch(),
        before={"x": 0},
        after={"x": 1},
    )
    opt = Optimizer(fake_repo, propose_only=True)
    recorded = opt.run_round(1, probe)
    assert recorded.status == REJECTED
    assert not (fake_repo / "optimizer" / "patches" / "round-001-T2.patch").exists()
    assert recorded.patch_file is None


def test_optimizer_stages_the_patch_and_records_the_theory_id(fake_repo: Path):
    theory = load_theory(fake_repo / "optimizer" / "theory")
    entry = theory["T-TEST"]
    patch = (
        _header(kind="theory", theory=entry.id, quote=entry.quote.splitlines()[0])
        + _algo_patch()
    )
    probe = Probe(
        rid="T3", kind="theory", finding="f", theory_id=entry.id, evidence="e",
        patch_text=patch, before={"signals": 0}, after={"signals": 1},
    )
    opt = Optimizer(fake_repo, propose_only=True)
    recorded = opt.run_round(1, probe)
    assert recorded.patch_file == "round-001-T3.patch"
    assert recorded.theory_id == entry.id
    staged = (fake_repo / "optimizer" / "patches" / "round-001-T3.patch")
    assert staged.read_text(encoding="utf-8") == patch


def test_optimizer_stops_the_loop_when_the_breaker_trips(fake_repo: Path):
    probes = [
        Probe(rid=f"T{i}", kind="engineering", finding="f", theory_id=None,
              evidence="e", patch_text=None, before={"n": 0}, after={"n": 0})
        for i in range(1, 6)
    ]
    opt = Optimizer(fake_repo, propose_only=True)
    recorded = opt.run(rounds=5, probes=probes)
    assert len(recorded) == 3
    assert recorded[-1].status == CIRCUIT_BREAK
    assert opt.state.should_break


def test_probe_without_patch_is_inconclusive_not_proposed(fake_repo: Path):
    probe = Probe(rid="T9", kind="theory", finding="证据不足", theory_id=None,
                  evidence="只跑了一次观测，下不了结论", patch_text=None,
                  before={"signals": 0}, after={"signals": 0})
    recorded = Optimizer(fake_repo, propose_only=True).run_round(1, probe)
    assert recorded.status == INCONCLUSIVE
    assert recorded.patch_file is None


def test_cli_list_probes_does_not_touch_anything(fake_repo: Path, capsys):
    from chanlun.optimizer.cli import main

    code = main(["--root", str(fake_repo), "--list-probes"])
    assert code == 0
    assert not (fake_repo / "optimizer" / "journal").iterdir().__next__() if False else True
    assert list((fake_repo / "optimizer" / "journal").iterdir()) == []


def test_theory_entry_serializes_to_json(theory):
    entry = theory["L27-DIVERGENCE-UNIT"]
    payload = json.loads(json.dumps(entry.to_dict(), ensure_ascii=False))
    assert payload["id"] == "L27-DIVERGENCE-UNIT"
    assert payload["quote"]


def test_adopted_patch_is_not_handed_back_as_a_proposal(fake_repo: Path):
    """已采纳的补丁**不能再当提案量一遍**：它已经是主干的一部分。

    影子目录里 `git apply` 会直接失败（那些 hunk 主干里有了），24x7 回访
    因此会在采纳当天崩掉。正确做法是把它标成 ``adopted`` 并交出**空补丁**：
    回访列表照旧（轮次号稳定），但这一轮没有提案可量。
    """
    patches = fake_repo / "optimizer" / "patches"
    (patches / "round-001-G1a.patch").write_text(
        _header(kind="engineering", status="adopted") + _algo_patch(), encoding="utf-8"
    )
    (patches / "round-002-G1b.patch").write_text(
        _header(kind="engineering", status="proposed") + _algo_patch(), encoding="utf-8"
    )

    by_rid = {p.rid: p for p in default_probes(fake_repo)}
    adopted, pending = by_rid["G1a"], by_rid["G1b"]

    assert adopted.adopted is True
    assert adopted.patch_text is None, "已采纳的补丁不该再交给影子目录 apply"
    assert "已采纳" in adopted.notes
    assert "常驻回归" in adopted.notes

    assert pending.adopted is False
    assert pending.patch_text is not None, "待评审提案必须照旧交出补丁文本"


def test_optimizer_run_cycles_only_pending_probes(fake_repo: Path):
    """24x7 循环只在**待评审**的观测点里转，不把已采纳的重新量成 inconclusive。"""
    pending = Probe(rid="P1", kind="engineering", finding="f", evidence="e")
    adopted = Probe(
        rid="P2", kind="engineering", finding="f", evidence="e", adopted=True
    )
    opt = Optimizer(fake_repo, propose_only=True, measure=False)
    rounds = opt.run(2, probes=[pending, adopted])
    assert [e.probe for e in rounds] == ["P1", "P1"]


def test_retired_patch_is_not_handed_back_as_a_proposal(fake_repo: Path):
    """**前提被证伪**的补丁不能再当提案量：它不会变好，只会每轮再写一条 inconclusive。

    与 ``adopted`` 的区别是：采纳是「补丁进了主干」，证伪是「补丁的前提不成立」，
    所以日志状态必须是 rejected 而不是 adopted。两者都交出空补丁 —— 观测点仍占位
    （轮次号是 journal 的历史主键），但这一轮没有提案可量。
    """
    patches = fake_repo / "optimizer" / "patches"
    (patches / "round-003-G1c.patch").write_text(
        _header(
            kind="engineering",
            status="retired",
            note="前提被 G1a 证伪：线段方向交替，嵌套 if 吞不掉另一侧。",
        )
        + _algo_patch(),
        encoding="utf-8",
    )

    by_rid = {p.rid: p for p in default_probes(fake_repo)}
    retired = by_rid["G1c"]

    assert retired.retired is True
    assert retired.adopted is False, "证伪不是采纳"
    assert retired.patch_text is None, "已证伪的补丁不该再交给影子目录 apply"
    assert "[已证伪]" in retired.notes
    assert "线段方向交替" in retired.notes, "补丁头的证伪结论要带进观测点"


def test_optimizer_run_skips_retired_and_adopted_probes_alike(fake_repo: Path):
    """回访循环只转「还值得量」的观测点：已证伪与已采纳都跳过，且不挪轮次。"""
    pending = Probe(rid="P1", kind="engineering", finding="f", evidence="e")
    adopted = Probe(
        rid="P2", kind="engineering", finding="f", evidence="e", adopted=True
    )
    retired = Probe(
        rid="P3", kind="engineering", finding="f", evidence="e", retired=True
    )
    opt = Optimizer(fake_repo, propose_only=True, measure=False)
    rounds = opt.run(3, probes=[pending, adopted, retired])
    assert [e.probe for e in rounds] == ["P1", "P1", "P1"]


def test_adopted_journal_entry_separates_history_from_the_current_trunk():
    """已采纳那几轮的数字是**采纳前**的历史记录，必须与主干现状分开写。

    它们 evidence 里那条 `--patch round-001-G1a.patch` 如今跑不起来（补丁已是主干
    的一部分，`git apply` 会跳过），所以那段数字不可能再复核。留着它有价值 ——
    它记录了这次修正到底改了什么 —— 但读的人不能把它当成当前口径，因此
    必须同时给出主干现状那一行。
    """
    import sys

    sys.path.insert(0, str(CHANLUN / "src"))
    from chanlun.optimizer.journal import ADOPTED, read_all

    adopted = [e for e in read_all(JOURNAL_DIR) if e.status == ADOPTED]
    assert adopted, "至少应有已采纳的轮次（G1a/G1b/G5a/G5b）"
    for entry in adopted:
        assert "[主干现状]" in entry.notes, f"round {entry.round:03d} 没写主干现状"
        assert entry.evidence.startswith("# [采纳前]"), (
            f"round {entry.round:03d} 的采纳前数字没标明，会被读成当前口径"
        )


def test_real_retired_probe_is_recorded_as_rejected():
    """真仓里的证伪结论必须落在 journal 里，且**不是** proposed —— 否则两套说法。

    round-003-G1c 的前提是「嵌套 if 会吞掉本该成立的 S3」。主干按第20课的位置口径
    修好后，离开段与回试段的方向必然相反，嵌套 if 结构上不可能吞掉另一侧
    （见 ``optimizer/tools/diag_third_point_nesting.py``：g1c_adds_s3=0、g1c_adds_b3=0）。
    """
    import sys

    sys.path.insert(0, str(CHANLUN / "src"))
    from chanlun.optimizer.journal import REJECTED, read_all

    retired_patches = [
        p for p in sorted(PATCH_DIR.glob("*.patch"))
        if parse_header(p.read_text(encoding="utf-8")).get("status", "").strip().lower()
        == "retired"
    ]
    assert retired_patches, "至少应有一个已证伪的提案（round-003-G1c）"
    by_probe = {e.probe: e for e in read_all(JOURNAL_DIR)}
    for path in retired_patches:
        rid = path.stem.split("-")[2]  # round-003-G1c.patch → G1c
        entry = by_probe[rid]
        assert entry.status == REJECTED, f"{rid} 的证伪结论没落到 journal"
        assert "[已证伪]" in entry.notes
        assert entry.patch_file, "证伪也要留档：补丁文件就是证据"


def test_adopted_patches_are_regression_tested_in_trunk():
    """已采纳的提案，判据必须落在主干测试里 —— 否则「采纳」只是删掉了一个补丁。

    round-010-G5b 的原始判据是 `store.read('sh.600030')` 能读到数据（0 → 38 行）。
    主干采纳后这条判据升级为：带前缀的代码必须解析到**正确市场目录下的裸文件名**，
    且指数键不得与同号段的个股抢同一个文件。这里直接在真仓上验这两点。
    """
    import sys

    sys.path.insert(0, str(CHANLUN / "src"))
    from chanlun.data import store

    adopted = [p for p in sorted(PATCH_DIR.glob("*.patch"))
               if _is_adopted(p.read_text(encoding="utf-8"))]
    assert adopted, "至少应有一个已采纳提案（round-010-G5b）"
    assert store.path_for("sh.600030", "day").as_posix().endswith("day/sh/600030.parquet")
    assert store.bare_code("sh.600030") == "600030"
    assert store.path_for("sh.000300", "day") != store.path_for("sz.000300", "day")
