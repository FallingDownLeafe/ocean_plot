"""
build_buoy_figure.py
======================
浮球/浮標六張資料表（wave1/wind/curr/wave/pres1/stemp1）的純函式繪圖器。

移植自 buoy_qc_dash.py 的 build_wave1_figure / build_wind_figure /
build_curr_figure / build_wave_figure / build_pres1_figure /
build_stemp1_figure（含 _empty_fig / _cd 兩個內部工具函式），供 Plan B
（BUOY_QC.md §10）整合進 dash_app.py 的「tab-buoy」頁籤使用。

與 buoy_qc_dash.py 原版的差異
------------------------------
無。這六個函式在原檔案裡本來就是純函式（輸入 DataFrame，輸出 go.Figure，
不含任何 callback/Dash 依賴），逐字搬移，僅去除其餘 Dash layout/callback
程式碼。色彩、字體、_LAYOUT_BASE 等常數各自保留一份，不與
build_water_figure.py / build_diagnostic_figure.py 共用同一份 style 模組
——這與現有 build_diagnostic_figure.py 的做法一致（各圖表模組各自維護
一份色彩定義，避免跨模組耦合；異動色系請對照 SYSTEM.md §7／
BUOY_QC.md §7 手動同步）。

各函式對應的資料表欄位（詳見 BUOY_QC.md §3）
------------------------------------------------
- build_wave1_figure(df)      : TIME, H3, HMAX, T3, TMEAN, QC（上：波高；下：週期）
- build_wind_figure(df)       : TIME, VM, VG, DM, QC（DM 目前未繪製）
- build_curr_figure(df)       : TIME, V, D, QC（D 目前未繪製）
- build_wave_figure(df)       : TIME, H, HMAX, T, TMEAN, QC（上：波高；下：週期）
- wave1／wave 兩個函式可另傳 marks（buoy_checks.wave_checks() 的結果）畫自動檢查標記
- build_pres1_figure(df)      : TIME, P, QC（_expand_hr 展開後的長格式）
- build_stemp1_figure(df, z)  : TIME, T, QC（_expand_hr 展開後的長格式）；
                                 z=-3 時視為氣溫，其餘（0/1/None）視為海溫

df 為 None 或空 DataFrame 時，六個函式一律回傳帶提示文字的空白深色圖，
不拋例外——與 build_diagnostic_figure.py 對 bundles 為空的處理原則一致。
"""

import pandas as pd
import plotly.graph_objects as go
import line_style as LW   # 全系統折線粗細（2026-11-20 統一）
from plotly.subplots import make_subplots

# ── 樣式常數（與 buoy_qc_dash.py 原版一致，獨立維護一份）───────────────────
_C = {
    "bg":     "#111820",
    "plot":   "#1E1E1E",
    "panel":  "#1E2A3A",
    "header": "#1A3A5C",
    "border": "rgba(200,214,229,0.25)",
    "text":   "#CCD0D4",
}
_FONT = "標楷體, PingFang TC, Noto Sans CJK TC, Arial, sans-serif"
_MONO = "標楷體, Courier New, Consolas, monospace"

_LAYOUT_BASE = dict(
    template="plotly_dark",
    paper_bgcolor=_C["plot"], plot_bgcolor=_C["plot"],
    font=dict(family=_FONT, size=16, color=_C["text"]),
    hovermode="x unified",
    height=520,
    margin=dict(l=60, r=40, t=40, b=40),
    dragmode="select",
)


def _empty_fig(msg: str = "無資料") -> go.Figure:
    return go.Figure(layout=dict(
        **_LAYOUT_BASE,
        annotations=[dict(
            text=msg, showarrow=False,
            font=dict(size=16, color=_C["text"]),
            xref="paper", yref="paper", x=0.5, y=0.5,
        )],
    ))


def _cd(df, col="QC"):
    """將 df[col] 轉為 customdata 所需的 (N,1) 陣列。"""
    return df[col].values.reshape(-1, 1)

def _to_unit(df, cols, factor=0.1):
    """資料庫原始整數 → 物理單位（2026-11-20）。
    wind VM/VG 0.1 m/s、curr V mm/s（→ cm/s）、pres1/pres6 0.1 hPa、stemp1/stemp6 0.1 °C
    （欄位註解見 .104 information_schema）。原本直接畫原始值、縱軸卻標 m/s、hPa、°C。
    只改畫圖用的副本；框選 SQL 只用時間範圍，不受影響。"""
    d = df.copy()
    for c in cols:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce") * factor
    return d


def _non_q_mask(df, col="QC"):
    """回傳 df 中 QC 不為 'Q' 的布林遮罩（大小寫不分）。
    df 沒有該欄位時回傳 None，呼叫端據此跳過紅叉繪製，不影響無 QC
    資料表的畫法。目前浮標六表＋潮位站環境四表皆已確認有 qc 欄位，
    這個分支保留作為未來新增資料表時的防呆。
    """
    if col not in df.columns:
        return None
    return df[col].fillna("").astype(str).str.upper() != "Q"

# ── 波浪圖共用工具（2026-11-19 新增）────────────────────────────────────
# 背景：hovermode="x unified" 下，某條線在游標時間沒有值（NaN）時，Plotly 不會
# 顯示「空」，而是改抓該線離游標最近、有值的點，且不註明那是別的時間。
# 例：成功浮球 2026-10-23 08 時 HMAX 為空，游標停在 08 時卻顯示 07 時的 1608 cm。
# 解法：
#   1. 補齊整點（_hourly_grid）：整小時沒有任何資料列就補一列空值 → 線在缺列處斷開。
#   2. 游標資訊改由「每格都有點」的透明載體 trace 提供（_row_hover_trace），
#      一次列出同一時間那一列的所有欄位，空值寫「無值」；其他 trace 不參與 hover。

def _hourly_grid(df, num_cols):
    """把 df 補成每小時至少一列。

    - 只在「該整點小時內完全沒有資料列」時補一列（TIME＝整點、數值全空、
      _MISSING=True），原有非整點列（2010～2014 浮球約 30 分一筆）保留不動。
    - num_cols 轉成數值（mysql-connector 回傳 Decimal），無法轉的變 NaN。
    """
    d = df.copy()
    d["TIME"] = pd.to_datetime(d["TIME"])
    for c in num_cols:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d["_MISSING"] = False
    if d.empty:
        return d
    have = pd.DatetimeIndex(d["TIME"].dt.floor("h").unique())
    grid = pd.date_range(have.min(), have.max(), freq="h")
    miss = grid.difference(have)
    if len(miss):
        pad = pd.DataFrame({"TIME": miss, "_MISSING": True})
        d = pd.concat([d, pad], ignore_index=True)
        d = d.sort_values("TIME", kind="stable").reset_index(drop=True)
    return d


def _fmt_val(v, unit, scale=1.0, nd=0):
    if v is None or pd.isna(v):
        return "無值"
    return f"{float(v) * scale:.{nd}f} {unit}"


def _row_hover_text(d, items, notes=None):
    """每列一段游標文字。items: [(標籤, 欄名, 單位, 倍率, 小數位)]。
    notes: {TIME: [說明, ...]}，自動檢查標到的時間在最後加列（2026-11-20）。"""
    notes = notes or {}
    out = []
    for _, r in d.iterrows():
        if r["_MISSING"]:
            out.append("<br>此小時無資料列")
            continue
        lines = [f"{lab}：{_fmt_val(r.get(col), unit, sc, nd)}"
                 for lab, col, unit, sc, nd in items]
        qc = r.get("QC")
        lines.append(f"qc（整列）：{'無值' if qc is None or pd.isna(qc) else qc}")
        lines += notes.get(r["TIME"], [])
        out.append("<br>" + "<br>".join(lines))   # 開頭換行：第一項不和名稱擠同一行
    return out


def _row_hover_trace(d, text):
    """透明載體：每個時間點都有一個 y=0 的隱形點，hover 只看它。"""
    return go.Scatter(
        x=d["TIME"], y=[0] * len(d), text=text,
        name="本時資料", mode="markers",
        marker=dict(size=6, opacity=0),
        showlegend=False,
        meta="row_hover",        # hover_rows.apply_row_hover 看到這個就跳過該子圖
        hovertemplate="%{text}<extra>%{fullData.name}</extra>",
    )


# 自動檢查標記樣式（2026-11-20）：和既有「紅叉＝資料庫 qc 非 Q」用形狀區分，
# 空心圈＝不通過（4）、空心菱形＝可疑（3）。顏色沿用多采旗標的狀態色。
_MARK_STYLE = {
    4: dict(symbol="circle-open",  color="#ff5a5a", tag="不通過"),
    3: dict(symbol="diamond-open", color="#fab219", tag="可疑"),
}


def _add_check_marks(fig, marks):
    """把 buoy_checks.wave_checks() 的標記畫上去（波高層 row 1、週期層 row 2）。"""
    from buoy_checks import CHECKS
    for code, (name, level, _) in CHECKS.items():
        m = marks[marks["CHECK"] == code]
        if m.empty:
            continue
        st = _MARK_STYLE[level]
        row = 1 if m["LAYER"].iloc[0] == "h" else 2
        fig.add_trace(go.Scatter(
            x=m["TIME"], y=m["Y"], mode="markers",
            name=f"自動檢查：{name}（{st['tag']}）",
            legendgroup="autocheck",
            marker=dict(symbol=st["symbol"], size=16, color=st["color"],
                        line=dict(width=2.5, color=st["color"])),
            hoverinfo="skip",
        ), row=row, col=1)


def _wave_figure(df, h_items, t_items, hover_items, bad_col, bad_name, marks=None):
    """波浪兩層圖：上層波高、下層週期，共用時間軸。

    h_items / t_items: [(欄名, 圖例名, 顏色, 線型, 線寬, 有無點)]
    marks: buoy_checks.wave_checks() 的結果；None 或空表時不畫（勾選框關閉時傳 None）。
    """
    num_cols = [c for c, *_ in h_items + t_items]
    d = _hourly_grid(df, num_cols)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.05, row_heights=[0.62, 0.38])
    fig.update_layout(**_LAYOUT_BASE)
    fig.update_layout(height=720, showlegend=True)
    fig.update_yaxes(title_text="波高 (cm)", rangemode="tozero", row=1, col=1)
    fig.update_yaxes(title_text="週期 (s)", rangemode="tozero", row=2, col=1)

    def _line(col, name, color, dash, width, markers, scale, row):
        if col not in d.columns:
            return
        fig.add_trace(go.Scatter(
            x=d["TIME"], y=d[col] * scale, name=name,
            mode="lines+markers" if markers else "lines",
            marker=dict(size=4), line=dict(color=color, width=width, dash=dash),
            hoverinfo="skip",
        ), row=row, col=1)

    for col, name, color, dash, width, mk in h_items:
        _line(col, name, color, dash, width, mk, 1.0, 1)
    for col, name, color, dash, width, mk in t_items:
        _line(col, name, color, dash, width, mk, 0.1, 2)   # 0.1 s → s

    # 非 Q 資料紅叉（只看原有資料列，補出來的空列不算）
    _bad = _non_q_mask(d)
    if _bad is not None:
        _bad = _bad & ~d["_MISSING"]
        if _bad.any():
            fig.add_trace(go.Scatter(
                x=d.loc[_bad, "TIME"], y=d.loc[_bad, bad_col],
                name=bad_name, mode="markers",
                marker=dict(symbol="x", color="red", size=8),
                hoverinfo="skip",
            ), row=1, col=1)

    notes = {}
    if marks is not None and not marks.empty:
        from buoy_checks import CHECKS
        _add_check_marks(fig, marks)
        for r in marks.itertuples():
            name, level, _ = CHECKS[r.CHECK]
            notes.setdefault(r.TIME, []).append(
                f"<b>⚠ 自動檢查・{name}（{_MARK_STYLE[level]['tag']}）</b>：{r.NOTE}")

    # 游標載體：上下兩層各一個，停在哪一層都看得到同一列的全部欄位
    text = _row_hover_text(d, hover_items, notes)
    fig.add_trace(_row_hover_trace(d, text), row=1, col=1)
    fig.add_trace(_row_hover_trace(d, text), row=2, col=1)
    return fig


def build_wave1_figure(df, marks=None) -> go.Figure:
    """浮球 wave1：上層 H3、HMAX；下層 T3、TMEAN。marks 見 _wave_figure。"""
    if df is None or df.empty:
        return _empty_fig("wave1：查無資料")
    return _wave_figure(
        df,
        h_items=[("H3",   "示性波高 H3",   "#1f77b4", "solid", LW.MAIN, True),
                 ("HMAX", "最大波高 HMAX", "#ff7f0e", "dash",  LW.SUB, False)],
        t_items=[("T3",    "示性週期 T3",    "#17becf", "solid", LW.MAIN, True),
                 ("TMEAN", "平均週期 TMEAN", "#bcbd22", "dash",  LW.SUB, False)],
        hover_items=[("示性波高 H3",    "H3",    "cm", 1.0, 0),
                     ("最大波高 HMAX",  "HMAX",  "cm", 1.0, 0),
                     ("示性週期 T3",    "T3",    "s",  0.1, 1),
                     ("平均週期 TMEAN", "TMEAN", "s",  0.1, 1)],
        bad_col="H3", bad_name="H3 非Q", marks=marks,
    )


def build_wind_figure(df) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("wind：查無資料")
    df = _to_unit(df, ["VM", "VG"])
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title="風速 (m/s)", showlegend=True))
    fig.add_trace(go.Scatter(
        x=df["TIME"], y=df["VM"], name="平均風速 VM",
        mode="lines+markers", marker=dict(size=4),
        line=dict(color="#9467bd", width=LW.MAIN),
        customdata=_cd(df),
        hovertemplate="VM: %{y:.1f} m/s  qc: %{customdata[0]}<extra>VM</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=df["TIME"], y=df["VG"], name="最大風速 VG",
        mode="lines", line=dict(color="#c5b0d5", width=LW.SUB, dash="dash"),
        customdata=_cd(df),
        hovertemplate="VG: %{y:.1f} m/s  qc: %{customdata[0]}<extra>VG</extra>",
        visible="legendonly",
    ))
    _bad = _non_q_mask(df)
    if _bad is not None and _bad.any():
        fig.add_trace(go.Scatter(
            x=df.loc[_bad, "TIME"], y=df.loc[_bad, "VM"],
            name="VM 非Q", mode="markers",
            marker=dict(symbol="x", color="red", size=8),
            customdata=_cd(df.loc[_bad]),
            hovertemplate="VM: %{y:.1f} m/s  qc: %{customdata[0]}<extra>非Q</extra>",
        ))
    return fig


def build_curr_figure(df) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("curr：查無資料")
    df = _to_unit(df, ["V"])
    # fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title="流速 (cm/s)"))
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title="流速 (cm/s)", showlegend=True)) # 治本的方法是在這些build裡面都強制顯示legend，先測試穩定後再統一加
    fig.add_trace(go.Scatter(
        x=df["TIME"], y=df["V"], name="流速 V",
        mode="lines+markers", marker=dict(size=4),
        line=dict(color="#17becf", width=LW.MAIN),
        customdata=_cd(df),
        hovertemplate="V: %{y:.1f} cm/s  qc: %{customdata[0]}<extra>V</extra>",
    ))
    _bad = _non_q_mask(df)
    if _bad is not None and _bad.any():
        fig.add_trace(go.Scatter(
            x=df.loc[_bad, "TIME"], y=df.loc[_bad, "V"],
            name="V 非Q", mode="markers",
            marker=dict(symbol="x", color="red", size=8),
            customdata=_cd(df.loc[_bad]),
            hovertemplate="V: %{y:.1f} cm/s  qc: %{customdata[0]}<extra>非Q</extra>",
        ))
    return fig


def build_wave_figure(df, marks=None) -> go.Figure:
    """浮標 wave：上層 H、HMAX；下層尖峰週期 T、平均週期 TMEAN。marks 見 _wave_figure。"""
    if df is None or df.empty:
        return _empty_fig("wave：查無資料")
    return _wave_figure(
        df,
        h_items=[("H",    "示性波高 H",    "#2ca02c", "solid", LW.MAIN, True),
                 ("HMAX", "最大波高 HMAX", "#ff7f0e", "dash",  LW.SUB, False)],
        t_items=[("T",     "尖峰週期 T",     "#17becf", "solid", LW.MAIN, True),
                 ("TMEAN", "平均週期 TMEAN", "#bcbd22", "dash",  LW.SUB, False)],
        hover_items=[("示性波高 H",     "H",     "cm", 1.0, 0),
                     ("最大波高 HMAX",  "HMAX",  "cm", 1.0, 0),
                     ("尖峰週期 T",     "T",     "s",  0.1, 1),
                     ("平均週期 TMEAN", "TMEAN", "s",  0.1, 1)],
        bad_col="H", bad_name="H 非Q", marks=marks,
    )


def build_pres1_figure(df) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("pres1：查無資料")
    df = _to_unit(df, ["P"])
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title="氣壓 (hPa)", showlegend=True))
    fig.add_trace(go.Scatter(
        x=df["TIME"], y=df["P"], name="氣壓 P",
        mode="lines+markers", marker=dict(size=4),
        line=dict(color="#8b4513", width=LW.MAIN),
        customdata=_cd(df),
        hovertemplate="P: %{y:.1f} hPa  qc: %{customdata[0]}<extra>P</extra>",
    ))
    # 使用者mermer：不知道如果其實有qc欄位但只是全部都qc=Q會怎樣？希望沒影響。
    # pres1 已確認有 qc 欄位（先前 fetch_pres1() 沒查詢到，導致全部資料
    # 被 _expand() 的防呆邏輯填成空字串、誤判為非Q，已在 data_engine.py
    # 的 fetch_pres1() 補上 qc AS QC 修正，詳見該處註解）。
    _bad = _non_q_mask(df)
    if _bad is not None and _bad.any():
        fig.add_trace(go.Scatter(
            x=df.loc[_bad, "TIME"], y=df.loc[_bad, "P"],
            name="P 非Q", mode="markers",
            marker=dict(symbol="x", color="red", size=8),
            customdata=_cd(df.loc[_bad]),
            hovertemplate="P: %{y:.1f} hPa  qc: %{customdata[0]}<extra>非Q</extra>",
        ))
    return fig


def build_stemp1_figure(df, z=None) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("stemp1：查無資料")
    df = _to_unit(df, ["T"])
    is_air = (z is not None and int(z) == -3)
    ylabel = "氣溫 (°C)" if is_air else "海溫 (°C)"
    tname  = "氣溫 T"    if is_air else "海溫 T"
    color  = "#ee7373"   if is_air else "#ff9896"
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title=ylabel, showlegend=True))
    fig.add_trace(go.Scatter(
        x=df["TIME"], y=df["T"], name=tname,
        mode="lines+markers", marker=dict(size=4),
        line=dict(color=color, width=LW.MAIN),
        customdata=_cd(df),
        hovertemplate=f"T: %{{y:.1f}} °C  qc: %{{customdata[0]}}<extra>{tname}</extra>",
    ))
    _bad = _non_q_mask(df)
    if _bad is not None and _bad.any():
        fig.add_trace(go.Scatter(
            x=df.loc[_bad, "TIME"], y=df.loc[_bad, "T"],
            name=f"{tname} 非Q", mode="markers",
            marker=dict(symbol="x", color="red", size=8),
            customdata=_cd(df.loc[_bad]),
            hovertemplate=f"T: %{{y:.1f}} °C  qc: %{{customdata[0]}}<extra>非Q</extra>",
        ))
    return fig
