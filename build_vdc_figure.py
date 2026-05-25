"""
build_vdc_figure.py
===================
仿 Van de Casteele 散佈圖繪圖模組。
- 縱軸 (Y)：主儀器（音波式）水位值。
- 橫軸 (X)：水位差值（音波式 - 雷達式 或 音波式 - 壓力式）。
- 顏色 (Color)：依觀測時間由舊到新進行漸變。
- 支援「多子圖排版」：若傳入多個 bundles，則垂直排列繪製多張散佈圖。
- 標註輔助線：
  - X=0 零差值線 (極細半透明白色虛線)
  - X=平均值 (極細橘色實線)
  - X=平均值 ± 標準差 (極細半透明橘色虛線)
- 具備文字自動避讓，防止標籤重疊。
"""

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 視覺字型與配色定義
_FONT = "標楷體, Noto Sans TC, Segoe UI, Arial, sans-serif"
_REF_LINE_COLOR = "rgba(255, 255, 255, 0.25)"     # X=0 參考線：白色半透明虛線
_MEAN_LINE_COLOR = "rgba(255, 127, 14, 0.7)"      # 平均差值線：橘色半透明實線
_STD_LINE_COLOR = "rgba(255, 127, 14, 0.25)"      # 標準差界線：橘色極淡虛線

def _clean_ts(ts) -> str:
    """把 Plotly 時間戳 'YYYY-MM-DDTHH:MM:SS.mmm' 轉為 MySQL 格式字串。"""
    if ts is None:
        return ""
    return str(ts).split(".")[0].replace("T", " ")

# def build_vdc_figure(bundles: list, diff_type: str = "radar") -> tuple[go.Figure, dict]:
def build_vdc_figure(bundles: list, diff_type: str = "auto", zoom_range: dict | None = None) -> tuple[go.Figure, dict]:
    """
    建立仿 Van de Casteele 散佈圖的 Plotly Figure 物件（多子圖模式）。

    Parameters
    ----------
    bundles : list[dict]
        從 dash_bridge 或 fetch_bundle 取得的測站 bundle 清單。
    diff_type : str
        副儀器選擇策略。"auto"=自動（優先雷達式，無雷達則用壓力式）；
        "雷達式"=強制雷達式（找不到則顯示提示）；"壓力式"=強制壓力式（找不到則顯示提示）。

    Returns
    -------
    fig : go.Figure
        繪製完成的 Plotly Figure 物件。
    stats_summary : dict
        各站點統計結果的字典，格式如 {stid: stats_dict}。
    """
    n = len(bundles)
    if n == 0:
        return _make_empty_figure("無測站資料（bundles 為空）"), {}

    # ---- 預先計算子圖標題的縮放標籤 ----
    zoom_label = ""
    if zoom_range:
        try:
            t0 = _clean_ts(zoom_range["x_start"])
            t1 = _clean_ts(zoom_range["x_end"])
            # 取得前 16 字元 (YYYY-MM-DD HH:MM)
            zoom_label = f"  ▶ 篩選：{t0[:16]} ～ {t1[:16]}"
        except Exception:
            pass
    
    # # Claude 針對痛點二提供可以直接套用的 patch 
    # # ── Pre-pass：計算各站 mean ± 3σ 後取最大值，統一 X 軸尺度 ──────────────
    # _x_bounds = []
    # for _b in bundles:
    #     _df = _b.get("df", pd.DataFrame())
    #     _meta = _b.get("tide_meta", {})
    #     _pri = next((s for s, m in _meta.items() if m.get("is_primary")), None)
    #     if _pri is None:
    #         _pri = next((s for s, m in _meta.items() if m.get("type") == 2), None)
    #     if _pri is None:
    #         continue
    #     if diff_type == "auto":
    #         _others = [s for s, m in _meta.items() if m.get("type") == 4 and s != _pri]
    #         if not _others:
    #             _others = [s for s, m in _meta.items() if m.get("type") == 3 and s != _pri]
    #     else:
    #         _t = 4 if diff_type == "雷達式" else 3
    #         _others = [s for s, m in _meta.items() if m.get("type") == _t and s != _pri]
    #     if not _others:
    #         continue
    #     _col = f"Diff_{_pri}_{_others[0]}"
    #     if _col not in _df.columns:
    #         continue
    #     _s = _df[_col].dropna()
    #     if len(_s) < 2:
    #         continue
    #     _x_bounds.append(abs(_s.mean()) + 3 * _s.std())

    # unified_x_half = max(max(_x_bounds), 1.0) if _x_bounds else None
    # # ── End Pre-pass ──────────────────────────────────────────────────────────

    # 1. 建立垂直排列子圖
    # 每站一列，不共享 X/Y 軸
    fig = make_subplots(
        rows=n,
        cols=1,
        vertical_spacing=0.12 if n > 1 else 0.05,
        subplot_titles=[
            f"{b['stname']}({b['stid']}) ─ Van de Casteele 散佈圖{zoom_label}"
            for b in bundles
        ]
    )

    stats_summary = {}

    # 2. 逐一處理每個測站 bundle
    for idx, b in enumerate(bundles):
        row_idx = idx + 1
        df = b.get("df", pd.DataFrame())
        tide_meta = b.get("tide_meta", {})
        stname = b.get("stname", "未知")
        stid = b.get("stid", "")
        
        # 2a. 尋找主儀器 (音波式 type=2)
        primary_stids = [s for s, meta in tide_meta.items() if meta.get("is_primary")]
        if not primary_stids:
            primary_stids = [s for s, meta in tide_meta.items() if meta.get("type") == 2]
        
        # 降級處理：若無 type=2，取第一個 WL_* 欄位
        if not primary_stids:
            wl_cols = [c for c in df.columns if c.startswith("WL_") and not c.endswith("_raw") and not c.endswith("_lf") and not c.endswith("_ewma")]
            primary_stid = wl_cols[0].replace("WL_", "") if wl_cols else None
        else:
            primary_stid = primary_stids[0]

        # # 2b. 尋找對應類型的副儀器 (雷達式 type=4 或 壓力式 type=3)
        # target_type = 4 if diff_type == "radar" else 3
        # other_stids = [s for s, meta in tide_meta.items() if meta.get("type") == target_type and s != primary_stid]
        
        # # 降級處理：若找不到指定類型，隨便找一個非主儀器
        # if not other_stids:
        #     other_stids = [s for s in tide_meta.keys() if s != primary_stid]

        # 2b. 尋找副儀器（根據 diff_type 決定策略）
        if diff_type == "auto":
            # 自動模式：優先雷達式 (type=4)，無雷達式則用壓力式 (type=3)
            other_stids = [s for s, meta in tide_meta.items() if meta.get("type") == 4 and s != primary_stid]
            if not other_stids:
                other_stids = [s for s, meta in tide_meta.items() if meta.get("type") == 3 and s != primary_stid]
        else:
            target_type = 4 if diff_type == "雷達式" else 3
            other_stids = [s for s, meta in tide_meta.items() if meta.get("type") == target_type and s != primary_stid]

        if not primary_stid or not other_stids:
            # 此站無此類型的副儀器，該子圖顯示錯誤訊息
            fig.add_annotation(
                # text=f"測站 {stname}({stid}) 無相配的雙儀器對比資料",
                text=f"測站 {stname}({stid}) 無{'雷達式' if diff_type == '雷達式' else '壓力式' if diff_type == '壓力式' else '可用的'}副儀器",
                x=0.5, y=0.5, xref=f"x{row_idx}" if row_idx > 1 else "x", yref=f"y{row_idx}" if row_idx > 1 else "y",
                showarrow=False, font=dict(color="#d9534f", size=13)
            )
            stats_summary[stid] = {"status": "no_data"}
            continue

        other_stid = other_stids[0]
        actual_type_desc = tide_meta.get(other_stid, {}).get("type_desc", "副儀器")
        primary_wl_col = f"WL_{primary_stid}"
        target_diff_col = f"Diff_{primary_stid}_{other_stid}"

        # 2c. 檢查 DataFrame 欄位是否存在
        if primary_wl_col not in df.columns or target_diff_col not in df.columns:
            fig.add_annotation(
                text=f"測站 {stname}({stid}) 水位或差值欄位不存在",
                x=0.5, y=0.5, xref=f"x{row_idx}" if row_idx > 1 else "x", yref=f"y{row_idx}" if row_idx > 1 else "y",
                showarrow=False, font=dict(color="#d9534f", size=13)
            )
            stats_summary[stid] = {"status": "no_fields"}
            continue

        # 2d. 提取有效點並計算統計量
        sub_df = df[["Time", primary_wl_col, target_diff_col]].dropna()
        # 依水位圖 zoom 範圍過濾時間（若有）
        if zoom_range and not sub_df.empty:
            try:
                t0 = pd.to_datetime(zoom_range["x_start"])
                t1 = pd.to_datetime(zoom_range["x_end"])
                sub_df = sub_df[
                    (sub_df["Time"] >= t0) & (sub_df["Time"] <= t1)
                ].copy()
            except Exception:
                pass  # 解析失敗時靜默忽略，使用全時間範圍
        if sub_df.empty:
            fig.add_annotation(
                text="此區間內無對齊的水位觀測資料",
                x=0.5, y=0.5, xref=f"x{row_idx}" if row_idx > 1 else "x", yref=f"y{row_idx}" if row_idx > 1 else "y",
                showarrow=False, font=dict(color="#888", size=13)
            )
            stats_summary[stid] = {"status": "empty_data"}
            continue

        diff_series = sub_df[target_diff_col]
        mean_val = float(diff_series.mean())
        std_val = float(diff_series.std())
        
        # ── 修正：回歸計算必須在建立 stats 字典之前執行 ────────────────────
        # ── 回歸預算（在 stats_html 之前執行）──────────────────────────────
        wl_vals  = sub_df[primary_wl_col].values
        dv_vals  = sub_df[target_diff_col].values
        slope = intercept = r_value = None
        try:
            from scipy.stats import linregress as _lr
            _res   = _lr(wl_vals, dv_vals)
            slope, intercept, r_value = _res.slope, _res.intercept, _res.rvalue
        except Exception:
            pass
        # ── End 回歸預算 ────────────────────────────────────────────────────

        stats = {
            "status": "success",
            "count": len(diff_series),
            "mean": mean_val,
            "std": std_val,
            "min": float(diff_series.min()),
            "max": float(diff_series.max()),
            "time_start": sub_df["Time"].min().strftime("%Y-%m-%d %H:%M"),  # 新增
            "time_end":   sub_df["Time"].max().strftime("%Y-%m-%d %H:%M"),  # 新增
            "slope": slope,        # ← 新增
            "r2": r_value**2 if r_value is not None else None,  # ← 新增
        }
        stats_summary[stid] = stats

        # 2e. 準備時間漸變色數值 (Unix Epoch 毫秒)
        sub_df["Time_Numeric"] = pd.to_datetime(sub_df["Time"]).astype(np.int64) // 10**6
        min_time = sub_df["Time"].min()
        max_time = sub_df["Time"].max()

        # 2f. 繪製 Scatter 點
        # 只有第一列子圖顯示 colorbar，避免多個 colorbar 重疊
        show_scale = (idx == 0)
        
        fig.add_trace(
            go.Scattergl(
                x=sub_df[target_diff_col],
                y=sub_df[primary_wl_col],
                # mode="markers",
                mode="lines+markers",
                # mode="lines+markers+text", # 文字超擠好可怕
                name=f"{stname}({stid}) 觀測點",
                line=dict(width=0.2, color="rgba(255,255,255,0.12)"), # 👈 添加設定線條粗細、顏色
                marker=dict(
                    size=4.5,
                    opacity=0.6,
                    color=sub_df["Time_Numeric"],
                    colorscale="Viridis",
                    showscale=show_scale,
                    colorbar=dict(
                        title=dict(text="時間軸", side="top"),
                        tickvals=[
                            sub_df["Time_Numeric"].min(),
                            sub_df["Time_Numeric"].mean(),
                            sub_df["Time_Numeric"].max()
                        ],
                        ticktext=[
                            min_time.strftime("%m/%d %H:%M"),
                            (min_time + (max_time - min_time)/2).strftime("%m/%d %H:%M"),
                            max_time.strftime("%m/%d %H:%M")
                        ],
                        ticks="outside",
                        thickness=14,
                        len=0.9 / n if n > 1 else 0.9,
                        y=1.0 - (idx * (1.0 / n)) - (0.5 / n) if n > 1 else 0.5
                    ) if show_scale else None
                ),
                text=sub_df["Time"].dt.strftime("%Y-%m-%d %H:%M"),
                hovertemplate="時間: %{text}<br>差值: %{x:.1f} mm<br>主水位: %{y:.1f} mm<extra></extra>"
            ),
            row=row_idx, col=1
        )

        # 2g. 動態防疊排版避讓邏輯
        # 決定零線與平均線 labels 的左右擺放位置
        if mean_val < 0:
            zero_pos = "bottom right"
            mean_pos = "top left"
            plus_std_pos = "top right"
            minus_std_pos = "top left"
        else:
            zero_pos = "bottom left"
            mean_pos = "top right"
            plus_std_pos = "top right"
            minus_std_pos = "top left"

        # 2h. 加入極細垂直輔助線與標籤

        # A. X=0 零差值線 (白色虛線)
        fig.add_vline(
            x=0, line_width=0.8, line_dash="dash", line_color=_REF_LINE_COLOR,
            annotation_text="零差值線", annotation_position=zero_pos,
            annotation_font=dict(size=10, color="rgba(255, 255, 255, 0.4)"),
            row=row_idx, col=1
        )

        # B. X=Mean 平均值線 (橘色實線)
        fig.add_vline(
            x=mean_val, line_width=0.8, line_color=_MEAN_LINE_COLOR,
            annotation_text=f"平均: {mean_val:.1f}", annotation_position=mean_pos,
            annotation_font=dict(size=10, color=_MEAN_LINE_COLOR),
            row=row_idx, col=1
        )

        # C. X=Mean + Std (橘色極淡虛線)
        fig.add_vline(
            x=mean_val + std_val, line_width=0.7, line_dash="dot", line_color=_STD_LINE_COLOR,
            annotation_text=f"+1σ: {(mean_val + std_val):.1f}", annotation_position=plus_std_pos,
            annotation_yshift=-16, # 避免與平均線標籤重疊
            annotation_font=dict(size=9, color="rgba(255, 127, 14, 0.4)"),
            row=row_idx, col=1
        )

        # D. X=Mean - Std (橘色極淡虛線)
        fig.add_vline(
            x=mean_val - std_val, line_width=0.7, line_dash="dot", line_color=_STD_LINE_COLOR,
            annotation_text=f"-1σ: {(mean_val - std_val):.1f}", annotation_position=minus_std_pos,
            annotation_yshift=-16, # 避免與平均線標籤重疊
            annotation_font=dict(size=9, color="rgba(255, 127, 14, 0.4)"),
            row=row_idx, col=1
        )

        # 2i. 設定各子圖 Y 軸與 X 軸標題
        # 各站獨立 x 軸範圍（mean ± 3σ）
        x_half = max(abs(mean_val) + 3 * std_val, 1.0)
        fig.update_xaxes(range=[-x_half, x_half], row=row_idx, col=1)

        # 回歸線：diff ~ WL（診斷差值是否隨水位系統性偏移）
        try:
            if slope is not None:
                wl_line   = np.linspace(wl_vals.min(), wl_vals.max(), 200)
                diff_line = slope * wl_line + intercept
                fig.add_trace(
                    go.Scatter(
                        x=diff_line, y=wl_line,
                        mode="lines",
                        line=dict(color="rgba(100,200,255,0.65)",
                                  width=1.2, dash="dash"),
                        hovertemplate=(
                            f"回歸線　斜率: {slope:.4f}<br>"
                            "差值: %{x:.1f} mm<br>"
                            "水位: %{y:.1f} mm<extra></extra>"
                        ),
                        showlegend=False,
                    ),
                    row=row_idx, col=1
                )
        except Exception:
            pass  # 資料不足或回歸失敗時靜默略過
        # try:
        #     wl_vals = sub_df[primary_wl_col].values
        #     dv_vals = sub_df[target_diff_col].values
        #     from scipy.stats import linregress
        #     result = linregress(wl_vals, dv_vals)
        #     slope, intercept, r_value = result.slope, result.intercept, result.rvalue
        #     # 原本 np.polyfit 那行可刪除
        #     # slope, intercept = np.polyfit(wl_vals, dv_vals, 1)
        #     wl_line = np.linspace(wl_vals.min(), wl_vals.max(), 200)
        #     diff_line = slope * wl_line + intercept
        #     fig.add_trace(
        #         go.Scatter(
        #             x=diff_line, y=wl_line,
        #             mode="lines",
        #             name=f"{stname} 回歸線",
        #             line=dict(color="rgba(100,200,255,0.65)", width=1.2, dash="dash"),
        #             hovertemplate=(
        #                 f"回歸線　斜率: {slope:.4f}<br>"
        #                 "差值: %{x:.1f} mm<br>水位: %{y:.1f} mm<extra></extra>"
        #             ),
        #             showlegend=False,
        #         ),
        #         row=row_idx, col=1
        #     )
        # except Exception:
        #     pass  # 資料不足時靜默略過
        # fig.update_xaxes(title_text=f"儀器差值 (音波式 - {diff_type}) (mm)", row=row_idx, col=1)
        fig.update_xaxes(title_text=f"儀器差值（音波式 - {actual_type_desc}）(mm)", row=row_idx, col=1)
        # if unified_x_half is not None:
        #     fig.update_xaxes(range=[-unified_x_half, unified_x_half], row=row_idx, col=1)
        fig.update_yaxes(title_text="主儀器音波水位 (mm)", row=row_idx, col=1)

        # 2j. 水位/差值子圖標註 (右上角統計 box)
        # stats_html 加 None 保護
        slope_str = f"{slope:.4f}" if slope is not None else "N/A"
        r2_str    = f"{r_value**2:.3f}" if r_value is not None else "N/A"
        stats_html = (
            f"<b>統計指標 (N={stats['count']})</b><br>"
            f"平均差 (Mean): {stats['mean']:.1f} mm<br>"
            f"標準差 (Std):  {stats['std']:.1f} mm<br>"
            f"最大值 (Max):  {stats['max']:.1f} mm<br>"
            f"最小值 (Min):  {stats['min']:.1f} mm<br>"
            f"回歸斜率 (a):  {slope_str}<br>"
            f"R²:           {r2_str}"
        )
        # stats_html = (
        #     f"<b>統計指標 (N={stats['count']})</b><br>"
        #     f"平均差 (Mean): {stats['mean']:.1f} mm<br>"
        #     f"標準差 (Std):  {stats['std']:.1f} mm<br>"
        #     f"最大值 (Max):  {stats['max']:.1f} mm<br>"
        #     f"最小值 (Min):  {stats['min']:.1f} mm<br>"
        #     f"回歸斜率 (a):  {slope:.4f}\n"
        #     f"R²:           {r_value**2:.3f}"
        # )
        
        # 這個文字視窗會擋住散佈圖，平常使用時註解掉
        # 如果要繪圖製作簡報的話再開啟，記得調整顯示位置不要擋住散佈點
        # fig.add_annotation(
        #     xref="paper", yref="paper",
        #     x=0.01, y=0.98,
        #     text=stats_html,
        #     showarrow=False,
        #     align="left",
        #     bgcolor="rgba(30, 42, 58, 0.75)",
        #     bordercolor="rgba(200, 214, 229, 0.15)",
        #     borderwidth=1,
        #     borderpad=6,
        #     font=dict(size=10, color="#CCCCCC"),
        #     row=row_idx, col=1
        # )


    # 3. Layout 全域風格設定
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#1E1E1E",
        plot_bgcolor="#1E1E1E",
        hovermode="closest",
        font=dict(family=_FONT, size=11, color="#E0E0E0"),
        height=450 * n if n > 1 else 500,
        margin=dict(l=60, r=100, t=60, b=60),
        showlegend=False
    )
    
    return fig, stats_summary


def _make_empty_figure(message: str) -> go.Figure:
    """產生包含錯誤或提示訊息的空白圖表。"""
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#1E1E1E",
        plot_bgcolor="#1E1E1E",
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        annotations=[dict(
            text=message,
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow=False,
            font=dict(color="#d9534f", size=14, family=_FONT)
        )]
    )
    return fig


# ==============================================================================
# 獨立測試區塊 (只在直接執行 python build_vdc_figure.py 時運作)
# ==============================================================================
if __name__ == "__main__":
    print("[VdC] 產生仿真資料進行獨立測試...")
    
    # 建立一個測試用時間序列 (7天，6分鐘一筆)
    test_time = pd.date_range("2026-05-01", periods=1680, freq="6min")
    t_sec = test_time.astype(np.int64) / 1e9
    primary_wl = 2500 + 1200 * np.sin(2 * np.pi * t_sec / (12.42 * 3600)) + np.random.randn(len(test_time)) * 10
    
    # 測站 1
    diff_val = 15.0 + 0.005 * (primary_wl - 2500) + np.random.randn(len(test_time)) * 8
    df1 = pd.DataFrame({
        "Time": test_time,
        "WL_1176": primary_wl,
        "WL_1176_backup": primary_wl - diff_val,
        "Diff_1176_1176_backup": diff_val
    })
    tide_meta1 = {
        "1176": {"type": 2, "type_desc": "音波式", "stnac": "測試站A", "is_primary": 1},
        "1176_backup": {"type": 4, "type_desc": "雷達式", "stnac": "測試站A-雷達式", "is_primary": 0}
    }
    bundle1 = {"stid": "1176", "stname": "測試站A", "df": df1, "tide_meta": tide_meta1}
    
    # 測站 2
    diff_val2 = -26.7 - 0.003 * (primary_wl - 2500) + np.random.randn(len(test_time)) * 20.9
    df2 = pd.DataFrame({
        "Time": test_time,
        "WL_2288": primary_wl + 300,
        "WL_2288_backup": (primary_wl + 300) - diff_val2,
        "Diff_2288_2288_backup": diff_val2
    })
    tide_meta2 = {
        "2288": {"type": 2, "type_desc": "音波式", "stnac": "測試站B", "is_primary": 1},
        "2288_backup": {"type": 4, "type_desc": "雷達式", "stnac": "測試站B-雷達式", "is_primary": 0}
    }
    bundle2 = {"stid": "2288", "stname": "測試站B", "df": df2, "tide_meta": tide_meta2}
    
    bundles = [bundle1, bundle2]
    
    # 畫圖
    # fig, stats = build_vdc_figure(bundles, "radar")
    fig, stats = build_vdc_figure(bundles)
    print("各站統計結果：", stats)
    fig.show()
