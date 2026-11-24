"""
build_water_figure.py
=====================
純函式版的 draw_water_only()。

移除：
    - 寫 temp HTML 檔（改由 dcc.Graph 直接接收 Figure）
    - 開啟瀏覽器視窗（改由 Tkinter 呼叫 webbrowser.open 在外部處理）

新增：
    回傳 go.Figure 物件，供 Dash dcc.Graph 直接消費。

Scattergl → Scatter 說明：
    原版使用 go.Scattergl 以提升大資料集效能。
    Dash 的 selectedData（Box Select）在部分環境下對 Scattergl 回傳的
    框選點資料不完整，改用 go.Scatter 可確保 QC 框選功能在所有平台正常運作。
    若資料量極大（> 10 萬點），可再視情況切回 Scattergl 並個別驗證。
"""

import pandas as pd
import plotly.graph_objects as go
import line_style as LW   # 全系統折線粗細（2026-11-20 統一）
from typhoon_bands import add_warn_bands
from plotly.subplots import make_subplots


# 儀器類型色系（與原版 draw_water_only 完全相同）
_TYPE_COLORS = {2: '#1f77b4', 3: '#0d47a1', 4: '#64b5f6'}
_DIFF_COLORS = ['#ff7f0e', '#e377c2', '#17becf']

# _FONT = "Noto Sans TC, PingFang TC, Microsoft JhengHei, Arial, sans-serif"
_FONT = "標楷體, PingFang TC, Noto Sans CJK TC, Arial, sans-serif"

# 以下build_water_figure 函式為 Gemini Trace 任一儀器水位資料產出 update QC SQL 的版本
# 挑站規則是：「多數決（點數最多者獲勝）」，剛好援用了原本 Claude 在 dash_app.py 為了偵測子圖的設計
AUTOQC_MARK = {   # 同浮標自動檢查、自動檢核唯讀頁籤的樣式；和紅叉（資料庫 qc 非 Q）用形狀區分
    4: dict(symbol="circle-open",  color="#ff5a5a", tag="不通過"),
    3: dict(symbol="diamond-open", color="#fab219", tag="可疑"),
}
AUTOQC_SIZE, AUTOQC_SIZE_DENSE, AUTOQC_DENSE_N = (8, 1.5), (5, 1.0), 300   # (大小, 邊線寬)


def _add_autoqc_marks(fig, marks: pd.DataFrame, row: int, legend_id: str):
    """自動檢核（試行）彙總 3／4 → 每列兩個 trace（可疑、不通過），同列各儀器合在一起。
    marks：qcflag_engine.load_summary_marks() 的標記表，多一欄 INST（儀器標籤）。
    hovertemplate 含 %{x}：hover_rows 視為「自己會顯示時間的稀疏標記」，保留自己的游標。"""
    for level in (4, 3):
        m = marks[marks["LEVEL"] == level]
        if m.empty:
            continue
        st = AUTOQC_MARK[level]
        size, lw = AUTOQC_SIZE if len(m) <= AUTOQC_DENSE_N else AUTOQC_SIZE_DENSE
        note = m["NOTE"].fillna("").map(lambda s: f"<br>未通過子項：{s}" if s else "")
        fig.add_trace(
            go.Scatter(
                x=m["TIME"], y=m["Y"],
                name=f"自動檢核 {st['tag']}（試行）",
                mode="markers",
                marker=dict(symbol=st["symbol"], size=size, color=st["color"],
                            line=dict(width=lw, color=st["color"])),
                customdata=list(zip(m["INST"], note)),
                hovertemplate=("%{x}<br>%{customdata[0]}：%{y:.0f} mm"
                               f"<br>自動檢核（試行）：{st['tag']}"
                               "%{customdata[1]}<extra></extra>"),
                legend=legend_id,
            ),
            row=row, col=1, secondary_y=False,
        )


def build_water_figure(bundles: list, land_range=None, typhoon_label: str | None = None,
                       autoqc_marks: dict | None = None) -> go.Figure:
    """
    移植自 draw_water_only(bundles, land_range)。

    Parameters
    ----------
    bundles    : list[dict]
        fetch_bundle() 回傳值的清單。每個 bundle 包含：
            stid       str          主測站代碼
            stname      str          測站中文名稱
            df         pd.DataFrame 合併後的時序資料（Time 欄為基準）
            tide_meta  dict         {STID: {type, type_desc, stnac, is_primary}}
    land_range : dict | tuple | None
        颱風警報期間：typhoon_bands.warn_ranges() 的結果（海警＋陸警，已裁到查詢期間）；
        舊格式 (beg, end) 視為陸警；None 不畫。（2026-11-20：參數名沿用，內容改為海陸警）
    typhoon_label: str | None = None,   # ← 新增
    autoqc_marks : dict | None
        {主站 stid: 標記表}，自動檢核（試行）彙總 3／4 的點（2026-11-24，QC 工作台勾選框）。
        對應的 trace_meta field_type＝'autoqc'；generate_sql 會略過這些點。

    Returns
    -------
    tuple(go.Figure, list[dict])
        回傳 (圖表物件, trace_meta清單) 供 Dash 處理。
    """
    n = len(bundles)
    if n == 0:
        # 防呆：無資料時回傳空白圖，避免 Dash callback 報錯
        fig = go.Figure()
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#1E1E1E",
            plot_bgcolor="#1E1E1E",
            annotations=[dict(text="無資料（bundles 為空）",
                              x=0.5, y=0.5, xref="paper", yref="paper",
                              showarrow=False, font=dict(color="#888", size=16))],
        )
        return fig, []

    # ── 子圖結構：每測站一列，共享 x 軸，雙 y 軸 ────────────────────────────
    fig = make_subplots(
        rows=n,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05, # <== 從 0.05 改大，例如 0.1 或 0.15
        subplot_titles=[
            (f"【{typhoon_label}】" if typhoon_label else "") + f"{b['stname']}({b['stid']}) - 水位細節診斷"
            for b in bundles
        ],
        specs=[[{"secondary_y": True}] for _ in range(n)],
    )

    trace_meta = []  # 💡 [修改點] 提前宣告，在每次 add_trace 時即時記錄真實 STID

    for idx, b in enumerate(bundles):
        row       = idx + 1
        df        = b["df"]
        tide_meta = b.get("tide_meta", {})
        bundle_stid = b["stid"]  # 主測站碼，供預報/差值等共用線條使用

        # 新增這行：定義獨立的圖例 ID (第一列為 "legend"，第二列為 "legend2"，依此類推)
        legend_id = "legend" if row == 1 else f"legend{row}"

        # ── § 0  舊系統降級路徑（無 tide_meta，僅 Obs / Pre 欄位）────────────
        if not tide_meta:
            if "Obs" in df.columns:
                fig.add_trace(
                    go.Scattergl(
                        x=df["Time"], y=df["Obs"],
                        name=f"{b['stname']}({b['stid']}) 水位",
                        mode="lines+markers",
                        line=dict(color="#1f77b4", width=LW.MAIN),
                        marker=dict(size=3, opacity=0.6),
                        connectgaps=False,
                        legend=legend_id,
                    ),
                    row=row, col=1, secondary_y=False,
                )
                trace_meta.append({"stid": bundle_stid, "field_type": "wl"})

            if "Pre" in df.columns:
                fig.add_trace(
                    go.Scattergl(
                        x=df["Time"], y=df["Pre"],
                        name=f"{b['stname']}({b['stid']}) 調和預報",
                        mode="lines",
                        line=dict(color="#2ca02c", dash="dot", width=LW.SUB),
                        connectgaps=True,
                        legend=legend_id,
                    ),
                    row=row, col=1, secondary_y=False,
                )
                trace_meta.append({"stid": bundle_stid, "field_type": "pred"})

            fig.update_yaxes(title_text="水位(mm)", row=row, col=1,
                             secondary_y=False, fixedrange=False)
            add_warn_bands(fig, land_range, row=row, col=1, theme="dark", label=(row == 1))
            continue # 降級路徑處理完畢，跳至下一站

        # ── § 1  各儀器水位線 ───────────────────────────────────────────────
        for stid_wl, meta in sorted(tide_meta.items()):
            col_name     = f"WL_{stid_wl}"        # 校正值（QC=Q）
            raw_col_name = f"WL_{stid_wl}_raw"    # 原始機測值（QC≠Q）
            qc_raw_name  = f"QC_{stid_wl}_raw"    # 原始值的 QC 代碼（hover 用）

            if col_name not in df.columns:
                continue

            label      = (f"{meta.get('stnac', '未知')}({stid_wl})"
                          f"-{meta.get('type_desc', '未知類型')}"
                          f"{'(主)' if meta['is_primary'] else ''}")
            color      = _TYPE_COLORS.get(meta["type"], "gray")
            dash_style = "solid" if meta["is_primary"] else "dash"

            # Trace 1：校正值（QC=Q）
            q_mask = df[col_name].notna()
            fig.add_trace(
                go.Scattergl(
                    x=df.loc[q_mask, "Time"],
                    y=df.loc[q_mask, col_name],
                    name=label,
                    mode="lines+markers",
                    line=dict(color=color, dash=dash_style, width=LW.MAIN),
                    marker=dict(size=3, opacity=0.6),
                    connectgaps=False,
                    legend=legend_id,
                ),
                row=row, col=1, secondary_y=False,
            )
            # 💡 記錄當下真正的儀器 STID
            trace_meta.append({"stid": stid_wl, "field_type": "wl"})

            # Trace 2：低頻趨勢線（25h 移動平均）
            lf_col = f"WL_{stid_wl}_lf"
            if lf_col in df.columns and df[lf_col].notna().any():
                fig.add_trace(
                    go.Scattergl(
                        x=df["Time"],
                        y=df[lf_col],
                        name=f"{meta['stnac']}({stid_wl})-水位低頻趨勢(25h-MA)",
                        mode="lines",
                        line=dict(color="rgba(180,180,180,0.55)", width=LW.SUB),
                        connectgaps=False,
                        showlegend=False,   # 💡 改由 dash_app.py「進階圖層顯示」勾選框控制
                        visible="legendonly",
                    ),
                    row=row, col=1, secondary_y=False,
                )
                trace_meta.append({"stid": stid_wl, "field_type": "lf", "label": label})

            # Trace 3：EWMA（α=0.05）
            ew_col = f"WL_{stid_wl}_ewma"
            if ew_col in df.columns and df[ew_col].notna().any():
                fig.add_trace(
                    go.Scattergl(
                        x=df["Time"],
                        y=df[ew_col],
                        name=f"{meta['stnac']}({stid_wl})-EWMA(α=0.05)",
                        mode="lines",
                        line=dict(color="rgba(255,200,100,0.7)", width=LW.SUB),
                        connectgaps=True,
                        showlegend=False,   # 💡 改由 dash_app.py「進階圖層顯示」勾選框控制
                        visible="legendonly",
                    ),
                    row=row, col=1, secondary_y=False,
                )
                trace_meta.append({"stid": stid_wl, "field_type": "ewma", "label": label})

            # Trace 4：原始機測值（QC≠Q）
            if raw_col_name in df.columns:
                raw_mask = df[raw_col_name].notna()
                if raw_mask.any():
                    customdata = (
                        df.loc[raw_mask, qc_raw_name].fillna("?").values
                        if qc_raw_name in df.columns
                        else ["?"] * raw_mask.sum()
                    )
                    fig.add_trace(
                        go.Scattergl(
                            x=df.loc[raw_mask, "Time"],
                            y=df.loc[raw_mask, raw_col_name],
                            name=f"⚠️ {stid_wl} 原始值(QC≠Q)",
                            mode="markers",
                            marker=dict(
                                color="red", symbol="x",
                                size=5, line=dict(width=0.8),
                            ),
                            customdata=customdata,
                            hovertemplate=(
                                "%{x}<br>原始值: %{y}"
                                "<br>QC代碼: %{customdata}<extra></extra>"
                            ),
                            legend=legend_id,
                            showlegend=True,
                        ),
                        row=row, col=1, secondary_y=False,
                    )
                    trace_meta.append({"stid": stid_wl, "field_type": "raw"})

                # Trace 5：1H 平滑輔助線（均值 ± std）
                temp_df = df[["Time", col_name]].dropna().set_index("Time")
                if not temp_df.empty:
                    smoothed = temp_df.resample("1h").agg(["mean", "std"])
                    smoothed.columns = ["mean", "std"]
                    smoothed = smoothed.reset_index()
                    smoothed["Time"] += pd.Timedelta(minutes=30)

                    fig.add_trace(
                        go.Scattergl(
                            x=smoothed["Time"],
                            y=smoothed["mean"],
                            name=f"{label}-平滑(1H)",
                            mode="lines",
                            line=dict(color=color, width=LW.SUB, dash="solid"),
                            error_y=dict(
                                type="data",
                                array=smoothed["std"],
                                visible=True,
                                color=color,
                                thickness=0.5,
                                width=2,
                            ),
                            connectgaps=False,
                            showlegend=False,   # 💡 改由 dash_app.py「進階圖層顯示」勾選框控制
                            visible="legendonly",
                            opacity=0.8,
                        ),
                        row=row, col=1, secondary_y=False,
                    )
                    trace_meta.append({"stid": stid_wl, "field_type": "smooth", "label": label})

        # ── § 2  預報水位（僅主測站）──────────────────────────────────────
        primary_stids = [st for st, meta in tide_meta.items() if meta["is_primary"]]
        if primary_stids:
            p_stid = primary_stids[0]

            # 調和預報（QC=h）
            pred_col_h = f"WL_{p_stid}_pred_h"
            if pred_col_h in df.columns:
                fig.add_trace(
                    go.Scattergl(
                        x=df["Time"],
                        y=df[pred_col_h],
                        name=f"{tide_meta[p_stid]['stnac']}({p_stid})-預報(h)",
                        mode="lines+markers",
                        line=dict(color="#2ca02c", dash="dot", width=LW.SUB),
                        marker=dict(size=2.5, opacity=0.6),
                        connectgaps=True,
                        legend=legend_id,
                    ),
                    row=row, col=1, secondary_y=False,
                )
                trace_meta.append({"stid": bundle_stid, "field_type": "pred"})

            # 天文潮重建（QC=a）
            pred_col_a = f"WL_{p_stid}_pred_a"
            if pred_col_a in df.columns:
                fig.add_trace(
                    go.Scattergl(
                        x=df["Time"],
                        y=df[pred_col_a],
                        name=f"{tide_meta[p_stid]['stnac']}({p_stid})-預報(a)",
                        mode="lines+markers",
                        line=dict(color="#98df8a", dash="dot", width=LW.SUB),
                        marker=dict(size=2.5, opacity=0.6),
                        connectgaps=True,
                        legend=legend_id,
                        visible="legendonly",
                    ),
                    row=row, col=1, secondary_y=False,
                )
                trace_meta.append({"stid": bundle_stid, "field_type": "pred"})

        # ── § 3  儀器差值（右 y 軸）─────────────────────────────────────
        diff_idx = 0
        for col in df.columns:
            if not col.startswith("Diff_"):
                continue
            d_color = _DIFF_COLORS[diff_idx % len(_DIFF_COLORS)]
            fig.add_trace(
                go.Scattergl(
                    x=df["Time"],
                    y=df[col],
                    name=f"差值: {col.replace('Diff_', '')}",
                    mode="markers",
                    marker=dict(size=3, opacity=0.5, color=d_color),
                    legend=legend_id,
                ),
                row=row, col=1, secondary_y=True,
            )
            trace_meta.append({"stid": bundle_stid, "field_type": "diff"})
            diff_idx += 1

        # ── § 3.5  自動檢核（試行）彙總 3／4 的點（2026-11-24，勾選才有）────
        marks = (autoqc_marks or {}).get(bundle_stid)
        if marks is not None and not marks.empty:
            n_before = len(fig.data)
            _add_autoqc_marks(fig, marks, row, legend_id)
            trace_meta.extend({"stid": bundle_stid, "field_type": "autoqc"}
                              for _ in range(len(fig.data) - n_before))

        # ── § 4  颱風海警／陸警色帶（2026-11-20 改用 typhoon_bands，海陸警都畫）──
        add_warn_bands(fig, land_range, row=row, col=1, theme="dark", label=(row == 1))

        # ── § 5  各列 y 軸標題 ────────────────────────────────────────────
        fig.update_yaxes(
            title_text="水位(mm)",
            row=row, col=1, secondary_y=False, fixedrange=False,
        )
        fig.update_yaxes(
            title_text="水位差值(mm)",
            row=row, col=1, secondary_y=True,
            showgrid=False, fixedrange=False,
        )

        # ── § 6  高低潮極值標記 ─────────────────────────────────────────
        tideh_df  = b.get('tideh_df',  pd.DataFrame())
        tidehl_df = b.get('tidehl_df', pd.DataFrame())

        _TIDEH_STYLES = [
            ('h', '調和預報', '#2ca02c', True),
            ('a', '調和分析', '#98df8a', 'legendonly'),
        ]
        _HORL_STYLES = [
            ('H', 'triangle-up',   '高潮'),
            ('L', 'triangle-down', '低潮'),
        ]

        # 預報極值
        if not tideh_df.empty:
            for qc_val, qc_label, qc_color, _ in _TIDEH_STYLES:
                qc_vis = "legendonly"
                sub_qc = tideh_df[tideh_df['QC'].str.lower() == qc_val]
                if sub_qc.empty:
                    continue
                for horl, symbol, hl_label in _HORL_STYLES:
                    pts = sub_qc[sub_qc['HORL'].str.upper() == horl]
                    if pts.empty:
                        continue
                    fig.add_trace(
                        go.Scatter(
                            x=pts['DATATIME'], y=pts['HEIGHT'],
                            name=f"{b['stname']}({b['stid']}) [預報] {qc_label} {hl_label}",
                            mode='markers',
                            marker=dict(
                                symbol=symbol,
                                size=8,
                                color='rgba(0,0,0,0)',
                                line=dict(color=qc_color, width=1.8),
                            ),
                            legend=legend_id,
                            visible=qc_vis,
                            hovertemplate=(
                                f"%{{x}}<br>"
                                f"[預報{hl_label}] %{{y}} mm"
                                f"<br>類型：{qc_label}<extra></extra>"
                            ),
                        ),
                        row=row, col=1, secondary_y=False,
                    )
                    trace_meta.append({"stid": bundle_stid, "field_type": "tide_extreme"})

        # 觀測極值
        if not tidehl_df.empty:
            sub_q   = tidehl_df[tidehl_df['QC'].str.upper() == 'Q']
            sub_bad = tidehl_df[tidehl_df['QC'].str.upper() != 'Q']

            for horl, symbol, hl_label in _HORL_STYLES:
                pts = sub_q[sub_q['HORL'].str.upper() == horl]
                if not pts.empty:
                    fig.add_trace(
                        go.Scatter(
                            x=pts['DATATIME'], y=pts['HEIGHT'],
                            name=f"{b['stname']}({b['stid']}) [觀測] {hl_label}(QC=Q)",
                            mode='markers',
                            marker=dict(
                                symbol=symbol,
                                size=8,
                                color='#00bcd4',
                                line=dict(color='#00bcd4', width=1),
                            ),
                            hovertemplate=f"%{{x}}<br>[觀測{hl_label}] %{{y}} mm<extra></extra>", 
                            legend=legend_id,
                            visible="legendonly",
                        ),
                        row=row, col=1, secondary_y=False,
                    )
                    trace_meta.append({"stid": bundle_stid, "field_type": "tide_extreme"})

            if not sub_bad.empty:
                fig.add_trace(
                    go.Scatter(
                        x=sub_bad['DATATIME'],
                        y=sub_bad['HEIGHT'],
                        name='⚠️ 觀測極值(QC異常)',
                        mode='markers',
                        marker=dict(
                            symbol='x',
                            size=7,
                            color='red',
                            line=dict(width=1.5),
                        ),
                        customdata=sub_bad['QC'].values,
                        hovertemplate=(
                            "%{x}<br>"
                            "水位: %{y} mm<br>"
                            "QC: %{customdata}<extra></extra>"
                        ),
                        legend=legend_id,
                    ),
                    row=row, col=1, secondary_y=False,
                )
                trace_meta.append({"stid": bundle_stid, "field_type": "tide_extreme"})

    # # ── 全局 Layout ────────────────────────────────────────────────────────
    # fig.update_layout(
    #     template="plotly_dark",
    #     paper_bgcolor="#1E1E1E",
    #     plot_bgcolor="#1E1E1E",
    #     height=600 * n,
    #     hovermode="x unified",
    #     hoverlabel=dict(namelength=-1),
    #     font=dict(family=_FONT, size=12, color="#E0E0E0"),
    #     uirevision=True,
    #     margin=dict(l=50, r=60, t=60, b=50),
    #     autosize=True,
    # )
    # fig.update_xaxes(rangeslider=dict(visible=False))

    # # # ── § 7 動態配置各列的專屬圖例位置 ────────────────────────
    # # legend_layouts = {}
    # # for r in range(1, n + 1):
    # #     leg_id = "legend" if r == 1 else f"legend{r}"
        
    # #     # 取得該列主 Y 軸的名稱 (例如第 2 列是 yaxis3)
    # #     y_axis_name = "yaxis" if r == 1 else f"yaxis{r * 2 - 1}"
        
    # #     # 直接從 layout 中讀取該子圖的頂部位置 (Y domain 的上限)
    # #     y_top = fig.layout[y_axis_name].domain[1]
        
    # #     # 寫入多重圖例設定
    # #     legend_layouts[leg_id] = dict(
    # #         y=y_top,           # 對齊子圖頂端
    # #         yanchor="top",     # 錨點設為頂部
    # #         x=1.02,            # 放在圖表主體右側之外
    # #         xanchor="left",    
    # #         bgcolor="rgba(0,0,0,0)", # 透明背景 (可視需求調整)
    # #         # title_text=f"測站 {r}" # 如果需要圖例標題可開啟
    # #     )
    
    # # # 將設定更新到全域 Layout 中
    # # fig.update_layout(**legend_layouts)

    # return fig, trace_meta

    # ── § 6.5 依各列「原生圖例項目數」動態算列高，避免跨子圖重疊 ────────────
    # 現在只有主線、原始值(QC≠Q)標記、預報(h/a)、差值、高低潮極值會進圖例；
    # 低頻趨勢/EWMA/1H平滑已在上面設 showlegend=False，不計入這裡的項目數，
    # 改由 dash_app.py 的「進階圖層顯示」勾選框控制（見 toggle_adv_layers）。
    _LEGEND_ITEM_PX = 20   # 每個圖例項目估計佔用高度（px，可依實測調整）
    _LEGEND_PAD_PX  = 60   # 圖例與子圖標題間的預留空間（px）
    _ROW_BASE_PX    = 420  # 子圖本體最低高度，避免圖例項目少時被壓扁
    _ROW_GAP_PX     = 40   # 子圖間垂直間距（改用絕對像素，取代原本比例式 vertical_spacing）
    _MARGIN_T, _MARGIN_B = 60, 50   # 需與下面 margin=dict(...) 保持一致

    def _row_of_yaxis(yaxis_id) -> int:
        """trace.yaxis（'y'/'y2'/'y3'/...）→ 對應第幾列（1-indexed）。"""
        num = 1 if (not yaxis_id or yaxis_id == "y") else int(yaxis_id[1:])
        return (num + 1) // 2

    legend_counts = [0] * n
    for tr in fig.data:
        r = _row_of_yaxis(getattr(tr, "yaxis", None))
        if 1 <= r <= n and tr.showlegend is not False:
            legend_counts[r - 1] += 1

    row_px = [
        max(_ROW_BASE_PX, _LEGEND_PAD_PX + cnt * _LEGEND_ITEM_PX)
        for cnt in legend_counts
    ]
    plot_area_px    = sum(row_px) + _ROW_GAP_PX * (n - 1)
    total_height_px = plot_area_px + _MARGIN_T + _MARGIN_B

    # 記錄 make_subplots 原本安排的子圖標題文字與 domain 頂端的相對位移，
    # 等一下覆寫 domain 後，標題文字才能貼著新的子圖頂端顯示，不會錯位。
    _title_offsets = []
    for r in range(1, n + 1):
        y_axis_name = "yaxis" if r == 1 else f"yaxis{r * 2 - 1}"
        orig_top = fig.layout[y_axis_name].domain[1]
        ann_y = (fig.layout.annotations[r - 1].y
                 if (r - 1) < len(fig.layout.annotations) else orig_top)
        _title_offsets.append(ann_y - orig_top)

    # ── 全局 Layout ────────────────────────────────────────────────────────
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#1E1E1E",
        plot_bgcolor="#1E1E1E",
        height=total_height_px,
        hovermode="x unified",
        hoverlabel=dict(namelength=-1),
        font=dict(family=_FONT, size=12, color="#E0E0E0"),
        uirevision=True,
        margin=dict(l=50, r=60, t=_MARGIN_T, b=_MARGIN_B),
        autosize=True,
    )
    fig.update_xaxes(rangeslider=dict(visible=False))

    # ── § 7 依 row_px 重新分配各列 y 軸 domain，並配置專屬圖例位置 ─────────
    legend_layouts = {}
    y_cursor = 1.0
    for r in range(1, n + 1):
        px = row_px[r - 1]
        y1 = y_cursor
        # y0 = y1 - px / plot_area_px # 選淡海和淡水兩站會出包，多選幾站就不會，好像是浮點數計算的問題
        y0 = max(0.0, y1 - px / plot_area_px)
        fig.update_yaxes(domain=[y0, y1], row=r, col=1, secondary_y=False)
        fig.update_yaxes(domain=[y0, y1], row=r, col=1, secondary_y=True)
        if r - 1 < len(fig.layout.annotations):
            fig.layout.annotations[r - 1].y = y1 + _title_offsets[r - 1]
        y_cursor = y0 - _ROW_GAP_PX / plot_area_px

        leg_id = "legend" if r == 1 else f"legend{r}"
        legend_layouts[leg_id] = dict(
            y=y1,              # 對齊該列子圖頂端
            yanchor="top",
            x=1.02,            # 放在圖表主體右側之外
            xanchor="left",
            bgcolor="rgba(0,0,0,0)",
        )

    fig.update_layout(**legend_layouts)

    return fig, trace_meta

# ── 白底簡報版：忠實對應 build_water_figure() 的 §0~§6 結構 ──────────────
_TYPE_COLORS_WHITE = {2: '#1f77b4', 3: '#0d47a1', 4: '#1565c0'}   # 音波/壓力/雷達
_REPORT_COLORS = {
    "lf_trend": "rgba(100,100,100,0.6)",
    "ewma": "rgba(230,140,0,0.75)",
    "pred_a": "#2e7d32",
    "tideh_a": "#2e7d32",
}
_DIFF_COLORS_WHITE = ['#ff7f0e', '#e377c2', '#00838f']

# 主管不喜歡標楷體，簡報版改用一般黑體；若要保留標楷體，把這行換回 _FONT
_REPORT_FONT = "Microsoft JhengHei, Noto Sans TC, Arial, sans-serif"


def build_water_report_figure(bundles: list, land_range=None, zoom_range=None) -> go.Figure:
    """
    白底版水位圖，結構逐項對應 build_water_figure()，僅調整配色/背景/字體。
    """
    n = len(bundles)
    if n == 0:
        fig = go.Figure()
        fig.update_layout(
            template="plotly_white", paper_bgcolor="white", plot_bgcolor="white",
            annotations=[dict(text="無資料（bundles 為空）", x=0.5, y=0.5,
                              xref="paper", yref="paper", showarrow=False,
                              font=dict(color="#888", size=16))],
        )
        return fig

    fig = make_subplots(
        rows=n, cols=1, shared_xaxes=True, vertical_spacing=0.05,
        subplot_titles=[f"{b['stname']}({b['stid']}) - 水位細節診斷" for b in bundles],
        specs=[[{"secondary_y": True}] for _ in range(n)],
    )

    for idx, b in enumerate(bundles):
        row       = idx + 1
        df        = b["df"]
        tide_meta = b.get("tide_meta", {})

        # ----------- Claude發現這有漏洞，因為無法過濾高低潮三角形的時間，所以先撤回 -----------
        # # 套用 zoom 時間過濾（匯出範圍與畫面一致）
        # if zoom_range:
        #     try:
        #         ts0 = pd.to_datetime(zoom_range["x_start"])
        #         ts1 = pd.to_datetime(zoom_range["x_end"])
        #         df  = df[(df["Time"] >= ts0) & (df["Time"] <= ts1)].copy()
        #     except Exception:
        #         pass
        # ----------- 撤回完畢 ------------

        # § 0 舊系統降級路徑
        if not tide_meta:
            if "Obs" in df.columns:
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df["Obs"], name=f"{b['stname']}({b['stid']}) 水位",
                    mode="lines+markers", line=dict(color="#1f77b4", width=LW.MAIN),
                    marker=dict(size=3, opacity=0.6), connectgaps=False,
                ), row=row, col=1, secondary_y=False)
            if "Pre" in df.columns:
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df["Pre"], name=f"{b['stname']}({b['stid']}) 調和預報",
                    mode="lines", line=dict(color="#2ca02c", dash="dot", width=LW.SUB),
                    connectgaps=True,
                ), row=row, col=1, secondary_y=False)
            fig.update_yaxes(title_text="水位(mm)", row=row, col=1,
                             secondary_y=False, fixedrange=False)
            add_warn_bands(fig, land_range, row=row, col=1, theme="light", label=(row == 1))
            continue

        # § 1 各儀器水位線
        for stid_wl, meta in sorted(tide_meta.items()):
            col_name, raw_col_name, qc_raw_name = (
                f"WL_{stid_wl}", f"WL_{stid_wl}_raw", f"QC_{stid_wl}_raw"
            )
            if col_name not in df.columns:
                continue

            label = (f"{meta.get('stnac', '未知')}({stid_wl})-{meta.get('type_desc', '未知類型')}"
                     f"{'(主)' if meta['is_primary'] else ''}")
            color = _TYPE_COLORS_WHITE.get(meta["type"], "gray")
            dash_style = "solid" if meta["is_primary"] else "dash"

            q_mask = df[col_name].notna()
            fig.add_trace(go.Scatter(
                x=df.loc[q_mask, "Time"], y=df.loc[q_mask, col_name], name=label,
                mode="lines+markers", line=dict(color=color, dash=dash_style, width=LW.MAIN),
                marker=dict(size=3, opacity=0.6), connectgaps=False,
            ), row=row, col=1, secondary_y=False)

            lf_col = f"WL_{stid_wl}_lf"
            if lf_col in df.columns and df[lf_col].notna().any():
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df[lf_col],
                    name=f"{meta['stnac']}({stid_wl})-水位低頻趨勢(25h-MA)",
                    mode="lines", line=dict(color=_REPORT_COLORS["lf_trend"], width=LW.SUB),
                    connectgaps=False, visible="legendonly",
                ), row=row, col=1, secondary_y=False)

            ew_col = f"WL_{stid_wl}_ewma"
            if ew_col in df.columns and df[ew_col].notna().any():
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df[ew_col], name=f"{meta['stnac']}({stid_wl})-EWMA(α=0.05)",
                    mode="lines", line=dict(color=_REPORT_COLORS["ewma"], width=LW.SUB),
                    connectgaps=True, visible="legendonly",
                ), row=row, col=1, secondary_y=False)

            if raw_col_name in df.columns:
                raw_mask = df[raw_col_name].notna()
                if raw_mask.any():
                    customdata = (df.loc[raw_mask, qc_raw_name].fillna("?").values
                                  if qc_raw_name in df.columns else ["?"] * raw_mask.sum())
                    fig.add_trace(go.Scatter(
                        x=df.loc[raw_mask, "Time"], y=df.loc[raw_mask, raw_col_name],
                        name=f"⚠️ {stid_wl} 原始值(QC≠Q)", mode="markers",
                        marker=dict(color="red", symbol="x", size=5, line=dict(width=0.8)),
                        customdata=customdata,
                        hovertemplate="%{x}<br>原始值: %{y}<br>QC代碼: %{customdata}<extra></extra>",
                        showlegend=True,
                    ), row=row, col=1, secondary_y=False)

            temp_df = df[["Time", col_name]].dropna().set_index("Time")
            if not temp_df.empty:
                smoothed = temp_df.resample("1h").agg(["mean", "std"])
                smoothed.columns = ["mean", "std"]
                smoothed = smoothed.reset_index()
                smoothed["Time"] += pd.Timedelta(minutes=30)
                fig.add_trace(go.Scatter(
                    x=smoothed["Time"], y=smoothed["mean"], name=f"{label}-平滑(1H)",
                    mode="lines", line=dict(color=color, width=LW.SUB, dash="solid"),
                    error_y=dict(type="data", array=smoothed["std"], visible=True,
                                 color=color, thickness=0.5, width=2),
                    connectgaps=False, visible="legendonly", opacity=0.8,
                ), row=row, col=1, secondary_y=False)

        # § 2 預報水位（僅主測站）
        primary_stids = [st for st, meta in tide_meta.items() if meta["is_primary"]]
        if primary_stids:
            p_stid = primary_stids[0]
            pred_col_h = f"WL_{p_stid}_pred_h"
            if pred_col_h in df.columns:
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df[pred_col_h],
                    name=f"{tide_meta[p_stid]['stnac']}({p_stid})-預報(h)",
                    mode="lines+markers", line=dict(color="#2ca02c", dash="dot", width=LW.SUB),
                    marker=dict(size=2.5, opacity=0.6), connectgaps=True,
                ), row=row, col=1, secondary_y=False)
            pred_col_a = f"WL_{p_stid}_pred_a"
            if pred_col_a in df.columns:
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df[pred_col_a],
                    name=f"{tide_meta[p_stid]['stnac']}({p_stid})-預報(a)",
                    mode="lines+markers", line=dict(color=_REPORT_COLORS["pred_a"], dash="dot", width=LW.SUB),
                    marker=dict(size=2.5, opacity=0.6), connectgaps=True, visible="legendonly",
                ), row=row, col=1, secondary_y=False)

        # § 3 儀器差值（右 y 軸）
        diff_idx = 0
        for col in df.columns:
            if not col.startswith("Diff_"):
                continue
            fig.add_trace(go.Scatter(
                x=df["Time"], y=df[col], name=f"差值: {col.replace('Diff_', '')}",
                mode="markers",
                marker=dict(size=3, opacity=0.5, color=_DIFF_COLORS_WHITE[diff_idx % 3]),
            ), row=row, col=1, secondary_y=True)
            diff_idx += 1

        # § 4 颱風海警／陸警色帶（2026-11-20 改用 typhoon_bands）
        add_warn_bands(fig, land_range, row=row, col=1, theme="light", label=(row == 1))

        # § 5 y 軸標題
        fig.update_yaxes(title_text="水位(mm)", row=row, col=1, secondary_y=False, fixedrange=False)
        fig.update_yaxes(title_text="水位差值(mm)", row=row, col=1, secondary_y=True,
                         showgrid=False, fixedrange=False)

        # § 6 高低潮極值標記
        tideh_df  = b.get('tideh_df',  pd.DataFrame())
        tidehl_df = b.get('tidehl_df', pd.DataFrame())
        _TIDEH_STYLES = [('h', '調和預報', '#2ca02c', True),
                         ('a', '調和分析', _REPORT_COLORS["tideh_a"], 'legendonly')]
        _HORL_STYLES = [('H', 'triangle-up', '高潮'), ('L', 'triangle-down', '低潮')]

        if not tideh_df.empty:
            # for qc_val, qc_label, qc_color, qc_vis in _TIDEH_STYLES:
            for qc_val, qc_label, qc_color, _ in _TIDEH_STYLES:
                qc_vis = "legendonly"   # 高低潮標記改為預設隱藏，需要時手動開啟    
                sub_qc = tideh_df[tideh_df['QC'].str.lower() == qc_val]
                if sub_qc.empty:
                    continue
                for horl, symbol, hl_label in _HORL_STYLES:
                    pts = sub_qc[sub_qc['HORL'].str.upper() == horl]
                    if pts.empty:
                        continue
                    fig.add_trace(go.Scatter(
                        x=pts['DATATIME'], y=pts['HEIGHT'],
                        name=f"{b['stname']}({b['stid']}) [預報] {qc_label} {hl_label}",
                # for horl, symbol, hl_label in _HORL_STYLES:
                #     pts = sub_qc[sub_qc['HORL'].str.upper() == horl]
                #     if pts.empty:
                #         continue
                #     fig.add_trace(go.Scatter(
                #         x=pts['DATATIME'], y=pts['HEIGHT'], name=f"[預報] {qc_label} {hl_label}",
                        mode='markers',
                        marker=dict(symbol=symbol, size=8, color='rgba(0,0,0,0)',
                                   line=dict(color=qc_color, width=1.8)),
                        visible=qc_vis,
                        hovertemplate=f"%{{x}}<br>[預報{hl_label}] %{{y}} mm<br>類型：{qc_label}<extra></extra>",
                    ), row=row, col=1, secondary_y=False)

        if not tidehl_df.empty:
            sub_q   = tidehl_df[tidehl_df['QC'].str.upper() == 'Q']
            sub_bad = tidehl_df[tidehl_df['QC'].str.upper() != 'Q']
            for horl, symbol, hl_label in _HORL_STYLES:
                pts = sub_q[sub_q['HORL'].str.upper() == horl]
                if not pts.empty:
                    fig.add_trace(go.Scatter(
                        x=pts['DATATIME'], y=pts['HEIGHT'],
                        name=f"{b['stname']}({b['stid']}) [觀測] {hl_label}(QC=Q)",
                # if not pts.empty:
                #     fig.add_trace(go.Scatter(
                #         x=pts['DATATIME'], y=pts['HEIGHT'], name=f"[觀測] {hl_label}(QC=Q)",
                        mode='markers',
                        marker=dict(symbol=symbol, size=8, color='#00838f', line=dict(color='#00838f', width=1)),
                        hovertemplate=f"%{{x}}<br>[觀測{hl_label}] %{{y}} mm<extra></extra>", visible="legendonly",   # ← 預設隱藏
                    ), row=row, col=1, secondary_y=False)
            if not sub_bad.empty:
                fig.add_trace(go.Scatter(
                    x=sub_bad['DATATIME'], y=sub_bad['HEIGHT'], name=f"⚠️ {b['stname']}({b['stid']}) 觀測極值(QC異常)",
                # fig.add_trace(go.Scatter(
                #     x=sub_bad['DATATIME'], y=sub_bad['HEIGHT'], name='⚠️ 觀測極值(QC異常)',
                    mode='markers', marker=dict(symbol='x', size=7, color='red', line=dict(width=1.5)),
                    customdata=sub_bad['QC'].values,
                    hovertemplate="%{x}<br>水位: %{y} mm<br>QC: %{customdata}<extra></extra>",
                    visible="legendonly",   # ← 與 sub_q 預設狀態對齊
                ), row=row, col=1, secondary_y=False)

    fig.update_layout(
        template="plotly_white", paper_bgcolor="white", plot_bgcolor="white",
        height=600 * n, hovermode="x unified", hoverlabel=dict(namelength=-1),
        font=dict(family=_REPORT_FONT, size=12, color="#1a1a1a"),
        margin=dict(l=50, r=60, t=60, b=50), autosize=True,
    )
    fig.update_xaxes(rangeslider=dict(visible=False))
    # 若有 zoom 範圍，固定 x 軸（使匯出與畫面一致）
    if zoom_range:
        try:
            ts0 = pd.to_datetime(zoom_range["x_start"])
            ts1 = pd.to_datetime(zoom_range["x_end"])
            fig.update_xaxes(range=[ts0, ts1])
        except Exception as e:
            print(f"[DEBUG zoom export] {e}")   # 先留著看有沒有報錯
    return fig