# 🌊 海洋動力診斷儀表板 — SYSTEM.md

> 技術文件版本：2025-05  
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
6. [已知限制與待辦事項](#6-已知限制與待辦事項)
7. [打包注意事項（PyInstaller）](#7-打包注意事項pyinstaller)

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
│       └─ go(mode="water") → dash_bridge.set_bundle()          │
│                             webbrowser.open(Dash URL)          │
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
│  └─ on_selection callback（Box Select → SQL 產生）            │
└──────────────────────────────────────────────────────────────┘
```

### 技術選型重點

| 項目 | 舊架構 | 新架構（現行） |
|------|--------|--------------|
| 圖表渲染 | Plotly 寫入 temp HTML，webbrowser 開啟 | Dash `dcc.Graph`，Flask 在本地 serve |
| QC 框選回傳 | 自製 HTTP callback server + queue | Dash `selectedData` callback |
| 執行緒溝通 | `_selection_queue`（舊 plotly_qc_select.py）| `dash_bridge`（threading.Lock 保護的記憶體快取）|
| QC SQL 產生 | `SqlDialog` Tkinter Toplevel | Dash 右側 QC 面板 + `dcc.Textarea` |
| 模式切換 | 單一 go() 路徑 | `mode="water"` → Dash；`mode="full"` → HTML |

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
- 日期選擇：tkcalendar `DateEntry`，含防呆（日期順序、時間範圍上限）
- `go(mode)` 方法：
  - `mode="water"`：呼叫 `dash_bridge.set_bundle()`，以 `webbrowser.open()` 導向 Dash URL
  - `mode="full"`：呼叫 `draw_diagnostic()`，產生全參數的多子圖 Plotly HTML

#### `draw_diagnostic()`（全參數圖）

使用 `make_subplots` 建立 n×2 子圖（水位、海氣象、暴潮偏差、波浪），直接以舊的 HTML 輸出路徑渲染，**不使用 Dash**。QC 紅叉標記與多儀器差值邏輯與 `build_water_figure.py` 保持一致。

---

### 2.2 `dash_bridge.py`

**角色：執行緒安全的資料快取橋接層**

```
Tkinter 主執行緒           Dash callback 執行緒
    set_bundle(key, bundle)  →  get_bundle(key)
    set_bundle(..., land_range)  →  get_land_range()
    get_latest_key()         ←  poll_bundle callback 輪詢
```

- 以 `threading.Lock` 保護所有讀寫，防止 race condition
- 快取結構：`_bundle_cache: dict[str, Any]`，支援多 key 共存
- `_latest_key`：最後一次寫入的 key，供 Dash poll callback 偵測更新
- `land_range`：颱風陸上警報時段，與 bundle 同步傳遞，用於繪製紅色警報色帶

> **職責邊界：** 本模組**只做存取**，不含任何繪圖、layout 或 Dash 元件邏輯。

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
├─ dcc.Interval(id="bundle-poll", 500ms) # 輪詢觸發器
├─ Header                                # 標題列
├─ 主內容
│   ├─ 左：dcc.Graph(id="main-graph")   # 水位圖表（3/4 寬）
│   └─ 右：QC 控制面板（1/4 寬）
│       ├─ §A SQL 模式選擇（Mode 1 / 2）
│       ├─ §B Mode 1 參數（新 QC 值輸入）
│       ├─ §C Mode 2 參數（運算子 + 數值，預設隱藏）
│       ├─ §D 框選資訊列
│       └─ §E SQL 輸出（dcc.Textarea + dcc.Clipboard 複製鈕）
└─ 底部狀態列
```

#### Callbacks

| Callback | 觸發 | 作用 |
|----------|------|------|
| `toggle_mode_controls` | `qc-mode` RadioItems 變更 | 顯示/隱藏 Mode 1 或 Mode 2 參數區塊 |
| `poll_bundle` | `bundle-poll` Interval（每 500ms）| 從 `dash_bridge.get_latest_key()` 偵測新資料，有變化則更新 `bundle-key-store` |
| `render_water_figure` | `bundle-key-store` 更新 | 從 `dash_bridge.get_bundle()` 取回 bundles，呼叫 `build_water_figure()`，更新 `main-graph` |
| `on_selection` | `main-graph.selectedData` 變更 | 將 Box Select 範圍轉換為 `sel` dict，呼叫對應 SQL builder，結果寫入 `sql-output` |

#### SQL 工具函式（獨立於 Tkinter）

| 函式 | 說明 |
|------|------|
| `_clean_ts(ts)` | 清理 Plotly 時間戳（`T`→空格、去毫秒），轉為 MySQL 可接受格式 |
| `build_mode1_sql(sel, stid, new_qc)` | Mode 1：產生按時間範圍與 y 值範圍篩選的 `UPDATE tide6 SET QC=...` |
| `build_mode2_sql_by_time(sel, stid, op, operand)` | Mode 2：從被框選點的展開 Time 反推 `DATATIME` 與 `MIN{N}` 欄位，產生 `MIN{N} = MIN{N} OP operand` |
| `_adapt_selected_data(selected_data)` | 將 Dash `selectedData` 格式轉換為 SQL builder 所需的 `sel` dict |

---

### 2.4 `build_water_figure.py`

**角色：純函式水位圖繪製器（無副作用）**

`build_water_figure(bundles, land_range=None) → go.Figure`

從 `draw_water_only()` 移植而來，**移除**了寫 temp HTML 與開啟瀏覽器的副作用，改為直接回傳 `go.Figure` 供 `dcc.Graph` 消費。

#### 子圖結構

每個 bundle 佔一列（`rows=n, cols=1`），共享 x 軸，雙 y 軸（左：水位，右：儀器差值）。

#### 每列 Trace 說明

| Trace | 樣式 | 說明 |
|-------|------|------|
| 校正值（QC=Q）| 藍色系實線/虛線 | 以 `WL_{stid}` 欄位繪製，`connectgaps=False` 讓缺值區間自然斷線 |
| 低頻趨勢（25h MA）| 半透明灰線 | `WL_{stid}_lf`，預設 `visible="legendonly"` |
| EWMA（α=0.05） | 半透明橘線 | `WL_{stid}_ewma`，預設隱藏 |
| 原始機測值（QC≠Q） | 紅叉（`symbol="x"`）| `WL_{stid}_raw`，hover 顯示 QC 代碼 |
| 1H 平滑輔助線 | 校正值色系，含 error bar | 預設隱藏，顯示均值 ± std |
| 諧和預報（QC=h）| 綠色點線 | `WL_{p_stid}_pred_h` |
| 天文潮（QC=a） | 淺綠色點線 | 預設隱藏 |
| 儀器差值 | 橘/洋紅/青點狀 | `Diff_{A}_{B}` 欄位，右 y 軸 |
| 颱風陸上警報色帶 | 紅色半透明 vrect | 由 `land_range` 控制 |

#### `go.Scattergl` vs `go.Scatter` 說明

原版使用 `Scattergl` 以提升大資料集 WebGL 效能。Dash 的 `selectedData`（Box Select）在部分平台對 `Scattergl` 回傳框選點資料不完整，但目前程式碼已改回 `Scattergl`（帶有 `# ← 從 Scatter 改回 Scattergl` 的行內注解）。若 QC 框選在特定環境失效，可嘗試改為 `go.Scatter` 並驗證。

#### 舊系統降級路徑

若 `fetch_tide_instruments()` 找不到 `stid_obs`，`fetch_bundle()` 回傳空 `tide_meta`。此時 `build_water_figure` 以舊版相容模式繪製（只有 `Obs` 和 `Pre` 兩欄），避免 KeyError 崩潰。

---

## 3. Tkinter 與 Dash 的資料流

### 完整流程圖

```
使用者操作
    │
    ▼
MainApp.go(mode="water")
    │
    ├─ [1] 讀取 UI 輸入：stids, start_date, end_date
    ├─ [2] 防呆檢查：日期順序、時間跨度（≤365天）、測站數（≤45）
    ├─ [3] 呼叫 fetch_bundle(stid, start, end) 取得 bundle dict
    │       └─ bundle = {
    │               stid, stname,
    │               df,          ← 合併後時序資料（含 WL_*/WL_*_raw 欄位）
    │               tide_meta,   ← {STID: {type, type_desc, stnac, is_primary}}
    │               src_ids,     ← {p, w, wv, wt: 資料來源 STID}
    │               src_names,   ← STID → 中文名稱
    │               mr_full,     ← 全年平均潮差
    │               mr_month,    ← 當月平均潮差
    │          }
    ├─ [4] 智慧過濾「幽靈颱風警報區」（time range 不重疊則 land_range=None）
    ├─ [5] dash_bridge.set_bundle(key, bundles, land_range=current_lr)
    │       ─── 寫入 _bundle_cache[key]，更新 _latest_key ───
    └─ [6] webbrowser.open("http://127.0.0.1:{DASH_PORT}")

  ↓（500ms 後）

Dash callback: poll_bundle（每 500ms）
    ├─ dash_bridge.get_latest_key()  →  取得最新 key
    ├─ 若 key 與 bundle-key-store 中的值不同
    └─ 更新 bundle-key-store

  ↓（bundle-key-store 變化觸發）

Dash callback: render_water_figure
    ├─ dash_bridge.get_bundle(key)   →  取回 bundles
    ├─ dash_bridge.get_land_range()  →  取回 land_range
    └─ build_water_figure(bundles, land_range)  →  go.Figure
       → 更新 main-graph
```

### 執行緒安全保證

- `set_bundle` / `get_bundle` / `get_latest_key` / `get_land_range` 均以 `threading.Lock` 包覆
- Dash callback 執行緒只讀取快取，不寫入；Tkinter 主執行緒只寫入，不讀取
- `key` 格式為 `{stid}_{timestamp}`，確保同一測站的多次查詢不會被舊快取覆蓋誤用

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

**反推邏輯：**

```
展開後的 Time → DATATIME（截到整點）+ MIN{N} 欄位
Time.minute // 6 = N
DATATIME = Time.replace(minute=0, second=0)
```

**產生的 SQL（每個 DATATIME 一條）：**

```sql
UPDATE tide6
SET    MIN3 = MIN3 + 0.5
WHERE  STID     = '{stid}'
  AND  DATATIME = '2024-07-15 14:00:00';
```

- 多個被框選的 Time 若屬同一 DATATIME，則合併為同一條 UPDATE（SET 多欄）
- 標頭注解說明共有幾筆 DATATIME、幾個欄位、使用何種運算

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

---
## 6. 視覺規範與樣式定義 (Visual Standards)

  本系統採用 Dark Mode 視覺風格，針對長時間監控需求設計，並針對 Windows 環境下的中文字體顯示進行優化。

  a. 核心配色表 (Color Palette)
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

  b. 字體系統 (Typography)
  為確保在 Windows 等寬環境下 SQL 指令與測站清單能精確對齊，優先選用「標楷體」。
   * UI 字體：標楷體, Noto Sans TC, Segoe UI, Arial, sans-serif
   * 等寬字體：標楷體, Courier New, Consolas, monospace (應用於 SQL 輸出、測站代碼對齊)
   * 圖表字體：標楷體, PingFang TC, Noto Sans CJK TC, Arial, sans-serif

  c. 圖表組件規範 (Plotly Styles)
   * 範本 (Template)：統一套用 plotly_dark。
   * 交互模式：hovermode='x unified' (十字準星) 且 uirevision=True (鎖定縮放視圖)。
   * CSS 覆蓋：透過 assets/custom.css 強制修正 Dash Dropdown 預設亮色樣式，確保選單背景為 #111820 並具備懸停高亮效果。

  ---

## 7. 系統微調與更新紀錄 (2026-05)

  本章節紀錄 2026 年 5 月期間針對系統穩定性、視覺體驗及操作效率所做的微調優化。

  1. UI/UX 增強
   * Y 軸範圍手動控制：於 Dash 右側面板新增 §0 區塊，支援手動輸入水位上限與下限。點擊「套用」後可同步調整所有水位子圖的垂直範圍，方便排除極端雜訊。
   * SQL 複製優化：在 SQL 輸出區右上方整合 dcc.Clipboard 按鈕，點擊即可一鍵複製生成的 UPDATE 指令，不需手動框選文字。

  2. 測站清單管理 (Tkinter)
   * 動態活站篩選 (Dynamic Filter)：新增「只列有資料站」按鈕。系統會根據當前選擇的時間範圍，即時查詢 tide6 資料表，僅保留有觀測紀錄的測站，節省無效查詢時間。
   * 單位快速選取：新增「選氣象署」與「非氣象署」按鈕。依據 sponsor_map 對應之業務單位執行批次勾選動作，方便處理不同權責來源的資料。

  3. 數據與繪圖邏輯升級
   * 多儀器自動識別：支援同地點「音波、壓力、雷達」多台水位儀器自動併圖顯示，並自動計算主/備儀器間的差值 (Diff) 顯示於右側 Y 軸。
   * 新增趨勢線 (EWMA)：引入指數加權移動平均線 (alpha=0.05)，相較於傳統移動平均線更能即時反應變動且減少邊界缺點，預設於圖例中隱藏。
   * 天文潮支援 (QC='a')：資料查詢路徑新增對 tide6ha 中 QC='a' (天文潮重建) 欄位的支援，提供多維度的預報比對。
   * Error Bar 平滑輔助線：在「水位細節」模式中提供 1H 平均值線，並附帶標準差誤差棒，輔助判斷資料離散程度。

---

## 8. 已知限制與待辦事項

### 功能面

| 優先度 | 項目 | 說明 |
|--------|------|------|
| 🔴 高 | `stid-store` 未與 bundle 同步 | SQL 產生時 STID 可能仍為 Demo 值（1176）。應在 `render_water_figure` 中同步更新 `stid-store` |
| 🔴 高 | Mode 2 只支援 tide6 | `build_mode2_sql_by_time` 硬寫 `UPDATE tide6`，不支援 wind / stemp6 等表 |
| 🟡 中 | `draw_diagnostic` QC 框選失效 | 全參數模式（mode="full"）仍以舊 HTML 路徑輸出，QC 框選功能不可用 |
| 🟡 中 | 多測站 bundle 的 STID 切換 | 同時查詢多測站時，QC 面板的 STID 未提供切換 UI，只使用第一筆 |
| 🟡 中 | 溫度顏色與紅叉衝突 | 氣溫（深紅）和海溫（淺粉）色系與 QC 紅叉視覺上相近，建議遷移至橘棕色系（`#e6740a` / `#ffbb78`）|
| 🟢 低 | `uirevision` 應為動態版本號 | 目前固定為 `True`，多次 push 新資料後縮放狀態可能不正確重置 |
| 🟢 低 | `DEBUG print` 未清除 | `query_multi_tide_data` 內有多行 `print("[DEBUG]...")` 尚未移除 |
| 🟢 低 | `bundle-poll` 500ms 效率 | 若 Dash 與 Tkinter 在同機執行，可縮短至 250ms；未來可改以 websocket 推送取代輪詢 |

### 架構面

- `dash_app.py` 中保留了 `OceanDataEngine` Stub 類別，整合後可移除（或改 import 真實版本）
- `plotly_qc_select.py`（舊版）應在確認功能完整遷移後從專案中刪除
- `draw_diagnostic()` 的 HTML 輸出路徑若要支援 QC 框選，需整合至 Dash 或維持舊版 HTTP callback server 並行運作

---

## 9. 打包注意事項（PyInstaller）

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

*本文件依 ocean_plot_dash.py、dash_bridge.py、dash_app.py、build_water_figure.py 四個檔案原始碼撰寫，如有修改請同步更新。*
