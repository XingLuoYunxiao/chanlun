"""复权口径与三态换算。

库里只存**不复权**原始价，另存一条除权因子阶梯 `k_t`（按段起始日给出，段内恒定，
最后一个除权日之后 `k = 1`）：

- 不复权 `raw_t = raw_t`
- 前复权 `qfq_t = raw_t * k_t`      （最新价三态一致）
- 后复权 `hfq_t = raw_t * k_t / k_0`（首日价三态一致）

复权只改价格，**不改成交量与成交额** —— 那是当时真实成交的客观事实，按除权因子去缩放
它会让「量」这个维度失去意义（放量/缩量的判断直接失真）。

因子阶梯的方向容易搞反，记一个用例：2 拆 1，原始价 `[10, 5]`，除权后因子 `k=[0.5, 1.0]`；
`qfq=[5, 5]`（最新价与原始一致）、`hfq=[10, 10]`（首日价与原始一致）。
"""
from __future__ import annotations

import pandas as pd

MODES = ("raw", "qfq", "hfq")
PRICE_COLUMNS = ("open", "high", "low", "close")
FACTOR_COLUMNS = ("ts", "k")

#: baostock 的口径码（`query_history_k_data_plus` 的 adjustflag）与自造取值的映射。
#: `"2"` 是前复权、`"1"` 是后复权、`"3"` 是不复权；`"none"/"raw"/""` 是本地写法。
_MAP = {"2": "qfq", "1": "hfq", "3": "raw", "none": "raw", "raw": "raw", "": "raw"}

#: 价格保留几位小数。除权因子乘出来是浮点噪声（`20*0.95 = 18.999999999999996`），
#: 不收敛到分位会让「三态在非除权区间完全一致」这种断言无法成立，也会让页面显示
#: `18.999999999999996` 这种数字。
PRICE_DECIMALS = 4


def normalize_adjust(value: str | None) -> str:
    """把各处口径写法统一成 `raw | qfq | hfq`。

    `None` 按前复权处理：一期默认（`bs_adjust="2"`）与旧库里的数据都是前复权，
    「没写」当成前复权才与历史行为一致。未知取值**报错**而不是猜一个 —— 猜错口径
    会静默改掉所有价格，比直接失败危险得多。
    """
    if value is None:
        return "qfq"
    key = str(value).strip().lower()
    if key in MODES:
        return key
    if key in _MAP:
        return _MAP[key]
    raise ValueError(f"未知复权口径: {value!r}（可用: {', '.join(MODES)}）")


def _empty_factors() -> pd.DataFrame:
    return pd.DataFrame({"ts": [], "k": []})


def _k_series(df: pd.DataFrame, factors: pd.DataFrame) -> pd.Series:
    """给每根 K 线取出生效因子：取「起始日 <= 该 K 线日期」的最后一段。

    段起始日之前的 K 线（因子表从中间开始）沿用第一段因子，而不是留空 ——
    空值会让整列变 `NaN`，页面直接空白。
    """
    f = factors[list(FACTOR_COLUMNS)].copy()
    f["ts"] = f["ts"].astype(str)
    f = f.sort_values("ts", kind="stable")
    pos = f["ts"].searchsorted(df["ts"].astype(str), side="right") - 1
    pos = pos.clip(min=0)
    return pd.Series(f["k"].to_numpy()[pos], index=df.index, dtype="float64")


def apply_adjust(df: pd.DataFrame, factors: pd.DataFrame | None, mode: str) -> pd.DataFrame:
    """按口径返回价格视图。**不改原表**，调用方拿到的是一份新表。

    因子缺失（`None` 或空表）时三态都原样返回：没有除权记录的票本来就三态相同，
    页面要如实标注「无除权记录」，而不是拿一个假因子把它"切"出差别来。
    """
    mode = normalize_adjust(mode)
    out = df.copy()
    if factors is None or len(factors) == 0 or mode == "raw":
        return out
    k = _k_series(df, factors)
    scale = k if mode == "qfq" else k / float(k.iloc[0])
    for col in PRICE_COLUMNS:
        out[col] = (out[col].astype("float64") * scale).round(PRICE_DECIMALS)
    return out


def unapply_adjust(df: pd.DataFrame, factors: pd.DataFrame | None, mode: str) -> pd.DataFrame:
    """把**前复权落库**的行情还原成三态（一期 baostock 的 `day` 就是这个口径）。

    库内是 `q_t = raw_t × k_t`，于是：

    - `raw_t = q_t / k_t`
    - `hfq_t = raw_t × k_t / k_0 = q_t / k_0`
    - `qfq_t` 原样返回

    与 `apply_adjust` 是**两条相反的公式**：同一个因子表、不同的落库口径，拿错一条
    不会报错，只会把价格二次复权 —— 除权日反而长出一根假的跳空缺口。
    """
    mode = normalize_adjust(mode)
    out = df.copy()
    if factors is None or len(factors) == 0 or mode == "qfq":
        return out
    k = _k_series(df, factors)
    scale = 1.0 / k if mode == "raw" else 1.0 / float(k.iloc[0])
    for col in PRICE_COLUMNS:
        out[col] = (out[col].astype("float64") * scale).round(PRICE_DECIMALS)
    return out


def infer_factors(raw: pd.DataFrame, qfq: pd.DataFrame) -> pd.DataFrame:
    """从「不复权 / 前复权」两份同区间数据反推因子阶梯。

    用于交叉校验权威因子（baostock `query_adjust_factor`）：两条来源算出的阶梯若不一致，
    说明其中一份数据的口径不是它自称的那个。

    `close <= 0` 的行直接丢掉：停牌等异常数据的 0 价做除数会得到 `inf`，
    一个 `inf` 因子会把整段价格污染成 `inf`。
    """
    merged = raw[["ts", "close"]].merge(qfq[["ts", "close"]], on="ts", suffixes=("_raw", "_qfq"))
    merged = merged[merged["close_raw"] > 0]
    merged["k"] = (merged["close_qfq"] / merged["close_raw"]).round(8)
    changed = merged["k"].ne(merged["k"].shift())
    return merged.loc[changed, ["ts", "k"]].reset_index(drop=True)
