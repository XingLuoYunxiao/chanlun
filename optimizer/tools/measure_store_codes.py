"""G5b 只读审计：带市场前缀的代码能不能读到本地 Parquet。

``store.read`` 对不存在的路径**静默返回空表**，所以「代码写法不对」和
「这只票没有数据」在调用方看来一模一样。这个脚本用同一只票的几种写法
各读一次，把行数摆在一起。探针一律选审计集内的票（``AUDIT_CODES``），
这样只要这一轮的冻结快照里有它们，尺子就不会因为行情仓在别处重写而变哑；
``bj.430047`` 是故意留的反例：本地确实没有它的数据，归一化不该凭空造出数字。

只读本地 Parquet，由 ``optimizer/agent.py`` 在影子目录里以 ``python -c`` 运行。
"""

import json

from chanlun.data import store

CODES = (
    # 沪市：带前缀 / 裸码 / 大写前缀，同一只票
    "sh.600030", "600030", "SH.600030",
    # 深市：同上
    "sz.000333", "000333", "SZ.000333",
    # 反例：本地确实没有这只票，归一化不该凭空造出数字
    "bj.430047",
)
START, END = "2024-01-01", "2024-03-01"

rows = {}
for code in CODES:
    df = store.read(code, "day", start=START, end=END)
    rows[code] = 0 if df is None else int(len(df))

path = store.path_for("sh.600030", "day")
print("__METRICS__" + json.dumps({
    "metrics": rows,
    "mech": {
        "path_for('sh.600030')": str(path),
        "path_exists": path.exists(),
        "window": f"{START}..{END}",
        "probe_codes_in_audit_set": ["sh.600030", "600030", "sz.000333", "000333"],
    },
}, ensure_ascii=False))
