"""chanlun 命令行入口。

用法：
    python -m chanlun universe [--enrich]
    python -m chanlun sync --period day [--codes 600000,000001] [--full] [--workers 4]

约定：
- 日志同时写入 `logs/chanlun.log` 与 stdout。
- `sync` 单只失败不中断，错误写入 `meta.sync_state.error`；
  仅当**全部**失败时以非 0 退出码结束。
- 增量为默认：从 `store.last_ts` 的下一个交易日起拉取；`--full` 从 1990-01-01 全量重拉。
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sqlite3
import sys
from pathlib import Path

from tqdm import tqdm

from .calendar import get_calendar
from .config import Config, load_config
from .data import meta, store
from .data.baostock_source import fetch_bars, strip_bs_code, to_bs_code
from .data.universe import build_universe

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
    p_sync.add_argument("--codes", default=None, help="逗号分隔代码，缺省为品种表全市场")
    p_sync.add_argument("--full", action="store_true", help="从 1990-01-01 全量重拉")
    p_sync.add_argument(
        "--workers", type=int, default=4,
        help="预留的并发度；baostock 单会话 socket 协议下当前串行执行",
    )
    return parser


# ---------------- 子命令 ----------------
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


def _cmd_sync(args, cfg: Config) -> int:
    period = str(args.period)
    conn = meta.init(cfg.data.meta_db)
    try:
        cal = get_calendar()
        today = dt.date.today()
        end = cal.last_trading_day(today)
        codes = _resolve_codes(args)
        if not codes:
            log.warning("没有需要同步的代码")
            print("同步完成: 成功 0 失败 0 跳过 0（共 0 只）")
            return 0

        if args.workers and args.workers > 1:
            log.info("--workers=%s：baostock 为单会话 socket 协议，本次串行抓取", args.workers)

        ok = failed = skipped = 0
        for raw in tqdm(codes, desc=f"sync {period}", unit="只"):
            code = str(raw)
            try:
                # 解析放在 try 内：无法识别的代码按单只失败处理，不中断整批
                code = strip_bs_code(to_bs_code(raw))
                start = _start_for(code, period, args.full, cal, end)
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
                log.warning("同步失败 code=%s period=%s: %s", raw, period, exc)
                prev = meta.get_sync(conn, code, period)
                _record(
                    conn, code, period, cfg.bs_adjust,
                    start_ts=prev["start_ts"] if prev else None,
                    end_ts=prev["end_ts"] if prev else None,
                    rows=int(prev["rows"]) if prev else 0,
                    error=str(exc),
                )

        print(f"同步完成: 成功 {ok} 失败 {failed} 跳过 {skipped}（共 {len(codes)} 只）")
        return 0 if (ok > 0 or failed == 0) else 1
    finally:
        conn.close()


# ---------------- 内部 ----------------
def _resolve_codes(args) -> list[str]:
    if args.codes:
        # 保留原始写法（可能带 sh./sz. 前缀），标准化放到同步循环内，
        # 让无法识别的代码走「单只失败」路径
        return [raw.strip() for raw in str(args.codes).split(",") if raw.strip()]
    # 全市场：品种表含退市股，避免幸存者偏差
    return [s.code for s in build_universe()]


def _start_for(
    code: str, period: str, full: bool, cal, end: dt.date
) -> str | None:
    """返回拉取起始日；已是最新则返回 None（跳过）。"""
    if full:
        return FULL_START
    last = store.last_ts(code, period)
    if not last:
        return FULL_START
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
