# 🌊 海洋動力診斷儀表板 — SYSTEM_DASH.md

> 技術文件版本：2026-08-14
> 架構版本：Tkinter + Dash（已從 Tkinter + Plotly HTML 遷移完成）

---

## 目錄

1. [系統架構概述](#1-系統架構概述)
2. [各模組職責](#2-各模組職責)
   - [ocean_plot_dash.py](#21-ocean_plot_dashpy)
   - [dash_bridge.py](#22-dash_bridgepy)
   - [dash_app.py](#23-dash_apppy)
   - [build_water_figure.py](#24-build_water_figurepy)
3. [Tkinter 與 Dash 的資料流](#3-tkinter-與-dash-的資料流)
4. [QC 框選產生 SQL 的機制](#4-qc-框選產生-sql-的機制)
5. [資料庫欄位與 QC 慣例速查](#5-資料庫欄位與-qc-慣例速查)
6. [視覺規範與樣式定義](#6-視覺規範與樣式定義-visual-standards)
7. [VdC 散佈圖模組（2026-05-25）](#7-vdc-散佈圖模組2026-05-25)
8. [暴潮偏差報表圖模組（2026-08）](#8-暴潮偏差報表圖模組2026-08)
9. [已知限制與待辦事項](#9-已知限制與待辦事項)
10. [打包注意事項（PyInstaller）](#10-打包注意事項pyinstaller)

---

## 1. 系統架構概述

本系統是一套**潮位監測資料視覺化與品管輔助工具**，串接 MySQL 資料庫，支援多測站、多儀器水位的觀測與預報比對，並提供互動式 QC 框選功能，讓操作員可在圖表上直接圈選異常資料段、自動產生對應的 SQL UPDATE 語句。

### 架構分層

```
┌──────────────────────────────────────────────────────────────┐
│  Tkinter GUI（主執行緒）                                        │
│  ocean_plot_dash.py                                           │
│  ├─ LoginWindow  → 建立 OceanDataEngine（DB 連線）             │
│  └─ MainApp      → 查詢控制 UI，選站、選期、選模式              │
│       ├─ go(mode="full")  → draw_diagnostic()  HTML 圖表       │
│       ├─ go(mode="water") → dash_bridge.set_bundle()          │
│       │                     webbrowser.open(Dash URL)          │
│       └─ go(mode="storm") → build_surge_report_figure()       │
│                             → 儲存至 surge_reports/ 並開啟     │
└──────────────────┬───────────────────────────────────────────┘
                   │  dash_bridge（同 process 內共用記憶體）
                   ▼
┌──────────────────────────────────────────────────────────────┐
│  Dash / Flask（daemon 執行緒，啟動於 MainApp.__init__）         │
│  dash_app.py                                                  │
│  ├─ dcc.Interval（每 500ms 輪詢）→ poll_bundle callback        │
│  │     └─ dash_bridge.get_latest_key() 偵測新資料             │
│  ├─ render_water_figure callback                              │
│  │     └─ build_water_figure.build_water_figure(bundles)      │
│  ├─ on_selection callback（Box Select → SQL 產生）            │
│  ├─ export_water_report callback → build_water_report_figure() │
│  └─ export_surge_report callback → build_surge_report_figure() │
└──────────────────────────────────────────────────────────────┘
```

### 技術選型重點

| 項目 | 舊架構 | 新架構（現行） |
|------|--------|--------------|
| 圖表渲染 | Plotly 寫入 temp HTML，webbrowser 開啟 | Dash `dcc.Graph`，Flask 在本地 serve |
| QC 框選回傳 | 自製 HTTP callback server + queue | Dash `selectedData` callback |
| 執行緒溝通 | `_selection_queue`（舊 plotly_qc_select.py）| `dash_bridge`（threading.Lock 保護的記憶體快取）|
| QC SQL 產生 | `SqlDialog` Tkinter Toplevel | Dash 右側 QC 面板 + `dcc.Textarea` |
| 模式切換 | 單一 go() 路徑 | `mode="water"` → Dash；`mode="full"` → HTML；`mode="storm"` → 暴潮偏差 PNG |

---

## 2. 各模組職責

### 2.1 `ocean_plot_dash.py`

**角色：主程式（Tkinter GUI + 資料層）**

#### `OceanDataEngine`（資料引擎類別）

所有 DB 操作集中在此類別，不含任何 UI 邏輯。

| 方法 | 說明 |
|------|------|
| `__init__()` | 建立 MySQL 連線，讀取測站對應表（CSV 或 DB 的 `stitemqc`，由 `.env` 的 `MAPPING_SRC` 控制） |
| `load_mapping()` | 載入 STID ↔ QCID 對應，並從 `st` 表查詢測站中文名稱 |
| `fetch_years()` / `fetch_typhoons()` | 查詢颱風資料庫（`med_data` 安外 / `mrbank` 安內，由 `TYPHOON_DB` env 控制） |
| `expand_data(df, val_name, freq)` | 將資料庫的寬表格（`MIN0`～`MIN9` 或 `HR` 欄位）展開為時序長格式。`id_cols` 自動攜帶所有非資料欄，包含 `QC`，是 QC 拆分前置作業的核心工具 |
| `fetch_tide_instruments(stid)` | 查詢測站下所有水位儀器（音波 type=2、壓力 type=3、雷達 type=4），以 `stid_obs` 為連結鍵 |
| `query_multi_tide_data()` | 為多儀器批量查詢 `tide6` 觀測值與 `tide6ha` 諧和/天文潮預報。在此完成 **QC 拆分**（`QC=Q` vs `QC≠Q`），並 outer merge 為並列欄位（`WL_{stid}` + `WL_{stid}_raw`） |
| `fetch_bundle(stid, start, end)` | 主要查詢入口。整合水位、氣壓、風、氣溫、海溫、波浪、海流，依 `candidates`（QCID + 自身）順序優先取用最佳資料源，最終回傳含完整時序資料的 bundle dict |

#### `MainApp`（Tkinter 主介面）

- 登入視窗（`LoginWindow`）：建立 `OceanDataEngine` 並取得 DB 連線
- 測站選單：從 CSV 或 DB 讀取的 `mapping_df` 動態填充
  - **動態活站篩選**：「只列有資料站」按鈕，依目前所選時間範圍即時查詢 `tide6` 資料表，僅保留有觀測紀錄的測站，節省無效查詢時間
  - **單位快速選取**：「選氣象署」與「非氣象署」按鈕，依 `sponsor_map` 對應之業務單位執行批次勾選
- 日期選擇：tkcalendar `DateEntry`，含防呆（日期順序、時間範圍上限）
- `go(mode)` 方法：
  - `mode="water"`：呼叫 `dash_bridge.set_bundle()`（含 `typhoon_info` 與 `thresholds_map`），以 `webbrowser.open()` 導向 Dash URL
  - `mode="full"`：呼叫 `draw_diagnostic()`，產生全參數的多子圖 Plotly HTML
  - `mode="storm"`：呼叫 `build_surge_report_figure()`，儲存至本機 `surge_reports/` 資料夾並以 `os.startfile()` 開啟；僅支援單站（多站時自動取第一站）

> **`mode="water"` 的附加資料傳遞**：`go()` 在呼叫 `set_bundle()` 前，會同步查詢颱風 info dict（從 `self.ty_cb` → `self.ty_df` 反查）和各站門檻值（`get_tsuwawa_thresholds(self.e.conn, stid)` 逐站查詢），一併存入 dash_bridge，供 Dash 端「匯出暴潮偏差圖」按鈕使用。

#### `draw_diagnostic()`（全參數圖）

使用 `make_subplots` 建立 n×2 子圖（水位、海氣象、暴潮偏差、波浪），直接以舊的 HTML 輸出路徑渲染，**不使用 Dash**。QC 紅叉標記與多儀器差值邏輯與 `build_water_figure.py` 保持一致。

---

### 2.2 `dash_bridge.py`

**角色：跨執行緒橋接層**

負責主進程（Tkinter）與子執行緒（Dash/Flask）間的資料交換。

- **滾動快取機制**：為防止記憶體無限增長，設定 `MAX_CACHE_SIZE = 10`。當存入新資料超過上限時，自動刪除最舊的 bundle，確保長效運作穩定。
- **執行緒安全**：使用 `threading.Lock` 確保在多使用者（或多瀏覽器分頁）同時讀取與 Tkinter 寫入時不會產生 Race Condition。
- **關鍵資料傳遞**：
    - `_bundle_cache`：以 `stid_timestamp` 為 Key 儲存多筆測站資料。
    - `_latest_key`：記錄最後一次寫入的 Key，供 Dash 端 `dcc.Interval` 輪詢偵測更新。
    - `_land_range`：同步傳遞颱風陸上警報時段，供繪圖模組標註紅色警報色帶。
    - `_typhoon_label`：目前選擇的颱風名稱與編號（如「丹娜絲(2504L)」），供 Dash 繪製標題或存取颱風屬性使用。
    - `_typhoon_info`：完整颱風事件 dict（`id` / `cname` / `warnSeaBeg` / `warnSeaEnd` / `warnLandBeg` / `warnLandEnd`），供暴潮偏差圖匯出使用。Tkinter `go(mode="water")` 在有颱風選取時傳入；未選颱風時為 `None`。
    - `_thresholds_map`：各測站的大潮注意值 / 警戒值（key = stid，值為 `{"警戒值_mm": float, "注意值_mm": float}` 或 `None`），由 Tkinter 在 `go()` 逐站呼叫 `get_tsuwawa_thresholds()` 後一併傳入。

**對應的 getter 函式：**

| 函式 | 回傳 |
|------|------|
| `get_bundle(key)` | bundle dict 或 None |
| `get_latest_key()` | 最新 key 字串或 None |
| `get_land_range()` | `(beg, end)` tuple 或 None |
| `get_typhoon_label()` | 字串或 None |
| `get_typhoon_info()` | dict 或 None |
| `get_thresholds_map()` | `{stid: dict|None}` 的 copy |
| `get_cache_count()` | int |

> **職責邊界**：本模組僅負責資料快取與線程調度，不涉及任何圖表渲染或 UI 佈局邏輯。

---

### 2.3 `dash_app.py`

**角色：Dash 儀表板（Flask 工作執行緒）**

#### 初始化

```python
app = Dash(__name__, title="海洋動力診斷儀表板", suppress_callback_exceptions=True)
```

以 `daemon=True` 的執行緒在 `MainApp.__init__()` 啟動，監聽 `127.0.0.1:{DASH_PORT}`（動態找可用 port，從 8050 開始往後搜尋）。

#### Layout 結構

```
app.layout
├─ dcc.Store(id="figure-store")          # 備用，未來可擴充
├─ dcc.Store(id="stid-store")            # 當前 STID（SQL 產生用）
├─ dcc.Store(id="bundle-key-store")      # 最新 bundle key
├─ dcc.Store(id="zoom-range-store")      # 水位圖目前 zoom x 範圍
├─ dcc.Interval(id="bundle-poll", 500ms) # 輪詢觸發器
├─ Header                                # 標題列
├─ 主內容
│   ├─ 左：dcc.Graph(id="main-graph")   # 水位圖表（3/4 寬）
│   └─ 右：QC 控制面板（1/4 寬）
│       ├─ §0 Y軸範圍手動控制
│       │     dcc.Input（上限/下限）+ 「套用」/「清除」按鈕
│       │     套用後同步調整所有水位子圖的垂直範圍
│       ├─ §A SQL 模式選擇（Mode 1 / 2 / 3）
│       ├─ §B Mode 1 參數（新 QC 值輸入）
│       ├─ §C Mode 2 參數（運算子 + 數值，預設隱藏）
│       ├─ §D 框選資訊列
│       ├─ §E SQL 輸出（dcc.Textarea + dcc.Clipboard 複製鈕）
│       ├─ §F 匯出一般白底圖（water-export-btn + dcc.Download）
│       └─ §G 匯出暴潮偏差圖（surge-export-btn + surge-export-status + dcc.Download）
└─ 底部狀態列
        └─ 顯示當前伺服器記憶體快取佔用狀態（例如：快取佔用：3 / 10）
```

#### Callbacks

| Callback | 觸發 | 作用 |
|----------|------|------|
| `toggle_mode_controls` | `qc-mode` RadioItems 變更 | 顯示/隱藏 Mode 1 / 2 / 3 對應參數區塊 |
| `poll_bundle` | `bundle-poll` Interval（每 500ms）| 從 `dash_bridge.get_latest_key()` 偵測新資料，有變化則更新 `bundle-key-store` |
| `render_water_figure` | `bundle-key-store`、`zoom-range-store` 更新 | 從 `dash_bridge.get_bundle()` 取回 bundles，呼叫 `build_water_figure()`，更新 `main-graph` |
| `capture_zoom` | `main-graph.relayoutData` | zoom / autorange 事件 → 更新 `zoom-range-store` |
| `on_selection` | `main-graph.selectedData` 變更 | 將 Box Select 範圍轉換為 `sel` dict，依 Mode 1 / 2 / 3 呼叫對應 SQL builder，結果寫入 `sql-output` |
| `apply_yaxis_range` | Y軸「套用」/「清除」按鈕 | Patch 所有水位子圖 yaxis 為手動輸入範圍，或還原自動縮放 |
| `export_water_report` | `water-export-btn.n_clicks` | 呼叫 `build_water_report_figure()`（白底），同步畫面圖例顯示狀態與 zoom x 範圍，下載 PNG |
| `export_surge_report` | `surge-export-btn.n_clicks` | 讀取 `typhoon_info` 與 `thresholds_map`，呼叫 `build_surge_report_figure()`；單站 → PNG，多站 → ZIP |
| `export_vdc_report` | `vdc-export-btn.n_clicks` | 呼叫 `build_vdc_report_figure()`，下載 PNG |

#### SQL 工具函式（獨立於 Tkinter）

| 函式 | 說明 |
|------|------|
| `_clean_ts(ts)` | 清理 Plotly 時間戳（`T`→空格、去毫秒），轉為 MySQL 可接受格式 |
| `build_mode1_sql(sel, stid, new_qc)` | Mode 1：產生按時間範圍與 y 值範圍篩選的 `UPDATE tide6 SET QC=...` |
| `build_mode2_sql_by_time(sel, stid, op, operand)` | Mode 2：從被框選點的展開 Time 反推 `DATATIME` 與 `MIN{N}` 欄位，產生 `MIN{N} = MIN{N} OP operand` |
| `build_mode3_sql(t_start, t_end, bundle_key, typhoon_label)` | Mode 3：對所有已載入 bundles 計算暴潮統計值，產生 `INSERT INTO mrbank.surge` 語句 |
| `_adapt_selected_data(selected_data)` | 將 Dash `selectedData` 格式轉換為 SQL builder 所需的 `sel` dict |

---

### 2.4 `build_water_figure.py`

**角色：純函式水位圖繪製器（無副作用）**

從舊版 `draw_water_only()` 移植，移除了寫 temp HTML 與開啟瀏覽器的副作用，直接回傳 `go.Figure` 供 `dcc.Graph` 消費。

#### `build_water_figure()`（互動深色版）

```python
build_water_figure(
    bundles: list,
    land_range: tuple | None = None
) -> go.Figure
```

| 參數 | 型別 | 說明 |
|------|------|------|
| `bundles` | `list[dict]` | `fetch_bundle()` 回傳值的清單，每個 bundle 含 `stid`、`stname`、`df`、`tide_meta` |
| `land_range` | `tuple \| None` | 颱風陸上警報時段 `(beg, end)`，無則傳 `None` |

**回傳：** `go.Figure`，可直接賦值給 `dcc.Graph(figure=...)`。`bundles` 為空時回傳帶說明文字的空白深色圖，不拋例外。

#### 子圖結構

每個 bundle 佔一列，`rows=n, cols=1`，`shared_xaxes=True`，`vertical_spacing=0.05`。每列為雙 y 軸（`secondary_y=True`），左軸水位、右軸儀器差值。

#### 每列 Trace 繪製順序（§1～§5）

**§1 各儀器水位（依 `tide_meta` 排序迭代）**

| Trace | 欄位 | 樣式 | 預設顯示 |
|-------|------|------|---------|
| 校正值（QC=Q） | `WL_{stid}` | 藍色系實線（主站）/虛線（備用），`connectgaps=False` | ✅ |
| 低頻趨勢（25h-MA） | `WL_{stid}_lf` | 半透明灰 `rgba(180,180,180,0.55)` | legendonly |
| EWMA（α=0.05） | `WL_{stid}_ewma` | 半透明金黃 `rgba(255,200,100,0.7)`，`connectgaps=True` | legendonly |
| 原始機測值（QC≠Q） | `WL_{stid}_raw` | 紅叉 `symbol="x" size=5`，hover 顯示 QC 代碼 | ✅ |
| 1H 平滑輔助線 | `WL_{stid}`（重採樣） | 校正值同色，含標準差 error bar，時間戳往後推 30min 置中 | legendonly |

**§2 預報水位（僅主測站，`is_primary=1`）**

| Trace | 欄位 | 樣式 | 預設顯示 |
|-------|------|------|---------|
| 諧和預報（QC=h） | `WL_{p_stid}_pred_h` | `#2ca02c` 綠色點線 | ✅ |
| 天文潮預報（QC=a） | `WL_{p_stid}_pred_a` | `#98df8a` 淺綠點線 | legendonly |

**§3 儀器差值（右 y 軸）**

掃描 `df.columns` 中所有 `Diff_` 前綴欄位，依序套用 `_DIFF_COLORS = ['#ff7f0e', '#e377c2', '#17becf']`，`mode="markers"`。

**§4 颱風陸上警報色帶**

`land_range` 不為 `None` 時，對當列加入紅色半透明 `vrect`（`fillcolor="Red", opacity=0.1`）。

**§5 Y 軸標題**

左軸「水位(mm)」，右軸「水位差值(mm)」（`showgrid=False`），兩軸 `fixedrange=False` 允許縮放。

#### 全局 Layout

```python
template="plotly_dark"
paper_bgcolor=plot_bgcolor="#1E1E1E"
height=600 * n            # 每站 600px
hovermode="x unified"
uirevision=True           # ⚠️ 待辦：應改為動態版本號，見 §9
rangeslider: 全部關閉     # fig.update_xaxes(rangeslider=dict(visible=False))
```

#### 舊系統降級路徑（`tide_meta` 為空時）

`fetch_tide_instruments()` 找不到 `stid_obs` 時，`fetch_bundle()` 回傳空 `tide_meta`。此時只繪製 `Obs`（藍線）和 `Pre`（綠點線）兩欄，跳過 §1～§5 的新系統 trace，避免 `KeyError` 崩潰。

#### `go.Scattergl` 注意事項

目前所有 trace 均使用 `go.Scattergl`（WebGL 加速）。若在特定環境下 Box Select 的 `selectedData` 回傳點數為 0，可逐條改為 `go.Scatter` 排查，代價是大資料集渲染效能下降。（原始碼中以 `# ← 從 Scatter 改回 Scattergl` 標示已還原的位置。）

#### `build_water_report_figure()`（白底靜態匯出版）

```python
build_water_report_figure(
    bundles: list,
    land_range: tuple | None = None,
    zoom_range: dict | None = None,
) -> go.Figure
```

與 `build_water_figure()` 邏輯相同，但套用白底報表樣式：

- `template="plotly_white"`、`paper_bgcolor="white"`、`plot_bgcolor="white"`
- 圖例、軸線、文字均調整為黑色系，適合簡報列印
- `zoom_range` 不為 `None` 時，縮限 X 軸顯示範圍；Y 軸仍自動縮放（已知限制，見 §9）

由 `export_water_report` callback 呼叫，同步畫面圖例的 visible 狀態（從 `main-graph.figure.data` 讀取各 trace 的 `visible` 欄位）。不同步 Y 軸範圍（同上已知限制）。

---

## 3. Tkinter 與 Dash 的資料流

### 資料流向

**Tkinter → Dash（單向推送）：**
- `MainApp` 查詢到資料（`bundle`）後，呼叫 `dash_bridge.set_bundle(key, bundle)` 將資料存入共用快取。
- `dash_app.py` 中的 `dcc.Interval` 定期輪詢 `dash_bridge.get_latest_key()`。
- 當 `key` 更新時，觸發 Dash callback（`render_figure`）從 `dash_bridge.get_bundle(key)` 讀取資料並更新圖表。

**獨立分頁（Session Isolation）：**
- 透過 URL 參數實作：`http://127.0.0.1:8050/?key={stid}_{timestamp}`。
- **優點**：每個開啟的瀏覽器分頁都「鎖定」在特定的查詢結果上，不會因為 Tkinter 發起新查詢而強制跳轉。這允許使用者同時開啟多個分頁進行測站對照。
- **Fallback 機制**：若 URL 無參數，則 Dash 會透過 `dcc.Interval` 輪詢並顯示最新的一筆查詢結果。

**Tkinter → Dash 操作流程：**
- `MainApp` 查詢完成後，產生唯一 Key 並呼叫 `dash_bridge.set_bundle`。
- 同時透過 `webbrowser.open` 打開帶有 Key 參數的網址。

**Dash → Tkinter（間接）：**
- Dash 應用程式本身不直接回傳資料給 Tkinter。
- Dash 負責顯示圖表和生成 SQL。生成的 SQL 語句由使用者手動複製。

### 埠號動態分配

```python
DASH_PORT = _find_free_port(start=8050)
```

程式啟動時從 8050 往後找第一個可用的 TCP 埠，避免多實例或埠被佔用時啟動失敗。Dash 啟動時也從環境變數 `DASH_PORT` 讀取同一個埠號。

---

## 4. QC 框選產生 SQL 的機制

### 流程說明

```
使用者在 dcc.Graph 上以 Box Select（□）框選區域
    │
    ▼
Dash: on_selection callback
    ├─ 接收 selectedData = {
    │       "points": [{x, y, curveNumber, ...}, ...],
    │       "range":  {"x": [start, end], "y": [lo, hi]}
    │  }
    ├─ _adapt_selected_data() 轉換格式
    │       → sel = {x_start, x_end, y_start, y_end, points: [{x, y}]}
    └─ 依 qc-mode 選擇 SQL builder
```

### Mode 1：更新 QC 旗標

**適用場景：** 整批標記某時間段內落在特定水位範圍的資料為異常（或恢復正常）。

**產生的 SQL：**

```sql
UPDATE tide6
SET    QC = {new_qc}
WHERE  STID     = '{stid}'
  AND  DATATIME BETWEEN '{t1}' AND '{t2}'
  AND  (   MIN0 BETWEEN {lo} AND {hi}
       OR  MIN1 BETWEEN {lo} AND {hi}
       ...
       OR  MIN9 BETWEEN {lo} AND {hi}
       );
```

- `t1`/`t2` 來自 Box Select 的 x 範圍（Plotly 時間戳清理後轉 MySQL 格式）
- `lo`/`hi` 來自 y 範圍，確保只更新落在框選區間的資料點

### Mode 2：MIN 欄位四則運算

**適用場景：** 精確修正特定展開時間點的 MIN 值（例如儀器系統偏移、單位換算錯誤）。
> ⚠️ **注意：** 目前此模式僅支援 `tide6` 資料表，不適用於 `wind`、`stemp6` 等其他資料表。

**反推邏輯：**

```
展開後的 Time → DATATIME（截到整點）+ MIN{N} 欄位
Time.minute // 6 = N
DATATIME = Time.replace(minute=0, second=0)
```

**產生的 SQL（按指令合併優化）：**

```sql
UPDATE tide6
SET    MIN3 = MIN3 + 0.5,
       MIN4 = MIN4 + 0.5
WHERE  STID     = '{stid}'
  AND  DATATIME IN ('2024-07-15 14:00:00', '2024-07-15 15:00:00', ...);
```

- 多個被框選的 Time 若屬同一 DATATIME，則合併為同一條 UPDATE（SET 多欄）
- 標頭注解說明共有幾筆 DATATIME、幾個欄位、使用何種運算
- **SQL 語法優化（IN 子句）**：對於 Mode 2，系統會自動歸類具有相同修改指令（SET 內容相同）的時間點，並合併為 `WHERE DATATIME IN ('...', '...')` 格式，大幅減少 SQL 筆數並提升執行效率。

### Mode 3：生成暴潮紀錄 INSERT SQL

**適用場景：** 颱風事件結束後，對所有已載入測站批量產生暴潮統計資料的 INSERT 語句，供人工審核後寫入 `mrbank.surge`。

**觸發方式：** 在 Dash 右側面板選擇 Mode 3 後，以 Box Select 框選目標時間範圍（或不框選使用全時段），SQL 即自動產生。

**計算邏輯（`build_mode3_sql()` 函式）：**

- 從 box select 的 x 範圍（`t_start` / `t_end`）決定計算窗口；若無框選則使用全時段
- 對每個已載入 bundle，計算框選窗口內的：

| 欄位 | 計算方式 |
|------|---------|
| `MAXRISE` | 觀測水位（`WL_{stid}`）最大值（mm） |
| `MAXRISET` | 發生 `MAXRISE` 的時間戳 |
| `MAXDEV` | 暴潮偏差（優先使用 `Resi`，即 Obs − pred_h）最大值（mm） |
| `MAXDEVT` | 發生 `MAXDEV` 的時間戳 |
| `MAXNEG` | 暴潮偏差最小值（mm，負向最大偏差） |
| `MAXNEGT` | 發生 `MAXNEG` 的時間戳 |
| `CNAME` / `ID` | 從 `typhoon_label`（如 `丹娜絲(2504L)`）解析 |

**產生的 SQL（每站一條 INSERT）：**

```sql
-- 測站 1176 丹娜絲(2504L)
INSERT INTO mrbank.surge
(ID, STID, MAXRISE, MAXRISET, MAXDEV, MAXDEVT, MAXNEG, MAXNEGT,
 QC, CNAME, KIND, PATH, INTENSITY, SPRING, PRES, VC, R7)
VALUES (
  '2504L', '1176', 2350, '2025-07-24 03:00:00',
  890, '2025-07-24 03:12:00', -120, '2025-07-23 22:00:00',
  'a', '丹娜絲', NULL, NULL, NULL, NULL, NULL, NULL, NULL
);
```

- `QC='a'`（自動計算，待人工核閱後依實際情況調整）
- `KIND`、`PATH`、`INTENSITY`、`SPRING`、`PRES`、`VC`、`R7` 固定輸出 NULL，需人工填寫
- 多站時各站 INSERT 以兩個換行分隔

### `stid-store` 的更新時機

目前 `stid-store` 初始值為 `_DEMO_STID = "1176"`。Tkinter 呼叫 `set_bundle()` 時**尚未**同步更新 `stid-store`；SQL builder 中的 `stid` 會先使用 `stid-store` 的值，若為空則 fallback 到 `_DEMO_STID`。

> ⚠️ **待辦：** `render_water_figure` callback 取得 bundle 後應同步更新 `stid-store`，確保 SQL 產生時使用正確測站代碼。

---

## 5. 資料庫欄位與 QC 慣例速查

| 資料表 | QC 欄位 | 合格值 | 說明 |
|--------|---------|--------|------|
| `tide6` | `QC`（大寫）| `'Q'`（大寫） | 水位 6min 觀測，mysql.connector 回傳小寫，需 `.upper()` 比對 |
| `tide6ha` | `QC` | `'h'`（諧和預報）/ `'a'`（天文潮）| 水位預報 |
| `wind` | `qc`（小寫）| `'Q'`（大寫）| 風速風向，比對時需 `.upper()` |
| `stemp6` | `QC` | `'Q'`（大寫）| 6min 溫度，潮位站用，有 QC 拆分邏輯 |
| `stemp1` | 無 | — | 浮標 1h 溫度，上游已做品管，不進行 QC 拆分 |
| `meteo` | 無 | — | 氣象站，直接使用，無 QC 欄位 |
| `pres6` | `QC` | `'Q'` | 氣壓（潮位站）|
| `curr` | 無 | — | 海流，上游已做品管 |
| `wave` | 無 | — | 波浪，上游已做品管 |

### mysql.connector 小寫欄位名問題

`mysql.connector` 原生連線（非 SQLAlchemy）執行 `pd.read_sql()` 時，**回傳的欄位名稱會被自動轉為小寫**，導致直接以大寫欄位名存取時拋出 `KeyError`。

**標準修正方式（每次 `read_sql` 後立即執行）：**

```python
df_obs.columns = [c.upper() for c in df_obs.columns]
```

此行已加入所有讀取 `tide6`、`stemp6`、`stemp1` 的程式碼段後。

> 完整資料表欄位定義請參閱 SYSTEM.md §十。

---

## 6. 視覺規範與樣式定義 (Visual Standards)

本系統採用 Dark Mode 視覺風格，針對長時間監控需求設計，並針對 Windows 環境下的中文字體顯示進行優化。

**a. 核心配色表 (Color Palette)**

```
┌──────────────────────┬────────────────────────┬────────────────────────────────┐
│ 項目                 │ HEX 色碼               │ 視覺用途                       │
├──────────────────────┼────────────────────────┼────────────────────────────────┤
│ Global Background    │ #111820                │ 系統底色、下拉選單、狀態列背景 │
│ Plot/Text Background │ #1E1E1E                │ 圖表繪圖區背景、SQL 輸出區背景 │
│ Panel Background     │ #1E2A3A                │ 右側 QC 控制面板背景           │
│ Header/Accent        │ #1A3A5C                │ 頂部標題列、區塊標題底色       │
│ Border/Highlight     │ rgba(200,214,229,0.25) │ 邊框、格線、半透明強調色       │
│ Primary Text         │ #CCD0D4                │ 一般標籤與選單文字色           │
└──────────────────────┴────────────────────────┴────────────────────────────────┘
```

**b. 字體系統 (Typography)**

為確保在 Windows 等寬環境下 SQL 指令與測站清單能精確對齊，優先選用「標楷體」。

- UI 字體：標楷體, Noto Sans TC, Segoe UI, Arial, sans-serif
- 等寬字體：標楷體, Courier New, Consolas, monospace（應用於 SQL 輸出、測站代碼對齊）
- 圖表字體：標楷體, PingFang TC, Noto Sans CJK TC, Arial, sans-serif

**c. 圖表組件規範 (Plotly Styles)**

- 範本 (Template)：統一套用 `plotly_dark`
- 交互模式：`hovermode='x unified'`（十字準星）且 `uirevision=True`（鎖定縮放視圖）
- CSS 覆蓋：透過 `assets/custom.css` 強制修正 Dash Dropdown 預設亮色樣式，確保選單背景為 `#111820` 並具備懸停高亮效果

---

## 7. VdC 散佈圖模組（2026-05-25）

### 新增檔案：`build_vdc_figure.py`

**職責：** 純函式繪圖模組，輸入 bundles 產出 Van de Casteele 散佈圖的 `go.Figure`。

**函式簽名：**
```python
build_vdc_figure(
    bundles: list,
    diff_type: str = "auto",   # "auto" | "雷達式" | "壓力式"
    zoom_range: dict | None = None  # {"x_start": str, "x_end": str}
) -> tuple[go.Figure, dict]
```

**副儀器選擇邏輯（diff_type）：**
- `"auto"`：優先雷達式（type=4），無雷達則用壓力式（type=3）
- `"雷達式"` / `"壓力式"`：強制指定，找不到時子圖顯示提示訊息

**X 軸範圍：各站獨立，mean ± 3σ**

每站以自身差值序列計算 `x_half = max(|mean| + 3×std, 1.0)`，套用於該子圖。使用者可透過 UI 手動覆蓋為統一對稱範圍（見 C 方案 callback）。

**回歸線：**

使用 `scipy.stats.linregress(WL, diff)` 計算，在 VdC 圖上疊加淡藍色虛線。斜率（a）與 R² 同步存入 `stats_summary` 並顯示於右側面板。

**回傳 stats_summary 結構：**
```python
{
  stid: {
    "status": "success" | "no_data" | "no_fields" | "empty_data",
    "count": int,
    "mean": float,   # mm
    "std": float,    # mm
    "min": float,
    "max": float,
    "slope": float | None,   # 回歸斜率（mm/mm，無因次）
    "r2": float | None,      # 決定係數
  }
}
```

**時間篩選（zoom 聯動）：**

`zoom_range` 不為 None 時，各站 `sub_df` 在 `dropna()` 後以 `t0 ≤ Time ≤ t1` 過濾，實現水位時序圖 zoom → VdC 自動重繪。

---

### dash_app.py 新增元件與 Callbacks

**新增 Store：**
- `dcc.Store(id="zoom-range-store", data=None)`：記錄水位圖目前的 zoom x 範圍

**新增 Callbacks：**

| Callback | 觸發 | 作用 |
|----------|------|------|
| `capture_zoom` | `main-graph.relayoutData` | 水位 tab 的 zoom/autorange 事件 → 更新 `zoom-range-store` |
| `apply_vdc_x_range` | `vdc-x-apply-btn` / `vdc-x-clear-btn` | Patch 所有 VdC 子圖 xaxis 為統一對稱範圍或還原自動 |

**`render_figure` 新增 Input：**

`zoom-range-store` 加為 Input，切換 tab 或 zoom 後自動重繪 VdC。

**VdC Tab 新增 UI 元素：**
- `dcc.RadioItems(id="vdc-diff-type")`：副儀器類型選擇
- `dcc.Input(id="vdc-x-range")`：X 軸對稱範圍 ±N mm
- `html.Button` × 2：`vdc-x-apply-btn` / `vdc-x-clear-btn`
- `html.Div(id="vdc-x-status")`：套用結果提示
- `html.Div(id="vdc-stats-output")`：各站統計摘要（N / 平均差 / σ / 回歸斜率 / R²）

---

### 已知待辦

| 優先度 | 項目 |
|--------|------|
| 🔵 追蹤 | （無待辦） |

---

### 匯出 PNG 報表功能（2026-05）

**觸發方式：** VdC tab 右側面板「📥 匯出 PNG 報表」按鈕

**輸出內容：** n 列 × 2 欄靜態圖，每列對應一個測站：
- 左欄：VdC 散佈圖（含回歸線，X 軸採各站獨立 mean ± 3σ 範圍）
- 右欄：差值分佈直方圖（機率密度正規化）+ 常態曲線疊加 + 平均值標線

**檔名格式：** `VdC_report_YYYYMMDD.png`

**相依套件：** `kaleido`（Plotly 靜態圖片匯出後端）。測試環境實裝版本為 `1.2.0`，運作正常。原始建議版本為 `0.2.1`，兩者 API 相容，受限環境無法安裝新版可降版測試。

**新增函式：`build_vdc_report_figure()`**

位於 `build_vdc_figure.py`，獨立於 `build_vdc_figure()` 之外，不影響互動圖邏輯。

```python
build_vdc_report_figure(
    bundles: list,
    diff_type: str = "auto",
    zoom_range: dict | None = None,
) -> go.Figure
```

- 內部重跑主副儀器選取與 zoom 時間篩選，確保匯出圖與畫面所見一致
- 使用 `go.Histogram(histnorm="probability density")` + `scipy.stats.norm.pdf` 疊加常態曲線
- 輸出解析度：`scale=2`，寬 1400px，高 420px × 站數

**新增元件（`dash_app.py`）：**

| 元件 | ID | 說明 |
|------|-----|------|
| `html.Button` | `vdc-export-btn` | 觸發匯出 |
| `dcc.Download` | `vdc-download` | 瀏覽器下載橋接（`dcc` 內建，無需額外 import）|

**新增 Callback：`export_vdc_report`**

| 項目 | 值 |
|------|-----|
| Output | `vdc-download.data` |
| Input | `vdc-export-btn.n_clicks` |
| State | `bundle-key-store`、`vdc-diff-type`、`zoom-range-store` |
| 回傳 | `dcc.send_bytes(png_bytes, filename)` |

---

## 8. 暴潮偏差報表圖模組（2026-08）

### 新增檔案：`build_surge_report_figure.py`

**職責：** 純函式繪圖模組，輸入單站 bundle、颱風資訊、門檻值，產出適合簡報列印的白底暴潮偏差 PNG。

---

### 輔助函式：`get_tsuwawa_thresholds()`

```python
get_tsuwawa_thresholds(
    conn,        # mysql.connector 連線物件
    stid: str,
) -> dict | None
```

查詢 `tsuwawa.warn` 表，取得指定測站的大潮警戒（`WARNVAL`）與注意（`STIDE`）水位門檻。

**回傳格式（單位 mm）：**
```python
{
    "警戒值_mm": float,   # WARNVAL × 1000（原始單位為 m）
    "注意值_mm": float,   # STIDE × 1000
}
```

查無資料時回傳 `None`。圖上相應警戒線不繪製，不拋例外。

---

### 主函式：`build_surge_report_figure()`

```python
build_surge_report_figure(
    bundle: dict,
    typhoon_info: dict,
    thresholds: dict | None,
) -> go.Figure
```

| 參數 | 型別 | 說明 |
|------|------|------|
| `bundle` | `dict` | `fetch_bundle()` 回傳的單站 bundle |
| `typhoon_info` | `dict` | 颱風事件資訊（`id`, `cname`, `warnSeaBeg`, `warnSeaEnd`, `warnLandBeg`, `warnLandEnd`） |
| `thresholds` | `dict \| None` | `get_tsuwawa_thresholds()` 的回傳值；`None` 時不繪製警戒線 |

**回傳：** `go.Figure`（白底，單站單面板，雙 y 軸）。

#### 圖表設計

**背景與樣式：**
- `template="plotly_white"`（或手動設 `paper_bgcolor="white"` / `plot_bgcolor="white"`）
- 字型：Microsoft JhengHei（安內環境有此字型，確保標題中文不亂碼）
- 輸出尺寸：`width=1400, height=500, scale=2`

**標題格式（兩行置中）：**
```
{cname}（{id}）暴潮偏差分析
測站：{stid_display}（舊站碼 {stid_obs} / 新站碼 {stid_new}）
```
`stid_obs` 與 `stid_new` 從 `bundle['tide_meta']` 的主站 meta 取得（需 `query_multi_tide_data` 已將這兩個欄位寫入 tide_meta）。

**Trace 設計（左 y 軸：水位 mm；右 y 軸：暴潮偏差 mm）：**

| Trace | 欄位 | 樣式 | y 軸 |
|-------|------|------|------|
| 觀測水位（QC=Q） | `WL_{primary_stid}` | 藍色實線 | 左 |
| 諧和預報（QC=h） | `WL_{primary_stid}_pred_h` | 深綠點線 | 左 |
| 高潮位標記 | 局部極大值三角形 ▲ | `#ff7f0e` 橘色，默認 legendonly | 左 |
| 低潮位標記 | 局部極小值三角形 ▽ | `#9467bd` 紫色，默認 legendonly | 左 |
| 暴潮偏差（Resi） | `Resi` = WL − pred_h | `#e74c3c` 紅色實線 | 右 |

**警戒門檻線（左 y 軸，水平 hline）：**

| 線 | 值 | 樣式 |
|----|-----|------|
| 大潮警戒值 | `thresholds["警戒值_mm"]` | 深橘色 `#e67e22`，dash，寬 1.5 |
| 大潮注意值 | `thresholds["注意值_mm"]` | 琥珀色 `#f39c12`，dash，寬 1.5 |

`thresholds` 為 `None` 時跳過，不拋例外。

**警報時段色帶（vrect）：**

| 色帶 | 時間範圍 | 顏色 |
|------|---------|------|
| 海上警報（海警） | `warnSeaBeg` ～ `warnSeaEnd` | 淡藍 `rgba(100,149,237,0.15)` |
| 陸上警報（陸警） | `warnLandBeg` ～ `warnLandEnd` | 淡橘紅 `rgba(255,100,50,0.12)` |

警報時間為 `None` 時（未選颱風或無該類警報）跳過對應 vrect。

**警報區標注（annotation）：**
- 海警時段於圖頂 `y=1.09` 位置標記文字「⚓ 海上警報」
- 陸警時段於 `y=1.03` 位置標記文字「🏠 陸上警報」

---

### 兩種呼叫路徑

#### 路徑 A：Tkinter `mode="storm"`（本機儲存）

在 `MainApp.go(mode="storm")` 中觸發：

1. 僅支援單站（多站查詢時自動取第一站，並顯示提示）
2. 解析 `self.ty_cb.get()` 取颱風資訊；若未選颱風則以「未指定颱風」填充 dict
3. 呼叫 `get_tsuwawa_thresholds(self.e.conn, stid)` 取門檻值
4. 呼叫 `build_surge_report_figure(bundle, typhoon_info, thresholds)`
5. 輸出至 `{BASE_DIR}/surge_reports/surge_{stid}_{ty_id}_{YYYYMMDD}.png`
6. `os.startfile()` 開啟圖檔，`messagebox.showinfo()` 顯示路徑

#### 路徑 B：Dash `export_surge_report` callback（瀏覽器下載）

按鈕位置：水位 tab 右側 QC 面板 §G「🌊 匯出暴潮偏差圖 PNG」。

| 項目 | 值 |
|------|-----|
| Output | `surge-download.data`、`surge-export-status.children` |
| Input | `surge-export-btn.n_clicks` |
| State | `bundle-key-store` |
| 資料來源 | `dash_bridge.get_typhoon_info()`、`dash_bridge.get_thresholds_map()` |

**多站處理（ZIP）：**
- 單站 → 下載 `Surge_{stid}_{timestamp}.png`
- 多站 → 對每站各自生成 PNG，打包為 `Surge_{ty_id}_{timestamp}.zip`（`zipfile.ZIP_DEFLATED`）

**錯誤處理：**
- `typhoon_info` 為 `None`（未選颱風即按水位模式查詢）→ `surge-export-status` 顯示警告，不觸發下載
- `thresholds_map` 不含某站 → 對該站傳入 `None`，圖上不顯示警戒線（不中止整批匯出）

---

### dash_bridge.py 新增欄位

| 欄位 | 型別 | 傳入時機 | 對應 getter |
|------|------|---------|------------|
| `_typhoon_info` | `dict \| None` | `go(mode="water")` 且有颱風選取時 | `get_typhoon_info()` |
| `_thresholds_map` | `dict` | `go(mode="water")` 同步查詢各站門檻值 | `get_thresholds_map()` |

`set_bundle()` 簽名對應更新：

```python
def set_bundle(key, bundle, land_range=None, typhoon_label=None,
               typhoon_info=None, thresholds_map=None) -> None
```

---

### 設計決策記錄

| 決策 | 結論 | 理由 |
|------|------|------|
| 暴潮偏差圖是否跟隨水位圖 zoom 範圍 | **不跟隨（設計決策）** | 颱風報告需呈現完整事件期間，局部裁切會導致海陸警色帶不完整，誤導判讀 |
| PNG 匯出是否同步 Y 軸手動範圍 | **不同步（已知限制）** | Y 軸 Patch 寫入後無法從 figure dict 直接反查範圍；對簡報用途影響小，列入限制記錄 |

---

## 9. 已知限制與待辦事項

### 功能面

| 優先度 | 項目 | 說明 |
|--------|------|------|
| 🔴 高 | `stid-store` 未與 bundle 同步 | SQL 產生時 STID 可能仍為 Demo 值（1176）。應在 `render_water_figure` 中同步更新 `stid-store` |
| 🔴 高 | Mode 2 只支援 tide6 | `build_mode2_sql_by_time` 硬寫 `UPDATE tide6`，不支援 wind / stemp6 等表 |
| 🟡 中 | `draw_diagnostic` QC 框選失效 | 全參數模式（mode="full"）仍以舊 HTML 路徑輸出，QC 框選功能不可用 |
| 🟡 中 | 多測站 bundle 的 STID 切換 | 同時查詢多測站時，QC 面板的 STID 未提供切換 UI，只使用第一筆 |
| 🟡 中 | 溫度顏色與紅叉衝突 | 氣溫（深紅）和海溫（淺粉）色系與 QC 紅叉視覺上相近，建議遷移至橘棕色系（`#e6740a` / `#ffbb78`）|
| 🟡 中 | PNG 匯出 Y 軸範圍未同步 | 手動調整的 Y 軸範圍或 zoom in 縮放均不會反映在匯出的白底 PNG 中；X 軸 zoom 範圍有同步（見 §2.4）。修正需從 figure dict 反查 layout.yaxis 範圍，複雜度偏高，暫不修正 |
| 🟢 低 | `uirevision` 應為動態版本號 | 目前固定為 `True`，多次 push 新資料後縮放狀態可能不正確重置 |
| 🟢 低 | `DEBUG print` 未清除 | `query_multi_tide_data` 內有多行 `print("[DEBUG]...")` 尚未移除 |
| 🟢 低 | `bundle-poll` 500ms 效率 | 若 Dash 與 Tkinter 在同機執行，可縮短至 250ms；未來可改以 websocket 推送取代輪詢 |

### 架構面

- `dash_app.py` 中保留了 `OceanDataEngine` Stub 類別，整合後可移除（或改 import 真實版本）
- `plotly_qc_select.py`（舊版）應在確認功能完整遷移後從專案中刪除
- `draw_diagnostic()` 的 HTML 輸出路徑若要支援 QC 框選，需整合至 Dash 或維持舊版 HTTP callback server 並行運作

---

## 10. 打包注意事項（PyInstaller）

### mysql.connector 必須使用 `--collect-all`

**錯誤做法（無效）：**

```
pyinstaller --hidden-import mysql.connector ...
```

**正確做法：**

```
pyinstaller --collect-all mysql.connector ...
```

`mysql.connector` 在執行時會動態載入語系檔（locale files）與驅動模組，`--hidden-import` 只能處理靜態 import，**無法涵蓋這些執行期資源**，打包後連線失敗時會因語系檔缺失觸發次生錯誤。

<!-- ### `--onefile` → `--onedir`：解決安內首次啟動極慢問題（2026-07）

**部署鏈澄清**：本專案在安外 Windows 開發機（.183，Python 3.13）打包成 `.exe`，
移動到安內 Windows PC（.142，無法安裝 Python）執行，串接安內資料庫（.71）。
`.142` 是使用者實際雙擊執行的機器，也是本節效能問題的觀測點。

**症狀**：`--onefile` 打包版本在 .142 上雙擊執行後，登入畫面需等待約 **30 秒**才出現
（實測值），後續操作皆流暢。等待期間容易讓人誤判為滑鼠沒點到、軟體當機或電腦當機。

**成因**：`--onefile` 每次執行都會先將整個打包內容解壓縮到系統 temp 資料夾，
再從 temp 執行。本專案疊了 dash + plotly + pandas + numpy + scipy + mysql-connector
+ babel，解壓縮體積大（`--onedir` 攤開後約 383MB），因此每次啟動都要重複付出這段
解壓時間。

**修正**：`build.bat` 將 `--onefile` 改為 `--onedir`：

```diff
- python -m PyInstaller --noconfirm --onefile --windowed --name ocean_plot_dash ...
+ python -m PyInstaller --noconfirm --onedir --windowed --name ocean_plot_dash ...
```

其餘所有 `--hidden-import` 與 `--collect-all babel` / `--collect-all mysql.connector`
**保持不變**——這些是解決 Dash/Babel/mysql.connector 執行期問題的必要項，與啟動速度
無關，不可因瘦身而移除，否則可能重現次生錯誤（見下節）。

**實測效果**：首次啟動時間由 **30 秒降至 4 秒**。建置（打包）耗時本身兩者理論上相近
（`--onefile` 只是多一道「壓縮為單一 exe」的最後步驟，理論上應略久於 `--onedir`）；
若實測感覺 `--onedir` 建置反而明顯較久，優先檢查建置機（.183）的防毒軟體是否對
`build/`、`dist/` 產生的大量小檔案做即時掃描，而非模式本身的問題。

**部署方式變更**：
- 舊：部署單一 `ocean_plot_dash.exe`
- 新：`dist/ocean_plot_dash/` 整個資料夾需壓縮為 `.zip` ；於 .142 解壓縮後
  雙擊資料夾內的 `.exe` 執行。**`.exe` 不可單獨移出資料夾**，需與旁邊的 `.dll`／
  子資料夾放在一起。
- **`.142` 資安限制**：不建議安裝第三方解壓縮工具（如 7-Zip），需用系統內建方式解壓，
  例如以系統管理員權限開啟 PowerShell 執行 `Expand-Archive`。

**`--onefile` 是否仍有保留價值？** 唯一優勢是「使用者只看到單一檔案」，但本專案的
部署流程本來就需要解壓縮 `.zip`，這項優勢在實際交付流程中並不成立；且啟動時間差距
（30 秒 vs 4 秒）的使用者體驗成本遠高於檔案外觀整潔與否，故正式改採 `--onedir`。

**建置快取建議**：每次重新打包前建議先清空專案下的 `build/` 與 `dist/` 目錄再執行，
避免沿用舊版分析快取。 -->

### 次生錯誤症狀與誤導風險

打包後連線失敗時，錯誤訊息可能顯示為：

```
No localization support for language 'eng'
```

這個錯誤與**實際的連線問題無關**（如密碼錯誤、網路不通、host 無法解析），卻會出現在最上層，容易讓除錯方向偏向語系設定，而非真正原因。

### 診斷方法：在 `login()` 的 except 區塊寫入完整 traceback

```python
except Exception as e:
    import traceback
    with open("error_log.txt", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
    raise
```

打包後執行，若連線失敗，可於執行檔同目錄查看 `error_log.txt` 取得完整堆疊，再針對真正原因排查。

### 其他打包注意

- `babel` 語系資料：`frozen` 環境下需設定 `BABEL_DATA_PATH`（程式頂端已處理）
- `.env` 檔與 `對應站表格.csv` 必須與執行檔放在同一目錄（`BASE_DIR` 以 `sys.executable` 所在目錄為準）
- Tkinter + Dash 雙執行緒打包後，`use_reloader=False` 是必要的（已設定）；`debug=False` 在整合執行時也必須關閉

---

*本文件依 ocean_plot_dash.py、dash_bridge.py、dash_app.py、build_water_figure.py、build_vdc_figure.py、build_surge_report_figure.py 六個檔案原始碼撰寫，如有修改請同步更新。*
