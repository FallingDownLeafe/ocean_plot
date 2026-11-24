"""
main_app.py
===========
Tkinter 主介面（V2）：登入 → 選站查詢 → 推資料到 Dash → 開瀏覽器。

類別：
  LoginApp  —— IP / 帳密 → 建立 OceanDataEngine → 啟動 Dash thread
  MainApp   —— 颱風選單 / 日期範圍 / 測站列表 / 查詢按鈕

技術限制（不得更動）：
  - 不使用 python-dotenv，用 _load_env() 手動解析 .env
  - mysql.connector（OceanDataEngine 內部），不用 SQLAlchemy
  - Dash 啟動參數：debug=False, use_reloader=False
  - 字體：標楷體
"""

import os
import threading
import webbrowser
from datetime import date, datetime

import tkinter as tk
from tkinter import ttk, messagebox

try:
    from tkcalendar import DateEntry
    _HAS_CAL = True
except ImportError:
    _HAS_CAL = False
    # 若尚未安裝：pip install tkcalendar

import dash_bridge
import dash_app as dash_app_module
from data_engine import OceanDataEngine
from typhoon_bands import normalize_typhoon_info, warn_ranges, query_end_exclusive
from station_registry import KIND_TIDE, KIND_BUOY


# ── .env 手動解析（不使用 python-dotenv）──────────────────────────────────
def _load_env(path: str = '.env') -> None:
    """
    讀取 .env 並以 os.environ.setdefault() 填入環境變數。
    已有值的鍵不覆蓋（允許系統環境變數優先）。

    搜尋順序：
      1. 本腳本所在目錄（IDE 執行時 cwd 不一定是腳本目錄）
      2. 當前工作目錄 fallback
    """
    import pathlib
    p = pathlib.Path(__file__).parent / path   # 優先：腳本同目錄
    if not p.exists():
        p = pathlib.Path(path)                 # fallback：cwd
    if not p.exists():
        return
    with open(p, encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))


_load_env()


# ── UI 常數 ───────────────────────────────────────────────────────────────
_F  = ('標楷體', 12)
_FS = ('標楷體', 11)

_BG     = '#111820'
_PANEL  = '#1E2A3A'
_HEADER = '#1A3A5C'
_TEXT   = '#CCD0D4'
_ACCENT = '#4A9EBF'
_ENTRY  = '#1E1E1E'
_ERR    = '#FF6B6B'


# ── 工具函式 ──────────────────────────────────────────────────────────────
def _find_free_port(start: int = 8050) -> int:
    """從 start 往後尋找第一個可用的本機 port。"""
    import socket
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', p)) != 0:
                return p
    return start


# ── LoginApp ──────────────────────────────────────────────────────────────
class LoginApp:
    """
    登入視窗：填入 DB IP / 帳號 / 密碼 → 按「連線」。
    成功後：
      1. 找一個可用 port，啟動 Dash server（daemon 執行緒）
      2. 隱藏登入視窗，開啟 MainApp Toplevel
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title('海洋動力診斷儀表板 V2')
        root.configure(bg=_BG)
        root.resizable(False, False)
        self._build()
        root.bind('<Return>', lambda e: self._connect())  # Enter 鍵觸發連線

    def _build(self):
        frm = tk.Frame(self.root, bg=_PANEL, padx=24, pady=24)
        frm.pack(padx=36, pady=36)

        tk.Label(frm, text='🌊 海洋動力診斷儀表板 V2',
                 font=('標楷體', 15, 'bold'), bg=_PANEL, fg=_ACCENT,
                 ).grid(row=0, column=0, columnspan=2, pady=(0, 18))

        # 三欄輸入（IP / 帳號 / 密碼）
        self._vars: dict[str, tk.StringVar] = {}
        fields = [
            ('資料庫 IP', 'DB_HOST_INT', ''),
            ('帳號',      'DB_USER',     ''),
            ('密碼',      'DB_PASS',     '*'),
        ]
        for i, (label, key, show) in enumerate(fields):
            tk.Label(frm, text=label, font=_F, bg=_PANEL, fg=_TEXT,
                     ).grid(row=i + 1, column=0, sticky='e', pady=4, padx=(0, 8))
            var = tk.StringVar(value=os.getenv(key, ''))
            tk.Entry(frm, textvariable=var, show=show, font=_F,
                     bg=_ENTRY, fg=_TEXT, insertbackground=_TEXT, width=22,
                     ).grid(row=i + 1, column=1, sticky='w', pady=4)
            self._vars[key] = var

        self._msg = tk.StringVar()
        tk.Label(frm, textvariable=self._msg, font=_FS,
                 bg=_PANEL, fg=_ERR,
                 ).grid(row=4, column=0, columnspan=2, pady=4)

        tk.Button(frm, text='連線', command=self._connect,
                  font=_F, bg=_ACCENT, fg='white', relief='flat', padx=18,
                  activebackground=_HEADER,
                  ).grid(row=5, column=0, columnspan=2, pady=8)

    def _connect(self):
        host = self._vars['DB_HOST_INT'].get().strip()
        user = self._vars['DB_USER'].get().strip()
        pwd  = self._vars['DB_PASS'].get()

        if not host or not user:
            self._msg.set('請填入 IP 與帳號')
            return

        self._msg.set('連線中…')
        self.root.update_idletasks()

        try:
            engine = OceanDataEngine(
                host=host,
                user=user,
                password=pwd,
                database=os.getenv('DB_NAME', 'mrbank'),
                port=int(os.getenv('DB_PORT', '3306')),
                warn_host=os.getenv('DB_HOST_EXT', '61.56.13.104'),
                mapping_src=os.getenv('MAPPING_SRC', 'csv'),
                typhoon_db=os.getenv('TYPHOON_DB', 'med_data'),
            )
        except Exception as exc:
            self._msg.set(f'連線失敗：{exc}')
            return

        # 明確呼叫 load_mapping（若 __init__ 已呼叫為 no-op；若未呼叫或靜默失敗則補救）
        # R4 的 load_mapping() 在 CSV 找不到時只記 logger.warning，不拋例外，
        # 所以即使失敗也不會被 except 到——用 kind_map 是否為空做二次確認。
        self._msg.set('載入測站對應表…')
        self.root.update_idletasks()
        try:
            engine.load_mapping()
        except Exception as exc:
            self._msg.set(f'load_mapping 失敗：{exc}')
            return

        if not getattr(engine, 'kind_map', {}):
            messagebox.showwarning(
                '測站對應表為空',
                'kind_map 為空，測站列表將空白。\n\n'
                '可能原因：\n'
                '  1. .env 未設 MAPPING_CSV，預設路徑「對應站表格.csv」\n'
                '     不在腳本同目錄\n'
                '  2. 或改設 MAPPING_SRC=db 改用資料庫讀取\n\n'
                '可先跳過繼續，但無法查詢。',
            )

        # 找可用 port → 啟動 Dash server（daemon 執行緒）
        port = _find_free_port()
        dash_app_module.PORT = port
        threading.Thread(
            target=lambda: dash_app_module.app.run(
                host='127.0.0.1', port=port,
                debug=False, use_reloader=False,
            ),
            daemon=True,
        ).start()

        # 隱藏登入視窗，開啟主介面
        self.root.withdraw()
        top = tk.Toplevel(self.root)
        top.protocol('WM_DELETE_WINDOW', self.root.destroy)
        MainApp(top, engine, port)


# ── MainApp ───────────────────────────────────────────────────────────────
class MainApp:
    """
    主查詢介面：
      - 颱風年份 / 事件選單（選填）
      - 起始 / 結束日期
      - 測站列表（潮位站 / 浮球浮標，依 kind_map 分頁）
      - 查詢按鈕 → 背景執行緒 fetch_bundle → 推到 dash_bridge → 開瀏覽器
    """

    def __init__(self, win: tk.Toplevel, engine: OceanDataEngine, port: int):
        self.win    = win
        self.ENGINE = engine
        self.port   = port
        self._ty_df  = None   # fetch_typhoons() 回傳的 DataFrame
        self._ty_sel = None   # 目前選取的颱風列（dict）

        win.title('海洋動力診斷儀表板 V2 — 選站查詢')
        win.configure(bg=_BG)
        win.geometry('820x720')

        self._build_ui()
        self._load_years()   # 非同步填充颱風年份（失敗不崩）

    # ── UI 建立 ───────────────────────────────────────────────────────────

    def _build_ui(self):
        # 日期範圍
        dt_frm = tk.LabelFrame(self.win, text='查詢時間範圍', font=_F,
                               bg=_PANEL, fg=_TEXT, padx=8, pady=6)
        dt_frm.pack(fill='x', padx=10, pady=4)
        dt_frm.columnconfigure((1, 3), weight=1)

        tk.Label(dt_frm, text='起始日期', font=_FS, bg=_PANEL, fg=_TEXT,
                 ).grid(row=0, column=0, sticky='e', padx=(0, 4))
        self._start = self._make_date(dt_frm)
        self._start.grid(row=0, column=1, sticky='w', padx=(0, 16))

        tk.Label(dt_frm, text='結束日期', font=_FS, bg=_PANEL, fg=_TEXT,
                 ).grid(row=0, column=2, sticky='e', padx=(0, 4))
        self._end = self._make_date(dt_frm)
        self._end.grid(row=0, column=3, sticky='w')

        # 颱風事件選單
        ty_frm = tk.LabelFrame(self.win, text='颱風事件（選填）', font=_F,
                               bg=_PANEL, fg=_TEXT, padx=8, pady=6)
        ty_frm.pack(fill='x', padx=10, pady=(10, 4))
        ty_frm.columnconfigure((1, 3), weight=1)

        tk.Label(ty_frm, text='年份', font=_FS, bg=_PANEL, fg=_TEXT,
                 ).grid(row=0, column=0, sticky='e', padx=(0, 4))
        self._yr_var = tk.StringVar()
        self._yr_cb  = ttk.Combobox(ty_frm, textvariable=self._yr_var,
                                    state='readonly', width=7, font=_FS)
        self._yr_cb.grid(row=0, column=1, sticky='w', padx=(0, 16))
        self._yr_cb.bind('<<ComboboxSelected>>', self._on_year)

        tk.Label(ty_frm, text='颱風', font=_FS, bg=_PANEL, fg=_TEXT,
                 ).grid(row=0, column=2, sticky='e', padx=(0, 4))
        self._ty_var = tk.StringVar()
        self._ty_cb  = ttk.Combobox(ty_frm, textvariable=self._ty_var,
                                    state='readonly', width=20, font=_FS)
        self._ty_cb.grid(row=0, column=3, sticky='w')
        self._ty_cb.bind('<<ComboboxSelected>>', self._on_typhoon)

        # 測站列表（分頁）
        st_frm = tk.LabelFrame(self.win, text='測站選擇', font=_F,
                               bg=_PANEL, fg=_TEXT, padx=6, pady=4)
        st_frm.pack(fill='both', expand=True, padx=10, pady=4)

        nb = ttk.Notebook(st_frm)
        nb.pack(fill='both', expand=True)

        tide_f = tk.Frame(nb, bg=_BG)
        nb.add(tide_f, text='🌊 潮位站')
        self._tide_lb, _ = self._make_lb(tide_f)

        buoy_f = tk.Frame(nb, bg=_BG)
        nb.add(buoy_f, text='📍 浮球/浮標')
        self._buoy_lb, _ = self._make_lb(buoy_f)

        self._stids_tide: list[str] = []
        self._stids_buoy: list[str] = []
        # 👇 新增：綁定日期改變事件，連動更新測站列表
        if _HAS_CAL:
            self._start.bind("<<DateEntrySelected>>", lambda e: self._fill_stations())
            self._end.bind("<<DateEntrySelected>>", lambda e: self._fill_stations())
        else:
            self._start.bind("<FocusOut>", lambda e: self._fill_stations())
            self._start.bind("<Return>", lambda e: self._fill_stations())
            self._end.bind("<FocusOut>", lambda e: self._fill_stations())
            self._end.bind("<Return>", lambda e: self._fill_stations())

        self._stids_tide: list[str] = []
        self._stids_buoy: list[str] = []
        self._fill_stations()
        # self._fill_stations()

        # 查詢按鈕列
        btn_frm = tk.Frame(self.win, bg=_BG)
        btn_frm.pack(fill='x', padx=10, pady=8)

        self._status = tk.StringVar()
        tk.Label(btn_frm, textvariable=self._status, font=_FS,
                 bg=_BG, fg=_ERR,
                 ).pack(side='left', padx=8)

        tk.Button(btn_frm, text='🔍  查詢', command=self._on_query,
                  font=('標楷體', 13, 'bold'), bg=_ACCENT, fg='white',
                  relief='flat', padx=14, pady=5,
                  activebackground=_HEADER,
                  ).pack(side='right', padx=8)

    def _make_date(self, parent) -> tk.Widget:
        """建立日期輸入元件。有 tkcalendar 用 DateEntry，否則用 Entry。"""
        if _HAS_CAL:
            return DateEntry(parent, font=_FS, width=13,
                             date_pattern='yyyy-mm-dd',
                             background=_HEADER, foreground='white')
        else:
            e = tk.Entry(parent, font=_FS, width=13,
                         bg=_ENTRY, fg=_TEXT, insertbackground=_TEXT)
            e.insert(0, str(date.today()))
            return e

    def _make_lb(self, parent) -> tuple[tk.Listbox, tk.Scrollbar]:
        """建立帶捲軸的多選 Listbox。"""
        f  = tk.Frame(parent, bg=_BG)
        f.pack(fill='both', expand=True)
        sb = tk.Scrollbar(f)
        sb.pack(side='right', fill='y')
        # Gemini建議加浮標part1：👇 在這裡加上 exportselection=False 參數
        lb = tk.Listbox(f, selectmode='multiple', yscrollcommand=sb.set,
                        font=_FS, bg=_ENTRY, fg=_TEXT,
                        selectbackground=_ACCENT, activestyle='none', bd=0,
                        exportselection=False)
        lb.pack(side='left', fill='both', expand=True)
        sb.config(command=lb.yview)
        return lb, sb

    # def _fill_stations(self):
    #     """依 kind_map 分流填入潮位站 / 浮球浮標列表。"""
    #     import math
    #     # # --- 加入以下這段來攔截檢查 ---
    #     # print("\n" + "="*30)
    #     # print("[盤點檢查] 1. mapping_df 狀態:")
    #     # df = getattr(self.ENGINE, 'mapping_df', None)
    #     # print(f"是否有資料表: {df is not None}")
    #     # if df is not None and not (hasattr(df, 'empty') and df.empty):
    #     #     print(f"包含的欄位: {df.columns.tolist()}")
            
    #     # print("\n[盤點檢查] 2. name_map 狀態 (印出前 3 筆):")
    #     # nm = getattr(self.ENGINE, 'name_map', {})
    #     # print(list(nm.items())[:3] if nm else "name_map 為空")
    #     # print("="*30 + "\n")
    #     # -----------------------------
    #     # ── STID key 正規化 ────────────────────────────────────────────────
    #     # CSV 空白列 → pandas 讀成 NaN（float）→ 跳過
    #     # 數字型 STID（如 1176.0）→ 轉 '1176'
    #     # 正常字串 STID → 原樣保留
    #     kind_map_raw = getattr(self.ENGINE, 'kind_map', {})
    #     kind_map: dict[str, str] = {}
    #     for k, v in kind_map_raw.items():
    #         # print('[DEBUG] name_map 內容:', getattr(self.ENGINE, 'name_map', {}))
    #         try:
    #             if isinstance(k, float) and math.isnan(k):
    #                 continue                                     # 跳過 NaN
    #             key = str(int(k)) if isinstance(k, float) else str(k)
    #             kind_map[key] = str(v)
    #         except (ValueError, TypeError):
    #             continue

    #     # 嘗試取站名：
    #     #   1. name_map（R4 在 load_mapping() 設定的屬性，{STID: 中文名稱}）
    #     #      → 同樣需要正規化 key，否則 float 1176.0 對不到 str '1176'
    #     #   2. mapping_df 欄位 fallback
    #     # ── 若要改站名顯示格式，修改此區塊的 lbl 行即可 ──
    #     stname: dict[str, str] = {}
    #     for k, v in (getattr(self.ENGINE, 'name_map', None) or {}).items():
    #         try:
    #             if isinstance(k, float) and math.isnan(k):
    #                 continue
    #             key = str(int(k)) if isinstance(k, float) else str(k)
    #             stname[key] = str(v) if v is not None else ''
    #         except (ValueError, TypeError):
    #             continue
    #     if not stname:
    #         df = getattr(self.ENGINE, 'mapping_df', None)
    #         if df is not None and not df.empty:
    #             for id_c in ('STID', 'stid'):
    #                 for nm_c in ('CNAME', 'cname', 'STNAC', 'stnac', 'STNAME', 'stname'):
    #                     if id_c in df.columns and nm_c in df.columns:
    #                         stname = dict(zip(df[id_c], df[nm_c]))
    #                         break
    #                 if stname:
    #                     break

    #     # # --------------Gemini 建議修改版本（完成R4除錯後已不需要）--------------
    #     # stname: dict[str, str] = {}
    #     # # 1. 優先從 mapping_df 尋找站名 (把 stnac 優先級拉高)
    #     # df = getattr(self.ENGINE, 'mapping_df', None)
    #     # if df is not None and not hasattr(df, 'empty') or (hasattr(df, 'empty') and not df.empty):
    #     #     for id_c in ('STID', 'stid'):
    #     #         # for nm_c in ('STNAC', 'stnac', 'CNAME', 'cname', 'STNAME', 'stname'):
    #     #         for nm_c in ('STNAC', 'stnac', 'CNAME_y', 'CNAME', 'cname', 'STNAME', 'stname'):  # R4有問題，暫時先這樣改，等改好要換回上一行。
    #     #             if id_c in df.columns and nm_c in df.columns:
    #     #                 # 確保轉換成字串對應
    #     #                 stname = {str(k): str(v) for k, v in zip(df[id_c], df[nm_c]) if not (isinstance(k, float) and __import__('math').isnan(k))}
    #     #                 break
    #     #         if stname:
    #     #             break
        
    #     # # 2. 如果 df 沒抓到，才使用原本的 name_map 備案
    #     # if not stname:
    #     #     for k, v in (getattr(self.ENGINE, 'name_map', None) or {}).items():
    #     #         try:
    #     #             if isinstance(k, float) and __import__('math').isnan(k):
    #     #                 continue
    #     #             key = str(int(k)) if isinstance(k, float) else str(k)
    #     #             stname[key] = str(v) if v is not None else ''
    #     #         except (ValueError, TypeError):
    #     #             continue
    #     # # --------------Gemini 建議修改版本（完成R4除錯後已不需要）--------------

    #     # for stid, kind in sorted(kind_map.items()):
    #     #     nm  = stname.get(stid, '')
    #     #     lbl = f'{stid}  {nm}' if nm else stid
    #     #     if kind in KIND_TIDE:
    #     #         self._stids_tide.append(stid)
    #     #         self._tide_lb.insert('end', lbl)
    #     #     elif kind in KIND_BUOY:
    #     #         self._stids_buoy.append(stid)
    #     #         self._buoy_lb.insert('end', lbl)
        
    #     # 嘗試抓取剛建立的 sponsor_map
    #     sponsor_map = getattr(self.ENGINE, 'sponsor_map', {})

    #     for stid, kind in sorted(kind_map.items()):
    #         nm  = stname.get(stid, '')
    #         sp  = sponsor_map.get(stid, '')
            
    #         # 【新增邏輯】：將 Sponsor 放進括號顯示，例如 "1176 將軍潮位站 (CWA)"
    #         if nm and sp:
    #             lbl = f'{stid}  {nm} ({sp})'
    #         elif nm:
    #             lbl = f'{stid}  {nm}'
    #         elif sp:
    #             lbl = f'{stid}  ({sp})'
    #         else:
    #             lbl = stid

    #         if kind in KIND_TIDE:
    #             self._stids_tide.append(stid)
    #             self._tide_lb.insert('end', lbl)
    #         elif kind in KIND_BUOY:
    #             self._stids_buoy.append(stid)
    #             self._buoy_lb.insert('end', lbl)

    def _fill_stations(self):
        """依 kind_map 分流，並搭配起訖時間與 datebeg/dateend 篩選測站。"""
        import math
        import pandas as pd
        
        # 1. 清空目前的 UI 列表與儲存陣列（因為每次換日期都會重跑）
        self._tide_lb.delete(0, 'end')
        self._buoy_lb.delete(0, 'end')
        self._stids_tide.clear()
        self._stids_buoy.clear()

        # 2. 取得使用者查詢的起訖時間
        u_start = self._get_date(self._start)
        u_end = self._get_date(self._end)

        # 3. 從 mapping_df 抓取 datebeg/dateend 並做格式轉換
        date_ranges = {}
        df = getattr(self.ENGINE, 'mapping_df', None)
        if df is not None and not (hasattr(df, 'empty') and df.empty):
            if 'DATEBEG' in df.columns and 'DATEEND' in df.columns:
                for _, row in df.iterrows():
                    try:
                        # 確保 key 格式正規化（與下面 kind_map 處理邏輯一致）
                        k = row['STID']
                        if isinstance(k, float) and math.isnan(k):
                            continue
                        key = str(int(k)) if isinstance(k, float) else str(k)
                        date_ranges[key] = (row['DATEBEG'], row['DATEEND'])
                    except (ValueError, TypeError):
                        continue

        # ── STID key 正規化 ────────────────────────────────────────────────
        kind_map_raw = getattr(self.ENGINE, 'kind_map', {})
        kind_map: dict[str, str] = {}
        for k, v in kind_map_raw.items():
            try:
                if isinstance(k, float) and math.isnan(k):
                    continue
                key = str(int(k)) if isinstance(k, float) else str(k)
                kind_map[key] = str(v)
            except (ValueError, TypeError):
                continue

        # 嘗試取站名 fallback
        stname: dict[str, str] = {}
        for k, v in (getattr(self.ENGINE, 'name_map', None) or {}).items():
            try:
                if isinstance(k, float) and math.isnan(k):
                    continue
                key = str(int(k)) if isinstance(k, float) else str(k)
                stname[key] = str(v) if v is not None else ''
            except (ValueError, TypeError):
                continue
                
        if not stname and df is not None and not df.empty:
            for id_c in ('STID', 'stid'):
                for nm_c in ('CNAME', 'cname', 'STNAC', 'stnac', 'STNAME', 'stname'):
                    if id_c in df.columns and nm_c in df.columns:
                        stname = dict(zip(df[id_c], df[nm_c]))
                        break
                if stname:
                    break

        sponsor_map = getattr(self.ENGINE, 'sponsor_map', {})

        # 4. 迴圈塞入選單並過濾時間
        for stid, kind in sorted(kind_map.items()):
            # 【時間重疊檢查邏輯】
            # 重疊條件：(使用者起日 <= 測站迄日) AND (使用者迄日 >= 測站起日)
            # 若無交集，直接 continue 略過該站
            if u_start and u_end and stid in date_ranges:
                db_beg, db_end = date_ranges[stid]
                
                db_beg_date, db_end_date = None, None
                try:
                    if pd.notnull(db_beg): db_beg_date = pd.to_datetime(db_beg).date()
                except Exception: pass
                
                try:
                    if pd.notnull(db_end): db_end_date = pd.to_datetime(db_end).date()
                except Exception: pass

                # 使用者查詢的結束時間比測站資料的起始時間還早 -> 無重疊
                if db_beg_date and u_end < db_beg_date:
                    continue
                # 使用者查詢的起始時間比測站資料的結束時間還晚 -> 無重疊
                if db_end_date and u_start > db_end_date:
                    continue

            # ── 顯示文字合成 ──
            nm  = stname.get(stid, '')
            sp  = sponsor_map.get(stid, '')
            
            if nm and sp:
                lbl = f'{stid}  {nm} ({sp})'
            elif nm:
                lbl = f'{stid}  {nm}'
            elif sp:
                lbl = f'{stid}  ({sp})'
            else:
                lbl = stid

            # 塞入清單
            if kind in KIND_TIDE:
                self._stids_tide.append(stid)
                self._tide_lb.insert('end', lbl)
            elif kind in KIND_BUOY:
                self._stids_buoy.append(stid)
                self._buoy_lb.insert('end', lbl)
    
    # ── 颱風選單 ──────────────────────────────────────────────────────────

    def _load_years(self):
        """載入颱風年份清單（失敗靜默，不影響主流程）。"""
        try:
            yrs = self.ENGINE.fetch_years()
        except Exception:
            return
        if not yrs:
            return
        self._yr_cb['values'] = [str(y) for y in yrs]
        self._yr_cb.set(str(yrs[0]))
        self._load_events(str(yrs[0]))

    def _on_year(self, _=None):
        yr = self._yr_var.get()
        if yr:
            self._load_events(yr)

    def _load_events(self, year: str):
        try:
            df = self.ENGINE.fetch_typhoons(year)
        except Exception:
            return
        if df is None or (hasattr(df, 'empty') and df.empty):
            return
        self._ty_df = df
        # 欄位名大小寫由 R4 決定，做防禦性查找
        id_c = 'id'    if 'id'    in df.columns else 'ID'
        nm_c = 'cname' if 'cname' in df.columns else 'CNAME'
        self._ty_cb['values'] = [f"{r[id_c]}  {r[nm_c]}"
                                  for _, r in df.iterrows()]
        self._ty_var.set('')
        self._ty_sel = None

    def _on_typhoon(self, _=None):
        i = self._ty_cb.current()
        if i < 0 or self._ty_df is None:
            self._ty_sel = None
            return
        self._ty_sel = self._ty_df.iloc[i].to_dict()
        self._auto_fill_date()   # 自動填入海警期間前後各一天

    def _auto_fill_date(self) -> None:
        """
        依選取颱風的海上警報時段自動設定起訖日期（前後各 +1 天）。
        欄位名依 R4 fetch_typhoons() 的 AS 別名而定；防禦性查找多種可能。
        """
        if not self._ty_sel:
            return
        beg_keys = ('WARN1BEG', 'warnSeaBeg', 'warn1beg')
        end_keys  = ('WARN1END', 'warnSeaEnd', 'warn1end')
        beg_val = next(
            (self._ty_sel[k] for k in beg_keys
             if k in self._ty_sel and self._ty_sel[k]),
            None,
        )
        end_val = next(
            (self._ty_sel[k] for k in end_keys
             if k in self._ty_sel and self._ty_sel[k]),
            None,
        )
        if beg_val is None or end_val is None:
            return
        try:
            from pandas import Timestamp
            import datetime as _dt
            s = Timestamp(beg_val).date() - _dt.timedelta(days=1)
            e = Timestamp(end_val).date()  + _dt.timedelta(days=1)
            self._set_date(self._start, s)
            self._set_date(self._end,   e)
            # 👇 新增：設定完颱風日期後，觸發重新篩選測站
            self._fill_stations()
        except Exception:
            pass   # 解析失敗靜默，不影響主流程
        # except Exception:
        #     pass   # 解析失敗靜默，不影響主流程

    def _set_date(self, w, d: date) -> None:
        """設定日期元件的值（相容 DateEntry 和 Entry 兩種寫法）。"""
        if _HAS_CAL and isinstance(w, DateEntry):
            w.set_date(d)
        else:
            w.delete(0, 'end')
            w.insert(0, str(d))

    # ── 查詢 ──────────────────────────────────────────────────────────────

    def _get_date(self, w) -> date | None:
        if _HAS_CAL and isinstance(w, DateEntry):
            try:
                return w.get_date()
            except Exception:
                pass
        raw = w.get().strip()
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            return None

    # def _on_query(self):
    #     # 取所選站（R5 只處理潮位站；浮球/浮標 R7 再接通）
    #     stids = [self._stids_tide[i] for i in self._tide_lb.curselection()]
    #     if not stids:
    #         self._status.set('請選擇至少一個潮位站')
    #         return

    #     s = self._get_date(self._start)
    #     e = self._get_date(self._end)
    #     if s is None or e is None:
    #         self._status.set('日期格式錯誤（YYYY-MM-DD）')
    #         return
    #     if s > e:
    #         self._status.set('起始日期不可晚於結束日期')
    #         return

    #     self._status.set('查詢中…')
    #     self.win.update_idletasks()

    #     # snapshot 颱風選擇（避免 thread 和 UI 線程競爭）
    #     ty_sel = self._ty_sel

    #     threading.Thread(
    #         target=self._do_query,
    #         args=(stids, s, e, ty_sel),
    #         daemon=True,
    #     ).start()
    def _on_query(self):
        # 同時取得潮位站與浮標站的選取資料
        tide_stids = [self._stids_tide[i] for i in self._tide_lb.curselection()]
        buoy_stids = [self._stids_buoy[i] for i in self._buoy_lb.curselection()]
        
        # 只要兩邊加起來有選至少一站就可以
        if not tide_stids and not buoy_stids:
            self._status.set('請選擇至少一個測站（潮位或浮標）')
            return

        s = self._get_date(self._start)
        e = self._get_date(self._end)
        if s is None or e is None:
            self._status.set('日期格式錯誤（YYYY-MM-DD）')
            return
        if s > e:
            self._status.set('起始日期不可晚於結束日期')
            return

        self._status.set('查詢中…')
        self.win.update_idletasks()

        ty_sel = self._ty_sel

        threading.Thread(
            target=self._do_query,
            # 改成傳遞兩種站點的清單
            args=(tide_stids, buoy_stids, s, e, ty_sel),
            daemon=True,
        ).start()

    # def _do_query(self, stids: list, start: date, end: date, ty_sel):
    #     """背景執行緒：逐站 fetch_bundle → 推到 bridge → 開瀏覽器。"""
    #     bundles = []
    #     for sid in stids:
    #         try:
    #             b = self.ENGINE.fetch_bundle(sid, start, end)
    #             if b is not None:
    #                 bundles.append(b)
    #         except Exception as exc:
    #             import traceback
    #             print(f'[main_app] fetch_bundle({sid}) 失敗：{exc}')
    #             traceback.print_exc()   # 顯示完整 traceback 以定位 R4 bug

    #     if not bundles:
    #         self.win.after(0, lambda: self._status.set('查無資料'))
    #         return

    #     # 颱風標籤（若有選）
    #     typhoon_label: str | None = None
    #     typhoon_info:  dict | None = None
    #     if ty_sel:
    #         typhoon_info = ty_sel
    #         for k in ('cname', 'CNAME'):
    #             if k in ty_sel:
    #                 typhoon_label = str(ty_sel[k])
    #                 break

    #     # 警戒值（R4 已實作 get_thresholds；warn_conn=None 時會回傳 None）
    #     thresholds_map: dict = {}
    #     for sid in stids:
    #         try:
    #             thresholds_map[sid] = self.ENGINE.get_thresholds(sid)
    #         except Exception:
    #             thresholds_map[sid] = None

    #     # 產生 URL key 並推到 bridge
    #     ts  = datetime.now().strftime('%Y%m%d%H%M%S')
    #     key = f'{stids[0]}_{ts}'

    #     dash_bridge.bridge.set_bundle(
    #         key=key,
    #         bundles=bundles,
    #         land_range=None,           # TODO R6：解析颱風陸警時段（WARN2BEG/END）
    #         typhoon_label=typhoon_label,
    #         typhoon_info=typhoon_info,
    #         thresholds_map=thresholds_map,
    #     )
    #     dash_bridge.bridge.set_selection(tide_stids=stids, buoy_stids=[])

    #     url = f'http://127.0.0.1:{self.port}/?key={key}'
    #     self.win.after(0, lambda: self._on_done(url, len(bundles)))
    # 接收潮位站和浮標站的參數
    def _do_query(self, tide_stids: list, buoy_stids: list, start: date, end: date, ty_sel):
        """背景執行緒：逐站 fetch_bundle → 推到 bridge → 開瀏覽器。"""
        # 將兩種站點合併，一起去撈取資料
        all_stids = tide_stids + buoy_stids
        bundles = []
        for sid in all_stids:
            try:
                b = self.ENGINE.fetch_bundle(sid, start, end)
                if b is not None:
                    bundles.append(b)
            except Exception as exc:
                import traceback
                print(f'[main_app] fetch_bundle({sid}) 失敗：{exc}')
                traceback.print_exc()

        if not bundles:
            self.win.after(0, lambda: self._status.set('查無資料'))
            return

        typhoon_label: str | None = None
        typhoon_info:  dict | None = None
        warn_rng:      dict | None = None
        if ty_sel:
            # 2026-11-20：在這裡統一颱風欄位名（id／cname／warnSeaBeg…，原欄位保留），
            # 並算好海警／陸警期間（裁到查詢期間內），下游圖表與 Mode 3 SQL 不再各自轉換。
            typhoon_info = normalize_typhoon_info(ty_sel)
            if typhoon_info.get('cname'):
                typhoon_label = str(typhoon_info['cname'])
            warn_rng = warn_ranges(typhoon_info, start, query_end_exclusive(end))

        thresholds_map: dict = {}
        for sid in all_stids:
            try:
                thresholds_map[sid] = self.ENGINE.get_thresholds(sid)
            except Exception:
                thresholds_map[sid] = None

        # [R7 新增] 獨立撈取浮標/浮球的 Raw Data 包裝成 buoy_qc_data
        buoy_qc_data = {}
        if buoy_stids:
            for sid in buoy_stids:
                try:
                    buoy_qc_data[sid] = {
                        'wave1_df': self.ENGINE.fetch_wave1(sid, start, end),
                        'wind_df':  self.ENGINE.fetch_wind(sid, start, end),
                        'curr_df':  self.ENGINE.fetch_curr(sid, start, end),
                        'wave_df':  self.ENGINE.fetch_wave(sid, start, end),
                        'pres1_df': self.ENGINE.fetch_pres1(sid, start, end),
                        'stemp1_dfs': self.ENGINE.fetch_stemp1(sid, start, end)
                    }
                except Exception as exc:
                    print(f'[main_app] fetch_buoy_raw({sid}) 失敗：{exc}')

        ts  = datetime.now().strftime('%Y%m%d%H%M%S')
        key = f'{all_stids[0]}_{ts}'

        # 推送到 Bridge 並分別傳遞潮位站和浮標站
        dash_bridge.bridge.set_bundle(
            key=key,
            bundles=bundles,
            land_range=warn_rng,     # 名稱沿用；內容是海警＋陸警（typhoon_bands.warn_ranges）
            typhoon_label=typhoon_label,
            typhoon_info=typhoon_info,
            thresholds_map=thresholds_map,
            buoy_qc_data=buoy_qc_data  # <== [R7 新增]
        )
        dash_bridge.bridge.set_selection(tide_stids=tide_stids, buoy_stids=buoy_stids)

        url = f'http://127.0.0.1:{self.port}/?key={key}'
        self.win.after(0, lambda: self._on_done(url, len(bundles)))

    def _on_done(self, url: str, n: int):
        self._status.set(f'查詢完成，已載入 {n} 站資料')
        webbrowser.open(url)


# ── 啟動 ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    root = tk.Tk()
    LoginApp(root)
    root.mainloop()
