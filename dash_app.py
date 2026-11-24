"""
dash_app.py
===========
Dash/Flask 工作區（V2）。daemon 執行緒由 main_app.py 啟動。
 
PORT 在 main_app.py 啟動執行緒前設定：
    import dash_app as dash_app_module
    dash_app_module.PORT = port
 
工作區架構（三層 Tab）：
  ws-qc     QC 工作台
    qc-water    水位時序 + QC   ← R5 接通
    qc-vdc      VdC 散佈圖      ← R9 接通
    qc-buoy     浮球/浮標 QC   ← R7 接通
  ws-diag   診斷總覽
    diag-ocean  海洋參數（唯讀）← R8 接通
    diag-qcflag 潮位自動檢核（唯讀）← 2026-11-20（2026-11-24 由「多采潮位旗標」改名）
  ws-event  事件報告
    event-surge  暴潮偏差（互動）← R9 接通
    event-report 事件報告（白底）← R9 接通
 
R5 只接通水位圖渲染，其餘子頁籤保留「請先查詢」占位文字。
"""
 
import os
from urllib.parse import parse_qs

import pandas as pd
 
import dash
from dash import dcc, html, Input, Output, State, Patch, ctx, no_update, MATCH
 
import dash_bridge
from build_water_figure import build_water_figure, build_water_report_figure
from build_diagnostic_figure import build_diagnostic_figure
from build_buoy_figure import (
    build_wave1_figure, build_wind_figure, build_curr_figure,
    build_wave_figure,  build_pres1_figure, build_stemp1_figure,
)
from buoy_checks import wave_checks, summarize as summarize_checks
from hover_rows import apply_row_hover
import qcflag_engine
from build_qcflag_figure import build_qcflag_figure, empty_figure as qcflag_empty_figure
from sql_builders import build_mode1_sql, build_mode2_sql, build_mode3_sql, build_buoy_qc_sql
from build_vdc_figure import build_vdc_figure
from build_surge_report_figure import build_surge_interactive_figure, build_surge_report_figure
 
PORT: int = 8050   # main_app.py 在 app.run() 前覆寫
 
# ── 顏色 ──────────────────────────────────────────────────────────────────
_BG     = '#111820'
_PANEL  = '#1E2A3A'
_HEADER = '#1A3A5C'
_TEXT   = '#CCD0D4'
_ACCENT = '#4A9EBF'
_ENTRY  = '#1E1E1E'
_BORDER = 'rgba(200,214,229,0.25)'

# ── 字體大小 (Gemini協助新增這區) ────────────────────────────────────────────────────
_FS_SYS_TITLE = '18px'  # 系統大標題 (原本 17px)
_FS_SUB_TITLE = '16px'  # 佔位符、測站標題 (原本 15px)
_FS_PANEL_H   = '14px'  # 💡 面板標題 (原本 13px)
_FS_BODY      = '13px'  # 一般內文、狀態列、Input、Radio、按鈕 (原本 12px)
_FS_SMALL     = '12px'  # ▸ 區塊小標題、SQL 程式碼、附註 (原本 11px)
_FS_TINY      = '11px'  # 極小警告與提示 (原本 10px)
_FS_LEGEND    =  14     # Plotly 圖例字體大小（注意：這裡是整數，沒有 px）
_FS_AXIS_TITLE=  14     # 座標軸標題大小（例如「時間」、「水位 (cm)」）
_FS_AXIS_TICK =  12     # 座標軸刻度數字大小（例如「2025-07-24」、「150」）
 
# ── Dash app ──────────────────────────────────────────────────────────────
app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = '海洋動力診斷儀表板 V2'

# 2026-11-20：框選／套索模式下雙擊也回到初始範圍。
# Plotly 的設計是：縮放模式雙擊＝重設座標軸；框選／套索模式雙擊＝只清除框選、不重設。
# 浮標圖預設是框選模式，所以雙擊沒反應；水位圖預設縮放模式，切到框選後一樣沒反應。
# 做法：框選／套索模式下雙擊時 Plotly 一定會發出 plotly_deselect（有沒有框選都會；單擊不會，
# 2026-11-20 無頭瀏覽器實測）。收到時代按該圖工具列的「重設座標軸」（房子圖示），效果和縮放模式下
# 雙擊相同（回到 Plotly 記下的初始範圍）。瀏覽器原生的 dblclick 事件被 Plotly 拖曳遮罩吃掉，不能用。
# 每秒掃一次頁面上的圖，替還沒掛上的圖掛監聽（Dash 換頁籤、換資料時會產生新的圖）。
# 寫在頁面樣板裡，不需另外打包 assets 資料夾。
_DBLCLICK_RESET_JS = """
<script>
setInterval(function () {
  document.querySelectorAll('.js-plotly-plot').forEach(function (gd) {
    if (gd._dblResetHooked || typeof gd.on !== 'function') return;
    gd._dblResetHooked = true;
    gd.on('plotly_deselect', function () {
      var dm = gd._fullLayout && gd._fullLayout.dragmode;
      if (dm !== 'select' && dm !== 'lasso') return;
      var btn = gd.querySelector('.modebar-btn[data-attr="zoom"][data-val="reset"]');
      if (btn) btn.click();
    });
  });
}, 1000);
</script>
"""
app.index_string = app.index_string.replace('{%renderer%}', '{%renderer%}' + _DBLCLICK_RESET_JS)
 
# ── 共用 Tab 樣式 ─────────────────────────────────────────────────────────
# 主工作區頁籤（深色背景）
_WT  = {'color': _TEXT,   'background': _BG,    'fontFamily': '標楷體',
        'fontSize': _FS_SUB_TITLE, 'padding': '8px 16px'}
_WTA = {'color': _ACCENT, 'background': _PANEL, 'fontFamily': '標楷體',
        'fontSize': _FS_SUB_TITLE, 'fontWeight': 'bold', 'padding': '8px 16px',
        'borderTop': f'2px solid {_ACCENT}'}
# 子頁籤（面板背景）
_ST  = {'color': _TEXT,   'background': _PANEL, 'fontFamily': '標楷體',
        'fontSize': _FS_PANEL_H, 'padding': '6px 12px'}
_STA = {'color': _ACCENT, 'background': _ENTRY, 'fontFamily': '標楷體',
        'fontSize': _FS_PANEL_H, 'fontWeight': 'bold', 'padding': '6px 12px',
        'borderTop': f'2px solid {_ACCENT}'}
 
 
# ── 佔位元件 ──────────────────────────────────────────────────────────────
def _ph(msg: str = '（請先在 Tkinter 視窗選站後點「查詢」）') -> html.Div:
    return html.Div(msg, style={
        'color': _TEXT, 'padding': '60px 40px',
        'fontSize': _FS_SUB_TITLE, 'fontFamily': '標楷體',
    })
 
 
# ── 水位時序子頁籤 layout ─────────────────────────────────────────────────
def _water_tab() -> html.Div:
    """
    左側圖表（flex:3）＋ 右側控制面板（固定 360px）。
    R5：右側面板只放狀態文字；Mode 選擇、SQL 輸出、Y 軸控制等 R6 再加。
 
    zoom-range-store 用途（Q3 補充）：
      本 round 的 capture_zoom callback 會把 water-graph 的 x 軸縮放範圍
      寫入 zoom-range-store，R9 的 VdC callback 讀取此 Store 來同步範圍。
      這是 V2 透過 Store 明確共享狀態的方式，取代 V1 共用同一個 Graph 元件
      的隱式共享，不需要兩個 tab 用同一個 dcc.Graph。
    """
    return html.Div([
        # 左側水位圖（R6 會在此圖上做 Box Select）
        # dcc.Graph(
        #     id='water-graph',
        #     config={
        #         'modeBarButtonsToAdd': ['select2d', 'lasso2d'],
        #         'toImageButtonOptions': {'format': 'png', 'scale': 2},
        #         'scrollZoom': True,
        #     },
        #     style={'flex': '3', 'minHeight': '80vh'},
        # ),

        # 把捲動設在外層Div，Graph本身只管寬度，仿照_diag_tab()的做法
        html.Div([
            dcc.Graph(
                id='water-graph',
                config={
                    'modeBarButtonsToAdd': ['select2d', 'lasso2d'],
                    'toImageButtonOptions': {'format': 'png', 'scale': 2},
                    'scrollZoom': True,
                },
                style={'width': '100%'},
            ),
        ], style={'flex': '3', 'overflowY': 'auto'}),

        # 右側控制面板
        html.Div([
            html.Div('💡 QC 控制面板', style={
                'fontFamily': '標楷體', 'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                'color': _ACCENT, 'padding': '10px 14px',
                'background': _HEADER,
                'borderBottom': f'1px solid {_BORDER}',
            }),
            html.Div(id='water-status', style={
                'padding': '10px 14px', 'color': _TEXT,
                'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                'whiteSpace': 'pre-wrap',
            }),
            # ── 自動檢核（試行）疊點（2026-11-24）─────────────────────────
            # 預設不勾；勾了才連旗標庫（.env 的 QC_DB_*），沒設定／連不上只在下方顯示提示。
            # 只疊彙總 3／4；各子項細節看「診斷總覽 → 潮位自動檢核（唯讀）」。
            html.Div('▸ 自動檢核（試行）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.Checklist(
                id='autoqc-toggle',
                options=[{'label': ' 疊自動檢核結果：可疑／不通過', 'value': 'on'}],
                value=[],
                inputStyle={'marginRight': '6px'},
                labelStyle={
                    'display': 'block', 'fontFamily': '標楷體',
                    'fontSize': _FS_BODY, 'color': _TEXT,
                    'padding': '2px 0', 'cursor': 'pointer',
                },
                style={'padding': '4px 18px 2px'},
            ),
            html.Div(id='autoqc-status', style={
                'padding': '2px 18px 8px', 'color': _TEXT, 'opacity': '0.85',
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL,
                'whiteSpace': 'pre-wrap', 'lineHeight': '1.5',
            }),
            # ── R6：Y 軸範圍控制 ──────────────────────────────────────────
            html.Div('▸ Y 軸範圍', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            html.Div([
                # 第一個元素（上限）：只需要設定右邊距
                html.Span('上限', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                    # 'color': _TEXT, 'margin': '0 4px 0 14px',
                    'color': _TEXT, 'marginRight': '4px',
                }),
                dcc.Input(
                    id='yaxis-max', type='number', placeholder='自動',
                    style={
                        'width': '70px', 'fontFamily': '標楷體',
                        'fontSize': _FS_BODY, 'background': _ENTRY,
                        'color': _TEXT, 'border': f'1px solid {_BORDER}',
                        'padding': '3px 6px',
                    },
                ),
                # 第二個元素（下限）：需要設定左邊距 (14px) 來跟前面的輸入框隔開
                html.Span('下限', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                    # 'color': _TEXT, 'marginRight': '4px',
                    'color': _TEXT, 'margin': '0 4px 0 14px',
                }),
                dcc.Input(
                    id='yaxis-min', type='number', placeholder='自動',
                    style={
                        'width': '70px', 'fontFamily': '標楷體',
                        'fontSize': _FS_BODY, 'background': _ENTRY,
                        'color': _TEXT, 'border': f'1px solid {_BORDER}',
                        'padding': '3px 6px',
                    },
                ),
            ], style={
                'padding': '4px 18px 4px',
                'display': 'flex', 'alignItems': 'center',
            }),
            html.Div([
                html.Button(
                    '套用 Y 範圍', id='apply-yaxis-btn', n_clicks=0,
                    style={
                        'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                        'background': _PANEL, 'color': _ACCENT,
                        'border': f'1px solid {_ACCENT}',
                        'padding': '4px 12px', 'cursor': 'pointer',
                        'borderRadius': '3px',
                    },
                ),
                html.Button(
                    '重設 Y 軸', id='reset-yaxis-btn', n_clicks=0,
                    style={
                        'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                        'background': _PANEL, 'color': _TEXT,
                        'border': f'1px solid {_BORDER}',
                        'padding': '4px 12px', 'cursor': 'pointer',
                        'borderRadius': '3px', 'marginLeft': '8px',
                    },
                ),
            ], style={
                'padding': '4px 18px 12px',
                'display': 'flex', 'alignItems': 'center',
            }),
            # R6 占位：Mode 選擇、SQL 輸出區、Y 軸範圍控制 在此插入
            # ── R6：操作模式 ──────────────────────────────────────────────
            html.Div('▸ 操作模式', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.RadioItems(
                id='qc-mode',
                options=[
                    {'label': ' Mode 1：QC 旗標', 'value': '1'},
                    {'label': ' Mode 2：數值修正', 'value': '2'},
                    {'label': ' Mode 3：暴潮紀錄', 'value': '3'},
                ],
                value='1',
                inputStyle={'marginRight': '6px'},
                labelStyle={
                    'display': 'block', 'fontFamily': '標楷體',
                    'fontSize': _FS_BODY, 'color': _TEXT,
                    'padding': '2px 0', 'cursor': 'pointer',
                },
                style={'padding': '4px 18px 6px'},
            ),
            # ── R6：新 QC 旗標值（Mode 1 必填）──────────────────────────
            html.Div('▸ 新 QC 旗標值（Mode 1 用）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            html.Div([
                dcc.Input(
                    id='qc-new-val', value='X', type='text',
                    style={
                        'width': '52px', 'fontFamily': '標楷體',
                        'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                        'background': _ENTRY, 'color': _TEXT,
                        'border': f'1px solid {_BORDER}',
                        'padding': '3px 6px', 'textAlign': 'center',
                    },
                ),
                html.Span('  X=異常  V=補值  空=待定', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_SMALL,
                    'color': _TEXT, 'opacity': '0.7', 'marginLeft': '8px',
                }),
            ], style={
                'padding': '4px 18px 6px',
                'display': 'flex', 'alignItems': 'center',
            }),
            # ── R6：Mode 2 附屬控制（僅 mode='2' 時顯示）────────────────
            html.Div(id='mode2-controls', style={'display': 'none'}, children=[
                html.Div('▸ Mode 2：四則運算', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                    'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
                }),
                html.Div([
                    html.Span('運算符', style={
                        'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                        'color': _TEXT, 'marginRight': '8px',
                    }),
                    dcc.RadioItems(
                        id='mode2-op', value='+',
                        options=[
                            {'label': ' +', 'value': '+'},
                            {'label': ' −', 'value': '-'},
                            {'label': ' ×', 'value': '*'},
                            {'label': ' ÷', 'value': '/'},
                        ],
                        inline=True,
                        inputStyle={'marginRight': '3px'},
                        labelStyle={
                            'fontFamily': '標楷體', 'fontSize': '14px',
                            'color': _TEXT, 'marginRight': '10px',
                            'cursor': 'pointer',
                        },
                    ),
                ], style={
                    'padding': '4px 18px 2px',
                    'display': 'flex', 'alignItems': 'center',
                }),
                html.Div([
                    html.Span('運算數', style={
                        'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                        'color': _TEXT, 'marginRight': '8px',
                    }),
                    dcc.Input(
                        id='mode2-operand', type='number',
                        placeholder='例：-5', debounce=True,
                        style={
                            'width': '110px', 'fontFamily': '標楷體',
                            'fontSize': _FS_BODY, 'background': _ENTRY,
                            'color': _TEXT, 'border': f'1px solid {_BORDER}',
                            'padding': '3px 6px',
                        },
                    ),
                ], style={
                    'padding': '4px 18px 6px',
                    'display': 'flex', 'alignItems': 'center',
                }),
            ]),
            # ── R6：Mode 3 附屬控制（僅 mode='3' 時顯示）────────────────
            html.Div(id='mode3-controls', style={'display': 'none'}, children=[
                html.Div('▸ Mode 3：暴潮 INSERT', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                    'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
                }),
                html.Div(id='mode3-ty-label', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_SMALL,
                    'color': _TEXT, 'padding': '2px 18px 4px', 'opacity': '0.8',
                }),
                html.Div(
                    html.Button(
                        '產生暴潮 INSERT SQL', id='mode3-btn', n_clicks=0,
                        style={
                            'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                            'background': _ACCENT, 'color': 'white',
                            'border': 'none', 'padding': '5px 14px',
                            'cursor': 'pointer', 'borderRadius': '3px',
                        },
                    ),
                    style={'padding': '4px 18px 8px'},
                ),
            ]),
            # ── R6：SQL 輸出（可編輯、可複製）────────────────────────────
            html.Div('▸ SQL 輸出（可編輯複製）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.Textarea(
                id='sql-output', value='',
                style={
                    'width': 'calc(100% - 28px)', 'height': '160px',
                    'fontFamily': 'Consolas, "Courier New", monospace',
                    'fontSize': _FS_SMALL, 'background': _ENTRY,
                    'color': '#A8D8A8',
                    'border': f'1px solid {_BORDER}',
                    'padding': '6px 8px', 'margin': '4px 14px 6px',
                    'resize': 'vertical', 'lineHeight': '1.5',
                    'outline': 'none',
                },
            ),
            # ── 進階圖層顯示（低頻趨勢／EWMA／1H平滑）─────────────────
            # 這三種圖層在 build_water_figure() 已設 showlegend=False，
            # 不進原生圖例（避免項目過多造成跨子圖重疊），改由這裡控制。
            html.Div('▸ 進階圖層顯示', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.Checklist(
                id='adv-layer-toggle',
                options=[
                    {'label': ' 水位低頻趨勢(25h-MA)', 'value': 'lf'},
                    {'label': ' EWMA(α=0.05)',        'value': 'ewma'},
                    {'label': ' 1H平滑(均值±std)',      'value': 'smooth'},
                ],
                value=[],
                inputStyle={'marginRight': '6px'},
                labelStyle={
                    'display': 'block', 'fontFamily': '標楷體',
                    'fontSize': _FS_BODY, 'color': _TEXT,
                    'padding': '2px 0', 'cursor': 'pointer',
                },
                style={'padding': '4px 18px 8px'},
            ),
            # ── 進階圖層目標儀器（避免多儀器的同類線疊在一起）─────────
            # 只影響 lf/ewma/smooth 這三種 overlay；wl/raw/pred/diff 等主線
            # 不受此篩選影響，仍會全部顯示。
            html.Div('▸ 進階圖層目標儀器', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '4px 14px 2px',
            }),
            dcc.RadioItems(
                id='adv-layer-target',
                options=[{'label': ' 全部儀器', 'value': 'all'}],
                value='all',
                inputStyle={'marginRight': '6px'},
                labelStyle={
                    'display': 'block', 'fontFamily': '標楷體',
                    'fontSize': _FS_BODY, 'color': _TEXT,
                    'padding': '2px 0', 'cursor': 'pointer',
                },
                style={'padding': '4px 18px 8px'},
            ),
            #### 該操作移動到上方，測試正確後刪除 ####
            # # ── R6：Y 軸範圍控制 ──────────────────────────────────────────
            # html.Div('▸ Y 軸範圍', style={
            #     'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
            #     'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            # }),
            # html.Div([
            #     html.Span('下限', style={
            #         'fontFamily': '標楷體', 'fontSize': _FS_BODY,
            #         'color': _TEXT, 'marginRight': '4px',
            #     }),
            #     dcc.Input(
            #         id='yaxis-min', type='number', placeholder='自動',
            #         style={
            #             'width': '70px', 'fontFamily': '標楷體',
            #             'fontSize': _FS_BODY, 'background': _ENTRY,
            #             'color': _TEXT, 'border': f'1px solid {_BORDER}',
            #             'padding': '3px 6px',
            #         },
            #     ),
            #     html.Span('上限', style={
            #         'fontFamily': '標楷體', 'fontSize': _FS_BODY,
            #         'color': _TEXT, 'margin': '0 4px 0 14px',
            #     }),
            #     dcc.Input(
            #         id='yaxis-max', type='number', placeholder='自動',
            #         style={
            #             'width': '70px', 'fontFamily': '標楷體',
            #             'fontSize': _FS_BODY, 'background': _ENTRY,
            #             'color': _TEXT, 'border': f'1px solid {_BORDER}',
            #             'padding': '3px 6px',
            #         },
            #     ),
            # ], style={
            #     'padding': '4px 18px 4px',
            #     'display': 'flex', 'alignItems': 'center',
            # }),
            # html.Div([
            #     html.Button(
            #         '套用 Y 範圍', id='apply-yaxis-btn', n_clicks=0,
            #         style={
            #             'fontFamily': '標楷體', 'fontSize': _FS_BODY,
            #             'background': _PANEL, 'color': _ACCENT,
            #             'border': f'1px solid {_ACCENT}',
            #             'padding': '4px 12px', 'cursor': 'pointer',
            #             'borderRadius': '3px',
            #         },
            #     ),
            #     html.Button(
            #         '重設 Y 軸', id='reset-yaxis-btn', n_clicks=0,
            #         style={
            #             'fontFamily': '標楷體', 'fontSize': _FS_BODY,
            #             'background': _PANEL, 'color': _TEXT,
            #             'border': f'1px solid {_BORDER}',
            #             'padding': '4px 12px', 'cursor': 'pointer',
            #             'borderRadius': '3px', 'marginLeft': '8px',
            #         },
            #     ),
            # ], style={
            #     'padding': '4px 18px 12px',
            #     'display': 'flex', 'alignItems': 'center',
            # }),
        ], style={
            'width': '360px', 'minWidth': '360px',
            'background': _PANEL,
            'borderLeft': f'1px solid {_BORDER}',
            'display': 'flex', 'flexDirection': 'column',
            'overflowY': 'auto',
        }),
    ], style={'display': 'flex', 'height': 'calc(100vh - 90px)'})
 
# ── 診斷總覽頁籤 layout ──────────────────────────────────────────────────
def _diag_tab() -> html.Div:
    """
    診斷總覽頁籤（R8 接通，唯讀）。
    build_diagnostic_figure() 依站數設定 figure.layout.height（每站 800px）。
    外層 Div 固定視窗高度 + overflowY: auto，讓多站高圖可垂直捲動。
    不加 select2d：唯讀頁籤，不支援 Box Select → SQL。
    """
    return html.Div([
        dcc.Graph(
            id='diag-graph',
            config={
                'scrollZoom': True,
                'toImageButtonOptions': {'format': 'png', 'scale': 2},
            },
            style={'width': '100%'},
        ),
    ], style={
        'height': 'calc(100vh - 90px)',
        'overflowY': 'auto',
        'background': _BG,
    })


# ── 潮位自動檢核頁籤 layout（2026-11-20，唯讀；原名「多采潮位旗標」）────────────────────────────
def _qcflag_tab() -> html.Div:
    """
    潮位自動檢核旗標（唯讀）。資料整理在 qcflag_engine.py、畫圖在 build_qcflag_figure.py。
    旗標庫連線另設（.env 的 QC_DB_*）；沒設定或連不上時只在本頁籤顯示提示，不影響其他頁籤。
    不加 select2d：唯讀，不產生 SQL。
    """
    _lbl = {'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
            'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}'}
    return html.Div([
        html.Div([
            dcc.Graph(
                id='qcflag-graph',
                figure=qcflag_empty_figure('（請先在 Tkinter 視窗選潮位站後點「查詢」）'),
                config={'scrollZoom': True,
                        'toImageButtonOptions': {'format': 'png', 'scale': 2}},
                style={'width': '100%'},
            ),
        ], style={'flex': '1', 'overflowY': 'auto'}),
        html.Div([
            html.Div('💡 潮位自動檢核（唯讀）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                'color': _ACCENT, 'padding': '10px 14px', 'background': _HEADER,
                'borderBottom': f'1px solid {_BORDER}',
            }),
            html.Div('▸ 儀器', style={**_lbl, 'borderTop': 'none'}),
            dcc.Dropdown(id='qcflag-inst', options=[], value=None, clearable=False,
                         style={'margin': '2px 14px 6px', 'color': '#111',
                                'fontFamily': '標楷體', 'fontSize': _FS_BODY}),
            dcc.Checklist(
                id='qcflag-show-dc',
                options=[{'label': ' 顯示多采模式列（dc_*，比較用）', 'value': 'on'}],
                value=[], inputStyle={'marginRight': '6px'},
                labelStyle={'display': 'block', 'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                            'color': _TEXT, 'cursor': 'pointer'},
                style={'padding': '2px 18px 4px'},
            ),
            html.Div(id='qcflag-status', style={
                'padding': '8px 14px', 'color': _TEXT, 'fontFamily': '標楷體',
                'fontSize': _FS_BODY, 'whiteSpace': 'pre-wrap', 'lineHeight': '1.5',
                'borderTop': f'1px solid {_BORDER}',
            }),
            html.Div(id='qcflag-info', style={
                'padding': '0 14px 12px', 'color': _TEXT, 'fontFamily': '標楷體',
                'fontSize': _FS_SMALL, 'lineHeight': '1.5',
            }),
        ], style={
            'width': '360px', 'minWidth': '360px', 'background': _PANEL,
            'borderLeft': f'1px solid {_BORDER}', 'display': 'flex',
            'flexDirection': 'column', 'overflowY': 'auto',
        }),
    ], style={'display': 'flex', 'height': 'calc(100vh - 90px)', 'background': _BG})


# ── 浮標 QC 頁籤 layout ───────────────────────────────────────────────────
def _buoy_tab() -> html.Div:
    """
    浮球/浮標 QC 頁籤（R7 唯讀版）。
    左側 8 張感測器圖表（垂直滾動），右側狀態面板。

    Graph id 對照：
      buoy-graph-wave1      wave1（浮球波浪）
      buoy-graph-wind       wind（浮標風速）
      buoy-graph-curr       curr（浮標海流）
      buoy-graph-wave       wave（浮標波浪）
      buoy-graph-pres1      pres1（浮標氣壓）
      buoy-graph-stemp-air  stemp1 z=-3（氣溫）
      buoy-graph-stemp-s0   stemp1 z=0（海溫1）
      buoy-graph-stemp-s1   stemp1 z=1（海溫2）

    bundle key 約定（data_engine.py 組裝 buoy bundle 時須符合）：
      b['wave1_df']     pd.DataFrame or None
      b['wind_df']      pd.DataFrame or None
      b['curr_df']      pd.DataFrame or None
      b['wave_df']      pd.DataFrame or None
      b['pres1_df']     pd.DataFrame or None
      b['stemp1_dfs']   list of (df, z_int)；無資料時為 []
    若 data_engine.py 使用不同 key，修改 render_buoy 中對應 .get() 參數即可。
    """
    def _g(gid: str) -> dcc.Graph:
        return dcc.Graph(
            id=gid,
            config={
                'modeBarButtonsToAdd': ['select2d'],
                'toImageButtonOptions': {'format': 'png', 'scale': 2},
                'scrollZoom': True,
            },
            style={'display': 'none'},   # render_buoy callback 控制顯示/隱藏
        )

    return html.Div([
        # 左側：感測器圖表欄（垂直捲動）
        html.Div([
            _g('buoy-graph-wave1'),
            _g('buoy-graph-wind'),
            _g('buoy-graph-curr'),
            _g('buoy-graph-wave'),
            _g('buoy-graph-pres1'),
            _g('buoy-graph-stemp-air'),
            _g('buoy-graph-stemp-s0'),
            _g('buoy-graph-stemp-s1'),
        ], style={
            'flex': '1', 'overflowY': 'auto',
        }),
        # 右側：狀態面板（R8 加入 SQL 面板）
        html.Div([
            html.Div('💡 浮標 QC 面板', style={
                'fontFamily': '標楷體', 'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                'color': _ACCENT, 'padding': '10px 14px',
                'background': _HEADER,
                'borderBottom': f'1px solid {_BORDER}',
            }),
            # ── 2026-11-20：波浪自動檢查標記（只標示，不改 qc；規則見 buoy_checks.py）──
            dcc.Checklist(
                id='buoy-autocheck-toggle',
                options=[{'label': ' 顯示波浪自動檢查標記', 'value': 'on'}],
                value=[],
                inputStyle={'marginRight': '6px'},
                labelStyle={
                    'display': 'block', 'fontFamily': '標楷體',
                    'fontSize': _FS_BODY, 'color': _TEXT,
                    'padding': '2px 0', 'cursor': 'pointer',
                },
                style={'padding': '8px 18px 0'},
            ),
            html.Div('○ 不通過　◇ 可疑（只標示，不改 qc；✕ 是資料庫裡 qc 不是 Q）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _TEXT,
                'opacity': '0.7', 'padding': '0 18px 4px',
            }),
            html.Div(id='buoy-status', style={
                'padding': '10px 14px', 'color': _TEXT,
                'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                'whiteSpace': 'pre-wrap',
            }),
            # html.Div('SQL 產生功能（R8 加入）', style={
            #     'padding': '10px 14px', 'color': _TEXT, 'opacity': '0.5',
            #     'fontFamily': '標楷體', 'fontSize': _FS_SMALL,
            # }),
            # ── R8：QC 旗標更新（Mode 1）────────────────────────────────
            html.Div('▸ QC 旗標更新（Mode 1）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            html.Div([
                html.Span('新旗標', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                    'color': _TEXT, 'marginRight': '8px',
                }),
                dcc.Input(
                    id='buoy-qc-new', value='X', type='text',
                    style={
                        'width': '52px', 'fontFamily': '標楷體',
                        'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                        'background': _ENTRY, 'color': _TEXT,
                        'border': f'1px solid {_BORDER}',
                        'padding': '3px 6px', 'textAlign': 'center',
                    },
                ),
                html.Span('舊旗標', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                    'color': _TEXT, 'margin': '0 8px 0 16px',
                }),
                dcc.Input(
                    id='buoy-qc-old', value='Q', type='text',
                    style={
                        'width': '52px', 'fontFamily': '標楷體',
                        'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                        'background': _ENTRY, 'color': _TEXT,
                        'border': f'1px solid {_BORDER}',
                        'padding': '3px 6px', 'textAlign': 'center',
                    },
                ),
            ], style={
                'padding': '4px 18px 2px',
                'display': 'flex', 'alignItems': 'center',
            }),
            html.Div('X=異常　Q=合格（舊）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _TEXT,
                'opacity': '0.7', 'padding': '0 18px 6px',
            }),
            html.Div('▸ SQL 輸出（可編輯複製）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.Textarea(
                id='buoy-sql-output', value='',
                style={
                    'width': 'calc(100% - 28px)', 'height': '200px',
                    'fontFamily': 'Consolas, "Courier New", monospace',
                    'fontSize': _FS_SMALL, 'background': _ENTRY,
                    'color': '#A8D8A8',
                    'border': f'1px solid {_BORDER}',
                    'padding': '6px 8px', 'margin': '4px 14px 6px',
                    'resize': 'vertical', 'lineHeight': '1.5',
                    'outline': 'none',
                },
            ),
            html.Div(
                '⚠ 多站查詢時 SQL 僅對應第一站；建議單站查詢後再執行 QC。',
                style={
                    'fontFamily': '標楷體', 'fontSize': _FS_TINY, 'color': _TEXT,
                    'opacity': '0.6', 'padding': '0 14px 8px', 'lineHeight': '1.5',
                },
            ),
        ], style={
            'width': '360px', 'minWidth': '360px',
            'background': _PANEL,
            'borderLeft': f'1px solid {_BORDER}',
            'display': 'flex', 'flexDirection': 'column',
            'overflowY': 'auto',
        }),
    ], style={'display': 'flex', 'height': 'calc(100vh - 90px)'}) 

def _tideenv_tab() -> html.Div:
    """
    潮位站環境參數 QC 頁籤（wind Z='6'、stemp6 Z='0'，2026-09 新增）。

    圖表函式直接重用 build_buoy_figure.py：wind 是同一張表（潮位站 Z='6'，
    浮標 Z IN ('2','3')），繪圖邏輯通用；stemp6 展開後（freq='6min'）欄位
    與 stemp1 展開後相同（TIME/T/QC），直接共用 build_stemp1_figure(df, z=0)。
    不另開 build_tide_env_figure.py。

    2026-09-11 起氣溫（Z='-3'）與氣壓（pres6）比照浮標一併納入 QC，
    原本排除這兩項的政策限制已取消。

    Graph id 對照：
      tideenv-graph-wind      wind（潮位站 Z='6'）
      tideenv-graph-stemp6    stemp6 Z='0'（海溫）
      tideenv-graph-stemp6-air  stemp6 Z='-3'（氣溫）
      tideenv-graph-pres6       pres6（氣壓）

    bundle key 約定（data_engine.fetch_bundle，_kind=='6' 時填入）：
      b['wind_z6_df']     pd.DataFrame or None
      b['stemp6_z0_df']   pd.DataFrame or None
      b['stemp6_zm3_df']   pd.DataFrame or None
      b['pres6_df']        pd.DataFrame or None
    """
    def _g(gid: str) -> dcc.Graph:
        return dcc.Graph(
            id=gid,
            config={
                'modeBarButtonsToAdd': ['select2d'],
                'toImageButtonOptions': {'format': 'png', 'scale': 2},
                'scrollZoom': True,
            },
            style={'display': 'none'},
        )

    return html.Div([
        html.Div([
            _g('tideenv-graph-wind'),
            _g('tideenv-graph-stemp6'),
            _g('tideenv-graph-stemp6-air'),
            _g('tideenv-graph-pres6'),
        ], style={'flex': '1', 'overflowY': 'auto'}),
        html.Div([
            html.Div('💡 潮位站環境 QC 面板', style={
                'fontFamily': '標楷體', 'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                'color': _ACCENT, 'padding': '10px 14px',
                'background': _HEADER,
                'borderBottom': f'1px solid {_BORDER}',
            }),
            html.Div(id='tideenv-status', style={
                'padding': '10px 14px', 'color': _TEXT,
                'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                'whiteSpace': 'pre-wrap',
            }),
            html.Div('▸ QC 旗標更新（Mode 1）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            html.Div([
                html.Span('新旗標', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                    'color': _TEXT, 'marginRight': '8px',
                }),
                dcc.Input(
                    id='tideenv-qc-new', value='X', type='text',
                    style={
                        'width': '52px', 'fontFamily': '標楷體',
                        'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                        'background': _ENTRY, 'color': _TEXT,
                        'border': f'1px solid {_BORDER}',
                        'padding': '3px 6px', 'textAlign': 'center',
                    },
                ),
                html.Span('舊旗標', style={
                    'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                    'color': _TEXT, 'margin': '0 8px 0 16px',
                }),
                dcc.Input(
                    id='tideenv-qc-old', value='Q', type='text',
                    style={
                        'width': '52px', 'fontFamily': '標楷體',
                        'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                        'background': _ENTRY, 'color': _TEXT,
                        'border': f'1px solid {_BORDER}',
                        'padding': '3px 6px', 'textAlign': 'center',
                    },
                ),
            ], style={
                'padding': '4px 18px 2px',
                'display': 'flex', 'alignItems': 'center',
            }),
            html.Div('▸ SQL 輸出（可編輯複製）', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.Textarea(
                id='tideenv-sql-output', value='',
                style={
                    'width': 'calc(100% - 28px)', 'height': '200px',
                    'fontFamily': 'Consolas, "Courier New", monospace',
                    'fontSize': _FS_SMALL, 'background': _ENTRY,
                    'color': '#A8D8A8',
                    'border': f'1px solid {_BORDER}',
                    'padding': '6px 8px', 'margin': '4px 14px 6px',
                    'resize': 'vertical', 'lineHeight': '1.5',
                    'outline': 'none',
                },
            ),
            html.Div(
                '⚠ 多站查詢時 SQL 僅對應第一站；建議單站查詢後再執行 QC。',
                style={
                    'fontFamily': '標楷體', 'fontSize': _FS_TINY, 'color': _TEXT,
                    'opacity': '0.6', 'padding': '0 14px 8px', 'lineHeight': '1.5',
                },
            ),
        ], style={
            'width': '360px', 'minWidth': '360px',
            'background': _PANEL,
            'borderLeft': f'1px solid {_BORDER}',
            'display': 'flex', 'flexDirection': 'column',
            'overflowY': 'auto',
        }),
    ], style={'display': 'flex', 'height': 'calc(100vh - 90px)'})
# ── VdC 散佈圖頁籤 layout ─────────────────────────────────────────────────
def _vdc_tab() -> html.Div:
    """
    VdC 散佈圖頁籤（R9 接通）。
    左側圖表（flex:3，可垂直捲動）＋ 右側控制面板（360px）。

    副儀器類型 vdc-diff-type → render_vdc → build_vdc_figure()。
    zoom-range-store（水位圖縮放範圍）同步至 VdC 時間篩選：
        store 格式 {'xmin','xmax'} 在 render_vdc 轉為 {'x_start','x_end'}。
    vdc-stats-panel 顯示各站統計（筆數、平均差、標準差、回歸斜率、R²）。
    VdC 只對潮位站有意義（需 tide_meta + 多儀器差值欄）。
    """
    return html.Div([
        # 左側圖表（可垂直捲動，多站高圖時 overflow）
        html.Div([
            dcc.Graph(
                id='vdc-graph',
                config={
                    'scrollZoom': True,
                    'toImageButtonOptions': {'format': 'png', 'scale': 2},
                },
                style={'width': '100%'},
            ),
        ], style={'flex': '3', 'overflowY': 'auto', 'minHeight': '80vh'}),
        # 右側控制面板
        html.Div([
            html.Div('💡 VdC 散佈圖控制', style={
                'fontFamily': '標楷體', 'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                'color': _ACCENT, 'padding': '10px 14px',
                'background': _HEADER,
                'borderBottom': f'1px solid {_BORDER}',
            }),
            html.Div(id='vdc-status', style={
                'padding': '10px 14px', 'color': _TEXT,
                'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                'whiteSpace': 'pre-wrap',
            }),
            html.Div('▸ 副儀器類型', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.RadioItems(
                id='vdc-diff-type',
                options=[
                    {'label': ' 自動（優先雷達式）', 'value': 'auto'},
                    {'label': ' 雷達式', 'value': '雷達式'},
                    {'label': ' 壓力式', 'value': '壓力式'},
                ],
                value='auto',
                inputStyle={'marginRight': '6px'},
                labelStyle={
                    'display': 'block', 'fontFamily': '標楷體',
                    'fontSize': _FS_BODY, 'color': _TEXT,
                    'padding': '2px 0', 'cursor': 'pointer',
                },
                style={'padding': '4px 18px 6px'},
            ),
            html.Div('▸ 統計結果', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            html.Div(
                id='vdc-stats-panel',
                style={
                    'padding': '6px 14px', 'color': _TEXT,
                    'fontFamily': '標楷體', 'fontSize': _FS_SMALL,
                    'lineHeight': '1.6', 'overflowY': 'auto',
                    'flex': '1',
                },
            ),
        ], style={
            'width': '360px', 'minWidth': '360px',
            'background': _PANEL,
            'borderLeft': f'1px solid {_BORDER}',
            'display': 'flex', 'flexDirection': 'column',
            'overflowY': 'auto',
        }),
    ], style={'display': 'flex', 'height': 'calc(100vh - 90px)'})
# ── 暴潮偏差互動頁籤 layout ───────────────────────────────────────────────
def _surge_tab() -> html.Div:
    """
    暴潮偏差互動頁籤（R9 接通）。
    左側圖表（flex:3，可垂直捲動）＋ 右側控制面板（360px）。

    build_surge_interactive_figure() 每站 480px 高，多站可垂直捲動。
    不跟隨 zoom-range-store（颱風報告需完整事件期間，見 SYSTEM.md §11）。
    未選颱風時標題顯示「暴潮偏差（未指定颱風）」，颱風色帶不繪製。
    pred_source：auto 優先 pred_a（天文潮分析），無則 pred_h（調和預報）。
    legend_mode：shared 全圖共用圖例置底；individual 各子圖內嵌右上角。
    """
    return html.Div([
        # 左側圖表（可垂直捲動）
        html.Div([
            dcc.Graph(
                id='surge-graph',
                config={
                    'scrollZoom': True,
                    'toImageButtonOptions': {'format': 'png', 'scale': 2},
                },
                style={'width': '100%'},
            ),
        ], style={'flex': '3', 'overflowY': 'auto'}),
        # 右側控制面板
        html.Div([
            html.Div('💡 暴潮偏差控制', style={
                'fontFamily': '標楷體', 'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
                'color': _ACCENT, 'padding': '10px 14px',
                'background': _HEADER,
                'borderBottom': f'1px solid {_BORDER}',
            }),
            html.Div(id='surge-status', style={
                'padding': '10px 14px', 'color': _TEXT,
                'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                'whiteSpace': 'pre-wrap',
            }),
            html.Div('▸ 預報來源', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.RadioItems(
                id='surge-pred-source',
                options=[
                    {'label': ' 自動（優先 pred_a）', 'value': 'auto'},
                    {'label': ' 天文潮分析 pred_a', 'value': 'pred_a'},
                    {'label': ' 調和預報 pred_h', 'value': 'pred_h'},
                ],
                value='auto',
                inputStyle={'marginRight': '6px'},
                labelStyle={
                    'display': 'block', 'fontFamily': '標楷體',
                    'fontSize': _FS_BODY, 'color': _TEXT,
                    'padding': '2px 0', 'cursor': 'pointer',
                },
                style={'padding': '4px 18px 6px'},
            ),
            html.Div('▸ 圖例模式', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.RadioItems(
                id='surge-legend-mode',
                options=[
                    {'label': ' 共用圖例（置底）', 'value': 'shared'},
                    {'label': ' 分組圖例（各子圖右上角）', 'value': 'individual'},
                ],
                value='shared',
                inputStyle={'marginRight': '6px'},
                labelStyle={
                    'display': 'block', 'fontFamily': '標楷體',
                    'fontSize': _FS_BODY, 'color': _TEXT,
                    'padding': '2px 0', 'cursor': 'pointer',
                },
                style={'padding': '4px 18px 6px'},
            ),
            # === 新增：Y軸顯示模式切換 ===
            html.Div('▸ Y 軸範圍模式', style={
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _ACCENT,
                'padding': '8px 14px 2px', 'borderTop': f'1px solid {_BORDER}',
            }),
            dcc.RadioItems(
                id='surge-yaxis-mode',
                options=[
                    {'label': ' 動態範圍 (最適留白)', 'value': 'auto'},
                    {'label': ' 固定範圍 (比較極端值)', 'value': 'fixed'},
                ],
                value='auto',
                inputStyle={'marginRight': '6px'},
                labelStyle={
                    'display': 'block', 'fontFamily': '標楷體',
                    'fontSize': _FS_BODY, 'color': _TEXT,
                    'padding': '2px 0', 'cursor': 'pointer',
                },
                style={'padding': '4px 18px 6px'},
            ),
            # ============================
            html.Div(id='surge-ty-label', style={
                'padding': '10px 14px', 'color': _TEXT, 'opacity': '0.8',
                'fontFamily': '標楷體', 'fontSize': _FS_SMALL,
                'borderTop': f'1px solid {_BORDER}',
            }),
        ], style={
            'width': '360px', 'minWidth': '360px',
            'background': _PANEL,
            'borderLeft': f'1px solid {_BORDER}',
            'display': 'flex', 'flexDirection': 'column',
            'overflowY': 'auto',
        }),
    ], style={'display': 'flex', 'height': 'calc(100vh - 90px)'})
# ── 事件報告頁籤 layout（R10）──────────────────────────────────────────────
def _report_tab() -> html.Div:
    """
    事件報告頁籤（R10 接通）。白底 PNG 下載，暴潮偏差 + 水位時序。

    每站各自一張暴潮偏差圖 + 一張水位圖，各自搭配一個下載按鈕
    （dcc.Download 一次對應一個觸發 id，見 download_water_report /
    download_surge_report；不做批次 zip，見 R10 開發紀錄）。

    report-list 的內容由 render_report callback 動態產生（依站數變化），
    每站卡片內含動態 id（pattern-matching {'type':..., 'index': stid}）。
    不跟隨 zoom-range-store，與 event-surge 相同理由（SYSTEM.md §11）。
    只列潮位站（tide_meta 有值），浮標站這輪不處理。
    """
    return html.Div([
        html.Div('💡 事件報告（白底 PNG 下載）', style={
            'fontFamily': '標楷體', 'fontSize': _FS_PANEL_H, 'fontWeight': 'bold',
            'color': _ACCENT, 'padding': '10px 14px',
            'background': _HEADER,
            'borderBottom': f'1px solid {_BORDER}',
        }),
        # html.Div(id='report-status', style={
        #     'padding': '10px 20px', 'color': _TEXT,
        #     'fontFamily': '標楷體', 'fontSize': _FS_BODY,
        # }),
        # html.Div(id='report-list', style={'padding': '0 20px 40px'}),
        html.Div(id='report-status', style={
            'padding': '10px 20px', 'color': _TEXT,
            'fontFamily': '標楷體', 'fontSize': _FS_BODY,
        }),
        html.Div([
            html.Button(
                '📦 打包下載本頁全部站點 PNG（zip）', id='dl-all-btn', n_clicks=0,
                style={
                    'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                    'background': _PANEL, 'color': _ACCENT,
                    'border': f'1px solid {_ACCENT}',
                    'padding': '6px 16px', 'cursor': 'pointer', 'borderRadius': '3px',
                },
            ),
            html.Span(
                '　站數多時可能需要等待數秒到數十秒，請耐心等候（無進度條）',
                style={'fontFamily': '標楷體', 'fontSize': _FS_SMALL, 'color': _TEXT, 'opacity': '0.6'},
            ),
            dcc.Loading(
                dcc.Download(id='dl-all-zip'),
                type='circle', color=_ACCENT,
            ), # 加入Loading轉圈圈。
        ], style={'padding': '0 20px 12px'}),
        html.Div(id='report-list', style={'padding': '0 20px 40px'}),
    ], style={
        'height': 'calc(100vh - 90px)',
        'overflowY': 'auto',
        'background': _BG,
    })


_REPORT_BTN_STYLE = {
    'fontFamily': '標楷體', 'fontSize': _FS_BODY,
    'background': _ACCENT, 'color': 'white',
    'border': 'none', 'padding': '6px 16px',
    'cursor': 'pointer', 'borderRadius': '3px',
    'margin': '8px 0 24px',
}


# def _report_card(b: dict, land_range, typhoon_info: dict, thresholds_map: dict) -> html.Div:
#     """單站報告卡片：暴潮偏差白底圖 + 水位白底圖，各自預覽 + 下載鈕。"""
def _report_card(b: dict, land_range, typhoon_info: dict, thresholds_map: dict, zoom_range: dict | None = None) -> html.Div:
    """
    單站報告卡片：暴潮偏差白底圖 + 水位白底圖，各自預覽 + 下載鈕。

    zoom_range（R10 新增）：{'x_start':..., 'x_end':...} 或 None。
    只影響水位白底圖；暴潮偏差圖維持完整事件期間，見 render_report 說明。
    """
    stid   = b.get('stid', '')
    stname = b.get('stname', stid)

    children = [
        html.Div(f'📍 {stname}（{stid}）', style={
            'fontFamily': '標楷體', 'fontSize': '15px', 'fontWeight': 'bold',
            'color': _ACCENT, 'padding': '14px 0 6px',
        }),
    ]

    # --- 暴潮偏差白底圖 ---
    try:
        # thresholds  = (thresholds_map or {}).get(stid)
        # surge_fig   = build_surge_report_figure(b, typhoon_info or {}, thresholds)
        # children.append(html.Div([
        #     dcc.Graph(
        #         figure=surge_fig,
        thresholds  = (thresholds_map or {}).get(stid)
        surge_fig   = build_surge_report_figure(b, typhoon_info or {}, thresholds)
        surge_fig.update_layout(dragmode=False, legend_font_size=_FS_LEGEND)  # R10：預覽縮放不影響下載，關掉避免誤導
        apply_row_hover(surge_fig)   # 2026-11-20 統一游標規則（只影響預覽；下載 PNG 另外產圖）
        surge_fig.update_xaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        surge_fig.update_yaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        children.append(html.Div([
            html.Div('🔍 此為下載預覽（暴潮偏差圖固定顯示完整颱風事件期間，不跟隨縮放）',
                      style={'color': _TEXT, 'opacity': '0.5', 'fontFamily': '標楷體',
                             'fontSize': _FS_TINY, 'padding': '0 0 4px'}),
            dcc.Graph(
                figure=surge_fig,
                config={'toImageButtonOptions': {'format': 'png', 'scale': 2}},
                style={'height': '480px'},
            ),
            html.Button(
                '下載暴潮偏差 PNG',
                id={'type': 'dl-surge-btn', 'index': stid},
                n_clicks=0, style=_REPORT_BTN_STYLE,
            ),
            dcc.Download(id={'type': 'dl-surge-dl', 'index': stid}),
        ]))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        children.append(html.Div(f'暴潮偏差圖繪製失敗：{exc}', style={
            'color': '#e74c3c', 'fontFamily': '標楷體', 'fontSize': _FS_BODY,
            'padding': '8px 0',
        }))

    # # --- 水位白底圖 ---
    # try:
    #     water_fig = build_water_report_figure([b], land_range=land_range)
    #     children.append(html.Div([
    #         dcc.Graph(
    #             figure=water_fig,
    #             config={'toImageButtonOptions': {'format': 'png', 'scale': 2}},
    #             style={'height': '600px'},
    #         ),
    #         html.Button(
    #             '下載水位 PNG',
    #             id={'type': 'dl-water-btn', 'index': stid},
    #             n_clicks=0, style=_REPORT_BTN_STYLE,
    #         ),
    #         dcc.Download(id={'type': 'dl-water-dl', 'index': stid}),
    #     ]))
    # except Exception as exc:
    # --- 水位白底圖（R10：套用 zoom_range）---
    try:
        # water_fig = build_water_report_figure([b], land_range=land_range, zoom_range=zoom_range)
        # water_children = []
        # if zoom_range:
        #     water_children.append(html.Div(
        #         f'🔍 已套用縮放範圍：{zoom_range["x_start"]} ～ {zoom_range["x_end"]}',
        water_fig = build_water_report_figure([b], land_range=land_range, zoom_range=zoom_range)
        water_fig.update_layout(dragmode=False, legend_font_size=_FS_LEGEND)  # R10：預覽縮放不影響下載，關掉避免誤導
        apply_row_hover(water_fig)   # 2026-11-20 統一游標規則（只影響預覽；下載 PNG 另外產圖）
        water_fig.update_xaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        water_fig.update_yaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        water_children = []
        if zoom_range:
            water_children.append(html.Div(
                f'🔍 已套用縮放範圍：{zoom_range["x_start"]} ～ {zoom_range["x_end"]}'
                '（此預覽無法再縮放；如需改範圍請回「水位時序＋QC」頁籤）',
                style={
                    'color': _TEXT, 'opacity': '0.7', 'fontFamily': '標楷體',
                    'fontSize': _FS_SMALL, 'padding': '0 0 4px',
        #         },
        #     ))
        # water_children += [
                },
            ))
        else:
            water_children.append(html.Div(
                '🔍 此為下載預覽（完整時段；如需下載特定時間範圍，請回「水位時序＋QC」頁籤縮放後再切回本頁）',
                style={'color': _TEXT, 'opacity': '0.5', 'fontFamily': '標楷體',
                       'fontSize': _FS_TINY, 'padding': '0 0 4px'},
            ))
        water_children += [
            dcc.Graph(
                figure=water_fig,
                config={'toImageButtonOptions': {'format': 'png', 'scale': 2}},
                style={'height': '600px'},
            ),
            html.Button(
                '下載水位 PNG',
                id={'type': 'dl-water-btn', 'index': stid},
                n_clicks=0, style=_REPORT_BTN_STYLE,
            ),
            dcc.Download(id={'type': 'dl-water-dl', 'index': stid}),
        ]
        children.append(html.Div(water_children))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        children.append(html.Div(f'水位圖繪製失敗：{exc}', style={
            'color': '#e74c3c', 'fontFamily': '標楷體', 'fontSize': _FS_BODY,
            'padding': '8px 0',
        }))

    return html.Div(children, style={
        'borderBottom': f'1px solid {_BORDER}', 'marginBottom': '10px',
    })
def _render_surge_png(b: dict, typhoon_info: dict, thresholds: dict | None) -> bytes:
    """暴潮偏差白底圖 → PNG bytes（kaleido）。固定 width/height，避免下載圖 x 軸標籤被壓縮。"""
    fig = build_surge_report_figure(b, typhoon_info or {}, thresholds)
    # fig.update_layout(dragmode=False, legend_font_size=_FS_LEGEND)  # 這三行是VScode自己加的，不知道要不要用，先註解起來
    # fig.update_xaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
    # fig.update_yaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
    return fig.to_image(format='png', scale=2, width=1600, height=700)


def _render_water_png(b: dict, land_range, zoom_range: dict | None) -> bytes:
    """水位白底圖 → PNG bytes（kaleido）。width 固定加大到 1900，長時間範圍也不會標籤重疊。"""
    fig = build_water_report_figure([b], land_range=land_range, zoom_range=zoom_range)
    # fig.update_layout(dragmode=False, legend_font_size=_FS_LEGEND)  # 這三行是VScode自己加的，不知道要不要用，先註解起來
    # fig.update_xaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
    # fig.update_yaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
    return fig.to_image(format='png', scale=2, width=1900, height=600)
# ── R6 工具函式（SQL 產生器輔助）────────────────────────────────────────────
 
 
def _resolve_stid(curve_num: int, trace_meta, bundles: list) -> str:
    """
    Plotly curveNumber → stid。
    trace_meta 格式容錯：list（索引映射）/ dict（int 或 str 鍵）/ None。
    Fallback：bundles[0]['stid']（單站查詢最常見情況）。
    """
    if trace_meta is not None:
        if isinstance(trace_meta, list):
            try:
                s = trace_meta[curve_num]
                # 2026-11-20 修正：build_water_figure 的 trace_meta 元素是 dict
                # {'stid':..., 'field_type':...}（V2 起），原本直接 str(dict) 塞進 SQL，
                # 產生 WHERE STID = '{'stid': '1275', ...}'，對不到任何資料。
                if isinstance(s, dict):
                    s = s.get('stid')
                if s:
                    return str(s)
            except (IndexError, TypeError):
                pass
        elif isinstance(trace_meta, dict):
            s = (trace_meta.get(curve_num) or
                 trace_meta.get(str(curve_num)))
            if isinstance(s, dict):
                s = s.get('stid')
            if s:
                return str(s)
    return bundles[0].get('stid', '') if bundles else ''
 
 
def _group_by_stid(points: list, trace_meta, bundles: list) -> dict:
    """
    selectedData['points'] 依 stid 分組，回傳 {stid: [pts]}。
    trace_meta 為 None 時所有點歸入 bundles[0]['stid']（單站 fallback）。
    多站查詢若 build_water_figure 未回傳 trace_meta，SQL 只會對應到第一站。
    """
    result: dict = {}
    for p in points:
        stid = _resolve_stid(p.get('curveNumber', 0), trace_meta, bundles)
        result.setdefault(stid, []).append(p)
    return result
 
 
def _drop_autoqc_points(selected_data, trace_meta):
    """selectedData 去掉自動檢核疊點（trace_meta field_type='autoqc'）；range（框選邊界）保留。"""
    if not selected_data or not isinstance(trace_meta, list):
        return selected_data
    skip = {i for i, m in enumerate(trace_meta)
            if isinstance(m, dict) and m.get('field_type') == 'autoqc'}
    if not skip:
        return selected_data
    out = dict(selected_data)
    out['points'] = [p for p in selected_data.get('points', []) if p.get('curveNumber') not in skip]
    return out


def _extract_sel_dict(selected_data) -> dict:
    """
    Plotly selectedData → {'x': [...], 'y': [...]} 格式，供 build_mode1_sql。
    - box select：直接取 selectedData['range']（精確框線）
    - lasso select：以 points 的 min/max 反推
    """
    if not selected_data:
        return {}
    # if 'range' in selected_data:
    #     r = selected_data['range']
    #     return {'x': r.get('x', []), 'y': r.get('y', [])}
    if 'range' in selected_data:
        r = selected_data['range']
        # 多子圖 range key：subplot 1='x'，subplot 2='x2'，依此類推
        xs = next((v for k, v in r.items() if k.startswith('x')), [])
        ys = next((v for k, v in r.items() if k.startswith('y')), [])
        sel: dict = {}
        if xs:
            sel['x'] = xs
        if ys:
            sel['y'] = ys
        return sel
    pts = selected_data.get('points', [])
    xs  = [p['x'] for p in pts if 'x' in p]
    ys  = [p['y'] for p in pts if p.get('y') is not None]
    sel: dict = {}
    if xs:
        sel['x'] = xs
    if ys:
        sel['y'] = ys
    return sel
 
 
# ── URL key 解析 ──────────────────────────────────────────────────────────
def _extract_key(search: str | None) -> str | None:
    """
    從 dcc.Location search（例如 '?key=1176_20250724153000'）取出 key 值。
    Session 隔離設計：main_app 每次查詢開啟帶 key 的 URL，
    讓每個瀏覽器分頁鎖定自己的查詢結果，不受後續查詢影響。
    """
    if not search:
        return None
    try:
        params = parse_qs(search.lstrip('?'))
        lst = params.get('key', [])
        return lst[0] if lst else None
    except Exception:
        return None
 
 
# ── Layout ────────────────────────────────────────────────────────────────
app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
 
    # 共用 Store / Interval
    dcc.Interval(id='bundle-poll', interval=500, n_intervals=0),
    dcc.Store(id='bundle-key-store', data=None),
    dcc.Store(id='zoom-range-store', data=None),    # R6 接：water zoom → VdC
    dcc.Store(id='trace-meta-store', data=None),    # R6 接：Box Select → STID 反查
    # html.Div(id='resize-dummy', style={'display': 'none'}),  # R12：純觸發用，無實際內容
 
    # 頂部標題列
    html.Div([
        html.Span('🌊 海洋動力診斷儀表板 V2', style={
            'fontFamily': '標楷體', 'fontSize': _FS_SYS_TITLE,
            'color': _ACCENT, 'fontWeight': 'bold',
        }),
    ], style={
        'background': _HEADER, 'padding': '10px 20px',
        'borderBottom': f'1px solid {_BORDER}',
    }),
 
    # 三工作區主頁籤
    dcc.Tabs(id='ws-tabs', value='ws-qc', children=[
 
        # ── QC 工作台 ────────────────────────────────────────────────────
        dcc.Tab(label='QC 工作台', value='ws-qc',
                style=_WT, selected_style=_WTA,
                children=[
            dcc.Tabs(id='qc-tabs', value='qc-water',
                     colors={'background': _PANEL, 'primary': _ACCENT,
                             'border': _BORDER},
                     children=[
                dcc.Tab(label='水位時序 + QC', value='qc-water',
                        style=_ST, selected_style=_STA,
                        children=[_water_tab()]),
                dcc.Tab(label='VdC 散佈圖', value='qc-vdc',
                        style=_ST, selected_style=_STA,
                        children=[_vdc_tab()]),
                dcc.Tab(label='潮位站環境 QC', value='qc-tideenv',
                        style=_ST, selected_style=_STA,
                        children=[_tideenv_tab()]),
                dcc.Tab(label='浮球/浮標 QC', value='qc-buoy',
                        style=_ST, selected_style=_STA,
                        children=[_buoy_tab()]),
            ]),
        ]),
 
        # ── 診斷總覽 ──────────────────────────────────────────────────────
        dcc.Tab(label='診斷總覽', value='ws-diag',
                style=_WT, selected_style=_WTA,
                children=[
            dcc.Tabs(id='diag-tabs', value='diag-ocean',
                     colors={'background': _PANEL, 'primary': _ACCENT,
                             'border': _BORDER},
                     children=[
                dcc.Tab(label='海洋參數（唯讀）', value='diag-ocean',
                        style=_ST, selected_style=_STA,
                        children=[_diag_tab()]),
                dcc.Tab(label='潮位自動檢核（唯讀）', value='diag-qcflag',
                        style=_ST, selected_style=_STA,
                        children=[_qcflag_tab()]),
            ]),
        ]),
 
        # ── 事件報告 ──────────────────────────────────────────────────────
        dcc.Tab(label='事件報告', value='ws-event',
                style=_WT, selected_style=_WTA,
                children=[
            dcc.Tabs(id='event-tabs', value='event-surge',
                     colors={'background': _PANEL, 'primary': _ACCENT,
                             'border': _BORDER},
                     children=[
                dcc.Tab(label='暴潮偏差（互動）', value='event-surge',
                        style=_ST, selected_style=_STA,
                        children=[_surge_tab()]),
                dcc.Tab(label='事件報告（白底）', value='event-report',
                        style=_ST, selected_style=_STA,
                        children=[_report_tab()]),
            ]),
        ]),
    ]),
 
], style={'background': _BG, 'minHeight': '100vh', 'fontFamily': '標楷體'})
 
 
# # ── R12：分頁切換時觸發瀏覽器端 Plotly resize（取代 R12 初版的整包重建）───
# # 只在瀏覽器做 Plotly.Plots.resize()，不回 Python 重算 figure，
# # 成本遠低於重新呼叫 build_*_figure()，適合高站數查詢。
# app.clientside_callback(
#     """
#     function(ws_tab, qc_tab, diag_tab, event_tab) {
#         setTimeout(function() {
#             document.querySelectorAll('.js-plotly-plot').forEach(function(gd) {
#                 if (gd.offsetWidth > 0 && gd.offsetHeight > 0 && window.Plotly) {
#                     try { window.Plotly.Plots.resize(gd); } catch (e) {}
#                 }
#             });
#         }, 100);
#         return '';
#     }
#     """,
#     Output('resize-dummy', 'children'),
#     Input('ws-tabs', 'value'),
#     Input('qc-tabs', 'value'),
#     Input('diag-tabs', 'value'),
#     Input('event-tabs', 'value'),
# )
# ── Callbacks ─────────────────────────────────────────────────────────────

_RENDER_CACHE_MAX = 10   # 與 dash_bridge.MAX_CACHE_SIZE 一致

def _cache_put(cache: dict, key, value):
    """簡易 FIFO 快取寫入，超過上限丟棄最舊一筆。"""
    if len(cache) >= _RENDER_CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[key] = value
 
@app.callback(
    Output('bundle-key-store', 'data'),
    Input('bundle-poll', 'n_intervals'),
    State('bundle-key-store', 'data'),
)
def poll_bundle(_, current_key):
    """
    每 500ms 輪詢一次 bridge。偵測到新 key 時更新 bundle-key-store，
    觸發 render_water。
    dcc.Store 是 per-session 的，各瀏覽器分頁各自維護自己的 store 值。
    """
    latest = dash_bridge.bridge.get_latest_key()
    if latest is not None and latest != current_key:
        return latest
    return no_update

_water_fig_cache: dict = {}

def _load_autoqc_marks(tide_bundles: list):
    """自動檢核（試行）彙總 3／4 的點（2026-11-24）→ ({主站: 標記表}, 狀態文字)。
    旗標庫讀取全部在 try 內：沒設定、連不上、讀取失敗只回傳提示文字，水位圖照畫。"""
    miss = qcflag_engine.missing_config()
    if miss:
        return None, ('未設定旗標資料庫，不疊點（水位圖與框選不受影響）。\n'
                      f'要使用時在 .env 加上：{"、".join(miss)}。')
    try:
        conn = qcflag_engine.connect()
    except Exception as exc:
        return None, f'旗標資料庫連不上（{os.getenv("QC_DB_HOST")}）：{exc}\n水位圖與框選不受影響。'
    out, lines, no_flag = {}, [], []
    try:
        for b in tide_bundles:
            df = b.get('df')
            meta = b.get('tide_meta') or {}
            if df is None or df.empty or not meta:
                continue
            t = pd.to_datetime(df['Time'])
            beg = t.min().normalize().to_pydatetime()
            end = (t.max().normalize() + pd.Timedelta(days=1)).to_pydatetime()
            parts = []
            for stid, m in sorted(meta.items()):
                marks = qcflag_engine.load_summary_marks(conn, stid, beg, end, df)
                if marks.empty:
                    if not qcflag_engine.stids_with_flags(conn, [stid], beg, end):
                        no_flag.append(stid)
                    continue
                name = str(m.get('stnac') or '').replace('潮位站', '')
                marks['INST'] = f"{name}({stid})-{m.get('type_desc') or ''}"
                parts.append(marks)
                n4 = int((marks['LEVEL'] == 4).sum())
                n3 = int((marks['LEVEL'] == 3).sum())
                extra = (f'；另 {marks.attrs["no_obs"]} 筆該時間沒有觀測值、畫不出來'
                         if marks.attrs.get('no_obs') else '')
                lines.append(f'{stid}：不通過 {n4}、可疑 {n3} 筆{extra}')
            if parts:
                out[b['stid']] = pd.concat(parts, ignore_index=True)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return None, f'讀取自動檢核結果失敗：{exc}\n水位圖與框選不受影響。'
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if no_flag:
        lines.append(f'沒有檢核結果（尚未計算）：{"、".join(no_flag)}')
    if not lines:
        lines.append('這段期間沒有可疑或不通過的點。')
    lines.append('筆數為逐 6 分鐘；只標示，不改 qc。各子項細節看「診斷總覽 → 潮位自動檢核（唯讀）」。')
    return out, '\n'.join(lines)


@app.callback(
    Output('water-graph', 'figure'),
    Output('water-status', 'children'),
    Output('trace-meta-store', 'data'),
    Output('autoqc-status', 'children'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    Input('ws-tabs', 'value'),
    Input('qc-tabs', 'value'),
    Input('autoqc-toggle', 'value'),
)
def render_water(store_key, url_search, ws_tab, qc_tab, autoqc=None):
    if ws_tab != 'ws-qc' or qc_tab != 'qc-water':
        return no_update, no_update, no_update, no_update

    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return no_update, '請先在 Tkinter 視窗選站後點「查詢」', no_update, no_update

    autoqc_on = bool(autoqc)
    cache_key = (key, autoqc_on)
    if cache_key in _water_fig_cache:
        return _water_fig_cache[cache_key]

    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update, f'資料不存在（key={key}）', no_update, no_update

    tide_bundles = [b for b in bundles if b.get('tide_meta')]
    buoy_only_names = [b.get('stname', b.get('stid', '?'))
                        for b in bundles if not b.get('tide_meta')]
    names  = [b.get('stname', b.get('stid', '?')) for b in bundles]
    status = f'已載入：{"、".join(names)}'
    if buoy_only_names:
        status += f'（浮標/浮球站請至「浮球/浮標 QC」頁籤查看：{"、".join(buoy_only_names)}）'

    if not tide_bundles:
        empty_fig = {
            'layout': {
                'xaxis': {'visible': False},
                'yaxis': {'visible': False},
                'annotations': [{
                    'text': '此為浮球/浮標測站，資料請至「浮球/浮標 QC」頁籤檢視。',
                    'xref': 'paper', 'yref': 'paper',
                    'showarrow': False,
                    'font': {'size': 18, 'color': '#CCD0D4'}
                }],
                'plot_bgcolor': '#111820',
                'paper_bgcolor': '#111820'
            }
        }
        result = (empty_fig, status, None, '')
        _cache_put(_water_fig_cache, cache_key, result)
        return result

    land_range    = dash_bridge.bridge.get_land_range()
    typhoon_label = dash_bridge.bridge.get_typhoon_label()

    # 自動檢核（試行）疊點：勾選才連旗標庫；失敗（marks=None）時不快取，下次切回或重勾再試
    autoqc_marks, autoqc_msg = (_load_autoqc_marks(tide_bundles) if autoqc_on else ({}, ''))
    cacheable = autoqc_marks is not None

    try:
        built = build_water_figure(tide_bundles, land_range, typhoon_label, autoqc_marks=autoqc_marks)
        fig, trace_meta = built if isinstance(built, tuple) else (built, None)
        # # # 👇 加上這一行來覆寫圖例字體大小 (假設你要設為 14)
        # # fig.update_layout(height=len(tide_bundles) * 350 + 50, legend_font_size=_FS_LEGEND)

        # # 1. 調整整體畫布高度
        # fig.update_layout(height=len(tide_bundles) * 350 + 50)
        
        # # 2. 針對所有測站的圖例，統一塞入 _FS_LEGEND 的字體大小
        #-------------------------------------------------------------
        # build_water_figure() 內部已依各站圖例項目數動態算好 height，
        # 這裡不再用 len(tide_bundles)*350+50 蓋掉，否則圖例會被擠壓/重疊。

        # 針對所有測站的圖例，統一塞入 _FS_LEGEND 的字體大小
        # （dict 是合併語意，只補 font，不影響 build_water_figure 算好的 y/x 定位）
        legend_updates = {}
        for i in range(1, len(tide_bundles) + 1):
            leg_key = "legend" if i == 1 else f"legend{i}"
            legend_updates[leg_key] = dict(font=dict(size=_FS_LEGEND))
        fig.update_layout(**legend_updates)
        
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return no_update, f'繪圖失敗：{exc}', no_update, no_update

    apply_row_hover(fig)      # 2026-11-20 統一游標規則（hover_rows.py）；載體加在最後，不影響 trace_meta 索引
    result = (fig, status, trace_meta, autoqc_msg)
    if cacheable:
        _cache_put(_water_fig_cache, cache_key, result)
    return result

# ── R6 Callbacks ──────────────────────────────────────────────────────────
 
@app.callback(
    Output('zoom-range-store', 'data'),
    Input('water-graph', 'relayoutData'),
    prevent_initial_call=True,
)
def capture_zoom(relayout):
    """
    水位圖縮放/平移 → zoom-range-store。
    R9 VdC tab 讀取此 Store 同步 x 軸範圍（不需兩 tab 共用同一 Graph）。
    只在 xaxis.range 確實改變時才更新；autosize 等其他事件略過。

    R10 新增：雙擊圖表重設縮放時 relayoutData 會回傳
    {'xaxis.autorange': True}（無 range[0]/[1]），這種情況要清空 Store，
    否則 VdC／event-report 會卡在舊的縮放範圍出不來。
    """
    if not relayout:
        return no_update
    if relayout.get('xaxis.autorange') or 'autosize' in relayout:
        return None
    xmin = relayout.get('xaxis.range[0]')
    xmax = relayout.get('xaxis.range[1]')
    if xmin is None and xmax is None:
        return no_update
    return {'xmin': xmin, 'xmax': xmax}
 
 
@app.callback(
    Output('mode2-controls', 'style'),
    Output('mode3-controls', 'style'),
    Input('qc-mode', 'value'),
)
def toggle_mode_ui(mode):
    """依選擇模式顯示/隱藏 Mode 2 / Mode 3 附屬控制區塊。"""
    show = {'display': 'block'}
    hide = {'display': 'none'}
    return (
        show if mode == '2' else hide,
        show if mode == '3' else hide,
    )
 
 
@app.callback(
    Output('mode3-ty-label', 'children'),
    Input('bundle-key-store', 'data'),
)
def update_ty_label(_key):
    """查詢完成後，於 Mode 3 面板顯示目前選取的颱風名稱。"""
    info = dash_bridge.bridge.get_typhoon_info()
    if not info:
        return '（尚未選取颱風；INSERT 以空颱風值產生）'
    ty_id   = info.get('id',    info.get('ID',    ''))
    ty_name = info.get('cname', info.get('CNAME', ''))
    if ty_id or ty_name:
        return f'颱風：{ty_id} {ty_name}'
    return '（颱風資訊欄位為空）'
 
 
@app.callback(
    Output('sql-output', 'value'),
    Input('water-graph', 'selectedData'),
    Input('mode3-btn', 'n_clicks'),
    Input('qc-mode', 'value'),
    State('qc-new-val', 'value'),
    State('mode2-op', 'value'),
    State('mode2-operand', 'value'),
    State('bundle-key-store', 'data'),
    State('trace-meta-store', 'data'),
    prevent_initial_call=True,
)
def generate_sql(selected_data, _n3, mode, new_qc, op, operand, key, trace_meta):
    """
    水位圖框選 → Mode 1/2 SQL；按鈕 → Mode 3 暴潮 INSERT SQL。
 
    觸發邏輯（ctx.triggered_id）：
      'mode3-btn'    → 一律執行 Mode 3，不需框選
      'qc-mode'      → 模式切換；有舊選取則用新模式重算，無選取則 no_update
      'water-graph'  → 依 mode 執行 Mode 1 或 Mode 2
 
    Mode 1 預期輸出（單站）：
      UPDATE tide6
      SET QC = 'X'
      WHERE STID = '1176'
      AND DATATIME BETWEEN '2025-07-24 00:00:00' AND '2025-07-24 23:59:59'
      AND (
             MIN0 BETWEEN 200 AND 300
          OR MIN1 BETWEEN 200 AND 300
          ...
          );
 
    Mode 2 預期輸出（框選兩點落在同一 DATATIME 的 MIN2/MIN3）：
      UPDATE tide6
      SET MIN2 = MIN2 + 5, MIN3 = MIN3 + 5
      WHERE STID = '1176'
       AND DATATIME = '2025-07-24 14:00:00';
    """
    trigger = ctx.triggered_id
 
    if not key:
        return no_update
 
    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update
 
    # ── Mode 3：只在按鈕被點擊時執行 ─────────────────────────────────────
    if trigger == 'mode3-btn':
        typhoon_info = dash_bridge.bridge.get_typhoon_info() or {}
        result = build_mode3_sql(bundles, typhoon_info)
        return result or '-- 無法產生暴潮 SQL（資料不足或颱風未選取）'
 
    if mode == '3':
        return no_update   # 切到 Mode 3 但尚未點按鈕：保持舊輸出
 
    # ── Mode 1 / 2：需要 selectedData ─────────────────────────────────────
    # 2026-11-24：自動檢核（試行）疊點（trace_meta field_type='autoqc'）和觀測點重疊，
    # 框選時會一起被選到 → 先拿掉，SQL 只依觀測值的點產生（Mode 2 才不會重複欄位）。
    selected_data = _drop_autoqc_points(selected_data, trace_meta)
    points = (selected_data or {}).get('points', [])
    if not points:
        return no_update   # 無框選時保持舊輸出，不顯示錯誤訊息
 
    stid_groups = _group_by_stid(points, trace_meta, bundles)
    new_qc_str  = (new_qc or 'X').strip().upper() or 'X'
 
    if mode == '1':
        # Mode 1：用整體框選邊界做 DATATIME BETWEEN + MIN{n} BETWEEN
        # box select 走 selectedData['range']（精確）；lasso 走 points min/max
        sel = _extract_sel_dict(selected_data)
        if not sel.get('x'):
            return no_update
        sqls = []
        for stid in stid_groups:
            if not stid:
                continue
            sql = build_mode1_sql(sel, stid, new_qc_str)
            if sql:
                sqls.append(sql)
        return '\n\n'.join(sqls) if sqls else no_update
 
    elif mode == '2':
        # Mode 2：用每個點的精確 Time 反推 DATATIME + MIN{n} 欄位
        if not op:
            return '-- 請選擇運算符（+  −  ×  ÷）'
        try:
            operand_f = float(operand)
        except (TypeError, ValueError):
            return '-- 請輸入運算數（數值）'
        sqls = []
        for stid, pts in stid_groups.items():
            if not stid:
                continue
            pt_list = [{'x': p['x'], 'y': p.get('y')} for p in pts]
            sql = build_mode2_sql(pt_list, stid, op, operand_f)
            if sql:
                sqls.append(sql)
        return '\n\n'.join(sqls) if sqls else no_update
 
    return no_update
 
 
@app.callback(
    Output('water-graph', 'figure', allow_duplicate=True),
    Input('apply-yaxis-btn', 'n_clicks'),
    State('yaxis-min', 'value'),
    State('yaxis-max', 'value'),
    State('water-graph', 'figure'),  # 新增：讀取目前的圖表狀態
    prevent_initial_call=True,
)
def apply_yaxis(_, ymin, ymax, fig):
    """
    套用 Y 軸範圍（Patch 只修改 layout，不重繪圖形資料）。
    下限和上限均須填寫才執行；任一為 None 則 no_update。
    需要 Dash ≥ 2.9（allow_duplicate 支援）。
    例：ymin=-500, ymax=3000 → 圖 Y 軸鎖定在 [-500, 3000]。
    """
    # if ymin is None or ymax is None or not fig:
    #     return no_update
    # patched = Patch()
    # # 用迴圈掃描 layout，只要是 yaxis 開頭的（yaxis, yaxis2, yaxis3...）一律套用
    # for key in fig.get('layout', {}):
    #     if key.startswith('yaxis'):
    #         patched['layout'][key]['autorange'] = False
    #         patched['layout'][key]['range']     = [ymin, ymax]
    # # patched['layout']['yaxis']['autorange'] = False
    # # patched['layout']['yaxis']['range']     = [ymin, ymax]
    # return patched
    
    # 如果「兩個都沒填」或是沒有圖表，就不動作
    if (ymin is None and ymax is None) or not fig:
        return no_update
        
    patched = Patch()
    for key in fig.get('layout', {}):
        if key.startswith('yaxis'):
            # 取得目前的 Y 軸範圍 (如果原本是自動縮放，可能不會有 range 屬性)
            current_range = fig['layout'][key].get('range', [None, None])
            
            # 如果某個值沒填，就沿用圖表當前範圍的值
            new_ymin = ymin if ymin is not None else current_range[0]
            new_ymax = ymax if ymax is not None else current_range[1]
            
            patched['layout'][key]['autorange'] = False
            patched['layout'][key]['range'] = [new_ymin, new_ymax]
            
    return patched

@app.callback(
    Output('adv-layer-target', 'options'),
    Output('adv-layer-target', 'value'),
    Input('trace-meta-store', 'data'),
    State('adv-layer-target', 'value'),
)
def update_adv_layer_targets(trace_meta, current=None):
    """
    依本次查詢的 trace_meta，列出有進階圖層（lf/ewma/smooth）的儀器清單，
    供「進階圖層目標儀器」選擇，可單獨看某一儀器的 1H平滑／EWMA／
    低頻趨勢線，不被同子圖其他儀器的同類線干擾。
    新查詢時，原選的儀器不在新查詢裡就重置回「全部儀器」（2026-11-24 前是一律重置）。
    """
    base = [{'label': ' 全部儀器', 'value': 'all'}]
    if not trace_meta:
        return base, 'all'

    seen = {}
    for meta in trace_meta:
        meta = meta or {}
        if meta.get('field_type') not in ('lf', 'ewma', 'smooth'):
            continue
        stid = meta.get('stid')
        if not stid or stid in seen:
            continue
        seen[stid] = meta.get('label', stid)

    options = base + [{'label': f' {label}', 'value': stid} for stid, label in seen.items()]
    # 2026-11-24：勾「自動檢核」會重畫同一查詢的圖、trace_meta 也會更新；儀器還在就保留原選擇
    return options, (current if current in seen else 'all')

# ── 進階圖層（低頻趨勢／EWMA／1H平滑）顯示切換 ─────────────────────
# 這三種圖層已在 build_water_figure() 內設 showlegend=False（不進原生圖例）。
# 這裡用 trace_meta 的 field_type（'lf'/'ewma'/'smooth'）反查 trace 索引，
# 靠 Patch 直接切換 visible，不用重新整包送 figure。
@app.callback(
    Output('water-graph', 'figure', allow_duplicate=True),
    Input('adv-layer-toggle', 'value'),
    Input('adv-layer-target', 'value'),
    Input('trace-meta-store', 'data'),
    prevent_initial_call=True,
)
def toggle_adv_layers(checked, target, trace_meta):
    """
    加入「目標儀器」篩選，可只顯示單一儀器的低頻趨勢/EWMA/1H平滑，
    避免同一子圖多儀器的同類線疊在一起干擾判斷。

    trace-meta-store 從 State 改成 Input：新查詢載入（trace_meta 換新）時
    會自動重新套用目前的勾選/目標狀態，修正先前「換站後勾選框跟畫面
    不同步」的已知限制。
    """
    if not trace_meta:
        return no_update
    checked = checked or []
    target  = target or 'all'
    patched = Patch()
    for i, meta in enumerate(trace_meta):
        meta = meta or {}
        ft = meta.get('field_type')
        if ft in ('lf', 'ewma', 'smooth'):
            show = ft in checked and (target == 'all' or meta.get('stid') == target)
            patched['data'][i]['visible'] = show
    return patched
 
@app.callback(
    Output('water-graph', 'figure', allow_duplicate=True),
    Output('yaxis-min', 'value'),
    Output('yaxis-max', 'value'),
    Input('reset-yaxis-btn', 'n_clicks'),
    State('water-graph', 'figure'),  # 新增：讀取目前的圖表狀態
    prevent_initial_call=True,
)
def reset_yaxis(_, fig):
    """
    重設 Y 軸為自動縮放，同時清空下限/上限輸入框。
    需要 Dash ≥ 2.9（allow_duplicate 支援）。
    """
    if not fig:
        return no_update, no_update, no_update
    patched = Patch()
    # 用迴圈掃描 layout，將所有 yaxis 重設為自動縮放
    for key in fig.get('layout', {}):
        if key.startswith('yaxis'):
            patched['layout'][key]['autorange'] = True
    # patched['layout']['yaxis']['autorange'] = True
    return patched, None, None

_diag_fig_cache: dict = {}

@app.callback(
    Output('diag-graph', 'figure'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    Input('ws-tabs', 'value'),
    Input('diag-tabs', 'value'),
)
def render_diag(store_key, url_search, ws_tab, diag_tab='diag-ocean'):
    # 2026-11-20 加 diag-tabs guard（同 R12）：切到旗標頁籤時不重畫；切回來時重新回傳，Plotly 才會正確 resize
    if ws_tab != 'ws-diag' or diag_tab != 'diag-ocean':
        return no_update

    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return no_update

    if key in _diag_fig_cache:
        return _diag_fig_cache[key]

    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update

    tide_bundles = [b for b in bundles if b.get('tide_meta')]
    if not tide_bundles:
        return no_update

    land_range    = dash_bridge.bridge.get_land_range()
    typhoon_label = dash_bridge.bridge.get_typhoon_label()

    try:
        fig = build_diagnostic_figure(tide_bundles, land_range, typhoon_label)
        fig.update_layout(legend_font_size=_FS_LEGEND)  # 👈 加上這行
        fig.update_xaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        fig.update_yaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return no_update

    apply_row_hover(fig)      # 2026-11-20 統一游標規則（hover_rows.py）
    _cache_put(_diag_fig_cache, key, fig)
    return fig


# ── 潮位自動檢核（唯讀，2026-11-20；原名「多采潮位旗標」）──────────────────────────────────────
_qcflag_cache: dict = {}


def _qcflag_insts(bundles) -> list:
    """本次查詢的潮位儀器 → [(value, label)]；value＝'主站|儀器'，主儀器排前面。"""
    out = []
    for b in bundles or []:
        meta = b.get('tide_meta') or {}
        items = sorted(meta.items(), key=lambda kv: -int(kv[1].get('is_primary') or 0))
        for stid, m in items:
            tag = '（主）' if int(m.get('is_primary') or 0) else ''
            name = str(m.get('stnac') or '').replace('潮位站', '')
            out.append((f"{b.get('stid')}|{stid}", f"{stid} {name} {m.get('type_desc') or ''}{tag}"))
    return out


@app.callback(
    Output('qcflag-inst', 'options'),
    Output('qcflag-inst', 'value'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    State('qcflag-inst', 'value'),
)
def update_qcflag_insts(store_key, url_search, current):
    key = _extract_key(url_search) or store_key
    bundles = dash_bridge.bridge.get_bundle(key) if key else None
    insts = _qcflag_insts(bundles)
    if not insts:
        return [], None
    values = [v for v, _ in insts]
    return ([{'label': lab, 'value': v} for v, lab in insts],
            current if current in values else values[0])


def _qcflag_table(header, rows):
    th = {'textAlign': 'left', 'padding': '2px 6px', 'borderBottom': f'1px solid {_BORDER}',
          'color': _ACCENT, 'fontWeight': 'normal'}
    td = {'padding': '2px 6px', 'verticalAlign': 'top'}
    return html.Table([
        html.Thead(html.Tr([html.Th(h, style=th) for h in header])),
        html.Tbody([html.Tr([html.Td(c, style=td) for c in r]) for r in rows]),
    ], style={'borderCollapse': 'collapse', 'width': '100%', 'fontSize': _FS_SMALL})


@app.callback(
    Output('qcflag-graph', 'figure'),
    Output('qcflag-status', 'children'),
    Output('qcflag-info', 'children'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    Input('ws-tabs', 'value'),
    Input('diag-tabs', 'value'),
    Input('qcflag-inst', 'value'),
    Input('qcflag-show-dc', 'value'),
)
def render_qcflag(store_key, url_search, ws_tab, diag_tab, inst, show_dc):
    """唯讀頁籤。所有旗標庫讀取包在 try 內：連不上只顯示提示，不影響其他頁籤。"""
    if ws_tab != 'ws-diag' or diag_tab != 'diag-qcflag':
        return no_update, no_update, no_update
    key = _extract_key(url_search) or store_key
    if not key:
        return no_update, no_update, no_update
    bundles = dash_bridge.bridge.get_bundle(key) or []
    insts = dict(_qcflag_insts(bundles))
    if not insts:
        return (qcflag_empty_figure('本次查詢沒有潮位站水位資料'),
                '自動檢核旗標只有潮位站水位（tide6）有；浮標、浮球沒有。', None)
    if inst not in insts:
        inst = next(iter(insts))
    with_dc = bool(show_dc)
    cache_key = (key, inst, with_dc)
    if cache_key in _qcflag_cache:
        return _qcflag_cache[cache_key]

    miss = qcflag_engine.missing_config()
    if miss:
        return (qcflag_empty_figure('未設定旗標資料庫'),
                '未設定旗標資料庫，這個頁籤不會顯示資料（其他頁籤不受影響）。\n'
                f'要使用時在 .env 加上：{"、".join(miss)}（QC_DB_PORT 可省略，預設 3306）。\n'
                '目前旗標只在開發機的本機資料庫 test_db（檢核提列測試專案）。', None)

    main_stid, stid = inst.split('|', 1)
    b = next((x for x in bundles if str(x.get('stid')) == main_stid), None)
    if b is None or b.get('df') is None or b['df'].empty:
        return qcflag_empty_figure('沒有水位資料'), '本次查詢沒有這台儀器的水位資料。', None
    df = b['df']
    t = pd.to_datetime(df['Time'])
    beg = t.min().normalize()
    end = t.max().normalize() + pd.Timedelta(days=1)
    meta = b.get('tide_meta') or {}
    siblings = [stid] + sorted((s for s in meta if s != stid),
                               key=lambda s: -int(meta[s].get('is_primary') or 0))
    try:
        conn = qcflag_engine.connect()
    except Exception as exc:
        return (qcflag_empty_figure('旗標資料庫連不上'),
                f'旗標資料庫連不上（{os.getenv("QC_DB_HOST")}）：{exc}\n其他頁籤不受影響。', None)
    try:
        flags = qcflag_engine.load_flags(conn, stid, beg.to_pydatetime(), end.to_pydatetime(), with_dc)
        if flags.empty:
            have = qcflag_engine.stids_with_flags(conn, list(meta), beg.to_pydatetime(), end.to_pydatetime())
            rng = qcflag_engine.flag_range(conn, stid)
            msg = f'{stid} 在 {beg:%Y-%m-%d}～{end - pd.Timedelta(days=1):%Y-%m-%d} 沒有旗標資料。'
            msg += (f'\n{stid} 有旗標的期間：{rng[0]:%Y-%m-%d}～{rng[1]:%Y-%m-%d}。' if rng else
                    f'\n{stid} 沒有任何旗標（自動檢核目前只算了部分測試儀器）。')
            if have:
                msg += f'\n這段期間有旗標的同址儀器：{"、".join(have)}（上方選單可切換）。'
            out = (qcflag_empty_figure('這台儀器在這段期間沒有旗標'), msg, None)
            _cache_put(_qcflag_cache, cache_key, out)
            return out
        events = qcflag_engine.load_events(conn, stid, beg.to_pydatetime(), end.to_pydatetime())
        model, est = qcflag_engine.load_nearby_est(conn, siblings, beg.to_pydatetime(), end.to_pydatetime())
        defs = qcflag_engine.load_test_defs(conn)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return qcflag_empty_figure('讀取旗標失敗'), f'讀取旗標失敗：{exc}', None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    obs = qcflag_engine.obs_from_bundle(df, stid)
    v = qcflag_engine.merge_view(obs, flags, est)
    v = v[(v['TIME'] >= beg) & (v['TIME'] < end)].reset_index(drop=True)
    hourly = (end - beg).days > qcflag_engine.HOURLY_OVER_DAYS
    if hourly:
        v = qcflag_engine.to_hourly(v)
    step_label = '逐時' if hourly else '逐 6 分鐘'
    cols, hidden = qcflag_engine.strip_columns(v, with_dc)
    marks = qcflag_engine.summary_marks(v, cols)
    groups = [g for g in qcflag_engine.group_events(events) if beg <= g['start'] < end]
    label = dict(_qcflag_insts(bundles)).get(inst, stid)
    est_label = None
    if model:
        est_label = '本儀器的模型' if model == stid else f'用同址 {model} 的模型'
    # bridge 的颱風期間只有一份（最後一次查詢的），用舊分頁看別的期間時可能不重疊 → 裁到本次期間，不重疊就不畫
    warn = dash_bridge.bridge.get_land_range()
    if isinstance(warn, dict):
        clipped = {}
        for k, seg in warn.items():
            if seg and pd.Timestamp(seg['x1']) > beg and pd.Timestamp(seg['x0']) < end:
                clipped[k] = {**seg, 'x0': max(pd.Timestamp(seg['x0']), beg),
                              'x1': min(pd.Timestamp(seg['x1']), end)}
            else:
                clipped[k] = None
        warn = clipped if any(clipped.values()) else None
    try:
        fig = build_qcflag_figure(v, cols, marks, groups, step_label,
                                  f'{label}　{beg:%Y-%m-%d}～{end - pd.Timedelta(days=1):%Y-%m-%d}',
                                  warn=warn, est_label=est_label)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return qcflag_empty_figure('畫圖失敗'), f'畫圖失敗：{exc}', None

    # 狀態文字
    n4 = int((v['qc_summary'] == 4).sum()) if 'qc_summary' in v else 0
    n3 = int((v['qc_summary'] == 3).sum()) if 'qc_summary' in v else 0
    unit = '小時' if hourly else '點'
    lines = [f'新規則彙總：不通過 4 共 {n4} {unit}、可疑 3 共 {n3} {unit}（{step_label}）。']
    if hourly:
        lines.append(f'超過 {qcflag_engine.HOURLY_OVER_DAYS} 天改逐時：旗標取該小時最嚴重者，'
                     '要看逐 6 分鐘請縮短查詢期間。')
    if not model:
        lines.append('沒有鄰站推估模型，不畫「觀測 − 鄰站推估」。')
    if with_dc and not any(c.startswith('dc_') for c in cols):
        lines.append('這段期間沒有多采模式（dc_*）結果。')

    # 右側資訊（2026-11-20 使用者嫌擠：預設只展開基準偏移事件，其餘收合）
    _h = {'color': _ACCENT, 'cursor': 'pointer', 'margin': '10px 0 2px'}
    info = []
    if groups:
        has_sn = any(g['rows']['tideshift_sn'].notna().any() for g in groups)
        rows = []
        for g in groups:
            for _, r in g['rows'].iterrows():
                row = [f"{r['break_time']:%Y-%m-%d %H:%M}",
                       qcflag_engine.SOURCE_LABEL.get(r['ref_source'], r['ref_source'] or ''),
                       qcflag_engine.WAY_LABEL.get(r['method'], r['method']),
                       f"{int(r['offset_mm']):+d}" if pd.notna(r['offset_mm']) else '']
                if has_sn:
                    row.append(str(int(r['tideshift_sn'])) if pd.notna(r['tideshift_sn']) else '')
                rows.append(row)
            rows.append([''] * (5 if has_sn else 4))
        info += [html.Div('▸ 基準偏移事件（±1 天內為同一事件）',
                          style={'color': _ACCENT, 'margin': '8px 0 2px'}),
                 _qcflag_table(['斷點', '參考來源', '偵測方式', '偏移 mm'] + (['tideshift'] if has_sn else []),
                               rows[:-1])]
    # 各項筆數：彙總放第一列，只列不通過／可疑（缺漏、未檢測看方格即可）
    cnt = qcflag_engine.code_counts(v, ['qc_summary'] + [c for c in cols if c != 'qc_summary'])
    info.append(html.Details([
        html.Summary(f'▸ 各項筆數（{step_label}）', style=_h),
        _qcflag_table(['項目', '不通過 4', '可疑 3'],
                      [[r['項目'], r['不通過'], r['可疑']] for _, r in cnt.iterrows()]),
    ]))
    if not defs.empty:
        d_sum = defs[defs['column_name'] == 'qc_summary']
        d_sub = defs[defs['column_name'] != 'qc_summary']
        info.append(html.Details([
            html.Summary('▸ 彙總規則（子項不通過、彙總卻通過的原因）', style=_h),
            *[html.Div(r['description'], style={'margin': '4px 0'}) for _, r in d_sum.iterrows()],
        ]))
        info.append(html.Details([
            html.Summary('▸ 各檢核項目判定方式', style=_h),
            *[html.Div([html.B(qcflag_engine.row_name(r['column_name'])), '：', r['description']],
                       style={'margin': '4px 0'}) for _, r in d_sub.iterrows()],
        ]))
    notes = ['只顯示，不改 qc，也不產生 SQL。',
             '觀測、天文潮、舊人工 qc 來自本次查詢的資料庫；旗標是在本機 test_db 的資料上算的，'
             '兩邊資料不同時（例：之後人工修正過）旗標可能對不上線。']
    if hidden:
        notes.append('整段都是「未檢測」而不畫的列（多半是這台儀器不適用）：'
                     + '、'.join(qcflag_engine.row_name(c) for c in hidden) + '。')
    info.append(html.Details([
        html.Summary('▸ 資料來源與限制', style=_h),
        *[html.Div(s, style={'margin': '4px 0'}) for s in notes],
    ]))

    out = (fig, '\n'.join(lines), info)
    _cache_put(_qcflag_cache, cache_key, out)
    return out

@app.callback(
    Output('buoy-graph-wave1',    'figure'),
    Output('buoy-graph-wave1',    'style'),
    Output('buoy-graph-wind',     'figure'),
    Output('buoy-graph-wind',     'style'),
    Output('buoy-graph-curr',     'figure'),
    Output('buoy-graph-curr',     'style'),
    Output('buoy-graph-wave',     'figure'),
    Output('buoy-graph-wave',     'style'),
    Output('buoy-graph-pres1',    'figure'),
    Output('buoy-graph-pres1',    'style'),
    Output('buoy-graph-stemp-air','figure'),
    Output('buoy-graph-stemp-air','style'),
    Output('buoy-graph-stemp-s0', 'figure'),
    Output('buoy-graph-stemp-s0', 'style'),
    Output('buoy-graph-stemp-s1', 'figure'),
    Output('buoy-graph-stemp-s1', 'style'),
    Output('buoy-status',         'children'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    Input('buoy-autocheck-toggle', 'value'),
)
def render_buoy(store_key, url_search, autocheck=None):
    """
    浮標頁籤渲染（R7 唯讀版）。共 17 個 Output（8 圖 × figure+style + status）。

    有資料的感測器：圖形顯示 + style={'minHeight':'520px',...}
    無資料的感測器：build_fn(None) 空圖 + style={'display':'none'}
    無 key / 無 bundle：所有 Output 回傳 no_update，圖保持初始隱藏狀態

    若查詢浮標後圖表全部空白隱藏，代表 data_engine.py 組裝 bundle 時
    key 名與本 callback 假設不符，修改下方各 .get() 參數即可。
    """
    _SHOW = {'minHeight': '520px', 'borderBottom': f'1px solid {_BORDER}'}
    _HIDE = {'display': 'none'}
    _NO16 = (no_update,) * 16

    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return *_NO16, '請先在 Tkinter 視窗選站後點「查詢」'

    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return *_NO16, f'資料不存在（key={key}）'

    buoy_bundles = [b for b in bundles if not b.get('tide_meta')]
    for _b in buoy_bundles:
        _nne = [k for k, v in _b.items() if hasattr(v, 'empty') and not v.empty]
        print(f"DEBUG buoy {_b.get('stid')} non-empty dfs: {_nne}")
    if not buoy_bundles:
        return *_NO16, '本次查詢無浮標資料'

    names  = [b.get('stname', b.get('stid', '?')) for b in bundles]
    status = f'已載入：{"、".join(names)}'

    # ── 波浪自動檢查（勾選才算；只標示，不改 qc）──────────────────────────
    # 浮球 wave1 看 H3／T3；浮標 wave 看 H／TMEAN（理由見 buoy_checks.wave_checks）
    _ac_on = bool(autocheck) and 'on' in autocheck
    _marks: dict = {}          # (id(b), key) → marks DataFrame
    if _ac_on:
        _lines = []
        for b in buoy_bundles:
            sname = b.get('stname', b.get('stid', '?'))
            depth = b.get('depth_m')
            for dkey, h_col, t_col in (('wave1_df', 'H3', 'T3'), ('wave_df', 'H', 'TMEAN')):
                df = b.get(dkey)
                if df is None or df.empty:
                    continue
                m = wave_checks(df, h_col, t_col, depth)
                _marks[(id(b), dkey)] = m
                _dep = f'水深 {depth:g} m' if depth else '站表查無水深，不做碎波上限'
                _lines.append(f'【{sname}｜{dkey[:-3]}，{_dep}】' + '\n' + summarize_checks(m))
        status += '\n\n▸ 波浪自動檢查\n' + ('\n'.join(_lines) if _lines else '本次查詢沒有波浪資料')

    def _multi(key, build_fn, *extra_args):
        entries = []
        for b in buoy_bundles:
            df = b.get(key)
            if df is not None and not df.empty:
                entries.append((df, b.get('stname', b.get('stid', '?')), b))
        if not entries:
            return build_fn(None, *extra_args), _HIDE

        def _build(df, b):
            m = _marks.get((id(b), key))
            if m is not None:
                return build_fn(df, *extra_args, marks=m)
            return build_fn(df, *extra_args)

        fig = _build(entries[0][0], entries[0][2])
        fig.update_traces(selector=dict(visible='legendonly'), visible=True)
        multi_query = len(buoy_bundles) > 1
        # multi_query = True
        if multi_query:
            for trace in fig.data:
                trace.name = f"{trace.name}｜{entries[0][1]}"
        for df, sname, b in entries[1:]:
            extra = _build(df, b)
            extra.update_traces(selector=dict(visible='legendonly'), visible=True)
            for trace in extra.data:
                if multi_query:
                    trace.name = f"{trace.name}｜{sname}"
                fig.add_trace(trace)
        fig.update_layout(showlegend=True, legend_font_size=_FS_LEGEND)
        fig.update_xaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        fig.update_yaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        return fig, _SHOW
    

    def _multi_stemp(z_val):
        entries = []
        for b in buoy_bundles:
            for df, z in b.get('stemp1_dfs', []):
                if z is not None and int(z) == z_val:
                    entries.append((df, b.get('stname', b.get('stid', '?'))))
                    break
        if not entries:
            return build_stemp1_figure(None, z_val), _HIDE
        fig = build_stemp1_figure(entries[0][0], z_val)
        multi_query = len(buoy_bundles) > 1
        # multi_query = True
        if multi_query:
            for trace in fig.data:
                trace.name = f"{trace.name}｜{entries[0][1]}"
        for df, sname in entries[1:]:
            extra = build_stemp1_figure(df, z_val)
            for trace in extra.data:
                if multi_query:
                    trace.name = f"{trace.name}｜{sname}"
                fig.add_trace(trace)
        fig.update_layout(showlegend=True, legend_font_size=_FS_LEGEND)
        return fig, _SHOW

    _r = [
        _multi('wave1_df',  build_wave1_figure),
        _multi('wind_df',   build_wind_figure),
        _multi('curr_df',   build_curr_figure),
        _multi('wave_df',   build_wave_figure),
        _multi('pres1_df',  build_pres1_figure),
        _multi_stemp(-3),
        _multi_stemp(0),
        _multi_stemp(1),
    ]
    _labels = ['wave1','wind','curr','wave','pres1','stemp-air','stemp-s0','stemp-s1']
    print("DEBUG _multi shows: " + ", ".join(
        f"{l}={'SHOW' if r[1].get('minHeight') else 'HIDE'}"
        for l, r in zip(_labels, _r)
    ))
    for _fig, _ in _r:
        apply_row_hover(_fig)   # 2026-11-20 統一游標規則；波浪圖已有自己的載體會被跳過
    return (*[v for r in _r for v in r], status)

_tideenv_fig_cache: dict = {}

@app.callback(
    Output('tideenv-graph-wind',        'figure'),
    Output('tideenv-graph-wind',        'style'),
    Output('tideenv-graph-stemp6',      'figure'),
    Output('tideenv-graph-stemp6',      'style'),
    Output('tideenv-graph-stemp6-air',  'figure'),
    Output('tideenv-graph-stemp6-air',  'style'),
    Output('tideenv-graph-pres6',       'figure'),
    Output('tideenv-graph-pres6',       'style'),
    Output('tideenv-status',            'children'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    Input('ws-tabs', 'value'),
    Input('qc-tabs', 'value'),
)
def render_tideenv(store_key, url_search, ws_tab, qc_tab):
    """
    潮位站環境 QC 頁籤渲染（wind Z='6' + stemp6 Z='0'/'-3' + pres6）。
    圖表函式重用 build_wind_figure / build_stemp1_figure / build_pres1_figure
    （見 _tideenv_tab 說明）。
    多潮位站查詢時比照 render_buoy 的 _multi() 做法合併 trace 並加站名後綴。
    """
    _SHOW = {'minHeight': '520px', 'borderBottom': f'1px solid {_BORDER}'}
    _HIDE = {'display': 'none'}
    _NO8 = (no_update,) * 8

    if ws_tab != 'ws-qc' or qc_tab != 'qc-tideenv':
        return *_NO8, no_update

    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return *_NO8, '請先在 Tkinter 視窗選站後點「查詢」'

    if key in _tideenv_fig_cache:
        return _tideenv_fig_cache[key]

    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return *_NO8, f'資料不存在（key={key}）'

    tide_bundles = [b for b in bundles if b.get('tide_meta')]
    if not tide_bundles:
        return *_NO8, '本次查詢無潮位站資料'

    names  = [b.get('stname', b.get('stid', '?')) for b in tide_bundles]
    status = f'已載入：{"、".join(names)}'

    def _multi(dkey, build_fn, *extra_args):
        entries = []
        for b in tide_bundles:
            df = b.get(dkey)
            if df is not None and not df.empty:
                entries.append((df, b.get('stname', b.get('stid', '?'))))
        if not entries:
            return build_fn(None, *extra_args), _HIDE
        fig = build_fn(entries[0][0], *extra_args)
        multi_query = len(tide_bundles) > 1
        if multi_query:
            for trace in fig.data:
                trace.name = f"{trace.name}｜{entries[0][1]}"
        for df, sname in entries[1:]:
            extra = build_fn(df, *extra_args)
            for trace in extra.data:
                if multi_query:
                    trace.name = f"{trace.name}｜{sname}"
                fig.add_trace(trace)
        fig.update_layout(showlegend=True, legend_font_size=_FS_LEGEND)
        fig.update_xaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        fig.update_yaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        return fig, _SHOW

    wind_fig,   wind_style   = _multi('wind_z6_df',    build_wind_figure)
    stemp6_fig, stemp6_style = _multi('stemp6_z0_df',  build_stemp1_figure, 0)
    air_fig,    air_style    = _multi('stemp6_zm3_df', build_stemp1_figure, -3)
    pres6_fig,  pres6_style  = _multi('pres6_df',      build_pres1_figure)
    for _fig in (wind_fig, stemp6_fig, air_fig, pres6_fig):
        apply_row_hover(_fig)   # 2026-11-20 統一游標規則（hover_rows.py）

    result = (
        wind_fig, wind_style, stemp6_fig, stemp6_style,
        air_fig, air_style, pres6_fig, pres6_style, status,
    )
    _cache_put(_tideenv_fig_cache, key, result)
    return result

# ── R8：浮標圖 Box Select → SQL ───────────────────────────────────────────

# 圖 ID → 目標資料表與 WHERE 條件參數映射
# wind z='2'：浮標主風向感測器（Z 小者優先，§9-4B）
# curr z='4'：主要流速層次（§15）
# stemp1 z=-3/0/1：氣溫 / 海溫1 / 海溫2
_BUOY_GRAPH_META: dict = {
    'buoy-graph-wave1':     {'table': 'wave1',  'z': None, 'hr': False},
    'buoy-graph-wind':      {'table': 'wind',   'z': '2',  'hr': False},
    'buoy-graph-curr':      {'table': 'curr',   'z': '4',  'hr': False},
    'buoy-graph-wave':      {'table': 'wave',   'z': None, 'hr': False},
    'buoy-graph-pres1':     {'table': 'pres1',  'z': None, 'hr': True},
    'buoy-graph-stemp-air': {'table': 'stemp1', 'z': '-3', 'hr': True},
    'buoy-graph-stemp-s0':  {'table': 'stemp1', 'z': '0',  'hr': True},
    'buoy-graph-stemp-s1':  {'table': 'stemp1', 'z': '1',  'hr': True},
}


@app.callback(
    Output('buoy-sql-output', 'value'),
    Input('buoy-graph-wave1',     'selectedData'),
    Input('buoy-graph-wind',      'selectedData'),
    Input('buoy-graph-curr',      'selectedData'),
    Input('buoy-graph-wave',      'selectedData'),
    Input('buoy-graph-pres1',     'selectedData'),
    Input('buoy-graph-stemp-air', 'selectedData'),
    Input('buoy-graph-stemp-s0',  'selectedData'),
    Input('buoy-graph-stemp-s1',  'selectedData'),
    State('buoy-qc-new',      'value'),
    State('buoy-qc-old',      'value'),
    State('bundle-key-store', 'data'),
    prevent_initial_call=True,
)
def generate_buoy_sql(
    sd_wave1, sd_wind, sd_curr, sd_wave,
    sd_pres1, sd_stemp_air, sd_stemp_s0, sd_stemp_s1,
    new_qc, old_qc, key,
):
    """
    浮標圖 Box Select → Mode 1 QC 旗標更新 SQL（R8）。

    觸發：任一 buoy-graph-* 的 selectedData 變化。
    ctx.triggered_id 決定目標資料表（見 _BUOY_GRAPH_META）。

    預期 SQL 格式：
    ──────────────────────────────────────────────
    （wave1 示例）
    UPDATE wave1
    SET qc = 'X'
    WHERE STID = '46694A'
      AND TIME BETWEEN '2025-07-24 00:00:00' AND '2025-07-25 00:00:00'
      AND qc = 'Q';

    （stemp1 z=0 示例）
    UPDATE stemp1
    SET qc = 'X'
    WHERE STID = '46694A'
      AND DATATIME BETWEEN '2025-07-24 00:00:00' AND '2025-07-25 00:00:00'
      AND Z = '0'
      AND qc = 'Q';
    ──────────────────────────────────────────────
    已知限制：多站查詢時 SQL 只對應第一個浮標 bundle 的 stid。
    """
    trigger = ctx.triggered_id
    if trigger not in _BUOY_GRAPH_META:
        return no_update

    sd_map = {
        'buoy-graph-wave1':     sd_wave1,
        'buoy-graph-wind':      sd_wind,
        'buoy-graph-curr':      sd_curr,
        'buoy-graph-wave':      sd_wave,
        'buoy-graph-pres1':     sd_pres1,
        'buoy-graph-stemp-air': sd_stemp_air,
        'buoy-graph-stemp-s0':  sd_stemp_s0,
        'buoy-graph-stemp-s1':  sd_stemp_s1,
    }
    selected_data = sd_map.get(trigger)
    if not selected_data:
        return no_update

    if not key:
        return no_update

    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update

    buoy_bundles = [b for b in bundles if not b.get('tide_meta')]
    if not buoy_bundles:
        return '-- 本次查詢無浮標資料'

    stid = buoy_bundles[0].get('stid', '')
    if not stid:
        return '-- 無法取得站碼'

    meta = _BUOY_GRAPH_META[trigger]
    sel  = _extract_sel_dict(selected_data)
    if not sel.get('x'):
        return no_update

    sql = build_buoy_qc_sql(
        sel=sel,
        stid=stid,
        table=meta['table'],
        old_qc=(old_qc or 'Q').strip(),
        new_qc=(new_qc or 'X').strip().upper(),
        z=meta['z'],
        hr_format=meta['hr'],
    )
    return sql if sql else no_update

_TIDEENV_GRAPH_META: dict = {
    'tideenv-graph-wind':   {'table': 'wind',   'z': '6', 'hr': False, 'min': False},
    'tideenv-graph-stemp6': {'table': 'stemp6', 'z': '0', 'hr': False, 'min': True},
    'tideenv-graph-stemp6-air': {'table': 'stemp6', 'z': '-3', 'hr': False, 'min': True},
    'tideenv-graph-pres6':      {'table': 'pres6',  'z': None, 'hr': False, 'min': True},
}


@app.callback(
    Output('tideenv-sql-output', 'value'),
    Input('tideenv-graph-wind',   'selectedData'),
    Input('tideenv-graph-stemp6', 'selectedData'),
    Input('tideenv-graph-stemp6-air', 'selectedData'),
    Input('tideenv-graph-pres6',      'selectedData'),
    State('tideenv-qc-new', 'value'),
    State('tideenv-qc-old', 'value'),
    State('bundle-key-store', 'data'),
    prevent_initial_call=True,
)
def generate_tideenv_sql(sd_wind, sd_stemp6, sd_stemp6_air, sd_pres6, new_qc, old_qc, key):
    """
    潮位站環境圖 Box Select → Mode 1 QC 旗標更新 SQL。
    wind（Z='6'）：TIME 型，hr_format/min_format 皆 False。
    stemp6（Z='0'）：MIN0~MIN9 型，DATATIME 截到「小時」，走 min_format=True
    （不可用 hr_format=True，那是 stemp1/pres1「截到當天」的邏輯，
    stemp6 若誤用會反推出錯誤日期，見本輪討論）。
    """
    trigger = ctx.triggered_id
    if trigger not in _TIDEENV_GRAPH_META:
        return no_update

    sd_map = {
        'tideenv-graph-wind':   sd_wind,
        'tideenv-graph-stemp6': sd_stemp6,
        'tideenv-graph-stemp6-air': sd_stemp6_air,
        'tideenv-graph-pres6':      sd_pres6,
    }
    selected_data = sd_map.get(trigger)
    if not selected_data:
        return no_update

    if not key:
        return no_update

    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update

    tide_bundles = [b for b in bundles if b.get('tide_meta')]
    if not tide_bundles:
        return '-- 本次查詢無潮位站資料'

    stid = tide_bundles[0].get('stid', '')
    if not stid:
        return '-- 無法取得站碼'

    meta = _TIDEENV_GRAPH_META[trigger]
    sel  = _extract_sel_dict(selected_data)
    if not sel.get('x'):
        return no_update

    sql = build_buoy_qc_sql(
        sel=sel,
        stid=stid,
        table=meta['table'],
        old_qc=(old_qc or 'Q').strip(),
        new_qc=(new_qc or 'X').strip().upper(),
        z=meta['z'],
        hr_format=meta['hr'],
        min_format=meta['min'],
    )
    return sql if sql else no_update

# ── R9：VdC 統計面板格式化 ───────────────────────────────────────────────

def _format_vdc_stats(stats_summary: dict) -> list:
    """
    將 build_vdc_figure 回傳的 stats_summary 格式化為 Dash children list。
    status != 'success' 時顯示對應失敗原因文字。

    stats 結構（per stid）：
        status / count / mean / std / min / max /
        time_start / time_end / slope / r2
    """
    if not stats_summary:
        return [html.Div('（無統計資料）', style={
            'color': _TEXT, 'opacity': '0.5',
            'fontFamily': '標楷體', 'fontSize': _FS_SMALL,
        })]

    _STATUS_LABEL = {
        'no_data':    '無副儀器（需雙儀器差值）',
        'no_fields':  '差值欄位不存在',
        'empty_data': '此區間無資料',
    }

    children = []
    for stid, stats in stats_summary.items():
        status = stats.get('status', 'unknown')
        if status != 'success':
            children.append(html.Div(
                f'{stid}：{_STATUS_LABEL.get(status, status)}',
                style={
                    'color': _TEXT, 'opacity': '0.6',
                    'marginBottom': '6px',
                    'fontFamily': '標楷體', 'fontSize': _FS_SMALL,
                },
            ))
            continue

        slope_str = (f"{stats['slope']:.4f}"
                     if stats.get('slope') is not None else 'N/A')
        r2_str    = (f"{stats['r2']:.3f}"
                     if stats.get('r2')    is not None else 'N/A')

        children.append(html.Div([
            html.Div(f'🔵 {stid}', style={
                'fontWeight': 'bold', 'color': _ACCENT,
                'fontFamily': '標楷體', 'fontSize': _FS_BODY,
                'marginBottom': '4px',
            }),
            html.Div(
                f'期間：{stats.get("time_start","?")} ～ {stats.get("time_end","?")}',
                style={'fontSize': _FS_TINY, 'opacity': '0.75', 'marginBottom': '2px'},
            ),
            html.Div(f'筆數：{stats["count"]}'),
            html.Div(f'平均差：{stats["mean"]:.1f} mm'),
            html.Div(f'標準差：{stats["std"]:.1f} mm'),
            html.Div(f'最大：{stats["max"]:.1f}　最小：{stats["min"]:.1f} mm'),
            html.Div(f'回歸斜率：{slope_str}'),
            html.Div(f'R²：{r2_str}'),
        ], style={
            'marginBottom': '12px',
            'paddingBottom': '8px',
            'borderBottom': f'1px solid {_BORDER}',
            'fontFamily': '標楷體', 'fontSize': _FS_SMALL,
            'color': _TEXT, 'lineHeight': '1.7',
        }))

    return children

# @app.callback(
#     Output('vdc-graph', 'figure'),
#     Output('vdc-status', 'children'),
#     Output('vdc-stats-panel', 'children'),
#     Input('bundle-key-store', 'data'),
#     Input('url', 'search'),
#     Input('vdc-diff-type', 'value'),
#     Input('zoom-range-store', 'data'),
#     # Sonnet 5 重複輸出了反向的相同內容跟我說要收回上一輪調整...
#     # 到底是覺得我笨到需要這樣輸出才教得會還是這樣可以達到拿抹布遮鏡頭完成最大清潔率的成就？
#     # Input('ws-tabs', 'value'),
#     # Input('qc-tabs', 'value'),
# )
# def render_vdc(store_key, url_search, diff_type, zoom_range_data):
# # def render_vdc(store_key, url_search, diff_type, zoom_range_data, ws_tab, qc_tab):
#     """
#     VdC 散佈圖渲染（R9）。
#     只對潮位站 bundle 有意義（需 tide_meta + 多儀器差值欄）。
#     純浮標查詢時 figure / stats_panel 均 no_update，status 顯示提示。

#     zoom-range-store 格式轉換：
#         store  → {'xmin': str, 'xmax': str}
#         vdc    → {'x_start': str, 'x_end': str}
#     zoom_range=None 時 build_vdc_figure 使用全時段。
#     """
#     # # R12：同上，VdC 巢狀在 qc-tabs 底下，兩層都要在正確分頁才繼續。
#     # if ws_tab != 'ws-qc' or qc_tab != 'qc-vdc':
#     #     return no_update, no_update, no_update
#     url_key = _extract_key(url_search)
#     key = url_key if url_key else store_key
#     if not key:
#         return no_update, '請先在 Tkinter 視窗選站後點「查詢」', no_update

#     bundles = dash_bridge.bridge.get_bundle(key)
#     if not bundles:
#         return no_update, f'資料不存在（key={key}）', no_update

#     tide_bundles = [b for b in bundles if b.get('tide_meta')]
#     names  = [b.get('stname', b.get('stid', '?')) for b in bundles]
#     status = f'已載入：{"、".join(names)}'

#     if not tide_bundles:
#         return no_update, '此查詢無潮位站資料（VdC 需雙儀器）', []

#     # zoom-range-store → build_vdc_figure 格式轉換
#     zoom_range = None
#     if zoom_range_data:
#         xmin = zoom_range_data.get('xmin')
#         xmax = zoom_range_data.get('xmax')
#         if xmin and xmax:
#             zoom_range = {'x_start': xmin, 'x_end': xmax}

#     try:
#         fig, stats_summary = build_vdc_figure(
#             tide_bundles,
#             diff_type=diff_type or 'auto',
#             zoom_range=zoom_range,
#         )
#     except Exception as exc:
#         import traceback
#         traceback.print_exc()
#         return no_update, f'繪圖失敗：{exc}', []

#     return fig, status, _format_vdc_stats(stats_summary)

_vdc_fig_cache: dict = {}

@app.callback(
    Output('vdc-graph', 'figure'),
    Output('vdc-status', 'children'),
    Output('vdc-stats-panel', 'children'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    Input('vdc-diff-type', 'value'),
    Input('zoom-range-store', 'data'),
    Input('ws-tabs', 'value'),
    Input('qc-tabs', 'value'),
)
def render_vdc(store_key, url_search, diff_type, zoom_range_data, ws_tab, qc_tab):
    if ws_tab != 'ws-qc' or qc_tab != 'qc-vdc':
        return no_update, no_update, no_update

    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return no_update, '請先在 Tkinter 視窗選站後點「查詢」', no_update

    cache_key = (key, diff_type or 'auto', str(zoom_range_data))
    if cache_key in _vdc_fig_cache:
        return _vdc_fig_cache[cache_key]

    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update, f'資料不存在（key={key}）', no_update

    tide_bundles = [b for b in bundles if b.get('tide_meta')]
    names  = [b.get('stname', b.get('stid', '?')) for b in bundles]
    status = f'已載入：{"、".join(names)}'

    if not tide_bundles:
        return no_update, '此查詢無潮位站資料（VdC 需雙儀器）', []

    zoom_range = None
    if zoom_range_data:
        xmin = zoom_range_data.get('xmin')
        xmax = zoom_range_data.get('xmax')
        if xmin and xmax:
            zoom_range = {'x_start': xmin, 'x_end': xmax}

    try:
        fig, stats_summary = build_vdc_figure(
            tide_bundles, diff_type=diff_type or 'auto', zoom_range=zoom_range,
        )
        fig.update_layout(legend_font_size=_FS_LEGEND)  # 👈 加上這行
        fig.update_xaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        fig.update_yaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return no_update, f'繪圖失敗：{exc}', []

    result = (fig, status, _format_vdc_stats(stats_summary))
    _cache_put(_vdc_fig_cache, cache_key, result)
    return result

# @app.callback(
#     Output('surge-graph', 'figure'),
#     Output('surge-status', 'children'),
#     Output('surge-ty-label', 'children'),
#     Input('bundle-key-store', 'data'),
#     Input('url', 'search'),
#     Input('surge-pred-source', 'value'),
#     Input('surge-legend-mode', 'value'),
#     # Input('ws-tabs', 'value'),
#     # Input('event-tabs', 'value'),
# )
# def render_surge(store_key, url_search, pred_source, legend_mode):
# # def render_surge(store_key, url_search, pred_source, legend_mode, ws_tab, event_tab):
#     """
#     暴潮偏差互動圖渲染（R9）。
#     只對潮位站 bundle 有意義（需水位與預報欄位）。
#     颱風 info 與各站門檻值從 dash_bridge 取得；未選颱風時仍可渲染（無色帶）。
#     不跟隨 zoom-range-store（颱風報告需完整事件期間，見 SYSTEM.md §11）。

#     thresholds_map 為空或 None 時：門檻線不繪製，靜默忽略。
#     typhoon_info   為空 dict {} 時：颱風標題顯示「未指定颱風」，色帶不繪製。
#     """
#     # if ws_tab != 'ws-event' or event_tab != 'event-surge':
#     #     return no_update, no_update, no_update
#     url_key = _extract_key(url_search)
#     key = url_key if url_key else store_key
#     if not key:
#         return no_update, '請先在 Tkinter 視窗選站後點「查詢」', no_update

#     bundles = dash_bridge.bridge.get_bundle(key)
#     if not bundles:
#         return no_update, f'資料不存在（key={key}）', no_update

#     tide_bundles = [b for b in bundles if b.get('tide_meta')]
#     names  = [b.get('stname', b.get('stid', '?')) for b in bundles]
#     status = f'已載入：{"、".join(names)}'

#     if not tide_bundles:
#         return no_update, '此查詢無潮位站資料', no_update

#     typhoon_info   = dash_bridge.bridge.get_typhoon_info() or {}
#     thresholds_map = dash_bridge.bridge.get_thresholds_map()

#     ty_id   = typhoon_info.get('id',    typhoon_info.get('ID',    ''))
#     ty_name = typhoon_info.get('cname', typhoon_info.get('CNAME', ''))
#     ty_label = (f'颱風：{ty_id} {ty_name}'
#                 if (ty_id or ty_name) else '（尚未選取颱風）')

#     try:
#         fig = build_surge_interactive_figure(
#             tide_bundles,
#             typhoon_info,
#             thresholds_map,
#             pred_source=pred_source or 'auto',
#             legend_mode=legend_mode or 'shared',
#         )
#     except Exception as exc:
#         import traceback
#         traceback.print_exc()
#         return no_update, f'繪圖失敗：{exc}', ty_label

#     return fig, status, ty_label

_surge_fig_cache: dict = {}

@app.callback(
    Output('surge-graph', 'figure'),
    Output('surge-status', 'children'),
    Output('surge-ty-label', 'children'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    Input('surge-pred-source', 'value'),
    Input('surge-legend-mode', 'value'),
    Input('surge-yaxis-mode', 'value'),  # 👈 新增：接聽 Y 軸切換鈕
    Input('ws-tabs', 'value'),
    Input('event-tabs', 'value'),
)
def render_surge(store_key, url_search, pred_source, legend_mode, y_axis_mode, ws_tab, event_tab):
    if ws_tab != 'ws-event' or event_tab != 'event-surge':
        return no_update, no_update, no_update

    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return no_update, '請先在 Tkinter 視窗選站後點「查詢」', no_update

    # 👈 注意：必須把 y_axis_mode 加進 cache_key，否則切換按鈕時圖表不會重新計算
    # cache_key = (key, pred_source or 'auto', legend_mode or 'shared')
    cache_key = (key, pred_source or 'auto', legend_mode or 'shared', y_axis_mode or 'auto')
    if cache_key in _surge_fig_cache:
        return _surge_fig_cache[cache_key]

    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update, f'資料不存在（key={key}）', no_update

    tide_bundles = [b for b in bundles if b.get('tide_meta')]
    names  = [b.get('stname', b.get('stid', '?')) for b in bundles]
    status = f'已載入：{"、".join(names)}'

    if not tide_bundles:
        return no_update, '此查詢無潮位站資料', no_update

    typhoon_info   = dash_bridge.bridge.get_typhoon_info() or {}
    thresholds_map = dash_bridge.bridge.get_thresholds_map()

    ty_id   = typhoon_info.get('id',    typhoon_info.get('ID',    ''))
    ty_name = typhoon_info.get('cname', typhoon_info.get('CNAME', ''))
    ty_label = (f'颱風：{ty_id} {ty_name}'
                if (ty_id or ty_name) else '（尚未選取颱風）')
    
    print(f"==== 準備繪圖！目前的 typhoon_info 長這樣：{typhoon_info} ====") # 👈 加上這行來除錯
    # === 新增：相容兩種資料表結構的正規化轉換 (雙軌制) ===
    if typhoon_info:
        mapped_typhoon_info = {
            # dict.get(優先找的Key, 找不到的話再找另一個Key)
            'id': typhoon_info.get('id', typhoon_info.get('ID', '')),
            'cname': typhoon_info.get('cname', typhoon_info.get('CNAME', '')),
            
            # 海警 (WARN1)
            'warnSeaBeg': typhoon_info.get('warnSeaBeg', typhoon_info.get('WARN1BEG')),
            'warnSeaEnd': typhoon_info.get('warnSeaEnd', typhoon_info.get('WARN1END')),
            
            # 陸警 (WARN2)
            'warnLandBeg': typhoon_info.get('warnLandBeg', typhoon_info.get('WARN2BEG')),
            'warnLandEnd': typhoon_info.get('warnLandEnd', typhoon_info.get('WARN2END'))
        }
    else:
        mapped_typhoon_info = {}
    # ==========================================================    
    try:
        # 將前端拿到的 y_axis_mode 往下傳遞給繪圖函式
        fig = build_surge_interactive_figure(
            tide_bundles, mapped_typhoon_info, thresholds_map,
            pred_source=pred_source or 'auto', legend_mode=legend_mode or 'shared',
            y_axis_mode=y_axis_mode or 'auto' # 👈 新增這行參數傳遞
        )
        fig.update_layout(legend_font_size=_FS_LEGEND)  # 👈 加上這行
        fig.update_xaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
        fig.update_yaxes(title_font_size=_FS_AXIS_TITLE, tickfont_size=_FS_AXIS_TICK)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return no_update, f'繪圖失敗：{exc}', ty_label

    apply_row_hover(fig)      # 2026-11-20 統一游標規則（hover_rows.py）
    result = (fig, status, ty_label)
    _cache_put(_surge_fig_cache, cache_key, result)
    return result

@app.callback(
    Output('qc-tabs', 'value'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    State('qc-tabs', 'value'),
)
def auto_switch_tab(store_key, url_search, current_tab):
    """依據查詢的站點類型，自動切換到對應的 QC 頁籤。"""
    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return no_update
 
    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update
 
    # 判斷是否包含潮位站
    has_tide = any(b.get('tide_meta') for b in bundles)
    
    # 如果沒有潮位站（只有浮標），就切換到 qc-buoy；否則留在 qc-water
    target_tab = 'qc-water' if has_tide else 'qc-buoy'
    
    # 如果已經在正確的頁籤，就不觸發更新避免畫面閃爍
    if current_tab == target_tab:
        return no_update
        
    return target_tab

# ── R10：事件報告渲染 + PNG 下載 ─────────────────────────────────────────

# @app.callback(
#     Output('report-list', 'children'),
#     Output('report-status', 'children'),
#     Input('bundle-key-store', 'data'),
#     Input('url', 'search'),
# )
# def render_report(store_key, url_search):
@app.callback(
    Output('report-list', 'children'),
    Output('report-status', 'children'),
    Input('bundle-key-store', 'data'),
    Input('url', 'search'),
    Input('zoom-range-store', 'data'), # 看起來只多這行
)
def render_report(store_key, url_search, zoom_range_data):
    """
    產生每站報告卡片（暴潮偏差白底圖 + 水位白底圖）。
    只處理潮位站，浮標站不顯示（與 render_vdc / render_surge 過濾邏輯一致）。
    """
    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return no_update, '請先在 Tkinter 視窗選站後點「查詢」'

    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update, f'資料不存在（key={key}）'

    tide_bundles = [b for b in bundles if b.get('tide_meta')]
    if not tide_bundles:
        return [], '此查詢無潮位站資料（事件報告僅支援潮位站）'

    # land_range     = dash_bridge.bridge.get_land_range()
    # typhoon_info   = dash_bridge.bridge.get_typhoon_info() or {}
    # thresholds_map = dash_bridge.bridge.get_thresholds_map()

    # cards  = [_report_card(b, land_range, typhoon_info, thresholds_map) for b in tide_bundles]
    # names  = [b.get('stname', b.get('stid', '?')) for b in tide_bundles]
    # status = f'已載入：{"、".join(names)}（共 {len(tide_bundles)} 站）'
    # return cards, status
    land_range     = dash_bridge.bridge.get_land_range()
    typhoon_info   = dash_bridge.bridge.get_typhoon_info() or {}
    thresholds_map = dash_bridge.bridge.get_thresholds_map()

    zoom_range = None
    if zoom_range_data:
        xmin = zoom_range_data.get('xmin')
        xmax = zoom_range_data.get('xmax')
        if xmin and xmax:
            zoom_range = {'x_start': xmin, 'x_end': xmax}

    cards  = [_report_card(b, land_range, typhoon_info, thresholds_map, zoom_range) for b in tide_bundles]
    names  = [b.get('stname', b.get('stid', '?')) for b in tide_bundles]
    zoom_note = '（水位圖已套用縮放範圍）' if zoom_range else ''
    status = f'已載入：{"、".join(names)}（共 {len(tide_bundles)} 站）{zoom_note}'
    return cards, status


def _fetch_station_bundle(store_key, url_search, stid):
    """下載 callback 共用：從 dash_bridge 反查目前 key 底下對應 stid 的 bundle。"""
    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return None
    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return None
    return next((b for b in bundles if b.get('stid') == stid), None)


@app.callback(
    Output({'type': 'dl-surge-dl', 'index': MATCH}, 'data'),
    Input({'type': 'dl-surge-btn', 'index': MATCH}, 'n_clicks'),
    State('bundle-key-store', 'data'),
    State('url', 'search'),
    prevent_initial_call=True,
)
def download_surge_report(n_clicks, store_key, url_search):
    if not n_clicks:
        return no_update
    stid = ctx.triggered_id['index']
    b = _fetch_station_bundle(store_key, url_search, stid)
    if b is None:
        return no_update

    typhoon_info   = dash_bridge.bridge.get_typhoon_info() or {}
    thresholds_map = dash_bridge.bridge.get_thresholds_map()
    thresholds     = (thresholds_map or {}).get(stid)

    # try:
    #     fig = build_surge_report_figure(b, typhoon_info, thresholds)
    #     img_bytes = fig.to_image(format='png', scale=2)
    # except Exception:
    #     import traceback
    #     traceback.print_exc()
    #     return no_update

    # ty_id = typhoon_info.get('id', typhoon_info.get('ID', ''))
    # fname = f'{ty_id}_{stid}_暴潮偏差.png' if ty_id else f'{stid}_暴潮偏差.png'
    try:
        img_bytes = _render_surge_png(b, typhoon_info, thresholds)
    except Exception:
        import traceback
        traceback.print_exc()
        return no_update

    ty_id = typhoon_info.get('id', typhoon_info.get('ID', ''))
    fname = f'{ty_id}_{stid}_暴潮偏差.png' if ty_id else f'{stid}_暴潮偏差.png'
    return dcc.send_bytes(img_bytes, fname)


# @app.callback(
#     Output({'type': 'dl-water-dl', 'index': MATCH}, 'data'),
#     Input({'type': 'dl-water-btn', 'index': MATCH}, 'n_clicks'),
#     State('bundle-key-store', 'data'),
#     State('url', 'search'),
#     prevent_initial_call=True,
# )
# def download_water_report(n_clicks, store_key, url_search):
#     if not n_clicks:
#         return no_update
#     stid = ctx.triggered_id['index']
#     b = _fetch_station_bundle(store_key, url_search, stid)
#     if b is None:
#         return no_update

#     land_range = dash_bridge.bridge.get_land_range()

#     try:
#         fig = build_water_report_figure([b], land_range=land_range)
#         img_bytes = fig.to_image(format='png', scale=2)
#     except Exception:
#         import traceback
#         traceback.print_exc()
#         return no_update

#     return dcc.send_bytes(img_bytes, f'{stid}_水位.png')

@app.callback(
    Output('dl-all-zip', 'data'),
    Input('dl-all-btn', 'n_clicks'),
    State('bundle-key-store', 'data'),
    State('url', 'search'),
    State('zoom-range-store', 'data'),
    prevent_initial_call=True,
)
def download_all_reports_zip(n_clicks, store_key, url_search, zoom_range_data):
    """
    打包下載目前 report-list 顯示的全部站點 PNG（暴潮偏差 + 水位）。
    同步生成、無進度條；12 站 × 2 圖可能需要數秒到數十秒，屬已知限制。
    若測試發現太慢／逾時，下一輪可改用 background callback + 進度提示。
    單一站繪圖失敗不影響其他站，失敗清單會寫進 zip 內的 _失敗清單.txt。
    """
    if not n_clicks:
        return no_update

    url_key = _extract_key(url_search)
    key = url_key if url_key else store_key
    if not key:
        return no_update
    bundles = dash_bridge.bridge.get_bundle(key)
    if not bundles:
        return no_update
    tide_bundles = [b for b in bundles if b.get('tide_meta')]
    if not tide_bundles:
        return no_update

    land_range     = dash_bridge.bridge.get_land_range()
    typhoon_info   = dash_bridge.bridge.get_typhoon_info() or {}
    thresholds_map = dash_bridge.bridge.get_thresholds_map()

    zoom_range = None
    if zoom_range_data:
        xmin = zoom_range_data.get('xmin')
        xmax = zoom_range_data.get('xmax')
        if xmin and xmax:
            zoom_range = {'x_start': xmin, 'x_end': xmax}

    import io
    import zipfile

    failed = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for b in tide_bundles:
            stid = b.get('stid', '?')

            try:
                thresholds = (thresholds_map or {}).get(stid)
                png = _render_surge_png(b, typhoon_info, thresholds)
                zf.writestr(f'{stid}_暴潮偏差.png', png)
            except Exception:
                import traceback
                # traceback.print_exc()
                # failed.append(f'{stid}：暴潮偏差圖失敗')
                tb = traceback.format_exc() #Sonnet5加的測試項，用在下兩行後面\n那裡
                traceback.print_exc()
                failed.append(f'{stid}：暴潮偏差圖失敗\n{tb}')

            try:
                png = _render_water_png(b, land_range, zoom_range)
                zf.writestr(f'{stid}_水位.png', png)
            except Exception:
                import traceback
                # traceback.print_exc()
                # failed.append(f'{stid}：水位圖失敗')
                tb = traceback.format_exc()  #Sonnet5加的測試項，用在下兩行後面\n那裡
                traceback.print_exc()
                failed.append(f'{stid}：水位圖失敗\n{tb}') # 不知道為什麼要拆兩次設計，大概是想把兩種失敗清單的內容都印清楚
            try:
                import plotly.io as pio
                pio.kaleido.scope._shutdown_kaleido()
            except Exception:
                pass

        if failed:
            zf.writestr('_失敗清單.txt', '\n'.join(failed))

    buf.seek(0)
    ty_id = typhoon_info.get('id', typhoon_info.get('ID', ''))
    fname = f'{ty_id}_事件報告.zip' if ty_id else '事件報告.zip'
    return dcc.send_bytes(buf.getvalue(), fname)

@app.callback(
    Output({'type': 'dl-water-dl', 'index': MATCH}, 'data'),
    Input({'type': 'dl-water-btn', 'index': MATCH}, 'n_clicks'),
    State('bundle-key-store', 'data'),
    State('url', 'search'),
    State('zoom-range-store', 'data'),
    prevent_initial_call=True,
)
def download_water_report(n_clicks, store_key, url_search, zoom_range_data):
    if not n_clicks:
        return no_update
    stid = ctx.triggered_id['index']
    b = _fetch_station_bundle(store_key, url_search, stid)
    if b is None:
        return no_update

    land_range = dash_bridge.bridge.get_land_range()

    zoom_range = None
    if zoom_range_data:
        xmin = zoom_range_data.get('xmin')
        xmax = zoom_range_data.get('xmax')
        if xmin and xmax:
            zoom_range = {'x_start': xmin, 'x_end': xmax}

    try:
        img_bytes = _render_water_png(b, land_range, zoom_range)
    except Exception:
        import traceback
        traceback.print_exc()
        return no_update

    return dcc.send_bytes(img_bytes, f'{stid}_水位.png')