"""`python -m chanlun levels`：级别递归的命令行出口（D-51）。

这个子命令的全部价值就是「能人工核对」：在它之前，真递归只能从 Python 里调
`chan.recursion.build_levels`。所以这里钉的不是算法（算法在
`tests/chan/test_recursion.py`），而是**出口本身**：参数解析、退出码、
人类可读格式、JSON 形状，以及「没有数据」这条路径不许静默成功。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from chanlun import __main__ as cli
from chanlun.config import Config, DataConfig
from chanlun.data import meta, store


def _series(seed: int = 0, n: int = 6000) -> pd.DataFrame:
    """确定性多尺度序列：够切出两级（L0 由线段构成、L1 由 L0 走势类型上推）。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    base = (100 + 20 * np.sin(t / 400) + 10 * np.sin(t / 90) + 5 * np.sin(t / 28)
            + 2.5 * np.sin(t / 9) + rng.normal(0, 0.35, n).cumsum() * 0.2)
    hi = base + np.abs(rng.normal(0, 0.35, n))
    lo = base - np.abs(rng.normal(0, 0.35, n))
    return pd.DataFrame({
        "ts": pd.date_range("2020-01-01", periods=n).astype(str),
        "open": base,
        "high": np.maximum(hi, base),
        "low": np.minimum(lo, base),
        "close": base,
        "volume": 1.0,
        "amount": 1.0,
    })


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = Config(
        data=DataConfig(root=tmp_path / "data"),
        log_path=tmp_path / "logs" / "chanlun.log",
    )
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(store, "DATA_ROOT", cfg.data.root)
    return cfg


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return _series()


def test_missing_data_fails_loudly(env, capsys):
    """本地没数据 → 退出码 1 且给出可照抄的补救命令，不许静默成功。"""
    rc = cli.main(["levels", "--codes", "600000", "--period", "day"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "本地没有数据" in out
    assert "sync --period day" in out


def test_prints_recursive_levels(env, bars, capsys):
    store.upsert("600000", "day", bars)
    rc = cli.main(["levels", "--codes", "600000", "--period", "day"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "基准显微镜=day" in out
    # 第 0 层来自线段（f1），第 1 层由第 0 层走势类型上推（f2）
    assert "线段直接构成（f1）" in out
    assert "上推（f2）" in out
    assert "day" in out and "week" in out
    # 无 --verbose 时给出提示而不是把上百条中枢刷屏
    assert "--verbose" in out


def test_verbose_lists_pivots_and_trends(env, bars, capsys):
    store.upsert("600000", "day", bars)
    rc = cli.main(["levels", "--codes", "600000", "--period", "day", "--verbose"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "级（depth=0）" in out
    assert " ZD=" in out and " ZG=" in out
    # 走势类型行带方向；盘整的方向必须是 0（第 17 课不给盘整分方向）
    assert "dir=+0" in out or "dir=+1" in out or "dir=-1" in out


def test_json_is_machine_readable(env, bars, capsys):
    store.upsert("600000", "day", bars)
    rc = cli.main(["levels", "--codes", "600000", "--period", "day", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["code"] == "600000"
    assert payload["period"] == "day"
    assert payload["segments"] > 0
    assert payload["problems"] == []
    levels = payload["levels"]
    assert [lv["depth"] for lv in levels] == list(range(len(levels)))
    assert levels[0]["level"] == "day"
    assert levels[0]["pivots"], "第 0 层必须有中枢，否则这个命令没测到东西"
    # 走势类型的区间与方向都出得来（f2 的输入）
    assert "high" in levels[0]["trends"][0] and "direction" in levels[0]["trends"][0]


def test_max_depth_limits_escalation(env, bars, capsys):
    store.upsert("600000", "day", bars)
    rc = cli.main(["levels", "--codes", "600000", "--period", "day",
                   "--max-depth", "1", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [lv["level"] for lv in payload["levels"]] == ["day"]


def test_multiple_codes_are_all_reported(env, bars, capsys):
    store.upsert("600000", "day", bars)
    rc = cli.main(["levels", "--codes", "600000,600001", "--period", "day"])
    out = capsys.readouterr().out
    # 一只有、一只没有：整体退出码 1，但两只都要在输出里交代清楚
    assert rc == 1
    assert "600000" in out and "600001" in out
    assert "本地没有数据" in out


def test_command_is_registered_in_parser():
    """参数长在命令自己身上，父分发表必须认得它。"""
    parser = cli.build_parser()
    ns = parser.parse_args(["levels", "--codes", "600000"])
    assert ns.command == "levels"
    assert ns.period == "day"
    assert ns.codes == "600000"
    assert ns.verbose is False and ns.json is False


# ------------------------------------------------ 三种复权格式（用户指令①）
def _with_factors(env, code: str = "600000") -> None:
    """给合成票注入一段除权因子，三态才有区别。

    **没有因子表时三态必然相同** —— 那种情况下如实回落到落库口径才是对的，
    另有一条用例专门钉它。这条用例要的是「有因子时三态真的不同」。
    """
    conn = meta.init()   # 走 tests/conftest 打过桩的 load_config
    try:
        meta.save_adjust_factors(conn, code, [("2020-01-01", 0.5), ("2025-01-01", 1.0)], source="test")
    finally:
        conn.close()


def test_without_factors_the_three_modes_honestly_collapse_to_stored(env, bars, capsys):
    """**没有因子表时三态相同** —— 这不是 bug，要如实说明而不是假装切得动。"""
    store.upsert("600000", "day", bars)
    for mode in ("raw", "qfq", "hfq"):
        rc = cli.main(["bars", "--code", "600000", "--period", "day", "--adjust", mode])
        out = capsys.readouterr().out
        assert rc == 0 and "复权=raw" in out
        assert "无除权记录" in out


def test_levels_accepts_the_three_adjust_modes(env, bars, capsys):
    """`--adjust raw|qfq|hfq` 都要能跑，且**把生效口径打印出来**。

    口径不是装饰：复权换的是价格 → 换线段 → 换中枢 → 换递归产出。
    不打印的话，命令行与页面看到的结构不同却无从察觉。
    """
    store.upsert("600000", "day", bars)
    _with_factors(env)
    for mode in ("raw", "qfq", "hfq"):
        rc = cli.main(["levels", "--codes", "600000", "--period", "day", "--adjust", mode])
        out = capsys.readouterr().out
        assert rc == 0, out
        assert f"复权口径：{mode}" in out, out


def test_bars_exports_all_three_modes(env, bars, capsys):
    """`bars` 三种格式都要出得来，且**后复权与不复权的价不同**。"""
    store.upsert("600000", "day", bars)
    _with_factors(env)
    seen = {}
    for mode in ("raw", "qfq", "hfq"):
        rc = cli.main(["bars", "--code", "600000", "--period", "day", "--adjust", mode])
        out = capsys.readouterr().out
        assert rc == 0, out
        assert f"复权={mode}" in out
        body = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
        seen[mode] = body[-1].split()
    # 按**浮点**比：pandas 的显示精度会让同一个数打成 `110.341768` / `110.3418`，
    # 拿字符串比会得到一个假的失败。
    close = {m: float(v[4]) for m, v in seen.items()}
    assert close["hfq"] != close["raw"], "后复权与不复权的收盘价不应当相同"
    # 容差取 1e-3：复权价会按 `adjust.PRICE_DECIMALS = 4` **收敛到分位**
    # （除权因子乘出来是浮点噪声，不收敛会让「三态在非除权区间一致」这类断言无法成立），
    # 所以 raw 与 qfq 在最新端允许差一个舍入粒度（实测 ~3e-5）。
    assert abs(close["raw"] - close["qfq"]) < 1e-3, (
        "前复权在**最新端**与不复权一致（它就是把最新价锚成原始价）"
    )
    # 量额不随口径变 —— 那是当时真实成交的客观事实（`adjust.py` 的约定）
    assert seen["hfq"][5] == seen["raw"][5] and seen["hfq"][6] == seen["raw"][6]


def test_bars_csv_and_out_file(env, bars, tmp_path, capsys):
    store.upsert("600000", "day", bars)
    _with_factors(env)
    dest = tmp_path / "out.csv"
    rc = cli.main(["bars", "--code", "600000", "--period", "day",
                   "--adjust", "hfq", "--csv", "--out", str(dest)])
    assert rc == 0, capsys.readouterr().out
    text = dest.read_text(encoding="utf-8")
    assert text.startswith("# 600000 day 复权=hfq"), text[:80]
    assert "close" in text


def test_bars_rejects_a_period_without_local_data(env, bars, capsys):
    rc = cli.main(["bars", "--code", "600000", "--period", "1", "--adjust", "raw"])
    assert rc == 2
    assert "没有数据" in capsys.readouterr().err
