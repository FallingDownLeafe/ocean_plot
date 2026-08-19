"""
buoy_qc_app.py — 浮球/浮標品管工具：入口與資料層
從 .env 讀取 DB 連線資訊，啟動 Dash 後自動開啟瀏覽器。
支援資料表：wave1, wind, curr, wave（pres1/stemp1 屬 Plan B）
"""

import sys
import os
import threading
import time
import webbrowser
from pathlib import Path

import mysql.connector
import pandas as pd
# from dotenv import load_dotenv

# # ── 環境設定 ──────────────────────────────────────────────────
# BASE_DIR = Path(__file__).resolve().parent
# load_dotenv(BASE_DIR / ".env")

# ── 環境設定 ──────────────────────────────────────────────────

# 判斷是否為 PyInstaller 打包環境
if getattr(sys, 'frozen', False):
    # 如果是打包後的 exe，抓取 exe 所在的目錄
    BASE_DIR = Path(sys.executable).parent
else:
    # 如果是開發環境，抓取 py 檔所在的目錄
    BASE_DIR = Path(__file__).resolve().parent

# 手動讀取同目錄下的 .env 檔（不依賴 python-dotenv，PyInstaller 友好）
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

DB_IP   = os.environ.get("DB_IP", "")
DB_USER = os.environ.get("DB_USER", "")
DB_PASS = os.environ.get("DB_PASS", "")
DB_NAME = os.environ.get("DB_NAME", "mrbank")
DASH_PORT = int(os.environ.get("BUOY_PORT", "8060"))  # 避免與主系統 8050 衝突


# ── 資料引擎 ──────────────────────────────────────────────────
class BuoyDataEngine:
    """
    浮球/浮標品管工具的資料層。
    不繼承主系統 OceanDataEngine，僅含品管工具所需的查詢。
    """

    def __init__(self):
        self.conn = mysql.connector.connect(
            host=DB_IP, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset="utf8mb3",
            connection_timeout=10,
        )

    # ── 測站清單 ───────────────────────────────────────────────
    def load_stations(self) -> pd.DataFrame:
        """查詢 kind=7（浮球）、kind=8（浮標）的測站清單。"""
        sql = """
            SELECT STID, stnac, kind
            FROM st
            WHERE kind IN (7, 8)
            ORDER BY kind, STID
        """
        df = pd.read_sql(sql, self.conn)
        df.columns = [c.upper() for c in df.columns]
        return df

    # ── 資料查詢 ──────────────────────────────────────────────
    def fetch_wave1(self, stid: str, start: str, end: str) -> pd.DataFrame:
        """查詢 wave1：TIME 直接為 datetime，回傳欄位全部大寫。"""
        sql = f"""
            SELECT TIME, H3, HMAX, qc
            FROM wave1
            WHERE STID = '{stid}'
              AND TIME BETWEEN '{start}' AND '{end}'
            ORDER BY TIME, qc
        """
        df = pd.read_sql(sql, self.conn)
        df.columns = [c.upper() for c in df.columns]
        if not df.empty:
            df["TIME"] = pd.to_datetime(df["TIME"])
        return df

    def fetch_wind(self, stid: str, start: str, end: str, z: int) -> pd.DataFrame:
        """查詢 wind：VM/VG 單位 0.1 m/s，回傳時已換算為 m/s。"""
        sql = f"""
            SELECT TIME, VM, VG, DM, qc
            FROM wind
            WHERE STID = '{stid}'
              AND Z = {z}
              AND TIME BETWEEN '{start}' AND '{end}'
            ORDER BY TIME, qc
        """
        df = pd.read_sql(sql, self.conn)
        df.columns = [c.upper() for c in df.columns]
        if not df.empty:
            df["TIME"] = pd.to_datetime(df["TIME"])
            df["VM"] = df["VM"] * 0.1   # 0.1 m/s → m/s
            df["VG"] = df["VG"] * 0.1
        return df

    def fetch_curr(self, stid: str, start: str, end: str, z: int) -> pd.DataFrame:
        """查詢 curr：V 單位 mm/s，回傳時已換算為 cm/s。"""
        sql = f"""
            SELECT TIME, V, D, qc
            FROM curr
            WHERE STID = '{stid}'
              AND Z = {z}
              AND TIME BETWEEN '{start}' AND '{end}'
            ORDER BY TIME, qc
        """
        df = pd.read_sql(sql, self.conn)
        df.columns = [c.upper() for c in df.columns]
        if not df.empty:
            df["TIME"] = pd.to_datetime(df["TIME"])
            df["V"] = df["V"] * 0.1    # mm/s → cm/s
        return df

    def fetch_wave(self, stid: str, start: str, end: str) -> pd.DataFrame:
        """查詢 wave（浮標波浪表）：H 單位 cm。"""
        sql = f"""
            SELECT TIME, H, TMEAN, qc
            FROM wave
            WHERE STID = '{stid}'
              AND TIME BETWEEN '{start}' AND '{end}'
            ORDER BY TIME, qc
        """
        df = pd.read_sql(sql, self.conn)
        df.columns = [c.upper() for c in df.columns]
        if not df.empty:
            df["TIME"] = pd.to_datetime(df["TIME"])
        return df

    # ── 輔助查詢 ──────────────────────────────────────────────
    def get_z_options(self, table: str, stid: str) -> list:
        """動態查詢指定資料表中該站的所有 Z 值（整數清單）。"""
        sql = f"SELECT DISTINCT Z FROM `{table}` WHERE STID = '{stid}' ORDER BY Z"
        df = pd.read_sql(sql, self.conn)
        if df.empty:
            return []
        return [int(v) for v in df.iloc[:, 0]]

    def _expand_hr(self, df: pd.DataFrame, val_name: str, scale: float = 1.0) -> pd.DataFrame:
        """將 DATATIME + HR0~HR23 寬格式展開為逐時長格式。"""
        hr_cols = [c for c in df.columns if c.startswith("HR")]
        id_cols  = [c for c in df.columns if c not in hr_cols]
        long = df.melt(id_vars=id_cols, value_vars=hr_cols,
                       var_name="_HR", value_name=val_name)
        long["TIME"] = (pd.to_datetime(long["DATATIME"]) +
                        pd.to_timedelta(long["_HR"].str[2:].astype(int), unit="h"))
        long[val_name] = long[val_name] * scale
        long = (long.dropna(subset=[val_name])
                    .sort_values("TIME")
                    .reset_index(drop=True))
        return long[["TIME", val_name, "QC"]]

    def fetch_pres1(self, stid: str, start: str, end: str) -> pd.DataFrame:
        """查詢 pres1，展開 HR0~HR23 為逐時序列（×0.1 = hPa）。"""
        sql = f"""
            SELECT * FROM pres1
            WHERE STID = '{stid}'
              AND DATATIME BETWEEN '{start}' AND '{end} 23:59:59'
            ORDER BY DATATIME, qc
        """
        df = pd.read_sql(sql, self.conn)
        df.columns = [c.upper() for c in df.columns]
        if df.empty:
            return pd.DataFrame(columns=["TIME", "P", "QC"])
        return self._expand_hr(df, "P", scale=0.1)

    def fetch_stemp1(self, stid: str, start: str, end: str, z: int) -> pd.DataFrame:
        """查詢 stemp1，展開 HR0~HR23 為逐時序列（×0.1 = °C）。"""
        sql = f"""
            SELECT * FROM stemp1
            WHERE STID = '{stid}'
              AND Z = {z}
              AND DATATIME BETWEEN '{start}' AND '{end} 23:59:59'
            ORDER BY DATATIME, qc
        """
        df = pd.read_sql(sql, self.conn)
        df.columns = [c.upper() for c in df.columns]
        if df.empty:
            return pd.DataFrame(columns=["TIME", "T", "QC"])
        return self._expand_hr(df, "T", scale=0.1)

# ── 啟動 ─────────────────────────────────────────────────────
def _start_dash():
    from buoy_qc_dash import app
    app.run(host="127.0.0.1", port=DASH_PORT, debug=False, use_reloader=False)


def main():
    engine = BuoyDataEngine()

    # 將 engine 注入 Dash 模組（在 Dash 啟動前設定，callbacks 不會在此之前觸發）
    import buoy_qc_dash as dash_mod
    dash_mod.ENGINE = engine

    t = threading.Thread(target=_start_dash, daemon=True)
    t.start()
    time.sleep(1.5)
    webbrowser.open(f"http://127.0.0.1:{DASH_PORT}/")
    # t.join()   # 主執行緒等待 Dash（無 Tkinter 事件迴圈）
    # t.join() 無 timeout，主執行緒永遠等待，Ctrl+C 在 VS Code 內建 terminal 裡常被 Flask 吃掉
    try:
        while t.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
