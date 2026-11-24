"""
buoy_checks.py
==============
浮球／浮標波浪的自動檢查（2026-11-20 新增）。**只標出來給人看，不改 qc。**

規則來源：浮標資料夾 `Ocean_plot浮標品管需求.md` §2（優先度「高」的三條波浪規則），
在成功浮球 wave1 歷年資料試算過（`浮標資料調查紀錄.md` §15：除 2017～2020 那批
「H3＝0 但有週期」外，2021～2026 平均每年約 1 筆）。改規則時兩邊一起改。

純函式：DataFrame 進、DataFrame 出，不 import Dash／Plotly（資料整理和畫圖分開）。

標記表（marks）格式——日後多采潮位旗標也用同一格式交給畫圖模組：
    TIME   pd.Timestamp  標記時間（逐點；區間型的檢查日後可加 TIME_END 欄）
    CHECK  str           檢查代碼（見 CHECKS）
    LEVEL  int           3＝可疑、4＝不通過（沿用多采旗標的代碼語意）
    LAYER  str           'h'＝畫在波高層、't'＝畫在週期層
    Y      float         標記畫在哪個值上（圖上的單位：cm 或 s）
    NOTE   str           游標顯示的一句說明（含數值）
"""

import pandas as pd

# 代碼 → (圖例名稱, 等級, 判定方式說明)
CHECKS = {
    "break": ("碎波上限", 4,
              "最大波高 > 0.78×水深：比這更高的浪在該水深會碎掉，物理上不可能"),
    "h0t":   ("波高0但有週期", 4,
              "示性波高＝0 但週期 > 0：同一筆紀錄自相矛盾"),
    "tjump": ("週期跳動", 3,
              "週期比前後一小時的平均高 ≥ 30%，且波高 ≥ 2 m：只看波高跳動抓不到這種錯"),
}

BREAK_RATIO  = 0.78   # 碎波指標 H/d
TJUMP_RATIO  = 1.30
TJUMP_MIN_CM = 200    # 波高 ≥ 2 m 才檢查週期跳動（小浪時週期本來就跳）

_COLS = ["TIME", "CHECK", "LEVEL", "LAYER", "Y", "NOTE"]


def _num(df, col):
    if col not in df.columns:
        return pd.Series([float("nan")] * len(df), index=df.index)
    return pd.to_numeric(df[col], errors="coerce")


def wave_checks(df, h_col, t_col, depth_m=None) -> pd.DataFrame:
    """波浪三條規則。

    df      : 含 TIME、h_col（示性波高 cm）、HMAX（最大波高 cm，可無）、
              t_col（週期，資料庫單位 0.1 s）、TMEAN（可無）。
    h_col   : 浮球 wave1 用 'H3'；浮標 wave 用 'H'。
    t_col   : 週期跳動看哪個週期。浮球用 'T3'（示性週期）；浮標用 'TMEAN'
              （浮標沒有示性週期，尖峰週期 T 本來就會在湧浪／風浪間跳，不適合）。
    depth_m : 水深（m），None 時不做碎波上限。
    回傳 marks（見檔頭），沒有標到時回傳空表（欄位齊全）。
    """
    if df is None or df.empty or h_col not in df.columns:
        return pd.DataFrame(columns=_COLS)

    d = pd.DataFrame({"TIME": pd.to_datetime(df["TIME"])})
    d["H"]    = _num(df, h_col).values
    d["HMAX"] = _num(df, "HMAX").values
    d["T"]    = _num(df, t_col).values
    d["TM"]   = _num(df, "TMEAN").values
    d = d.dropna(subset=["TIME"]).drop_duplicates("TIME").sort_values("TIME")
    out = []

    # 1. 碎波上限
    if depth_m:
        lim_cm = BREAK_RATIO * float(depth_m) * 100
        for r in d[d["HMAX"] > lim_cm].itertuples():
            out.append((r.TIME, "break", 4, "h", r.HMAX,
                        f"最大波高 {r.HMAX/100:.2f} m > 0.78×水深 {depth_m:g} m"
                        f"＝{lim_cm/100:.1f} m"))

    # 2. 波高 0 但有週期
    has_t = (d["T"] > 0) | (d["TM"] > 0)
    for r in d[(d["H"] == 0) & has_t].itertuples():
        tt = r.T if pd.notna(r.T) and r.T > 0 else r.TM
        out.append((r.TIME, "h0t", 4, "h", 0.0,
                    f"{h_col}＝0，但週期 {tt/10:.1f} s"))

    # 3. 週期跳動：前後「剛好一小時」的值都要有（缺一邊就不判）
    s = d.set_index("TIME")["T"]
    prev = s.reindex(s.index - pd.Timedelta(hours=1)).values
    nxt  = s.reindex(s.index + pd.Timedelta(hours=1)).values
    avg  = (prev + nxt) / 2
    ratio = s.values / avg
    hit = (ratio >= TJUMP_RATIO) & (d["H"].values >= TJUMP_MIN_CM)
    for i in [k for k, v in enumerate(hit) if v]:
        t0 = s.index[i]
        out.append((t0, "tjump", 3, "t", s.values[i] / 10,
                    f"{t_col} {s.values[i]/10:.1f} s，前後 {prev[i]/10:.1f}、"
                    f"{nxt[i]/10:.1f} s（比 {ratio[i]:.2f}）；"
                    f"{h_col} {d['H'].values[i]/100:.2f} m"))

    return pd.DataFrame(out, columns=_COLS).sort_values("TIME").reset_index(drop=True)


def summarize(marks: pd.DataFrame, max_list: int = 8) -> str:
    """狀態面板用的文字：各檢查筆數＋前幾筆時間。"""
    if marks is None or marks.empty:
        return "0 筆"
    parts = []
    for code, (name, level, _) in CHECKS.items():
        m = marks[marks["CHECK"] == code]
        if m.empty:
            continue
        times = "、".join(t.strftime("%m/%d %H:%M") for t in m["TIME"].head(max_list))
        more = f" 等 {len(m)} 筆" if len(m) > max_list else ""
        parts.append(f"{name} {len(m)} 筆：{times}{more}")
    return "\n".join(parts)
