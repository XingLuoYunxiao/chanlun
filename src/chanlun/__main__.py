"""chanlun 命令行入口。

用法：
    python -m chanlun universe [--enrich]
    python -m chanlun sync --period day [--codes 600000,000001] [--full] [--since 2021-01-01]
    python -m chanlun daily [--dry-run] [--periods day,30,5] [--codes ...]
    python -m chanlun scan [--period day] [--codes ...]
    python -m chanlun backtest [--period day] [--codes ...]
    python -m chanlun tdx [--download] [--src DIR] [--period day] [--dry-run]
    python -m chanlun factors [--codes ... | --all] [--src DIR] [--dry-run]
    python -m chanlun serve [--host 127.0.0.1] [--port 8888]

约定：
- 日志同时写入 `logs/chanlun.log` 与 stdout。
- `sync` 单只失败不中断，错误写入 `meta.sync_state.error`；
  仅当**全部**失败时以非 0 退出码结束。
- 增量为默认：从 `store.last_ts` 的下一个交易日起拉取；`--full` 从 1990-01-01 全量重拉。
- `--since` 只作用于**本地还没有数据的票**（首次全市场同步时别为 5471 只票各拉 35 年）；
  已有数据的票仍然纯增量，避免在历史中间留出空洞 —— 空洞比少几年历史危险得多，
  缠论结构会跨着洞算。
- `serve` 默认端口 8888（配置文件可改默认值）；端口被占用时**报错退出**，
  不会自动换端口 —— 换了端口书签、自选池和外部脚本会一起失效。
- `daily` 是收盘后的一键流水线：同步 → 校验 → 全量结构快照入库 → 扫描 → 自选池跟踪
  → 写 `structure_snapshot`/`scan_result` → 推送。**非交易日直接 INFO 退出 0**
  （launchd 在周末补跑不算错误）；分钟数据陈旧时降级为「仅日线」并在输出里写明
  数据源与陈旧程度，绝不用落后几周的分钟数据去算当下的结构。
- **周期范围**：日线跑全市场（通达信整包一次 551 MB、本地解析 83 秒）；分钟没有公开整包，
  只能逐只走 baostock（实测 30 分 ≈30 秒/只、5 分 ≈162 秒/只，全市场 ≈292 小时），
  所以缺省只跑**自选池**。显式 `--codes` 仍照办：用户自己圈定的范围已经是有界的。
  同步 / 结构快照 / 扫描共用同一份范围（`_period_scope`）。
- `daily --dry-run` 是彩排：不联网同步、不落库、不推送，只把结果打在屏幕上。
  「跑通但不留痕」这件事必须真的成立，所以它走的是与真跑**同一套代码**，只在写入口收手。
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from tqdm import tqdm

from .calendar import get_calendar
from .chan.macd import macd
from .config import PROJECT_ROOT, Config, load_config
from .data import markets, meta, periods, quality, store
from .data.baostock_source import fetch_bars, strip_bs_code, to_bs_code
from .data.factors import MIN_OVERLAP
from .data.universe import build_universe
from .notify import build_notifier, notify_scan, notify_track
from .scan import (
    ScanReport,
    build_payload,
    check_sufficiency,
    default_snapshot_source,
    scan_report,
    track_watchlist,
)

log = logging.getLogger("chanlun")

FULL_START = "1990-01-01"
PERIODS = ("day", "60", "30", "15", "5")


# ---------------- 入口 ----------------
def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = load_config()
    setup_logging(cfg.log_path)

    if args.command == "universe":
        return _cmd_universe(args, cfg)
    if args.command == "sync":
        return _cmd_sync(args, cfg)
    if args.command == "daily":
        return _cmd_daily(args, cfg)
    if args.command == "scan":
        from .scan import cli as scan_cli

        return int(scan_cli.run(args))
    if args.command == "backtest":
        from .backtest import cli as backtest_cli

        return int(backtest_cli.run_command(args, cfg))
    if args.command == "tdx":
        return _cmd_tdx(args, cfg)
    if args.command == "factors":
        return _cmd_factors(args, cfg)
    if args.command == "serve":
        return _cmd_serve(args, cfg)
    if args.command == "seed-watchlist":
        return _cmd_seed_watchlist(args, cfg)
    _build_parser().print_help()
    return 2


def setup_logging(log_path: Path | str) -> None:
    """日志写文件并同时输出到 stdout。"""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def _parse_date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD：{text}") from exc


def _parse_periods(text: str) -> tuple[str, ...]:
    items = tuple(t.strip() for t in str(text).split(",") if t.strip())
    if not items:
        raise argparse.ArgumentTypeError("至少要给一个周期，例如 day,30,5")
    bad = [p for p in items if p not in PERIODS]
    if bad:
        raise argparse.ArgumentTypeError(f"未知周期：{','.join(bad)}（可选 {'/'.join(PERIODS)}）")
    return tuple(dict.fromkeys(items))  # 去重但保留顺序：同一个周期跑两遍纯属浪费


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m chanlun", description="缠论数据工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_universe = sub.add_parser("universe", help="重建品种表并打印数量")
    p_universe.add_argument(
        "--enrich", action="store_true",
        help="逐只 query_stock_basic 补全 list_date/delisted（很慢，默认关闭）",
    )

    p_sync = sub.add_parser("sync", help="同步 K 线到 Parquet")
    p_sync.add_argument("--period", required=True, choices=list(PERIODS), help="周期")
    p_sync.add_argument(
        "--codes", default=None,
        help="逗号分隔代码，缺省为品种表全市场（分钟周期缺省只跑自选池）")
    p_sync.add_argument("--full", action="store_true", help="从 1990-01-01 全量重拉")
    p_sync.add_argument(
        "--since", type=_parse_date, default=None, metavar="YYYY-MM-DD",
        help="本地无数据的票从这个日期开始拉（默认 1990-01-01）；已有数据仍为增量",
    )
    p_sync.add_argument(
        "--workers", type=int, default=4,
        help="预留的并发度；baostock 单会话 socket 协议下当前串行执行",
    )

    _add_daily_parser(sub)
    _add_tdx_parser(sub)
    _add_factors_parser(sub)

    # Task 14/15 的子命令由各自模块提供参数定义，父分发器只负责挂上去：
    # 参数长在谁身上，谁的 CLI 测试就覆盖得到，不会出现「父命令与子命令两套参数」。
    from .backtest import cli as backtest_cli
    from .scan import cli as scan_cli

    backtest_cli.add_parser(sub)
    scan_cli.add_parser(sub)

    p_serve = sub.add_parser("serve", help="启动盘后看盘页（FastAPI + ECharts）")
    p_serve.add_argument("--host", default=None, help="监听地址，缺省取配置（127.0.0.1）")
    p_serve.add_argument("--port", type=int, default=None, help="端口，缺省取配置（8888）")

    p_seed = sub.add_parser(
        "seed-watchlist",
        help="把自选池重置成七个大盘指数（上证/深证/创业板/沪深300/上证50/中证500/科创50）")
    p_seed.add_argument(
        "--keep", action="store_true",
        help="只补缺的，不清空已有自选（缺省清空重建：用户要的是「默认只放指数」）")
    p_seed.add_argument(
        "--no-sync", action="store_true",
        help="只改自选池，不联网同步日线（缺省把缺的指数一并拉下来）")
    p_seed.add_argument(
        "--since", type=_parse_date, default=None, metavar="YYYY-MM-DD",
        help="本地无数据的指数从这个日期开始拉（默认 1990-01-01）")
    return parser


def build_parser() -> argparse.ArgumentParser:
    """公开别名：外部脚本与测试靠它拿参数定义（`_build_parser` 是历史私有名）。"""
    return _build_parser()


def _add_daily_parser(sub) -> argparse.ArgumentParser:
    p = sub.add_parser("daily", help="收盘后一键流水线（同步→校验→快照→扫描→跟踪→推送）")
    p.add_argument(
        "--dry-run", action="store_true",
        help="彩排：不联网同步、不落库、不推送，只算一遍并把结果打印出来",
    )
    p.add_argument(
        "--periods", type=_parse_periods, default=None, metavar="day,30,5",
        help="本次同步并参与结构计算的周期，缺省取配置（day,30,5）；分钟数据陈旧时自动降级",
    )
    p.add_argument(
        "--codes", default=None,
        help="逗号分隔代码，缺省为品种表全市场（分钟周期缺省只跑自选池）")
    p.add_argument(
        "--source", choices=("baostock", "tdx"), default=None,
        help="日线数据源，缺省取配置（baostock）；tdx 走通达信整包",
    )
    p.add_argument(
        "--tdx-src", default=None, metavar="DIR",
        help="通达信 vipdoc 目录，缺省 data/tdx_raw；仅在 --source tdx 时用",
    )
    p.add_argument(
        "--no-download", action="store_true",
        help="不重新下载整包（551 MB），直接用 --tdx-src 下已解开的目录",
    )
    p.add_argument(
        "--since", type=_parse_date, default=None, metavar="YYYY-MM-DD",
        help="本地无数据的票从这个日期开始拉（默认 1990-01-01）；已有数据仍为增量",
    )
    p.add_argument(
        "--workers", type=int, default=4,
        help="扫描的进程数（结构快照与自选池跟踪固定串行，便于定位单只失败）",
    )
    p.add_argument(
        "--notify", default="file", choices=["console", "file", "null"],
        help="通知出口：file 追加写日志文件（默认）、console 打到屏幕、null 静默",
    )
    p.add_argument(
        "--notify-path", default=None, metavar="PATH",
        help="--notify file 的落盘路径，缺省 logs/notify.log",
    )
    return p


# ---------------- 子命令 ----------------
def _add_tdx_parser(sub) -> argparse.ArgumentParser:
    """通达信整包导入。`--period` 故意不用 argparse 的 choices：
    分钟周期要在 `_cmd_tdx` 里给出「官方没有公开分钟整包」的解释，而不是一句 usage。"""
    p = sub.add_parser("tdx", help="导入通达信 vipdoc 整包（官方只公开日线）")
    p.add_argument("--download", action="store_true", help="先下载官方整包（约 551 MB）再解压")
    p.add_argument(
        "--src", default=str(PROJECT_ROOT / "data" / "tdx_raw"),
        help="vipdoc 目录（也是下载与解压的目的地）",
    )
    p.add_argument("--period", default="day", help="周期；官方只有日线整包")
    p.add_argument("--markets", default="sh,sz,bj", help="逗号分隔市场")
    p.add_argument(
        "--kinds", default="stock,index",
        help="逗号分隔品种类型：stock/index/board/fund（默认只导个股与指数）",
    )
    p.add_argument("--codes", default=None, help="逗号分隔代码，缺省为目录内全部")
    p.add_argument("--dry-run", action="store_true", help="只统计不落库")
    return p


def _add_factors_parser(sub) -> argparse.ArgumentParser:
    """除权因子入库：从通达信原始价 + 库内前复权价反推因子阶梯。"""
    p = sub.add_parser("factors", help="反推除权因子并入库（三态复权的前提）")
    p.add_argument("--codes", default=None, help="逗号分隔代码，缺省为自选池")
    p.add_argument("--all", action="store_true", help="走品种表全市场（5471 只，几分钟）")
    p.add_argument(
        "--source", default="infer", choices=("infer",),
        help="因子来源；目前只有 infer（反推）。baostock query_adjust_factor 尚未实现",
    )
    p.add_argument(
        "--src", default=str(PROJECT_ROOT / "data" / "tdx_raw"),
        help="通达信 vipdoc 目录（原始价来源）",
    )
    p.add_argument("--period", default="day", help="反推所依据的行情周期，目前只有 day")
    p.add_argument("--min-overlap", type=int, default=MIN_OVERLAP, help="两份数据最少重叠根数")
    p.add_argument("--dry-run", action="store_true", help="只反推不落库")
    return p


def _cmd_universe(args, cfg: Config) -> int:
    enrich = bool(args.enrich)
    secs = build_universe(refresh=True, enrich=enrich)
    if enrich:
        delisted = sum(1 for s in secs if s.delisted)
        print(f"品种表: {len(secs)} 只（其中退市 {delisted} 只）")
    else:
        # enrich=False 时未查 query_stock_basic，不能谎报「退市 0 只」
        print(f"品种表: {len(secs)} 只（enrich=False 未补全退市标记，--enrich 可精确统计）")
    return 0


def _cmd_serve(args, cfg: Config) -> int:
    # 延迟导入：只跑 `sync` 的机器不必为了 CLI 去 import fastapi/uvicorn/echarts 静态目录
    from .web.app import PortInUseError, serve

    try:
        serve(cfg, host=args.host, port=args.port)
    except PortInUseError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 3
    return 0


def _tdx_index_frame(cfg: Config, code: str) -> tuple[pd.DataFrame | None, str]:
    """本地通达信整包里有没有这只**指数**的日线。返回 `(df, 说明)`。

    为什么需要这条兜底：baostock 没有科创50（实测 `sh.000688` 三次重登都返回 0 行），
    而用户点名要它进默认自选池。通达信整包里 `sh000688.day` 是有的（1637 根，
    2019-12-31 起，与科创50 指数 2019-12-31 基日吻合），且整包写的就是不复权价 ——
    指数本来也没有除权。**必须用 `tdx.is_index` 验号段**：`sz000688` 是另一只
    个股（收 24.24），拿它顶替指数就是换了一只票。
    """
    from .data import tdx

    # 找文件用**带市场前缀**的形式（`sh600000.day` 的命名规则），不要用落库键：
    # 落库键对普通个股是裸码（`600000`），拿它拼路径会拼出个不存在的文件，
    # 于是「号段不是指数」这条检查就永远走不到。
    market, _, bare = to_bs_code(code).partition(".")
    if not market or len(bare) != 6 or not bare.isdigit():
        return None, ""
    path = cfg.data.root / "tdx_raw" / market / "lday" / f"{market}{bare}.day"
    if not path.is_file():
        return None, f"本地通达信整包里也没有 {path.name}"
    if not tdx.is_index(market, bare):
        return None, f"通达信 {path.name} 不是指数（号段不符），不用它冒充指数"
    df = tdx.parse_file(path)
    return df, f"取自本地通达信整包 {path.name}（不复权，{len(df)} 根）"


def _cmd_seed_watchlist(args, cfg: Config) -> int:
    """把自选池重置成七个大盘指数，并把缺的日线一并拉下来。

    为什么单独取数、不复用 `_sync_period`：

    1. **指数的复权口径与股票不同**。指数没有除权，必须按不复权取（baostock `adjustflag=3`）；
       而 `_sync_period` 用的是 `cfg.bs_adjust`（缺省 `2`=前复权）。价格上两者对指数恰好相同，
       但 `sync_state.adjust` 会替指数声称「前复权」，这句话一路传到页面的复权说明里。
    2. 指数一共七只、单只几百到几千根，**每次都全量重拉**最省事也最不容易出错：
       增量拉法会让「库里有 1393 根（2021 起）」的沪深300 永远停在 2021 起。
    """
    conn = meta.init(cfg.data.meta_db)
    try:
        rows = meta.seed_watchlist(conn, replace=not bool(args.keep))
        listed = "、".join(f"{code} {name}" for code, name in meta.DEFAULT_WATCHLIST)
        print(f"自选池：{len(rows)} 只 —— {listed}")
        if args.no_sync:
            return 0
        cal = get_calendar()
        end = cal.last_trading_day(dt.date.today())
        ok = failed = skipped = 0
        for code, _name in meta.DEFAULT_WATCHLIST:
            try:
                start = _start_for(code, "day", True, cal, end, args.since)
                df = fetch_bars(to_bs_code(code), "day", start, end, adjust="3")
                origin = "baostock 不复权"
                if len(df) == 0:
                    # 数据源没有这只指数 → 先在**本地通达信整包**里找（科创50 就是这样来的）。
                    fallback, why = _tdx_index_frame(cfg, code)
                    if fallback is not None and len(fallback):
                        df, origin = fallback, why
                    else:
                        # 两条路都没有：**不谎报**。库里有旧文件就照实记它的行数，
                        # 并把原因写进 error —— 页面上它仍是「有数据」，日报会报出来。
                        prev = meta.get_sync(conn, code, "day")
                        have = store.read(code, "day") if store.exists(code, "day") else None
                        _record(
                            conn, code, "day", "3",
                            start_ts=(str(have["ts"].iloc[0])
                                      if have is not None and len(have) else None),
                            end_ts=(str(have["ts"].iloc[-1])
                                    if have is not None and len(have) else None),
                            rows=len(have) if have is not None else int(prev["rows"]) if prev else 0,
                            error=f"baostock 返回 0 行；{why or '本地无通达信整包'}",
                        )
                        skipped += 1
                        log.warning("指数无数据 code=%s（baostock 0 行，%s）", code, why)
                        continue
                store.upsert(code, "day", df)
                stored = store.read(code, "day")
                _record(
                    conn, code, "day", "3",
                    start_ts=str(stored["ts"].iloc[0]) if len(stored) else None,
                    end_ts=str(stored["ts"].iloc[-1]) if len(stored) else None,
                    rows=len(stored), error=None,
                )
                ok += 1
                print(f"  {code}: {len(stored)} 根（{origin}）"
                      f" {stored['ts'].iloc[0]} → {stored['ts'].iloc[-1]}")
            except Exception as exc:  # noqa: BLE001 - 单只失败不中断整批
                failed += 1
                prev = meta.get_sync(conn, code, "day")
                _record(
                    conn, code, "day", "3",
                    start_ts=prev["start_ts"] if prev else None,
                    end_ts=prev["end_ts"] if prev else None,
                    rows=int(prev["rows"]) if prev else 0,
                    error=str(exc),
                )
                log.warning("指数同步失败 code=%s: %s", code, exc)
        print(f"指数日线：成功 {ok} 失败 {failed} 跳过 {skipped}（共 {len(meta.DEFAULT_WATCHLIST)} 只）")
        return 0 if failed == 0 else 1
    finally:
        conn.close()


def _cmd_sync(args, cfg: Config) -> int:
    conn = meta.init(cfg.data.meta_db)
    try:
        out = _sync_period(
            conn, cfg, str(args.period),
            codes=_codes_for_period(
                conn, str(args.period), _resolve_codes(args), explicit=bool(args.codes)),
            full=bool(args.full), since=args.since, workers=args.workers,
        )
    finally:
        conn.close()
    print(f"同步完成: 成功 {out.ok} 失败 {out.failed} 跳过 {out.skipped}（共 {out.total} 只）")
    return 0 if (out.ok > 0 or out.failed == 0) else 1


@dataclass(frozen=True)
class SyncOutcome:
    """一个周期的同步结果。

    之所以不返回退出码：每日流水线要把它写进摘要，还得据此判断整条链路是否真的
    进了数据。「成功 0 失败 0 跳过 5471」和「成功 0 失败 5471」必须能区分开。
    """

    period: str
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0
    first_error: str = ""
    #: 各市场最新一根 K 线的日期，形如 `(("sh", "2026-09-30"), ...)`。
    #: 整包导入必须分市场报，不能报一个笼统的"最新日期"：北交所实测落后沪深一整年
    #: （2025-09-30 vs 2026-09-30），一个平均数会把这个事实抹掉。
    newest: tuple[tuple[str, str], ...] = ()

    def summary(self) -> str:
        text = (f"{self.period}: 成功 {self.ok} 失败 {self.failed} 跳过 {self.skipped}"
                f"（共 {self.total} 只）")
        if self.newest:
            text += "；最新 " + " ".join(f"{m} {d}" for m, d in self.newest)
        return f"{text}；首个错误：{self.first_error}" if self.first_error else text


def _daily_source(args, cfg: Config) -> str:
    """本次日线用哪个源：命令行优先于配置，配置缺省 baostock。"""
    return str(getattr(args, "source", None) or getattr(cfg.data, "source", "baostock"))


def _tdx_root(args, cfg: Config) -> Path:
    raw = getattr(args, "tdx_src", None)
    return Path(raw) if raw else cfg.data.root / "tdx_raw"


def _period_source(period: str, source: str) -> str:
    """周期落到哪个源。分钟周期永远 baostock —— 通达信没有公开的分钟整包。

    这个判断单独拎出来，是因为它是「切源」这件事的全部语义：只有 `day` 会换口径。
    """
    return source if period == "day" else "baostock"


def _sync_day_tdx(conn: sqlite3.Connection, *, tdx_root, codes=None, dry_run: bool = False,
                  download: bool = True, markets: tuple[str, ...] = ("sh", "sz", "bj"),
                  kinds: tuple[str, ...] = ("stock", "index")) -> SyncOutcome:
    """日线走通达信整包：下载 → 解压 → 导入，口径是**不复权 raw**。

    与逐只 baostock 的差别不只是快（551 MB 一次拉完 vs 5471 次请求）：整包写的是
    **不复权**价，同一个 `(code, day)` 文件因此换了口径，`sync_state.adjust` 记成 `raw`，
    页面据此标注。`codes` 为空即全导 —— 一期品种表只有 baostock 的 5471 只，
    拿它当过滤器会把通达信里多出来的指数挡在门外，而整包的钱已经花了。
    """
    from .data import tdx

    root = Path(tdx_root)
    if download:
        zip_path = tdx.fetch_package(root)
        tdx.extract_package(zip_path, root)
    if not root.exists():
        return SyncOutcome(period="day", first_error=f"通达信目录不存在：{root}")
    stats = tdx.import_dir(root, conn, "day", markets, kinds, codes or None, dry_run)
    first_error = f"{stats.errors[0][0]}: {stats.errors[0][1]}" if stats.errors else ""
    return SyncOutcome(
        period="day",
        ok=stats.ok,
        failed=stats.failed,
        skipped=stats.skipped_empty + stats.skipped_alias,
        total=stats.ok + stats.failed + stats.skipped_empty + stats.skipped_alias,
        first_error=first_error,
        newest=tuple(sorted((str(m), str(d)) for m, d in stats.newest_by_market)),
    )


def _switch_note(conn: sqlite3.Connection, source: str) -> str:
    """切源时把**不可逆**的那一半说出来：除权因子以后没法再由库内数据反推。

    反推需要同一只票同时有前复权与不复权两份数据。日线一旦改由整包写，前复权那份
    就被覆盖了；届时唯一权威来源是 baostock `query_adjust_factor`（尚未实现）。
    """
    if source != "tdx":
        return ""
    qfq = [r for r in meta.all_sync(conn)
           if str(r["period"]) == "day" and str(r["adjust"] or "") == "2"]
    if not qfq:
        return ""
    return (f"口径切换：日线 {len(qfq)} 只原为 baostock 前复权，本次起改为通达信不复权；"
            f"库内前复权副本被覆盖后，除权因子无法再反推（需 baostock query_adjust_factor）")


def _sync_period(conn: sqlite3.Connection, cfg: Config, period: str, *, codes,
                 full: bool, since: dt.date | None, workers: int) -> SyncOutcome:
    """同步一个周期。**单只失败不中断整批**，错误逐只写入 `meta.sync_state.error`。"""
    cal = get_calendar()
    today = dt.date.today()
    end = cal.last_trading_day(today)
    if not codes:
        log.warning("没有需要同步的代码")
        return SyncOutcome(period=period)

    if workers and workers > 1:
        log.info("--workers=%s：baostock 为单会话 socket 协议，本次串行抓取", workers)

    ok = failed = skipped = 0
    first_error = ""
    for raw in tqdm(codes, desc=f"sync {period}", unit="只"):
        code = str(raw)
        try:
            # 解析放在 try 内：无法识别的代码按单只失败处理，不中断整批
            code = markets.store_key(to_bs_code(raw))
            start = _start_for(code, period, full, cal, end, since)
            if start is None:
                skipped += 1
                continue
            df = fetch_bars(to_bs_code(code), period, start, end, adjust=cfg.bs_adjust)
            if len(df) == 0:
                skipped += 1
                _record(conn, code, period, cfg.bs_adjust, start_ts=None, end_ts=None,
                        rows=0, error=None)
                continue
            store.upsert(code, period, df)
            stored = store.read(code, period)
            _record(
                conn, code, period, cfg.bs_adjust,
                start_ts=str(stored["ts"].iloc[0]) if len(stored) else None,
                end_ts=str(stored["ts"].iloc[-1]) if len(stored) else None,
                rows=len(stored), error=None,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 单只失败必须不中断整批
            failed += 1
            first_error = first_error or f"{raw}: {exc}"
            log.warning("同步失败 code=%s period=%s: %s", raw, period, exc)
            prev = meta.get_sync(conn, code, period)
            _record(
                conn, code, period, cfg.bs_adjust,
                start_ts=prev["start_ts"] if prev else None,
                end_ts=prev["end_ts"] if prev else None,
                rows=int(prev["rows"]) if prev else 0,
                error=str(exc),
            )
    return SyncOutcome(period=period, ok=ok, failed=failed, skipped=skipped,
                       total=len(codes), first_error=first_error)


# ---------------- 每日流水线（Task 16） ----------------
@dataclass
class DailyReport:
    """一次每日流水线的完整结果。

    流水线的每一步都是「尽力而为」：某一步失败只写在报告里，不吞掉后面的步骤 ——
    收盘后的数据是**不可再生**的，因为一次网络抖动就整批跳过，代价远比多跑几分钟大。
    """

    run_day: str
    trading_day: bool = True
    dry_run: bool = False
    source: str = "baostock"
    periods: tuple[str, ...] = ()
    active_periods: tuple[str, ...] = ()
    sync: tuple[SyncOutcome, ...] = ()
    quality_total: int = 0
    quality_failed: int = 0
    quality_error: str = ""
    quality_kinds: dict[str, dict[str, int]] = field(default_factory=dict)
    snapshots: int = 0
    snapshot_failed: int = 0
    scan: ScanReport | None = None
    track_rows: tuple[Any, ...] = ()
    degrade: tuple[str, ...] = ()
    notified: str = ""
    notes: list[str] = field(default_factory=list)


TDX_STALE_DAYS = 10
MARKET_NAMES = {"sh": "沪", "sz": "深", "bj": "北"}


def _csv(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    return tuple(t.strip() for t in str(text).split(",") if t.strip())


def _cmd_tdx(args, cfg: Config) -> int:
    """把 vipdoc 整包导进本地库（口径 raw）。北交所改号合并与逐市场最新日期都要看得见。"""
    from .data import tdx

    if args.period != "day":
        print(
            f"通达信：官方只公开日线整包（hsjday.zip），没有公开的分钟整包，"
            f"因此 --period {args.period} 无法导入；分钟数据请走 baostock。",
            file=sys.stderr,
        )
        return 2

    src = Path(args.src)
    if args.download:
        zip_path = tdx.fetch_package(src)
        print(f"整包已就位：{zip_path}")
        print(f"已解压 {tdx.extract_package(zip_path, src)} 个文件到 {src}")

    markets = _csv(args.markets)
    kinds = _csv(args.kinds)
    codes = _csv(args.codes) or None
    conn = None if args.dry_run else meta.init()
    try:
        stats = tdx.import_dir(src, conn, args.period, markets, kinds, codes, args.dry_run)
    finally:
        if conn is not None:
            conn.close()

    print(
        f"通达信导入({args.period}): 成功 {stats.ok} 失败 {stats.failed} "
        f"空文件 {stats.skipped_empty} 改号合并 {stats.skipped_alias} "
        f"共 {stats.rows} 行（口径 raw）"
    )
    newest = dict(stats.newest_by_market)
    top = max(newest.values(), default="")
    for market in markets:
        name = MARKET_NAMES.get(market, market)
        end = newest.get(market)
        if not end:
            print(f"  最新 {name} 无数据")
            continue
        stale = ""
        if top:
            gap = (dt.date.fromisoformat(top) - dt.date.fromisoformat(end)).days
            if gap > TDX_STALE_DAYS:
                stale = f"（陈旧）落后最新 {gap} 天"
        print(f"  最新 {name} {end}{stale}")
    if stats.aliased:
        print("  改号合并：" + "、".join(f"{o}→{n}" for o, n in stats.aliased))
    for code, err in stats.errors:
        print(f"  失败 {code}: {err}", file=sys.stderr)
    return 0


def _cmd_factors(args, cfg: Config) -> int:
    """反推除权因子。逐只失败不中断，最后按状态汇总 —— 全市场跑几分钟，得知道跳过了什么。"""
    from .data import factors

    if args.period != "day":
        print(f"因子反推目前只支持日线：{args.period} 的前复权参照不在库里", file=sys.stderr)
        return 2
    if args.codes:
        codes = _csv(args.codes)
    elif args.all:
        codes = [s.code for s in build_universe()]
    else:
        conn0 = meta.init()
        try:
            codes = tuple(r["code"] for r in meta.get_watchlist(conn0))
        finally:
            conn0.close()
        if not codes:
            print("自选池是空的：用 --codes 指定代码，或用 --all 跑全市场", file=sys.stderr)
            return 2

    conn = meta.init()
    try:
        results = factors.build_many(
            conn, codes, tdx_root=args.src, period=args.period,
            dry_run=args.dry_run, min_overlap=args.min_overlap,
        )
    finally:
        conn.close()

    counts = factors.summarize(results)
    for status, n in counts.items():
        print(f"  {status}: {n}")
    written = [r for r in results if r.status in ("written", "dry_run")]
    if written:
        segs = sum(r.segments for r in written)
        print(
            f"因子反推({args.period}{'，dry-run' if args.dry_run else ''}): "
            f"{len(written)}/{len(results)} 只有因子，共 {segs} 段"
        )
    else:
        print("因子反推：没有一只票反推出因子", file=sys.stderr)
    for res in results:
        if res.status not in ("written", "dry_run"):
            print(f"  {res.status} {res.code}: {res.note}", file=sys.stderr)
    return 0 if written or args.dry_run else 1


def _cmd_daily(args, cfg: Config) -> int:
    conn = meta.init(cfg.data.meta_db)
    try:
        rep = _run_daily(args, cfg, conn)
    finally:
        conn.close()
    print(_format_daily(rep))
    return _daily_exit_code(rep)


def _run_daily(args, cfg: Config, conn: sqlite3.Connection) -> DailyReport:
    cal = get_calendar()
    today = dt.date.today()
    run_day = cal.last_trading_day(today)
    dry = bool(args.dry_run)
    rep = DailyReport(
        run_day=run_day.isoformat(), trading_day=(run_day == today), dry_run=dry,
        periods=_daily_periods(args, cfg),
    )
    if not rep.trading_day:
        # 周末/节假日被 launchd 补跑：没有新收盘数据，什么也不做，但**不是错误**
        log.info("非交易日：今天 %s（最近交易日 %s），跳过每日流水线", today, run_day)
        return rep

    codes = _resolve_codes(args)
    scope = _period_scope(conn, rep.periods, codes, explicit=bool(args.codes))
    source = _daily_source(args, cfg)
    rep.source = source
    note = _switch_note(conn, source)
    if note:
        rep.notes.append(note)
    if dry:
        log.info("dry-run：跳过同步、落库与推送，只做只读计算")
    else:
        outcomes: list[SyncOutcome] = []
        for period in rep.periods:
            if _period_source(period, source) == "tdx":
                outcomes.append(_sync_day_tdx(
                    conn, tdx_root=_tdx_root(args, cfg), codes=scope[str(period)], dry_run=False,
                    download=not getattr(args, "no_download", False),
                ))
            else:
                outcomes.append(_sync_period(
                    conn, cfg, period, codes=scope[str(period)], full=False, since=args.since,
                    workers=args.workers,
                ))
        rep.sync = tuple(outcomes)

    # 先决定「哪些周期敢用」，再算结构：陈旧周期一律不进结构计算
    rep.active_periods, rep.degrade = _usable_periods(conn, rep.periods)

    if not dry:
        (rep.quality_total, rep.quality_failed, rep.quality_error,
         rep.quality_kinds) = _run_quality()

    rep.snapshots, rep.snapshot_failed = _snapshot_all(
        conn, codes, rep.active_periods, save=not dry, codes_by_period=scope)
    rep.scan = scan_report(
        periods=rep.active_periods, codes=codes, max_workers=int(args.workers or 1),
        conn=conn, save=not dry, run_date=rep.run_day, codes_by_period=scope,
    )
    rep.track_rows = tuple(track_watchlist(
        periods=rep.active_periods or ("day",), conn=conn, save=not dry))

    if not dry:
        notifier, target = _daily_notifier(args, cfg)
        notify_scan(notifier, rep.scan.hits, run_date=rep.run_day)
        notify_track(notifier, rep.track_rows)
        rep.notified = f"{target}（扫描 1 条 + 跟踪 1 条）"
    return rep


def _daily_exit_code(rep: DailyReport) -> int:
    """只有「数据一条都没进来」才算失败。

    非交易日、dry-run、以及部分周期失败都返回 0：定时任务的价值在于**持续记录**，
    动不动就非 0 退出只会让人对红色告警麻木。
    """
    if rep.dry_run or not rep.trading_day:
        return 0
    if rep.sync and all(o.total > 0 and o.ok == 0 and o.failed > 0 for o in rep.sync):
        return 1
    return 0


def _daily_periods(args, cfg: Config) -> tuple[str, ...]:
    items = tuple(args.periods) if args.periods else tuple(str(p) for p in cfg.periods)
    bad = [p for p in items if p not in PERIODS]
    if bad:
        raise SystemExit(f"配置里的周期不认识：{','.join(bad)}（可选 {'/'.join(PERIODS)}）")
    return tuple(dict.fromkeys(items))  # 去重但保留顺序：跑两遍同一个周期纯属浪费


def _daily_notifier(args, cfg: Config):
    kind = str(args.notify)
    path = Path(args.notify_path) if args.notify_path else cfg.log_path.parent / "notify.log"
    return build_notifier(kind, path=path), (str(path) if kind == "file" else kind)


def _run_quality() -> tuple[int, int, str, dict[str, dict[str, int]]]:
    """跨源哨兵校验：抽 20 只票比对外部行情源（`quality.run_all` 的默认口径）。

    它要联网，所以失败一律降级成一行说明，绝不让「外部网站不可达」毁掉当天的结构计算。
    """
    try:
        results = quality.run_all()
    except Exception as exc:  # noqa: BLE001 - 校验失败不阻断流水线
        log.warning("质量校验失败：%s: %s", type(exc).__name__, exc)
        return 0, 0, f"{type(exc).__name__}: {exc}", {}
    failed = sum(1 for r in results if not getattr(r, "ok", True))
    if failed:
        log.warning("质量校验：%d 条失败（详见 data/quality_report.json）", failed)
    kinds: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = kinds.setdefault(getattr(r, "kind", "unknown"), {"total": 0, "failed": 0})
        bucket["total"] += 1
        if not getattr(r, "ok", True):
            bucket["failed"] += 1
    return len(results), failed, "", dict(sorted(kinds.items()))


def _median_end(rows: Sequence[Any]) -> dt.date | None:
    """各票 `sync_state.end_ts` 的中位数。

    用中位数而不是最大值/最小值：只要有一只票碰巧数据新（或新上市），极值就会
    把「全市场分钟数据普遍落后」这个事实掩盖掉。
    """
    days = sorted(d for d in (_as_day(r["end_ts"]) for r in rows) if d is not None)
    if not days:
        return None
    return days[len(days) // 2]


def _as_day(value: Any) -> dt.date | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _usable_periods(
    conn: sqlite3.Connection, periods: Sequence[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """哪些周期可以参与本次结构计算，以及哪些被降级、为什么。

    以**日线的最新一天**为基准：分钟数据只要落后于日线，就当它不可用。宁可只算日线，
    也不能拿几周前的分钟结构冒充「当下」—— 级别不同，中枢与买卖点的结论完全不同。
    """
    by_period: dict[str, list[Any]] = {}
    for row in meta.all_sync(conn):
        by_period.setdefault(str(row["period"]), []).append(row)
    ref = _median_end(by_period.get("day", ()))
    usable: list[str] = []
    degrade: list[str] = []
    for raw in periods:
        period = str(raw)
        if period == "day":
            usable.append(period)
            continue
        med = _median_end(by_period.get(period, ()))
        if med is None:
            degrade.append(f"{period} 分钟：本地无该周期数据")
            continue
        if ref is None:
            usable.append(period)
            continue
        lag = (ref - med).days
        if lag > 0:
            degrade.append(
                f"{period} 分钟：最新 {med.isoformat()}，而日线最新 {ref.isoformat()}，"
                f"落后 {lag} 个自然日")
            continue
        usable.append(period)
    return tuple(usable), tuple(degrade)


def _snapshot_all(conn: sqlite3.Connection, codes: Sequence[str],
                  periods: Sequence[str], *, save: bool,
                  codes_by_period: Mapping[str, Sequence[str]] | None = None) -> tuple[int, int]:
    """把每只票每个周期的结构指纹冻结入库，返回（入库份数, 失败只数）。

    数据不足的票直接跳过（全市场大部分票在首次同步前都没有数据）；
    单只算出异常只记一次失败，不影响其它票。
    """
    synced = {(str(r["code"]), str(r["period"])): r for r in meta.all_sync(conn)}
    saved = failed = 0
    for period in periods:
        period = str(period)
        for code in _scope_codes(codes, codes_by_period, period):
            code = str(code)
            try:
                bars = store.read(code, period)
                row = synced.get((code, period))
                suf = check_sufficiency(bars, period, row["start_ts"] if row else None)
                if not suf.ok:
                    continue
                snap = default_snapshot_source(code, period, bars)
                macd_df = None
                try:
                    macd_df = macd(bars["close"])
                except Exception:  # MACD 算不出来不该拖累结构快照
                    macd_df = None
                payload = build_payload(snap, bars, macd_df=macd_df, period=period)
                if save:
                    meta.save_structure_snapshot(conn, code, period, payload["as_of"], payload)
                saved += 1
            except Exception as exc:  # noqa: BLE001 - 单只失败不中断全市场
                failed += 1
                log.warning("结构快照失败 code=%s period=%s: %s", code, period, exc)
    return saved, failed


def _format_source(rep: DailyReport) -> str:
    """摘要里这行是运维唯一的线索：切了源还写「baostock 前复权」就是撒谎。"""
    if rep.source != "tdx":
        return ("数据源：baostock 前复权（本地 Parquet）；"
                "校验用外部源：东财 fqt=1（不可达时回退腾讯 qfq）")
    line = ("数据源：日线=通达信整包（不复权 raw）；"
            "校验用外部源：东财 fqt=1（不可达时回退腾讯 qfq）")
    if any(p != "day" for p in rep.periods):
        line += "；分钟=baostock 前复权"
    return line


def _format_daily(rep: DailyReport) -> str:
    lines = [f"=================== 每日流水线 {rep.run_day} ==================="]
    if not rep.trading_day:
        lines.append(f"非交易日：跳过（最近交易日 {rep.run_day}）")
        return "\n".join(lines)
    if rep.dry_run:
        lines.append("dry-run：不联网同步、不落库、不推送（只读彩排）")
    lines.append(f"周期：{','.join(rep.periods) or '（无）'}；"
                 f"参与结构计算：{','.join(rep.active_periods) or '（无）'}")
    lines.append(_format_source(rep))
    if rep.dry_run:
        lines.append("同步：dry-run 跳过")
        lines.append("校验：dry-run 跳过（不联网）")
    else:
        lines.append("同步：" + ("；".join(o.summary() for o in rep.sync) or "（无）"))
        if rep.quality_error:
            lines.append(f"校验：失败（{rep.quality_error}），不影响后续步骤")
        else:
            line = f"校验：抽样 {rep.quality_total} 条，失败 {rep.quality_failed} 条"
            if rep.quality_kinds:
                line += "（" + "；".join(
                    f"{k} {v['total']} 条失败 {v['failed']}"
                    for k, v in rep.quality_kinds.items()
                ) + "）"
            lines.append(line)
    lines.append(
        f"结构快照：可入库 {rep.snapshots} 份（dry-run 未落库，失败 {rep.snapshot_failed} 只）"
        if rep.dry_run else
        f"结构快照：入库 {rep.snapshots} 份，失败 {rep.snapshot_failed} 只"
    )
    if rep.scan is not None:
        lines.append(
            f"扫描：任务 {rep.scan.tasks} 个，命中 {len(rep.scan.hits)} 条，"
            f"跳过 {len(rep.scan.skipped)}，失败 {len(rep.scan.errors)}，"
            f"耗时 {rep.scan.elapsed:.1f}s"
        )
    changed = sum(1 for r in rep.track_rows if getattr(r, "has_change", False))
    lines.append(f"自选池：{len(rep.track_rows)} 行，其中 {changed} 只有变化")
    if rep.degrade:
        lines.append("降级：分钟数据陈旧，本次降级为仅日线 —— " + "；".join(rep.degrade))
    lines.append(f"推送：{rep.notified}" if rep.notified else "推送：（dry-run 未推送）")
    for note in rep.notes:
        lines.append(f"备注：{note}")
    return "\n".join(lines)


# ---------------- 内部 ----------------
#: 分钟周期的缺省取数范围。日线有通达信整包（一次 551 MB、本地解析 83 秒），全市场跑得起；
#: 分钟没有公开整包，只能逐只走 baostock —— 实测 30 分 ≈30 秒/只、5 分 ≈162 秒/只，
#: 5471 只 ≈100 小时，会直接撞上第二天开盘。所以分钟缺省只跑自选池。
#: 显式 `--codes` 是用户自己圈定的范围，照办：范围已经是有界的，不存在误伤全市场这回事。
def _is_minute_period(period: str) -> bool:
    """**只有分钟周期**才按自选池圈范围。

    不能写成 `period != "day"`：周线/月线是由日线聚合出来的**派生周期**，既不需要
    单独同步，也不该被当成分钟周期圈进自选池（`_period_scope` 会拿它去算同步范围）。
    """
    key = str(period)
    return not periods.is_derived(key) and key != "day"


def _codes_for_period(conn: sqlite3.Connection, period: str, codes: Sequence[str],
                      *, explicit: bool) -> list[str]:
    """按周期给出**取数范围**：日线=给定范围；分钟=自选池（除非显式指定了 `--codes`）。"""
    if not _is_minute_period(period) or explicit:
        return [str(c) for c in codes]
    return [str(r["code"]) for r in meta.get_watchlist(conn)]


def _period_scope(conn: sqlite3.Connection, periods: Sequence[str], codes: Sequence[str],
                  *, explicit: bool) -> dict[str, list[str]]:
    """一次算清每个周期的范围，同步/快照/扫描**共用同一份**，免得三处各写一套。"""
    return {
        str(p): _codes_for_period(conn, str(p), codes, explicit=explicit) for p in periods
    }


def _scope_codes(codes: Sequence[str],
                 codes_by_period: Mapping[str, Sequence[str]] | None,
                 period: str) -> list[str]:
    """某个周期实际要跑的代码；没给分周期范围就退回同一份（旧行为）。"""
    if codes_by_period is None:
        return [str(c) for c in codes]
    return [str(c) for c in codes_by_period.get(str(period), ())]


def _resolve_codes(args) -> list[str]:
    if args.codes:
        # 保留原始写法（可能带 sh./sz. 前缀），标准化放到同步循环内，
        # 让无法识别的代码走「单只失败」路径
        return [raw.strip() for raw in str(args.codes).split(",") if raw.strip()]
    # 全市场：品种表含退市股，避免幸存者偏差
    return [s.code for s in build_universe()]


def _start_for(
    code: str, period: str, full: bool, cal, end: dt.date, since: dt.date | None = None
) -> str | None:
    """返回拉取起始日；已是最新则返回 None（跳过）。

    `since`（`--since`）只作用于**本地还没有数据的票**：首次全市场同步没必要为 5471 只票
    各拉 35 年。已有数据的票仍然纯增量 —— 若把起点强行压到 `since`，本地数据停在上古年份的
    票就会被跳过中间几年、在文件里留下空洞，而缠论结构会跨着洞算，这比少几年历史危险。
    """
    if full:
        return FULL_START
    last = store.last_ts(code, period)
    if not last:
        return (since or dt.date.fromisoformat(FULL_START)).isoformat()
    nxt = cal.next_trading_day(str(last)[:10])
    return None if nxt > end else nxt.isoformat()


def _record(
    conn: sqlite3.Connection,
    code: str,
    period: str,
    adjust: str,
    start_ts: str | None,
    end_ts: str | None,
    rows: int,
    error: str | None,
) -> None:
    meta.set_sync(conn, code, period, start_ts, end_ts, rows, adjust, error=error)


if __name__ == "__main__":
    raise SystemExit(main())
