"""
build_surge_report_figure.py
=============================
單站颱風暴潮偏差報表圖 -- 純函式繪圖模組。

用途：
    颱風事件期間，比對單一測站的觀測水位、調和預報、暴潮偏差（Resi），
    與該站的大潮注意值/警戒值門檻，供人工判讀「暴潮是否達警戒」使用。
    白底、單站、靜態 PNG（kaleido 匯出），不含互動元件。

資料來源（全部沿用既有方法，不重複查詢資料庫）：
    - 觀測(藍)/預報(綠)/Resi(紅)：fetch_bundle() 回傳 df 裡的
      WL_{primary}、WL_{primary}_pred_h、Resi 三欄（僅主測站 QC=Q 校正值）
    - 站碼/站名：bundle['tide_meta'][primary_stid]（stnac + stid_obs/stid_new）
    - 門檻值：get_tsuwawa_thresholds()，查 tsuwawa.warn
      （與 mrbank 同主機 .71，安外鏡像在 .160/.104，帳號 dps 純 SELECT）

============================================================
PATCH（套用到 ocean_plot_dash.py，OceanDataEngine.query_multi_tide_data）
============================================================
原本（約第 237-242 行）：

    tide_meta[stid] = {
        'type': type_val,
        'type_desc': type_desc,
        'stnac': stnac,
        'is_primary': is_primary
    }

改為（多帶 stid_obs / stid_new，兩者在同一個 for row 迴圈內已經存在）：

    tide_meta[stid] = {
        'type': type_val,
        'type_desc': type_desc,
        'stnac': stnac,
        'is_primary': is_primary,
        'stid_obs': row['stid_obs'],
        'stid_new': row['stid_new'],
    }

這是唯一需要動到 ocean_plot_dash.py 的地方，其餘全部是本檔案的新程式碼。
============================================================
"""

import pandas as pd
import plotly.graph_objects as go


_FONT = "PingFang TC, Noto Sans CJK TC, Arial, sans-serif"
# _FONT = "標楷體, PingFang TC, Noto Sans CJK TC, Arial, sans-serif" #主管表示他不喜歡標楷體，說大家都覺得很醜

# 白底報表色系（獨立維護，不影響 build_water_figure.py 的深色系）
_COLOR_OBS = '#1f77b4'        # 觀測水位（校正值 QC=Q）
_COLOR_PRED = '#2ca02c'       # 調和預報
_COLOR_RESI = '#c0392b'       # 暴潮偏差 Resi（避開淡粉色系，白底需高對比）
_COLOR_STIDE = '#d4a017'      # 潮位注意值（琥珀黃）
_COLOR_WARNVAL = '#d2691e'    # 潮位警戒值（深橘）

# 陸警色帶：中性灰，不用紅 -- 避免跟 Resi 紅線搶語意、且半透明紅疊在紅線上
# 會讓重疊區段視覺飽和度失真
# _COLOR_LAND_BAND = 'rgba(90,90,90,0.10)'
# _COLOR_LAND_LINE = 'rgba(80,80,80,0.6)'

# 陸警色帶：改用Gemini建議的調色
_COLOR_LAND_BAND = 'rgba(230, 90, 90, 0.10)'
_COLOR_LAND_LINE = 'rgba(220, 80, 80, 0.6)'

def get_tsuwawa_thresholds(conn, stid: str) -> dict | None:
    """
    查詢大潮注意值(STIDE)/暴潮警戒值(WARNVAL)，單位換算為 mm。

    沿用既有的 mrbank connection，以 `tsuwawa.warn` 全名跨 schema 查詢
    （tsuwawa 與 mrbank 同主機 .71，安外鏡像在 .160/.104）。

    注意：dps 帳號需對 tsuwawa schema 有 SELECT 權限，若權限不足會拋
    mysql.connector 的 Access denied 例外，請在第一次測試時順便確認。

    Parameters
    ----------
    conn : mysql.connector connection
        OceanDataEngine.conn，或任何已連上同一主機的連線物件。
    stid : str
        測站代碼。

    Returns
    -------
    dict | None
        {"警戒值_mm": float, "注意值_mm": float}
        查無資料（該站不在 tsuwawa.warn 裡）時回傳 None。
    """
    query = "SELECT WARNVAL, STIDE FROM tsuwawa.warn WHERE STID = %s"
    df = pd.read_sql(query, conn, params=(stid,))
    if df.empty:
        return None
    df.columns = [c.upper() for c in df.columns]
    return {
        "警戒值_mm": float(df.iloc[0]["WARNVAL"]) * 1000,
        "注意值_mm": float(df.iloc[0]["STIDE"]) * 1000,
    }


def build_surge_report_figure(
    bundle: dict,
    typhoon_info: dict,
    thresholds: dict | None,
) -> go.Figure:
    """
    單站颱風暴潮偏差報表圖（白底，供 kaleido 匯出 PNG）。

    Parameters
    ----------
    bundle : dict
        fetch_bundle(stid, start, end) 回傳值（單站）。
        本函式只讀取 bundle['df']、bundle['tide_meta']、bundle['stname']，
        不重新查詢資料庫、不修改 bundle。
    typhoon_info : dict
        {id, cname, warnSeaBeg, warnSeaEnd, warnLandBeg, warnLandEnd}
        對應 OceanDataEngine.fetch_typhoons() 回傳 DataFrame 的一列
        （用 .iloc[0].to_dict() 轉換）。
    thresholds : dict | None
        get_tsuwawa_thresholds() 的回傳值。為 None 時代表查無門檻資料，
        圖上不畫門檻線，改在圖角落標紅字提示。

    Returns
    -------
    go.Figure
    """
    df = bundle['df']
    tide_meta = bundle.get('tide_meta') or {}

    # 找主測站（is_primary=1）。若 tide_meta 為空（舊系統降級模式），
    # 則直接使用 Obs/Pre 欄位，此時 stid_obs/stid_new 無法取得。
    primary_stid = next(
        (st for st, meta in tide_meta.items() if meta.get('is_primary')),
        None
    )

    if primary_stid is not None:
        col_obs = f'WL_{primary_stid}'
        col_pred = f'WL_{primary_stid}_pred_h'
        meta = tide_meta[primary_stid]
        stid_obs = meta.get('stid_obs', '未知')
        stid_new = meta.get('stid_new', '未知')
        stid_display = bundle['stid']
    else:
        col_obs, col_pred = 'Obs', 'Pre'
        stid_obs = stid_new = '未知（舊系統降級模式）'

    fig = go.Figure()

    # --- §1 觀測水位（左軸） ---
    if col_obs in df.columns:
        fig.add_trace(go.Scatter(
            x=df['Time'], y=df[col_obs],
            mode='lines', name='觀測水位',
            line=dict(color=_COLOR_OBS, width=1.5),
            connectgaps=False,
        ))

    # --- §2 調和預報（左軸，同軸方便比對震盪幅度） ---
    if col_pred in df.columns:
        fig.add_trace(go.Scatter(
            x=df['Time'], y=df[col_pred],
            mode='lines', name='調和預報',
            line=dict(color=_COLOR_PRED, width=1.2),
        ))

    # --- §3 暴潮偏差 Resi（右軸） ---
    if 'Resi' in df.columns:
        fig.add_trace(go.Scatter(
            x=df['Time'], y=df['Resi'],
            mode='lines', name='暴潮偏差',
            line=dict(color=_COLOR_RESI, width=1.5),
            yaxis='y2',
        ))

    # Gemini pro 嘗試優化
    # --- §4 潮位注意值／警戒值（改用 Scatter 繪製，即可自動加入圖例並保持互動性） ---
    y2_range = None
    if thresholds is not None and not df.empty:
        # 取得 X 軸（時間軸）的起點與終點，讓水平線能完美橫跨整張圖
        x_start = df['Time'].min()
        x_end = df['Time'].max()
        
        # 1. 潮位注意值
        fig.add_trace(go.Scatter(
            x=[x_start, x_end],
            y=[thresholds['注意值_mm'], thresholds['注意值_mm']],
            mode='lines',
            name=f"潮位注意值", # 主管說數值沒人在意不用寫幾mm出來
            # name=f"潮位注意值 ({thresholds['注意值_mm']:.0f}mm)",
            line=dict(color=_COLOR_STIDE, dash='dash', width=1.2),
            hoverinfo='skip',  # 滑鼠移過去時「不要」顯示這條線的 tooltip，避免干擾數據判讀
            showlegend=True
        ))
        
        # 2. 潮位警戒值
        fig.add_trace(go.Scatter(
            x=[x_start, x_end],
            y=[thresholds['警戒值_mm'], thresholds['警戒值_mm']],
            mode='lines',
            name=f"潮位警戒值", # 主管說數值沒人在意不用寫幾mm出來
            # name=f"潮位警戒值 ({thresholds['警戒值_mm']:.0f}mm)",
            line=dict(color=_COLOR_WARNVAL, dash='dash', width=1.2),
            hoverinfo='skip',  # 同樣忽略懸停提示
            showlegend=True
        ))
    # # --- §4 潮位注意值／警戒值（左軸水平線，範圍固定涵蓋兩線） ---
    # # 印象中這應該是我 prompt 說明給錯，而非 Claude 誤判，下次自己要注意提的需求正確性。
    # y2_range = None
    # if thresholds is not None:
    #     fig.add_hline(
    #         y=thresholds['注意值_mm'],
    #         line=dict(color=_COLOR_STIDE, dash='dash', width=1.2),
    #         annotation_text=f"潮位注意值 {thresholds['注意值_mm']:.0f}mm",
    #         annotation_position="top left",
    #         yref='y', 
    #     )
    #     fig.add_hline(
    #         y=thresholds['警戒值_mm'],
    #         line=dict(color=_COLOR_WARNVAL, dash='dash', width=1.2),
    #         annotation_text=f"潮位警戒值 {thresholds['警戒值_mm']:.0f}mm",
    #         annotation_position="top left",
    #         annotation_font=dict(size=10),   # 設定字體大小（10偏小，比陸警還小；陸警只有12，可是12已經比預設小，那預設應該是14）
    #         # annotation_font=dict(size=10,color="black",family="Arial"),   # 設定字體大小、顏色、字型
    #         yref='y',
    #     )
    #     # 改回開放右側y軸自動抓範圍，這裡不要硬性寫死
    #     # y2_range = [
    #     #     min(0, thresholds['注意值_mm']) - 200,
    #     #     thresholds['警戒值_mm'] + 200,
    #     # ]
    # else:
    #     fig.add_annotation(
    #         text="警戒值查無資料",
    #         xref="paper", yref="paper", x=0.99, y=0.02,
    #         showarrow=False, font=dict(color='red', size=12),
    #     )

    # --- §5 颱風陸警色帶（中性灰，不用紅） ---

    # # # --- 颱風海警色帶設定區開始線 ---
    # # # --- 颱風海警色帶（比陸警更淺一階的灰，先畫，讓陸警疊在上面） ---　（不知為何好像沒work）
    # # sea_beg = typhoon_info.get('warnSeaBeg')
    # # sea_end = typhoon_info.get('warnSeaEnd')
    # # if sea_beg is not None and sea_end is not None and pd.notna(sea_beg) and pd.notna(sea_end):
    # #     fig.add_vrect(
    # #         x0=sea_beg, x1=sea_end,
    # #         fillcolor='rgba(90,90,90,0.05)', line_width=0, layer='below',
    # #     )
    # # # --- 颱風海警色帶設定區截止線 ---
    # land_beg = typhoon_info.get('warnLandBeg')
    # land_end = typhoon_info.get('warnLandEnd')
    # # mid = land_beg + (land_end - land_beg) # ChatGPT5.5 提供的簡單算法，未驗證留作備用
    # mid = pd.Timestamp(land_beg) + (pd.Timestamp(land_end) - pd.Timestamp(land_beg)) / 2  # ChatGPT5.5 提供的保險算法，已確認可work
    # if land_beg is not None and land_end is not None and pd.notna(land_beg) and pd.notna(land_end):
    #     fig.add_vrect(
    #         x0=land_beg, x1=land_end,
    #         fillcolor=_COLOR_LAND_BAND, line_width=0, layer='below',
    #     )
    #     for x in (land_beg, land_end):
    #         fig.add_vline(x=x, line=dict(color=_COLOR_LAND_LINE, dash='dash', width=1))
    #     fig.add_annotation(
    #         x=mid, y=1.04, xref='x', yref='paper',
    #         text=f"陸警 {pd.Timestamp(land_beg):%m/%d %H:%M} - {pd.Timestamp(land_end):%m/%d %H:%M}",
    #         showarrow=False, font=dict(color='#444444', size=12), xanchor='center', 
    #     )

    # ==================== 颱風警報色帶（海陸警雙層疊加） ====================
    
    # --- §1 颱風海警色帶（象徵海洋，採用淡藍灰色） ---
    sea_beg = typhoon_info.get('warnSeaBeg')
    sea_end = typhoon_info.get('warnSeaEnd')
    
    if sea_beg is not None and sea_end is not None and pd.notna(sea_beg) and pd.notna(sea_end):
        # 強制轉為 Timestamp，確保能完美對齊 X 軸
        sea_beg_ts = pd.Timestamp(sea_beg)
        sea_end_ts = pd.Timestamp(sea_end)
        
        # 繪製海警陰影帶（溫和的藍灰，不搶主線風采）
        fig.add_vrect(
            x0=sea_beg_ts, x1=sea_end_ts,
            fillcolor='rgba(70, 130, 180, 0.08)',  # 調整到 0.08，既看得見又很優雅
            line_width=0, layer='below',
        )
        # 繪製海警虛線邊界
        for x in (sea_beg_ts, sea_end_ts):
            fig.add_vline(x=x, line=dict(color='rgba(70, 130, 180, 0.35)', dash='dash', width=1))
            
        # 海警文字標籤（放在 y=1.09，位置最高，與陸警錯開）
        sea_mid = sea_beg_ts + (sea_end_ts - sea_beg_ts) / 2
        fig.add_annotation(
            x=sea_mid, y=1.09, xref='x', yref='paper',
            text=f"海警 {sea_beg_ts:%m/%d %H:%M} - {sea_end_ts:%m/%d %H:%M}",
            showarrow=False, font=dict(color='rgba(60, 110, 160, 0.9)', size=11), xanchor='center',
        )

    # --- §5 颱風陸警色帶（象徵警戒，採用淡紅粉色） ---
    land_beg = typhoon_info.get('warnLandBeg')
    land_end = typhoon_info.get('warnLandEnd')
    
    if land_beg is not None and land_end is not None and pd.notna(land_beg) and pd.notna(land_end):
        land_beg_ts = pd.Timestamp(land_beg)
        land_end_ts = pd.Timestamp(land_end)
        
        # 繪製陸警陰影帶（使用溫和淡紅，重疊時會與海警藍融合成優雅的淡紫色）
        fig.add_vrect(
            x0=land_beg_ts, x1=land_end_ts,
            fillcolor='rgba(220, 80, 80, 0.07)',  # 0.07 不會遮擋背景格線 #此處rgba設定數值等同_COLOR_LAND_LINE，到時候統一套參數應該會比較好
            line_width=0, layer='below',
        )
        # 繪製陸警虛線邊界
        for x in (land_beg_ts, land_end_ts):
            fig.add_vline(x=x, line=dict(color=_COLOR_LAND_LINE, dash='dash', width=1))
            
        # 陸警文字標籤（維持在原有的 y=1.03）
        # land_mid = land_beg_ts + (land_end_ts - land_beg_ts) / 2
        land_mid = sea_beg_ts + (sea_end_ts - sea_beg_ts) / 2   #跟海警文字對齊看會不會比較好...但Claude說如果沒有海警要用上面那行，只是哪次颱風會沒有海警只有陸警？
        fig.add_annotation(
            x=land_mid, y=1.03, xref='x', yref='paper',
            text=f"陸警 {land_beg_ts:%m/%d %H:%M} - {land_end_ts:%m/%d %H:%M}",
            showarrow=False, font=dict(color='rgba(210, 60, 60, 0.9)', size=11), xanchor='center',
            # showarrow=False, font=dict(color='#444444', size=11), xanchor='center',
        )

    # --- §6 標題資訊列 ---
    stname = bundle.get('stname', '未知測站')
    # title = (
    #     f"{typhoon_info.get('id', '')} {typhoon_info.get('cname', '')}　"
    #     f"{stname}（舊站碼 {stid_obs} / 新站碼 {stid_new}）"
    # )
    title = (
        f"{typhoon_info.get('id', '')} {typhoon_info.get('cname', '')}<br>"
        f"{stname}（舊站碼 {stid_display}/ 新站碼 {stid_new}）"
    )

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor='center', font=dict(family=_FONT, size=24, color='black')), # 標題置中看看效果，要改可移除或調整
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family=_FONT, color='black', size=16),
        hovermode='x unified',
        # xaxis=dict(title='時間', showgrid=True, gridcolor='rgba(0,0,0,0.08)'), # 主管認為這種顯示方式不夠精簡
        xaxis=dict(title='時間', tickformat="%Y/%-m/%-d %H:%M", showgrid=True, gridcolor='rgba(0,0,0,0.08)'),
        yaxis=dict(title='水位 (mm)', showgrid=True, gridcolor='rgba(0,0,0,0.08)'),
        yaxis2=dict(
            title='暴潮偏差 (mm)', overlaying='y', side='right',
            showgrid=False, range=y2_range,
        ),
        legend=dict(orientation='h', y=-0.18),
        # margin=dict(t=90, b=70),
        # Gemini建議稍微調大 t (top) 數值，給頂部的海陸警文字騰出呼吸空間
        margin=dict(t=130, b=50, l=50, r=50),
    )

    # Gemini建議的設定，看起來似乎是直接接在後面就好？
    # --- 設定 X 軸框線與刻度 ---
    fig.update_xaxes(
        showline=True,         # 顯示軸線
        linecolor='black',     # 設定軸線顏色（也可以用 '#444444' 深灰更溫和）
        linewidth=1.2,         # 軸線粗細
        ticks='outside',       # 將刻度線畫在框線「外側」，把時間標籤往外推
        tickwidth=1.2,
        tickcolor='black',
        showgrid=True,         # 保留原本的背景網格
    )

    # --- 設定 Y 軸（包含左軸 y 與右軸 y2）框線與刻度 ---
    fig.update_yaxes(
        showline=True,         # 顯示軸線
        linecolor='black',     # 設定軸線顏色
        linewidth=1.2,         # 軸線粗細
        ticks='outside',       # 將刻度線畫在外側，把 Y 軸數字往外推，避免跟折線重疊
        tickwidth=1.2,
        tickcolor='black',
        showgrid=True,         # 保留背景網格
    )

    return fig


