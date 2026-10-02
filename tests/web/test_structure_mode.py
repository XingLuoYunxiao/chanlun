"""Task 7：`/api/structure` 的 `mode` 口径开关与背驰出口。

三件事必须钉住，否则「严格 / 非严格」这个开关会**静默变成假功能**：

1. **缓存键必须含 `mode`**（`api.py:381`）。`_CACHE` 的键原本是
   `(code, period, effective, last_ts, fingerprint)` —— 用户先看严格模式、再切非严格，
   键完全相同 ⇒ 直接命中严格模式的旧快照 ⇒ 图上什么都没变。**不报错、不崩溃，
   只是按钮无效**，用户会以为功能没做。`test_cache_key_includes_mode` 就是那条会红的线。
2. **`loose` 必须严格多于 `strict`**（反向断言）。入口归一化 `SignalMode(mode)`
   一旦被删掉，字符串 `"loose"` 会**静默退化**成严格模式 —— `SignalMode(str, Enum)`
   让 `mode is SignalMode.LOOSE` 对字符串恒为 `False`。这个 bug 本仓库刚修过
   （`signal.py:185`）。只测「能解析参数」「不报错」是**无价值**的。
3. **背驰必须真的出得来，且趋势背驰与盘整背驰可区分**。改动前 `api.py:385` 没接
   `divergence_fn` ⇒ `engine.py:189` 的判空不成立 ⇒ `Snapshot.divergences` **恒为 `()`**，
   线上背驰列表一直是空的。`kind` 是 `"trend"` / `"consolidation"`，人读的中文名只有
   `DivergenceKind.name_cn`（`divergence.py:64`）**一处**实现，payload 里的 `kind_cn`
   必须与它一致 —— 第 015 / 060 课要求盘整背驰不许简写成「背驰」。

夹具（`tests/chan/fixtures/bars.parquet` 的 `sh.600000`）是 **1212 根**的窗口，
实测 `strict` 1 个买卖点、`loose` 9 个、背驰两种口径都是 5 个（**全是盘整背驰**）；
真实数据里 `sh.600000` 全史 6 个背驰也**全是盘整背驰**。趋势背驰要稀得多 ——
有日线数据的 5439 只票里只有 230 只有、且多数只有 1 个 —— 所以夹具端到端凑不出
`TREND` 那一支：`kind_cn` 对两种 `kind` 的映射用
`test_divergence_kind_name_cn_pins_both_members` **直接构造**覆盖，**不 skip**。
"""

from __future__ import annotations

from chanlun.chan.divergence import DivergenceKind

#: 夹具里 4 只样本票之一；`sh.600000` 归一化成 store 键 `600000`。
CODE = "sh.600000"
PERIOD = "day"


def _structure(client, **params):
    """`/api/structure` 的一次请求。**省略 `limit`** ⇒ 走默认 1200（夹具 1212 根，几乎全史）。

    写小 `limit`（计划里的 300）会把窗口外的结构裁掉：结构在全史算、再裁到窗口，
    裁完的买卖点条数就不再是「两种口径的差」而是「窗口里还剩几个」。
    """
    r = client.get("/api/structure", params={"code": CODE, "period": PERIOD, **params})
    assert r.status_code == 200, r.text
    return r


# ---------------- mode 参数本身 ----------------
def test_default_mode_is_strict(client):
    """不传 `mode` ⇒ 严格模式。`find_signals` 的默认值就是 `SignalMode.STRICT`
    （`signal.py:171`），所以既有调用路径的默认行为不变 —— 但必须**实测**，不能靠读代码。"""
    assert _structure(client).json()["mode"] == "strict"


def test_invalid_mode_is_422_not_500(client):
    """非法口径给 422（FastAPI 的查询参数校验），**不许变成 500**。

    422 说明非法值根本到不了 `SignalMode(mode)`；若签名改成裸 `str`，
    这里会变成 500 且栈里是 `ValueError`。
    """
    r = client.get("/api/structure", params={"code": CODE, "period": PERIOD, "mode": "wild"})
    assert r.status_code == 422, f"非法 mode 必须 422，实测 {r.status_code}: {r.text[:200]}"


def test_loose_has_strictly_more_signals_than_strict(client):
    """**反向断言**：非严格口径必须真的放宽出更多买卖点，用 `>` 而不是 `>=`。

    这条同时是「入口归一化没被删」的哨兵：`mode` 忘了 `SignalMode(mode)` 时，
    `"loose"` 会静默按严格算，两边条数相等，这里立刻红。
    """
    strict = _structure(client, mode="strict").json()
    loose = _structure(client, mode="loose").json()
    assert len(strict["signals"]) > 0, "夹具上严格模式应至少有一个买卖点（实测 s3 @ 2022-01-13）"
    assert len(loose["signals"]) > len(strict["signals"]), (
        f"loose 应严格多于 strict，实测 strict={len(strict['signals'])} loose={len(loose['signals'])}"
        " —— 相等意味着入口 SignalMode(mode) 归一化丢失，字符串静默退化成了严格模式"
    )


# ---------------- 缓存键（承重） ----------------
def test_cache_key_includes_mode(client):
    """缓存键漏加 mode 时，第二次请求会命中第一次的严格快照 —— 这条必须先炸。

    同一个测试里连续两次请求，**中间不清缓存**：`snapshot_of` 的缓存键若不含 `mode`，
    第二次（loose）会直接命中第一次（strict）写进去的 `Snapshot`，两次 `signals`
    逐项相同 —— 页面上切口径什么都没变。
    """
    params = {"code": CODE, "period": PERIOD}
    strict = client.get("/api/structure", params={**params, "mode": "strict"}).json()
    loose = client.get("/api/structure", params={**params, "mode": "loose"}).json()
    assert len(loose["signals"]) > len(strict["signals"]), (
        "缓存键不含 mode：loose 命中了 strict 的旧快照"
    )
    # 返回体自己也必须如实报出口径（不能靠缓存里那份快照的口径搪塞）。
    assert strict["mode"] == "strict"
    assert loose["mode"] == "loose"


# ---------------- 默认行为逐字节不变 ----------------
def test_explicit_strict_is_byte_identical_to_default(client):
    """`?mode=strict` 与**完全不传 `mode`** 的返回体逐字节相同。

    这是 Task 10 的「严格模式逐项不变」在 Web 层的对应物：新参数默认值一旦
    不是 `"strict"`，既有页面/回测拿到的口径就悄悄换了。
    """
    default = client.get("/api/structure", params={"code": CODE, "period": PERIOD})
    explicit = client.get("/api/structure", params={"code": CODE, "period": PERIOD, "mode": "strict"})
    assert default.status_code == 200, default.text
    assert explicit.status_code == 200, explicit.text
    assert explicit.content == default.content, "带 ?mode=strict 与不传 mode 的返回体应当逐字节相同"


# ---------------- 背驰出口 ----------------
def test_divergences_are_present_and_kind_cn_matches_kind(client):
    """背驰列表非空（`divergence_fn` 真的接上了），且 `kind_cn` 与 `kind` 一一对应。

    改动前 `divergences` 恒为 `[]`（`api.py:385` 没传 `divergence_fn`），
    所以「非空」这条本身就是**会失败**的测量。中文名只许来自 `name_cn` 一处实现。
    """
    for mode in ("strict", "loose"):
        divs = _structure(client, mode=mode).json()["divergences"]
        assert divs, f"mode={mode} 的背驰列表为空 —— 引擎的 divergence_fn 没接上"
        pairs = set()
        for d in divs:
            assert d["kind"] in {"trend", "consolidation"}, d["kind"]
            assert d["kind_cn"] == DivergenceKind(d["kind"]).name_cn, (
                f"kind_cn={d['kind_cn']!r} 与 kind={d['kind']!r} 不一致："
                "中文名必须来自 DivergenceKind.name_cn，不许另抄一份"
            )
            assert d["reason"], "reason 是图上 tooltip 的唯一来源（Task 8 直接用），不能为空"
            pairs.add((d["kind"], d["kind_cn"]))
        # 一一对应 = 两种 kind 不许映射到同一个中文名（「盘整背驰」不许简写成「背驰」）。
        assert len({cn for _, cn in pairs}) == len({k for k, _ in pairs}), pairs


def test_divergence_kind_name_cn_pins_both_members():
    """直接构造两种 `DivergenceKind`，钉住中文名的**唯一实现**（`divergence.py:64`）。

    真实数据上趋势背驰很稀（5439 只票里 230 只有，夹具窗口里**一个都没有**），
    所以这条映射不可能靠夹具端到端凑出来 —— 也**不许 `pytest.skip` 掉**：
    第 15 课「没有趋势，没有背驰」与第 37 课「最多就是盘整背驰」把两者分了界，
    盘整背驰单独命名、不许简写成「背驰」，这条措辞约束只能这样守。
    """
    assert DivergenceKind("trend") is DivergenceKind.TREND
    assert DivergenceKind("consolidation") is DivergenceKind.CONSOLIDATION
    assert DivergenceKind.TREND.name_cn == "趋势背驰"
    assert DivergenceKind.CONSOLIDATION.name_cn == "盘整背驰"
    assert DivergenceKind.TREND.name_cn != DivergenceKind.CONSOLIDATION.name_cn
