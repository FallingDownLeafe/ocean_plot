"""
data_engine.py
==============
OceanDataEngine — 所有 DB 查詢方法集中處。

雙連線架構
  self.conn      主資料庫連線（DB_IP）
  self.warn_conn med.p_setting_warn 連線（WARN_IP；外部伺服器 .104/.160）
                 WARN_IP 未設或連線失敗時為 None；get_thresholds() 回傳 None。

.env 參數（由呼叫端 load_dotenv_manual() 解析後以 os.getenv 取得）
#   DB_IP        主 DB 主機 IP
#   DB_USER      主 DB 帳號
#   DB_PASS      主 DB 密碼
#   DB_NAME      主 DB 名稱（預設 'tsuwawa'）
#   WARN_IP      warn DB 主機 IP（選填；不設則不建立 warn 連線）
#   WARN_USER    warn DB 帳號（不設則同 DB_USER）
#   WARN_PASS    warn DB 密碼（不設則同 DB_PASS）
#   MAPPING_SRC  測站對應表來源（'csv' | 'db'，預設 'csv'）
#   MAPPING_CSV  CSV 路徑（MAPPING_SRC='csv' 時，預設 '對應站表格.csv'）
#   TYPHOON_DB   颱風 DB 名稱（安外 'med_data'，安內 'mrbank'，預設 'med_data'）

# main_app.py 建議建立方式：
#   load_dotenv_manual()
#   engine = OceanDataEngine(
#       host=os.getenv('DB_IP'),
#       user=os.getenv('DB_USER'),
#       password=os.getenv('DB_PASS'),
#       database=os.getenv('DB_NAME', 'tsuwawa'),
#       warn_host=os.getenv('WARN_IP'),
#       warn_user=os.getenv('WARN_USER'),
#       warn_password=os.getenv('WARN_PASS'),
#       mapping_src=os.getenv('MAPPING_SRC', 'csv'),
#       typhoon_db=os.getenv('TYPHOON_DB', 'med_data'),
# 新（替換）
  DB_HOST_INT  主 DB 主機 IP（安內伺服器）
  DB_HOST_EXT  外部伺服器 IP（.104/.160；同時作為 warn 連線目標）
  DB_PORT      主 DB port（預設 3306）
  DB_USER      DB 帳號
  DB_PASS      DB 密碼
  DB_NAME      主 DB 名稱（預設 'mrbank'）
  MAPPING_SRC  測站對應表來源（'csv' | 'db'，預設 'csv'）
  MAPPING_CSV  CSV 路徑（MAPPING_SRC='csv' 時，預設 '對應站表格.csv'）
  TYPHOON_DB   颱風 DB 名稱（安外 'med_data'，安內 'mrbank'，預設 'med_data'）

main_app.py 建議建立方式：
  load_dotenv_manual()
  engine = OceanDataEngine(
      host=os.getenv('DB_HOST_INT'),
      user=os.getenv('DB_USER'),
      password=os.getenv('DB_PASS'),
      database=os.getenv('DB_NAME', 'mrbank'),
      port=int(os.getenv('DB_PORT', '3306')),
      warn_host=os.getenv('DB_HOST_EXT'),
      mapping_src=os.getenv('MAPPING_SRC', 'csv'),
      typhoon_db=os.getenv('TYPHOON_DB', 'med_data'),
  )
  )
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path

import mysql.connector
import decimal as _decimal   # ← 新增這行
import re
import pandas as pd

logger = logging.getLogger(__name__)


# ── .env 手動解析 ──────────────────────────────────────────────────────────

def load_dotenv_manual(path: str | Path = '.env') -> None:
    """讀取 .env，以 os.environ.setdefault 寫入（不覆蓋已設環境變數）。"""
    try:
        with open(path, encoding='utf-8') as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, val)
    except FileNotFoundError:
        logger.warning('.env 不存在，依賴系統環境變數')


# ── OceanDataEngine ────────────────────────────────────────────────────────

class OceanDataEngine:
    """DB 查詢方法集中類別，不含 UI 邏輯。

    Parameters
    ----------
    host, user, password : str
        主資料庫連線參數。
    database : str
        主資料庫名稱（預設 'tsuwawa'）。
    warn_host : str | None
        med.p_setting_warn 所在外部伺服器 IP（.104 / .160）。
        None 表示不建立 warn 連線，get_thresholds() 一律回傳 None。
    warn_user, warn_password : str | None
        warn 連線帳密；None 時沿用主連線帳密。
    mapping_src : 'csv' | 'db'
        測站對應表來源。
    typhoon_db : str
        颱風 DB 名稱（安外 'med_data'，安內 'mrbank'）。
    mapping_csv_path : str | None
        MAPPING_SRC='csv' 時的 CSV 路徑；None 時從 os.getenv('MAPPING_CSV') 取得。
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        database: str = 'mrbank',
        port: int = 3306,
        *,
        warn_host: str | None = None,
        warn_user: str | None = None,
        warn_password: str | None = None,
        mapping_src: str = 'csv',
        typhoon_db: str = 'med_data',
        mapping_csv_path: str | None = None,
    ) -> None:
        self.mapping_src = mapping_src
        self.typhoon_db = typhoon_db

        # ── 主連線 ──
        self.conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database,
            port=port,
            charset='utf8mb4',
            autocommit=True,
        )
        logger.info('主 DB 連線成功：%s / %s', host, database)

        # ── warn 連線（med.p_setting_warn 只存在外部伺服器 .104 / .160） ──
        # 連線失敗時設 None；get_thresholds() 回傳 None，不中斷啟動。
        # 已知失敗原因：在安內環境連不到外部 IP；記錄原因供後續診斷。
        self.warn_conn: mysql.connector.connection.MySQLConnection | None = None
        if warn_host:
            _wu = warn_user or user
            _wp = warn_password or password
            try:
                self.warn_conn = mysql.connector.connect(
                    host=warn_host,
                    user=_wu,
                    password=_wp,
                    database='med',
                    charset='utf8mb4',
                    autocommit=True,
                )
                logger.info('warn 連線成功：%s / med', warn_host)
            except mysql.connector.Error as exc:
                logger.warning('warn 連線失敗（%s）：%s', warn_host, exc)
                logger.warning('颱風警戒門檻值（med.p_setting_warn）將無法查詢')

        # ── 測站對應表 ──
        self.mapping_df: pd.DataFrame = pd.DataFrame()
        self.kind_map: dict[str, str] = {}   # {STID: kind_str}
        self.name_map: dict[str, str] = {}   # {STID: 中文名稱}
        self.load_mapping(mapping_csv_path)

    # ──────────────────────────────────────────────────────────────────────
    # 內部工具
    # ──────────────────────────────────────────────────────────────────────

    def _query_df(
        self,
        sql: str,
        params: tuple | None = None,
        conn: mysql.connector.connection.MySQLConnection | None = None,
    ) -> pd.DataFrame:
        """執行 SQL，回傳欄位名稱全大寫的 DataFrame。

        等效於 pd.read_sql() + df.columns = [c.upper() for c in df.columns]，
        但直接使用 cursor 以確保 mysql.connector 相容性（避免 pandas 2.x
        對非 SQLAlchemy 連線的 DeprecationWarning）。
        conn 預設 self.conn；warn 相關查詢傳入 self.warn_conn。
        """
        _conn = conn if conn is not None else self.conn
        try:
            _conn.ping(reconnect=True, attempts=1, delay=0)
        except mysql.connector.Error:
            pass  # execute 失敗時再拋例外，不在此遮蔽

        cursor = _conn.cursor()
        try:
            cursor.execute(sql, params or ())
            if cursor.description is None:
                return pd.DataFrame()
            cols = [d[0].upper() for d in cursor.description]
            rows = cursor.fetchall()
            # return pd.DataFrame(rows, columns=cols)
            # 新
            df = pd.DataFrame(rows, columns=cols)
            # mysql.connector 回傳 DECIMAL 欄位為 decimal.Decimal，
            # 無法直接與 float 做算術；此處統一轉型，避免各方法個別處理。
            for _col in df.columns:
                _s = df[_col].dropna()
                if not _s.empty and isinstance(_s.iloc[0], _decimal.Decimal):
                    # df[_col] = df[_col].apply(float)
                    df[_col] = pd.to_numeric(df[_col], errors='coerce')
            return df
        finally:
            cursor.close()

    # ──────────────────────────────────────────────────────────────────────
    # 測站對應 / 初始化
    # ──────────────────────────────────────────────────────────────────────

    def load_mapping(self, csv_path: str | None = None) -> None:
        """載入 STID ↔ QCID 對應表，並從 st 補充測站名稱與 kind。

        結果存入：
          self.mapping_df   欄位：STID, QCID, CNAME, KIND（KIND 為 str）
          self.kind_map     {STID: kind_str}
          self.name_map     {STID: 中文名稱}
        """
        if self.mapping_src == 'db':
            raw = self._query_df("SELECT stid AS STID, qcid AS QCID FROM stitemqc")
        else:
            _path = csv_path or os.getenv('MAPPING_CSV', '對應站表格.csv')
            try:
                raw = pd.read_csv(_path, dtype=str, encoding='utf-8-sig')
                raw.columns = [c.upper() for c in raw.columns]
            except FileNotFoundError:
                logger.error('找不到測站對應 CSV：%s', _path)
                return

        if 'STID' not in raw.columns or 'QCID' not in raw.columns:
            logger.error('對應表缺少 STID / QCID 欄位，實際欄位：%s', list(raw.columns))
            return

        stids = raw['STID'].dropna().unique().tolist()
        if not stids:
            logger.warning('對應表無有效 STID')
            return

        # ph = ', '.join(['%s'] * len(stids))
        # st_df = self._query_df(
        #     f"SELECT STID, stnac AS CNAME, kind AS KIND FROM st WHERE STID IN ({ph})",
        #     tuple(stids),
        # )
        # st_df['KIND'] = st_df['KIND'].astype(str)

        # self.mapping_df = raw.merge(st_df, on='STID', how='left')
        # self.kind_map = dict(zip(
        #     self.mapping_df['STID'],
        #     self.mapping_df['KIND'].fillna(''),
        # ))
        # if 'CNAME' in self.mapping_df.columns:
        #     self.name_map = dict(zip(
        #         self.mapping_df['STID'],
        #         self.mapping_df['CNAME'].fillna(''),
        #     ))

        # --------------Gemini 建議修改版本--------------
        ph = ', '.join(['%s'] * len(stids))
        # 修正 1：保留 STNAC 原名，不要 AS CNAME 避免與 CSV 欄位撞名
        # 加入 DATEBEG 與 DATEEND 提供前端做日期交集過濾
        st_df = self._query_df(
            f"SELECT STID, stnac AS STNAC, kind AS KIND, sponsor AS SPONSOR, datebeg AS DATEBEG, dateend AS DATEEND FROM st WHERE STID IN ({ph})",
            tuple(stids),
        )
        st_df['KIND'] = st_df['KIND'].astype(str)

        self.mapping_df = raw.merge(st_df, on='STID', how='left')
        self.kind_map = dict(zip(
            self.mapping_df['STID'],
            self.mapping_df['KIND'].fillna(''),
        ))
        
        # 修正 2：優先使用從資料庫抓出來的 STNAC，如果沒有才用 CSV 的 CNAME
        if 'STNAC' in self.mapping_df.columns:
            self.name_map = dict(zip(
                self.mapping_df['STID'],
                self.mapping_df['STNAC'].fillna(''),
            ))
        elif 'CNAME' in self.mapping_df.columns:
            self.name_map = dict(zip(
                self.mapping_df['STID'],
                self.mapping_df['CNAME'].fillna(''),
            ))
        # 【新增】：建立 sponsor 對應表，準備交給 UI
        if 'SPONSOR' in self.mapping_df.columns:
            self.sponsor_map = dict(zip(
                self.mapping_df['STID'],
                self.mapping_df['SPONSOR'].fillna(''),
            ))
        # --------------Gemini 建議修改版本--------------
        logger.info('測站對應表載入完成：%d 筆', len(self.mapping_df))

    # ──────────────────────────────────────────────────────────────────────
    # MIN/HR 寬表展開
    # ──────────────────────────────────────────────────────────────────────

    def expand_data(
        self,
        df: pd.DataFrame,
        val_name: str,
        freq: str = '6min',
    ) -> pd.DataFrame:
        """MIN0~MIN9 / HR0~HR23 寬格式展開為時序長格式。

        Parameters
        ----------
        df : DataFrame
            必須含 DATATIME 欄位（已大寫）。呼叫前應已依 QC 分組，
            避免同一 Time 存在多筆不同 QC 的資料。
        val_name : str
            展開後的值欄位名稱（例如 'WL'、'AT'、'P'）。
        freq : '6min' | '1h'
            '6min' → prefix='MIN'，step=6 分鐘（tide6 格式）
            '1h'   → prefix='HR'，step=60 分鐘（pres6/stemp6/stemp1/pres1 格式）

        回傳 DataFrame 欄位：Time, {val_name}[, QC]
        Time 為 title case（非全大寫），供與 build_* 繪圖模組的 DataFrame 介面對齊。
        """
        if df is None or df.empty:
            return pd.DataFrame(columns=['Time', val_name])

        # prefix = 'MIN' if freq == '6min' else 'HR'
        # step = 6 if freq == '6min' else 60
        # 新
        if freq == '6min':
            prefix, step = 'MIN', 6
        else:
            step = 60
            # DB 欄位可能是 HR0/H0/HOUR0 等，動態偵測避免硬寫死
            for _p in ('HR', 'H', 'HOUR'):
                if any(c.startswith(_p) and c[len(_p):].isdigit() for c in df.columns):
                    prefix = _p
                    break
            else:
                prefix = 'HR'

        # 識別值欄位：以 prefix 開頭且後綴為純數字（如 MIN0, HR23）
        val_cols = [c for c in df.columns if c.startswith(prefix) and c[len(prefix):].isdigit()]
        if not val_cols:
            return pd.DataFrame(columns=['Time', val_name])

        id_cols = [c for c in df.columns if c not in val_cols and c != 'LASTUPDATETIME']

        melted = df.melt(id_vars=id_cols, value_vars=val_cols, var_name='_IDX', value_name=val_name)
        melted['_N'] = melted['_IDX'].str[len(prefix):].astype(int)
        melted['Time'] = (
            pd.to_datetime(melted['DATATIME']) + pd.to_timedelta(melted['_N'] * step, unit='m')
        ).dt.floor('min')

        keep = ['Time', val_name]
        if 'QC' in melted.columns:
            keep.append('QC')

        return (
            melted[keep]
            .dropna(subset=[val_name])
            .drop_duplicates(subset=['Time'])
            .sort_values('Time')
            .reset_index(drop=True)
        )

##### ────────────────── Sonnet 5 把以下整段都換掉了，不知道實際上到底換多少、不知道能不能work ────────
    # ──────────────────────────────────────────────────────────────────────
    # 颱風資料
    # ──────────────────────────────────────────────────────────────────────

    # def fetch_years(self) -> list[int]:
    #     """回傳颱風資料庫有資料的年份清單（降冪）。

    #     安內欄位大寫（WARN1BEG）；安外欄位小寫（warnSeaBeg）。
    #     依序嘗試兩種 SQL，失敗時 logger.warning 並回傳 []。
    #     """
    #     for col in ('WARN1BEG', 'warnSeaBeg'):
    #         try:
    #             sql = (
    #                 f"SELECT DISTINCT YEAR({col}) AS YR FROM {self.typhoon_db}.typhoonid "
    #                 f"WHERE {col} IS NOT NULL ORDER BY YR DESC"
    #             )
    #             df = self._query_df(sql)
    #             return df['YR'].dropna().astype(int).tolist()
    #         except mysql.connector.Error:
    #             continue
    #     logger.warning('fetch_years: 無法查詢颱風年份（安內/安外欄位皆失敗）')
    #     return []

    # def fetch_typhoons(self, year: int) -> pd.DataFrame:
    #     """回傳指定年份颱風清單。

    #     回傳欄位：ID, CNAME, WARN1BEG, WARN1END, WARN2BEG, WARN2END
    #     sponsor='LocalTime' 篩選需加 COLLATE utf8mb4_general_ci，
    #     否則在安外環境會拋 Illegal mix of collations。
    #     """
    #     # 安內：欄位大寫
    #     try:
    #         sql = (
    #             f"SELECT id AS ID, cname AS CNAME, "
    #             f"WARN1BEG, WARN1END, WARN2BEG, WARN2END "
    #             f"FROM {self.typhoon_db}.typhoonid "
    #             f"WHERE sponsor = 'LocalTime' COLLATE utf8mb4_general_ci "
    #             f"AND YEAR(WARN1BEG) = %s ORDER BY WARN1BEG"
    #         )
    #         return self._query_df(sql, (year,))
    #     except mysql.connector.Error:
    #         pass

    #     # 安外：欄位小寫（warnSeaBeg / warnLandBeg）
    #     sql = (
    #         f"SELECT id AS ID, cname AS CNAME, "
    #         f"warnSeaBeg AS WARN1BEG, warnSeaEnd AS WARN1END, "
    #         f"warnLandBeg AS WARN2BEG, warnLandEnd AS WARN2END "
    #         f"FROM {self.typhoon_db}.typhoonid "
    #         f"WHERE sponsor = 'LocalTime' COLLATE utf8mb4_general_ci "
    #         f"AND YEAR(warnSeaBeg) = %s ORDER BY warnSeaBeg"
    #     )
    #     return self._query_df(sql, (year,))
##### ────────────────── Sonnet 5 把以上整段都換掉了，不知道實際上到底換多少、不知道能不能work ────────

    # ──────────────────────────────────────────────────────────────────────
    # 颱風資料
    # ──────────────────────────────────────────────────────────────────────

    def _write_typhoon_diag(self, lines: list[str]) -> None:
        """R11 暫時診斷用：颱風查詢的嘗試過程寫到本地 log，供打包後（無主控台）除錯。
        確認問題解決後可整段移除。
        """
        try:
            from datetime import datetime
            with open('typhoon_debug.log', 'a', encoding='utf-8') as f:
                f.write(f"--- {datetime.now().isoformat()} ---\n")
                for line in lines:
                    f.write(line + '\n')
        except Exception:
            pass

    def _write_debug_log(self, category: str, lines: list[str]) -> None:
        """通用診斷 log：不同類別的錯誤共用同一份檔案，依 category 加前綴分類，
        方便安內無主控台環境時比對時間序（同一份 log 依時間排序，
        不必跨檔比對多個檔案）。與 _write_typhoon_diag 併存，
        後者維持只給颱風查詢用，不強制合併，降低改動風險。
        """
        try:
            from datetime import datetime
            with open('debug.log', 'a', encoding='utf-8') as f:
                f.write(f"--- {datetime.now().isoformat()} [{category}] ---\n")
                for line in lines:
                    f.write(line + '\n')
        except Exception:
            pass

    def fetch_years(self) -> list[int]:
        """回傳颱風資料庫有資料的年份清單（降冪）。

        R11 修正：改用 warn_conn（外部伺服器 .104/.160）查詢，不再用主連線。
        原因：安內主機上不一定有 typhoon_db 指定的那個 schema；get_thresholds
        走 warn_conn 能正常運作即為證據。warn_conn 未建立時退回 self.conn
        （安外環境本身可能就是 warn_host，此時兩者等效）。
        """
        _conn = self.warn_conn if self.warn_conn is not None else self.conn
        _conn_label = 'warn' if _conn is self.warn_conn else 'main'
        _diag = []
        for col in ('WARN1BEG', 'warnSeaBeg'):
            sql = (
                f"SELECT DISTINCT YEAR({col}) AS YR FROM {self.typhoon_db}.typhoonid "
                f"WHERE {col} IS NOT NULL ORDER BY YR DESC"
            )
            try:
                df = self._query_df(sql, conn=_conn)
                _diag.append(f"[OK] col={col} conn={_conn_label} typhoon_db={self.typhoon_db} rows={len(df)}")
                self._write_typhoon_diag(_diag)
                return df['YR'].dropna().astype(int).tolist()
            except mysql.connector.Error as exc:
                _diag.append(f"[FAIL] col={col} conn={_conn_label} typhoon_db={self.typhoon_db} error={exc}")
                continue
        logger.warning('fetch_years: 無法查詢颱風年份（安內/安外欄位皆失敗）')
        self._write_typhoon_diag(_diag)
        return []

    def fetch_typhoons(self, year: int) -> pd.DataFrame:
        """回傳指定年份颱風清單。

        回傳欄位：ID, CNAME, WARN1BEG, WARN1END, WARN2BEG, WARN2END
        sponsor='LocalTime' 篩選需加 COLLATE utf8mb4_general_ci，
        否則在安外環境會拋 Illegal mix of collations。
        R11 修正：改用 warn_conn 查詢，理由同 fetch_years()。
        """
        _conn = self.warn_conn if self.warn_conn is not None else self.conn
        _conn_label = 'warn' if _conn is self.warn_conn else 'main'
        _diag = []

        # 安內：欄位大寫
        try:
            sql = (
                f"SELECT id AS ID, cname AS CNAME, "
                f"WARN1BEG, WARN1END, WARN2BEG, WARN2END "
                f"FROM {self.typhoon_db}.typhoonid "
                f"WHERE sponsor = 'LocalTime' COLLATE utf8mb4_general_ci "
                f"AND YEAR(WARN1BEG) = %s ORDER BY WARN1BEG"
            )
            df = self._query_df(sql, (year,), conn=_conn)
            _diag.append(f"[OK] 大寫欄位 conn={_conn_label} typhoon_db={self.typhoon_db} year={year} rows={len(df)}")
            self._write_typhoon_diag(_diag)
            return df
        except mysql.connector.Error as exc:
            _diag.append(f"[FAIL] 大寫欄位 conn={_conn_label} typhoon_db={self.typhoon_db} year={year} error={exc}")

        # 安外：欄位小寫（warnSeaBeg / warnLandBeg）
        sql = (
            f"SELECT id AS ID, cname AS CNAME, "
            f"warnSeaBeg AS WARN1BEG, warnSeaEnd AS WARN1END, "
            f"warnLandBeg AS WARN2BEG, warnLandEnd AS WARN2END "
            f"FROM {self.typhoon_db}.typhoonid "
            f"WHERE sponsor = 'LocalTime' COLLATE utf8mb4_general_ci "
            f"AND YEAR(warnSeaBeg) = %s ORDER BY warnSeaBeg"
        )
        try:
            df = self._query_df(sql, (year,), conn=_conn)
            _diag.append(f"[OK] 小寫欄位 conn={_conn_label} typhoon_db={self.typhoon_db} year={year} rows={len(df)}")
            self._write_typhoon_diag(_diag)
            return df
        except mysql.connector.Error as exc:
            _diag.append(f"[FAIL] 小寫欄位 conn={_conn_label} typhoon_db={self.typhoon_db} year={year} error={exc}")
            self._write_typhoon_diag(_diag)
            raise

    # ──────────────────────────────────────────────────────────────────────
    # 水位 — 儀器後設與多儀器查詢
    # ──────────────────────────────────────────────────────────────────────

    def fetch_tide_instruments(self, stid: str) -> pd.DataFrame:
        """查詢 st 表，取 stid 所屬站點的所有水位儀器。

        Step 1：從 st 查出 stid 對應的 stid_obs（連結鍵）。
        Step 2：用 stid_obs 找所有共用該連結鍵的儀器（type 2/3/4）。

        例：stid='1176' → stid_obs='C4N01' → 查 WHERE stid_obs='C4N01'

        回傳欄位：STID, STID_OBS, STID_NEW, TYPE, TYPE_DESC, STNAC, IS_PRIMARY
        """
        ref = self._query_df(
            "SELECT stid_obs AS STID_OBS FROM st WHERE STID = %s LIMIT 1",
            (stid,),
        )
        if ref.empty:
            return pd.DataFrame()
        # stid_obs_val = str(ref['STID_OBS'].iloc[0])
        # Gemini建議的治本寫法：遇到空值直接安全撤退
        # 要觀察一下這樣會不會反而有問題...雖然預期是不會，現在的站都有STID_OBS這個欄位
        val = ref['STID_OBS'].iloc[0]
        if pd.isna(val):
            return pd.DataFrame()
        stid_obs_val = str(val)
        # -------------------------------------------

        sql = (
            "SELECT s.STID, s.stid_obs AS STID_OBS, s.stid_new AS STID_NEW, "
            "s.type AS TYPE, s.stnac AS STNAC, "
            "CASE s.type WHEN 2 THEN '音波式' WHEN 3 THEN '壓力式' WHEN 4 THEN '雷達式' "
            "ELSE '其他' END AS TYPE_DESC, "
            "IF(s.stid_new = s.stid_obs, 1, 0) AS IS_PRIMARY "
            "FROM st s WHERE s.stid_obs = %s AND s.type IN (2, 3, 4)"
        )
        return self._query_df(sql, (stid_obs_val,))

    def query_multi_tide_data(
        self,
        stids: list[str],
        start: date,
        end: date,
    ) -> dict:
        """批量查詢 tide6（觀測）與 tide6ha（預報），QC 分流後 outer merge。

        Parameters
        ----------
        stids : list[str]
            要查詢的潮位站代碼清單。各站會自動查詢所有共用 stid_obs 的儀器。

        回傳 dict：
          wl_data     {instrument_STID: df}
                      欄位：Time, WL_{stid}, QC_{stid}, WL_{stid}_raw, QC_{stid}_raw
          pred_data   {input_STID: df}  欄位：Time, WL_{stid}_pred_h
          pred_data_a {input_STID: df}  欄位：Time, WL_{stid}_pred_a
          tide_meta   {instrument_STID: {type, type_desc, stnac, is_primary,
                                         stid_obs, stid_new}}
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
        result: dict = {'wl_data': {}, 'pred_data': {}, 'pred_data_a': {}, 'tide_meta': {}}

        for stid in stids:
            inst_df = self.fetch_tide_instruments(stid)
            if inst_df.empty:
                logger.debug('query_multi_tide_data: %s 無儀器紀錄，略過', stid)
                continue

            # 填入 tide_meta
            for _, row in inst_df.iterrows():
                r = str(row['STID'])
                result['tide_meta'][r] = {
                    'type': int(row['TYPE']),
                    'type_desc': str(row['TYPE_DESC']),
                    'stnac': str(row['STNAC']),
                    'is_primary': int(row['IS_PRIMARY']),
                    'stid_obs': str(row['STID_OBS']),
                    'stid_new': str(row['STID_NEW']),
                }

            # 觀測值：每個儀器查 tide6，Python 端 QC 分流
            for r_stid in [str(s) for s in inst_df['STID'].tolist()]:
                df_obs = self._query_df(
                    "SELECT DATATIME, MIN0, MIN1, MIN2, MIN3, MIN4, "
                    "MIN5, MIN6, MIN7, MIN8, MIN9, qc AS QC "
                    "FROM tide6 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s",
                    (r_stid, s_str, e_str),
                )
                if df_obs.empty:
                    continue

                # QC 分流：Q（校正值）vs 非 Q（原始機測值）
                df_q = df_obs[df_obs['QC'].str.upper() == 'Q'].copy()
                df_bad = df_obs[df_obs['QC'].str.upper() != 'Q'].copy()

                long_q = self.expand_data(df_q, 'WL', freq='6min')
                long_bad = self.expand_data(df_bad, 'WL', freq='6min')

                long_q = long_q.rename(columns={'WL': f'WL_{r_stid}', 'QC': f'QC_{r_stid}'})
                long_bad = long_bad.rename(columns={
                    'WL': f'WL_{r_stid}_raw',
                    'QC': f'QC_{r_stid}_raw',
                })

                bad_cols = ['Time', f'WL_{r_stid}_raw', f'QC_{r_stid}_raw']
                existing_bad = [c for c in bad_cols if c in long_bad.columns]
                merged = pd.merge(long_q, long_bad[existing_bad], on='Time', how='outer')
                result['wl_data'][r_stid] = merged.sort_values('Time').reset_index(drop=True)

            # 預報值：只查主站 stid 的 tide6ha（QC='h' 諧和 / 'a' 天文潮）
            df_pred = self._query_df(
                "SELECT DATATIME, MIN0, MIN1, MIN2, MIN3, MIN4, "
                "MIN5, MIN6, MIN7, MIN8, MIN9, QC "
                "FROM tide6ha WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s",
                (stid, s_str, e_str),
            )
            if not df_pred.empty:
                for qc_val, out_key, col_sfx in [
                    ('h', 'pred_data', 'pred_h'),
                    ('a', 'pred_data_a', 'pred_a'),
                ]:
                    sub = df_pred[df_pred['QC'].str.lower() == qc_val].copy()
                    if not sub.empty:
                        long = self.expand_data(sub, 'WL', freq='6min')
                        col = f'WL_{stid}_{col_sfx}'
                        long = long.rename(columns={'WL': col})
                        result[out_key][stid] = long[['Time', col]]

        return result

    def fetch_tidal_extrema(
        self,
        stid: str,
        start: date,
        end: date,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """查詢高低潮資料。

        回傳 (tideh_df, tidehl_df)；欄位：DATATIME, HEIGHT, HORL, QC
          tideh  QC='a'（重建分析）或 'h'（調和預報）
          tidehl 只取 QC='Q' 品管通過
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
        tideh = self._query_df(
            "SELECT DATATIME, HEIGHT, HORL, QC FROM tideh "
            "WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s ORDER BY DATATIME",
            (stid, s_str, e_str),
        )
        tidehl = self._query_df(
            "SELECT DATATIME, HEIGHT, HORL, QC FROM tidehl "
            "WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s AND QC = 'Q' ORDER BY DATATIME",
            (stid, s_str, e_str),
        )
        return tideh, tidehl

    # ──────────────────────────────────────────────────────────────────────
    # QC 工作台用：各資料表獨立查詢方法
    # 方法名稱對應 station_registry.REGISTRY[kind]['params'][i]['fetch_method']
    # 回傳欄位全大寫；寬格式（DATATIME, HR0~HR23）由呼叫端視需要呼叫 expand_data()
    # ──────────────────────────────────────────────────────────────────────

    def fetch_wind(
        self,
        stid: str,
        start: date,
        end: date,
        z_filter: str | None = None,
    ) -> pd.DataFrame:
        """查詢 wind 表（風速 VM、陣風 VG、風向 DM）。

        z_filter 傳入 station_registry 的 z_filter 欄位值，例如：
          "Z = '6'"          潮位站
          "Z IN ('2', '3')"  浮標
          None               回傳所有 Z

        回傳欄位：TIME, VM, VG, DM, Z, QC
        VM×0.1=m/s 的轉換由呼叫端（build_buoy_figure 或 _fetch_wind_env）處理。
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
        where = "STID = %s AND TIME >= %s AND TIME < %s"
        if z_filter:
            where += f" AND {z_filter}"
        sql = f"SELECT TIME, VM, VG, DM, Z, qc AS QC FROM wind WHERE {where} ORDER BY TIME, Z"
        return self._query_df(sql, (stid, s_str, e_str))

    def fetch_stemp6(
        self,
        stid: str,
        start: date,
        end: date,
        z: str | None = None,
    ) -> pd.DataFrame:
        """查詢 stemp6 表（潮位站溫度，寬格式 MIN0~MIN9，Z VARCHAR，含 QC）。

        Z: '-3'=氣溫, '0'=海溫
        回傳原始寬格式；呼叫端呼叫 expand_data() 後再進行 QC 分流。

        本次修正：先前版本查詢欄位誤寫成 HR0~HR23（沿用 stemp1 的命名），
        stemp6 實際上是 MIN0~MIN9（與 tide6 同構，見 SYSTEM.md §15／R8 覆核）。
        原寫法執行時會拋 Unknown column 'HR0'，因為目前尚無呼叫端用到，
        沒有被觸發過。
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
        where = "STID = %s AND DATATIME >= %s AND DATATIME < %s"
        params: tuple = (stid, s_str, e_str)
        if z is not None:
            where += " AND Z = %s"
            params = (stid, s_str, e_str, z)
        sql = (
            "SELECT DATATIME, Z, qc AS QC, "
            "MIN0, MIN1, MIN2, MIN3, MIN4, MIN5, MIN6, MIN7, MIN8, MIN9 "
            f"FROM stemp6 WHERE {where} ORDER BY DATATIME, Z"
        )
        return self._query_df(sql, params)
        # sql = (
        #     "SELECT DATATIME, Z, qc AS QC, "
        #     "HR0, HR1, HR2, HR3, HR4, HR5, HR6, HR7, HR8, HR9, HR10, HR11, "
        #     "HR12, HR13, HR14, HR15, HR16, HR17, HR18, HR19, HR20, HR21, HR22, HR23 "
        #     "FROM stemp6 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s ORDER BY DATATIME, Z"
        # )
        # return self._query_df(sql, (stid, s_str, e_str))

    def fetch_pres6(
        self,
        stid: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """查詢 pres6 表（潮位站氣壓，寬格式 MIN0~MIN9，含 QC）。

        MIN 欄位 ×0.1 = hPa；轉換由呼叫端處理。

        本次修正：先前版本查詢欄位誤寫成 HR0~HR23（沿用 pres1 的命名），
        pres6 實際上是 MIN0~MIN9（與 tide6/stemp6 同構，同類問題見 R14
        fetch_stemp6() 修正紀錄）。原寫法執行時會拋 Unknown column 'HR0'，
        因先前沒有呼叫端用到這個方法，沒有被觸發過，這輪新增潮位站氣壓
        QC 頁籤後才踩到。
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
        sql = (
            "SELECT DATATIME, qc AS QC, "
            "MIN0, MIN1, MIN2, MIN3, MIN4, MIN5, MIN6, MIN7, MIN8, MIN9 "
            "FROM pres6 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s ORDER BY DATATIME"
        )
        return self._query_df(sql, (stid, s_str, e_str))

    def fetch_wave1(
        self,
        stid: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """查詢 wave1 表（浮球波浪，直接時間戳 TIME）。

        回傳欄位：TIME, H3, HMAX, T3, TMEAN, QC
        （T3 示性週期、TMEAN 平均週期，單位 0.1 s；2026-11-19 新增，供週期子圖）
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
        sql = (
            "SELECT TIME, H3, HMAX, T3, TMEAN, qc AS QC FROM wave1 "
            "WHERE STID = %s AND TIME >= %s AND TIME < %s ORDER BY TIME"
        )
        return self._query_df(sql, (stid, s_str, e_str))

    def fetch_stemp1(
        self,
        stid: str,
        start: date,
        end: date,
        z: int | None = None,
    ) -> pd.DataFrame:
        """查詢 stemp1 表（浮球/浮標溫度，寬格式 HR0~HR23，Z INT）。

        z: -3=氣溫, 0=海溫計1, 1=海溫計2；None 回傳所有 Z。

        ⚠️  stemp1 是否含 qc 欄位尚待確認（見 SYSTEM.md §6）。
        station_registry 設 has_qc=True，故此處含 qc 欄位查詢。
        若執行時報 Unknown column 'qc'，代表此站 DB schema 無 qc，需更新 station_registry。
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
        where = "STID = %s AND DATATIME >= %s AND DATATIME < %s"
        params: tuple = (stid, s_str, e_str)
        if z is not None:
            where += " AND Z = %s"
            params = (stid, s_str, e_str, z)
        sql = (
            f"SELECT DATATIME, Z, qc AS QC, "
            f"HR0, HR1, HR2, HR3, HR4, HR5, HR6, HR7, HR8, HR9, HR10, HR11, "
            f"HR12, HR13, HR14, HR15, HR16, HR17, HR18, HR19, HR20, HR21, HR22, HR23 "
            f"FROM stemp1 WHERE {where} ORDER BY DATATIME, Z"
        )
        return self._query_df(sql, params)

    def fetch_station_depth(self, stid: str) -> float | None:
        """從站表 st.location 的說明文字取水深（m），例：「…水深約28公尺」→ 28.0。

        2026-11-20 新增，供浮標自動檢查「最大波高 > 0.78×水深」。
        站表只記目前的布放位置；浮標換位置後水深可能不同（例：彭佳嶼 2022 年前約 112 m），
        查歷史資料時以目前水深計算。文字裡找不到水深時回傳 None（該站不做這項檢查）。
        """
        df = self._query_df("SELECT location AS LOC FROM st WHERE STID = %s", (stid,))
        if df.empty or df.iloc[0]['LOC'] is None:
            return None
        m = re.search(r"水深[約為]?\s*'?\s*([0-9]+(?:\.[0-9]+)?)\s*公尺", str(df.iloc[0]['LOC']))
        return float(m.group(1)) if m else None

    def fetch_wave(
        self,
        stid: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """查詢 wave 表（浮標波浪，YEAR/MONTH/DAY/HOUR 格式）。

        跨年時自動納入兩個年份（YEAR IN (y1, y2)）。
        回傳前組合 TIME 欄位並切除範圍外資料。
        回傳欄位：TIME, H, T, TMEAN, HMAX, QC
        （T 尖峰週期、TMEAN 平均週期，單位 0.1 s；T 於 2026-11-19 新增，供週期子圖；
          HMAX 最大波高 cm，2026-11-20 新增，供自動檢查「碎波上限」）
        """
        # years = sorted({start.year, end.year})
        # year_clause = f"YEAR = {years[0]}" if len(years) == 1 else f"YEAR IN ({years[0]}, {years[1]})"
        # Gemini建議的治本寫法：產生連續年份的清單
        years = list(range(start.year, end.year + 1))
        if len(years) == 1:
            year_clause = f"YEAR = {years[0]}"
        else:
            year_clause = f"YEAR IN ({', '.join(map(str, years))})"
        # --------------------------------------
        sql = (
            f"SELECT YEAR, MONTH, DAY, HOUR, H, T, TMEAN, hmax AS HMAX, qc AS QC FROM wave "
            f"WHERE STID = %s AND {year_clause} ORDER BY YEAR, MONTH, DAY, HOUR"
        )
        df = self._query_df(sql, (stid,))
        if df.empty:
            return df

        df['TIME'] = pd.to_datetime({
            'year': df['YEAR'], 'month': df['MONTH'], 'day': df['DAY'], 'hour': df['HOUR'],
        })
        s_ts = pd.Timestamp(start)
        e_ts = pd.Timestamp(end + timedelta(days=1))
        df = df[(df['TIME'] >= s_ts) & (df['TIME'] < e_ts)].reset_index(drop=True)
        return df[['TIME', 'H', 'T', 'TMEAN', 'HMAX', 'QC']]

    def fetch_curr(
        self,
        stid: str,
        start: date,
        end: date,
        z_filter: str | None = None,
    ) -> pd.DataFrame:
        """查詢 curr 表（浮標海流）。

        z_filter 例："Z = '4'"（來自 station_registry，Z='4' 為主要層次）
        回傳欄位：TIME, V, D, Z, QC
        V×0.1=cm/s 的轉換由呼叫端處理。
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
        where = "STID = %s AND TIME >= %s AND TIME < %s"
        if z_filter:
            where += f" AND {z_filter}"
        sql = f"SELECT TIME, V, D, Z, qc AS QC FROM curr WHERE {where} ORDER BY TIME, Z"
        return self._query_df(sql, (stid, s_str, e_str))

    def fetch_pres1(
        self,
        stid: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """查詢 pres1 表（浮標氣壓，寬格式 HR0~HR23，含 QC）。

        HR 欄位 ×0.1 = hPa；轉換由呼叫端處理。

        本次修正：先前版本查詢不含 qc（docstring 誤寫「無 QC 欄位」／
        has_qc=False，DDL 已確認 pres1 實際有 qc 欄位，與其他浮標表一致）。
        原寫法因為 SELECT 沒帶 qc，fetch_bundle() 內 _expand() 的防呆邏輯
        （展開後缺 QC 欄位時補 df['QC']=''）會讓整批資料的 QC 變成空字串，
        本輪新增的非Q紅叉判斷（_non_q_mask）因此把全部資料點誤判成異常。
        
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')
        sql = (
            "SELECT DATATIME, qc AS QC,  "
            "HR0, HR1, HR2, HR3, HR4, HR5, HR6, HR7, HR8, HR9, HR10, HR11, "
            "HR12, HR13, HR14, HR15, HR16, HR17, HR18, HR19, HR20, HR21, HR22, HR23 "
            "FROM pres1 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s ORDER BY DATATIME"
        )
        return self._query_df(sql, (stid, s_str, e_str))

    # ──────────────────────────────────────────────────────────────────────
    # 颱風警戒門檻值（warn 連線，med.p_setting_warn）
    # ──────────────────────────────────────────────────────────────────────

    def get_thresholds(self, stid: str) -> dict | None:
        """查詢 med.p_setting_warn（大潮注意值 STIDE、暴潮警戒值 WARNVAL）。

        使用 self.warn_conn（外部伺服器 .104/.160）。
        warn_conn 未建立（安內環境）或查無資料時回傳 None，不拋例外。
        回傳 {'stide_mm': int, 'warnval_mm': int}；DB 儲存單位 m，×1000 換算 mm。
        """
        if self.warn_conn is None:
            return None
        try:
            df = self._query_df(
                "SELECT STIDE, WARNVAL FROM p_setting_warn WHERE STID = %s LIMIT 1",
                (stid,),
                conn=self.warn_conn,
            )
            if df.empty:
                return None
            row = df.iloc[0]
            return {
                'stide_mm': int(float(row['STIDE']) * 1000),
                'warnval_mm': int(float(row['WARNVAL']) * 1000),
            }
        except mysql.connector.Error as exc:
            logger.warning('get_thresholds(%s) 失敗：%s', stid, exc)
            return None

    # ──────────────────────────────────────────────────────────────────────
    # fetch_bundle：診斷總覽 / 事件報告主查詢入口
    # ──────────────────────────────────────────────────────────────────────

    def fetch_bundle(self, stid: str, start: date, end: date) -> dict:
        """整合水位、環境與海洋資料，回傳 bundle dict。

        完整流程見 SYSTEM.md §9-3（Step 1–6）。

        回傳 dict 鍵：
          stid, stname, df, src_ids, src_names,
          tide_meta, mr_full, mr_month, tideh_df, tidehl_df
        df 欄位以 'Time'（title case）為時間軸，與 build_* 繪圖模組介面一致。
        """
        s_str = start.strftime('%Y-%m-%d')
        e_str = (end + timedelta(days=1)).strftime('%Y-%m-%d')

        mr_full: float | None = None
        mr_month: float | None = None
        src_ids: dict[str, str] = {}

        # ── Step 1：候選站名單（QCID 優先，自身墊底，去重保序） ────────
        row = self.mapping_df[self.mapping_df['STID'] == stid]
        if not row.empty and 'QCID' in row.columns:
            q_ids = [q for q in row['QCID'].dropna().tolist() if q != stid]
        else:
            q_ids = []

        seen: set[str] = set()
        candidates: list[str] = []
        for c in q_ids + [stid]:
            if c not in seen:
                seen.add(c)
                candidates.append(c)

        if candidates:
            ph = ', '.join(['%s'] * len(candidates))
            cand_df = self._query_df(
                f"SELECT STID, kind AS KIND, stnac AS CNAME FROM st WHERE STID IN ({ph})",
                tuple(candidates),
            )
            cand_df['KIND'] = cand_df['KIND'].astype(str)
            cand_kind = dict(zip(cand_df['STID'], cand_df['KIND']))
            cand_name = dict(zip(cand_df['STID'], cand_df['CNAME']))
        else:
            cand_kind = {}
            cand_name = {}

        stname = cand_name.get(stid, stid)

        # ── Step 2：多儀器水位查詢 ───────────────────────────────────────
        tide_result = self.query_multi_tide_data([stid], start, end)
        tide_meta = tide_result['tide_meta']
        wl_data = tide_result['wl_data']
        pred_data = tide_result['pred_data']
        pred_data_a = tide_result['pred_data_a']

        # 找主測站（is_primary=1）
        primary_stid = stid
        for s, meta in tide_meta.items():
            if meta.get('is_primary') == 1:
                primary_stid = s
                break

        if wl_data:
            # 新系統：outer merge 各儀器水位欄位
            main: pd.DataFrame | None = None
            for _, df_wl in wl_data.items():
                if main is None:
                    main = df_wl.copy()
                else:
                    new_cols = [c for c in df_wl.columns if c != 'Time']
                    main = pd.merge(main, df_wl[['Time'] + new_cols], on='Time', how='outer')

            if main is None:
                main = pd.DataFrame(columns=['Time'])

            for pred_dict in (pred_data, pred_data_a):
                if stid in pred_dict:
                    main = pd.merge(main, pred_dict[stid], on='Time', how='outer')

            main = main.sort_values('Time').reset_index(drop=True)
        else:
            # 舊系統降級：單儀器，Obs / Pre / Resi
            df_obs = self._query_df(
                "SELECT DATATIME, MIN0, MIN1, MIN2, MIN3, MIN4, "
                "MIN5, MIN6, MIN7, MIN8, MIN9, qc AS QC "
                "FROM tide6 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s",
                (stid, s_str, e_str),
            )
            if not df_obs.empty:
                obs_long = self.expand_data(df_obs, 'Obs', freq='6min')
                df_pred = self._query_df(
                    "SELECT DATATIME, MIN0, MIN1, MIN2, MIN3, MIN4, "
                    "MIN5, MIN6, MIN7, MIN8, MIN9, QC "
                    "FROM tide6ha WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s AND QC = 'h'",
                    (stid, s_str, e_str),
                )
                if not df_pred.empty:
                    pred_long = self.expand_data(df_pred, 'Pre', freq='6min')
                    main = pd.merge(obs_long[['Time', 'Obs']], pred_long[['Time', 'Pre']], on='Time', how='outer')
                    main['Resi'] = main['Obs'] - main['Pre']
                else:
                    main = obs_long[['Time', 'Obs']].copy()
            else:
                main = pd.DataFrame(columns=['Time'])

        # ── Step 3：衍生欄位（僅主測站） ─────────────────────────────────
        wl_col = f'WL_{primary_stid}'
        if wl_col in main.columns:
            pred_h = f'WL_{primary_stid}_pred_h'
            pred_a = f'WL_{primary_stid}_pred_a'
            if pred_h in main.columns:
                main['Resi'] = main[wl_col] - main[pred_h]
            elif pred_a in main.columns:
                main['Resi'] = main[wl_col] - main[pred_a]

            # 25h 中心移動平均（低頻趨勢，window=250 點 × 6min = 1500min ≈ 25h）
            main[f'{wl_col}_lf'] = (
                main[wl_col].rolling(window=250, center=True, min_periods=125).mean()
            )
            # EWMA（α=0.05）
            main[f'{wl_col}_ewma'] = main[wl_col].ewm(alpha=0.05, ignore_na=True).mean()

            # 儀器差值
            for o_stid in wl_data:
                if o_stid != primary_stid:
                    o_col = f'WL_{o_stid}'
                    if o_col in main.columns:
                        main[f'Diff_{primary_stid}_{o_stid}'] = main[wl_col] - main[o_col]

            # 平均潮差（供 Resi_Norm）
            mr_full, mr_month = self._fetch_mr(stid, start.year, start.month)
            if mr_full is not None and mr_full > 0 and 'Resi' in main.columns:
                main['Resi_Norm'] = main['Resi'] / mr_full * 100

        # ── Step 4：環境資料（candidates 依序，先到先得） ─────────────────
        has_p = has_w = has_at = has_wt = False

        for c_id in candidates:
            if has_p and has_w and has_at and has_wt:
                break
            c_kind = cand_kind.get(c_id, '')

            # 氣象站（kind 1/2/3）
            if c_kind in ('1', '2', '3'):
                met = self._fetch_meteo(c_id, s_str, e_str)
                if met is not None and not met.empty:
                    if not has_p and 'P' in met.columns:
                        main = pd.merge(main, met[['Time', 'P']], on='Time', how='outer')
                        src_ids['P'] = c_id
                        has_p = True
                    if not has_w and 'WS' in met.columns:
                        main = pd.merge(main, met[['Time', 'WS', 'WD']], on='Time', how='outer')
                        src_ids['WS'] = c_id
                        has_w = True
                    if not has_at and 'AT' in met.columns:
                        main = pd.merge(main, met[['Time', 'AT']], on='Time', how='outer')
                        src_ids['AT'] = c_id
                        has_at = True

            # 潮位站（kind=6）
            elif c_kind == '6':
                if not has_p:
                    p_df = self._fetch_pres6_env(c_id, s_str, e_str)
                    if p_df is not None and not p_df.empty:
                        main = pd.merge(main, p_df, on='Time', how='outer')
                        src_ids['P'] = c_id
                        has_p = True
                if not has_w:
                    w_df = self._fetch_wind_env(c_id, s_str, e_str, z_kind='6')
                    if w_df is not None and not w_df.empty:
                        main = pd.merge(main, w_df, on='Time', how='outer')
                        src_ids['WS'] = c_id
                        has_w = True
                if not has_at:
                    at_df = self._fetch_stemp6_env(c_id, s_str, e_str, z_val='-3', out_col='AT')
                    if at_df is not None and not at_df.empty:
                        main = pd.merge(main, at_df, on='Time', how='outer')
                        src_ids['AT'] = c_id
                        has_at = True
                if not has_wt:
                    wt_df = self._fetch_stemp6_env(c_id, s_str, e_str, z_val='0', out_col='WT')
                    if wt_df is not None and not wt_df.empty:
                        main = pd.merge(main, wt_df, on='Time', how='outer')
                        src_ids['WT'] = c_id
                        has_wt = True

            # 浮球、浮標（kind=7、8）
            # elif c_kind in ('7', '8'):
            # 浮標（kind=8）
            elif c_kind == '8':
                if not has_p:
                    p_df = self._fetch_pres1_env(c_id, s_str, e_str)
                    if p_df is not None and not p_df.empty:
                        main = pd.merge(main, p_df, on='Time', how='outer')
                        src_ids['P'] = c_id
                        has_p = True
                if not has_w:
                    w_df = self._fetch_wind_env(c_id, s_str, e_str, z_kind='8')
                    if w_df is not None and not w_df.empty:
                        main = pd.merge(main, w_df, on='Time', how='outer')
                        src_ids['WS'] = c_id
                        has_w = True
                if not has_at:
                    at_df = self._fetch_stemp1_env(c_id, s_str, e_str, z_val=-3, out_col='AT')
                    if at_df is not None and not at_df.empty:
                        main = pd.merge(main, at_df, on='Time', how='outer')
                        src_ids['AT'] = c_id
                        has_at = True
                if not has_wt:
                    wt_df = self._fetch_stemp1_env(c_id, s_str, e_str, z_val=0, out_col='WT')
                    if wt_df is not None and not wt_df.empty:
                        main = pd.merge(main, wt_df, on='Time', how='outer')
                        src_ids['WT'] = c_id
                        has_wt = True

        # ── Step 5：海洋資料（第一個 kind=8 候選） ───────────────────────
        # buoy_id = next((c for c in candidates if cand_kind.get(c) in ('7', '8')), None)
        buoy_id = next((c for c in candidates if cand_kind.get(c) == '8'), None)
        if buoy_id is not None:
            wave_df = self.fetch_wave(buoy_id, start, end)
            if not wave_df.empty:
                wave_df = wave_df.rename(columns={'TIME': 'Time'}).copy()
                wave_df['H_m'] = wave_df['H'] * 0.01
                wave_df['T_sec'] = wave_df['TMEAN'] * 0.1
                main = pd.merge(main, wave_df[['Time', 'H_m', 'T_sec']], on='Time', how='outer')
                src_ids['H_m'] = buoy_id

            curr_df = self.fetch_curr(buoy_id, start, end, z_filter="Z = '4'")
            if not curr_df.empty:
                curr_df = curr_df.rename(columns={'TIME': 'Time', 'D': 'DIR'}).copy()
                curr_df['V'] = curr_df['V'] * 0.1
                main = pd.merge(main, curr_df[['Time', 'V', 'DIR']], on='Time', how='outer')
                src_ids['V'] = buoy_id

        # ── Step 6：整理、切除超出範圍、異常值過濾 ──────────────────────
        if 'Time' in main.columns and not main.empty:
            main = main.sort_values('Time').reset_index(drop=True)
            s_ts = pd.Timestamp(start)
            e_ts = pd.Timestamp(end + timedelta(days=1))
            main = main[(main['Time'] >= s_ts) & (main['Time'] < e_ts)].reset_index(drop=True)

            # AT / WT 超出合理範圍（>40°C 或 ≤-10°C）設 NaN
            for col in ('AT', 'WT'):
                if col in main.columns:
                    main.loc[(main[col] > 40) | (main[col] <= -10), col] = float('nan')

        tideh_df, tidehl_df = self.fetch_tidal_extrema(stid, start, end)
        src_names = {param: cand_name.get(sid, sid) for param, sid in src_ids.items()}

        # ── 浮標感測器（kind=7 浮球 / kind=8 浮標）────────────────────────────────
        def _sf(m, *a, **kw):
            """安全 fetch：例外時回傳 None，不中斷 bundle 組裝。"""
            try:
                return m(*a, **kw)
            except Exception as _e:
                msg = f"[_sf] {m.__name__} stid={a[0] if a else '?'} kwargs={kw}: {_e}"
                print(f"DEBUG {msg}")
                self._write_debug_log('fetch_bundle', [msg])
                return None

        def _expand(raw_df, val_name, freq='1h'):
            """寬格式（HR0~HR23 或 MIN0~MIN9，取決於 freq） → 長格式 TIME + val_name。
            expand_data 回傳欄名 Time（title case），
            此處統一改為 TIME 對齊 build_buoy_figure.py 介面。
            freq='1h'（預設，stemp1/pres1）／freq='6min'（stemp6/pres6）。"""
            if raw_df is None or raw_df.empty:
                return None
            df = self.expand_data(raw_df, val_name, freq=freq)
            # if df is not None and 'Time' in df.columns:
            #     df = df.rename(columns={'Time': 'TIME'})
            # return df
            if df is not None and 'Time' in df.columns:
                df = df.rename(columns={'Time': 'TIME'})
            # ⚠️ 這裡補的 QC='' 曾經造成 bug：非Q紅叉判斷把空字串一律視為
            # 「非Q」，若來源表其實有 qc 只是查詢時沒選到（例：先前的
            # fetch_pres1()），會讓全部資料點被誤判成異常。目前 wave1/wind/
            # curr/wave/pres1/stemp1/stemp6/pres6 對應的 fetch_* 方法都已
            # 確認會查詢真正的 qc，這個分支理論上不會再觸發，保留純粹是
            # 給未來萬一真的新增無 QC 資料表時的防呆。
            if df is not None and 'QC' not in df.columns:
                df['QC'] = ''
            return df

        _buoy_dfs: dict = {}
        _kind = cand_kind.get(stid, '')
        if _kind in ('7', '8'):
            _buoy_dfs['wave1_df'] = _sf(self.fetch_wave1, stid, start, end)
            _raw_c = _sf(self.fetch_curr, stid, start, end)
            print(f"DEBUG curr {stid}: {None if _raw_c is None else len(_raw_c)} rows")
            if _kind == '8':
                _buoy_dfs['wind_df']  = _sf(self.fetch_wind,  stid, start, end)
                _buoy_dfs['curr_df']  = _sf(self.fetch_curr,  stid, start, end)
                _buoy_dfs['wave_df']  = _sf(self.fetch_wave,  stid, start, end)
                _raw_p = _sf(self.fetch_pres1, stid, start, end)
                _buoy_dfs['pres1_df'] = _expand(_raw_p, 'P')
            _st_list = []
            for _z in (-3, 0, 1):
                _raw_s = _sf(self.fetch_stemp1, stid, start, end, z=_z)
                _df = _expand(_raw_s, 'T')
                if _df is not None and not _df.empty:
                    _st_list.append((_df, _z))
            _buoy_dfs['stemp1_dfs'] = _st_list
            # 2026-11-20：站表 location 文字裡的水深（m），供自動檢查「碎波上限」；查不到為 None
            _buoy_dfs['depth_m'] = _sf(self.fetch_station_depth, stid)
        elif _kind == '6':
            # 潮位站環境參數 QC 用原始資料（wind Z='6' + stemp6 Z='0'）。
            # 沿用 _buoy_dfs 這個變數名稱一起回傳（**_buoy_dfs 已在 return 處展開），
            # 命名上現在同時涵蓋浮標與潮位站環境資料，不另外改 return 語句。
            _buoy_dfs['wind_z6_df'] = _sf(self.fetch_wind, stid, start, end, z_filter="Z = '6'")
            _raw_st6 = _sf(self.fetch_stemp6, stid, start, end, z='0')
            _buoy_dfs['stemp6_z0_df'] = _expand(_raw_st6, 'T', freq='6min')
            _raw_st6_air = _sf(self.fetch_stemp6, stid, start, end, z='-3')
            _buoy_dfs['stemp6_zm3_df'] = _expand(_raw_st6_air, 'T', freq='6min')
            _raw_p6 = _sf(self.fetch_pres6, stid, start, end)
            _buoy_dfs['pres6_df'] = _expand(_raw_p6, 'P', freq='6min')
        # def _sf(m, *a, **kw):
        #     """安全 fetch：例外時回傳 None，不中斷 bundle 組裝。"""
        #     try:
        #         return m(*a, **kw)
        #     except Exception:
        #         return None

        # _buoy_dfs: dict = {}
        # _kind = cand_kind.get(stid, '')
        # if _kind in ('7', '8'):
        #     _buoy_dfs['wave1_df'] = _sf(self.fetch_wave1, stid, start, end)
        #     if _kind == '8':
        #         _buoy_dfs['wind_df']  = _sf(self.fetch_wind,  stid, start, end)
        #         _buoy_dfs['curr_df']  = _sf(self.fetch_curr,  stid, start, end)
        #         _buoy_dfs['wave_df']  = _sf(self.fetch_wave,  stid, start, end)
        #         _buoy_dfs['pres1_df'] = _sf(self.fetch_pres1, stid, start, end)
        #     _st_list = []
        #     for _z in (-3, 0, 1):
        #         _df = _sf(self.fetch_stemp1, stid, start, end, z=_z)
        #         if _df is not None and not _df.empty:
        #             _st_list.append((_df, _z))
        #     _buoy_dfs['stemp1_dfs'] = _st_list

        return {
            'stid': stid,
            'stname': stname,
            'df': main,
            'src_ids': src_ids,
            'src_names': src_names,
            'tide_meta': tide_meta,
            'mr_full': mr_full,
            'mr_month': mr_month,
            'tideh_df': tideh_df,
            'tidehl_df': tidehl_df,
            **_buoy_dfs,
        }
        #     'tidehl_df': tidehl_df,
        # }

    # ──────────────────────────────────────────────────────────────────────
    # fetch_bundle 私用輔助
    # ──────────────────────────────────────────────────────────────────────

    def _fetch_mr(self, stid: str, year: int, month: int) -> tuple[float | None, float | None]:
        """查 tidestat 取全年（MONTH=0）與當月平均潮差 MR。"""
        try:
            df = self._query_df(
                "SELECT MONTH, MR FROM tidestat "
                "WHERE STID = %s AND YEAR = %s AND SL = 'S' AND QC = 'Q' AND MONTH IN (0, %s)",
                (stid, year, month),
            )
            if df.empty:
                return None, None
            mr_full = float(df.loc[df['MONTH'] == 0, 'MR'].iloc[0]) if (df['MONTH'] == 0).any() else None
            mr_month = float(df.loc[df['MONTH'] == month, 'MR'].iloc[0]) if (df['MONTH'] == month).any() else None
            return mr_full, mr_month
        except Exception as exc:
            logger.warning('_fetch_mr(%s, %d, %d) 失敗：%s', stid, year, month, exc)
            return None, None

    def _fetch_meteo(self, stid: str, s_str: str, e_str: str) -> pd.DataFrame | None:
        """查 meteo 整點值（min=0），回傳 Time/P/WS/WD/AT（已轉換單位）。"""
        try:
            df = self._query_df(
                "SELECT DATATIME, p * 0.1 AS P, ws * 0.1 AS WS, wd AS WD, t * 0.1 AS AT "
                "FROM meteo WHERE stid = %s AND DATATIME >= %s AND DATATIME < %s AND min = 0 "
                "ORDER BY DATATIME",
                (stid, s_str, e_str),
            )
            if df.empty:
                return None
            df = df.rename(columns={'DATATIME': 'Time'})
            df['Time'] = pd.to_datetime(df['Time'])
            return df
        except Exception as exc:
            logger.warning('_fetch_meteo(%s) 失敗：%s', stid, exc)
            return None

    def _fetch_pres6_env(self, stid: str, s_str: str, e_str: str) -> pd.DataFrame | None:
        """查 pres6，QC='Q' 展開，回傳 Time/P（×0.1 hPa）。"""
        try:
            # df = self._query_df(
            #     "SELECT DATATIME, qc AS QC, "
            #     "HR0, HR1, HR2, HR3, HR4, HR5, HR6, HR7, HR8, HR9, HR10, HR11, "
            #     "HR12, HR13, HR14, HR15, HR16, HR17, HR18, HR19, HR20, HR21, HR22, HR23 "
            #     "FROM pres6 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s",
            # 新 SQL
            df = self._query_df(
                "SELECT * FROM pres6 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s",
                (stid, s_str, e_str),
            )
            if df.empty:
                return None
            df_q = df[df['QC'].str.upper() == 'Q'].copy()
            if df_q.empty:
                return None
            # long = self.expand_data(df_q, 'P', freq='1h')
            long = self.expand_data(df_q, 'P', freq='6min')
            long['P'] = long['P'] * 0.1
            return long[['Time', 'P']]
        except Exception as exc:
            logger.warning('_fetch_pres6_env(%s) 失敗：%s', stid, exc)
            return None

    def _fetch_pres1_env(self, stid: str, s_str: str, e_str: str) -> pd.DataFrame | None:
        """查 pres1，展開，回傳 Time/P（×0.1 hPa）。"""
        try:
            df = self._query_df(
                "SELECT DATATIME, "
                "HR0, HR1, HR2, HR3, HR4, HR5, HR6, HR7, HR8, HR9, HR10, HR11, "
                "HR12, HR13, HR14, HR15, HR16, HR17, HR18, HR19, HR20, HR21, HR22, HR23 "
                "FROM pres1 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s",
                (stid, s_str, e_str),
            )
            if df.empty:
                return None
            long = self.expand_data(df, 'P', freq='1h')
            long['P'] = long['P'] * 0.1
            return long[['Time', 'P']]
        except Exception as exc:
            logger.warning('_fetch_pres1_env(%s) 失敗：%s', stid, exc)
            return None

    def _fetch_wind_env(
        self, stid: str, s_str: str, e_str: str, z_kind: str
    ) -> pd.DataFrame | None:
        """查 wind，QC 分流，回傳 Time/WS/WD/WS_raw（WS 已 ×0.1 m/s）。

        z_kind: '6' 潮位站（Z='6'）; '8' 浮標（Z IN ('2','3')，取最小 Z）
        """
        try:
            z_clause = "Z = '6'" if z_kind == '6' else "Z IN ('2', '3')"
            df = self._query_df(
                f"SELECT TIME, VM, DM, Z, qc AS QC FROM wind "
                f"WHERE STID = %s AND TIME >= %s AND TIME < %s AND {z_clause} "
                f"ORDER BY TIME, Z",
                (stid, s_str, e_str),
            )
            if df.empty:
                return None

            # 浮標：同一時刻取最小 Z 值的量測
            if z_kind == '8':
                df = df.sort_values('Z').drop_duplicates(subset=['TIME'], keep='first')

            df_q = df[df['QC'].str.upper() == 'Q'].copy()
            df_bad = df[df['QC'].str.upper() != 'Q'].copy()

            df_q = df_q.rename(columns={'TIME': 'Time', 'VM': 'WS', 'DM': 'WD'})
            df_q['WS'] = df_q['WS'] * 0.1
            df_q = df_q[['Time', 'WS', 'WD']].reset_index(drop=True)

            if not df_bad.empty:
                df_bad = df_bad.rename(columns={'TIME': 'Time', 'VM': 'WS_raw'})
                df_bad['WS_raw'] = df_bad['WS_raw'] * 0.1
                return pd.merge(df_q, df_bad[['Time', 'WS_raw']], on='Time', how='outer')
            else:
                df_q['WS_raw'] = float('nan')
                return df_q
        except Exception as exc:
            logger.warning('_fetch_wind_env(%s, z_kind=%s) 失敗：%s', stid, z_kind, exc)
            return None

    def _fetch_stemp6_env(
        self, stid: str, s_str: str, e_str: str, z_val: str, out_col: str
    ) -> pd.DataFrame | None:
        """查 stemp6（指定 Z VARCHAR），QC 分流，回傳 Time/{out_col}/{out_col}_raw（×0.1 °C）。"""
        try:
            # df = self._query_df(
            #     "SELECT DATATIME, qc AS QC, "
            #     "HR0, HR1, HR2, HR3, HR4, HR5, HR6, HR7, HR8, HR9, HR10, HR11, "
            #     "HR12, HR13, HR14, HR15, HR16, HR17, HR18, HR19, HR20, HR21, HR22, HR23 "
            #     "FROM stemp6 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s AND Z = %s",
            #     (stid, s_str, e_str, z_val),
            # )
            df = self._query_df(
                "SELECT * FROM stemp6 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s AND Z = %s",
                (stid, s_str, e_str, z_val),
            )
            if df.empty:
                return None

            df_q = df[df['QC'].str.upper() == 'Q'].copy()
            df_bad = df[df['QC'].str.upper() != 'Q'].copy()

            # long_q = self.expand_data(df_q, out_col, freq='1h')
            long_q = self.expand_data(df_q, out_col, freq='6min')
            long_q[out_col] = long_q[out_col] * 0.1

            if not df_bad.empty:
                long_bad = self.expand_data(df_bad, f'{out_col}_raw', freq='6min')
                long_bad[f'{out_col}_raw'] = long_bad[f'{out_col}_raw'] * 0.1
                return pd.merge(
                    long_q[['Time', out_col]],
                    long_bad[['Time', f'{out_col}_raw']],
                    on='Time', how='outer',
                )
            else:
                result = long_q[['Time', out_col]].copy()
                result[f'{out_col}_raw'] = float('nan')
                return result
        except Exception as exc:
            logger.warning('_fetch_stemp6_env(%s, z=%s) 失敗：%s', stid, z_val, exc)
            return None

    def _fetch_stemp1_env(
        self, stid: str, s_str: str, e_str: str, z_val: int, out_col: str
    ) -> pd.DataFrame | None:
        """查 stemp1（指定 Z INT），展開，回傳 Time/{out_col}（×0.1 °C）。"""
        try:
            df = self._query_df(
                "SELECT DATATIME, Z, "
                "HR0, HR1, HR2, HR3, HR4, HR5, HR6, HR7, HR8, HR9, HR10, HR11, "
                "HR12, HR13, HR14, HR15, HR16, HR17, HR18, HR19, HR20, HR21, HR22, HR23 "
                "FROM stemp1 WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s AND Z = %s",
                (stid, s_str, e_str, z_val),
            )
            if df.empty:
                return None
            long = self.expand_data(df, out_col, freq='1h')
            long[out_col] = long[out_col] * 0.1
            return long[['Time', out_col]]
        except Exception as exc:
            logger.warning('_fetch_stemp1_env(%s, z=%s) 失敗：%s', stid, z_val, exc)
            return None
