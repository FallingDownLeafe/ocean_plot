import pandas as pd
import numpy as np # 需要用到 numpy 計算向量分量
import mysql.connector
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import tkinter as tk
from tkinter import ttk, messagebox
from tkcalendar import DateEntry
import datetime
import os
import sys
if getattr(sys, 'frozen', False):
    # 打包後執行環境
    os.environ['BABEL_DATA_PATH'] = os.path.join(sys._MEIPASS, 'babel')

import platform # [新增] 用於判斷作業系統
import threading
import queue
import webbrowser
from plotly_qc_select import write_chart_html, _start_callback_server, _selection_queue

import dash_bridge
# DASH_PORT = 8050
import socket

def _find_free_port(start=8050):
    for port in range(start, start + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start  # 找不到就用原始值，讓 Flask 自己報錯

DASH_PORT = _find_free_port(8050)


# ==========================================
# 1. 環境配置與跨平台設定
# ==========================================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# CSV_PATH = os.path.join(BASE_DIR, '對應站表格_氣象站.csv')
CSV_PATH = os.path.join(BASE_DIR, '對應站表格.csv')

# 手動讀取同目錄下的 .env 檔（不依賴 python-dotenv，PyInstaller 友好）
_env_path = os.path.join(BASE_DIR, '.env')
if os.path.exists(_env_path):
    with open(_env_path, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())

DB_IP   = os.getenv("DB_IP",   "61.56.13.160")
DB_USER = os.getenv("DB_USER", "dps")



# --- [新增] 跨平台字體動態適應 ---
OS_TYPE = platform.system()
if OS_TYPE == "Windows":
    # UI_FONT = "UI_FONT"
    UI_FONT = "標楷體"  # Gemini Code assist 說 Tkinter 的 font 參數在 tuple 形式下，第一個元素應為單一字體名稱
    # UI_FONT = "標楷體, Microsoft JhengHei, Noto Sans CJK TC, Arial, sans-serif"
elif OS_TYPE == "Darwin": # macOS
    UI_FONT = "PingFang TC"
else: # Linux
    UI_FONT = "Noto Sans CJK TC" # Linux 常見支援的開源中文字體

class OceanDataEngine:
    def __init__(self, password, host=DB_IP, user=DB_USER, database='mrbank', tables=None):
        try:
            self.host = host
            self.user = user
            self.database = database
            self.conty_map = {} # [新增] 初始化縣市對照表
            self.sponsor_map = {} # [新增] 初始化業務單位對照表
            # --- [核心修改 1] 外部參數引入 ---
            # 讀取 .env 中的設定，如果沒有設定，就預設為安外(開發區)的模式
            self.typhoon_db = os.getenv("TYPHOON_DB", "med_data") # 安內請在 .env 設為 mrbank
            self.mapping_src = os.getenv("MAPPING_SRC", "csv")    # 安內請在 .env 設為 db
            # 預設資料表對照表，若外部有傳入則覆蓋
            self.tables = {
                'st': 'st',
                'tide6': 'tide6',
                'tide6ha': 'tide6ha'
            }
            if tables: self.tables.update(tables)
            self.config = {'host': host, 'user': user, 'password': password, 'database': database, 'connect_timeout': 10}
            self.conn = mysql.connector.connect(**self.config)
            self.load_mapping()
        except Exception as e:
            raise Exception(f"資料庫連線失敗: {e}")

    def load_mapping(self):
        # --- [核心修改 2] 智慧切換 CSV 或資料庫讀取 ---
        if self.mapping_src == "db":
            # 【安內模式】從資料庫的 stitemqc 讀取
            try:
                # 假設資料庫中的欄位叫 stid 和 qcid，請依實際情況調整大小寫
                query = "SELECT stid AS STID, qcid AS QCID FROM stitemqc WHERE flag = 's'"
                self.mapping_df = pd.read_sql(query, self.conn).dropna(subset=['STID'])
                self.mapping_df['STID'] = self.mapping_df['STID'].astype(str).str.strip()
            except Exception as e:
                raise Exception(f"從 stitemqc 讀取測站對應失敗: {e}")
        else:
            # 【安外模式】從本地 CSV 讀取
            if not os.path.exists(CSV_PATH): raise FileNotFoundError(f"找不到檔案：{CSV_PATH}")
            self.mapping_df = pd.read_csv(CSV_PATH, encoding='utf-8-sig', dtype={'STID': str}).dropna(subset=['STID'])
            self.mapping_df['STID'] = self.mapping_df['STID'].str.strip()
            
        stids = self.mapping_df['STID'].dropna().unique().tolist()
        cursor = self.conn.cursor(dictionary=True)
        cursor.execute(f"SELECT stid, stnac, conty, sponsor FROM {self.tables['st']} WHERE stid IN ({','.join(['%s']*len(stids))}) AND kind = '6'", tuple(stids))
        # self.name_map = {str(r['stid']).strip(): str(r['stnac']) if r['stnac'] else "未命名" for r in cursor.fetchall()}
        # [修正] 先將結果取出存入 rows，避免 cursor 被一次性消耗完畢
        rows = cursor.fetchall() 
        self.name_map = {str(r['stid']).strip(): str(r['stnac']) if r['stnac'] else "未命名" for r in rows}
        self.conty_map = {str(r['stid']).strip(): str(r['conty']) if r.get('conty') else "未知" for r in rows}
        self.sponsor_map = {str(r['stid']).strip(): str(r['sponsor']) if r.get('sponsor') else "未知" for r in rows}

    def fetch_years(self):
        if self.host == "127.0.0.1": return [] # 本地模式直接跳過
        try:
            # --- [核心修改 3] 動態替換颱風資料庫名稱 ---
            query = f"SELECT DISTINCT LEFT(id, 2) as yr FROM {self.typhoon_db}.typhoonid WHERE sponsor='LocalTime' ORDER BY yr DESC"
            df = pd.read_sql(query, self.conn)
            return [f"20{y}" for y in df['yr']]
        except Exception: return []

    def fetch_typhoons(self, yr_full):
        if self.host == "127.0.0.1": return pd.DataFrame()
        try:
            if self.typhoon_db == "mrbank":
                # 安內：欄位名是大寫 WARN1/WARN2，用 AS 統一成程式碼期望的名稱
                query = f"""SELECT DISTINCT id, cname,
                            WARN1BEG as warnSeaBeg, WARN1END as warnSeaEnd,
                            WARN2BEG as warnLandBeg, WARN2END as warnLandEnd
                            FROM {self.typhoon_db}.typhoonid
                            WHERE sponsor='LocalTime' AND id LIKE '{yr_full[-2:]}%%'
                            ORDER BY id DESC"""
            else:
                # 安外 med_data：欄位名原本就是小寫 warnSeaBeg 等
                query = f"""SELECT DISTINCT id, cname,
                            warnSeaBeg, warnSeaEnd, warnLandBeg, warnLandEnd
                            FROM {self.typhoon_db}.typhoonid
                            WHERE sponsor='LocalTime' AND id LIKE '{yr_full[-2:]}%%'
                            ORDER BY id DESC"""
            return pd.read_sql(query, self.conn)
        except Exception: return pd.DataFrame()
    
    def expand_data(self, df, val_name, freq='6min'):
        if df.empty: return pd.DataFrame(columns=['Time', val_name])
        prefix = 'MIN' if freq == '6min' else 'HR'
        val_cols = [c for c in df.columns if c.startswith(prefix)]
        # 保持原本動態邏輯：自動帶走所有非資料欄位（包含 QC、STID 等）
        id_cols = [c for c in df.columns if not c.startswith(prefix) and c != 'LASTUPDATETIME']
        melted = df.melt(id_vars=id_cols, value_vars=val_cols, var_name='idx', value_name=val_name)
        step = 6 if freq == '6min' else 60
        melted['Time'] = (pd.to_datetime(melted['DATATIME']) + pd.to_timedelta(melted['idx'].str.extract('(\d+)')[0].astype(int)*step, unit='m')).dt.floor('min')
        # 回傳時帶走 QC 欄位（若存在）。
        # 注意：呼叫此函數前應已按 QC 拆分，同一 QC 群組內不應有重複 Time，
        # drop_duplicates 僅作保險用途。
        cols_to_keep = ['Time', val_name, 'QC'] if 'QC' in melted.columns else ['Time', val_name]
        return melted[cols_to_keep].dropna(subset=[val_name]).drop_duplicates(subset=['Time']).sort_values('Time')

    def fetch_tide_instruments(self, stid):
        """
        查詢該測站位置（stid_obs）下的所有水位儀器
        返回 DataFrame: {STID, stid_obs, stid_new, type, stnac, is_primary}
        type: 2=音波式, 3=壓力式, 4=雷達式
        """
        cursor = self.conn.cursor(dictionary=True)
        
        # Step 1: 從輸入的 STID 找到 stid_obs
        cursor.execute(f"SELECT stid_obs FROM {self.tables['st']} WHERE STID = %s LIMIT 1", (stid,))
        result = cursor.fetchone()
        if not result or not result['stid_obs']:
            # 容錯：如果沒有 stid_obs，返回空（降級為單儀器模式）
            return pd.DataFrame()
        
        stid_obs = str(result['stid_obs']).strip()
        
        # Step 2: 查詢該 stid_obs 下的所有水位儀器
        cursor.execute(f"""
            SELECT STID, stid_obs, stid_new, type, stnac
            FROM {self.tables['st']}
            WHERE stid_obs = %s
            ORDER BY type ASC
        """, (stid_obs,))
        
        rows = cursor.fetchall()
        if not rows:
            return pd.DataFrame()
        
        # Step 3: 轉為 DataFrame，標記主測站（通常是 stid_new = stid_obs 的那個）
        df = pd.DataFrame(rows)
        df['STID'] = df['STID'].astype(str).str.strip()
        df['stid_obs'] = df['stid_obs'].astype(str).str.strip()
        df['stid_new'] = df['stid_new'].astype(str).str.strip()
        df['type'] = df['type'].astype(int)
        
        # 標記主測站：stid_new == stid_obs 的為主測站
        df['is_primary'] = (df['stid_new'] == df['stid_obs']).astype(int)
        
        return df

    def query_multi_tide_data(self, tide_instruments_df, start, end, start_str, end_str):
        """
        為多個水位儀器批量查詢 tide6/tide6ha 資料
        輸入：tide_instruments_df (from fetch_tide_instruments)
        輸出：{
            'wl_data': {STID: expanded_df, ...},
            'pred_data': {STID: expanded_df, ...} (僅主測站),
            'tide_meta': {STID: {type, stnac, is_primary}, ...}
        }
        """
        wl_data = {}
        pred_data = {}
        pred_data_a = {}
        tide_meta = {}
        
        for _, row in tide_instruments_df.iterrows():
            stid = row['STID']
            type_val = row['type']
            stnac = row['stnac']
            is_primary = row['is_primary']
            
            # 記錄元數據
            type_desc = {2: '音波式', 3: '壓力式', 4: '雷達式'}.get(type_val, '未知')
            tide_meta[stid] = {
                'type': type_val,
                'type_desc': type_desc,
                'stnac': stnac,
                'is_primary': is_primary
            }
            
            # 查詢觀測資料（不限制 QC，讓程式端處理）
            df_obs = pd.read_sql(
                f"SELECT * FROM {self.tables['tide6']} WHERE STID='{stid}' AND DATATIME BETWEEN '{start_str}' AND '{end_str}'",
                self.conn
            )
            if not df_obs.empty:
                # mysql.connector 原生連線會把欄位名轉小寫，統一轉回大寫避免 KeyError
                df_obs.columns = [c.upper() for c in df_obs.columns]

                # --- [核心修改] 拆分 QC=Q（校正值）與 QC≠Q（原始機測值），各自展開後並排 ---
                # 這樣同一個時間點的兩筆資料都能保留，繪圖端可以分開標示
                df_q   = df_obs[df_obs['QC'].str.upper() == 'Q']
                df_bad = df_obs[df_obs['QC'].str.upper() != 'Q']

                # 展開 QC=Q 群組（可信的校正值）
                if not df_q.empty:
                    expanded_q = self.expand_data(df_q, f'WL_{stid}')
                    expanded_q = expanded_q.rename(columns={'QC': f'QC_{stid}'})
                else:
                    expanded_q = pd.DataFrame(columns=['Time', f'WL_{stid}', f'QC_{stid}'])

                # 展開 QC≠Q 群組（原始機測值，欄位名加 _raw 以示區別）
                if not df_bad.empty:
                    expanded_bad = self.expand_data(df_bad, f'WL_{stid}_raw')
                    expanded_bad = expanded_bad.rename(columns={'QC': f'QC_{stid}_raw'})
                else:
                    expanded_bad = pd.DataFrame(columns=['Time', f'WL_{stid}_raw', f'QC_{stid}_raw'])

                # 以 Time 為鍵 outer merge，讓兩組資料並排在同一列
                expanded = pd.merge(expanded_q, expanded_bad, on='Time', how='outer').sort_values('Time')

                # [DEBUG] 確認欄位結構與各 QC 群組的資料量
                print(f"[DEBUG] stid={stid} 展開後欄位: {expanded.columns.tolist()}")
                print(f"[DEBUG] stid={stid} QC=Q  筆數: {expanded[f'WL_{stid}'].notna().sum()}")
                print(f"[DEBUG] stid={stid} QC≠Q 筆數: {expanded[f'WL_{stid}_raw'].notna().sum()}")
                if f'QC_{stid}_raw' in expanded.columns:
                    print(f"[DEBUG] stid={stid} QC≠Q 值分佈:\n{expanded[f'QC_{stid}_raw'].value_counts(dropna=False)}")

                wl_data[stid] = expanded
            
            # 只為主測站查詢預報資料
            if is_primary:
                # 諧和預報 (h)
                df_pred_h = pd.read_sql(
                    f"SELECT * FROM {self.tables['tide6ha']} WHERE STID='{stid}' AND QC='h' AND DATATIME BETWEEN '{start_str}' AND '{end_str}'",
                    self.conn
                )
                if not df_pred_h.empty:
                    pred_data[stid] = self.expand_data(df_pred_h, f'WL_{stid}_pred_h')
                
                # 天文潮預報 (a)
                df_pred_a = pd.read_sql(
                    f"SELECT * FROM {self.tables['tide6ha']} WHERE STID='{stid}' AND QC='a' AND DATATIME BETWEEN '{start_str}' AND '{end_str}'",
                    self.conn
                )
                if not df_pred_a.empty:
                    pred_data_a[stid] = self.expand_data(df_pred_a, f'WL_{stid}_pred_a')
        
        return {
            'wl_data': wl_data,
            'pred_data': pred_data,
            'pred_data_a': pred_data_a,
            'tide_meta': tide_meta
        }

    def fetch_bundle(self, stid, start, end):
        s_s, e_s = start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')
        
        # 1. 準備候選名單 (Candidates) - [修正邏輯：QCID 優先，自己墊底]
        row = self.mapping_df[self.mapping_df['STID'] == stid]
        # 解析 CSV 中的 QCID
        q_ids = [x.strip() for x in str(row.iloc[0]['QCID']).split(',')] if not row.empty and pd.notna(row.iloc[0]['QCID']) else []
        
        # 這裡的順序決定了資料的優先權：先查 QCID 指定的站，最後才查自己
        candidates = q_ids + [stid]
        # 去重但保持順序 (例如 QCID 裡有自己，就不用查兩次)
        candidates = list(dict.fromkeys(candidates))

        # 2. 一次查詢所有候選站的 Kind (效率優化)
        cursor = self.conn.cursor(dictionary=True)
        if candidates:
            format_strings = ','.join(['%s'] * len(candidates))
            cursor.execute(f"SELECT stid, kind FROM {self.tables['st']} WHERE stid IN ({format_strings})", tuple(candidates))
            kind_map = {str(r['stid']).strip(): str(r['kind']).strip() for r in cursor.fetchall()}
        else:
            kind_map = {}

        # 3. 抓取多儀器水位資料 (核心改動：支持音波、壓力、雷達多儀器)
        # ==========================================
        tide_instruments_df = self.fetch_tide_instruments(stid)
        
        if tide_instruments_df.empty:
            # 容錯：如果沒找到 stid_obs（舊系統），降級為單儀器模式
            # [修改] 同樣移除 AND QC='Q'，抓全部資料；並統一欄位名大寫
            _df_obs_fb = pd.read_sql(f"SELECT * FROM {self.tables['tide6']} WHERE STID='{stid}' AND DATATIME BETWEEN '{s_s}' AND '{e_s}'", self.conn)
            if not _df_obs_fb.empty: _df_obs_fb.columns = [c.upper() for c in _df_obs_fb.columns]
            obs = self.expand_data(_df_obs_fb, 'Obs')
            pre = self.expand_data(pd.read_sql(f"SELECT * FROM {self.tables['tide6ha']} WHERE STID='{stid}' AND QC='h' AND DATATIME BETWEEN '{s_s}' AND '{e_s}'", self.conn), 'Pre')
            main = pd.merge(obs, pre, on='Time', how='outer')
            if 'Obs' in main.columns and 'Pre' in main.columns:
                main['Resi'] = main['Obs'] - main['Pre']
            else:
                main['Resi'] = np.nan
            tide_meta = {}
        else:
            # 新系統：查詢多儀器資料
            tide_result = self.query_multi_tide_data(tide_instruments_df, start, end, s_s, e_s)
            wl_data = tide_result['wl_data']
            pred_data = tide_result['pred_data']
            pred_data_a = tide_result.get('pred_data_a', {}) # [新增] 取得 QC='a' 預報
            tide_meta = tide_result['tide_meta']
            
            # 合併所有水位資料到 main
            main = pd.DataFrame()
            
            # A. 先取第一個水位儀器的 Time 作為基準
            if wl_data:
                main = list(wl_data.values())[0][['Time']].copy()
            else:
                # 如果沒有水位資料，建立空 DataFrame
                main = pd.DataFrame(columns=['Time'])
            
            # B. 逐個 merge 所有水位儀器
            for stid_wl, wl_df in wl_data.items():
                main = pd.merge(main, wl_df, on='Time', how='outer')
            
            # C. Merge 預報資料（僅主測站）
            for stid_pred, pred_df in pred_data.items():
                main = pd.merge(main, pred_df, on='Time', how='outer')
            # [新增] Merge QC='a' 預報資料
            for stid_pred, pred_df_a in pred_data_a.items():
                main = pd.merge(main, pred_df_a, on='Time', how='outer')
            
            # D. 計算主測站的暴潮偏差（Resi）
            # 找出主測站的 STID
            primary_stids = [st for st, meta in tide_meta.items() if meta['is_primary']]
            if primary_stids and f"WL_{primary_stids[0]}" in main.columns and f"WL_{primary_stids[0]}_pred_h" in main.columns:
                main['Resi'] = main[f"WL_{primary_stids[0]}"] - main[f"WL_{primary_stids[0]}_pred_h"]
            else:
                main['Resi'] = np.nan
            
            # 原先對每個儀器的水位都計算 25h 低頻移動平均的設計，線條略多且可能不必要，後改為只對主測站計算低頻線，且測試改以指數平滑（EWMA）減少邊界效應和資料缺口的影響。
            # D-1. 第一處：fetch_bundle 裡 wl_data merge 完之後（約在把 main 回傳前），加一段對每個 stid 計算移動平均：
            # 在 fetch_bundle 的 main merge 完成後，回傳前加入
            # 對每個儀器的水位計算 25h 低頻移動平均
            # SAMPLING_MIN = 6
            # WINDOW_PTS = int(25 * 60 / SAMPLING_MIN)  # = 250 點

            # for stid_wl in wl_data.keys():
            #     col = f'WL_{stid_wl}'
            #     lf_col = f'WL_{stid_wl}_lf'
            #     if col in main.columns:
            #         main[lf_col] = (
            #             main[col]
            #             .rolling(window=WINDOW_PTS, center=True, min_periods=int(WINDOW_PTS * 0.5))
            #             .mean()
            #         )

            SAMPLING_MIN = 6
            WINDOW_PTS = int(25 * 60 / SAMPLING_MIN)

            # 只對主儀器計算低頻濾波線
            primary_stid = next(
                (stid_wl for stid_wl, meta in tide_meta.items() if meta['is_primary']),
                None
            )
            if primary_stid:
                col = f'WL_{primary_stid}'
                lf_col = f'WL_{primary_stid}_lf'
                ew_col = f'WL_{primary_stid}_ewma'   # 新增
                if col in main.columns:
                    # main[lf_col] = (
                    #     main[col]
                    #     .ewm(alpha=0.05, adjust=False, ignore_na=True) # 一階指數平滑移動平均法，效果其實差不多，可以視情況改alpha權重
                    #     .mean()
                    # )
                    main[lf_col] = (
                        main[col]
                        .rolling(window=WINDOW_PTS, center=True, min_periods=int(WINDOW_PTS * 0.5))
                        .mean()
                    )
                    main[ew_col] = (                 # 新增
                        main[col]
                        .ewm(alpha=0.05, adjust=False, ignore_na=True) # 一階指數平滑移動平均法，效果其實差不多，可以視情況改alpha權重
                        .mean()
                    )

            # D-2. 查詢 MR（平均潮差）用於暴潮偏差正規化
            start_dt = pd.to_datetime(s_s)
            year = start_dt.year
            month = start_dt.month
            
            # 試試全年 MR (MONTH=0)
            query_mr_full_year = f"SELECT MR FROM tidestat WHERE STID='{stid}' AND YEAR={year} AND MONTH=0 AND SL='S' AND QC='Q'"
            df_mr_full = pd.read_sql(query_mr_full_year, self.conn)
            mr_full = df_mr_full['MR'].values[0] if not df_mr_full.empty and df_mr_full['MR'].notna().any() else None
            
            # 也試試當月 MR
            query_mr_month = f"SELECT MR FROM tidestat WHERE STID='{stid}' AND YEAR={year} AND MONTH={month} AND SL='S' AND QC='Q'"
            df_mr_month = pd.read_sql(query_mr_month, self.conn)
            mr_month = df_mr_month['MR'].values[0] if not df_mr_month.empty and df_mr_month['MR'].notna().any() else None
            
            # 偏好全年 MR，若無則用當月
            mr_value = mr_full if mr_full else mr_month
            
            if mr_value and mr_value != 0:
                # MR 在 tidestat 中已經縮放（×0.1），需要還原再計算百分比
                mr_mm = mr_value * 1.0  # 同步 tide6 的縮放方式
                main['Resi_Norm'] = (main['Resi'] / mr_mm) * 100  # 百分比
                print(f"\n【正規化資訊】{stid} - 使用 MR={mr_mm:.1f}mm (全年={mr_full}, 當月={mr_month})")
            else:
                main['Resi_Norm'] = np.nan
                print(f"\n【正規化資訊】{stid} - 無有效 MR 資料")
            
            # E. 計算儀器間的差值 (任一為空則不計算)
            if len(tide_meta) > 1:
                stid_list = sorted(list(tide_meta.keys()))  # 排序保持一致性
                primary_stid = primary_stids[0] if primary_stids else stid_list[0]
                
                for i, other_stid in enumerate(stid_list):
                    if other_stid != primary_stid:
                        col_name = f"Diff_{primary_stid}_{other_stid}"
                        if f"WL_{primary_stid}" in main.columns and f"WL_{other_stid}" in main.columns:
                            # 只有兩個都有值才計算差值，否則為 NaN
                            main[col_name] = np.where(
                                (main[f"WL_{primary_stid}"].notna()) & (main[f"WL_{other_stid}"].notna()),
                                main[f"WL_{primary_stid}"] - main[f"WL_{other_stid}"],
                                np.nan
                            )
                            
                            # [測試] 列印差值驗證
                            valid_rows = main[[f"WL_{primary_stid}", f"WL_{other_stid}", col_name]].dropna()
                            if not valid_rows.empty:
                                print(f"\n【差值驗證】{primary_stid} vs {other_stid} (總共 {len(valid_rows)} 筆有效數據)")
                                print(f"前10筆：")
                                for idx, (_, row) in enumerate(valid_rows.head(10).iterrows()):
                                    print(f"  [{idx+1}] {primary_stid}={row[f'WL_{primary_stid}']:.1f}mm, {other_stid}={row[f'WL_{other_stid}']:.1f}mm, 差值={row[col_name]:.1f}mm (驗算={row[f'WL_{primary_stid}']-row[f'WL_{other_stid}']:.1f}mm)")
        
        # 排序並鎖定時間範圍
        if not main.empty:
            main = main.sort_values('Time').reset_index(drop=True)
            main = main[(main['Time'] >= pd.to_datetime(s_s)) & (main['Time'] <= pd.to_datetime(e_s))]
        
        # 初始化為空 Resi（以防萬一）
        if 'Resi' not in main.columns:
            main['Resi'] = np.nan

        # 4. 初始化環境變數 DataFrame
        p_data, w_data, at_data, wt_data, wv_data, cu_data = [pd.DataFrame() for _ in range(6)]
        src_ids = {'p': 'None', 'w': 'None', 'wv': 'None', 'wt': 'None'}

        # ==========================================
        # 5. 迴圈搜尋環境資料 (氣壓、風、氣溫)
        # 邏輯：按照 candidates 順序，誰先有資料就用誰的
        # ==========================================
        for cid in candidates:
            kind = kind_map.get(cid)
            if not kind: continue # 如果資料庫 st 表沒這個站，跳過

            # --- A. 氣壓 (Pressure) ---
            # 只有當 p_data 還是空的時候，才去查
            if p_data.empty:
                df_p = pd.DataFrame()
                if kind in ['1', '2', '3']: # 氣象站 -> meteo
                    df_p = pd.read_sql(f"SELECT DATATIME as Time, (p*0.1) as P FROM meteo WHERE stid='{cid}' AND min=0 AND DATATIME BETWEEN '{s_s}' AND '{e_s}'", self.conn)
                elif kind == '8': # 浮標 -> pres1
                    df_p = self.expand_data(pd.read_sql(f"SELECT * FROM pres1 WHERE STID='{cid}' AND DATATIME BETWEEN '{s_s}' AND '{e_s}'", self.conn), 'P', '1h')
                    if not df_p.empty: df_p['P'] *= 0.1
                elif kind == '6': # 潮位站 -> pres6
                    df_p = self.expand_data(pd.read_sql(f"SELECT * FROM pres6 WHERE STID='{cid}' AND DATATIME BETWEEN '{s_s}' AND '{e_s}' AND QC = 'Q'", self.conn), 'P', '1h')
                    if not df_p.empty: df_p['P'] *= 0.1
                
                # 如果查到了，就存起來，並標記來源
                if not df_p.empty:
                    p_data = df_p
                    src_ids['p'] = cid

            # --- B. 風 (Wind) ---
            if w_data.empty:
                df_w = pd.DataFrame()
                if kind in ['1', '2', '3']: # 氣象站 -> meteo
                    df_w = pd.read_sql(f"SELECT DATATIME as Time, (ws*0.1) as WS, wd as WD FROM meteo WHERE stid='{cid}' AND min=0 AND DATATIME BETWEEN '{s_s}' AND '{e_s}'", self.conn)
                elif kind in ['6', '8']: # 潮位站/浮標 -> wind
                    # 決定 Z 值: 潮位站 Z=6, 浮標 Z=2 或 3
                    z_condition = "Z='6'" if kind == '6' else "Z IN ('2', '3')"
                    raw_w = pd.read_sql(f"SELECT TIME as Time, (VM*0.1) as WS, DM as WD, Z, qc as QC FROM wind WHERE STID='{cid}' AND {z_condition} AND TIME BETWEEN '{s_s}' AND '{e_s}'", self.conn)
                    if not raw_w.empty:
                        raw_w['Time'] = pd.to_datetime(raw_w['Time']).dt.floor('min')
                        # 浮標若有多個風速計，優先取 Z 小的 (Z=2)
                        if kind == '8':
                            raw_w = raw_w.sort_values('Z').drop_duplicates(subset=['Time'], keep='first')
                        # 確保 QC 判斷不分大小寫
                        raw_w['QC_UP'] = raw_w['QC'].str.upper()
                        raw_w_q   = raw_w[raw_w['QC_UP'] == 'Q'][['Time', 'WS', 'WD']]
                        raw_w_bad = raw_w[raw_w['QC_UP'] != 'Q'][['Time', 'WS', 'WD', 'QC']].rename(
                            columns={'WS': 'WS_raw', 'WD': 'WD_raw', 'QC': 'WS_QC_raw'})
                        df_w = pd.merge(raw_w_q, raw_w_bad, on='Time', how='outer')
                
                if not df_w.empty:
                    # 確保時間對齊分鐘
                    df_w['Time'] = pd.to_datetime(df_w['Time']).dt.floor('min')
                    w_data = df_w
                    src_ids['w'] = cid

            # --- C. 氣溫 (Air Temp) ---
            if at_data.empty:
                df_at = pd.DataFrame()
                if kind in ['1', '2', '3']: # 氣象站 -> meteo
                    df_at = pd.read_sql(f"SELECT DATATIME as Time, (t*0.1) as AT FROM meteo WHERE stid='{cid}' AND min=0 AND DATATIME BETWEEN '{s_s}' AND '{e_s}'", self.conn)
                elif kind in ['6', '8']: # 潮位站/浮標 -> stemp6 / stemp1 (Z=-3)
                    table = 'stemp6' if kind == '6' else 'stemp1'
                    raw_at = pd.read_sql(f"SELECT * FROM {table} WHERE STID='{cid}' AND Z='-3' AND DATATIME BETWEEN '{s_s}' AND '{e_s}'", self.conn)
                    if not raw_at.empty:
                        raw_at.columns = [c.upper() for c in raw_at.columns]
                    if kind == '6' and not raw_at.empty and 'QC' in raw_at.columns:
                        # stemp6 有 QC 欄位，拆分正常與異常
                        raw_at['QC_UP'] = raw_at['QC'].str.upper()
                        at_q   = raw_at[raw_at['QC_UP'] == 'Q']
                        at_bad = raw_at[raw_at['QC_UP'] != 'Q']
                        df_at_q   = self.expand_data(at_q,   'AT',     '1h') if not at_q.empty   else pd.DataFrame(columns=['Time', 'AT'])
                        df_at_bad = self.expand_data(at_bad, 'AT_raw', '1h') if not at_bad.empty else pd.DataFrame(columns=['Time', 'AT_raw'])
                        if not df_at_q.empty:   df_at_q['AT']     *= 0.1
                        if not df_at_bad.empty:
                            df_at_bad['AT_raw'] *= 0.1
                            df_at_bad = df_at_bad.rename(columns={'QC': 'AT_QC_raw'}) if 'QC' in df_at_bad.columns else df_at_bad
                        df_at = pd.merge(df_at_q, df_at_bad, on='Time', how='outer')
                    else:
                        df_at = self.expand_data(raw_at, 'AT', '1h')
                        if not df_at.empty: df_at['AT'] *= 0.1

                if not df_at.empty:
                    at_data = df_at
                    # 氣溫通常沒有獨立的圖例標籤，我們假設它跟氣壓同一來源，或不特別標示
            # ... (接在 C. 氣溫 之後) ...

            # --- D. 海溫 (Water Temp) [新增] ---
            # 邏輯：因為在迴圈內，會先查 QCID (浮標)，沒資料才會查到自己 (潮位站)
            if wt_data.empty:
                df_wt = pd.DataFrame()
                if kind == '8': # 浮標 -> stemp1 (Z=0)
                    raw_wt = pd.read_sql(f"SELECT * FROM stemp1 WHERE STID='{cid}' AND Z='0' AND DATATIME BETWEEN '{s_s}' AND '{e_s}'", self.conn)
                    df_wt = self.expand_data(raw_wt, 'WT', '1h')
                    if not df_wt.empty: df_wt['WT'] *= 0.1
                elif kind == '6': # 潮位站 -> stemp6 (Z=0)
                    raw_wt = pd.read_sql(f"SELECT * FROM stemp6 WHERE STID='{cid}' AND Z='0' AND DATATIME BETWEEN '{s_s}' AND '{e_s}'", self.conn)
                    if not raw_wt.empty:
                        raw_wt.columns = [c.upper() for c in raw_wt.columns]
                    if not raw_wt.empty and 'QC' in raw_wt.columns:
                        raw_wt['QC_UP'] = raw_wt['QC'].str.upper()
                        wt_q   = raw_wt[raw_wt['QC_UP'] == 'Q']
                        wt_bad = raw_wt[raw_wt['QC_UP'] != 'Q']
                        df_wt_q   = self.expand_data(wt_q,   'WT',     '1h') if not wt_q.empty   else pd.DataFrame(columns=['Time', 'WT'])
                        df_wt_bad = self.expand_data(wt_bad, 'WT_raw', '1h') if not wt_bad.empty else pd.DataFrame(columns=['Time', 'WT_raw'])
                        if not df_wt_q.empty:   df_wt_q['WT']     *= 0.1
                        if not df_wt_bad.empty:
                            df_wt_bad['WT_raw'] *= 0.1
                            df_wt_bad = df_wt_bad.rename(columns={'QC': 'WT_QC_raw'}) if 'QC' in df_wt_bad.columns else df_wt_bad
                        df_wt = pd.merge(df_wt_q, df_wt_bad, on='Time', how='outer')
                    else:
                        df_wt = self.expand_data(raw_wt, 'WT', '1h')
                        if not df_wt.empty: df_wt['WT'] *= 0.1
                
                if not df_wt.empty:
                    wt_data = df_wt
                    src_ids['wt'] = cid

            # 檢查點：現在有四個變數要檢查 (P, W, AT, WT)
            # 如果都齊了就 break。注意：如果該站本來就沒海溫(如氣象站)，這行可能會導致提早結束而沒查到後面的海溫
            # 建議稍微放寬條件，或者乾脆拿掉 break 讓它跑完 (反正候選站不多)，比較保險
            # if not p_data.empty and not w_data.empty and not at_data.empty and not wt_data.empty:
            #    break

            # 檢查點：如果三樣都齊了，就不用浪費資源查後面的站了
            if not p_data.empty and not w_data.empty and not at_data.empty:
                break
        
        # ==========================================
        # 6. 海洋資料 (波浪、海流、海溫) - 浮標專用
        # 邏輯：一樣優先查 QCID 裡的浮標，最後才查自己
        # ==========================================
        # 從候選名單中找出所有的浮標 ID (kind=8)
        buoy_candidates = [cid for cid in candidates if kind_map.get(cid) == '8']
        
        # 我們只取「優先順位最高」的那個浮標來畫海象圖
        b_id = buoy_candidates[0] if buoy_candidates else None
        
        if b_id:
            src_ids['wv'] = src_ids['wt'] = b_id

            # A. 波浪 (Wave)
            # df_wv_raw = pd.read_sql(f"SELECT YEAR, MONTH, DAY, HOUR, H, TMEAN FROM wave WHERE STID='{b_id}' AND YEAR={start.year} AND MONTH BETWEEN {start.month} AND {end.month}", self.conn)
            # if not df_wv_raw.empty:
            #     df_wv_raw['Time'] = pd.to_datetime(df_wv_raw[['YEAR', 'MONTH', 'DAY', 'HOUR']].assign(MINUTE=0, SECOND=0))
            #     wv_data = df_wv_raw[['Time', 'H', 'TTMEAN']].rename(columns={'H': 'H_mm', 'TTMEAN': 'T_sec'})
            #     wv_data['H_mm'] = wv_data['H_mm'] * 10 
            #     wv_data['T_sec'] = wv_data['T_sec'] * 0.1 
            #     wv_data = wv_data[(wv_data['Time'] >= pd.to_datetime(s_s)) & (wv_data['Time'] <= pd.to_datetime(e_s))]
            # ... (A. 波浪 Wave 區塊開始) ...
            # [修正跨年問題的寫法]
            # 邏輯：直接抓取 "起始年" 和 "結束年" 的所有資料 (YEAR IN (...))
            # 這樣就算跨年 (2025->2026)，兩年的資料都會被撈出來，不會漏掉
            query = f"""
                SELECT YEAR, MONTH, DAY, HOUR, H, TMEAN 
                FROM wave 
                WHERE STID='{b_id}' 
                AND YEAR IN ({start.year}, {end.year}) 
            """
            df_wv_raw = pd.read_sql(query, self.conn)

            if not df_wv_raw.empty:
                # 組合時間欄位
                df_wv_raw['Time'] = pd.to_datetime(df_wv_raw[['YEAR', 'MONTH', 'DAY', 'HOUR']].assign(MINUTE=0, SECOND=0))
                
                # 整理欄位
                wv_data = df_wv_raw[['Time', 'H', 'TMEAN']].rename(columns={'H': 'H_m', 'TMEAN': 'T_sec'})
                
                # 單位換算
                wv_data['H_m'] = wv_data['H_m'] * 0.01
                wv_data['T_sec'] = wv_data['T_sec'] * 0.1 

                # [關鍵] 用 Python 進行最後的時間切割
                # 因為 SQL 抓了一整年，這裡要把頭尾不需要的部分切掉
                wv_data = wv_data[(wv_data['Time'] >= pd.to_datetime(s_s)) & (wv_data['Time'] <= pd.to_datetime(e_s))]

            # B. 海流 (Current)
            cu_df = pd.read_sql(f"SELECT TIME as Time, (V*0.1) as V, D as DIR FROM curr WHERE STID='{b_id}' AND Z='4' AND TIME BETWEEN '{s_s}' AND '{e_s}'", self.conn)
            if not cu_df.empty:
                cu_df['Time'] = pd.to_datetime(cu_df['Time']).dt.floor('min')
                cu_data = cu_df[['Time', 'V', 'DIR']]


        # ==========================================
        # 7. 合併與輸出
        # ==========================================
        dfs_to_merge = [p_data, w_data, at_data, wt_data, wv_data, cu_data]
        for df_part in dfs_to_merge:
            if not df_part.empty:
                df_part['Time'] = pd.to_datetime(df_part['Time']).dt.floor('min')
                df_part = df_part.drop_duplicates(subset=['Time'])
                # [關鍵] 使用 outer join 保留所有資料 (例如氣象站的資料時間點可能跟潮位站不同)
                main = pd.merge(main, df_part, on='Time', how='outer')

        main = main.sort_values('Time').reset_index(drop=True)
        # 強制鎖定時間範圍，切掉 outer join 可能產生的頭尾空值
        main = main[(main['Time'] >= pd.to_datetime(s_s)) & (main['Time'] <= pd.to_datetime(e_s))]

        # 異常值過濾
        for c in ['AT', 'WT']:
            if c in main.columns: 
                main.loc[(main[c] > 40) | (main[c] <= 10), c] = np.nan
        
        # ==========================================
        # 8. 查詢所有 src_ids 的測站名稱 (用於圖例顯示)
        # ==========================================
        src_names = {}
        unique_srcs = set([v for v in src_ids.values() if v != 'None'])
        if unique_srcs:
            format_strings = ','.join(['%s'] * len(unique_srcs))
            cursor = self.conn.cursor(dictionary=True)
            cursor.execute(f"SELECT stid, stnac FROM {self.tables['st']} WHERE stid IN ({format_strings})", tuple(unique_srcs))
            src_names = {str(r['stid']).strip(): str(r['stnac']) if r['stnac'] else "未命名" for r in cursor.fetchall()}
        
        return {
            'stid': stid, 
            'stname': self.name_map.get(stid, "未知"), 
            'src_ids': src_ids, 
            'src_names': src_names,  # 所有數據源的名稱對應表
            'tide_meta': tide_meta,  # [新增] 水位儀器元數據 {STID: {type, type_desc, stnac, is_primary}}
            'mr_full': mr_full,      # [新增] 全年平均潮差
            'mr_month': mr_month,    # [新增] 當月平均潮差
            'df': main
        }

def draw_diagnostic(bundles, land_range=None):
# ==========================================
    # 1. 空間設定 (加大數值以避免重疊)
    # ==========================================
    # 每個測站的高度 (加大到 800，讓波形圖有空間伸展)
    STATION_BLOCK_HEIGHT = 800
    
    # 子圖垂直間距 (加大到 120，這才是容納滑桿+文字所需的真實空間)
    # 50px (文字) + 30px (滑桿) + 40px (緩衝) = 120px
    GAP_PX = 120 
    
    # 滑桿高度固定 30px
    PX_SLIDER = 30
    
    for i in range(0, len(bundles), 3):
        chunk = bundles[i:i+3]
        n_stations = len(chunk)
        
        # A. 算出總高度
        total_height = STATION_BLOCK_HEIGHT * n_stations
        
        # B. 算出間距比例
        # 這裡會算出來大約 0.05~0.1 左右，這才足夠把圖表推開
        spacing_ratio = GAP_PX / total_height if total_height > 0 else 0.1
        
        # C. 算出滑桿比例
        slider_ratio = PX_SLIDER / total_height if total_height > 0 else 0.05

        # ... (前面的設定 STATION_BLOCK_HEIGHT, GAP_PX... 維持不變) ...

        # ==========================================
        # 2. 建立畫布 (標題更新)
        # ==========================================
        fig = make_subplots(
            rows=n_stations*2, cols=2,
            # [修改] 更新子圖標題順序
            subplot_titles=[f"{b['stname']}({b['stid']}) - {t}" for b in chunk for t in 
                            ["水位 (Obs/Pre)",    # (1,1) 左上
                             "海氣象 (風/流/溫)",    # (1,2) 右上
                             "暴潮與氣壓 (暴潮偏差/氣壓)",  # (2,1) 左下
                             "波浪特性 (示性波高/平均週期)"]],   # (2,2) 右下
            specs=[[{"secondary_y": True}]*2]*(n_stations*2),
            vertical_spacing=spacing_ratio,
        )
        
        for idx, b in enumerate(chunk):
            r_top = idx * 2 + 1
            r_bot = r_top + 1
            df = b['df']
            lbl = f"{b['stname']}({b['stid']})"
            sids = b['src_ids']
            src_names = b.get('src_names', {})  # [新增] 取得數據源名稱對應表
            mr_full = b.get('mr_full')  # [新增] 取得全年平均潮差
            mr_month = b.get('mr_month')  # [新增] 取得當月平均潮差
            
            # ==========================================
            # [新增] 輔助函式：取得正確的測站名稱標籤
            # ==========================================
            def get_src_label(src_id, default_param_name):
                """根據 src_id 和參數名稱，生成格式為 '測站ID(中文名)-參數' 的標籤"""
                if src_id == 'None':
                    return f"無數據-{default_param_name}"
                src_cname = src_names.get(src_id, "未知")
                return f"{src_cname}({src_id})-{default_param_name}"
            
            # 取得水位儀器元數據
            tide_meta = b.get('tide_meta', {})
            
            # =========================================================
            # (1,1) 左上：多儀器水位 + 預報水位 + 儀器間差值
            # 色系：藍色系(水位) + 綠色(預報) + 橙色系(差值)
            # =========================================================
            # A. 繪製所有水位儀器的觀測水位 (藍色系梯度)
            type_colors = {2: '#1f77b4', 3: '#0d47a1', 4: '#64b5f6'}  # 主(深藍) 備1(深藍) 備2(淺藍)
            type_names = {2: '音波', 3: '壓力', 4: '雷達'}
            
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
                    color = type_colors.get(type_val, 'gray')
                    dash_style = 'solid' if meta['is_primary'] else 'dash'
                    is_hidden = 'legendonly' if not meta['is_primary'] else True
                    
                    # Trace 1：校正值（QC=Q），connectgaps=False 讓異常區間自動斷線
                    fig.add_trace(go.Scattergl(
                        x=df['Time'], y=df[col_name], 
                        name=label, 
                        mode='lines+markers',
                        line=dict(color=color, dash=dash_style, width=1.2),
                        marker=dict(size=2.5, opacity=0.6),
                        connectgaps=False,
                        visible=is_hidden # 跟主線一樣的顯示邏輯
                    ), row=r_top, col=1)
                    # 低頻趨勢線（25h 移動平均）
                    lf_col = f'WL_{stid_wl}_lf'     # 這個欄位在前面只針對主測站計算，未來想全部儀器都看的話改前面函式就能兼容
                    if lf_col in df.columns and df[lf_col].notna().any():
                        fig.add_trace(go.Scattergl(
                            x=df['Time'], y=df[lf_col],
                            name=f"{meta['stnac']}({stid_wl})-水位低頻趨勢(25h-MA)",
                            mode='lines',
                            line=dict(color='rgba(180,180,180,0.55)', width=1.2),
                            connectgaps=False,
                            visible=is_hidden # 跟主線一樣的顯示邏輯
                        ), row=r_top, col=1, secondary_y=False)
                    # 新增：EWMA 線，預設隱藏，點圖例才顯示
                    ew_col = f'WL_{stid_wl}_ewma'
                    if ew_col in df.columns and df[ew_col].notna().any():
                        fig.add_trace(go.Scattergl(
                            x=df['Time'], y=df[ew_col],
                            name=f"{meta['stnac']}({stid_wl})-EWMA(α=0.05)",
                            mode='lines',
                            line=dict(color='rgba(255,200,100,0.7)', width=1.2),
                            connectgaps=True,          # EWMA 跨缺口不斷線，這裡設 True
                            visible=is_hidden       # 跟主線一樣的顯示邏輯
                        ), row=r_top, col=1, secondary_y=False)
                    
                    # Trace 2：原始機測值（QC≠Q），紅叉
                    if raw_col_name in df.columns:
                        raw_mask = df[raw_col_name].notna()
                        if raw_mask.any():
                            customdata = df.loc[raw_mask, qc_raw_name].fillna('?').values if qc_raw_name in df.columns else ['?'] * raw_mask.sum()
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
            
            # B. 繪製主測站的預報水位 (綠色)
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
            
            # C. 繪製儀器間的差值 (橙色系梯度，只在兩個都有值時顯示)
            # diff_colors = ['#ff7f0e', "#d84315", '#ffb74d']  # 深橘 -> 中橘 -> 淺橘
            diff_colors = ['#ff7f0e', '#e377c2', '#17becf']  # 高對比：橘、洋紅、青
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
                            color = diff_colors[diff_idx % len(diff_colors)]
                            
                            fig.add_trace(go.Scattergl(
                                x=df['Time'], y=df[diff_col_name],
                                name=label,
                                # mode='lines+markers',
                                mode='markers', # 改成只顯示差值的點狀分布，避免低解析度資料點連線後顯得雜亂
                                line=dict(color=color, width=0.8),
                                marker=dict(size=2, opacity=0.5),
                                connectgaps=True,
                                yaxis='y2' if diff_idx > 0 else 'y'  # AI認為若差值過多可用右軸，但我覺得無論如何都該用右軸
                            ), row=r_top, col=1, secondary_y=True)
                            
                            diff_idx += 1

            # =========================================================
            # (2,1) 左下：暴潮偏差 (左軸 粉色) + 氣壓 (右軸 棕色)
            # =========================================================
            fig.add_trace(go.Scattergl(x=df['Time'], y=df['Resi'], name=f"{lbl}-暴潮偏差", 
                                     mode='lines+markers',
                                     line=dict(color="#faafe4", width=1.2),
                                     marker=dict(size=2.5, opacity=0.6),
                                     connectgaps=True, legendgroup=f"g{idx}"), row=r_bot, col=1, secondary_y=False)
            
            # 添加正規化暴潮偏差（分別用全年和當月MR，都用淺粉紅色系，用線型區分）
            if 'Resi_Norm' in df.columns and mr_full and mr_full != 0:
                fig.add_trace(go.Scattergl(x=df['Time'], y=df['Resi'] / (mr_full * 1.0) * 100, 
                                         name=f"{lbl}-暴潮偏差(正規化%-全年MR)", 
                                         mode='lines+markers',
                                         line=dict(color='#f8bbd0', width=1, dash='dash'),  # 淺粉紅虛線
                                         marker=dict(size=2.5, opacity=0.6),
                                         connectgaps=True,
                                         visible='legendonly'),  # 預設隱藏
                                         row=r_bot, col=1, secondary_y=False)
            
            if 'Resi_Norm' in df.columns and mr_month and mr_month != 0:
                fig.add_trace(go.Scattergl(x=df['Time'], y=df['Resi'] / (mr_month * 1.0) * 100, 
                                         name=f"{lbl}-暴潮偏差(正規化%-當月MR)", 
                                         mode='lines+markers',
                                         line=dict(color='#f8bbd0', width=1, dash='dot'),  # 淺粉紅點線
                                         marker=dict(size=2.5, opacity=0.6),
                                         connectgaps=True,
                                         visible='legendonly'),  # 預設隱藏
                                         row=r_bot, col=1, secondary_y=False)
            
            if 'P' in df.columns:
                p_label = get_src_label(sids['p'], '氣壓')
                fig.add_trace(go.Scattergl(x=df['Time'], y=df['P'], name=p_label, 
                                         mode='lines+markers',
                                         line=dict(color='#8b4513', width=1, dash='dot'),
                                         marker=dict(size=2.5, opacity=0.6),
                                         connectgaps=True), 
                                         row=r_bot, col=1, secondary_y=True)

            # =========================================================
            # (1,2) 右上：風速/流速 (左軸 紫色系) + 氣溫/海溫 (右軸 紅色系)
            # =========================================================
            # A. 風速與風向 (左軸 紫色)
            if 'WS' in df.columns:
                grp_name = f"wind_{b['stid']}"
                w_label = get_src_label(sids['w'], '風速')
                fig.add_trace(go.Scattergl(
                    x=df['Time'], y=df['WS'], name=w_label, legendgroup=grp_name,
                    mode='lines+markers',
                    connectgaps=False, line=dict(color='#9467bd', width=1.2),
                    marker=dict(size=3, opacity=0.6)
                ), row=r_top, col=2, secondary_y=False)
                # 風速異常值紅叉
                if 'WS_raw' in df.columns:
                    ws_raw_mask = df['WS_raw'].notna()
                    if ws_raw_mask.any():
                        ws_customdata = df.loc[ws_raw_mask, 'WS_QC_raw'].fillna('?').values if 'WS_QC_raw' in df.columns else ['?'] * ws_raw_mask.sum()
                        fig.add_trace(go.Scattergl(
                            x=df.loc[ws_raw_mask, 'Time'], y=df.loc[ws_raw_mask, 'WS_raw'],
                            name=f"⚠️ 風速 原始值(QC≠Q)", legendgroup=grp_name,
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
                            marker=dict(symbol='arrow', size=10, color='#800080', angle=(arrow_df['WD'] + 180) % 360),
                        ), row=r_top, col=2, secondary_y=False)

            # B. 流速與流向 (左軸 紫色備用)
            if 'V' in df.columns:
                grp_name = f"curr_{b['stid']}"
                v_label = get_src_label(sids['wv'], '流速')
                fig.add_trace(go.Scattergl(
                    x=df['Time'], y=df['V'], name=v_label, legendgroup=grp_name,
                    mode='lines+markers',
                    connectgaps=True, line=dict(color='#c5b0d5', width=1, dash='dash'),
                    marker=dict(size=3, opacity=0.6),
                    visible='legendonly'    # 預設隱藏
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
                            visible='legendonly'    # 預設隱藏
                        ), row=r_top, col=2, secondary_y=False)

            # C. 溫度 (右軸 紅色系)
            if 'AT' in df.columns:
                at_label = get_src_label(sids['p'], '氣溫')
                fig.add_trace(go.Scattergl(x=df['Time'], y=df['AT'], name=at_label, 
                                         mode='lines+markers',
                                         line=dict(color="#ee7373", width=1.2),
                                         marker=dict(size=3, opacity=0.6),
                                         connectgaps=False), row=r_top, col=2, secondary_y=True)
                # 氣溫異常值紅叉
                if 'AT_raw' in df.columns:
                    at_raw_mask = df['AT_raw'].notna()
                    if at_raw_mask.any():
                        at_customdata = df.loc[at_raw_mask, 'AT_QC_raw'].fillna('?').values if 'AT_QC_raw' in df.columns else ['?'] * at_raw_mask.sum()
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
                fig.add_trace(go.Scattergl(x=df['Time'], y=df['WT'], name=wt_label, 
                                         mode='lines+markers',
                                         line=dict(color='#ff9896', width=1, dash='dash'),
                                         marker=dict(size=3, opacity=0.6),
                                         connectgaps=False,
                                         visible='legendonly'), row=r_top, col=2, secondary_y=True) # 預設隱藏
                # 海溫異常值紅叉
                if 'WT_raw' in df.columns:
                    wt_raw_mask = df['WT_raw'].notna()
                    if wt_raw_mask.any():
                        wt_customdata = df.loc[wt_raw_mask, 'WT_QC_raw'].fillna('?').values if 'WT_QC_raw' in df.columns else ['?'] * wt_raw_mask.sum()
                        fig.add_trace(go.Scattergl(
                            x=df.loc[wt_raw_mask, 'Time'], y=df.loc[wt_raw_mask, 'WT_raw'],
                            name="⚠️ 海溫 原始值(QC≠Q)",
                            mode='markers',
                            marker=dict(color='red', symbol='x', size=3, line=dict(width=0.8)),
                            customdata=wt_customdata,
                            hovertemplate='%{x}<br>海溫原始值: %{y}<br>QC代碼: %{customdata}<extra></extra>',
                            showlegend=True,
                            visible='legendonly' # 預設隱藏
                        ), row=r_top, col=2, secondary_y=True)

            # =========================================================
            # (2,2) 右下：波高 (左軸 深綠) + 週期 (右軸 淺綠)
            # =========================================================
            if 'H_m' in df.columns:
                h_label = get_src_label(sids['wv'], '示性波高(m)')
                fig.add_trace(go.Scattergl(x=df['Time'], y=df['H_m'], name=h_label, 
                                         mode='lines+markers',
                                         line=dict(color='#1b5e20', width=1.2),
                                         marker=dict(size=2.5, opacity=0.6),
                                         connectgaps=True), 
                                         row=r_bot, col=2, secondary_y=False)
            
            # 波浪週期
            if 'T_sec' in df.columns:
                t_label = get_src_label(sids['wv'], '平均週期(s)')
                fig.add_trace(go.Scattergl(x=df['Time'], y=df['T_sec'], name=t_label, 
                                         mode='lines+markers',
                                         line=dict(color='#81c784', width=1, dash='dot'),
                                         marker=dict(size=2.5, opacity=0.6),
                                         connectgaps=True), 
                                         row=r_bot, col=2, secondary_y=True)

            # --- 警示區塊 (Land Range) ---
            if land_range:
                for r_curr in [r_top, r_bot]:
                    for c_curr in [1, 2]:
                        fig.add_vrect(x0=land_range[0], x1=land_range[1], fillcolor="red", opacity=0.1, line_width=0, row=r_curr, col=c_curr)

            # --- 軸標題與設定 ---
            # 左上：水位
            fig.update_yaxes(title_text="水位(mm)", row=r_top, col=1, fixedrange=False)
            fig.update_yaxes(title_text="水位差值(mm)", secondary_y=True, row=r_top, col=1, fixedrange=False, showgrid=False) # 格線隱藏避免混亂
            # fig.update_yaxes(title_text="水位差值(mm)", secondary_y=True, row=r_bot, col=1, fixedrange=False, showgrid=False) # 格線隱藏避免混亂
            
            # 左下：偏差(左) / 氣壓(右)
            fig.update_yaxes(title_text="暴潮偏差(mm)", secondary_y=False, row=r_bot, col=1, fixedrange=False)
            fig.update_yaxes(title_text="氣壓(hPa)", secondary_y=True, row=r_bot, col=1, fixedrange=False, showgrid=False) # 格線隱藏避免混亂

            # 右上：速度(左) / 溫度(右)
            fig.update_yaxes(title_text="速度(m/s)", secondary_y=False, row=r_top, col=2, fixedrange=False)
            fig.update_yaxes(title_text="溫度(℃)", secondary_y=True, row=r_top, col=2, fixedrange=False, showgrid=False) # 格線隱藏避免混亂

            # 右下：波高(左) / 週期(右)
            fig.update_yaxes(title_text="示性波高(m)", secondary_y=False, row=r_bot, col=2, fixedrange=False)
            fig.update_yaxes(title_text="平均週期(s)", secondary_y=True, row=r_bot, col=2, fixedrange=False, showgrid=False) # 格線隱藏避免混亂

            # [滑桿設定]
            # ==========================================
            # 1. 上半部 (水位) - 顯示滑桿
            fig.update_xaxes(
                matches='x',
                rangeslider_visible=True,
                # rangeslider_thickness=slider_ratio, # 套用計算出的比例
                rangeslider=dict(
                    visible=True,
                    thickness=slider_ratio,
                    bgcolor="#333333",      # [關鍵] 底色改為深灰 (比畫布亮一點)
                    # bordercolor="#00BFFF",  # [關鍵] 邊框改為亮青色 (Cyberpunk 風格)
                    borderwidth=1,          # 邊框寬度
                ),
                row=r_top, col=1
            )
            
            # 2. 下半部 (偏差) - 顯示滑桿
            fig.update_xaxes(
                matches='x',
                rangeslider_visible=True,
                # rangeslider_thickness=slider_ratio, # 套用計算出的比例
                rangeslider=dict(
                    visible=True,
                    thickness=slider_ratio,
                    bgcolor="#333333",      # [關鍵] 底色改為深灰 (比畫布亮一點)
                    # bordercolor="#00BFFF",  # [關鍵] 邊框改為亮青色 (Cyberpunk 風格)
                    borderwidth=1,          # 邊框寬度
                ),
                row=r_bot, col=1
            )

            # 3. 右側圖表 - 關閉滑桿
            fig.update_xaxes(matches='x', rangeslider_visible=False, row=r_top, col=2)
            fig.update_xaxes(matches='x', rangeslider_visible=False, row=r_bot, col=2)
        

        # 在最後設定 Layout 的地方：
        fig.update_layout(
            # 調整成暗色系
            # [新增] 這行就是魔法！一鍵套用專業暗色主題
            template="plotly_dark", 
            # 配合暗色主題，稍微調整整張圖的背景色 (讓它跟 Plotly 的黑融合)
            paper_bgcolor="#1E1E1E", # 畫布外圍背景
            plot_bgcolor="#1E1E1E",  # 圖表繪圖區背景
            uirevision=True,         # [新增] 鎖定 UI 狀態：點擊圖例時保留目前的縮放/平移視圖，不自動重置
            font=dict(
                family=f"{UI_FONT}, Arial, sans-serif",
                size=12,         # 預設字體大小
                color="#E0E0E0"  # 字體顏色 (配合暗色底)
            ),

            # title=dict(text="海洋動力診斷儀表板", x=0.5),  #太佔空間了先拿掉
            height=total_height,  # <--- 套用算好的總高度
            hovermode='x unified',  # 十字準星模式
            hoverlabel=dict(namelength=-1),  # 完整顯示名稱
            margin=dict(l=50, r=300, t=50, b=50), # 右邊留300px給浮動圖例
            autosize=True  # 自適應寬度

        )
        # fig.show(config={'displayModeBar': True, 'scrollZoom': False})
        # 定義要移除的按鈕清單
        remove_buttons = [
            # 'select2d',        # 矩形選取 (Box Select) → 現在要用來框選產出SQL指令
            'lasso2d',         # 套索選取 (Lasso Select)
            'zoomIn2d',        # 放大 (點擊)
            'zoomOut2d',       # 縮小 (點擊)
            'autoScale2d',     # 自動縮放 (有時會破壞版面)
            'hoverClosestCartesian', # 這些看個人喜好，保留也沒關係
            'hoverCompareCartesian'
        ]
        from plotly_qc_select import write_chart_html
        import webbrowser
        path = write_chart_html(fig, config={
            'displayModeBar': True,
            'scrollZoom': False,
            'doubleClick': 'reset',
            'modeBarButtonsToRemove': remove_buttons,
            'displaylogo': False
        })
        webbrowser.open(f"file://{path}")

def draw_water_only(bundles, land_range=None):
    """彈出一個僅包含水位與儀器差值的大圖表視窗 (修復版)"""
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go
    
    # 這裡我們不分頁，直接在一個網頁顯示所有選中測站的大水位圖
    fig = make_subplots(rows=len(bundles), cols=1, shared_xaxes=True,
                          vertical_spacing=0.05,
                          subplot_titles=[f"{b['stname']}({b['stid']}) - 水位細節診斷" for b in bundles],
                          specs=[[{"secondary_y": True}] for _ in range(len(bundles))])

    for idx, b in enumerate(bundles):
        row = idx + 1
        df = b['df']
        tide_meta = b.get('tide_meta', {}) # 取得水位儀器資訊
        
        # 1. 繪製所有觀測水位（校正值藍線 + 原始機測值紅叉）
        type_colors = {2: '#1f77b4', 3: '#0d47a1', 4: '#64b5f6'} 

        # [DEBUG] 確認到了繪圖端 df 的欄位結構
        print(f"[DEBUG] draw_water_only bundle={b['stid']} df 欄位: {df.columns.tolist()}")

        for stid_wl, meta in sorted(tide_meta.items()):
            col_name     = f'WL_{stid_wl}'       # 校正值（QC=Q）
            raw_col_name = f'WL_{stid_wl}_raw'   # 原始機測值（QC≠Q）
            qc_raw_name  = f'QC_{stid_wl}_raw'   # 原始值的 QC 代碼（用於 hover）

            # [DEBUG] 確認每個儀器的欄位是否存在
            print(f"[DEBUG] stid_wl={stid_wl}: 校正值欄位存在={col_name in df.columns}, 原始值欄位存在={raw_col_name in df.columns}")

            if col_name not in df.columns:
                continue

            label      = f"{meta['stnac']}({stid_wl})-{meta['type_desc']}{'(主)' if meta['is_primary'] else ''}"
            color      = type_colors.get(meta['type'], 'gray')
            dash_style = 'solid' if meta['is_primary'] else 'dash'

            # --- Trace 1：校正值（QC=Q），connectgaps=False 讓異常區間自動斷線不補線 ---
            q_mask = df[col_name].notna()
            fig.add_trace(go.Scattergl(
                x=df.loc[q_mask, 'Time'], y=df.loc[q_mask, col_name],
                name=label,
                mode='lines+markers',
                line=dict(color=color, dash=dash_style, width=1.2),
                marker=dict(size=3, opacity=0.6),
                connectgaps=False
            ), row=row, col=1, secondary_y=False)
            # 低頻趨勢線（25h 移動平均）    
            lf_col = f'WL_{stid_wl}_lf'     # 這個欄位在前面只針對主測站計算，未來想全部儀器都看的話改前面函式就能兼容
            if lf_col in df.columns and df[lf_col].notna().any():
                fig.add_trace(go.Scattergl(
                    x=df['Time'], y=df[lf_col],
                    name=f"{meta['stnac']}({stid_wl})-水位低頻趨勢(25h-MA)",
                    mode='lines',
                    line=dict(color='rgba(180,180,180,0.55)', width=1.2),
                    connectgaps=False,
                    visible='legendonly'          # 預設隱藏
                ), row=row, col=1, secondary_y=False)
            # 新增：EWMA 線，預設隱藏，點圖例才顯示
            ew_col = f'WL_{stid_wl}_ewma'
            if ew_col in df.columns and df[ew_col].notna().any():
                fig.add_trace(go.Scattergl(
                    x=df['Time'], y=df[ew_col],
                    name=f"{meta['stnac']}({stid_wl})-EWMA(α=0.05",
                    mode='lines',
                    line=dict(color='rgba(255,200,100,0.7)', width=1.2),
                    connectgaps=True,
                    visible='legendonly'          # 預設隱藏
                ), row=row, col=1, secondary_y=False)

            # --- Trace 2：原始機測值（QC≠Q），紅叉，hover 顯示 QC 代碼 ---
            if raw_col_name in df.columns:
                raw_mask = df[raw_col_name].notna()
                if raw_mask.any():
                    print(f"[DEBUG] stid_wl={stid_wl} 原始機測值點數: {raw_mask.sum()}")
                    # 準備 hover 用的 QC 代碼（若欄位不存在則填 '?'）
                    if qc_raw_name in df.columns:
                        customdata = df.loc[raw_mask, qc_raw_name].fillna('?').values
                    else:
                        customdata = ['?' ] * raw_mask.sum()
                    fig.add_trace(go.Scattergl(
                        x=df.loc[raw_mask, 'Time'],
                        y=df.loc[raw_mask, raw_col_name],
                        name=f"⚠️ {stid_wl} 原始值(QC≠Q)",
                        mode='markers',
                        marker=dict(color='red', symbol='x', size=5, line=dict(width=0.8)),
                        customdata=customdata,
                        hovertemplate='%{x}<br>原始值: %{y}<br>QC代碼: %{customdata}<extra></extra>',
                        showlegend=True
                    ), row=row, col=1, secondary_y=False)

                # [新增] 繪製平滑後的一小時輔助線
                temp_df = df[['Time', col_name]].dropna().set_index('Time')
                if not temp_df.empty:
                    # [修改] 同時計算平均值與標準差 (std)
                    smoothed_df = temp_df.resample('1H').agg(['mean', 'std'])
                    smoothed_df.columns = ['mean', 'std'] # 簡化欄位名稱
                    smoothed_df = smoothed_df.reset_index()

                    # [修正] 將時間戳記往後推 30 分鐘 (置中)，解決視覺上的相位延遲 (Phase Lag)
                    smoothed_df['Time'] += pd.Timedelta(minutes=30)
                    
                    smooth_label = f"{label}-平滑(1H)"
                    fig.add_trace(go.Scattergl(
                        x=smoothed_df['Time'], y=smoothed_df['mean'],
                        name=smooth_label,
                        mode='lines',
                        line=dict(color=color, width=1, dash='solid'),
                        error_y=dict(
                            type='data',
                            array=smoothed_df['std'], # 設定標準差為誤差棒
                            visible=True,
                            color=color,
                            thickness=0.5, # 誤差棒線條細一點，避免喧賓奪主
                            width=2        # 誤差棒頂端寬度 (Caps)
                        ),
                        connectgaps=False,
                        visible='legendonly', # 預設隱藏
                        opacity=0.8
                    ), row=row, col=1, secondary_y=False)

        # 2. 繪製預報水位 (僅主測站)
        primary_stids = [st for st, meta in tide_meta.items() if meta['is_primary']]
        if primary_stids:
            p_stid = primary_stids[0]
            # 繪製 QC='h' 天文潮預報 (調和預報)
            pred_col_h = f'WL_{p_stid}_pred_h'
            if pred_col_h in df.columns:
                fig.add_trace(go.Scattergl(
                    x=df['Time'], y=df[pred_col_h], 
                    name=f"{tide_meta[p_stid]['stnac']}({p_stid})-預報(h)",
                    mode='lines+markers',
                    line=dict(color='#2ca02c', dash='dot', width=1.2), # 綠色
                    marker=dict(size=2.5, opacity=0.6),
                    connectgaps=True
                ), row=row, col=1, secondary_y=False)
            
            # 繪製 QC='a' 天文潮重建水位 (調和分析)
            pred_col_a = f'WL_{p_stid}_pred_a'
            if pred_col_a in df.columns:
                fig.add_trace(go.Scattergl(
                    x=df['Time'], y=df[pred_col_a], 
                    name=f"{tide_meta[p_stid]['stnac']}({p_stid})-預報(a)",
                    mode='lines+markers',
                    line=dict(color='#98df8a', dash='dot', width=1.2), # 淺綠色
                    marker=dict(size=2.5, opacity=0.6),
                    connectgaps=True,
                    visible='legendonly' # 預設隱藏
                ), row=row, col=1, secondary_y=False)

        # 3. 繪製儀器差值 (右軸)
        # diff_colors = ['#ff7f0e', "#d84315", '#ffb74d']  # 深橘 -> 中橘 -> 淺橘
        diff_colors = ['#ff7f0e', "#e377c2", '#17becf'] # 高對比色系
        diff_idx = 0
        for col in df.columns:
            if col.startswith('Diff_'):
                color = diff_colors[diff_idx % len(diff_colors)]
                fig.add_trace(go.Scattergl(
                    x=df['Time'], y=df[col], 
                    name=f"差值: {col.replace('Diff_', '')}",
                    mode='markers', 
                    marker=dict(size=3, opacity=0.5, color=color)), 
                    row=row, col=1, secondary_y=True)
                diff_idx += 1

        # 4. 颱風警報區塊 (背景)
        if land_range:
            fig.add_vrect(x0=land_range[0], x1=land_range[1], fillcolor="Red", 
                          opacity=0.1, layer="below", line_width=0, row=row, col=1)

        # 軸標題設定
        fig.update_yaxes(title_text="水位(mm)", row=row, col=1, secondary_y=False, fixedrange=False)
        fig.update_yaxes(title_text="水位差值(mm)", row=row, col=1, secondary_y=True, showgrid=False, fixedrange=False)

    fig.update_layout(
        template="plotly_dark",
        height=600 * len(bundles), # 加大高度讓細節更清楚
        hovermode="x unified",
        font=dict(family=UI_FONT, size=12),
        uirevision=True # [新增] 鎖定 UI 狀態：點擊圖例時保留目前的縮放/平移視圖
    )
    
    # 顯示滑桿設定 (同步您的深色配色)
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.05, bgcolor="#333333"))
    
    from plotly_qc_select import write_chart_html
    import webbrowser
    # 第二個（有 toImageButtonOptions）
    path = write_chart_html(fig, config={
        'displayModeBar': True,
        'displaylogo': False,
        'doubleClick': 'reset',
        'toImageButtonOptions': {
            'format': 'png',
            'filename': '水位細節診斷大圖',
            'scale': 1
        }
    })
    webbrowser.open(f"file://{path}")


class LoginWin:
    def __init__(self):
        self.root = tk.Tk(); self.root.title("系統登入"); self.root.geometry("330x230")
        # self.root.option_add('*Font', 'UI_FONT 12') #這是原本claude寫的，gemini code一直建議改成下面那行
        self.root.option_add('*Font', f'{UI_FONT} 12')
        tk.Label(self.root, text="海洋動力診斷儀表板", font=(UI_FONT, 14, "bold")).pack(pady=(10, 5))
        
        # 新增切換選項：本地模式
        self.is_local = tk.BooleanVar(value=False)
        self.cb_local = tk.Checkbutton(self.root, text="切換至 Localhost 測試模式", 
                                       variable=self.is_local, font=(UI_FONT, 10), fg="blue")
        self.cb_local.pack()

        self.info_label = tk.Label(self.root, text=f"預設連線: {DB_IP} ({DB_USER})", 
                                   fg="#666", font=(UI_FONT, 9))
        self.info_label.pack()

        # self.pw = tk.Entry(self.root, show="*"); self.pw.pack(pady=10); self.pw.focus_set() ## 星號視覺上有點過時
        self.pw = tk.Entry(self.root, show="●", font=("Arial", 12)); self.pw.pack(pady=10); self.pw.focus_set()
        self.pw.bind('<Return>', lambda e: self.login())
        tk.Button(self.root, text="登入系統", width=15, bg="#007bff", fg="white", command=self.login).pack(pady=10)
        self.e = None; self.root.mainloop()

    def login(self):
        # 根據是否勾選「本地模式」決定連線參數
        if self.is_local.get():
            h, u, db = "127.0.0.1", "root", "test_db"
            # 關鍵修改：定義測試環境的特殊資料表名稱
            tbls = {'st': 'st_all', 'tide6': 'tide_wide_test'}
        else:
            h, u, db = DB_IP, DB_USER, "mrbank"
            tbls = None
            
        try: self.e = OceanDataEngine(self.pw.get(), host=h, user=u, database=db, tables=tbls); self.root.destroy()
        # except Exception as ex: messagebox.showerror("失敗", str(ex))
        except Exception as ex:
            import traceback
            with open("error_log.txt", "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)
            messagebox.showerror("失敗", str(ex))

# ... (MainApp 與入口代碼維持不變) ...
if __name__ == "__main__":
    try:
        ln = LoginWin()
        if ln.e:
            from tkinter import ttk
            from tkcalendar import DateEntry
            # 這裡需要 MainApp 的完整定義，為了節省長度，請沿用 v3.2 的 MainApp
            class MainApp:
                def __init__(self, engine):
                    self.e = engine

                    # 在 MainApp.init 裡啟動 Dash server
                    import threading, time
                    from dash_app import app as dash_app
                    t = threading.Thread(
                        target=dash_app.run,
                        kwargs={"host": "127.0.0.1", "port": DASH_PORT,
                                "debug": False, "use_reloader": False},
                        daemon=True,
                    )
                    t.start()
                    time.sleep(1.5)

                    self.root = tk.Tk(); self.root.title("海洋動力診斷系統 v5.0"); self.root.geometry("650x850")
                    # self.root.option_add('*Font', 'UI_FONT 14') #這是原本claude寫的，gemini code一直建議改成下面那行
                    self.root.option_add('*Font', f'{UI_FONT} 14')
                    self.root.option_add('*TCombobox*Listbox.font', (UI_FONT, 12)) # 確保下拉選單彈出的清單字體統一
                    
                    # ← 屬性要先定義，build_ui() 才能用到它們
                    self._last_stid = ""    # ← 新增：記錄最後一次畫圖的站台
                    self._sql_dlg = None    # ← 新增：SQL 視窗的參考
                    self._qc_mode   = tk.StringVar(value="1")   # "1"=QC更新, "2"=MIN運算
                    self._new_qc    = tk.IntVar(value=9)
                    self._op        = tk.StringVar(value="+")
                    self._operand   = tk.DoubleVar(value=0.0)

                    self.build_ui() # ← 移到後面
                    threading.Thread(target=_start_callback_server, daemon=True).start()
                    self.root.after(200, self._poll_queue)   # ← 注意是 self.root.after
                

                def _poll_queue(self):
                    try:
                        while True:
                            data = _selection_queue.get_nowait()
                            self._on_selection(data)
                    except queue.Empty:
                        pass
                    except Exception as e:
                        print(f"[_poll_queue 錯誤] {e}")   # 先印出來看是什麼問題
                    self.root.after(200, self._poll_queue)

                def _on_selection(self, data: dict):
                    from plotly_qc_select import (build_mode1_sql, build_mode2_sql_by_time,
                                                SqlDialog, _clean_ts)
                    stid = self._last_stid or "UNKNOWN"
                    cnt  = data.get("point_count", 0)

                    if self._qc_mode.get() == "1":
                        sql  = build_mode1_sql(data, stid, self._new_qc.get())
                        info = (f"Mode 1 | {cnt} 點 | "
                                f"{_clean_ts(data.get('x_start'))} ~ {_clean_ts(data.get('x_end'))}")
                    else:
                        sql  = build_mode2_sql_by_time(data, stid,
                                                        self._op.get(), self._operand.get())
                        info = (f"Mode 2 | {cnt} 點 | "
                                f"原值 {self._op.get()} {self._operand.get()}")

                    if self._sql_dlg is None or not self._sql_dlg.winfo_exists():
                        self._sql_dlg = SqlDialog(self.root)
                    self._sql_dlg.update_content(sql, info)

                def select_by_agency(self, target_agency, exclude=False):
                    """根據業務單位自動勾選測站，支援排除模式"""
                    # 先清除目前所有選取
                    self.lb.selection_clear(0, tk.END)
                    # 遍歷 mapping_df 找出符合單位的測站索引
                    for i, r in self.e.mapping_df.iterrows():
                        sid = str(r['STID']).strip()
                        agency = self.e.sponsor_map.get(sid, "未知")
                        is_target = target_agency in agency
                        # 如果 (排除模式 且 不包含關鍵字) OR (包含模式 且 包含關鍵字)
                        if (exclude and not is_target) or (not exclude and is_target):
                            self.lb.select_set(i)

                def filter_active_stations(self):
                    """查詢資料庫，僅保留目前所選時間範圍內有資料的測站"""
                    try:
                        self.root.config(cursor="watch"); self.root.update()
                        s = self.sd.get_date().strftime('%Y-%m-%d 00:00:00')
                        e = self.ed.get_date().strftime('%Y-%m-%d 23:59:59')
                        # 查詢 tide6 找出該時段有產出資料的 STID 清單
                        query = f"SELECT DISTINCT STID FROM {self.e.tables['tide6']} WHERE DATATIME BETWEEN '{s}' AND '{e}'"
                        active_stids = set(pd.read_sql(query, self.e.conn)['STID'].astype(str).str.strip())
                        
                        self.lb.delete(0, tk.END)
                        count = 0
                        for _, r in self.e.mapping_df.iterrows():
                            sid = str(r['STID']).strip()
                            if sid in active_stids:
                                count += 1
                                stn, cty, agy = self.e.name_map.get(sid, "未知"), self.e.conty_map.get(sid, "未知"), self.e.sponsor_map.get(sid, "未知")
                                self.lb.insert(tk.END, f"{count:2d}. [{sid:<6}] {stn} ({cty})({agy})")
                        if count == 0: messagebox.showinfo("篩選結果", "所選時段內資料庫完全沒有任何測站的資料。")
                    except Exception as ex: messagebox.showerror("錯誤", f"篩選失敗：{ex}")
                    finally: self.root.config(cursor="")

                def reset_station_list(self):
                    """還原顯示所有原始測站清單"""
                    self.lb.delete(0, tk.END)
                    for i, r in self.e.mapping_df.iterrows():
                        sid = str(r['STID']).strip()
                        stn, cty, agy = self.e.name_map.get(sid, "未知"), self.e.conty_map.get(sid, "未知"), self.e.sponsor_map.get(sid, "未知")
                        self.lb.insert(tk.END, f"{i+1:2d}. [{sid:<6}] {stn} ({cty})({agy})")

                def build_ui(self):
                    # 原本的白色底設定（有點懶得改，先不動）
                    tk.Label(self.root, text=f"連線伺服器: {self.e.host} | 帳號: {self.e.user}", fg="#666", font=(UI_FONT, 10)).pack(pady=5)
                    
                    is_local = (self.e.host == "127.0.0.1")
                    f1 = tk.LabelFrame(self.root, text="1. 颱風與日期" + (" (測試模式停用颱風)" if is_local else "(颱風非必選)"), font=(UI_FONT), padx=10, pady=5); f1.pack(fill="x", padx=20)
                    
                    years = self.e.fetch_years() if not is_local else []
                    self.yr_cb = ttk.Combobox(f1, values=years, state="readonly" if not is_local else "disabled", width=12, font=(UI_FONT)); self.yr_cb.pack(side="left")
                    if not is_local: self.yr_cb.bind("<<ComboboxSelected>>", self.on_yr)
                    
                    self.ty_cb = ttk.Combobox(f1, state="readonly" if not is_local else "disabled", width=25, font=(UI_FONT)); self.ty_cb.pack(side="left", padx=10)
                    if not is_local: self.ty_cb.bind("<<ComboboxSelected>>", self.on_ty)
                    if is_local: tk.Label(f1, text="本地連線中，請手動選擇日期", fg="blue", font=(UI_FONT, 9)).pack(side="left")

                    # 1. 起始時間行 (建立一個 Frame 橫向排列)
                    f_sd = tk.Frame(self.root)
                    f_sd.pack(pady=5) # 這一行的垂直間距
                    tk.Label(f_sd, text="起始時間:", font=(UI_FONT)).pack(side="left")
                    self.sd = DateEntry(f_sd, width=15, date_pattern='yyyy-mm-dd', font=(UI_FONT))
                    self.sd.pack(side="left", padx=5) # padx 讓文字跟輸入框有點距離
                    # 2. 結束時間行
                    f_ed = tk.Frame(self.root)
                    f_ed.pack(pady=5)
                    tk.Label(f_ed, text="結束時間:", font=(UI_FONT)).pack(side="left")
                    self.ed = DateEntry(f_ed, width=15, date_pattern='yyyy-mm-dd', font=(UI_FONT))
                    self.ed.pack(side="left", padx=5)

                    tk.Label(self.root, text="海洋參數模式下，建議查詢範圍不超過 45 天，每次建議選取 12 站以內。", fg="red", font=(UI_FONT, 10)).pack(pady=(0, 5))
                    tk.Label(self.root, text="水位細節模式下，建議查詢範圍不超過 365 天，每次建議選取 45 站以內。", fg="red", font=(UI_FONT, 10)).pack(pady=(0, 5))

                    # --- [修改] 全選/取消按鈕區塊 ---
                    # 1. 建立一個容器，但不讓它過度撐開
                    btn_frame = tk.Frame(self.root)
                    btn_frame.pack(fill="x", padx=20, pady=2) 
                    # 2. 全選按鈕
                    # width=6: 設定寬度為 6 個字元 (小巧)
                    # relief="raised": 設定為凸起樣式 (看起來像立體按鈕)
                    # bd=2: 邊框厚度
                    # bg="#e1e1e1": 淺灰色背景，增加辨識度
                    tk.Button(btn_frame, text="全部選取", font=(UI_FONT, 10), width=8, 
                            bg="#e1e1e1", bd=2, relief="raised",
                            command=lambda: self.lb.select_set(0, tk.END)).pack(side="left", padx=(0, 5)) 
                            # padx=(0, 5) 意思是右邊留 5px 的縫隙給下一個按鈕
                    
                    # 3. 取消全選按鈕
                    tk.Button(btn_frame, text="全部取消", font=(UI_FONT, 10), width=8, 
                            bg="#e1e1e1", bd=2, relief="raised",
                            command=lambda: self.lb.selection_clear(0, tk.END)).pack(side="left", padx=(0, 5))
                    # # --- [修改結束] 全選/取消按鈕區塊 ---
                    # --- 將功能性按鈕靠右對齊，注意 pack 順序會決定從右往左的排列 ---
                    # 4. [新增]選氣象署所有測站按鈕
                    tk.Button(btn_frame, text="選氣象署", font=(UI_FONT, 10), width=8, 
                            bg="#e1e1e1", bd=2, relief="raised",
                            command=lambda: self.select_by_agency("中央氣象署")).pack(side="left", padx=(0, 5))
                    # 5. [新增]選非氣象署所有測站按鈕
                    tk.Button(btn_frame, text="非氣象署", font=(UI_FONT, 10), width=8, 
                            bg="#e1e1e1", bd=2, relief="raised",
                            command=lambda: self.select_by_agency("中央氣象署", exclude=True)).pack(side="left", padx=(0, 5))
                    tk.Button(btn_frame, text="重設清單", font=(UI_FONT, 10), width=8, 
                            bg="#d1dfe7", bd=2, relief="raised",
                            command=self.reset_station_list).pack(side="right", padx=(5, 0))
                    tk.Button(btn_frame, text="只列有資料站", font=(UI_FONT, 10), width=12, 
                            bg="#d1e7dd", bd=2, relief="raised",
                            command=self.filter_active_stations).pack(side="right", padx=(5, 0))

                            

                    self.lb = tk.Listbox(self.root, selectmode="multiple", font=(UI_FONT, 10)) ##測站清單原本字體是Courier New，比較能對齊
                    for i, r in self.e.mapping_df.iterrows():
                        # stn = self.e.name_map.get(str(r['STID']).strip(), "未知")
                        # self.lb.insert(tk.END, f"{i+1:2d}. [{r['STID']:<6}] {stn}")
                        sid = str(r['STID']).strip()
                        stn = self.e.name_map.get(sid, "未知")
                        cty = self.e.conty_map.get(sid, "未知")
                        agency = self.e.sponsor_map.get(sid, "未知")
                        
                        # [修正] 組合顯示字串，顯示格式如： 1. [1176  ] 基隆 (基隆市)
                        display_text = f"{i+1:2d}. [{sid:<6}] {stn} ({cty})({agency})" #（注意這裡的括號是全形的，確保在等寬字體下對齊）
                        self.lb.insert(tk.END, display_text)
                    self.lb.pack(fill="both", expand=True, padx=20)
                    # tk.Button(self.root, text="生成診斷報表", bg="#28a745", fg="#FFFFFF", font=("UI_FONT", 10, "bold"), command=self.go).pack(pady=10) #字體本來是Arial

                    # ── QC 操作設定框 ──────────────────────────────
                    f_qc = tk.LabelFrame(self.root, text="框選操作設定", font=(UI_FONT), padx=8, pady=5)
                    f_qc.pack(fill="x", padx=20, pady=(0, 5))

                    # Mode 選擇
                    tk.Radiobutton(f_qc, text="Mode 1：更新 QC 值", font=(UI_FONT),
                                variable=self._qc_mode, value="1").grid(row=0, column=0, sticky="w")
                    tk.Spinbox(f_qc, textvariable=self._new_qc, from_=0, to=9,
                            width=4, font=(UI_FONT)).grid(row=0, column=1, sticky="w", padx=(4, 0))

                    tk.Radiobutton(f_qc, text="Mode 2：MIN 四則運算", font=(UI_FONT),
                                variable=self._qc_mode, value="2").grid(row=1, column=0, sticky="w")
                    ttk.Combobox(f_qc, textvariable=self._op,
                                values=["+", "-", "*", "/"], width=3,
                                state="readonly").grid(row=1, column=1, sticky="w", padx=(4, 0))
                    tk.Entry(f_qc, textvariable=self._operand,
                            width=8).grid(row=1, column=2, sticky="w", padx=(4, 0))

                    # === 執行按鈕區 (改用 Frame 包起來放兩顆) ===
                    btn_box = tk.Frame(self.root)
                    btn_box.pack(pady=10)
                    # tk.Button(btn_box, text="查看海洋參數", bg="#28a745", fg="#FFFFFF", font=("UI_FONT", 10, "bold"),  
                    #         command=self.go).pack(side="left", padx=5)
                    # tk.Button(btn_box, text="查看統計", bg="#17a2b8", fg="#FFFFFF", font=("UI_FONT", 10, "bold"),  
                    #         command=self.show_stats).pack(side="left", padx=5)

                    ## 加入查看水位細節按鈕，用不同mode呼叫go函式
                    tk.Button(btn_box, text=" 🔍 查看水位細節", bg="#3a17b8", fg="#FFFFFF", font=(UI_FONT, 10, "bold"),
                  command=lambda: self.go(mode="water")).pack(side="left", padx=10)
                    # [修改] 使用 lambda 傳遞 mode="full"
                    tk.Button(btn_box, text="查看海洋參數", bg="#28a745", fg="#FFFFFF", font=(UI_FONT, 10, "bold"),  
                            command=lambda: self.go(mode="full")).pack(side="left", padx=5)
                    tk.Button(btn_box, text="查看統計", bg="#17a2b8", fg="#FFFFFF", font=(UI_FONT, 10, "bold"),  
                            command=self.show_stats).pack(side="left", padx=5)

                def on_yr(self, e):
                    self.ty_df = self.e.fetch_typhoons(self.yr_cb.get())
                    self.ty_cb['values'] = [f"{r['cname']}({r['id']})" for _, r in self.ty_df.iterrows()]; self.ty_cb.set('')

                def on_ty(self, e):
                    idx = self.ty_cb.current()
                    if idx >= 0:
                        row = self.ty_df.iloc[idx]
                        self.sd.set_date(row['warnSeaBeg'] - datetime.timedelta(hours=24))
                        self.ed.set_date(row['warnSeaEnd'] + datetime.timedelta(hours=24))
                        self.lr = (row['warnLandBeg'], row['warnLandEnd']) if row['warnLandBeg'] else None

                # [修改] 加上 mode 參數，預設為 "full"
                def go(self, mode="full"):
                    # 1. 取得使用者輸入
                    start_date = self.sd.get_date()
                    end_date = self.ed.get_date()
                    
                    # 2. [防呆] 檢查日期順序
                    if start_date > end_date:
                        messagebox.showerror("日期錯誤", "「起始日期」不能晚於「結束日期」！\n請重新選擇。")
                        return

                    # 3. [防呆] 檢查時間跨度 (依據模式給予不同限制)
                    # 水位細節模式可以看長一點(365天)，全參數模式限制(45天)
                    LIMIT_DAYS = 365 if mode == "water" else 45 
                    delta = end_date - start_date
                    if delta.days > LIMIT_DAYS:
                        messagebox.showwarning("範圍過大", f"此模式建議時間範圍為 {LIMIT_DAYS} 天以內。\n請縮小範圍。")
                        return

                    # 4. [UX] 檢查是否選取了測站
                    selections = self.lb.curselection()
                    if not selections:
                        messagebox.showwarning("未選取測站", "請至少從清單中選擇一個測站！")
                        return
                    
                    # 5. [防呆] 檢查測站數量
                    LIMIT_STATIONS = 45 if mode == "water" else 12
                    if len(selections) > LIMIT_STATIONS:
                        messagebox.showwarning("選取過多", f"為了效能考量，建議一次不超過 {LIMIT_STATIONS} 個測站。")
                        return

                    # 6. 開始執行
                    self.root.config(cursor="watch"); self.root.update()
                    
                    try:
                        stids = [self.lb.get(i).split('[')[1].split(']')[0].strip() for i in selections]
                        self._last_stid = stids[0] if stids else ""    # ← 新增這行
                        bundles = [self.e.fetch_bundle(s, start_date, end_date) for s in stids]
                        
                        self.latest_bundles = bundles 
                        
                        # 智慧過濾「幽靈颱風警報區」
                        current_lr = getattr(self, 'lr', None)
                        if current_lr:
                            lr_s = pd.to_datetime(current_lr[0])
                            lr_e = pd.to_datetime(current_lr[1])
                            user_s = pd.to_datetime(start_date)
                            user_e = pd.to_datetime(end_date)
                            
                            if lr_e < user_s or lr_s > user_e:
                                current_lr = None

                        # ==========================================
                        # [核心精華] 根據傳入的 mode，決定呼叫哪個畫布！
                        # ==========================================
                        if mode == "water":
                            import time
                            key = f"{stids[0]}_{time.time()}"
                            dash_bridge.set_bundle(key, bundles, land_range=current_lr)
                            # print(f"[go] current_lr={current_lr}")      # ← 加這行
                            webbrowser.open(f"http://127.0.0.1:{DASH_PORT}")
                        else:
                            draw_diagnostic(bundles, current_lr)
                        
                    except Exception as err:
                        messagebox.showerror("執行錯誤", f"資料讀取或繪圖失敗：\n{err}")
                    finally:
                        self.root.config(cursor="")

                def show_stats(self):
                    if not hasattr(self, 'latest_bundles') or not self.latest_bundles:
                        messagebox.showinfo("提示", "請先執行「查看海洋參數」，才能計算統計數據！")
                        return

                    # 1. 建立視窗
                    top = tk.Toplevel(self.root)
                    top.title("統計數據摘要")
                    top.geometry("500x600") # 稍微加大一點
                    
                    # [設定] AI 使用 Courier New 讓數字對齊，但好醜，還是改掉好了。
                    txt = tk.Text(top, font=(UI_FONT, 12), padx=15, pady=15)
                    txt.pack(fill="both", expand=True)

                    # [設定] 定義標題的樣式 (藍色、粗體、UI_FONT)
                    # 這裡示範：雖然內文用 Courier New，但標題我想用UI_FONT
                    txt.tag_config("header", foreground="blue", font=(UI_FONT, 12, "bold"), spacing1=10)
                    txt.tag_config("norm", foreground="black")

                    # 2. 計算並寫入
                    for b in self.latest_bundles:
                        df = b['df']
                        name = b['stname']
                        
                        # 使用 "header" 樣式插入標題
                        txt.insert("end", f"【{name}】\n", "header")
                        
                        msg = ""
                        # 輔助函數：左對齊參數名稱到8字寬
                        def param_label(name: str) -> str:
                            return name.ljust(8)
                        
                        # 水位
                        if 'Obs' in df.columns:
                            msg += f"  {param_label('水位')} | 平均 {df['Obs'].mean():>7.2f}  最高 {df['Obs'].max():>7.2f}  最低 {df['Obs'].min():>7.2f} (cm)\n"
                        
                        # 風速
                        if 'WS' in df.columns:
                            msg += f"  {param_label('風速')} | 平均 {df['WS'].mean():>7.1f}  最大 {df['WS'].max():>7.1f} (m/s)\n"

                        if 'H_m' in df.columns:
                            msg += f"  {param_label('示性波高')} | 平均 {df['H_m'].mean():>7.2f}  最大 {df['H_m'].max():>7.2f} (m)\n"

                        # 流速
                        if 'V' in df.columns:
                            msg += f"  {param_label('流速')} | 平均 {df['V'].mean():>7.1f} | 最大 {df['V'].max():>7.1f} (cm/s)\n"
                            
                        msg += "-"*45 + "\n"
                        
                        # 使用 "norm" 一般樣式插入數據
                        txt.insert("end", msg, "norm")

                    txt.config(state="disabled") # 鎖定，不讓使用者修改內容
            
            app = MainApp(ln.e)
            app.root.mainloop()
    except Exception as final_err:
        tk.Tk().withdraw(); messagebox.showerror("異常", str(final_err))