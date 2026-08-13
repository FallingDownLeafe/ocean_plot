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
from plotly.subplots import make_subplots


# 儀器類型色系（與原版 draw_water_only 完全相同）
_TYPE_COLORS = {2: '#1f77b4', 3: '#0d47a1', 4: '#64b5f6'}
_DIFF_COLORS = ['#ff7f0e', '#e377c2', '#17becf']

# _FONT = "Noto Sans TC, PingFang TC, Microsoft JhengHei, Arial, sans-serif"
_FONT = "標楷體, PingFang TC, Noto Sans CJK TC, Arial, sans-serif"


def build_water_figure(bundles: list, land_range=None, typhoon_label: str | None = None) -> go.Figure:
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
    land_range : tuple | None
    typhoon_label: str | None = None,   # ← 新增
        颱風陸上警報時段 (beg, end)，若無則傳 None。

    Returns
    -------
    go.Figure
        可直接賦值給 dcc.Graph(figure=...) 的 Plotly Figure 物件。
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
        return fig

    # ── 子圖結構：每測站一列，共享 x 軸，雙 y 軸 ────────────────────────────
    fig = make_subplots(
        rows=n,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        # subplot_titles=[
        #     f"{b['stname']}({b['stid']}) - 水位細節診斷"
        #     for b in bundles
        # ],
        subplot_titles=[
            (f"【{typhoon_label}】" if typhoon_label else "") + f"{b['stname']}({b['stid']}) - 水位細節診斷"
            for b in bundles
        ],
        specs=[[{"secondary_y": True}] for _ in range(n)],
    )

    for idx, b in enumerate(bundles):
        row       = idx + 1
        df        = b["df"]
        tide_meta = b.get("tide_meta", {})

        # ── § 0  舊系統降級路徑（無 tide_meta，僅 Obs / Pre 欄位）────────────
        # fetch_tide_instruments() 找不到 stid_obs 時 fetch_bundle 回傳空 tide_meta，
        # 此時 df 只有 Obs（觀測值）和 Pre（調和預報）兩欄，直接畫線即可。
        if not tide_meta:
            if "Obs" in df.columns:
                fig.add_trace(
                    # go.Scatter(
                    go.Scattergl(          # ← 從 Scatter 改回 Scattergl
                        x=df["Time"], y=df["Obs"],
                        name=f"{b['stname']}({b['stid']}) 水位",
                        mode="lines+markers",
                        line=dict(color="#1f77b4", width=1.2),
                        marker=dict(size=3, opacity=0.6),
                        connectgaps=False,
                    ),
                    row=row, col=1, secondary_y=False,
                )
            if "Pre" in df.columns:
                fig.add_trace(
                    # go.Scatter(
                    go.Scattergl(          # ← 從 Scatter 改回 Scattergl
                        x=df["Time"], y=df["Pre"],
                        name=f"{b['stname']}({b['stid']}) 調和預報",
                        mode="lines",
                        line=dict(color="#2ca02c", dash="dot", width=1.2),
                        connectgaps=True,
                    ),
                    row=row, col=1, secondary_y=False,
                )
            fig.update_yaxes(title_text="水位(mm)", row=row, col=1,
                             secondary_y=False, fixedrange=False)
            if land_range:
                fig.add_vrect(x0=land_range[0], x1=land_range[1],
                              fillcolor="Red", opacity=0.1,
                              layer="below", line_width=0, row=row, col=1)

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
            # connectgaps=False → 異常區間自動斷線，不補線，視覺上更誠實
            q_mask = df[col_name].notna()
            fig.add_trace(
                # go.Scatter(
                go.Scattergl(          # ← 從 Scatter 改回 Scattergl
                    x=df.loc[q_mask, "Time"],
                    y=df.loc[q_mask, col_name],
                    name=label,
                    mode="lines+markers",
                    line=dict(color=color, dash=dash_style, width=1.2),
                    marker=dict(size=3, opacity=0.6),
                    connectgaps=False,
                ),
                row=row, col=1, secondary_y=False,
            )

            # Trace 2：低頻趨勢線（25h 移動平均，主測站才有此欄）— 預設隱藏
            lf_col = f"WL_{stid_wl}_lf"
            if lf_col in df.columns and df[lf_col].notna().any():
                fig.add_trace(
                    # go.Scatter(
                    go.Scattergl(          # ← 從 Scatter 改回 Scattergl
                        x=df["Time"],
                        y=df[lf_col],
                        name=f"{meta['stnac']}({stid_wl})-水位低頻趨勢(25h-MA)",
                        mode="lines",
                        line=dict(color="rgba(180,180,180,0.55)", width=1.2),
                        connectgaps=False,
                        visible="legendonly",
                    ),
                    row=row, col=1, secondary_y=False,
                )

            # Trace 3：EWMA（α=0.05）— 預設隱藏
            ew_col = f"WL_{stid_wl}_ewma"
            if ew_col in df.columns and df[ew_col].notna().any():
                fig.add_trace(
                    # go.Scatter(
                    go.Scattergl(          # ← 從 Scatter 改回 Scattergl
                        x=df["Time"],
                        y=df[ew_col],
                        name=f"{meta['stnac']}({stid_wl})-EWMA(α=0.05)",
                        mode="lines",
                        line=dict(color="rgba(255,200,100,0.7)", width=1.2),
                        connectgaps=True,
                        visible="legendonly",
                    ),
                    row=row, col=1, secondary_y=False,
                )

            # Trace 4：原始機測值（QC≠Q）— 紅叉，hover 顯示 QC 代碼
            if raw_col_name in df.columns:
                raw_mask = df[raw_col_name].notna()
                if raw_mask.any():
                    customdata = (
                        df.loc[raw_mask, qc_raw_name].fillna("?").values
                        if qc_raw_name in df.columns
                        else ["?"] * raw_mask.sum()
                    )
                    fig.add_trace(
                        # go.Scatter(
                        go.Scattergl(          # ← 從 Scatter 改回 Scattergl
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
                            showlegend=True,
                        ),
                        row=row, col=1, secondary_y=False,
                    )

                # Trace 5：1H 平滑輔助線（均值 ± std）— 預設隱藏
                # 以 col_name（校正值）為基礎計算，與原版相同
                temp_df = df[["Time", col_name]].dropna().set_index("Time")
                if not temp_df.empty:
                    smoothed = (
                        temp_df
                        .resample("1h")
                        .agg(["mean", "std"])
                    )
                    smoothed.columns = ["mean", "std"]
                    smoothed = smoothed.reset_index()
                    # 時間戳記往後推 30 分鐘（置中），修正視覺上的相位延遲
                    smoothed["Time"] += pd.Timedelta(minutes=30)

                    fig.add_trace(
                        # go.Scatter(
                        go.Scattergl(          # ← 從 Scatter 改回 Scattergl
                            x=smoothed["Time"],
                            y=smoothed["mean"],
                            name=f"{label}-平滑(1H)",
                            mode="lines",
                            line=dict(color=color, width=1, dash="solid"),
                            error_y=dict(
                                type="data",
                                array=smoothed["std"],
                                visible=True,
                                color=color,
                                thickness=0.5,
                                width=2,
                            ),
                            connectgaps=False,
                            visible="legendonly",
                            opacity=0.8,
                        ),
                        row=row, col=1, secondary_y=False,
                    )

        # ── § 2  預報水位（僅主測站）──────────────────────────────────────
        primary_stids = [
            st for st, meta in tide_meta.items() if meta["is_primary"]
        ]
        if primary_stids:
            p_stid = primary_stids[0]

            # 調和預報（QC=h）— 綠色點線
            pred_col_h = f"WL_{p_stid}_pred_h"
            if pred_col_h in df.columns:
                fig.add_trace(
                    # go.Scatter(
                    go.Scattergl(          # ← 從 Scatter 改回 Scattergl
                        x=df["Time"],
                        y=df[pred_col_h],
                        name=f"{tide_meta[p_stid]['stnac']}({p_stid})-預報(h)",
                        mode="lines+markers",
                        line=dict(color="#2ca02c", dash="dot", width=1.2),
                        marker=dict(size=2.5, opacity=0.6),
                        connectgaps=True,
                    ),
                    row=row, col=1, secondary_y=False,
                )

            # 天文潮重建（QC=a）— 淺綠色點線，預設隱藏
            pred_col_a = f"WL_{p_stid}_pred_a"
            if pred_col_a in df.columns:
                fig.add_trace(
                    # go.Scatter(
                    go.Scattergl(          # ← 從 Scatter 改回 Scattergl
                        x=df["Time"],
                        y=df[pred_col_a],
                        name=f"{tide_meta[p_stid]['stnac']}({p_stid})-預報(a)",
                        mode="lines+markers",
                        line=dict(color="#98df8a", dash="dot", width=1.2),
                        marker=dict(size=2.5, opacity=0.6),
                        connectgaps=True,
                        visible="legendonly",
                    ),
                    row=row, col=1, secondary_y=False,
                )

        # ── § 3  儀器差值（右 y 軸）─────────────────────────────────────
        diff_idx = 0
        for col in df.columns:
            if not col.startswith("Diff_"):
                continue
            d_color = _DIFF_COLORS[diff_idx % len(_DIFF_COLORS)]
            fig.add_trace(
                # go.Scatter(
                go.Scattergl(          # ← 從 Scatter 改回 Scattergl
                    x=df["Time"],
                    y=df[col],
                    name=f"差值: {col.replace('Diff_', '')}",
                    mode="markers",
                    marker=dict(size=3, opacity=0.5, color=d_color),
                ),
                row=row, col=1, secondary_y=True,
            )
            diff_idx += 1

        # ── § 4  颱風陸上警報色帶 ────────────────────────────────────────
        if land_range:
            fig.add_vrect(
                x0=land_range[0],
                x1=land_range[1],
                fillcolor="Red",
                opacity=0.1,
                layer="below",
                line_width=0,
                row=row, col=1,
            )

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
        # 6-A: 天文潮/調和預報極值（tideh）
        #   空心三角 = 預報，顏色與預報水位線一致
        #   QC='h'（調和預報）預設顯示；QC='a'（重建分析）預設隱藏
        tideh_df  = b.get('tideh_df',  pd.DataFrame())
        tidehl_df = b.get('tidehl_df', pd.DataFrame())

        _TIDEH_STYLES = [
            # (qc_val, label,          color,     default_vis)
            ('h', '調和預報', '#2ca02c', True),
            ('a', '天文潮重建', '#98df8a', 'legendonly'),
        ]
        _HORL_STYLES = [
            # (horl, symbol,          hl_label)
            ('H', 'triangle-up',   '高潮'),
            ('L', 'triangle-down', '低潮'),
        ]

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
                #     fig.add_trace(
                #         go.Scatter(
                #             x=pts['DATATIME'],
                #             y=pts['HEIGHT'],
                #             name=f"[預報] {qc_label} {hl_label}",
                            mode='markers',
                            marker=dict(
                                symbol=symbol,
                                size=8,
                                color='rgba(0,0,0,0)',       # 空心（透明填色）
                                line=dict(color=qc_color, width=1.8),
                            ),
                            visible=qc_vis,
                            hovertemplate=(
                                f"%{{x}}<br>"
                                f"[預報{hl_label}] %{{y}} mm"
                                f"<br>類型：{qc_label}<extra></extra>"
                            ),
                        ),
                        row=row, col=1, secondary_y=False,
                    )

        # 6-B: 觀測高低潮（tidehl）
        #   實心三角 = 觀測（QC=Q），紅X = QC 異常
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
                #     fig.add_trace(
                #         go.Scatter(
                #             x=pts['DATATIME'],
                #             y=pts['HEIGHT'],
                #             name=f"[觀測] {hl_label}(QC=Q)",
                            mode='markers',
                            marker=dict(
                                symbol=symbol,
                                size=8,
                                color='#00bcd4',             # 實心青藍，與預報空心形成對比
                                line=dict(color='#00bcd4', width=1),
                            ),
                            hovertemplate=f"%{{x}}<br>[觀測{hl_label}] %{{y}} mm<extra></extra>", visible="legendonly",   # ← 預設隱藏
                            # 這種寫法不work：括號內用逗號區隔會變成tuple，但hovertemplate只接受字串，所以想設定隱藏參數就不能用括號。
                            # hovertemplate=(
                            #     f"%{{x}}<br>"
                            #     f"[觀測{hl_label}] %{{y}} mm<extra></extra>",
                            #     # visible="legendonly",   # ← 預設隱藏
                            # ),
                        ),
                        row=row, col=1, secondary_y=False,
                    )

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
                    ),
                    row=row, col=1, secondary_y=False,
                )

    # ── 全局 Layout（與原版保持一致，補上背景色與字體） ───────────────────────
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#1E1E1E",
        plot_bgcolor="#1E1E1E",
        height=600 * n,
        hovermode="x unified",
        hoverlabel=dict(namelength=-1),
        font=dict(family=_FONT, size=12, color="#E0E0E0"),
        # uirevision=True 在原版中鎖定圖例點擊時的縮放視圖；
        # Dash 整合後應改為動態版本號（由 update_graph_from_bundles 傳入），
        # 才能在 push 新資料時正確重置 zoom。
        # 此處保留 True，作為獨立函式的預設值。
        uirevision=True,
        margin=dict(l=50, r=60, t=60, b=50),
        autosize=True,
    )

    # x 軸滑桿（所有子圖共用；深色配色與原版一致）
    fig.update_xaxes(rangeslider=dict(visible=False))          # 先全部關掉
    
    # 橫桿好像沒什麼用到，先不要用。
    # fig.update_xaxes(
    #     rangeslider=dict(
    #         visible=True,
    #         thickness=0.05,
    #         bgcolor="#333333",
    #         borderwidth=1,
    #         yaxis=dict(rangemode="fixed"),   # ← 加這行，固定預覽圖的 y 軸範圍讓它不亂跳
    #     ),
    #     row=n, col=1, # secondary_y=False, 
    # )

    return fig

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


def build_water_report_figure(bundles: list, land_range=None) -> go.Figure:
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

        # § 0 舊系統降級路徑
        if not tide_meta:
            if "Obs" in df.columns:
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df["Obs"], name=f"{b['stname']}({b['stid']}) 水位",
                    mode="lines+markers", line=dict(color="#1f77b4", width=1.2),
                    marker=dict(size=3, opacity=0.6), connectgaps=False,
                ), row=row, col=1, secondary_y=False)
            if "Pre" in df.columns:
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df["Pre"], name=f"{b['stname']}({b['stid']}) 調和預報",
                    mode="lines", line=dict(color="#2ca02c", dash="dot", width=1.2),
                    connectgaps=True,
                ), row=row, col=1, secondary_y=False)
            fig.update_yaxes(title_text="水位(mm)", row=row, col=1,
                             secondary_y=False, fixedrange=False)
            if land_range:
                fig.add_vrect(x0=land_range[0], x1=land_range[1], fillcolor="Red",
                              opacity=0.1, layer="below", line_width=0, row=row, col=1)
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
                mode="lines+markers", line=dict(color=color, dash=dash_style, width=1.2),
                marker=dict(size=3, opacity=0.6), connectgaps=False,
            ), row=row, col=1, secondary_y=False)

            lf_col = f"WL_{stid_wl}_lf"
            if lf_col in df.columns and df[lf_col].notna().any():
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df[lf_col],
                    name=f"{meta['stnac']}({stid_wl})-水位低頻趨勢(25h-MA)",
                    mode="lines", line=dict(color=_REPORT_COLORS["lf_trend"], width=1.2),
                    connectgaps=False, visible="legendonly",
                ), row=row, col=1, secondary_y=False)

            ew_col = f"WL_{stid_wl}_ewma"
            if ew_col in df.columns and df[ew_col].notna().any():
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df[ew_col], name=f"{meta['stnac']}({stid_wl})-EWMA(α=0.05)",
                    mode="lines", line=dict(color=_REPORT_COLORS["ewma"], width=1.2),
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
                    mode="lines", line=dict(color=color, width=1, dash="solid"),
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
                    mode="lines+markers", line=dict(color="#2ca02c", dash="dot", width=1.2),
                    marker=dict(size=2.5, opacity=0.6), connectgaps=True,
                ), row=row, col=1, secondary_y=False)
            pred_col_a = f"WL_{p_stid}_pred_a"
            if pred_col_a in df.columns:
                fig.add_trace(go.Scatter(
                    x=df["Time"], y=df[pred_col_a],
                    name=f"{tide_meta[p_stid]['stnac']}({p_stid})-預報(a)",
                    mode="lines+markers", line=dict(color=_REPORT_COLORS["pred_a"], dash="dot", width=1.2),
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

        # § 4 颱風陸上警報色帶
        if land_range:
            fig.add_vrect(x0=land_range[0], x1=land_range[1], fillcolor="Red",
                          opacity=0.1, layer="below", line_width=0, row=row, col=1)

        # § 5 y 軸標題
        fig.update_yaxes(title_text="水位(mm)", row=row, col=1, secondary_y=False, fixedrange=False)
        fig.update_yaxes(title_text="水位差值(mm)", row=row, col=1, secondary_y=True,
                         showgrid=False, fixedrange=False)

        # § 6 高低潮極值標記
        tideh_df  = b.get('tideh_df',  pd.DataFrame())
        tidehl_df = b.get('tidehl_df', pd.DataFrame())
        _TIDEH_STYLES = [('h', '調和預報', '#2ca02c', True),
                         ('a', '天文潮重建', _REPORT_COLORS["tideh_a"], 'legendonly')]
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
    return fig