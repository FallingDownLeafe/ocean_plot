"""
hover_rows.py
=============
統一的游標規則（2026-11-20 新增），在 dash_app 各 render callback 把圖組好之後呼叫 apply_row_hover(fig)。

要解決的問題
------------
hovermode="x unified" 下，某條線在游標時間沒有值（NaN）或沒有資料點時，Plotly 不會顯示「空」，
而是改抓那條線離游標最近（20 px 內）、有值的點，且不註明那是別的時間。
例：成功浮球 2026-10-23 08 時 HMAX 為空，游標停在 08 時卻顯示 07 時的 1608 cm（浮標波浪圖已於
2026-11-19 用 build_buoy_figure._row_hover_trace 修正；本模組把同一做法推廣到所有圖）。

規則（每個子圖，即每個 x 軸各自處理）
------------------------------------
1. 「連續序列」= 線（mode 含 lines），或點數 ≥ 該子圖時間格一半的點狀序列（例：水位差值）。
   這些 trace 設 hoverinfo='skip'，改由一條透明的「游標載體」提供游標文字：
   每個時間點一段，列出每條序列在「剛好這個時間」的值，沒有值寫「無值」。
   序列取樣比較疏（例：逐時氣壓疊在 6 分鐘水位上）時，顯示它在游標之前最近一點的值，並標出該點時間；
   超過它自己一個取樣間隔都沒有點，寫「無值」。
2. 稀疏的點狀標記：
   - hovertemplate 自己會顯示時間（含 %{x}，例：高低潮、水位非 Q 原始值）→ 保留自己的游標，不會誤植。
   - 其他（例：浮標「非Q」紅叉）→ 也設 skip，只有在剛好這個時間有點時，載體文字多列一行。
3. 不處理：隱藏中的 trace（visible=False／'legendonly'，例：水位圖的進階圖層，日後由勾選框打開時
   仍用自己的游標）；已經有載體的子圖（build_buoy_figure 的波浪圖，trace.meta == 'row_hover'）。
4. 載體用 mode='lines'、透明線：Plotly 的框選只選有 marker 的 trace，所以不會被框選到，
   不影響框選產生 SQL；加在 fig.data 最後面，不影響 trace_meta 的索引對應。
5. 一個子圖的時間格超過 MAX_POINTS 時不加載體（游標文字太大會拖慢瀏覽器），維持 Plotly 原本行為。

純函式：只依賴 plotly／pandas，不 import Dash。
"""

import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go

MAX_POINTS = 30000          # 6 分鐘資料約 125 天
CARRIER_META = "row_hover"
_UNIT_RE = re.compile(r"[（(]\s*([^()（）]+?)\s*[)）]\s*$")


def _visible(tr):
    return tr.visible in (None, True)


def _xy(tr):
    """trace 的 (時間 Index, 值 ndarray)；x 不是時間或無資料時回傳 None。"""
    if tr.x is None or tr.y is None:
        return None
    try:
        x = pd.to_datetime(pd.Index(np.asarray(tr.x)))
    except (TypeError, ValueError):
        return None
    y = pd.to_numeric(pd.Series(np.asarray(tr.y, dtype=object)), errors="coerce").to_numpy(dtype=float)
    if len(x) != len(y) or len(x) == 0:
        return None
    return x, y


def _unit(fig, tr):
    yname = "yaxis" + (tr.yaxis[1:] if tr.yaxis and tr.yaxis != "y" else "")
    ax = fig.layout[yname] if yname in fig.layout else None
    title = (ax.title.text if ax is not None and ax.title is not None else None) or ""
    m = _UNIT_RE.search(title)
    return m.group(1) if m else ""


def _fmt(v, unit):
    if v is None or not np.isfinite(v):
        return "無值"
    s = f"{v:.0f}" if abs(v) >= 100 else f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{s} {unit}".rstrip()


def _series_text(x, y, grid, unit):
    """每個 grid 時間的文字（見檔頭規則 1）。向量化，3 萬格 × 數條線約 1 秒內。"""
    s = pd.Series(y, index=x)
    s = s[~s.index.duplicated(keep="first")].sort_index()
    idx = s.index
    vals = s.to_numpy(dtype=float)
    exact = grid.isin(idx)
    pos = idx.searchsorted(grid, side="right") - 1
    step = pd.Series(idx).diff().median() if len(idx) > 1 else pd.NaT
    out = np.empty(len(grid), dtype=object)
    ex_pos = idx.get_indexer(grid[exact])
    out[exact] = [_fmt(v, unit) for v in vals[ex_pos]]
    rest = np.where(~exact)[0]
    for k in rest:
        p = pos[k]
        if p >= 0 and pd.notna(step) and grid[k] - idx[p] < step:
            out[k] = f"{_fmt(vals[p], unit)}（{idx[p]:%m/%d %H:%M}）"
        else:
            out[k] = "無值"
    return list(out)


def apply_row_hover(fig: go.Figure) -> go.Figure:
    """就地修改並回傳 fig。見檔頭規則。"""
    if fig is None or not len(fig.data):
        return fig
    groups: dict = {}
    for i, tr in enumerate(fig.data):
        if tr.type not in ("scatter", "scattergl"):
            continue
        groups.setdefault(tr.xaxis or "x", []).append(i)

    for xa, idxs in groups.items():
        if any(fig.data[i].meta == CARRIER_META for i in idxs):
            continue                                        # 已有載體（浮標波浪圖）
        info = []
        for i in idxs:
            tr = fig.data[i]
            if not _visible(tr):
                continue
            xy = _xy(tr)
            if xy is None:
                continue
            info.append((i, tr, xy))
        if not info:
            continue
        grid_all = pd.DatetimeIndex(sorted(set().union(*[set(xy[0]) for _, _, xy in info])))
        if len(grid_all) > MAX_POINTS:
            continue
        mode_of = lambda t: (t.mode or "lines+markers")
        series, sparse = [], []
        for i, tr, xy in info:
            dense = "lines" in mode_of(tr) or len(xy[0]) >= 0.5 * len(grid_all)
            if dense:
                series.append((i, tr, xy))
            elif "%{x}" not in (tr.hovertemplate or "") and tr.hoverinfo != "skip":
                sparse.append((i, tr, xy))
            # 其餘稀疏標記：自己顯示時間，或本來就 skip → 不動
        if len(series) < 1:
            continue
        # 載體的時間格只用連續序列的時間（稀疏標記不增加格點）
        grid = pd.DatetimeIndex(sorted(set().union(*[set(xy[0]) for _, _, xy in series])))
        cols = []
        for i, tr, (x, y) in series:
            name = tr.name or f"trace {i}"
            cols.append((name, _series_text(x, y, grid, _unit(fig, tr))))
        extra = [[] for _ in range(len(grid))]
        gpos = {t: k for k, t in enumerate(grid)}
        for i, tr, (x, y) in sparse:
            unit = _unit(fig, tr)
            for t, v in zip(x, y):
                k = gpos.get(t)
                if k is not None:
                    extra[k].append(f"⚠ {tr.name}：{_fmt(v, unit)}")
        text = []
        for k in range(len(grid)):
            lines = [f"{name}：{vals[k]}" for name, vals in cols] + extra[k]
            text.append("<br>" + "<br>".join(lines))

        for i, *_ in series + sparse:
            fig.data[i].hoverinfo = "skip"
            fig.data[i].hovertemplate = None

        # 載體放在第一條序列的 y 軸上，y 用該軸資料的中位數（不影響自動範圍）
        first = series[0][1]
        same_axis = [xy[1] for _, tr, xy in series if (tr.yaxis or "y") == (first.yaxis or "y")]
        vals = np.concatenate(same_axis) if same_axis else np.array([])
        vals = vals[np.isfinite(vals)]
        y0 = float(np.median(vals)) if len(vals) else 0.0
        fig.add_trace(go.Scatter(
            x=grid, y=[y0] * len(grid), text=text, mode="lines",
            line=dict(width=0, color="rgba(0,0,0,0)"),
            name="本時資料", showlegend=False, meta=CARRIER_META,
            hovertemplate="%{text}<extra>%{fullData.name}</extra>",
            xaxis=xa, yaxis=first.yaxis or "y",
        ))
    return fig
