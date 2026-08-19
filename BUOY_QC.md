# 🔵 浮球/浮標品管工具 — BUOY_QC.md

> 文件版本：2026-08-19
> 定位：Plan A 過渡工具（三檔案獨立 Dash App），待 Plan B（整合進 ocean_plot_dash.py）
> 完成後退役。主系統文件請見 SYSTEM.md。

---

## 目錄

1. [系統概述](#1-系統概述)
2. [目錄與環境設定](#2-目錄與環境設定)
3. [資料表結構與 QC 通則](#3-資料表結構與-qc-通則)
4. [資料層 BuoyDataEngine](#4-資料層-buoydataengine)
5. [Dash App 架構](#5-dash-app-架構)
6. [SQL 產生邏輯](#6-sql-產生邏輯)
7. [圖表與色系](#7-圖表與色系)
8. [打包注意事項](#8-打包注意事項)
9. [已知限制與 Plan B 待辦](#9-已知限制與-plan-b-待辦)

---

## 1. 系統概述

獨立 Dash App，連接 `mrbank` MySQL 資料庫，提供浮球（kind=7）與浮標（kind=8）觀測資料的
視覺化與 QC 旗標批次修改。使用者在圖表上 Box Select 框選異常時間段，工具自動產生對應的
`UPDATE ... SET qc = '新值' ... AND qc = '舊值'` SQL 語句供複製貼上執行。

不使用 python-dotenv（未列入 requirements，一律手動解析 `.env`）。
不含 Tkinter。不共用主系統的 `dash_bridge` 或 Dash app 進程。

---

## 2. 目錄與環境設定

```
buoy_qc/
├── buoy_qc_app.py    # 入口：BuoyDataEngine + 啟動 Dash
├── buoy_qc_dash.py   # Dash layout + callbacks + SQL / figure builders
└── .env              # DB 連線與 port 設定
```

### `.env` 可設定的變數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `DB_IP` | `""` | DB 主機 IP |
| `DB_USER` | `""` | DB 帳號 |
| `DB_PASS` | `""` | DB 密碼 |
| `DB_NAME` | `mrbank` | 資料庫名稱 |
| `BUOY_PORT` | `8060` | Dash 監聽 port（與主系統 8050 錯開） |

`.env` 手動解析：逐行讀取，`split("=", 1)` 取鍵值，略過 `#` 開頭行；
使用直接指派（非 `os.environ.setdefault`，避免覆寫空字串失敗問題）。

---

## 3. 資料表結構與 QC 通則

> **核心規則（所有六張表一律適用）**
> `qc` 欄位均為複合 Primary Key 的成員。
> UPDATE SQL **必須**在 WHERE 子句加 `AND qc = '{old_qc}'`，
> 否則會因 PK 衝突失敗或更新到錯誤列。

### 3-1 TIME 型資料表（有直接 TIME 欄位）

| 資料表 | PK | Z 欄位 | 主要查詢欄位 | 單位換算 |
|--------|-----|--------|-------------|---------|
| `wave1` | STID, TIME, qc | 無 | H3（示性波高）, HMAX（最大波高）| cm，直接顯示 |
| `wave`  | STID, TIME, qc | 無 | H（示性波高）, TMEAN（平均週期）| cm / 0.1 s |
| `wind`  | STID, TIME, Z, qc | INT | VM（平均風速）, VG（最大風速）, DM（風向）| VM/VG × 0.1 = m/s |
| `curr`  | STID, TIME, Z, qc | INT | V（流速）, D（流向）| V × 0.1 = cm/s |

UPDATE 模板（TIME 型）：

```sql
UPDATE {table}
SET    qc = '{new_qc}'
WHERE  STID = '{stid}'
  [AND Z = {z}]                           -- 僅 wind / curr
  AND  TIME BETWEEN '{t1}' AND '{t2}'
  AND  qc = '{old_qc}';
```

### 3-2 DATATIME + HR 型資料表（寬格式，每日一列）

| 資料表 | PK | Z 欄位 | 主要查詢欄位 | 單位換算 |
|--------|-----|--------|-------------|---------|
| `pres1`  | STID, DATATIME, qc      | **無** | HR0~HR23（逐時氣壓）| × 0.1 = hPa |
| `stemp1` | STID, DATATIME, Z, qc   | INT    | HR0~HR23（逐時溫度）| × 0.1 = °C  |

`stemp1` Z 值語義：

| Z 值（INT）| 意義 |
|-----------|------|
| `-3` | 氣溫 |
| `0`  | 海溫計 1 |
| `1`  | 海溫計 2 |

UPDATE 模板（HR 型）：

```sql
UPDATE {table}
SET    qc = '{new_qc}'
WHERE  STID = '{stid}'
  [AND Z = {z}]                           -- 僅 stemp1
  AND  DATATIME IN ('{YYYY-MM-DD 00:00:00}', ...)
  AND  qc = '{old_qc}';
```

> HR 型表的 qc 是**整列（整天）共用一個值**。框選某天的任意幾個小時，
> SQL 會更新該天整列的 qc；這是預期行為，非 bug。

---

## 4. 資料層 BuoyDataEngine

位於 `buoy_qc_app.py`。由 `main()` 實例化後注入 `buoy_qc_dash.ENGINE`，
Dash callbacks 共用同一個實例，不重複建立連線。

### 方法一覽

| 方法 | 簽名 | 回傳欄位 | 說明 |
|------|------|---------|------|
| `__init__` | — | — | 建立 mysql.connector 連線（`charset="utf8mb3"`）|
| `load_stations` | `() → DataFrame` | STID, STNAC, KIND | 查 `st.kind IN (7, 8)`，填充下拉選單 |
| `fetch_wave1` | `(stid, start, end)` | TIME, H3, HMAX, QC | 查 wave1 |
| `fetch_wind`  | `(stid, start, end, z: int)` | TIME, VM, VG, DM, QC | VM/VG × 0.1 m/s |
| `fetch_curr`  | `(stid, start, end, z: int)` | TIME, V, D, QC | V × 0.1 cm/s |
| `fetch_wave`  | `(stid, start, end)` | TIME, H, TMEAN, QC | 查 wave |
| `fetch_pres1` | `(stid, start, end)` | TIME, P, QC | HR 展開後 × 0.1 hPa |
| `fetch_stemp1`| `(stid, start, end, z: int)` | TIME, T, QC | HR 展開後 × 0.1 °C |
| `get_z_options` | `(table, stid) → list[int]` | — | `DISTINCT Z` 供 Z 選擇器用 |
| `_expand_hr` | `(df, val_name, scale) → DataFrame` | TIME, val_name, QC | 內部工具，見下 |

### `_expand_hr()` 展開邏輯

```python
def _expand_hr(self, df, val_name, scale=1.0):
    hr_cols = [c for c in df.columns if c.startswith("HR")]
    id_cols  = [c for c in df.columns if c not in hr_cols]
    long = df.melt(id_vars=id_cols, value_vars=hr_cols,
                   var_name="_HR", value_name=val_name)
    long["TIME"] = (pd.to_datetime(long["DATATIME"]) +
                    pd.to_timedelta(long["_HR"].str[2:].astype(int), unit="h"))
    long[val_name] = long[val_name] * scale
    return (long.dropna(subset=[val_name])
                .sort_values("TIME")
                .reset_index(drop=True))[["TIME", val_name, "QC"]]
```

- `pd.melt()` 將 HR0~HR23 轉長格式，`TIME = DATATIME + N 小時`
- 回傳固定三欄 `[TIME, val_name, QC]`；QC 為整列（整天）的 qc 值，每小時複製一份
- `dropna` 移除缺測值，不做 `drop_duplicates`

**`mysql.connector` 欄位小寫問題：**
所有 `pd.read_sql()` 後立即 `df.columns = [c.upper() for c in df.columns]`。

---

## 5. Dash App 架構

### 啟動流程

```
buoy_qc_app.py main()
  → BuoyDataEngine()
  → buoy_qc_dash.ENGINE = engine          # 注入，在 Dash 啟動前完成
  → threading.Thread(target=_start_dash, daemon=True).start()
  → time.sleep(1.5)                        # 等 Flask ready
  → webbrowser.open(http://127.0.0.1:{BUOY_PORT}/)
  → while t.is_alive(): time.sleep(0.5)   # 主執行緒等待（Ctrl+C 中斷）
```

### Layout 結構

```
app.layout
├─ dcc.Interval(id="init-trigger", 300ms, max_intervals=1)
├─ dcc.Store(id="active-tab-store")
├─ Header
└─ 主內容（flex row）
    ├─ 左：圖表區（flex: 3）
    │   ├─ 頂部控制列（測站下拉 / DatePickerRange / Z 選擇器 / 查詢按鈕）
    │   ├─ dcc.Tabs(id="main-tabs")   ← 6 個 Tab
    │   ├─ dcc.Graph(id="main-graph")
    │   └─ html.Div(id="status-bar")
    └─ 右：QC 面板（minWidth:220px / maxWidth:280px）
        ├─ §A 資料表名稱（Div id="active-table-display"）
        ├─ §B QC 設定
        │   ├─ 舊 QC 顯示（Div id="old-qc-display"，黃色）
        │   └─ 新 QC 輸入（Input id="new-qc-input"，預設 "R"）
        └─ §C SQL 輸出
            ├─ Textarea id="sql-output"
            └─ Clipboard 複製按鈕
```

### Tab / Z 控制常數

```python
_TABS = [
    {"value": "wave1",  "label": "浮球波浪表 wave1"},
    {"value": "wind",   "label": "風速 wind"},
    {"value": "curr",   "label": "海流 curr"},
    {"value": "wave",   "label": "浮標波浪表 wave"},
    {"value": "pres1",  "label": "氣壓 pres1"},
    {"value": "stemp1", "label": "溫度 stemp1"},
]
_TABS_WITH_Z = {"wind", "curr", "stemp1"}  # 顯示 Z 選擇器
_HR_TABLES   = {"pres1", "stemp1"}          # 走 DATATIME IN SQL
```

Z 選擇器由 `get_z_options()` 動態查詢，stemp1 的顯示標籤：
`-3 → "Z=-3（氣溫）"`、`0 → "Z=0（海溫計1）"`、`1 → "Z=1（海溫計2）"`。

### Callbacks

| Callback | 觸發 Input | 主要 Output | 說明 |
|----------|-----------|------------|------|
| `populate_stations` | `init-trigger` | `station-dropdown.options/value` | 一次性載入測站清單 |
| `on_tab_change` | `main-tabs.value` | `active-tab-store`, `z-selector-wrap.style`, `active-table-display` | 切 Tab 時顯隱 Z 選擇器 |
| `update_z_options` | `station-dropdown.value`, `active-tab-store` | `z-selector.options/value` | 動態查詢可用 Z 值 |
| `render_figure` | `query-btn.n_clicks` | `main-graph.figure`, `status-bar` | 查詢 + 繪圖 |
| `on_selection` | `main-graph.selectedData` | `sql-output.value`, `old-qc-display` | Box Select → SQL |

`render_figure` 以 `State` 讀取 `active-tab-store`，依 tab 值分派到對應的
`ENGINE.fetch_*()` 和 `build_*_figure()`；pres1 / stemp1 因需傳入 `z` 參數，
不走 `_FIG_BUILDERS` dict，改為 if/elif 直接呼叫。

---

## 6. SQL 產生邏輯

### 流程

```
on_selection(selected_data, stid, new_qc, tab, z)
  → _extract_sel(pts) → (times, old_qc)
  → build_sql(tab, selected_data, stid, new_qc, z)
       ├─ tab in _HR_TABLES  → _build_sql_hr(...)
       └─ 其餘              → _build_sql(...)
  → return (sql_str, old_qc)
```

### `_extract_sel(pts)`

```python
times   = sorted({_clean_ts(p["x"]) for p in pts if "x" in p})
qc_list = [p["customdata"][0] for p in pts if p.get("customdata")]
old_qc  = max(set(qc_list), key=qc_list.count) if qc_list else "Q"
```

`_clean_ts`：`ts.replace("T", " ").split(".")[0]`（Plotly 時間戳 → MySQL 格式）。

### `_build_sql`（TIME 型）

```python
f"UPDATE {table}\nSET qc = '{new_qc}'\n"
f"WHERE STID = '{stid}'"
[f"\n  AND Z = {int(z)}"]
f"\n  AND TIME BETWEEN '{t1}' AND '{t2}'\n"
f"  AND qc = '{old_qc}';"
```

### `_build_sql_hr`（HR 型）

時間還原：`datatimes = sorted({ts[:10] + " 00:00:00" for ts in times})`

```python
f"UPDATE {table}\nSET qc = '{new_qc}'\n"
f"WHERE STID = '{stid}'"
[f"\n  AND Z = {int(z)}"]
f"\n  AND DATATIME IN ({', '.join(repr(dt) for dt in datatimes)})\n"
f"  AND qc = '{old_qc}';"
```

### customdata 設置規則

所有 trace 均設 `customdata=df["QC"].values.reshape(-1, 1)`，
`_extract_sel` 從 `p["customdata"][0]` 讀取 qc 值。

---

## 7. 圖表與色系

### 樣式常數 `_C`（與主系統 §6 一致）

| 鍵 | HEX | 用途 |
|----|-----|------|
| `bg` | `#111820` | 全局底色、Tab 列背景 |
| `plot` | `#1E1E1E` | 圖表繪圖區、SQL 輸出區背景 |
| `panel` | `#1E2A3A` | 右側 QC 面板背景 |
| `header` | `#1A3A5C` | 頁首、查詢按鈕背景 |
| `border` | `rgba(200,214,229,0.25)` | 邊框、分隔線 |
| `text` | `#CCD0D4` | 一般文字 |

字體：`_FONT = "標楷體, PingFang TC, Noto Sans CJK TC, Arial, sans-serif"`
等寬：`_MONO = "標楷體, Courier New, Consolas, monospace"`（SQL 輸出、資料表名稱）

### 各 Tab 折線色

| Tab | 欄位 | 顏色 | 線型 | 預設顯示 |
|-----|------|------|------|---------|
| wave1 | H3 | `#1f77b4`（深藍）| lines+markers | ✅ |
| wave1 | HMAX | `#ff7f0e`（橘）| dash | legendonly |
| wind | VM | `#9467bd`（中紫）| lines+markers | ✅ |
| wind | VG | `#c5b0d5`（淡紫）| dash | legendonly |
| curr | V | `#17becf`（青）| lines+markers | ✅ |
| wave | H | `#2ca02c`（綠）| lines+markers | ✅ |
| pres1 | P | `#8b4513`（棕）| lines+markers | ✅ |
| stemp1 | T（氣溫 Z=-3）| `#ee7373`（珊瑚紅）| lines+markers | ✅ |
| stemp1 | T（海溫 Z≥0）| `#ff9896`（淡粉）| lines+markers | ✅ |

### `_LAYOUT_BASE` 共用設定

```python
_LAYOUT_BASE = dict(
    template="plotly_dark",
    paper_bgcolor=_C["plot"], plot_bgcolor=_C["plot"],
    font=dict(family=_FONT, color=_C["text"]),
    hovermode="x unified",
    height=520,
    margin=dict(l=60, r=40, t=40, b=40),
    dragmode="select",   # 預設啟用 Box Select
)
```

### 字體大小對照（HTML 元件，非 Plotly）

| 畫面元素 | 函式/行號 | 設定 |
|---------|---------|------|
| 頁首標題 | `html.Div("🌊..."` style | `fontSize: 16px` |
| 控制列標籤（測站/日期/Z值）| `_label()` 函式 | `fontSize: 14px` |
| 查詢按鈕 | `html.Button` style | `fontSize: 14px` |
| Tab 標籤（一般/選中）| `dcc.Tab` style / selected_style | `fontSize: 14px` |
| 狀態列 | `Div id="status-bar"` style | `fontSize: 14px` |
| 右側區塊標題（§A/§B/§C）| `_section()` 函式 title div | `fontSize: 14px` |
| 舊 QC 顯示（黃色）| `Div id="old-qc-display"` style | `fontSize: 15px` |
| 新 QC 輸入框 | `Input id="new-qc-input"` style | `fontSize: 14px` |
| SQL 輸出文字區 | `Textarea id="sql-output"` style | `fontSize: 14px` |
| Plotly 軸/tick（圖表內）| `_LAYOUT_BASE` `font=dict(...)` | `size` 未指定（Plotly 預設 12px）|

---

## 8. 打包注意事項

### 正確的 `build_buoy_qc.bat`

```bat
python -m PyInstaller --noconfirm --onedir --windowed --name buoy_qc_tool ^
  --collect-all dash ^
  --collect-all plotly ^
  --collect-all mysql.connector ^
  --collect-all babel ^
  --hidden-import flask ^
  --hidden-import flask_compress ^
  --hidden-import pandas ^
  --hidden-import numpy ^
  buoy_qc_app.py
pause
```

### 常見錯誤與原因

| 錯誤做法 | 症狀 | 原因 |
|---------|------|------|
| `--hidden-import dash / dash.dcc / dash.html` 代替 `--collect-all dash` | 右側面板消失、測站下拉永遠空白 | Dash 的 JS/CSS 資產（React runtime、callback 通訊層）靠 `--collect-all` 才會打包；`--hidden-import` 只處理 Python import，React 沒有初始化 → layout 的 `display:flex` 沒套到 DOM；callback 靜默失敗 |
| `--add-data "buoy_qc_dash.py;."` | `ENGINE` 注入失效，查詢全部出錯 | `.py` 已由 PyInstaller 編譯為 frozen bytecode；`--add-data` 再放一份原始碼，導致 frozen module 與 source module 各為不同實例，注入的 `ENGINE` 在另一個實例的 `ENGINE=None` 環境下跑 Dash |
| `--hidden-import tkcalendar` | 無害但多餘 | 本工具不使用 Tkinter |
| `--hidden-import mysql.connector` 代替 `--collect-all mysql.connector` | `No localization support for language 'eng'` | 同主系統，`mysql.connector` 需動態載入語系檔，`--hidden-import` 無法涵蓋 |

### Babel 語系設定

frozen 環境下與主系統相同，`BABEL_DATA_PATH` 由 `buoy_qc_app.py` 頂端處理（若有）。

---

## 9. 已知限制與 Plan B 待辦

| 優先度 | 項目 | 說明 |
|--------|------|------|
| 🟡 中 | **Plan A 過渡定位需對齊主管** | 主管偏好 Plan B（整合進主系統）；若不明確溝通，Plan A 有變成永久工具的風險 |
| 🟡 中 | **Plan B 尚未開始** | 整合進 `ocean_plot_dash.py`，含 `pres1` / `stemp1` HR 寬格式、`draw_diagnostic()` Dash 整合、`trace_meta` dcc.Store 多站修正；待 Plan A 穩定後開新對話 |
| 🟢 低 | DB 連線無自動重連 | `BuoyDataEngine.__init__` 建立後不偵測斷線；長時間閒置後查詢會拋 `OperationalError`，需重啟程式 |
| 🟢 低 | Dropdown 選項清單高度調整困難 | Dash 4.x Portal 渲染，`assets/custom.css` 的 `[class*="dropdown-menu"]` 選擇器未必命中，效果不保證 |
| ⬜ 追蹤中 | kind=7 浮球的資料表覆蓋範圍 | 目前 `wave1` Tab 支援 k7；其餘 Tab（wind/curr/wave/pres1/stemp1）若選到 k7 測站，查無資料時回傳空 df → 顯示「查無資料」，屬預期行為 |

---

*本文件依 `buoy_qc_app.py`、`buoy_qc_dash.py` 撰寫，
版本對應 2026-08-19 新增 pres1 / stemp1 兩個 Tab 後的狀態。
如有修改請同步更新本文件。*
