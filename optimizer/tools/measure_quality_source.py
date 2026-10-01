"""G5a 只读审计：质量校验的「口径」能不能从产物里看出来。

读 ``data/quality_report.json``（真实跑过的那一份），把里面的 results 重新
交给 ``quality._write_report`` 落一次盘，再看两件事：

1. ``summary`` 分不分得清哨兵种类（``cross_source`` 要联网取外部源，
   ``latest_equals_actual`` 本地就能算，两者共用一个数字时，
   外部源整年不可达也照样显示「40 条全过」）；
2. ``daily`` 汇总行有没有如实说出数据源与校验口径。

不联网、不写真实数据目录（写到临时文件），由 ``optimizer/agent.py`` 在
影子目录里以 ``python -c`` 运行。
"""

import json
import tempfile
from pathlib import Path

from chanlun import __main__ as cli
from chanlun.data import quality

raw = json.loads(Path("data/quality_report.json").read_text(encoding="utf-8"))
results = [quality.CheckResult(**r) for r in raw["results"]]

# 1) 落盘报告的口径：summary 是否分得清哨兵种类
tmp = Path(tempfile.mkdtemp()) / "report.json"
quality._write_report(tmp, results)
summary = json.loads(tmp.read_text(encoding="utf-8"))["summary"]
kinds = summary.get("kinds", {})

# 2) daily 汇总行：数据源与校验口径是否如实披露
kw = dict(run_day=str(raw["generated_at"])[:10], periods=("day",),
          active_periods=("day",), quality_total=summary["total"],
          quality_failed=summary["failed"])
try:
    rep = cli.DailyReport(**kw, quality_kinds=kinds)  # 打上提案后才有这个字段
except TypeError:
    rep = cli.DailyReport(**kw)
lines = [ln for ln in cli._format_daily(rep).splitlines()
         if ln.startswith(("数据源：", "校验："))]

print("__METRICS__" + json.dumps({
    "metrics": {
        "summary_json": json.dumps(summary, ensure_ascii=False, sort_keys=True),
        "summary_kinds": sorted(kinds),
        "校验行": next((ln for ln in lines if ln.startswith("校验：")), ""),
    },
    "mech": {
        "lines": lines,
        "kind_counts": {
            k: sum(1 for r in raw["results"] if r["kind"] == k)
            for k in sorted({r["kind"] for r in raw["results"]})
        },
    },
}, ensure_ascii=False))
