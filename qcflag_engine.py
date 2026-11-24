"""
qcflag_engine.py
================
多采潮位檢核旗標（唯讀）的資料讀取與整理（2026-11-20 新增）。

來源：檢核提列測試專案（`...\\2026多采\\檢核提列測試\\`）寫在本機 test_db 的旗標表；
本檔的整理邏輯參考該專案 `qc_viewer.py`（2026-11-20 版）的 load()／hourly()／flag_layout()／
row_name()，**複製改寫、不 import 該專案**（打包後的 exe 拿不到它）。旗標表結構、代碼語意或
子項欄位改了，要回頭對這裡的 SUB_COLS、NAME_ZH。

讀的表（全部 SELECT，不寫入）：
    tide6_qc          逐 6 分鐘子項旗標＋彙總 qc_summary（1 通過、2 未檢測、3 可疑、4 不通過、9 缺漏）
    tide6_qc_dc       多采模式（dc_*，比較用，勾選才讀）
    qc_datum_event    基準偏移事件（斷點時間、偏移量、參考來源、偵測方式）
    tide6_nearby_est  鄰站推估值與門檻（同址各儀器共用主儀器的模型）
    qc_test_def       各子項判定方式（頁面上的說明）

連線（.env，和登入畫面的主資料庫分開；沒設定時頁籤只顯示提示，不影響啟動與其他頁籤）：
    QC_DB_HOST、QC_DB_PORT（預設 3306）、QC_DB_USER、QC_DB_PASS、QC_DB_NAME

觀測水位、天文潮、舊 qc 不從旗標庫讀，沿用本次查詢的 bundle（主資料庫）。

純函式＋DataFrame：不 import Dash／Plotly（資料整理和畫圖分開，§13 設計原則）。
"""

import os

import pandas as pd

ENV_KEYS = ("QC_DB_HOST", "QC_DB_USER", "QC_DB_PASS", "QC_DB_NAME")   # QC_DB_PORT 可省略
CONNECT_TIMEOUT = 5          # 秒；連不上時不要讓頁面卡住
HOURLY_OVER_DAYS = 62        # 超過這個天數改逐時（旗標取該小時最嚴重者），同 qc_viewer

# 子項（順序同 qc_viewer 的 QC_COLS）與中文名
SUB_COLS = ["qc_range", "qc_spike", "qc_tide", "qc_dca", "qc_flat", "qc_datum",
            "qc_same_site", "qc_nearby", "qc_site_offset", "qc_time"]
DC_COLS = ["dc_range", "dc_spike", "dc_tide", "dc_dca"]
NAME_ZH = {"qc_range": "上下限", "qc_spike": "突波", "qc_tide": "天文潮差", "qc_dca": "誤差衰退 DCA",
           "qc_flat": "卡值", "qc_datum": "基準偏移", "qc_same_site": "同址儀器比對",
           "qc_nearby": "鄰站推估", "qc_site_offset": "同址偏移期間", "qc_time": "時間檢測",
           "qc_summary": "新規則彙總"}
OLD_QC_ROW = "舊人工 qc（tide6.qc 非 Q）"
CODE_LABEL = {1: "通過", 2: "未檢測", 3: "可疑", 4: "不通過", 9: "缺漏"}
SEVERITY = {1: 0, 2: 1, 9: 2, 3: 3, 4: 4}

SOURCE_LABEL = {"same_site": "同址儀器", "nearby": "鄰站", "tide": "天文潮", "satellite": "衛星測高（月）"}
SOURCE_OF = {"pair_diff": "same_site", "meanlevel_adjust": "tide", "vote": "nearby", "alt_tg": "satellite"}
WAY_LABEL = {"pair_diff": "差值階梯", "vote": "日平均三法投票", "meanlevel_adjust": "大偏移擬合",
             "alt_tg": "衛星月差值"}
EVENT_JOIN = pd.Timedelta(days=1)   # 斷點相隔 ≤ 1 天視為同一事件（同 qc_datum 計票的 ±1 天）


# ── 連線 ──────────────────────────────────────────────────────────────────

def missing_config() -> list:
    """缺少的 .env 變數名；空 list 表示設定齊全。"""
    return [k for k in ENV_KEYS if not os.getenv(k)]


def connect():
    """依 .env 連旗標庫。設定不齊或連不上時丟例外（呼叫端轉成頁面提示）。"""
    miss = missing_config()
    if miss:
        raise RuntimeError("未設定：" + "、".join(miss))
    import mysql.connector
    return mysql.connector.connect(
        host=os.getenv("QC_DB_HOST"),
        port=int(os.getenv("QC_DB_PORT", "3306") or 3306),
        user=os.getenv("QC_DB_USER"),
        password=os.getenv("QC_DB_PASS"),
        database=os.getenv("QC_DB_NAME"),
        charset=33,                      # 3-byte UTF-8（8.0 叫 utf8mb3、5.7 叫 utf8）
        connection_timeout=CONNECT_TIMEOUT,
    )


def _query(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
    finally:
        cur.close()
    return pd.DataFrame(rows, columns=cols)


# ── 讀取 ──────────────────────────────────────────────────────────────────

def stids_with_flags(conn, stids, beg, end) -> list:
    """stids 中在 [beg, end) 有 tide6_qc 資料的儀器。"""
    stids = [str(s) for s in stids]
    if not stids:
        return []
    out = []
    for s in stids:   # 每台各查一次 LIMIT 1，走 PK，比 GROUP BY 掃整段快
        df = _query(conn, "SELECT 1 FROM tide6_qc WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s LIMIT 1",
                    (s, beg, end))
        if not df.empty:
            out.append(s)
    return out


def flag_range(conn, stid):
    """這台儀器有旗標的最早、最晚時間；沒有任何旗標時 None。"""
    df = _query(conn, "SELECT MIN(DATATIME), MAX(DATATIME) FROM tide6_qc WHERE STID = %s", (str(stid),))
    if df.empty or df.iloc[0, 0] is None:
        return None
    return pd.Timestamp(df.iloc[0, 0]), pd.Timestamp(df.iloc[0, 1])


def load_flags(conn, stid, beg, end, with_dc=False) -> pd.DataFrame:
    """逐 6 分鐘旗標：TIME＋SUB_COLS＋qc_summary（＋DC_COLS）。"""
    cols = ", ".join(f"q.{c}" for c in SUB_COLS)
    if with_dc:
        sql = (f"SELECT q.DATATIME AS TIME, {cols}, q.qc_summary, "
               + ", ".join(f"d.{c}" for c in DC_COLS)
               + " FROM tide6_qc q LEFT JOIN tide6_qc_dc d ON d.STID = q.STID AND d.DATATIME = q.DATATIME"
                 " WHERE q.STID = %s AND q.DATATIME >= %s AND q.DATATIME < %s ORDER BY q.DATATIME")
    else:
        sql = (f"SELECT q.DATATIME AS TIME, {cols}, q.qc_summary FROM tide6_qc q"
               " WHERE q.STID = %s AND q.DATATIME >= %s AND q.DATATIME < %s ORDER BY q.DATATIME")
    df = _query(conn, sql, (stid, beg, end))
    if df.empty:
        return df
    df["TIME"] = pd.to_datetime(df["TIME"])
    for c in df.columns:
        if c != "TIME":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_events(conn, stid, beg, end) -> pd.DataFrame:
    """[beg − 1 天, end + 1 天) 內的基準偏移事件（前後多 1 天，畫面邊緣的事件才分得出同組）。"""
    lo = pd.Timestamp(beg) - EVENT_JOIN
    hi = pd.Timestamp(end) + EVENT_JOIN
    df = _query(conn, "SELECT break_time, offset_mm, method, ref_source, ref_stid, tideshift_sn, note"
                      " FROM qc_datum_event WHERE STID = %s AND break_time >= %s AND break_time < %s"
                      " ORDER BY break_time", (stid, lo, hi))
    if df.empty:
        return df
    df["break_time"] = pd.to_datetime(df["break_time"])
    df["ref_source"] = df["ref_source"].fillna(df["method"].map(SOURCE_OF))
    return df


def load_nearby_est(conn, candidates, beg, end):
    """鄰站推估：依 candidates 順序找第一台在期間內有推估值的儀器（同址各台共用主儀器的模型）。
    回傳 (模型儀器, DataFrame[TIME, est, thr])；都沒有時 (None, 空表)。"""
    for s in candidates:
        df = _query(conn, "SELECT DATATIME AS TIME, est, thr FROM tide6_nearby_est"
                          " WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s ORDER BY DATATIME",
                    (str(s), beg, end))
        if not df.empty:
            df["TIME"] = pd.to_datetime(df["TIME"])
            df["est"] = pd.to_numeric(df["est"], errors="coerce")
            df["thr"] = pd.to_numeric(df["thr"], errors="coerce")
            return str(s), df
    return None, pd.DataFrame(columns=["TIME", "est", "thr"])


def load_test_defs(conn) -> pd.DataFrame:
    """qc_test_def：欄位、判定方式（依 SUB_COLS＋qc_summary 排序）。"""
    df = _query(conn, "SELECT column_name, description FROM qc_test_def")
    order = {c: i for i, c in enumerate([*SUB_COLS, "qc_summary"])}
    df["_o"] = df["column_name"].map(order).fillna(99)
    return df.sort_values("_o").drop(columns="_o").reset_index(drop=True)


# ── 整理 ──────────────────────────────────────────────────────────────────

def row_name(col) -> str:
    """方格列名＝欄位名＋中文（字面看得懂）。"""
    if col.startswith("dc_"):
        zh = NAME_ZH.get("qc_" + col[3:])
        return f"{col} 多采{zh}" if zh else col
    return f"{col} {NAME_ZH[col]}" if col in NAME_ZH else col


def strip_columns(flags, show_dc=False):
    """方格要畫的欄位與不畫的欄位。
    整段期間都是 2（未檢測）的子項不畫：多半是這台儀器不適用（例：音波式沒有 qc_same_site、
    沒有鄰站模型就沒有 qc_nearby），畫成整列灰色會被看成「彙總沒檢核」（qc_viewer 2026-11-06 同理）。
    回傳 (cols, hidden)。"""
    cols, hidden = [], []
    for c in SUB_COLS:
        if c not in flags or (flags[c].dropna() == 2).all():
            hidden.append(c)
            continue
        cols.append(c)
        dc = "dc_" + c[3:]
        if show_dc and dc in flags and flags[dc].notna().any():
            cols.append(dc)
    return cols + ["qc_summary"], hidden


def obs_from_bundle(bundle_df: pd.DataFrame, stid: str) -> pd.DataFrame:
    """bundle 的觀測（Q 與非 Q 合併）、舊 qc、天文潮 → TIME, obs, old_qc, tide。

    bundle 的 QC 分流（data_engine.query_multi_tide_data）：WL_{stid}＝qc 為 Q 的值、
    WL_{stid}_raw＝非 Q 的原始值。旗標是對「所有非備份列」算的，所以觀測線用兩者合併。
    舊 qc 非 Q＝該時間沒有 Q 的值、只有非 Q 的值。
    天文潮：bundle 只查主站的 tide6ha（WL_{主站}_pred_h），同址各儀器共用。"""
    df = bundle_df
    out = pd.DataFrame({"TIME": pd.to_datetime(df["Time"])})
    q = df.get(f"WL_{stid}")
    raw = df.get(f"WL_{stid}_raw")
    q = pd.to_numeric(q, errors="coerce") if q is not None else pd.Series(float("nan"), index=df.index)
    raw = pd.to_numeric(raw, errors="coerce") if raw is not None else pd.Series(float("nan"), index=df.index)
    out["obs"] = q.combine_first(raw).values
    qc_raw = df.get(f"QC_{stid}_raw")
    old = pd.Series(None, index=df.index, dtype=object)
    if qc_raw is not None:
        old = qc_raw.where(q.isna() & raw.notna())
    out["old_qc"] = old.values
    pred_cols = [c for c in df.columns if str(c).endswith("_pred_h")]
    out["tide"] = pd.to_numeric(df[pred_cols[0]], errors="coerce").values if pred_cols else float("nan")
    return out.dropna(subset=["TIME"]).drop_duplicates("TIME").sort_values("TIME").reset_index(drop=True)


def merge_view(obs: pd.DataFrame, flags: pd.DataFrame, est: pd.DataFrame) -> pd.DataFrame:
    """以時間合併觀測、旗標、推估。"""
    v = obs.merge(flags, on="TIME", how="outer") if not flags.empty else obs.copy()
    if est is not None and not est.empty:
        v = v.merge(est, on="TIME", how="left")
    else:
        v["est"], v["thr"] = float("nan"), float("nan")
    return v.sort_values("TIME").reset_index(drop=True)


def to_hourly(v: pd.DataFrame) -> pd.DataFrame:
    """長時段改逐時：數值取整點，旗標取該小時最嚴重者，舊 qc 取該小時第一個非 Q 代碼。"""
    g = v.set_index("TIME")
    hour = g.index.floor("h")
    num = [c for c in ("obs", "tide", "est", "thr") if c in g]
    out = g.loc[g.index.minute == 0, num]
    out = out[~out.index.duplicated()]
    full = pd.DataFrame(index=pd.DatetimeIndex(sorted(set(hour)), name="TIME"))
    out = full.join(out)
    for c in [*SUB_COLS, "qc_summary", *DC_COLS]:
        if c not in g:
            continue
        sev = g[c].map(SEVERITY)
        worst = sev.groupby(hour).max()
        out[c] = worst.map({v_: k for k, v_ in SEVERITY.items()})
    if "old_qc" in g:
        out["old_qc"] = g["old_qc"].groupby(hour).first()
    return out.reset_index()


def group_events(events: pd.DataFrame) -> list:
    """斷點相隔 ≤ 1 天的事件併成一組。每組：start（最早時間；主圖垂直虛線畫這裡）、rows（各方法）。
    §13 設計原則 (4)：qc_datum 是綜合判斷，垂直線先用同一事件各方法中最早的時間，游標列出各方法時間。"""
    if events is None or events.empty:
        return []
    ev = events.sort_values("break_time").reset_index(drop=True)
    groups, cur = [], [ev.iloc[0]]
    for i in range(1, len(ev)):
        r = ev.iloc[i]
        if r["break_time"] - cur[-1]["break_time"] <= EVENT_JOIN:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    out = []
    for g in groups:
        rows = pd.DataFrame(g)
        srcs = rows["ref_source"].dropna().unique().tolist()
        out.append({"start": rows["break_time"].min(), "rows": rows, "n_source": len(srcs)})
    return out


def event_line(r) -> str:
    """一個事件（一個偵測方式）的說明文字。"""
    src = SOURCE_LABEL.get(r["ref_source"], r["ref_source"] or "?")
    way = WAY_LABEL.get(r["method"], r["method"])
    off = r["offset_mm"]
    off_s = f"{int(off):+d} mm" if pd.notna(off) else "偏移量不明"
    ts = f"；tideshift.sn {int(r['tideshift_sn'])}" if pd.notna(r.get("tideshift_sn")) else ""
    return f"{r['break_time']:%Y-%m-%d %H:%M} {src}（{way}）{off_s}{ts}"


def summary_marks(v: pd.DataFrame, cols) -> pd.DataFrame:
    """qc_summary 為 3／4 的點 → 標記表（同 buoy_checks 格式：TIME, CHECK, LEVEL, LAYER, Y, NOTE）。
    NOTE 列出該點不是 1 的子項。§13 設計原則 (2)：交接格式沿用 buoy_checks 的標記表。"""
    cols_out = ["TIME", "CHECK", "LEVEL", "LAYER", "Y", "NOTE"]
    if "qc_summary" not in v:
        return pd.DataFrame(columns=cols_out)
    m = v[v["qc_summary"].isin([3, 4])]
    rows = []
    subs = [c for c in cols if c != "qc_summary"]
    for _, r in m.iterrows():
        bad = [f"{NAME_ZH.get(c, c)} {int(r[c])}" for c in subs
               if c in r and pd.notna(r[c]) and int(r[c]) in (3, 4, 9)]
        rows.append((r["TIME"], "qc_summary", int(r["qc_summary"]), "level", r.get("obs"),
                     "、".join(bad) if bad else ""))
    return pd.DataFrame(rows, columns=cols_out)


def load_summary_marks(conn, stid, beg, end, bundle_df: pd.DataFrame) -> pd.DataFrame:
    """水位大圖（QC 工作台）疊點用（2026-11-24）：只讀 qc_summary 為 3／4 的列（逐 6 分鐘，不改逐時），
    Y 取本次查詢 bundle 該儀器的觀測值（Q 與非 Q 合併，同 obs_from_bundle）。
    回傳標記表（同 summary_marks：TIME, CHECK, LEVEL, LAYER, Y, NOTE）；該時間 bundle 沒有觀測值的點
    畫不出來，丟掉（回傳表的 attrs['no_obs'] 記丟掉幾筆）。"""
    cols = ", ".join(SUB_COLS)
    flags = _query(conn, f"SELECT DATATIME AS TIME, {cols}, qc_summary FROM tide6_qc"
                         " WHERE STID = %s AND DATATIME >= %s AND DATATIME < %s AND qc_summary IN (3, 4)"
                         " ORDER BY DATATIME", (str(stid), beg, end))
    if flags.empty:
        out = summary_marks(pd.DataFrame(), [])
        out.attrs["no_obs"] = 0
        return out
    flags["TIME"] = pd.to_datetime(flags["TIME"])
    for c in flags.columns:
        if c != "TIME":
            flags[c] = pd.to_numeric(flags[c], errors="coerce")
    obs = obs_from_bundle(bundle_df, str(stid))[["TIME", "obs"]]
    v = flags.merge(obs, on="TIME", how="left")
    marks = summary_marks(v, [*SUB_COLS, "qc_summary"])
    no_obs = int(marks["Y"].isna().sum())
    marks = marks.dropna(subset=["Y"]).reset_index(drop=True)
    marks.attrs["no_obs"] = no_obs
    return marks


def code_counts(v: pd.DataFrame, cols) -> pd.DataFrame:
    """各列的 3／4／9／2 筆數（逐 6 分鐘或逐時，跟畫面一致）。"""
    out = []
    for c in cols:
        s = v[c].dropna().astype(int) if c in v else pd.Series(dtype=int)
        out.append({"項目": row_name(c), **{CODE_LABEL[k]: int((s == k).sum()) for k in (4, 3, 9, 2)}})
    return pd.DataFrame(out)
