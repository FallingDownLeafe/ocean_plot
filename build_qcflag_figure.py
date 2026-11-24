"""
build_qcflag_figure.py
======================
多采潮位檢核旗標唯讀頁籤的圖（2026-11-20 新增）。資料由 qcflag_engine.py 整理好傳進來，
本檔只畫圖（不碰資料庫、不 import Dash）。

版面（共用時間軸，由上而下）：
  1. 水位：觀測（本次查詢的主資料庫）、天文潮（主站 tide6ha h）、鄰站推估（有才畫）、
     新規則彙總 qc_summary 3／4 的點（空心菱形＝可疑、空心圈＝不通過，同浮標自動檢查的樣式）、
     基準偏移事件的垂直虛線（只畫同一事件各方法中最早的時間）、颱風海警／陸警底色。
  2. 檢核結果方格：第 1 列舊人工 qc（非 Q）、第 2 列新規則彙總，細線以下是各子項；go.Heatmap 畫；空白＝通過。
  3. 觀測 − 鄰站推估：灰帶＝± 門檻，超出的點即 qc_nearby 不通過（沒有推估就不畫這列）。

圖例（2026-11-20 使用者看畫面後改）：
  - 每張子圖各自一組圖例，放在該子圖標題同一行的右側（Plotly legend／legend2），點圖例只開關該子圖的線或點。
  - 方格的顏色說明不放圖例（Heatmap 是一整個 trace，點了也關不掉單一顏色），改成方格標題列右側的色塊文字。

§13 設計原則：整片底色只留給颱風；基準偏移在主圖只畫開始的垂直虛線，偏移期間看方格列
（qc_datum、qc_site_offset）；逐點判斷用標記形狀。

游標：三列各放一條透明載體（meta='row_hover'，hover_rows.apply_row_hover 會跳過），
停在任何一列都列出同一時間的觀測、天文潮、推估、舊 qc、彙總、非通過的子項與基準偏移事件。
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import line_style as LW   # 全系統折線粗細（2026-11-20 統一）
from plotly.subplots import make_subplots

from qcflag_engine import CODE_LABEL, OLD_QC_ROW, event_line, row_name
from typhoon_bands import add_warn_bands

_C = {"plot": "#1E1E1E", "text": "#CCD0D4", "grid": "rgba(200,214,229,0.12)"}
_FONT = "標楷體, PingFang TC, Noto Sans CJK TC, Arial, sans-serif"

# 線色：觀測、天文潮沿用 §7（音波式藍、諧和預報綠點線）；鄰站推估青綠（多采 #0f8a8a 在深色底調亮）
LINE = {"obs": "#1f77b4", "tide": "#2ca02c", "est": "#26b3a6", "datum": "#e8e8e8"}
# 粗細集中在這裡調（2026-11-20 使用者：線太細、標記太肥 → 線加粗、標記縮小）
WIDTH = {"obs": LW.MAIN, "tide": LW.SUB, "est": LW.SUB, "datum": 1.5, "dev": LW.MAIN}   # datum 垂直線比 GUIDE 粗：要看得到
MARK_SIZE = (7, 1.5)          # 彙總標記（大小, 邊線寬）
MARK_SIZE_DENSE = (5, 1.0)    # 標記超過 DENSE_N 點時（例：壓力式整段偏移數千點），不要連成粗帶蓋住觀測線
DENSE_N = 300
OUT_SIZE = 4                  # 觀測 − 推估超出門檻的點
# 方格顏色（深色底）：可疑、不通過同浮標自動檢查；「未檢測」接近底色（不重要）、「缺漏」淺灰（醒目），
# 明暗關係和多采檢視器（淺色底：未檢測淺灰、缺漏近黑）相同；舊人工 qc 用藍
STRIP = [(2, "未檢測 2", "#4a515c"), (9, "缺漏 9", "#c9c7c0"), (3, "可疑 3", "#fab219"),
         (4, "不通過 4", "#ff5a5a"), (-1, "舊人工 qc 非 Q", "#5c8de6")]
_CAT = {code: i + 1 for i, (code, _, _) in enumerate(STRIP)}     # 代碼 → 色階類別 1..5
MARK = {4: dict(symbol="circle-open", color="#ff5a5a", tag="不通過 4"),
        3: dict(symbol="diamond-open", color="#fab219", tag="可疑 3")}

PX_LEVEL, PX_ROW, PX_DIFF, PX_GAP = 380, 24, 170, 50
TOP_PX = 56              # 標題
MAX_CARRIER = 30000      # 同 hover_rows.MAX_POINTS


def empty_figure(msg: str) -> go.Figure:
    return go.Figure(layout=dict(
        template="plotly_dark", paper_bgcolor=_C["plot"], plot_bgcolor=_C["plot"],
        font=dict(family=_FONT, size=15, color=_C["text"]), height=300,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        annotations=[dict(text=msg, showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
                          font=dict(size=16, color=_C["text"]))],
    ))


def _colorscale():
    n = len(STRIP)
    cs = []
    for i, (_, _, color) in enumerate(STRIP):
        cs += [[i / n, color], [(i + 1) / n, color]]
    return cs


def _fmt(v, unit="mm"):
    return "無值" if v is None or pd.isna(v) else f"{v:.0f} {unit}"


def _hover_text(v, cols, event_notes):
    """每個時間點一段游標文字。"""
    subs = [c for c in cols if c != "qc_summary"]
    has_est = v["est"].notna().any()
    out = []
    for r in v.itertuples(index=False):
        d = r._asdict()
        lines = [f"觀測：{_fmt(d.get('obs'))}", f"天文潮：{_fmt(d.get('tide'))}"]
        if has_est:
            est, thr = d.get("est"), d.get("thr")
            if pd.notna(est):
                dev = d.get("obs") - est if pd.notna(d.get("obs")) else None
                lines.append(f"鄰站推估：{_fmt(est)}（觀測 − 推估 {_fmt(dev)}，門檻 ±{_fmt(thr)}）")
            else:
                lines.append("鄰站推估：無值")
        oq = d.get("old_qc")
        lines.append(f"舊人工 qc：{oq if isinstance(oq, str) and oq else 'Q 或無資料'}")
        s = d.get("qc_summary")
        lines.append(f"<b>新規則彙總：{CODE_LABEL.get(int(s), s) if pd.notna(s) else '無旗標'}</b>")
        bad = [f"{row_name(c)} {CODE_LABEL.get(int(d[c]), d[c])}" for c in subs
               if pd.notna(d.get(c)) and int(d[c]) in (3, 4, 9)]
        if bad:
            lines.append("　" + "<br>　".join(bad))
            # 子項有 3／4、彙總卻通過：多采規則的例外（颱風期間、有佐證、連續鋸齒…），規則見右側面板
            if pd.notna(s) and int(s) in (1, 2) and any(
                    int(d[c]) in (3, 4) for c in subs if pd.notna(d.get(c))):
                lines.append("　→ 彙總依例外規則不計（見右側「彙總規則」）")
        for note in event_notes.get(r.TIME, []):
            lines.append(f"<b>⚠ 基準偏移事件</b>：{note}")
        out.append("<br>" + "<br>".join(lines))
    return out


def _strip_key() -> str:
    """方格的顏色說明（色塊＋文字），放方格標題列右側。"""
    order = [4, 3, 9, 2, -1]
    by = {code: (name, color) for code, name, color in STRIP}
    return "　".join(f"<span style='color:{by[c][1]}'>■</span> {by[c][0]}" for c in order)


def build_qcflag_figure(v: pd.DataFrame, cols, marks: pd.DataFrame, groups: list,
                        step_label: str, title: str, warn=None, est_label=None) -> go.Figure:
    """
    v        : qcflag_engine.merge_view()（或 to_hourly()）的結果：TIME, obs, tide, old_qc, est, thr, 各旗標欄
    cols     : 方格要畫的旗標欄（qcflag_engine.strip_columns），最後一個是 qc_summary
    marks    : qcflag_engine.summary_marks() 的標記表
    groups   : qcflag_engine.group_events()，只傳 start 在畫面期間內的組
    step_label: '逐 6 分鐘'／'逐時'
    warn     : typhoon_bands.warn_ranges() 的結果（bridge 的 land_range）
    est_label: 鄰站推估所用模型儀器的說明（None＝沒有推估）
    """
    if v is None or v.empty:
        return empty_figure("這段期間沒有資料")
    has_est = v["est"].notna().any()
    # 方格列：舊人工 qc、新規則彙總（兩列相鄰方便對照），細線以下是子項
    subs = [c for c in cols if c != "qc_summary"]
    strip_cols = (["qc_summary"] if "qc_summary" in cols else []) + subs
    rows = [OLD_QC_ROW, *[row_name(c) for c in strip_cols]]
    heights = [PX_LEVEL, PX_ROW * len(rows)] + ([PX_DIFF] if has_est else [])
    n = len(heights)
    plot_px = sum(heights) + PX_GAP * (n - 1)
    titles = [f"水位（{step_label}，mm）", "檢核結果方格（空白＝通過）"]
    if has_est:
        titles.append(f"觀測 − 鄰站推估（{est_label or ''}）")
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True, row_heights=heights,
                        vertical_spacing=PX_GAP / plot_px, subplot_titles=titles)
    t = v["TIME"]

    # ── 1. 水位 ─────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(x=t, y=v["tide"], name="天文潮", mode="lines",
                             line=dict(color=LINE["tide"], width=WIDTH["tide"], dash="dot"),
                             hoverinfo="skip"), row=1, col=1)
    if has_est:
        fig.add_trace(go.Scatter(x=t, y=v["est"], name="鄰站推估", mode="lines",
                                 line=dict(color=LINE["est"], width=WIDTH["est"]), hoverinfo="skip"),
                      row=1, col=1)
    fig.add_trace(go.Scatter(x=t, y=v["obs"], name="觀測", mode="lines",
                             line=dict(color=LINE["obs"], width=WIDTH["obs"]), hoverinfo="skip"),
                  row=1, col=1)

    # 基準偏移：每組一條垂直虛線（同一 trace，用 None 斷開）
    yv = pd.concat([v["obs"], v["tide"]]).dropna()
    if groups and not yv.empty:
        pad = (yv.max() - yv.min()) * 0.05 or 10
        lo, hi = yv.min() - pad, yv.max() + pad
        xs, ys = [], []
        for g in groups:
            xs += [g["start"], g["start"], None]
            ys += [lo, hi, None]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="基準偏移（最早斷點）",
                                 line=dict(color=LINE["datum"], width=WIDTH["datum"], dash="dash"),
                                 hoverinfo="skip"), row=1, col=1)

    for level in (3, 4):
        m = marks[marks["LEVEL"] == level] if marks is not None and not marks.empty else None
        if m is None or m.empty:
            continue
        st = MARK[level]
        size, lw = MARK_SIZE if len(m) <= DENSE_N else MARK_SIZE_DENSE
        fig.add_trace(go.Scatter(x=m["TIME"], y=m["Y"], mode="markers",
                                 name=f"彙總 {st['tag']}",
                                 marker=dict(symbol=st["symbol"], size=size, color=st["color"],
                                             line=dict(width=lw, color=st["color"])),
                                 hoverinfo="skip"), row=1, col=1)
    add_warn_bands(fig, warn, row=1, col=1, theme="dark", label=True)

    # ── 2. 方格 ─────────────────────────────────────────────────────────
    z = []
    old = v["old_qc"] if "old_qc" in v else pd.Series(None, index=v.index)
    z.append([_CAT[-1] if isinstance(q, str) and q else np.nan for q in old])
    for c in strip_cols:
        s = v[c] if c in v else pd.Series(np.nan, index=v.index)
        z.append([_CAT.get(int(x), np.nan) if pd.notna(x) else np.nan for x in s])
    fig.add_trace(go.Heatmap(x=t, y=rows, z=z, zmin=0.5, zmax=len(STRIP) + 0.5,
                             colorscale=_colorscale(), showscale=False, hoverinfo="skip",
                             showlegend=False, xgap=0, ygap=2), row=2, col=1)
    fig.update_yaxes(categoryorder="array", categoryarray=rows, autorange="reversed",
                     tickfont=dict(size=12), showgrid=False, fixedrange=True, row=2, col=1)
    if "qc_summary" in cols and subs:   # 彙總與子項之間一條細線（類別軸第 i 列的位置＝i）
        fig.add_shape(type="line", xref="x2 domain", x0=0, x1=1, yref="y2", y0=1.5, y1=1.5,
                      line=dict(color="rgba(220,220,220,0.45)", width=1))

    # ── 3. 觀測 − 推估 ──────────────────────────────────────────────────
    if has_est:
        dev = v["obs"] - v["est"]
        thr = v["thr"]
        fig.add_trace(go.Scatter(x=t, y=thr, mode="lines", line=dict(width=0), showlegend=False,
                                 hoverinfo="skip", legend="legend2"), row=3, col=1)
        fig.add_trace(go.Scatter(x=t, y=-thr, mode="lines", line=dict(width=0), fill="tonexty",
                                 fillcolor="rgba(200,200,200,0.18)", name="± 門檻",
                                 hoverinfo="skip", legend="legend2"), row=3, col=1)
        fig.add_trace(go.Scatter(x=t, y=dev, mode="lines", name="觀測 − 推估",
                                 line=dict(color=LINE["est"], width=WIDTH["dev"]),
                                 hoverinfo="skip", legend="legend2"), row=3, col=1)
        out = dev.abs() > thr
        if out.any():
            fig.add_trace(go.Scatter(x=t[out], y=dev[out], mode="markers", name="超出門檻",
                                     marker=dict(color="#ff5a5a", size=OUT_SIZE), hoverinfo="skip",
                                     legend="legend2"), row=3, col=1)
        fig.update_yaxes(title_text="差 (mm)", zeroline=True, zerolinecolor="rgba(200,200,200,0.4)",
                         row=3, col=1)
        add_warn_bands(fig, warn, row=3, col=1, theme="dark", label=False)

    # ── 游標載體 ────────────────────────────────────────────────────────
    if len(v) <= MAX_CARRIER:
        notes = {}
        if groups:
            grid = pd.DatetimeIndex(t)
            for g in groups:
                for _, r in g["rows"].iterrows():
                    k = grid.searchsorted(r["break_time"])   # 斷點落在第一個 ≥ 它的格點
                    if k < len(grid):
                        notes.setdefault(grid[k], []).append(event_line(r))
        text = _hover_text(v, cols, notes)
        y_level = float(np.nanmedian(v["obs"])) if v["obs"].notna().any() else 0.0
        carriers = [(1, [y_level] * len(v)), (2, [rows[len(rows) // 2]] * len(v))]
        if has_est:
            carriers.append((3, [0.0] * len(v)))
        for row, y in carriers:
            fig.add_trace(go.Scatter(x=t, y=y, text=text, mode="lines",
                                     line=dict(width=0, color="rgba(0,0,0,0)"),
                                     name="本時資料", showlegend=False, meta="row_hover",
                                     hovertemplate="%{text}<extra>%{fullData.name}</extra>"),
                          row=row, col=1)

    fig.update_yaxes(title_text="水位 (mm)", row=1, col=1)
    fig.update_xaxes(showgrid=True, gridcolor=_C["grid"])

    # 各子圖的圖例／色塊說明放在該子圖標題同一行的右側（paper 座標由 y 軸 domain 換算）
    def _top(axis):            # 子圖上緣（子圖標題的底線）
        return fig.layout[axis].domain[1]
    legend_common = dict(orientation="h", xanchor="right", x=1, yanchor="bottom",
                         traceorder="normal", font=dict(size=13), bgcolor="rgba(0,0,0,0)")
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=_C["plot"], plot_bgcolor=_C["plot"],
        font=dict(family=_FONT, size=14, color=_C["text"]),
        hovermode="x unified", dragmode="zoom",
        height=plot_px + TOP_PX + 40,
        margin=dict(l=230, r=30, t=TOP_PX, b=40),
        title=dict(text=title, x=0.008, xref="container", xanchor="left",
                   y=1 - 10 / (plot_px + TOP_PX + 40), yref="container", yanchor="top",
                   font=dict(size=16)),
        legend=dict(**legend_common, y=_top("yaxis") - 5 / plot_px),   # 圖例框有內距，往下 5px 才和標題同一行
        uirevision=title,
    )
    if has_est:
        fig.update_layout(legend2=dict(**legend_common, y=_top("yaxis3") - 5 / plot_px))
    for a in fig.layout.annotations:          # 子圖標題靠左
        if a.text in titles:
            a.update(x=0, xanchor="left", font=dict(size=14))
    fig.add_annotation(text=_strip_key(), xref="paper", x=1, xanchor="right",
                       yref="paper", y=_top("yaxis2"), yanchor="bottom",
                       showarrow=False, font=dict(size=13, color=_C["text"]))
    return fig
