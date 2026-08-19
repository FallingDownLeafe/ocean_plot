"""
buoy_qc_dash.py — 浮球/浮標品管工具：Dash UI + Callbacks + SQL Builder
由 buoy_qc_app.py 啟動；ENGINE 在 main() 中注入，callbacks 觸發前已就緒。

支援資料表（各自一個 Tab）：
  wave1 — 浮球波浪（完整實作）
  wind  — 風速
  curr  — 海流
  wave  — 浮標波浪

SQL 產生邏輯：
  所有表的 qc 均在 PK 中，UPDATE 必須在 WHERE 加 AND qc = '{old_qc}'。
  old_qc 從 Box Select 選中點的 customdata 自動偵測（取眾數）。
"""

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html, no_update
from dash.exceptions import PreventUpdate

# ── 由 buoy_qc_app.py 注入 ────────────────────────────────────
ENGINE = None   # type: ignore

# ── 樣式常數（與主系統 §6 一致）─────────────────────────────────
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

_TABS = [
    {"value": "wave1",  "label": "浮球波浪表 wave1"},
    {"value": "wind",   "label": "風速 wind"},
    {"value": "curr",   "label": "海流 curr"},
    {"value": "wave",   "label": "浮標波浪表 wave"},
    {"value": "pres1",  "label": "氣壓 pres1"},
    {"value": "stemp1", "label": "溫度 stemp1"},
]
_TABS_WITH_Z = {"wind", "curr", "stemp1"}
_HR_TABLES   = {"pres1", "stemp1"}   # DATATIME+HR0~HR23 寬格式表

# ── Dash App ──────────────────────────────────────────────────
app = Dash(__name__, title="浮球浮標品管", suppress_callback_exceptions=True)


# ── Layout 輔助 ───────────────────────────────────────────────
def _label(text):
    return html.Div(text, style={
        "color": _C["text"], "fontSize": "18px", "marginBottom": "3px",
    })


def _section(title, *children):
    return html.Div([
        html.Div(title, style={
            "color": _C["text"], "fontWeight": "bold", "fontSize": "14px",
            "borderBottom": f"1px solid {_C['border']}",
            "paddingBottom": "4px", "marginBottom": "8px",
        }),
        *children,
    ], style={"marginBottom": "16px"})


# ── Layout ────────────────────────────────────────────────────
app.layout = html.Div([

    # 隱藏控制元件
    dcc.Interval(id="init-trigger", interval=300, max_intervals=1),
    dcc.Store(id="active-tab-store", data="wave1"),

    # Header
    html.Div("🌊 浮球 / 浮標品管工具", style={
        "background": _C["header"], "color": _C["text"], "fontFamily": _FONT,
        "padding": "10px 20px", "fontSize": "24px", "letterSpacing": "1px",
    }),

    # 主體
    html.Div([

        # ── 左：圖表區 ────────────────────────────────────────
        html.Div([

            # 頂部控制列
            html.Div([
                html.Div([
                    _label("測站"),
                    dcc.Dropdown(id="station-dropdown", options=[], placeholder="選擇測站",
                                 style={"minWidth": "200px", "fontSize": "16px"}),
                ], style={"marginRight": "14px"}),

                html.Div([
                    _label("日期範圍"),
                    dcc.DatePickerRange(id="date-range", display_format="YYYY-MM-DD",
                                        style={"fontSize": "16px"}),
                ], style={"marginRight": "12px"}),

                html.Div([
                    _label("Z 值"),
                    dcc.RadioItems(id="z-selector", options=[], value=None,
                                   inline=True,
                                   style={"color": _C["text"], "fontSize": "16px"}),
                ], id="z-selector-wrap", style={"marginRight": "12px", "display": "none"}),

                html.Button("查詢", id="query-btn", n_clicks=0, style={
                    "background": _C["header"], "color": _C["text"],
                    "border": f"1px solid {_C['border']}",
                    "padding": "5px 18px", "cursor": "pointer",
                    "fontSize": "16px", "alignSelf": "flex-end", "marginBottom": "1px",
                }),
            ], style={
                "display": "flex", "alignItems": "flex-end",
                "padding": "8px 12px", "background": _C["panel"],
                "borderBottom": f"1px solid {_C['border']}",
                "flexWrap": "wrap", "gap": "4px",
            }),

            # Tab 切換
            dcc.Tabs(id="main-tabs", value="wave1",
                     style={"background": _C["bg"]},
                     children=[
                         dcc.Tab(label=t["label"], value=t["value"],
                                 style={"background": _C["bg"], "color": _C["text"],
                                        "fontSize": "16px", "padding": "6px 12px"},
                                 selected_style={"background": _C["header"], "color": "#FFFFFF",
                                                 "fontSize": "16px", "padding": "6px 12px"})
                         for t in _TABS
                     ]),

            # 圖表
            dcc.Graph(
                id="main-graph",
                figure=go.Figure(layout=dict(
                    template="plotly_dark",
                    paper_bgcolor=_C["plot"], plot_bgcolor=_C["plot"],
                    height=520,
                    font=dict(family=_FONT, color=_C["text"]),
                    annotations=[dict(
                        text="請選擇測站與日期範圍，按查詢", showarrow=False,
                        font=dict(size=16, color=_C["text"]),
                        xref="paper", yref="paper", x=0.5, y=0.5,
                    )],
                )),
                config={
                    "displayModeBar": True,
                    "modeBarButtonsToRemove": ["lasso2d"],
                    "scrollZoom": True,
                },
            ),

            # 狀態列
            html.Div(id="status-bar", style={
                "color": _C["text"], "fontSize": "16px",
                "padding": "4px 12px", "background": _C["bg"],
                "borderTop": f"1px solid {_C['border']}",
            }),

        ], style={
            "flex": "3", "minWidth": 0, "display": "flex", "flexDirection": "column",
            "background": _C["bg"], "borderRight": f"1px solid {_C['border']}",
        }),

        # ── 右：QC 面板 ───────────────────────────────────────
        html.Div([

            _section("§A 資料表",
                html.Div(id="active-table-display",
                         style={"color": "#7EB6E8", "fontFamily": _MONO, "fontSize": "16px"}),
            ),

            _section("§B QC 設定",
                _label("偵測到的舊 QC 值（來自框選資料）"),
                html.Div(id="old-qc-display", style={
                    "color": "#F9C74F", "fontFamily": _MONO,
                    "fontSize": "16px", "marginBottom": "10px", "minHeight": "22px",
                }),
                _label("新 QC 值"),
                dcc.Input(id="new-qc-input", value="R", maxLength=2,
                          debounce=False,
                          style={
                              "background": _C["plot"], "color": _C["text"],
                              "border": f"1px solid {_C['border']}",
                              "width": "64px", "fontFamily": _MONO,
                              "fontSize": "16px", "padding": "3px 6px",
                          }),
            ),

            _section("§C SQL 輸出",
                dcc.Textarea(id="sql-output", value="",
                             style={
                                 "width": "100%", "height": "230px",
                                 "resize": "vertical",
                                 "background": _C["plot"], "color": "#A8D8A8",
                                 "fontFamily": _MONO, "fontSize": "16px",
                                 "border": f"1px solid {_C['border']}",
                                 "boxSizing": "border-box",
                             }),
                dcc.Clipboard(target_id="sql-output", title="複製 SQL",
                              style={
                                  "background": _C["header"], "color": _C["text"],
                                  "border": f"1px solid {_C['border']}",
                                  "padding": "4px 14px", "cursor": "pointer",
                                  "fontSize": "16px", "marginTop": "6px",
                                  "display": "inline-block",
                              }),
            ),

        ], style={
            "flex": "1", "minWidth": "220px", "maxWidth": "280px",
            "background": _C["panel"], "padding": "16px",
            "display": "flex", "flexDirection": "column",
            "fontFamily": _FONT, "overflowY": "auto",
        }),

    ], style={
        "display": "flex", "height": "calc(100vh - 42px)",
        "overflow": "hidden", "background": _C["bg"],
    }),

], style={"background": _C["bg"], "minHeight": "100vh", "fontFamily": _FONT})


# ── SQL Builder ───────────────────────────────────────────────
def _clean_ts(ts: str) -> str:
    """Plotly 時間戳 → MySQL 格式（去 T 與毫秒）。"""
    return ts.replace("T", " ").split(".")[0]


def _extract_sel(pts: list) -> tuple[list, str]:
    """從 selectedData points 取出排序後的時間清單與 old_qc（取眾數）。"""
    times = sorted({_clean_ts(p["x"]) for p in pts if "x" in p})
    qc_list = []
    for p in pts:
        cd = p.get("customdata")
        if cd is None:
            continue
        qc_list.append(cd[0] if isinstance(cd, (list, tuple)) else cd)
    old_qc = max(set(qc_list), key=qc_list.count) if qc_list else "Q"
    return times, old_qc

def _build_sql_hr(table: str, stid: str, new_qc: str,
                  times: list, old_qc: str, extra_where: str = "") -> str:
    """pres1 / stemp1 專用：逐時 Time → DATATIME IN 子句。"""
    datatimes = sorted({ts[:10] + " 00:00:00" for ts in times})
    dt_list = ", ".join(f"'{dt}'" for dt in datatimes)
    extra = f"\n  AND {extra_where}" if extra_where else ""
    return (
        f"-- {table}  {len(datatimes)} 個 DATATIME  "
        f"舊qc='{old_qc}' → '{new_qc}'\n"
        f"UPDATE {table}\n"
        f"SET qc = '{new_qc}'\n"
        f"WHERE STID = '{stid}'{extra}\n"
        f"  AND DATATIME IN ({dt_list})\n"
        f"  AND qc = '{old_qc}';"
    )

def _build_sql(table: str, stid: str, new_qc: str,
               times: list, old_qc: str, extra_where: str = "") -> str:
    t1, t2 = times[0], times[-1]
    extra = f"\n  AND {extra_where}" if extra_where else ""
    return (
        f"-- {table}  {len(times)} 筆時間點  "
        f"舊qc='{old_qc}' → '{new_qc}'\n"
        f"UPDATE {table}\n"
        f"SET qc = '{new_qc}'\n"
        f"WHERE STID = '{stid}'{extra}\n"
        f"  AND TIME BETWEEN '{t1}' AND '{t2}'\n"
        f"  AND qc = '{old_qc}';"
    )

def build_sql(tab: str, selected_data: dict, stid: str, new_qc: str, z=None):
    """依 tab 選擇對應的 UPDATE SQL，回傳 (sql_str, old_qc_str)。"""
    pts = (selected_data or {}).get("points", [])
    if not pts:
        return "", ""
    times, old_qc = _extract_sel(pts)
    extra = f"Z = {int(z)}" if z is not None and tab in _TABS_WITH_Z else ""
    if tab in _HR_TABLES:
        sql = _build_sql_hr(tab, stid, new_qc, times, old_qc, extra)
    else:
        sql = _build_sql(tab, stid, new_qc, times, old_qc, extra)
    return sql, old_qc


# ── Figure Builder ────────────────────────────────────────────
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


def build_wave1_figure(df) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("wave1：查無資料")
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title="波高 (cm)"))
    # 示性波高 H3（主 trace）
    fig.add_trace(go.Scattergl(
        x=df["TIME"], y=df["H3"], name="示性波高 H3",
        mode="lines+markers", marker=dict(size=4),
        line=dict(color="#1f77b4", width=1.5),
        customdata=_cd(df),
        hovertemplate="H3: %{y} cm  qc: %{customdata[0]}<extra>H3</extra>",
    ))
    # 最大波高 HMAX（次要 trace，預設隱藏）
    fig.add_trace(go.Scattergl(
        x=df["TIME"], y=df["HMAX"], name="最大波高 HMAX",
        mode="lines", line=dict(color="#ff7f0e", width=1, dash="dash"),
        customdata=_cd(df),
        hovertemplate="HMAX: %{y} cm  qc: %{customdata[0]}<extra>HMAX</extra>",
        visible="legendonly",
    ))
    return fig


def build_wind_figure(df) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("wind：查無資料")
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title="風速 (m/s)"))
    fig.add_trace(go.Scattergl(
        x=df["TIME"], y=df["VM"], name="平均風速 VM",
        mode="lines+markers", marker=dict(size=4),
        line=dict(color="#9467bd", width=1.5),
        customdata=_cd(df),
        hovertemplate="VM: %{y:.1f} m/s  qc: %{customdata[0]}<extra>VM</extra>",
    ))
    fig.add_trace(go.Scattergl(
        x=df["TIME"], y=df["VG"], name="最大風速 VG",
        mode="lines", line=dict(color="#c5b0d5", width=1, dash="dash"),
        customdata=_cd(df),
        hovertemplate="VG: %{y:.1f} m/s  qc: %{customdata[0]}<extra>VG</extra>",
        visible="legendonly",
    ))
    return fig


def build_curr_figure(df) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("curr：查無資料")
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title="流速 (cm/s)"))
    fig.add_trace(go.Scattergl(
        x=df["TIME"], y=df["V"], name="流速 V",
        mode="lines+markers", marker=dict(size=4),
        line=dict(color="#17becf", width=1.5),
        customdata=_cd(df),
        hovertemplate="V: %{y:.1f} cm/s  qc: %{customdata[0]}<extra>V</extra>",
    ))
    return fig


def build_wave_figure(df) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("wave：查無資料")
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title="示性波高 (cm)"))
    fig.add_trace(go.Scattergl(
        x=df["TIME"], y=df["H"], name="示性波高 H",
        mode="lines+markers", marker=dict(size=4),
        line=dict(color="#2ca02c", width=1.5),
        customdata=_cd(df),
        hovertemplate="H: %{y} cm  qc: %{customdata[0]}<extra>H</extra>",
    ))
    return fig

def build_pres1_figure(df) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("pres1：查無資料")
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title="氣壓 (hPa)"))
    fig.add_trace(go.Scattergl(
        x=df["TIME"], y=df["P"], name="氣壓 P",
        mode="lines+markers", marker=dict(size=4),
        line=dict(color="#8b4513", width=1.5),
        customdata=_cd(df),
        hovertemplate="P: %{y:.1f} hPa  qc: %{customdata[0]}<extra>P</extra>",
    ))
    return fig


def build_stemp1_figure(df, z=None) -> go.Figure:
    if df is None or df.empty:
        return _empty_fig("stemp1：查無資料")
    is_air = (z is not None and int(z) == -3)
    ylabel = "氣溫 (°C)" if is_air else "海溫 (°C)"
    tname  = "氣溫 T"    if is_air else "海溫 T"
    color  = "#ee7373"   if is_air else "#ff9896"
    fig = go.Figure(layout=dict(**_LAYOUT_BASE, yaxis_title=ylabel))
    fig.add_trace(go.Scattergl(
        x=df["TIME"], y=df["T"], name=tname,
        mode="lines+markers", marker=dict(size=4),
        line=dict(color=color, width=1.5),
        customdata=_cd(df),
        hovertemplate=f"T: %{{y:.1f}} °C  qc: %{{customdata[0]}}<extra>{tname}</extra>",
    ))
    return fig


_FIG_BUILDERS = {
    "wave1": build_wave1_figure,
    "wind":  build_wind_figure,
    "curr":  build_curr_figure,
    "wave":  build_wave_figure,
    # pres1 / stemp1 需傳入 z，在 render_figure 中直接呼叫，不走此 dict
}


# ── Callbacks ─────────────────────────────────────────────────
@app.callback(
    Output("station-dropdown", "options"),
    Output("station-dropdown", "value"),
    Input("init-trigger", "n_intervals"),
)
def populate_stations(_):
    """頁面載入後從 DB 取測站清單填入下拉選單。"""
    if ENGINE is None:
        return [], None
    df = ENGINE.load_stations()
    opts = [
        {"label": f"[k{row.KIND}] {row.STID}  {row.STNAC}", "value": row.STID}
        for row in df.itertuples()
    ]
    return opts, (opts[0]["value"] if opts else None)


@app.callback(
    Output("active-tab-store", "data"),
    Output("z-selector-wrap", "style"),
    Output("active-table-display", "children"),
    Input("main-tabs", "value"),
)
def on_tab_change(tab):
    """Tab 切換：更新 store、顯示/隱藏 Z 選擇器、更新資料表名稱顯示。"""
    needs_z = tab in _TABS_WITH_Z
    z_style = ({"marginRight": "12px"} if needs_z
               else {"marginRight": "12px", "display": "none"})
    return tab, z_style, tab


@app.callback(
    Output("z-selector", "options"),
    Output("z-selector", "value"),
    Input("station-dropdown", "value"),
    Input("active-tab-store", "data"),
    prevent_initial_call=True,
)
def update_z_options(stid, tab):
    """測站或 Tab 變更時動態查詢可用 Z 值。"""
    if ENGINE is None or not stid or tab not in _TABS_WITH_Z:
        return [], None
    z_list = ENGINE.get_z_options(tab, stid)
    _S1 = {-3: "Z=-3（氣溫）", 0: "Z=0（海溫計1）", 1: "Z=1（海溫計2）"}
    if tab == "stemp1":
        opts = [{"label": _S1.get(z, f"Z={z}"), "value": z} for z in z_list]
    else:
        opts = [{"label": f"Z={z}", "value": z} for z in z_list]
    return opts, (z_list[0] if z_list else None)


@app.callback(
    Output("main-graph", "figure"),
    Output("status-bar", "children"),
    Input("query-btn", "n_clicks"),
    State("station-dropdown", "value"),
    State("date-range", "start_date"),
    State("date-range", "end_date"),
    State("active-tab-store", "data"),
    State("z-selector", "value"),
    prevent_initial_call=True,
)
def render_figure(_, stid, start, end, tab, z):
    """查詢按鈕觸發：依 Tab 呼叫對應的資料查詢與繪圖函式。"""
    if not stid or not start or not end:
        return _empty_fig("請選擇測站與日期範圍"), "⚠ 輸入不完整"
    if tab in _TABS_WITH_Z and z is None:
        return _empty_fig(f"{tab}：請先選擇 Z 值"), "⚠ 請選擇 Z 值"
    try:
        start_s = str(start)[:10]
        end_s   = str(end)[:10]
        if tab == "wave1":
            df = ENGINE.fetch_wave1(stid, start_s, end_s)
            fig = build_wave1_figure(df)
        elif tab == "wind":
            df = ENGINE.fetch_wind(stid, start_s, end_s, int(z))
            fig = build_wind_figure(df)
        elif tab == "curr":
            df = ENGINE.fetch_curr(stid, start_s, end_s, int(z))
            fig = build_curr_figure(df)
        elif tab == "pres1":
            df = ENGINE.fetch_pres1(stid, start_s, end_s)
            fig = build_pres1_figure(df)
        elif tab == "stemp1":
            df = ENGINE.fetch_stemp1(stid, start_s, end_s, int(z))
            fig = build_stemp1_figure(df, z)
        else:   # wave
            df = ENGINE.fetch_wave(stid, start_s, end_s)
            fig = build_wave_figure(df)
        n = len(df) if df is not None else 0
        status = f"✅  {stid}  {tab}  {start_s} ～ {end_s}  共 {n} 筆"
        return fig, status
    except Exception as exc:
        return _empty_fig(f"查詢失敗：{exc}"), f"❌ {exc}"


@app.callback(
    Output("sql-output", "value"),
    Output("old-qc-display", "children"),
    Input("main-graph", "selectedData"),
    State("station-dropdown", "value"),
    State("new-qc-input", "value"),
    State("active-tab-store", "data"),
    State("z-selector", "value"),
    prevent_initial_call=True,
)
def on_selection(selected_data, stid, new_qc, tab, z):
    """Box Select 框選後自動產生 UPDATE SQL。"""
    if not selected_data or not stid or not new_qc:
        raise PreventUpdate
    sql, old_qc = build_sql(tab, selected_data, stid, new_qc, z)
    if not sql:
        raise PreventUpdate
    return sql, f"'{old_qc}'"
