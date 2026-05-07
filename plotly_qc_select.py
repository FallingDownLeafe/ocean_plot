"""
plotly_qc_select.py  (v2)
==========================
Tkinter + Plotly 框選 → UPDATE SQL 產生器

改動 (v2):
  - SQL 視窗固定最前方（topmost），重複框選在同一個視窗更新，不再疊新視窗
  - Mode 1：UPDATE QC，條件 MIN0 BETWEEN…OR MIN1 BETWEEN…OR…（任一欄被框到即修改該筆 QC）
  - Mode 2：UPDATE MIN 欄位，依 DATATIME 分組，一筆資料一條 SQL，
            只更新被框到的欄位，套用四則運算（+  -  *  /）

整合到 ocean_plot.py 的方式：
  1. 將本檔放在同一目錄
  2. 在 ocean_plot.py 頂端加：
       from plotly_qc_select import write_chart_html, _start_callback_server
       import threading, webbrowser
  3. 在 MainApp.__init__ 結尾加：
       threading.Thread(target=_start_callback_server, daemon=True).start()
       self.after(200, self._poll_queue)   # 若 Tk 視窗在 self.root 則改 self.root.after
  4. 把 fig.show() 換成：
       path = write_chart_html(fig)
       webbrowser.open(f"file://{path}")
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
import json
import webbrowser
import tempfile
import os
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
import plotly.graph_objects as go
import pandas as pd
import numpy as np

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
CALLBACK_PORT    = 18765
POLL_INTERVAL_MS = 200
MIN_COLS         = [f"MIN{i}" for i in range(10)]

_selection_queue: queue.Queue = queue.Queue()


# ──────────────────────────────────────────────
# 本地 HTTP callback server
# ──────────────────────────────────────────────
class _SelectionHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self._cors(); self.end_headers()

    def do_POST(self):
        if self.path != "/selection":
            self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length", 0))
        try:
            _selection_queue.put(json.loads(self.rfile.read(n)))
        except Exception:
            pass
        self._cors(); self.end_headers(); self.wfile.write(b"OK")

    def _cors(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *_): pass


def _start_callback_server():
    HTTPServer(("localhost", CALLBACK_PORT), _SelectionHandler).serve_forever()


# ──────────────────────────────────────────────
# JS 注入
# ──────────────────────────────────────────────
def _build_js(port: int) -> str:
    return f"""
<script>
(function () {{
    var URL = "http://localhost:{port}/selection";
    function attach() {{
        var plots = document.querySelectorAll(".js-plotly-plot");
        if (!plots.length) {{ setTimeout(attach, 300); return; }}
        plots.forEach(function (plot) {{
            plot.on("plotly_selected", function (ev) {{
                if (!ev) return;
                var xr = (ev.range && ev.range.x) || [null, null];
                var yr = (ev.range && ev.range.y) || [null, null];
                var pts = (ev.points || []).map(function (p) {{
                    return {{ x: p.x, y: p.y,
                              curveNumber: p.curveNumber,
                              traceName: p.data ? p.data.name : null }};
                }});
                fetch(URL, {{
                    method: "POST",
                    headers: {{"Content-Type": "application/json"}},
                    body: JSON.stringify({{
                        x_start: xr[0], x_end: xr[1],
                        y_start: yr[0], y_end: yr[1],
                        point_count: (ev.points || []).length,
                        points: pts
                    }})
                }}).catch(function(e){{ console.warn("[QC]", e); }});
            }});
        }});
        console.log("[QC] 監聽器已掛載");
    }}
    if (document.readyState === "complete") attach();
    else window.addEventListener("load", attach);
}})();
</script>"""


# ──────────────────────────────────────────────
# HTML 輸出
# ──────────────────────────────────────────────
def write_chart_html(fig: go.Figure,
                     port: int = CALLBACK_PORT,
                     config: dict | None = None) -> str:
    # fig.update_layout(dragmode="select")   # 原本預設secect，改成zoom比較好，使用者可以點工具列切換
    # dragmode="select" 要在 layout，不在 config，所以這裡不衝突
    if "dragmode" not in fig.layout or fig.layout.dragmode is None:
        fig.update_layout(dragmode="zoom")   # 改成預設 zoom，使用者可點 □ 切換
    merged = {"scrollZoom": False, **(config or {})}   # 有傳就覆蓋預設值
    html = fig.to_html(full_html=True, include_plotlyjs=True, config=merged)
    # include_plotlyjs=True 代表把整個 Plotly.js 直接嵌進 HTML 檔案裡（約 3MB），完全不需要網路，在任何環境都能開。
    # 代價是 HTML 檔案變大，但AI評估對我們所需用途完全沒有影響。

    # html = fig.to_html(full_html=True, include_plotlyjs="cdn",
    #                    config=merged) # cdn要有網路才能用
    # "cdn" 的意思是「從網路上的 CDN 伺服器下載 Plotly.js」。
    # 安外環境有網路所以正常，安內環境沒有對外網路，瀏覽器下載不到 Plotly.js，圖表就是空白頁。
    # 舊版的 fig.show() 是 Plotly 自己在本地起一個 server（那個 127.0.0.1:61297），Plotly.js 從本地提供，所以不需要外網。
    html = html.replace("</body>", _build_js(port) + "\n</body>")
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".html", mode="w",
        encoding="utf-8", prefix="ocean_qc_")
    tmp.write(html); tmp.close()
    return tmp.name


# ──────────────────────────────────────────────
# SQL 產生器
# ──────────────────────────────────────────────
def _clean_ts(ts) -> str:
    """把 Plotly 時間戳 'YYYY-MM-DDTHH:MM:SS.mmm' 轉為 MySQL 格式。"""
    if ts is None: return ""
    return str(ts).split(".")[0].replace("T", " ")


def build_mode1_sql(sel: dict, stid: str, new_qc: int) -> str:
    """
    Mode 1 — UPDATE QC
    條件：DATATIME 在框選時間範圍內，且任一 MIN 欄位落在 y 範圍內
    """
    t1 = _clean_ts(sel.get("x_start"))
    t2 = _clean_ts(sel.get("x_end"))
    v1 = sel.get("y_start")
    v2 = sel.get("y_end")

    lines = [
        f"UPDATE tide6",
        f"SET    QC = {new_qc}",
        f"WHERE  STID     = '{stid}'",
        f"  AND  DATATIME BETWEEN '{t1}' AND '{t2}'",
    ]
    if v1 is not None and v2 is not None:
        lo, hi = sorted([float(v1), float(v2)])
        conds = "\n       OR  ".join(
            f"{c} BETWEEN {lo:.6f} AND {hi:.6f}" for c in MIN_COLS
        )
        lines.append(f"  AND  (   {conds}")
        lines.append(f"       )")
    lines.append(";")
    return "\n".join(lines)


# 這個函式只能Demo用
def build_mode2_sql(sel: dict, stid: str,
                    operator: str, operand: float) -> str:
    """
    Mode 2 — UPDATE MIN 值（四則運算）
    依 DATATIME 分組，每筆資料一條 UPDATE，只更新被框到的欄位。

    sel['points'] 須包含 traceName（對應 MIN0~MIN9 的 trace 名稱）。
    """
    points = sel.get("points", [])

    # grouped: { "2024-01-01 00:06:00": {"MIN0", "MIN3"}, ... }
    grouped: dict[str, set] = defaultdict(set)
    for p in points:
        ts   = _clean_ts(p.get("x"))
        name = p.get("traceName") or ""
        if ts and name in MIN_COLS:
            grouped[ts].add(name)

    if not grouped:
        return (
            "-- ⚠️  找不到可識別的 MIN 欄位資料點\n"
            "-- 請確認 Plotly trace 的 name 設定為 MIN0~MIN9"
        )

    sqls = []
    for ts in sorted(grouped.keys()):
        cols = sorted(grouped[ts], key=lambda c: int(c[3:]))
        set_clause = ",\n       ".join(
            f"{c} = {c} {operator} {operand}" for c in cols
        )
        sqls.append(
            f"UPDATE tide6\n"
            f"SET    {set_clause}\n"
            f"WHERE  STID     = '{stid}'\n"
            f"  AND  DATATIME = '{ts}';"
        )

    header = (
        f"-- Mode 2：{len(grouped)} 筆資料，"
        f"共 {sum(len(v) for v in grouped.values())} 個欄位\n"
        f"-- 運算：原值 {operator} {operand}\n\n"
    )
    return header + "\n\n".join(sqls)

def build_mode2_sql_by_time(sel: dict, stid: str,
                             operator: str, operand: float) -> str:
    """
    Mode 2 正式版：從選中點的 x 值（展開後的 Time）反推 DATATIME 和 MIN 欄位。
    不依賴 traceName，適用於 ocean_plot.py 的複雜 trace name。

    反推邏輯：
      tide6 每列 DATATIME 存一個整點，MIN0~MIN9 各差 6 分鐘
      → Time = DATATIME + N×6min
      → N = Time.minute // 6
      → DATATIME = Time 截到整點 (minute=0)
    """
    points = sel.get("points", [])
    grouped: dict[str, set] = defaultdict(set)

    for p in points:
        ts_str = _clean_ts(p.get("x"))
        if not ts_str:
            continue
        try:
            ts = pd.to_datetime(ts_str)
            n = ts.minute // 6                          # 0~9
            datatime = ts.replace(minute=0, second=0, microsecond=0)
            grouped[datatime.strftime("%Y-%m-%d %H:%M:%S")].add(f"MIN{n}")
        except Exception:
            continue

    if not grouped:
        return "-- ⚠️  無法從選中的點反推 MIN 欄位（確認 x 軸為 DATATIME 展開的時間格式）"

    sqls = []
    for datatime_str in sorted(grouped.keys()):
        cols = sorted(grouped[datatime_str], key=lambda c: int(c[3:]))
        set_clause = ",\n       ".join(
            f"{c} = {c} {operator} {operand}" for c in cols
        )
        sqls.append(
            f"UPDATE tide6\n"
            f"SET    {set_clause}\n"
            f"WHERE  STID     = '{stid}'\n"
            f"  AND  DATATIME = '{datatime_str}';"
        )

    header = (
        f"-- Mode 2：{len(grouped)} 筆 DATATIME，"
        f"共 {sum(len(v) for v in grouped.values())} 個欄位\n"
        f"-- 運算：原值 {operator} {operand}\n\n"
    )
    return header + "\n\n".join(sqls)

# ──────────────────────────────────────────────
# SQL 顯示視窗（可重複使用，固定最前方）
# ──────────────────────────────────────────────
class SqlDialog(tk.Toplevel):
    """
    每次框選後更新此視窗內容，不重複建立新視窗。
    固定顯示在所有視窗最前方。
    """

    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("UPDATE SQL")
        self.geometry("720x380")
        self.resizable(True, True)
        self.attributes("-topmost", True)
        self.lift()
        self.focus_force()
        self._build()

    def _build(self):
        self._info_var = tk.StringVar(value="等待框選…")
        ttk.Label(self, textvariable=self._info_var,
                  foreground="#666", font=("TkDefaultFont", 9)
                  ).pack(anchor="w", padx=12, pady=(8, 2))

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        xsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL)
        ysb = ttk.Scrollbar(frame)
        xsb.pack(side=tk.BOTTOM, fill=tk.X)
        ysb.pack(side=tk.RIGHT,  fill=tk.Y)

        self._txt = tk.Text(
            frame, wrap=tk.NONE,
            font=("Courier New", 11),
            xscrollcommand=xsb.set, yscrollcommand=ysb.set,
            relief=tk.FLAT, bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white", selectbackground="#264f78"
        )
        self._txt.pack(fill=tk.BOTH, expand=True)
        xsb.config(command=self._txt.xview)
        ysb.config(command=self._txt.yview)

        btn_f = ttk.Frame(self)
        btn_f.pack(fill=tk.X, padx=10, pady=(2, 10))
        ttk.Button(btn_f, text="📋  複製 SQL",
                   command=self._copy).pack(side=tk.LEFT)

    def update_content(self, sql: str, info: str):
        self._info_var.set(info)
        self._txt.config(state=tk.NORMAL)
        self._txt.delete("1.0", tk.END)
        self._txt.insert("1.0", sql)
        self._txt.config(state=tk.DISABLED)
        self.attributes("-topmost", True)
        self.lift()
        self.focus_force()

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._txt.get("1.0", tk.END).strip())
        messagebox.showinfo("已複製", "SQL 已複製到剪貼簿", parent=self)


# ──────────────────────────────────────────────
# 主應用程式
# ──────────────────────────────────────────────
class OceanQcApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Ocean QC Tool")
        self.geometry("460x370")
        self.resizable(False, False)

        self.stid    = tk.StringVar(value="YOUR_STID")
        self.new_qc  = tk.IntVar(value=9)
        self.mode    = tk.StringVar(value="1")
        self.op      = tk.StringVar(value="+")
        self.operand = tk.DoubleVar(value=0.0)

        self._tmp_html: str | None = None
        self._sql_dlg:  SqlDialog | None = None

        self._build_ui()
        threading.Thread(target=_start_callback_server, daemon=True).start()
        self.after(POLL_INTERVAL_MS, self._poll_queue)

    def _build_ui(self):
        top = ttk.LabelFrame(self, text="基本參數", padding=10)
        top.pack(fill=tk.X, padx=12, pady=8)
        ttk.Label(top, text="STID：").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.stid, width=22).grid(
            row=0, column=1, sticky="ew")
        top.columnconfigure(1, weight=1)

        mode_f = ttk.LabelFrame(self, text="SQL 模式", padding=10)
        mode_f.pack(fill=tk.X, padx=12)

        ttk.Radiobutton(
            mode_f, text="Mode 1 — 更新 QC（任一 MIN 欄位被框到即修改）",
            variable=self.mode, value="1", command=self._on_mode_change
        ).pack(anchor="w")

        m1_f = ttk.Frame(mode_f)
        m1_f.pack(fill=tk.X, padx=20, pady=(0, 6))
        ttk.Label(m1_f, text="新 QC 值：").pack(side=tk.LEFT)
        ttk.Spinbox(m1_f, textvariable=self.new_qc,
                    from_=0, to=9, width=5).pack(side=tk.LEFT)

        ttk.Radiobutton(
            mode_f, text="Mode 2 — 更新 MIN 值（依 DATATIME 分組，四則運算）",
            variable=self.mode, value="2", command=self._on_mode_change
        ).pack(anchor="w")

        self._m2_frame = ttk.Frame(mode_f)
        self._m2_frame.pack(fill=tk.X, padx=20, pady=(0, 4))
        ttk.Label(self._m2_frame, text="運算：MIN欄位 ").pack(side=tk.LEFT)
        ttk.Combobox(self._m2_frame, textvariable=self.op,
                     values=["+", "-", "*", "/"],
                     width=3, state="readonly").pack(side=tk.LEFT)
        ttk.Entry(self._m2_frame, textvariable=self.operand,
                  width=10).pack(side=tk.LEFT, padx=(4, 0))

        self._on_mode_change()

        ttk.Button(self, text="🗺  開啟圖表（框選後自動產生 SQL）",
                   command=self._open_chart
                   ).pack(fill=tk.X, padx=12, pady=10)

        self._status = ttk.Label(self, text="就緒。",
                                 foreground="#666", anchor="w")
        self._status.pack(fill=tk.X, padx=12)

    def _on_mode_change(self):
        state = tk.NORMAL if self.mode.get() == "2" else tk.DISABLED
        for w in self._m2_frame.winfo_children():
            try: w.config(state=state)
            except tk.TclError: pass

    def _poll_queue(self):
        try:
            while True:
                self._on_selection(_selection_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(POLL_INTERVAL_MS, self._poll_queue)

    def _on_selection(self, data: dict):
        stid = self.stid.get().strip()
        cnt  = data.get("point_count", 0)
        if self.mode.get() == "1":
            sql  = build_mode1_sql(data, stid, self.new_qc.get())
            info = (f"Mode 1 | {cnt} 點 | "
                    f"t=[{_clean_ts(data.get('x_start'))} ~ "
                    f"{_clean_ts(data.get('x_end'))}]")
        else:
            sql  = build_mode2_sql(data, stid, self.op.get(), self.operand.get())
            info = (f"Mode 2 | {cnt} 點 | "
                    f"運算：原值 {self.op.get()} {self.operand.get()}")
        self._status.config(text=info)
        self._show_or_update_sql(sql, info)

    def _show_or_update_sql(self, sql: str, info: str):
        if self._sql_dlg is None or not self._sql_dlg.winfo_exists():
            self._sql_dlg = SqlDialog(self)
        self._sql_dlg.update_content(sql, info)

    def _open_chart(self):
        fig = self._make_demo_figure()
        path = write_chart_html(fig, port=CALLBACK_PORT)
        if self._tmp_html and os.path.exists(self._tmp_html):
            try: os.unlink(self._tmp_html)
            except OSError: pass
        self._tmp_html = path
        webbrowser.open(f"file://{path}")
        self._status.config(text=f"已開啟：{os.path.basename(path)}")

    def _make_demo_figure(self) -> go.Figure:
        """
        示範圖表（替換成你的 MySQL 查詢）。
        重點：每條 trace 的 name 必須是 MIN0~MIN9，
        Mode 2 才能正確辨識哪些欄位被框到。
        """
        rng  = pd.date_range("2024-03-01", periods=200, freq="6min")
        base = np.cumsum(np.random.randn(200)) + 150.0
        fig  = go.Figure()
        for i, col in enumerate(["MIN0", "MIN1", "MIN2"]):
            fig.add_trace(go.Scatter(
                x=rng, y=base + np.random.randn(200) * 0.3 + i * 0.5,
                mode="markers+lines", name=col,
                marker=dict(size=5),
            ))
        fig.update_layout(
            title="框選工具（□）選取範圍 → 自動產生 SQL",
            xaxis_title="DATATIME", yaxis_title="水位（cm）",
            dragmode="select", hovermode="closest",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        return fig

    def destroy(self):
        if self._tmp_html and os.path.exists(self._tmp_html):
            try: os.unlink(self._tmp_html)
            except OSError: pass
        super().destroy()


if __name__ == "__main__":
    OceanQcApp().mainloop()
