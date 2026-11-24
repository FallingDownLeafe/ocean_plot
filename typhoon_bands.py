"""
typhoon_bands.py
================
颱風海上／陸上警報期間的資料整理與色帶繪製（2026-11-20 新增）。

背景：V2 改寫時水位大圖、診斷總覽、事件報告都沒有颱風色帶（main_app 寫死
land_range=None），且颱風資料欄位名在各處不一致（DB 回傳 WARN1BEG…，畫圖程式讀
warnSeaBeg…，Mode 3 SQL 讀 id／cname），只有暴潮互動圖自己轉過。
現在統一在 main_app 查詢完成時呼叫 normalize_typhoon_info() 一次，下游都讀同一套欄位名。

畫法（沿用暴潮報表圖的設計）：
- 海警藍、陸警紅兩層半透明底色疊在一起（重疊處呈淡紫）。
- 說明文字兩行（海警一行、陸警一行）放在同一個位置：海警期間的中點，
  兩行上下對齊；沒有海警時才改用陸警中點。
"""

from datetime import date, datetime, timedelta

import pandas as pd

_KEYMAP = {
    "id":          ("id", "ID"),
    "cname":       ("cname", "CNAME"),
    "warnSeaBeg":  ("warnSeaBeg", "WARN1BEG", "warnseabeg", "warn1beg"),
    "warnSeaEnd":  ("warnSeaEnd", "WARN1END", "warnseaend", "warn1end"),
    "warnLandBeg": ("warnLandBeg", "WARN2BEG", "warnlandbeg", "warn2beg"),
    "warnLandEnd": ("warnLandEnd", "WARN2END", "warnlandend", "warn2end"),
}

# 配色：dark＝互動頁（plotly_dark）、light＝白底報告圖（同 build_surge_report_figure）
_STYLE = {
    "dark":  {"sea": "rgba(90, 150, 220, 0.16)", "land": "rgba(235, 85, 85, 0.16)",
              "sea_txt": "#8fbfef", "land_txt": "#f08a8a", "bg": "rgba(30,30,30,0.6)"},
    "light": {"sea": "rgba(70, 130, 180, 0.08)", "land": "rgba(220, 80, 80, 0.07)",
              "sea_txt": "rgb(60, 110, 160)", "land_txt": "rgb(210, 60, 60)",
              "bg": "rgba(255,255,255,0.7)"},
}


def _ts(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return pd.Timestamp(v)
    except (TypeError, ValueError):
        return None


def normalize_typhoon_info(info: dict | None) -> dict | None:
    """保留原欄位，另補上 id／cname／warnSeaBeg／warnSeaEnd／warnLandBeg／warnLandEnd。"""
    if not info:
        return info
    out = dict(info)
    for std, cands in _KEYMAP.items():
        for k in cands:
            if k in info and info[k] is not None:
                out[std] = info[k]
                break
        else:
            out.setdefault(std, None)
    return out


def warn_ranges(info: dict | None, start=None, end=None) -> dict | None:
    """回傳 {'sea': {...}|None, 'land': {...}|None}；兩者都沒有（或都不在查詢期間內）時回傳 None。

    每段 {'beg','end'（實際警報時間，給文字用）,'x0','x1'（裁到查詢期間內，給色帶用）}。
    start／end 為查詢期間（date 或 datetime；end 不含）。不裁的話色帶會把 x 軸撐到警報期間。
    """
    info = normalize_typhoon_info(info)
    if not info:
        return None
    lo = pd.Timestamp(start) if start is not None else None
    hi = pd.Timestamp(end) if end is not None else None
    res = {}
    for name, kb, ke in (("sea", "warnSeaBeg", "warnSeaEnd"),
                         ("land", "warnLandBeg", "warnLandEnd")):
        b, e = _ts(info.get(kb)), _ts(info.get(ke))
        if b is None or e is None or e < b:
            res[name] = None
            continue
        x0 = max(b, lo) if lo is not None else b
        x1 = min(e, hi) if hi is not None else e
        res[name] = {"beg": b, "end": e, "x0": x0, "x1": x1} if x0 < x1 else None
    return res if (res["sea"] or res["land"]) else None


def _as_ranges(r):
    """相容舊格式：(beg, end) tuple 視為陸警。"""
    if not r:
        return None
    if isinstance(r, dict):
        return r
    b, e = _ts(r[0]), _ts(r[1])
    if b is None or e is None:
        return None
    return {"sea": None, "land": {"beg": b, "end": e, "x0": b, "x1": e}}


def add_warn_bands(fig, ranges, row=None, col=None, theme="dark", label=False, font_size=12,
                   layer="below", label_pos="inside"):
    """在 fig（或其 row/col 子圖）畫海警、陸警色帶；label=True 時加兩行說明文字。

    多列子圖共用 x 軸時，文字只需在第一列加（label=True），其他列只畫色帶。
    layer：x 軸開了 rangeslider 的子圖，'below' 的色帶會被蓋住看不到（2026-11-20 實測，
    診斷總覽左欄），要傳 'above'（半透明，會讓資料線略帶色調）。
    label_pos：'inside'＝兩行文字放在圖內頂端（水位大圖、報告水位圖）；
               'above'＝併成一行，放在子圖標題上方、不壓在折線上（診斷總覽，子圖矮，2026-11-20 使用者反映擋線）。
    """
    r = _as_ranges(ranges)
    if not r:
        return
    st = _STYLE[theme]
    kw = {} if row is None else {"row": row, "col": col if col is not None else 1}
    for name in ("sea", "land"):
        seg = r.get(name)
        if seg:
            fig.add_vrect(x0=seg["x0"], x1=seg["x1"], fillcolor=st[name],
                          line_width=0, layer=layer, **kw)
    if not label:
        return
    lines = []
    for name, tag in (("sea", "海警"), ("land", "陸警")):
        seg = r.get(name)
        if seg:
            lines.append(f"<span style='color:{st[name + '_txt']}'>{tag} "
                         f"{seg['beg']:%m/%d %H:%M} – {seg['end']:%m/%d %H:%M}</span>")
    anchor = r.get("sea") or r.get("land")
    mid = anchor["x0"] + (anchor["x1"] - anchor["x0"]) / 2
    if label_pos == "above":
        fig.add_annotation(
            x=mid, y=1, xref="x", yref="y domain", xanchor="center", yanchor="bottom",
            yshift=24, text="　".join(lines), showarrow=False,
            font=dict(size=font_size), **kw,
        )
        return
    fig.add_annotation(
        x=mid, y=0.99, xref="x", yref="y domain", xanchor="center", yanchor="top",
        text="<br>".join(lines), showarrow=False, align="center",
        font=dict(size=font_size), bgcolor=st["bg"], borderpad=3, **kw,
    )


def query_end_exclusive(end) -> datetime:
    """查詢迄日（含當天）→ 不含的上界。"""
    if isinstance(end, datetime):
        return end
    if isinstance(end, date):
        return datetime(end.year, end.month, end.day) + timedelta(days=1)
    return pd.Timestamp(end).to_pydatetime() + timedelta(days=1)
