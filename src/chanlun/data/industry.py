"""申万行业指数（一级 / 二级）的**清单与取名**。

用户 2026-10-05 的要求：「A股的话，需要有那个一级行业、二级行业的行业指数。」
本模块回答两个问题：**有哪些**、**叫什么**。取数在 `sw_source.py`，分派在 `sources.py`。

## 为什么清单是内置的，而不是每次联网拉

行情（日线）在 `sw_source.py` 里联网取，但**清单**必须是本地的：

1. 搜索与 `meta.name_of` 都是纯本地操作（页面每敲一个字就要用），联网会把
   「搜行业」变成一件看网络心情的事 —— 而 `universe` 那张股票名单也是本地落库的，
   同一个系统里两套规则会让人猜不到哪一步需要网。
2. 清单极稳定：申万 2021 版一级 **31** 个、二级 **124** 个，几年才动一次。

所以清单写死在这里，并给一个 `python -m chanlun industry --check` 去**对照实测**：
联网拉一遍数据商的板块列表，报出「多了 / 少了 / 改名了」。清单过期时看得见，
而不是安静地少一个行业。

## 代码写法：`sw.<申万 6 位码>`

- `sw.801120` = 一级「食品饮料」，`sw.801125` = 二级「白酒Ⅱ」。
- 裸 `801120` **不能**用：`markets.HEAD2` 把 `81` 判成北交所板块指数，裸码会落到
  `bj` 去。必须带 `sw.` 前缀（与 `sh.000001` 必须带前缀同理，见 `markets.needs_prefix`）。
- 一级与二级共用 `801xxx` 号段，但**不撞车**：实测一级 31 个码**全部以 `0` 结尾**、
  二级 124 个**全部不以 `0` 结尾**，交集为空。所以代码里不需要再编一层级别号，
  级别只影响「怎么分组显示」，不影响身份。

## 数据商要的写法

`pt01` + 申万 6 位码（`pt01801120` ← `801120`），见 `vendor_code`。实测依据在
`docs/evidence/2026-10-05-申万行业指数数据源.md`。
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass

from .types import DataSourceError

log = logging.getLogger(__name__)

#: 数据商的板块列表接口（腾讯）。`board_type=hy` 一级 / `hy2` 二级。
#:
#: 实测（2026-10-05）：`hy` → 31 条、`stock_type` 全为 `BK-HY-1`；
#: `hy2` → 124 条、全为 `BK-HY-2`（正好是申万一级/二级的规范个数）。
#: `count` **必须 ≤ 200**：`count=500` 回
#: `{"code":1619377101,"msg":"error_type=2 suggest=2 msg=参数错误:count too large"}`，
#: 所以按 `offset` 翻页。`hy3` 回 0 条（没有三级）。
RANK_API = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"

#: 本地级别号 → 数据商的 `board_type`。
_BOARD_TYPE: dict[str, str] = {"1": "hy", "2": "hy2"}

#: 板块代码前缀：数据商写 `pt01` + 申万 6 位码。
VENDOR_PREFIX = "pt01"

#: 一次取多少条。200 是实测上限。
_PAGE = 200

#: 与 `tencent_source` / `search` 同一套路数：数据商对裸 `python-urllib` 不友好。
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) chanlun/0.5"

#: 级别 → 中文名（页面与 CLI 的分组标题）。
LEVEL_NAMES: dict[str, str] = {"1": "一级行业", "2": "二级行业"}

#: 支持的级别，顺序即显示顺序。
LEVELS: tuple[str, ...] = ("1", "2")

#: 这份内置清单**核对**的日期（不是"今天"）。
#: `python -m chanlun industry --check` 拿实测列表和它对，`GET /api/industries`
#: 把它回给页面显示 —— 申万分类几年才调一次，但悄悄过期比过期本身更坏。
CHECKED_ON = "2026-10-05"

#: 申万一级行业（31 个）。
SW_L1: tuple[tuple[str, str], ...] = (
    ("801170", "交通运输"),
    ("801760", "传媒"),
    ("801160", "公用事业"),
    ("801010", "农林牧渔"),
    ("801150", "医药生物"),
    ("801200", "商贸零售"),
    ("801740", "国防军工"),
    ("801030", "基础化工"),
    ("801110", "家用电器"),
    ("801710", "建筑材料"),
    ("801720", "建筑装饰"),
    ("801180", "房地产"),
    ("801050", "有色金属"),
    ("801890", "机械设备"),
    ("801880", "汽车"),
    ("801950", "煤炭"),
    ("801970", "环保"),
    ("801730", "电力设备"),
    ("801080", "电子"),
    ("801960", "石油石化"),
    ("801210", "社会服务"),
    ("801130", "纺织服饰"),
    ("801230", "综合"),
    ("801980", "美容护理"),
    ("801750", "计算机"),
    ("801140", "轻工制造"),
    ("801770", "通信"),
    ("801040", "钢铁"),
    ("801780", "银行"),
    ("801790", "非银金融"),
    ("801120", "食品饮料"),
)

#: 申万二级行业（124 个）。
SW_L2: tuple[tuple[str, str], ...] = (
    ("801103", "IT服务Ⅱ"),
    ("801203", "一般零售"),
    ("801724", "专业工程"),
    ("801218", "专业服务"),
    ("801204", "专业连锁Ⅱ"),
    ("801074", "专用设备"),
    ("801981", "个护用品"),
    ("801155", "中药Ⅱ"),
    ("801095", "乘用车"),
    ("801206", "互联网电商"),
    ("801128", "休闲食品"),
    ("801194", "保险Ⅱ"),
    ("801083", "元件"),
    ("801735", "光伏设备"),
    ("801084", "光学光电子"),
    ("801082", "其他电子Ⅱ"),
    ("801733", "其他电源设备Ⅱ"),
    ("801017", "养殖业"),
    ("801745", "军工电子Ⅱ"),
    ("801012", "农产品加工"),
    ("801038", "农化制品"),
    ("801785", "农商行Ⅱ"),
    ("801043", "冶钢原料"),
    ("801769", "出版"),
    ("801018", "动物保健Ⅱ"),
    ("801141", "包装印刷"),
    ("801982", "化妆品"),
    ("801034", "化学制品"),
    ("801151", "化学制药"),
    ("801033", "化学原料"),
    ("801032", "化学纤维"),
    ("801153", "医疗器械"),
    ("801156", "医疗服务"),
    ("801154", "医药商业"),
    ("801081", "半导体"),
    ("801114", "厨卫电器"),
    ("801096", "商用车"),
    ("801782", "国有大型银行Ⅱ"),
    ("801743", "地面兵装Ⅱ"),
    ("801784", "城商行Ⅱ"),
    ("801723", "基础建设"),
    ("801036", "塑料"),
    ("801191", "多元金融"),
    ("801142", "家居用品"),
    ("801116", "家电零部件Ⅱ"),
    ("801113", "小家电"),
    ("801054", "小金属"),
    ("801055", "工业金属"),
    ("801726", "工程咨询服务Ⅱ"),
    ("801077", "工程机械"),
    ("801765", "广告营销"),
    ("801766", "影视院线"),
    ("801181", "房地产开发"),
    ("801183", "房地产服务"),
    ("801721", "房屋建设Ⅱ"),
    ("801881", "摩托车及其他"),
    ("801994", "教育"),
    ("801767", "数字媒体"),
    ("801145", "文娱用品"),
    ("801993", "旅游及景区"),
    ("801044", "普钢"),
    ("801132", "服装家纺"),
    ("801037", "橡胶"),
    ("801711", "水泥"),
    ("801092", "汽车服务"),
    ("801093", "汽车零部件"),
    ("801962", "油服工程"),
    ("801085", "消费电子"),
    ("801015", "渔业"),
    ("801764", "游戏Ⅱ"),
    ("801963", "炼化及贸易"),
    ("801952", "焦炭Ⅱ"),
    ("801951", "煤炭开采"),
    ("801115", "照明设备Ⅱ"),
    ("801163", "燃气Ⅱ"),
    ("801178", "物流"),
    ("801045", "特钢Ⅱ"),
    ("801972", "环保设备Ⅱ"),
    ("801971", "环境治理"),
    ("801712", "玻璃玻纤"),
    ("801152", "生物制品"),
    ("801161", "电力"),
    ("801086", "电子化学品Ⅱ"),
    ("801731", "电机Ⅱ"),
    ("801737", "电池"),
    ("801738", "电网设备"),
    ("801995", "电视广播Ⅱ"),
    ("801111", "白色家电"),
    ("801125", "白酒Ⅱ"),
    ("801016", "种植业"),
    ("801131", "纺织制造"),
    ("801231", "综合Ⅱ"),
    ("801783", "股份制银行Ⅱ"),
    ("801056", "能源金属"),
    ("801078", "自动化设备"),
    ("801741", "航天装备Ⅱ"),
    ("801744", "航海装备Ⅱ"),
    ("801991", "航空机场"),
    ("801742", "航空装备Ⅱ"),
    ("801992", "航运港口"),
    ("801713", "装修建材"),
    ("801722", "装修装饰Ⅱ"),
    ("801101", "计算机设备"),
    ("801193", "证券Ⅱ"),
    ("801129", "调味发酵品Ⅱ"),
    ("801053", "贵金属"),
    ("801202", "贸易Ⅱ"),
    ("801076", "轨交设备Ⅱ"),
    ("801104", "软件开发"),
    ("801223", "通信服务"),
    ("801102", "通信设备"),
    ("801072", "通用设备"),
    ("801143", "造纸"),
    ("801219", "酒店餐饮"),
    ("801051", "金属新材料"),
    ("801179", "铁路公路"),
    ("801126", "非白酒"),
    ("801039", "非金属材料Ⅱ"),
    ("801736", "风电设备"),
    ("801124", "食品加工"),
    ("801127", "饮料乳品"),
    ("801133", "饰品"),
    ("801014", "饲料"),
    ("801112", "黑色家电"),
)


@dataclass(frozen=True)
class Board:
    """一个行业板块。`code` 是**落库键**（`sw.801120`），页面拿它直接取数。"""

    code: str
    name: str
    level: str  # "1" / "2"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "name": self.name, "level": self.level}


#: 全部板块，一级在前、二级在后；每个级别内部按名称排序（内置表已排好）。
ALL: tuple[Board, ...] = (
    tuple(Board(f"sw.{c}", n, "1") for c, n in SW_L1)
    + tuple(Board(f"sw.{c}", n, "2") for c, n in SW_L2)
)

_BY_CODE: dict[str, Board] = {b.code: b for b in ALL}


def all_boards(level: str = "") -> list[Board]:
    """按级别列出板块；`level` 为空给全部。级别非法返回空列表（不猜）。"""
    lv = str(level or "").strip()
    if not lv:
        return list(ALL)
    if lv not in LEVELS:
        return []
    return [b for b in ALL if b.level == lv]


def get(code: str) -> Board | None:
    """落库键 → 板块；不是行业代码返回 `None`。"""
    return _BY_CODE.get(str(code or "").strip().lower())


def is_industry(code: str) -> bool:
    """是不是内置清单里的行业板块。"""
    return get(code) is not None


def name_of(code: str) -> str:
    """行业名；查不到返回空串（与 `meta.name_of` 的约定一致）。"""
    board = get(code)
    return board.name if board is not None else ""


def level_of(code: str) -> str:
    """级别号（"1"/"2"）；不是行业返回空串。"""
    board = get(code)
    return board.level if board is not None else ""


def search_rows() -> list[dict[str, str]]:
    """给 `/api/search` 用的行：与 `meta.get_universe()` 同形状，另带 `kind`/`source`。

    `kind="ZS"`（指数）与联想词那条路上的分类同名 —— 板块是**指数**，不是个股，
    页面按指数渲染才对。`source="industry"` 让每条能追溯到内置清单
    （`source="universe"` 是品种表，`source="smartbox"` 是腾讯联想词）。
    """
    return [
        {"code": b.code, "name": b.name, "market": "sw", "kind": "ZS", "source": "industry"}
        for b in ALL
    ]


def vendor_code(code: str) -> str:
    """落库键 → 数据商板块符号：`sw.801120` → `pt01801120`。

    只做字符串拼接，**不查清单** —— 取数不该因为清单里没有这个码就拒绝
    （清单过期时仍然要能取到数据，`--check` 会告诉你清单该更新了）。
    """
    text = str(code or "").strip().lower()
    _, _, digits = text.partition(".")
    return VENDOR_PREFIX + digits if digits else ""


def fetch_live(level: str) -> list[Board]:
    """联网拉一个级别的板块列表。失败抛 `DataSourceError`（调用方决定怎么显示）。"""
    lv = str(level or "").strip()
    board_type = _BOARD_TYPE.get(lv)
    if board_type is None:
        raise DataSourceError(f"不认得的行业级别 {level!r}（可选 {', '.join(LEVELS)}）")
    out: list[Board] = []
    seen: set[str] = set()
    offset = 0
    while True:
        url = (
            f"{RANK_API}?board_type={board_type}&sort_type=price&direct=down"
            f"&offset={offset}&count={_PAGE}"
        )
        payload = _get_json(url)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DataSourceError(f"板块列表响应里没有 data 对象 url={url} body={payload!r}")
        rows = data.get("rank_list")
        if not isinstance(rows, list):
            raise DataSourceError(f"板块列表响应里没有 rank_list url={url} body={payload!r}")
        if not rows:
            break
        for row in rows:
            raw = str(row.get("code") or "")
            digits = raw[len(VENDOR_PREFIX):] if raw.startswith(VENDOR_PREFIX) else ""
            if not digits:
                continue
            code = f"sw.{digits}"
            if code in seen:
                continue
            seen.add(code)
            out.append(Board(code, str(row.get("name") or ""), lv))
        if len(rows) < _PAGE:
            break
        offset += _PAGE
    return out


def _get_json(url: str, timeout: int = 25) -> dict:
    """取 JSON。与两个行情源一样走 `urllib.request`（能穿过本机代理）。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定 https 域名
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - 网络/解析异常一律翻成 DataSourceError
        raise DataSourceError(f"板块列表取数失败 url={url}: {exc}") from exc


def diff(live: list[Board]) -> list[str]:
    """内置清单 vs 实测列表的差异，逐条中文说明。空列表 = 一致。

    `--check` 用它。**只报差异，不改清单** —— 改清单要人来判断（申万确实调整了
    分类，还是数据商接口变了），自动改写会让一次接口抖动变成一次静默的分类变更。
    """
    if not live:
        return ["实测列表为空，无法对照（接口或参数可能变了）"]
    lv = live[0].level
    ours = {b.code: b.name for b in all_boards(lv)}
    theirs = {b.code: b.name for b in live}
    out: list[str] = []
    for code in sorted(set(theirs) - set(ours)):
        out.append(f"实测多出：{code} {theirs[code]}")
    for code in sorted(set(ours) - set(theirs)):
        out.append(f"内置多出（实测没有）：{code} {ours[code]}")
    for code in sorted(set(ours) & set(theirs)):
        if ours[code] != theirs[code]:
            out.append(f"名称不一致：{code} 内置={ours[code]!r} 实测={theirs[code]!r}")
    return out
