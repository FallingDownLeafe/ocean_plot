"""
build_diagnostic_figure.py
===========================
純函式版的 draw_diagnostic()（四子圖海洋參數診斷圖：水位／海氣象／暴潮與氣壓／波浪特性）。

移植自 ocean_plot_dash.py 的 draw_diagnostic()，供 Dash 的「海洋參數（唯讀）」
頁籤（tab-diagnostic）使用。與 build_water_figure.py／build_surge_report_figure.py
的 build_surge_interactive_figure() 屬同一類：輸入 bundles，回傳 go.Figure，
不含任何 HTML 輸出或 webbrowser 副作用。

與原版 draw_diagnostic() 的差異
--------------------------------
1. 移除「每 3 站一頁、各自 write_chart_html() + webbrowser.open()」的分頁邏輯。
   改為單一可捲動的 Figure，一次涵蓋所有傳入的 bundles——與 build_water_figure()
   （水位頁籤最多 45 站也不分頁，只靠瀏覽器捲動）的處理方式一致。
   站數的合理上限沿用 Tkinter 端既有限制（mode="full" 建議 12 站以內，
   詳見 ocean_plot_dash.py 的 LIMIT_STATIONS），故最高約 12 * 800px ≈ 9600px，
   在此不另加防呆。
2. 新增 typhoon_label 參數，行為與 build_water_figure() 一致：非 None 時於每個
   子圖標題前綴「【颱風名】」。原版 draw_diagnostic() 沒有這個參數。
3. 移除 remove_buttons / config 相關設定——這些原本是給 write_chart_html() 用的
   modeBar 設定，Dash 版由 dash_app.py 的 dcc.Graph(config=...) 統一管理
   （所有頁籤共用同一個 config，見 §已知限制）。

用途與定位
----------
本圖表僅供「唯讀檢視」。QC 框選（Box Select）→ SQL 產生功能僅在「水位時序與
QC」頁籤（build_water_figure 對應）提供。因此：
    - 本模組不產生 trace_meta（curveNumber → STID 對應表）。
    - dash_app.py 的 render_figure callback 在 tab-diagnostic 分支，
      trace-meta-store 應回傳 no_update（維持水位頁籤最後一次算出的內容不變）。
    - dash_app.py 的 on_selection callback 已加入 active_tab 判斷（見該檔案
      Patch 9），非 tab-water 時直接略過，不會用本圖表的 curveNumber 誤產生 SQL。

已知限制
--------
- main-graph 的 dcc.Graph config（含 modeBarButtonsToAdd 的 Box Select／Lasso）
  是所有頁籤共用的單一設定，並未針對本頁籤特別移除框選工具；使用者仍可在此
  頁籤看到框選按鈕，但拖曳框選不會有任何作用（on_selection 的 Patch 9 guard
  會直接跳過）。若要在 UI 上完全隱藏這兩顆按鈕，需要把 dcc.Graph 的 config
  改成依 active_tab 動態輸出，屬於較大的改動，目前判斷非必要，先不處理。
- 尚未提供白底 PNG 匯出（對應 build_water_report_figure() 的角色）。若未來
  需要，可另外新增 build_diagnostic_report_figure()，白底版通常只需要調整
  配色與 template，邏輯可直接複用本檔案的 trace 建構部分。
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 與互動式深色頁籤（水位／VdC／暴潮偏差）一致的字體優先序
_FONT = "標楷體, Noto Sans TC, Segoe UI, Arial, sans-serif"

# 儀器色系與差值色系（與 draw_diagnostic / build_water_figure 完全一致）
_TYPE_COLORS = {2: '#1f77b4', 3: '#0d47a1', 4: '#64b5f6'}
_DIFF_COLORS = ['#ff7f0e', '#e377c2', '#17becf']

STATION_BLOCK_HEIGHT = 800   # 每站的高度預算（px），涵蓋該站的上、下兩排子圖
GAP_PX = 120                 # 子圖垂直間距所需的實際像素（文字 + 滑桿 + 緩衝）
PX_SLIDER = 30                # 滑桿高度


def build_diagnostic_figure(
    bundles: list,
    land_range: tuple | None = None,
    typhoon_label: str | None = None,
) -> go.Figure:
    """
    移植自 draw_diagnostic(bundles, land_range)，回傳單一 go.Figure（不分頁）。

    Parameters
    ----------
    bundles : list[dict]
        fetch_bundle() 回傳值的清單，每個 bundle 含 stid、stname、df、
        src_ids、src_names、tide_meta、mr_full、mr_month。
    land_range : tuple | None
        颱風陸上警報時段 (beg, end)，無則傳 None。
    typhoon_label : str | None
        颱風名稱標籤（如 "丹娜絲(2504L)"），非 None 時前綴於每個子圖標題。

    Returns
    -------
    go.Figure
        可直接賦值給 dcc.Graph(figure=...) 的 Plotly Figure 物件。
        bundles 為空時回傳帶提示文字的空白深色圖，不拋例外。
    """
    n_stations = len(bundles)
    if n_stations == 0:
        fig = go.Figure()
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#1E1E1E",
            plot_bgcolor="#1E1E1E",
            annotations=[dict(
                text="無資料（bundles 為空）",
                x=0.5, y=0.5, xref="paper", yref="paper",
                showarrow=False, font=dict(color="#888", size=16),
            )],
        )
        return fig

    # ── 空間設定（與原版相同公式，改為對「全部」bundles 一次計算，不分頁）──────
    total_height = STATION_BLOCK_HEIGHT * n_stations
    spacing_ratio = GAP_PX / total_height if total_height > 0 else 0.1
    slider_ratio = PX_SLIDER / total_height if total_height > 0 else 0.05

    ty_prefix = f"【{typhoon_label}】" if typhoon_label else ""

    fig = make_subplots(
        rows=n_stations * 2, cols=2,
        subplot_titles=[
            f"{ty_prefix}{b['stname']}({b['stid']}) - {t}"
            for b in bundles for t in
            ["水位 (Obs/Pre)",
             "海氣象 (風/流/溫)",
             "暴潮與氣壓 (暴潮偏差/氣壓)",
             "波浪特性 (示性波高/平均週期)"]
        ],
        specs=[[{"secondary_y": True}] * 2] * (n_stations * 2),
        vertical_spacing=spacing_ratio,
    )

    for idx, b in enumerate(bundles):
        r_top = idx * 2 + 1
        r_bot = r_top + 1
        df = b['df']
        lbl = f"{b['stname']}({b['stid']})"
        sids = b['src_ids']
        src_names = b.get('src_names', {})
        mr_full = b.get('mr_full')
        mr_month = b.get('mr_month')

        def get_src_label(src_id, default_param_name):
            """根據 src_id 和參數名稱，生成格式為 '測站ID(中文名)-參數' 的標籤"""
            if src_id == 'None':
                return f"無數據-{default_param_name}"
            src_cname = src_names.get(src_id, "未知")
            return f"{src_cname}({src_id})-{default_param_name}"

        tide_meta = b.get('tide_meta', {})

        # =========================================================
        # (1,1) 左上：多儀器水位 + 預報水位 + 儀器間差值
        # =========================================================
        for stid_wl, meta in sorted(tide_meta.items()):
            col_name     = f'WL_{stid_wl}'
            raw_col_name = f'WL_{stid_wl}_raw'
            qc_raw_name  = f'QC_{stid_wl}_raw'
            if col_name in df.columns:
                type_val = meta['type']
                type_desc = meta['type_desc']
                stnac = meta['stnac']
                is_primary_marker = '(主)' if meta['is_primary'] else ''

                label = f"{stnac}({stid_wl})-{type_desc}{is_primary_marker}"
                color = _TYPE_COLORS.get(type_val, 'gray')
                dash_style = 'solid' if meta['is_primary'] else 'dash'
                is_hidden = 'legendonly' if not meta['is_primary'] else True

                fig.add_trace(go.Scattergl(
                    x=df['Time'], y=df[col_name],
                    name=label,
                    mode='lines+markers',
                    line=dict(color=color, dash=dash_style, width=1.2),
                    marker=dict(size=2.5, opacity=0.6),
                    connectgaps=False,
                    visible=is_hidden
                ), row=r_top, col=1)

                lf_col = f'WL_{stid_wl}_lf'
                if lf_col in df.columns and df[lf_col].notna().any():
                    fig.add_trace(go.Scattergl(
                        x=df['Time'], y=df[lf_col],
                        name=f"{meta['stnac']}({stid_wl})-水位低頻趨勢(25h-MA)",
                        mode='lines',
                        line=dict(color='rgba(180,180,180,0.55)', width=1.2),
                        connectgaps=False,
                        visible=is_hidden
                    ), row=r_top, col=1, secondary_y=False)

                ew_col = f'WL_{stid_wl}_ewma'
                if ew_col in df.columns and df[ew_col].notna().any():
                    fig.add_trace(go.Scattergl(
                        x=df['Time'], y=df[ew_col],
                        name=f"{meta['stnac']}({stid_wl})-EWMA(α=0.05)",
                        mode='lines',
                        line=dict(color='rgba(255,200,100,0.7)', width=1.2),
                        connectgaps=True,
                        visible=is_hidden
                    ), row=r_top, col=1, secondary_y=False)

                if raw_col_name in df.columns:
                    raw_mask = df[raw_col_name].notna()
                    if raw_mask.any():
                        customdata = (df.loc[raw_mask, qc_raw_name].fillna('?').values
                                      if qc_raw_name in df.columns
                                      else ['?'] * raw_mask.sum())
                        fig.add_trace(go.Scattergl(
                            x=df.loc[raw_mask, 'Time'], y=df.loc[raw_mask, raw_col_name],
                            name=f"⚠️ {stid_wl} 原始值(QC≠Q)",
                            mode='markers',
                            marker=dict(color='red', symbol='x', size=5, line=dict(width=0.8)),
                            customdata=customdata,
                            hovertemplate='%{x}<br>原始值: %{y}<br>QC代碼: %{customdata}<extra></extra>',
                            showlegend=True,
                            visible=is_hidden
                        ), row=r_top, col=1)

        primary_stids = [st for st, meta in tide_meta.items() if meta['is_primary']]
        if primary_stids:
            primary_stid = primary_stids[0]
            pred_col_name = f'WL_{primary_stid}_pred_h'
            if pred_col_name in df.columns:
                label = f"{tide_meta[primary_stid]['stnac']}({primary_stid})-預報(h)"
                fig.add_trace(go.Scattergl(
                    x=df['Time'], y=df[pred_col_name],
                    name=label,
                    mode='lines+markers',
                    line=dict(color='#2ca02c', dash='dot', width=1.2),
                    marker=dict(size=2.5, opacity=0.6),
                    connectgaps=True
                ), row=r_top, col=1)

        diff_idx = 0
        stid_list = sorted(list(tide_meta.keys()))
        if len(stid_list) > 1 and primary_stids:
            primary_stid = primary_stids[0]
            for other_stid in stid_list:
                if other_stid != primary_stid:
                    diff_col_name = f"Diff_{primary_stid}_{other_stid}"
                    if diff_col_name in df.columns and df[diff_col_name].notna().any():
                        other_desc = tide_meta[other_stid]['type_desc']
                        label = f"差值: {primary_stid}-{other_stid}({other_desc})"
                        color = _DIFF_COLORS[diff_idx % len(_DIFF_COLORS)]
                        fig.add_trace(go.Scattergl(
                            x=df['Time'], y=df[diff_col_name],
                            name=label,
                            mode='markers',
                            line=dict(color=color, width=0.8),
                            marker=dict(size=2, opacity=0.5),
                            connectgaps=True,
                            yaxis='y2' if diff_idx > 0 else 'y'
                        ), row=r_top, col=1, secondary_y=True)
                        diff_idx += 1

        # =========================================================
        # (2,1) 左下：暴潮偏差（左軸） + 氣壓（右軸）
        # =========================================================
        fig.add_trace(go.Scattergl(
            x=df['Time'], y=df['Resi'], name=f"{lbl}-暴潮偏差",
            mode='lines+markers',
            line=dict(color="#faafe4", width=1.2),
            marker=dict(size=2.5, opacity=0.6),
            connectgaps=True, legendgroup=f"g{idx}",
        ), row=r_bot, col=1, secondary_y=False)

        if 'Resi_Norm' in df.columns and mr_full and mr_full != 0:
            fig.add_trace(go.Scattergl(
                x=df['Time'], y=df['Resi'] / (mr_full * 1.0) * 100,
                name=f"{lbl}-暴潮偏差(正規化%-全年MR)",
                mode='lines+markers',
                line=dict(color='#f8bbd0', width=1, dash='dash'),
                marker=dict(size=2.5, opacity=0.6),
                connectgaps=True,
                visible='legendonly',
            ), row=r_bot, col=1, secondary_y=False)

        if 'Resi_Norm' in df.columns and mr_month and mr_month != 0:
            fig.add_trace(go.Scattergl(
                x=df['Time'], y=df['Resi'] / (mr_month * 1.0) * 100,
                name=f"{lbl}-暴潮偏差(正規化%-當月MR)",
                mode='lines+markers',
                line=dict(color='#f8bbd0', width=1, dash='dot'),
                marker=dict(size=2.5, opacity=0.6),
                connectgaps=True,
                visible='legendonly',
            ), row=r_bot, col=1, secondary_y=False)

        if 'P' in df.columns:
            p_label = get_src_label(sids['p'], '氣壓')
            fig.add_trace(go.Scattergl(
                x=df['Time'], y=df['P'], name=p_label,
                mode='lines+markers',
                line=dict(color='#8b4513', width=1, dash='dot'),
                marker=dict(size=2.5, opacity=0.6),
                connectgaps=True,
            ), row=r_bot, col=1, secondary_y=True)

        # =========================================================
        # (1,2) 右上：風速/流速（左軸） + 氣溫/海溫（右軸）
        # =========================================================
        if 'WS' in df.columns:
            grp_name = f"wind_{b['stid']}"
            w_label = get_src_label(sids['w'], '風速')
            fig.add_trace(go.Scattergl(
                x=df['Time'], y=df['WS'], name=w_label, legendgroup=grp_name,
                mode='lines+markers',
                connectgaps=False, line=dict(color='#9467bd', width=1.2),
                marker=dict(size=3, opacity=0.6)
            ), row=r_top, col=2, secondary_y=False)

            if 'WS_raw' in df.columns:
                ws_raw_mask = df['WS_raw'].notna()
                if ws_raw_mask.any():
                    ws_customdata = (df.loc[ws_raw_mask, 'WS_QC_raw'].fillna('?').values
                                      if 'WS_QC_raw' in df.columns
                                      else ['?'] * ws_raw_mask.sum())
                    fig.add_trace(go.Scattergl(
                        x=df.loc[ws_raw_mask, 'Time'], y=df.loc[ws_raw_mask, 'WS_raw'],
                        name="⚠️ 風速 原始值(QC≠Q)", legendgroup=grp_name,
                        mode='markers',
                        marker=dict(color='red', symbol='x', size=3, line=dict(width=0.8)),
                        customdata=ws_customdata,
                        hovertemplate='%{x}<br>風速原始值: %{y}<br>QC代碼: %{customdata}<extra></extra>',
                        showlegend=True
                    ), row=r_top, col=2, secondary_y=False)

            if 'WD' in df.columns:
                arrow_df = df.iloc[::6].copy().dropna(subset=['WD'])
                arrow_df['WD'] = pd.to_numeric(arrow_df['WD'], errors='coerce').dropna()
                if not arrow_df.empty:
                    w_dir_label = get_src_label(sids['w'], '風向')
                    fig.add_trace(go.Scattergl(
                        x=arrow_df['Time'], y=arrow_df['WS'], mode='markers',
                        name=w_dir_label, legendgroup=grp_name, showlegend=False,
                        marker=dict(symbol='arrow', size=10, color='#800080',
                                    angle=(arrow_df['WD'] + 180) % 360),
                    ), row=r_top, col=2, secondary_y=False)

        if 'V' in df.columns:
            grp_name = f"curr_{b['stid']}"
            v_label = get_src_label(sids['wv'], '流速')
            fig.add_trace(go.Scattergl(
                x=df['Time'], y=df['V'], name=v_label, legendgroup=grp_name,
                mode='lines+markers',
                connectgaps=True, line=dict(color='#c5b0d5', width=1, dash='dash'),
                marker=dict(size=3, opacity=0.6),
                visible='legendonly'
            ), row=r_top, col=2, secondary_y=False)

            if 'DIR' in df.columns:
                c_arrow = df.iloc[::6].copy().dropna(subset=['DIR'])
                c_arrow['DIR'] = pd.to_numeric(c_arrow['DIR'], errors='coerce').dropna()
                if not c_arrow.empty:
                    v_dir_label = get_src_label(sids['wv'], '流向')
                    fig.add_trace(go.Scattergl(
                        x=c_arrow['Time'], y=c_arrow['V'], mode='markers',
                        name=v_dir_label, legendgroup=grp_name, showlegend=False,
                        marker=dict(symbol='arrow', size=3, color="#a03fea", angle=c_arrow['DIR']),
                        visible='legendonly'
                    ), row=r_top, col=2, secondary_y=False)

        if 'AT' in df.columns:
            at_label = get_src_label(sids['p'], '氣溫')
            fig.add_trace(go.Scattergl(
                x=df['Time'], y=df['AT'], name=at_label,
                mode='lines+markers',
                line=dict(color="#ee7373", width=1.2),
                marker=dict(size=3, opacity=0.6),
                connectgaps=False,
            ), row=r_top, col=2, secondary_y=True)

            if 'AT_raw' in df.columns:
                at_raw_mask = df['AT_raw'].notna()
                if at_raw_mask.any():
                    at_customdata = (df.loc[at_raw_mask, 'AT_QC_raw'].fillna('?').values
                                      if 'AT_QC_raw' in df.columns
                                      else ['?'] * at_raw_mask.sum())
                    fig.add_trace(go.Scattergl(
                        x=df.loc[at_raw_mask, 'Time'], y=df.loc[at_raw_mask, 'AT_raw'],
                        name="⚠️ 氣溫 原始值(QC≠Q)",
                        mode='markers',
                        marker=dict(color='red', symbol='x', size=3, line=dict(width=0.8)),
                        customdata=at_customdata,
                        hovertemplate='%{x}<br>氣溫原始值: %{y}<br>QC代碼: %{customdata}<extra></extra>',
                        showlegend=True
                    ), row=r_top, col=2, secondary_y=True)

        if 'WT' in df.columns:
            wt_label = get_src_label(sids['wt'], '海溫')
            fig.add_trace(go.Scattergl(
                x=df['Time'], y=df['WT'], name=wt_label,
                mode='lines+markers',
                line=dict(color='#ff9896', width=1, dash='dash'),
                marker=dict(size=3, opacity=0.6),
                connectgaps=False,
                visible='legendonly',
            ), row=r_top, col=2, secondary_y=True)

            if 'WT_raw' in df.columns:
                wt_raw_mask = df['WT_raw'].notna()
                if wt_raw_mask.any():
                    wt_customdata = (df.loc[wt_raw_mask, 'WT_QC_raw'].fillna('?').values
                                      if 'WT_QC_raw' in df.columns
                                      else ['?'] * wt_raw_mask.sum())
                    fig.add_trace(go.Scattergl(
                        x=df.loc[wt_raw_mask, 'Time'], y=df.loc[wt_raw_mask, 'WT_raw'],
                        name="⚠️ 海溫 原始值(QC≠Q)",
                        mode='markers',
                        marker=dict(color='red', symbol='x', size=3, line=dict(width=0.8)),
                        customdata=wt_customdata,
                        hovertemplate='%{x}<br>海溫原始值: %{y}<br>QC代碼: %{customdata}<extra></extra>',
                        showlegend=True,
                        visible='legendonly'
                    ), row=r_top, col=2, secondary_y=True)

        # =========================================================
        # (2,2) 右下：波高（左軸） + 週期（右軸）
        # =========================================================
        if 'H_m' in df.columns:
            h_label = get_src_label(sids['wv'], '示性波高(m)')
            fig.add_trace(go.Scattergl(
                x=df['Time'], y=df['H_m'], name=h_label,
                mode='lines+markers',
                line=dict(color='#1b5e20', width=1.2),
                marker=dict(size=2.5, opacity=0.6),
                connectgaps=True,
            ), row=r_bot, col=2, secondary_y=False)

        if 'T_sec' in df.columns:
            t_label = get_src_label(sids['wv'], '平均週期(s)')
            fig.add_trace(go.Scattergl(
                x=df['Time'], y=df['T_sec'], name=t_label,
                mode='lines+markers',
                line=dict(color='#81c784', width=1, dash='dot'),
                marker=dict(size=2.5, opacity=0.6),
                connectgaps=True,
            ), row=r_bot, col=2, secondary_y=True)

        # ── 颱風陸警色帶 ──────────────────────────────────────────────
        if land_range:
            for r_curr in [r_top, r_bot]:
                for c_curr in [1, 2]:
                    fig.add_vrect(x0=land_range[0], x1=land_range[1],
                                  fillcolor="red", opacity=0.1, line_width=0,
                                  row=r_curr, col=c_curr)

        # ── 軸標題 ────────────────────────────────────────────────────
        fig.update_yaxes(title_text="水位(mm)", row=r_top, col=1, fixedrange=False)
        fig.update_yaxes(title_text="水位差值(mm)", secondary_y=True, row=r_top, col=1,
                          fixedrange=False, showgrid=False)

        fig.update_yaxes(title_text="暴潮偏差(mm)", secondary_y=False, row=r_bot, col=1, fixedrange=False)
        fig.update_yaxes(title_text="氣壓(hPa)", secondary_y=True, row=r_bot, col=1,
                          fixedrange=False, showgrid=False)

        fig.update_yaxes(title_text="速度(m/s)", secondary_y=False, row=r_top, col=2, fixedrange=False)
        fig.update_yaxes(title_text="溫度(℃)", secondary_y=True, row=r_top, col=2,
                          fixedrange=False, showgrid=False)

        fig.update_yaxes(title_text="示性波高(m)", secondary_y=False, row=r_bot, col=2, fixedrange=False)
        fig.update_yaxes(title_text="平均週期(s)", secondary_y=True, row=r_bot, col=2,
                          fixedrange=False, showgrid=False)

        # ── 滑桿設定（左欄顯示，右欄關閉；matches='x' 讓全部子圖共用同一時間軸）──
        fig.update_xaxes(
            matches='x',
            rangeslider_visible=True,
            rangeslider=dict(visible=True, thickness=slider_ratio,
                              bgcolor="#333333", borderwidth=1),
            row=r_top, col=1,
        )
        fig.update_xaxes(
            matches='x',
            rangeslider_visible=True,
            rangeslider=dict(visible=True, thickness=slider_ratio,
                              bgcolor="#333333", borderwidth=1),
            row=r_bot, col=1,
        )
        fig.update_xaxes(matches='x', rangeslider_visible=False, row=r_top, col=2)
        fig.update_xaxes(matches='x', rangeslider_visible=False, row=r_bot, col=2)

    # ── 全局 Layout ──────────────────────────────────────────────────────
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#1E1E1E",
        plot_bgcolor="#1E1E1E",
        uirevision=True,
        font=dict(family=_FONT, size=12, color="#E0E0E0"),
        height=total_height,
        hovermode='x unified',
        hoverlabel=dict(namelength=-1),
        margin=dict(l=50, r=300, t=50, b=50),  # 右邊留 300px 給浮動圖例
        autosize=True,
    )

    return fig
