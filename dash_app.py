"""
dash_app.py
===========
海洋動力診斷儀表板 — Dash 圖表 + QC 框選 + SQL 產生模組

取代原 plotly_qc_select.py 的：
    _SelectionHandler HTTP server
    _selection_queue / _poll_queue
    _build_js() JS 注入
    write_chart_html() → tempfile
    SqlDialog Tkinter Toplevel

整合說明：
    1. 獨立執行（開發）：python dash_app.py
    2. 整合 Tkinter：從 MainApp.__init__() 以 daemon thread 呼叫 app.run()，
       完成查詢後將 go.Figure 透過 shared_state.push_figure() 傳入，
       再 webbrowser.open("http://127.0.0.1:8050")
"""

import os
from collections import defaultdict

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output, State, no_update, Patch, ctx
from dash.exceptions import PreventUpdate
import dash_bridge
from build_water_figure import build_water_figure

# ── 環境設定 ──────────────────────────────────────────────────────────────────
DASH_PORT = int(os.getenv("DASH_PORT", "8050"))
DASH_HOST = "127.0.0.1"
MIN_COLS  = [f"MIN{i}" for i in range(10)]


# ══════════════════════════════════════════════════════════════════════════════
# § 1  SQL 工具函式
#
#   從 plotly_qc_select.py 搬移；移除所有 Tkinter 依賴，可獨立 import。
#   整合完成後可改為：
#       from plotly_qc_select import build_mode1_sql, build_mode2_sql_by_time
# ══════════════════════════════════════════════════════════════════════════════

def _clean_ts(ts) -> str:
    """把 Plotly 時間戳 'YYYY-MM-DDTHH:MM:SS.mmm' 轉為 MySQL 格式字串。"""
    if ts is None:
        return ""
    return str(ts).split(".")[0].replace("T", " ")


def build_mode1_sql(sel: dict, stid: str, new_qc: int) -> str:
    """
    Mode 1 — UPDATE QC 旗標。
    條件：DATATIME 在框選時間範圍內，且任一 MIN 欄位落在 y 範圍內。

    sel 格式（與原版 plotly_qc_select.py 相同）：
        x_start, x_end  : 框選時間範圍
        y_start, y_end  : 框選水位 y 範圍
    """
    t1 = _clean_ts(sel.get("x_start"))
    t2 = _clean_ts(sel.get("x_end"))
    v1 = sel.get("y_start")
    v2 = sel.get("y_end")

    lines = [
        "UPDATE tide6",
        f"SET    QC = {new_qc}",
        f"WHERE  STID     = '{stid}'",
        f"  AND  DATATIME BETWEEN '{t1}' AND '{t2}'",
    ]
    if v1 is not None and v2 is not None:
        lo, hi = sorted([float(v1), float(v2)])
        conds = "\n       OR  ".join(
            f"{c} BETWEEN {lo:.6f} AND {hi:.6f}" for c in MIN_COLS
        )
        lines.append(f"  AND  (   {conds}")
        lines.append( "       )")
    lines.append(";")
    return "\n".join(lines)


def build_mode2_sql_by_time(sel: dict, stid: str,
                             operator: str, operand: float) -> str:
    """
    Mode 2 — 從框選點的 x（展開後的 Time）反推 DATATIME 與 MIN 欄位，
    產生 MIN 值四則運算 UPDATE。不依賴 traceName。

    反推邏輯（tide6 整點存一列，MIN0~MIN9 各差 6 分鐘）：
        Time = DATATIME + N×6min
        N    = Time.minute // 6
        DATATIME = Time 截到整點
    """
    points  = sel.get("points", [])
    grouped: dict[str, set] = defaultdict(set)

    for p in points:
        ts_str = _clean_ts(p.get("x"))
        if not ts_str:
            continue
        try:
            ts       = pd.to_datetime(ts_str)
            n        = ts.minute // 6
            datatime = ts.replace(minute=0, second=0, microsecond=0)
            grouped[datatime.strftime("%Y-%m-%d %H:%M:%S")].add(f"MIN{n}")
        except Exception:
            continue

    if not grouped:
        return (
            "-- ⚠️  無法從選中的點反推 MIN 欄位\n"
            "-- 請確認 x 軸為 tide6 展開後的 6min 時間格式"
        )

    sqls = []
    for datatime_str in sorted(grouped.keys()):
        cols       = sorted(grouped[datatime_str], key=lambda c: int(c[3:]))
        set_clause = ",\n       ".join(
            f"{c} = {c} {operator} {operand}" for c in cols
        )
        sqls.append(
            f"UPDATE tide6\n"
            f"SET    {set_clause}\n"
            f"WHERE  STID     = '{stid}'\n"
            f"  AND  DATATIME = '{datatime_str}';"
        )

    header = (
        f"-- Mode 2：{len(grouped)} 筆 DATATIME，"
        f"共 {sum(len(v) for v in grouped.values())} 個欄位\n"
        f"-- 運算：原值 {operator} {operand}\n\n"
    )
    return header + "\n\n".join(sqls)


def _adapt_selected_data(selected_data: dict) -> dict:
    """
    Dash dcc.Graph.selectedData → SQL builder 所需的 sel dict。

    Dash 原始格式（Box Select 模式）：
        {
          "points": [{"x": "...", "y": ..., "curveNumber": N, ...}],
          "range":  {"x": ["start", "end"], "y": [lo, hi]}
        }
    """
    rng   = selected_data.get("range") or {}
    x_rng = rng.get("x", [None, None])
    y_rng = rng.get("y", [None, None])

    return {
        "x_start":     x_rng[0] if len(x_rng) > 0 else None,
        "x_end":       x_rng[1] if len(x_rng) > 1 else None,
        "y_start":     y_rng[0] if len(y_rng) > 0 else None,
        "y_end":       y_rng[1] if len(y_rng) > 1 else None,
        "point_count": len(selected_data.get("points", [])),
        "points":      [{"x": p.get("x"), "y": p.get("y")}
                        for p in selected_data.get("points", [])],
    }


# ══════════════════════════════════════════════════════════════════════════════
# § 2  OceanDataEngine Stub
#
#   方法簽名與真實版本完全相同，均回傳空值。
#   整合時替換為：from ocean_plot import OceanDataEngine
# ══════════════════════════════════════════════════════════════════════════════

class OceanDataEngine:
    """Stub — 供骨架階段佔位；整合時直接替換 import。"""

    def __init__(self, password, host="", user="",
                 database="mrbank", tables=None):
        self.mapping_df = pd.DataFrame(columns=["STID", "QCID"])
        self.name_map   = {}

    def load_mapping(self): pass

    def fetch_years(self) -> list[str]:
        return []

    def fetch_typhoons(self, yr_full: str) -> pd.DataFrame:
        return pd.DataFrame()

    def expand_data(self, df, val_name, freq="6min") -> pd.DataFrame:
        return pd.DataFrame(columns=["Time", val_name])

    def fetch_tide_instruments(self, stid: str) -> pd.DataFrame:
        return pd.DataFrame()

    def query_multi_tide_data(self, tide_instruments_df, start, end,
                               start_str, end_str) -> dict:
        return {"wl_data": {}, "pred_data": {},
                "pred_data_a": {}, "tide_meta": {}}

    def fetch_bundle(self, stid: str, start, end) -> dict:
        return {
            "stid": stid, "stname": "（stub）",
            "df": pd.DataFrame(), "src_ids": {},
            "src_names": {}, "tide_meta": {},
            "mr_full": None, "mr_month": None,
        }


# ══════════════════════════════════════════════════════════════════════════════
# § 3  Demo Figure
#
#   仿真潮位資料，展示 QC 框選效果。
#   整合後由 draw_water_only(bundles) 或 draw_diagnostic(bundles) 取代。
# ══════════════════════════════════════════════════════════════════════════════

_DEMO_STID = "1176"


def make_demo_figure() -> go.Figure:
    """產生仿真潮位圖（半日潮 + 20 個異常點），供骨架展示用。"""
    rng = pd.date_range("2024-07-01", periods=1200, freq="6min")
    np.random.seed(42)

    # 半日潮 + 全日潮 分量
    t_sec = rng.astype(np.int64) / 1e9
    tide  = (120
             + 80 * np.sin(2 * np.pi * t_sec / 44712)   # M2 半日潮（12.42 h）
             + 40 * np.sin(2 * np.pi * t_sec / 86400)   # K1 全日潮
             + np.random.randn(1200) * 1.5)
    pred  = (120
             + 80 * np.sin(2 * np.pi * t_sec / 44712)
             + 40 * np.sin(2 * np.pi * t_sec / 86400))
    resi  = tide - pred

    # 植入 20 個異常點
    bad_idx = np.random.choice(len(rng), 20, replace=False)
    bad_x   = rng[bad_idx]
    bad_y   = (tide[bad_idx]
               + np.random.choice([-1, 1], 20)
               * np.random.uniform(30, 60, 20))

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=rng, y=tide,
        mode="lines",
        name=f"WL_{_DEMO_STID}（校正值）",
        line=dict(color="#1f77b4", width=1.5),
        hovertemplate="%{x|%Y-%m-%d %H:%M}<br>水位：%{y:.1f} cm<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=rng, y=pred,
        mode="lines",
        name=f"WL_{_DEMO_STID}_pred_h（調和預報）",
        line=dict(color="#2ca02c", width=1, dash="dot"),
        visible="legendonly",
        hovertemplate="%{x|%Y-%m-%d %H:%M}<br>預報：%{y:.1f} cm<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=rng, y=resi,
        mode="lines",
        name="Resi（暴潮偏差）",
        line=dict(color="#faafe4", width=1),
        yaxis="y2",
        hovertemplate="%{x|%Y-%m-%d %H:%M}<br>偏差：%{y:.1f} cm<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=bad_x, y=bad_y,
        mode="markers",
        name="異常原始值（QC≠Q）",
        marker=dict(color="red", symbol="x", size=9,
                    line=dict(width=2)),
        hovertemplate="%{x|%Y-%m-%d %H:%M}<br>原始值：%{y:.1f} cm<extra></extra>",
    ))

    fig.update_layout(
        title=dict(
            text=(f"測站 {_DEMO_STID} — 水位細節"
                  "　（以工具列 □ 框選異常區段 → 自動產生 UPDATE SQL）"),
            font=dict(size=13),
            x=0,
        ),
        xaxis=dict(
            title="Time",
            rangeslider=dict(visible=True, thickness=0.04),
        ),
        yaxis=dict(title="水位 (cm)", side="left"),
        yaxis2=dict(
            title="暴潮偏差 (cm)",
            overlaying="y", side="right",
            showgrid=False,
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=60, r=60, t=80, b=40),
        hovermode="x unified",
        dragmode="zoom",          # 預設 zoom，框選請點 □
        template="plotly_white",
        height=520,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# § 4  Dash App 初始化
# ══════════════════════════════════════════════════════════════════════════════

# app = Dash(
#     __name__,
#     title="海洋動力診斷儀表板",
#     suppress_callback_exceptions=True,
# )

# 明確指定路徑名稱修正：在建立 app 時明確指定 assets_folder
app = Dash(
    __name__,
    assets_folder=os.path.join(os.path.dirname(__file__), "assets"),
    title="海洋動力診斷儀表板",
    suppress_callback_exceptions=True,
)

# ── 共用樣式常數 ─────────────────────────────────────────────────────────────
_CLR_NAVY   = "#1a3a5c"
_CLR_PANEL  = "#f0f4f8"
# _CLR_BORDER = "#c8d6e5"
_CLR_BORDER = "rgba(200, 214, 229, 0.25)" # 半透明邊框，與 Dash 深色底更協調
# _FONT_MONO  = "Courier New, Consolas, monospace"
# _FONT_UI    = "Noto Sans TC, Segoe UI, Arial, sans-serif"
_FONT_MONO  = "標楷體, Courier New, Consolas, monospace" # 將標楷體加入等寬字體列表
_FONT_UI    = "標楷體, Noto Sans TC, Segoe UI, Arial, sans-serif" # 將標楷體加入 Dash UI 字體列表


# ══════════════════════════════════════════════════════════════════════════════
# § 5  Layout
# ══════════════════════════════════════════════════════════════════════════════
app.layout = html.Div(
    style={
        "fontFamily": _FONT_UI,
        "minHeight": "100vh",
        "backgroundColor": "#111820",
    },
    children=[

        # ── Store ─────────────────────────────────────────────────────────────
        # figure-store：整合 Tkinter 後，Interval 輪詢 shared_state 寫入此處
        dcc.Store(id="figure-store"),
        # stid-store：當前主測站代碼（Tkinter push_figure 時一併更新）
        dcc.Store(id="stid-store", data=None),
        dcc.Store(id="bundle-key-store", data=None),
        dcc.Interval(id="bundle-poll", interval=500, n_intervals=0),

        # ── 頂部 Header ───────────────────────────────────────────────────────
        html.Div(
            style={
                "backgroundColor": _CLR_NAVY,
                "color": "white",
                "padding": "11px 24px",
                "display": "flex",
                "alignItems": "center",
                "gap": "12px",
            },
            children=[
                html.Span("🌊", style={"fontSize": "20px"}),
                html.H1(
                    "海洋動力診斷儀表板",
                    style={"margin": 0, "fontSize": "17px", "fontWeight": 600},
                ),
                html.Span(
                    "QC 框選模式",
                    style={"marginLeft": "auto", "fontSize": "12px", "opacity": 0.65},
                ),
            ],
        ),

        # ── 主內容（左：圖表  右：QC 面板） ──────────────────────────────────
        html.Div(
            style={
                "display": "flex",
                "gap": "0",
                "alignItems": "flex-start",
                "padding": "16px 16px 0",
            },
            children=[

                # ── 左：圖表區 ────────────────────────────────────────────────
                html.Div(
                    style={"flex": "3", "minWidth": 0},
                    # children=[
                    children=dcc.Loading(
                        id="loading-graph",
                        type="circle",
                        color="#7eb8f7",
                        children=[
                        dcc.Graph(
                            id="main-graph",
                            # figure=go.Figure(),
                            # 初始化即採用深色底，避免視覺閃爍
                            figure=go.Figure(layout=dict(
                                template="plotly_dark",
                                paper_bgcolor="#1E1E1E",
                                plot_bgcolor="#1E1E1E"
                            )),
                            config={
                                "scrollZoom": False,
                                # Box Select (□) 與 Lasso Select 加到工具列
                                "modeBarButtonsToAdd": ["select2d", "lasso2d"],
                                "modeBarButtonsToRemove": ["autoScale2d"],
                                "displaylogo": False,
                                "toImageButtonOptions": {
                                    "format": "png",
                                    "scale": 2,
                                    "filename": f"ocean_{_DEMO_STID}",
                                },
                            },
                            style={
                                "borderRadius": "8px",
                                "boxShadow": "0 2px 8px rgba(0,0,0,.12)",
                                # "backgroundColor": "white",
                                 "backgroundColor": "#1E1E1E", #改這行好像沒用
                            },
                        ),
                        html.P(
                            "💡  請點選圖表右上角工具列的 □（Box Select）切換框選模式，"
                            "在圖表上拖曳選取異常區段，右側即自動產生 UPDATE SQL。",
                            style={
                                "color": "#666",
                                "fontSize": "12px",
                                "marginTop": "6px",
                                "paddingLeft": "4px",
                            },
                        ),
                    # ],
                    ]),
                ),

                # ── 右：QC 控制面板 ───────────────────────────────────────────
                html.Div(
                    style={
                        "width": "320px",
                        "flexShrink": 0,
                        "marginLeft": "16px",
                        "display": "flex",
                        "flexDirection": "column",
                        "gap": "12px",
                    },
                    children=[

                        # §0  水位 Y 軸範圍設定
                        html.Div(
                            style={
                                "backgroundColor": "#1e2a3a",
                                "border": f"1px solid {_CLR_BORDER}",
                                "borderRadius": "8px",
                                "padding": "14px 16px",
                            },
                            children=[
                                html.H3(
                                    "水位 Y 軸範圍",
                                    style={
                                        "margin": "0 0 10px",
                                        "fontSize": "13px",
                                        "color": "#7eb8f7",
                                        "borderBottom": f"2px solid {_CLR_NAVY}",
                                        "paddingBottom": "6px",
                                    },
                                ),
                                html.Div(
                                    style={"display": "flex", "alignItems": "center", "gap": "8px", "marginBottom": "8px"},
                                    children=[
                                        html.Label("上限", style={"fontSize": "13px", "color": "#ccd", "whiteSpace": "nowrap"}),
                                        dcc.Input(
                                            id="yaxis-max", type="number", placeholder="自動",
                                            debounce=True,
                                            style={
                                                "width": "80px", "fontSize": "13px",
                                                "padding": "4px 8px",
                                                "border": f"1px solid {_CLR_BORDER}",
                                                "borderRadius": "4px",
                                                "backgroundColor": "#111820",
                                                "color": "#ccd",
                                            },
                                        ),
                                        html.Label("下限", style={"fontSize": "13px", "color": "#ccd", "whiteSpace": "nowrap"}),
                                        dcc.Input(
                                            id="yaxis-min", type="number", placeholder="自動",
                                            debounce=True,
                                            style={
                                                "width": "80px", "fontSize": "13px",
                                                "padding": "4px 8px",
                                                "border": f"1px solid {_CLR_BORDER}",
                                                "borderRadius": "4px",
                                                "backgroundColor": "#111820",
                                                "color": "#ccd",
                                            },
                                        ),
                                    ],
                                ),
                                html.Div(
                                    style={"display": "flex", "gap": "8px", "marginBottom": "6px"},
                                    children=[
                                        html.Button("套用", id="yaxis-apply-btn", n_clicks=0,
                                                    style={"flex": "1", "fontFamily": _FONT_UI, "fontSize": "13px", "padding": "4px 0",
                                                        "backgroundColor": "#2a3f5f", "color": "#ccd",
                                                        "border": f"1px solid {_CLR_BORDER}", "borderRadius": "4px",
                                                        "cursor": "pointer"}),
                                        html.Button("清除", id="yaxis-clear-btn", n_clicks=0,
                                                    style={"flex": "1", "fontFamily": _FONT_UI, "fontSize": "13px", "padding": "4px 0",
                                                        "backgroundColor": "#2a3f5f", "color": "#ccd",
                                                        "border": f"1px solid {_CLR_BORDER}", "borderRadius": "4px",
                                                        "cursor": "pointer"}),
                                    ],
                                ),
                                html.Div(id="yaxis-status",
                                        style={"fontSize": "11px", "color": "#888", "minHeight": "16px"}),
                            ],
                        ),
                        
                        # §A  SQL 模式選擇
                        html.Div(
                            style={
                                # "backgroundColor": "white",
                                "backgroundColor": "#1e2a3a",
                                "border": f"1px solid {_CLR_BORDER}",
                                "borderRadius": "8px",
                                "padding": "14px 16px",
                            },
                            children=[
                                html.H3(
                                    "SQL 模式",
                                    style={
                                        "margin": "0 0 10px",
                                        "fontSize": "13px",
                                        # "color": _CLR_NAVY,
                                        "color": "#7eb8f7",
                                        "borderBottom": f"2px solid {_CLR_NAVY}",
                                        "paddingBottom": "6px",
                                    },
                                ),
                                dcc.RadioItems(
                                    id="qc-mode",
                                    options=[
                                        {"label": " Mode 1　更新 QC 旗標",
                                         "value": "1"},
                                        {"label": " Mode 2　MIN 欄位四則運算",
                                         "value": "2"},
                                    ],
                                    value="1",
                                    inputStyle={"marginRight": "6px"},
                                    labelStyle={
                                        "display": "block",
                                        "marginBottom": "6px",
                                        "fontSize": "13px",
                                        "color": "#ccd",  # #7eb8f7
                                    },
                                ),
                            ],
                        ),

                        # §B  Mode 1 參數（新 QC 值）
                        html.Div(
                            id="mode1-controls",
                            style={
                                "backgroundColor": "#1e2a3a",
                                "border": f"1px solid {_CLR_BORDER}",
                                "borderRadius": "8px",
                                "padding": "14px 16px",
                            },
                            children=[
                                html.H3(
                                    "Mode 1 參數",
                                    style={
                                        "margin": "0 0 10px",
                                        "fontSize": "13px",
                                        # "color": _CLR_NAVY,
                                        "color": "#7eb8f7",
                                        "borderBottom": f"2px solid {_CLR_NAVY}",
                                        "paddingBottom": "6px",
                                    },
                                ),
                                html.Div(
                                    style={
                                        "display": "flex",
                                        "alignItems": "center",
                                        "gap": "10px",
                                    },
                                    children=[
                                        html.Label(
                                            "新 QC 值：",
                                            # style={"fontSize": "13px",
                                            #        "whiteSpace": "nowrap"},
                                            style={"fontSize": "13px", "whiteSpace": "nowrap", "color": "#ccd"},
                                        ),
                                        dcc.Input(
                                            id="new-qc-value",
                                            type="number",
                                            value=9, min=0, max=9, step=1,
                                            style={
                                                "width": "60px",
                                                "border": f"1px solid {_CLR_BORDER}",
                                                "borderRadius": "4px",
                                                "padding": "4px 8px",
                                                "fontSize": "13px",
                                                "backgroundColor": "#111820",   # ← 加這行
                                                "color": "#ccd",                # ← 加這行
                                            },
                                        ),
                                        html.Span(
                                            "（0=良好  9=異常）",
                                            style={"fontSize": "11px", "color": "#888"},
                                        ),
                                    ],
                                ),
                            ],
                        ),

                        # §C  Mode 2 參數（運算子 + 數值）— 初始隱藏
                        html.Div(
                            id="mode2-controls",
                            style={
                                "display": "none",
                                "backgroundColor": "#1e2a3a",
                                "border": f"1px solid {_CLR_BORDER}",
                                "borderRadius": "8px",
                                "padding": "14px 16px",
                            },
                            children=[
                                html.H3(
                                    "Mode 2 參數",
                                    style={
                                        "margin": "0 0 10px",
                                        "fontSize": "13px",
                                        # "color": _CLR_NAVY,
                                        "color": "#7eb8f7",
                                        "borderBottom": f"2px solid {_CLR_NAVY}",
                                        # "borderBottom": "2px solid #7eb8f7",
                                        "paddingBottom": "6px",
                                    },
                                ),
                                html.Div(
                                    style={
                                        "display": "flex",
                                        "alignItems": "center",
                                        "gap": "8px",
                                    },
                                    children=[
                                        html.Label("MIN 欄位",
                                                   style={"fontSize": "13px", "whiteSpace": "nowrap", "color": "#ccd"}),
                                        dcc.Dropdown(
                                            id="qc-operator",
                                            options=[
                                                {"label": "+  加", "value": "+"},
                                                {"label": "−  減", "value": "-"},
                                                {"label": "×  乘", "value": "*"},
                                                {"label": "÷  除", "value": "/"},
                                            ],
                                            value="+",
                                            clearable=False,
                                            style={
                                                "width": "90px", 
                                                "fontSize": "13px",
                                                "border": f"1px solid {_CLR_BORDER}",
                                                "borderRadius": "4px"
                                            },
                                        ),
                                        dcc.Input(
                                            id="qc-operand",
                                            type="number",
                                            value=0.5,
                                            step=0.1,
                                            style={
                                                "width": "80px",
                                                "border": f"1px solid {_CLR_BORDER}",
                                                "borderRadius": "4px",
                                                "padding": "4px 8px",
                                                "fontSize": "13px",
                                                "backgroundColor": "#111820",   # ← 加這行
                                                "color": "#ccd",                # ← 加這行
                                            },
                                        ),
                                    ],
                                ),
                                html.P(
                                    "語意：被框到的各 MIN 欄位 = 原值  OP  數值",
                                    style={"margin": "8px 0 0",
                                           "fontSize": "11px", "color": "#888"},
                                ),
                            ],
                        ),

                        # §D  框選資訊列
                        html.Div(
                            id="selection-info",
                            style={
                                "fontSize": "12px",
                                "color": "#555",
                                "minHeight": "18px",
                                "paddingLeft": "2px",
                            },
                            children="尚未框選。",
                        ),

                        # §E  SQL 輸出 + 複製按鈕
                        html.Div(
                            style={
                                # "backgroundColor": "white",
                                "backgroundColor": "#111820",
                                "border": f"1px solid {_CLR_BORDER}",
                                "borderRadius": "8px",
                                "overflow": "hidden",
                            },
                            children=[
                                # 標題列（深藍底 + 複製按鈕）
                                html.Div(
                                    style={
                                        "backgroundColor": _CLR_NAVY,
                                        "color": "white",
                                        "padding": "8px 14px",
                                        "display": "flex",
                                        "alignItems": "center",
                                        "justifyContent": "space-between",
                                    },
                                    children=[
                                        html.Span(
                                            "UPDATE SQL",
                                            style={"fontSize": "13px",
                                                   "color": "#7eb8f7",
                                                   "fontWeight": 600},
                                        ),
                                        dcc.Clipboard(
                                            id="copy-btn",
                                            target_id="sql-output",
                                            title="複製 SQL",
                                            style={
                                                "fontSize": "18px",
                                                "cursor": "pointer",
                                                # "color": "white",
                                                "color": "#7eb8f7",
                                                "lineHeight": 1,
                                            },
                                        ),
                                    ],
                                ),
                                # SQL 文字區（深色等寬字體）
                                dcc.Textarea(
                                    id="sql-output",
                                    value=(
                                        "-- 框選圖表上的區段後，SQL 會顯示在此處。\n"
                                        "-- 請先在圖表工具列點選 □（Box Select）。"
                                    ),
                                    readOnly=True,
                                    style={
                                        "width": "100%",
                                        "height": "280px",
                                        "resize": "vertical",
                                        "border": "none",
                                        "outline": "none",
                                        "padding": "12px 14px",
                                        "fontFamily": _FONT_MONO,
                                        "fontSize": "12px",
                                        "lineHeight": "1.6",
                                        "backgroundColor": "#1e1e1e",
                                        "color": "#d4d4d4",
                                        "boxSizing": "border-box",
                                    },
                                ),
                            ],
                        ),

                    ],  # end QC 面板 children
                ),

            ],  # end 主內容 children
        ),

        # ── 底部狀態列 ────────────────────────────────────────────────────────
        html.Div(
            id="status-bar",
            style={
                "padding": "7px 24px",
                "fontSize": "11px",
                "color": "#666",
                "borderTop": f"1px solid {_CLR_BORDER}",
                "marginTop": "12px",
                "backgroundColor": "#111820",
            },
            children=(
                f"就緒｜Demo STID：{_DEMO_STID}｜"
                "請切換至 □ Box Select 模式後在圖表上框選"
            ),
        ),

    ],
)


# ══════════════════════════════════════════════════════════════════════════════
# § 6  Callbacks
# ══════════════════════════════════════════════════════════════════════════════

# ── Callback 1：切換 QC 模式 → 顯示/隱藏對應參數區塊 ─────────────────────────

@app.callback(
    Output("mode1-controls", "style"),
    Output("mode2-controls", "style"),
    Input("qc-mode", "value"),
)
def toggle_mode_controls(mode: str):
    """Mode 1 ↔ Mode 2 切換時，顯示對應的參數輸入區，隱藏另一區。"""
    _base = {
        # "backgroundColor": "white",
        "backgroundColor": "#1e2a3a",
        "border": f"1px solid {_CLR_BORDER}",
        "borderRadius": "8px",
        "padding": "14px 16px",
    }
    show = {**_base, "display": "block"}
    hide = {**_base, "display": "none"}
    return (show, hide) if mode == "1" else (hide, show)


# ── Callback 2：Box Select 框選 → 產生 SQL ───────────────────────────────────

@app.callback(
    Output("sql-output",     "value"),
    Output("selection-info", "children"),
    Output("status-bar",     "children"),
    Input("main-graph",      "selectedData"),
    State("qc-mode",         "value"),
    State("new-qc-value",    "value"),
    State("qc-operator",     "value"),
    State("qc-operand",      "value"),
    State("stid-store",      "data"),
    prevent_initial_call=True,
)
def on_selection(selected_data, mode, new_qc, operator, operand, stid):
    """
    dcc.Graph.selectedData 觸發（使用者以 Box Select 框選圖表區域）。
    轉換格式後呼叫對應的 SQL builder，結果寫入 Textarea。
    """
    if not selected_data:
        return (
            "-- 框選範圍為空，請重新操作。",
            "框選結果：無資料點。",
            "狀態：框選範圍為空。",
        )

    # Dash selectedData → SQL builder 所需 sel dict
    sel     = _adapt_selected_data(selected_data)
    cnt     = sel.get("point_count", 0)
    t_start = _clean_ts(sel.get("x_start")) or "—"
    t_end   = _clean_ts(sel.get("x_end"))   or "—"
    stid    = stid or _DEMO_STID

    if mode == "1":
        sql  = build_mode1_sql(sel, stid, int(new_qc or 9))
        info = f"Mode 1 ｜ {cnt} 點 ｜ t = [ {t_start}  ～  {t_end} ]"
    else:
        sql  = build_mode2_sql_by_time(
            sel, stid, operator or "+", float(operand or 0)
        )
        info = f"Mode 2 ｜ {cnt} 點 ｜ 運算：原值 {operator or '+'} {operand or 0}"

    status = f"就緒｜STID：{stid}｜{info}"
    return sql, info, status


@app.callback(
    Output("bundle-key-store", "data"),
    Input("bundle-poll",       "n_intervals"),
    State("bundle-key-store",  "data"),
)
def poll_bundle(n_intervals, current_key):
    latest = dash_bridge.get_latest_key()
    # 如果橋接層有新資料，立即更新 Store
    if latest:
        if latest != current_key:
            return latest
    # # 若為第一次執行且橋接層無資料（代表可能是獨立啟動 dash_app.py 進行開發測試）
    # 為了跳轉畫面美觀關掉試試
    # elif n_intervals == 0:
    #     return "demo"
        
    return no_update


@app.callback(
    Output("main-graph", "figure"),
    Output("stid-store", "data"),
    Input("bundle-key-store", "data"),
)
def render_water_figure(key):
    if not key:
        return no_update, no_update
        
    # 處理開發模式下的 Demo 展示
    if key == "demo":
        return make_demo_figure(), _DEMO_STID

    bundle = dash_bridge.get_bundle(key)
    if bundle is None:
        return no_update, no_update
        
    bundles = bundle if isinstance(bundle, list) else [bundle] #強制把格式改成list傳遞給build_water_figure，以免單一值時格式為bundle（dict）報錯。因為該函式設計只接受list
    # 從資料中動態取得 STID 並更新 Store，確保 SQL 產生器抓到正確的測站
    primary_stid = bundles[0].get("stid", _DEMO_STID) if bundles else _DEMO_STID
    
    lr = dash_bridge.get_land_range()          # ← 加這行
    return build_water_figure(bundles, land_range=lr), primary_stid


# -------貼在現有 callbacks 區塊末尾，不影響任何既有邏輯：--------
# # 第二版clientside_callback
# app.clientside_callback(
#     """
#     function(n_apply, n_clear, y_max, y_min) {
#         const ctx = dash_clientside.callback_context;
#         if (!ctx || !ctx.triggered.length) return window.dash_clientside.no_update;

#         const triggered_id = ctx.triggered[0].prop_id.split('.')[0];
#         const graphDiv = document.getElementById('main-graph');
#         if (!graphDiv || !graphDiv._fullLayout) return window.dash_clientside.no_update;

#         const leftAxes = Object.keys(graphDiv._fullLayout).filter(k =>
#             k === 'yaxis' ||
#             (k.startsWith('yaxis') && /^[0-9]+$/.test(k.slice(5)) && parseInt(k.slice(5)) % 2 === 1)
#         );

#         const update = {};

#         if (triggered_id === 'yaxis-clear-btn') {
#             leftAxes.forEach(k => {
#                 update[k + '.autorange'] = true;
#                 update[k + '.range']     = null;
#             });
#             Plotly.relayout(graphDiv, update);
#             return '✓ 已重設為自動範圍';
#         }

#         if (y_max === null && y_min === null) return '⚠ 請至少輸入一個值';
#         if (y_max !== null && y_min !== null && y_max <= y_min) return '⚠ 上限必須大於下限';

#         leftAxes.forEach(k => {
#             update[k + '.autorange'] = false;
#             update[k + '.range']     = [y_min, y_max];
#         });
#         Plotly.relayout(graphDiv, update);

#         const lo = y_min !== null ? y_min : 'auto';
#         const hi = y_max !== null ? y_max : 'auto';
#         return '✓ 已套用至 ' + leftAxes.length + ' 個子圖　[' + lo + ', ' + hi + ']';
#     }
#     """,
#     Output("yaxis-status", "children"),
#     Input("yaxis-apply-btn",  "n_clicks"),
#     Input("yaxis-clear-btn",  "n_clicks"),
#     State("yaxis-max", "value"),
#     State("yaxis-min", "value"),
#     prevent_initial_call=True,
# )

# # 把整個 apply_yaxis_range callback 函式完整刪除（包含 @app.callback 裝飾器）。
# app.clientside_callback(
#     """
#     function(n_apply, n_clear, y_max, y_min) {
#         const ctx = dash_clientside.callback_context;
#         if (!ctx || !ctx.triggered.length) return window.dash_clientside.no_update;

#         const triggered_id = ctx.triggered[0].prop_id.split('.')[0];
#         const graphDiv = document.getElementById('main-graph');

#         // 圖表尚未渲染完成時略過
#         if (!graphDiv || !graphDiv._fullLayout) return window.dash_clientside.no_update;

#         // 找出所有左側 Y 軸 key（水位軸，奇數編號）
#         const leftAxes = Object.keys(graphDiv._fullLayout).filter(k =>
#             k === 'yaxis' ||
#             (k.startsWith('yaxis') && /^[0-9]+$/.test(k.slice(5)) && parseInt(k.slice(5)) % 2 === 1)
#         );

#         const update = {};

#         if (triggered_id === 'yaxis-clear-btn') {
#             leftAxes.forEach(k => {
#                 update[k + '.autorange'] = true;
#                 update[k + '.range']     = null;
#             });
#             Plotly.relayout(graphDiv, update);
#             return '✓ 已重設為自動範圍';
#         }

#         // 套用分支
#         if (y_max === null && y_min === null) return '⚠ 請至少輸入一個值';
#         if (y_max !== null && y_min !== null && y_max <= y_min) return '⚠ 上限必須大於下限';

#         leftAxes.forEach(k => {
#             update[k + '.autorange'] = false;
#             update[k + '.range']     = [y_min, y_max];
#         });
#         Plotly.relayout(graphDiv, update);

#         const lo = y_min !== null ? y_min : 'auto';
#         const hi = y_max !== null ? y_max : 'auto';
#         return `✓ 已套用至 ${leftAxes.length} 個子圖　[${lo}, ${hi}]`;
#     }
#     """,
#     Output("yaxis-status", "children"),
#     Input("yaxis-apply-btn",  "n_clicks"),
#     Input("yaxis-clear-btn",  "n_clicks"),
#     State("yaxis-max", "value"),
#     State("yaxis-min", "value"),
#     prevent_initial_call=True,
# )

@app.callback(
    Output("main-graph", "figure"),
    Output("yaxis-status", "children"),
    Input("yaxis-apply-btn", "n_clicks"),
    Input("yaxis-clear-btn", "n_clicks"),     # 新增
    State("yaxis-max", "value"),
    State("yaxis-min", "value"),
    State("main-graph", "figure"),
    prevent_initial_call=True,
)
def apply_yaxis_range(n_apply, n_clear, y_max, y_min, current_fig):
    if current_fig is None:
        raise Dash.exceptions.PreventUpdate

    layout = current_fig.get("layout", {})
    left_yaxis_keys = [
        k for k in layout
        if k == "yaxis" or (k.startswith("yaxis") and k[5:].isdigit() and int(k[5:]) % 2 == 1)
    ]

    patched = Patch()

    # ── 清除分支 ──────────────────────────────────────────
    if ctx.triggered_id == "yaxis-clear-btn":
        for key in left_yaxis_keys:
            patched["layout"][key]["autorange"] = True
            patched["layout"][key]["range"] = None
        return patched, "✓ 已重設為自動範圍"

    # ── 套用分支 ──────────────────────────────────────────
    if y_max is None and y_min is None:
        return no_update, "⚠ 請至少輸入一個值"
    if y_max is not None and y_min is not None and y_max <= y_min:
        return no_update, "⚠ 上限必須大於下限"

    for key in left_yaxis_keys:
        patched["layout"][key]["autorange"] = False
        patched["layout"][key]["range"] = [y_min, y_max]   # None 的一側 Plotly 會自動處理

    n_rows = len(left_yaxis_keys)
    lo_str = str(y_min) if y_min is not None else "auto"
    hi_str = str(y_max) if y_max is not None else "auto"
    return patched, f"✓ 已套用至 {n_rows} 個子圖　[{lo_str}, {hi_str}]"

# ══════════════════════════════════════════════════════════════════════════════
# § 7  Entry Point
#
#  獨立執行（開發 / 驗證）：
#      python dash_app.py
#
#  整合 Tkinter 時不在 __main__ 下執行，改由 MainApp.__init__() 呼叫：
#
#      from dash_app import app as dash_app, DASH_HOST, DASH_PORT
#
#      t = threading.Thread(
#              target=dash_app.run,
#              kwargs={"host": DASH_HOST, "port": DASH_PORT,
#                      "debug": False, "use_reloader": False},
#              daemon=True,
#          )
#      t.start()
#      time.sleep(1.5)   # 等待 Flask 啟動
#      # …查詢完成後…
#      shared_state.push_figure(fig, stid)
#      webbrowser.open(f"http://{DASH_HOST}:{DASH_PORT}")
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"[Dash] 啟動於 http://{DASH_HOST}:{DASH_PORT}")
    app.run(
        host=DASH_HOST,
        port=DASH_PORT,
        debug=True,           # 開發時開啟；整合 Tkinter 後必須設為 False
        use_reloader=False,   # 嵌入 Tkinter daemon thread 時必須關閉
    )
