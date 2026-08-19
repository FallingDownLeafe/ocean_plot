# 🌊 海洋動力診斷儀表板 — SYSTEM.md

> 文件版本：2026-08-19
> 架構：Tkinter（資料層 + 登入/查詢 UI）＋ Dash（唯一的圖表渲染介面）
>
> 本文件取代舊有的 `SYSTEM.md`（HTML 架構時期）與 `SYSTEM_DASH.md`（Dash 遷移後）
> 兩份文件，合併為單一現行參考。專案 Files 中若仍留有 `SYSTEM_DASH.md`，可以刪除，
> 全部內容已併入本檔。

---

## 目錄

1. [系統架構概述](#1-系統架構概述)
2. [專案目錄與環境設定](#2-專案目錄與環境設定)
3. [各模組職責](#3-各模組職責)
4. [Tkinter 與 Dash 的資料流](#4-tkinter-與-dash-的資料流)
5. [QC 框選產生 SQL 的機制](#5-qc-框選產生-sql-的機制)
6. [資料庫欄位與 QC 查詢慣例](#6-資料庫欄位與-qc-查詢慣例)
7. [折線色系定義](#7-折線色系定義)
8. [視覺規範與樣式定義](#8-視覺規範與樣式定義)
9. [資料管線詳細設計（fetch_bundle 完整流程）](#9-資料管線詳細設計fetch_bundle-完整流程)
10. [VdC 散佈圖模組](#10-vdc-散佈圖模組)
11. [暴潮偏差報表圖模組](#11-暴潮偏差報表圖模組)
12. [海洋參數診斷圖模組（唯讀，2026-08-19 新增）](#12-海洋參數診斷圖模組唯讀2026-08-19-新增)
13. [已知限制與待辦事項](#13-已知限制與待辦事項)
14. [打包注意事項（PyInstaller）](#14-打包注意事項pyinstaller)
15. [資料表說明（供 SQL 參考）](#15-資料表說明供-sql-參考)

---

## 1. 系統架構概述

本系統是一套**潮位監測資料視覺化與品管輔助工具**，串接 MySQL 資料庫，支援多測站、多儀器水位的觀測與預報比對，並提供互動式 QC 框選功能，讓操作員可在圖表上直接圈選異常資料段、自動產生對應的 SQL UPDATE 語句。

繪圖與互動全部走 Dash（Flask）。Tkinter 只負責登入、查詢條件 UI、資料查詢，查完把結果丟進共用記憶體快取，交給 Dash 渲染。舊版「每次查詢寫一個 temp HTML、開瀏覽器」的路徑已完全淘汰。

### 架構分層

```
┌──────────────────────────────────────────────────────────────┐
│  Tkinter GUI（主執行緒）                                        │
│  ocean_plot_dash.py                                           │
│  ├─ LoginWindow  → 建立 OceanDataEngine（DB 連線）             │
│  └─ MainApp      → 查詢控制 UI，選站、選期、選模式              │
│       ├─ go(mode="water") → dash_bridge.set_bundle()          │
│       │                     webbrowser.open(Dash URL)          │
│       │                     （Dash 端「水位」「VdC」「暴潮偏差」│
│       │                      「海洋參數（唯讀）」四個頁籤共用   │
│       │                      同一份查詢結果）                  │
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
│  ├─ render_figure callback（依右側頁籤分派繪圖函式）           │
│  │     ├─ tab-water      → build_water_figure()               │
│  │     ├─ tab-vdc        → build_vdc_figure()                 │
│  │     ├─ tab-surge      → build_surge_interactive_figure()   │
│  │     └─ tab-diagnostic → build_diagnostic_figure()（唯讀）  │
│  ├─ on_selection callback（Box Select → SQL 產生，僅 tab-water │
│  │     生效，其餘頁籤 Patch 9 guard 直接略過）                 │
│  ├─ export_water_report / export_surge_report / export_vdc_report │
│  └─ apply_yaxis_range / apply_vdc_x_range / capture_zoom       │
└──────────────────────────────────────────────────────────────┘
```

`mode="full"`（舊版四子圖 HTML 大圖）在 Tkinter 端仍保留為備用選項（`draw_diagnostic()` 函式沒有刪除），但主要使用路徑已經是 Dash 的「海洋參數（唯讀）」頁籤（`build_diagnostic_figure()`）。是否要在 Tkinter UI 上直接拿掉 `mode="full"` 按鈕、完全收斂到 Dash，屬於後續可考慮的清理項目，見 §13。

`plotly_qc_select.py`（舊版 HTTP callback server + `_selection_queue` + `SqlDialog` Tkinter Toplevel 的整套機制）已被 Dash 的 `selectedData` callback 完全取代，若專案目錄裡還留著這個檔案，可以刪除。

---

## 2. 專案目錄與環境設定

```
專案目錄/
├── ocean_plot_dash.py         # 主程式（Tkinter GUI、OceanDataEngine 資料層、draw_diagnostic 備用路徑）
├── dash_app.py                # Dash 儀表板（Layout + Callbacks）
├── dash_bridge.py             # Tkinter ↔ Dash 跨執行緒資料橋接
├── build_water_figure.py      # 水位時序圖（互動深色版 + 白底匯出版）
├── build_vdc_figure.py        # Van de Casteele 散佈圖（互動版 + 匯出報表版）
├── build_surge_report_figure.py  # 暴潮偏差圖（白底單站版 + 互動多站版）
├── build_diagnostic_figure.py # 海洋參數四子圖診斷圖（唯讀，2026-08-19 新增）
├── 對應站表格.csv              # 安外用測站對應表（安內改從 DB 讀取）
└── .env                        # 環境變數（DB_IP、DB_USER、MAPPING_SRC、TYPHOON_DB）
```

### `.env` 可設定的變數

| 變數 | 安外預設值 | 安內設定值 | 說明 |
|------|-----------|-----------|------|
| `DB_IP` | `61.56.13.160` | 實際 IP | 資料庫主機 |
| `DB_USER` | `dps` | 實際帳號 | 資料庫帳號 |
| `MAPPING_SRC` | `csv` | `db` | 測站對應來源 |
| `TYPHOON_DB` | `med_data` | `mrbank` | 颱風資料庫名稱 |

不使用 python-dotenv（未列入 requirements），一律用手動解析器讀取 `.env`（`ocean_plot_dash.py` 頂端已實作，見該檔案）。

---

## 3. 各模組職責

### 3.1 `ocean_plot_dash.py`

**角色：主程式（Tkinter GUI + 資料層）**

#### `OceanDataEngine`（資料引擎類別）

所有 DB 操作集中在此類別，不含任何 UI 邏輯。

| 方法 | 說明 |
|------|------|
| `__init__()` | 建立 MySQL 連線，讀取測站對應表（CSV 或 DB 的 `stitemqc`，由 `.env` 的 `MAPPING_SRC` 控制） |
| `load_mapping()` | 載入 STID ↔ QCID 對應，並從 `st` 表查詢測站中文名稱、縣市、業務單位 |
| `fetch_years()` / `fetch_typhoons()` | 查詢颱風資料庫（`med_data` 安外 / `mrbank` 安內，由 `TYPHOON_DB` env 控制） |
| `expand_data(df, val_name, freq)` | 將資料庫的寬表格（`MIN0`～`MIN9` 或 `HR0`～`HR23` 欄位）展開為時序長格式。`id_cols` 自動攜帶所有非資料欄，包含 `QC`，是 QC 拆分前置作業的核心工具 |
| `fetch_tide_instruments(stid)` | 查詢測站下所有水位儀器（音波 type=2、壓力 type=3、雷達 type=4），以 `stid_obs` 為連結鍵 |
| `query_multi_tide_data()` | 為多儀器批量查詢 `tide6` 觀測值與 `tide6ha` 諧和/天文潮預報。在此完成 **QC 拆分**（`QC=Q` vs `QC≠Q`），並 outer merge 為並列欄位（`WL_{stid}` + `WL_{stid}_raw`） |
| `fetch_tidal_extrema(stid, s_s, e_s)` | 查詢 `tideh`（天文潮高低潮預報）與 `tidehl`（觀測對應高低潮），供水位圖標記高低潮三角形 |
| `fetch_bundle(stid, start, end)` | 主要查詢入口。整合水位、氣壓、風、氣溫、海溫、波浪、海流，依 `candidates`（QCID + 自身）順序優先取用最佳資料源，最終回傳含完整時序資料的 bundle dict。完整流程見 §9 |

kind=7（浮球）目前不在 `fetch_bundle()` 的環境資料迴圈處理範圍內（迴圈只認 kind ∈ {'1','2','3','6','8'}）。浮球/浮標資料整合是獨立進行中的工作，見 §13 待辦事項。

#### `MainApp`（Tkinter 主介面）

- 登入視窗（`LoginWin`）：建立 `OceanDataEngine` 並取得 DB 連線
- 測站選單：從 CSV 或 DB 讀取的 `mapping_df` 動態填充
  - **動態活站篩選**：「只列有資料站」按鈕，依目前所選時間範圍即時查詢 `tide6` 資料表，僅保留有觀測紀錄的測站
  - **單位快速選取**：「選氣象署」與「非氣象署」按鈕，依 `sponsor_map` 對應之業務單位執行批次勾選
- 日期選擇：tkcalendar `DateEntry`，含防呆（日期順序、時間範圍上限）
- `go(mode)` 方法：
  - `mode="water"`：呼叫 `dash_bridge.set_bundle()`（含 `typhoon_info` 與 `thresholds_map`），以 `webbrowser.open()` 導向 Dash URL。Dash 端的水位、VdC、暴潮偏差、海洋參數（唯讀）四個頁籤都讀這同一份快取
  - `mode="full"`：呼叫 `draw_diagnostic()`，產生四子圖的 Plotly HTML（舊路徑，備用；主要使用方式已是 Dash 的「海洋參數（唯讀）」頁籤）
  - `mode="storm"`：呼叫 `build_surge_report_figure()`，儲存至本機 `surge_reports/` 資料夾並以 `os.startfile()` 開啟；僅支援單站

> **`mode="water"` 的附加資料傳遞**：`go()` 在呼叫 `set_bundle()` 前，會同步查詢颱風 info dict（從 `self.ty_cb` → `self.ty_df` 反查）和各站門檻值（`get_tsuwawa_thresholds(self.e.conn, stid)` 逐站查詢），一併存入 dash_bridge。

#### `draw_diagnostic()`（舊版四子圖 HTML，備用）

使用 `make_subplots` 建立 n×2 子圖（水位、海氣象、暴潮偏差、波浪），走舊的 HTML 輸出路徑渲染。每 3 站分一頁、各自開一個瀏覽器分頁。**不支援 QC 框選**（`remove_buttons` 清單裡沒有 `select2d`，理論上可以框選，但沒有接到任何 SQL 產生機制）。與 `build_diagnostic_figure.py` 屬於同一套視覺邏輯的兩個實作（見 §12），trace 建構規則完全一致，差別只在輸出方式。

---

### 3.2 `dash_bridge.py`

**角色：跨執行緒橋接層**

負責主進程（Tkinter）與子執行緒（Dash/Flask）間的資料交換。

- **滾動快取機制**：`MAX_CACHE_SIZE = 10`。超過上限時自動刪除最舊的 bundle。
- **執行緒安全**：`threading.Lock` 確保多分頁讀取與 Tkinter 寫入不會 race。
- **關鍵資料**：
    - `_bundle_cache`：以 `stid_timestamp` 為 Key 儲存多筆測站資料。
    - `_latest_key`：供 Dash 端 `dcc.Interval` 輪詢偵測更新。
    - `_land_range`：颱風陸上警報時段。
    - `_typhoon_label` / `_typhoon_info`：颱風標籤字串 / 完整事件 dict。
    - `_thresholds_map`：各測站的大潮注意值 / 警戒值。

**對應的 getter 函式：**

| 函式 | 回傳 |
|------|------|
| `get_bundle(key)` | bundle dict 或 None |
| `get_latest_key()` | 最新 key 字串或 None |
| `get_land_range()` | `(beg, end)` tuple 或 None |
| `get_typhoon_label()` | 字串或 None |
| `get_typhoon_info()` | dict 或 None |
| `get_thresholds_map()` | `{stid: dict\|None}` 的 copy |
| `get_cache_count()` | int |

本模組僅負責資料快取與線程調度，不涉及任何圖表渲染或 UI 佈局邏輯。

---

### 3.3 `dash_app.py`

**角色：Dash 儀表板（Flask 工作執行緒）**

以 `daemon=True` 的執行緒在 `MainApp.__init__()` 啟動，監聽 `127.0.0.1:{DASH_PORT}`（動態找可用 port，從 8050 開始往後搜尋）。

#### Layout 結構

```
app.layout
├─ dcc.Store(id="figure-store")          # 備用，未來可擴充
├─ dcc.Store(id="stid-store")            # 當前 STID（SQL 產生用）
├─ dcc.Store(id="bundle-key-store")      # 最新 bundle key
├─ dcc.Store(id="zoom-range-store")      # 水位圖目前 zoom x 範圍
├─ dcc.Store(id="trace-meta-store")      # curveNumber → STID 對應（僅 tab-water 產生）
├─ dcc.Interval(id="bundle-poll", 500ms) # 輪詢觸發器
├─ Header
├─ 主內容
│   ├─ 左：dcc.Graph(id="main-graph")   # 圖表區（flex: 3，四個頁籤共用同一個 Graph）
│   └─ 右：QC 控制面板 / 頁籤內容（寬度 360px，2026-08-19 由 320px 調寬）
│       └─ dcc.Tabs(id="right-panel-tabs")
│             ├─ tab-water：水位時序與 QC（Y軸範圍、SQL Mode 1/2/3、SQL 輸出、白底匯出）
│             ├─ tab-vdc：VdC 散佈圖（副儀器類型、X 軸範圍、統計摘要、PNG 匯出）
│             ├─ tab-surge：暴潮偏差（預報來源、圖例模式、PNG 匯出）
│             └─ tab-diagnostic：海洋參數（唯讀）（說明文字 + 統計摘要，2026-08-19 新增）
└─ 底部狀態列（顯示伺服器記憶體快取佔用狀態）
```

`main-graph` 這個 `dcc.Graph` 元件是四個頁籤共用的單一實體，`config`（含 `modeBarButtonsToAdd: ["select2d", "lasso2d"]`）也是全頁籤共用、寫死在 layout 裡，不會依頁籤動態調整。這代表在 `tab-vdc` / `tab-surge` / `tab-diagnostic` 上，Box Select 工具按鈕仍然看得到，只是選取結果不會產生任何有意義的 SQL（見下方 `on_selection` 說明）。

#### Callbacks

| Callback | 觸發 | 作用 |
|----------|------|------|
| `toggle_mode_controls` | `qc-mode` RadioItems 變更 | 顯示/隱藏 Mode 1 / 2 / 3 對應參數區塊 |
| `poll_bundle` | `bundle-poll` Interval（每 500ms）| 從 `dash_bridge.get_latest_key()` 偵測新資料，有變化則更新 `bundle-key-store` |
| `render_figure` | `bundle-key-store`、`url.search`、`right-panel-tabs.value` 等更新 | 依 `active_tab` 分派對應的 `build_*_figure()`，更新 `main-graph`、`stid-store`、`vdc-stats-output`、`diagnostic-stats-output`、`trace-meta-store` |
| `capture_zoom` | `main-graph.relayoutData` | zoom / autorange 事件（僅 tab-water）→ 更新 `zoom-range-store` |
| `on_selection` | `main-graph.selectedData` 變更 | 依 `active_tab` 判斷：非 `tab-water` 時直接回傳 `no_update`（2026-08-19 新增的 guard，見下）；`tab-water` 時依 Mode 1/2/3 呼叫對應 SQL builder |
| `apply_yaxis_range` | Y軸「套用」/「清除」按鈕（tab-water） | Patch 所有水位子圖 yaxis 為手動輸入範圍，或還原自動縮放 |
| `apply_vdc_x_range` | VdC X軸「套用」/「清除」按鈕 | 統一所有 VdC 子圖的 X 軸對稱範圍 |
| `export_water_report` / `export_surge_report` / `export_vdc_report` | 各頁籤匯出按鈕 | 呼叫對應白底/報表版繪圖函式，下載 PNG（暴潮偏差多站時為 ZIP） |

`render_figure` 目前回傳 7 個 Output（`main-graph.figure`、`stid-store.data`、`vdc-stats-output.children`、`page-lock-status.children`、`page-lock-status.style`、`trace-meta-store.data`、`diagnostic-stats-output.children`），每個 `active_tab` 分支都必須回傳完整 7-tuple，未使用的欄位填 `no_update`。新增/修改這個 callback 時務必檢查每個 `return` 的元素數量，否則 Dash 在瀏覽器端會直接報錯、整個頁面停止更新。

**`on_selection` 的 tab guard（2026-08-19 新增）**：由於 `main-graph` 在所有頁籤間共用，若使用者在 `tab-vdc`／`tab-surge`／`tab-diagnostic` 上誤用 Box Select 工具，`selectedData` 仍會變化並觸發這個 callback。加入 `State("right-panel-tabs", "value")` 後，非 `tab-water` 一律直接 `return no_update, no_update, no_update`，避免用不對應的 `trace_meta` 產生誤導性的 SQL 寫進（雖然畫面上看不到的）`sql-output`。

#### SQL 工具函式（獨立於 Tkinter）

| 函式 | 說明 |
|------|------|
| `_clean_ts(ts)` | 清理 Plotly 時間戳（`T`→空格、去毫秒），轉為 MySQL 可接受格式 |
| `build_mode1_sql(sel, stid, new_qc)` | Mode 1：產生按時間範圍與 y 值範圍篩選的 `UPDATE tide6 SET QC=...` |
| `build_mode2_sql_by_time(sel, stid, op, operand)` | Mode 2：從被框選點的展開 Time 反推 `DATATIME` 與 `MIN{N}` 欄位，產生 `MIN{N} = MIN{N} OP operand` |
| `build_mode3_sql(t_start, t_end, stid, bundle_key)` | Mode 3：對所有已載入 bundles 計算暴潮統計值，產生 `INSERT INTO mrbank.surge` 語句 |
| `_adapt_selected_data(selected_data)` | 將 Dash `selectedData` 格式轉換為 SQL builder 所需的 `sel` dict，支援多子圖的 `x2`/`x3`… range key |

這三個 builder **目前只支援 `tide6`**，是已知限制，見 §13。

---

### 3.4 `build_water_figure.py`

**角色：純函式水位圖繪製器（無副作用）**

```python
build_water_figure(bundles, land_range=None, typhoon_label=None) -> (go.Figure, trace_meta)
```

每個 bundle 佔一列，`shared_xaxes=True`，雙 y 軸（左水位、右差值）。繪製內容：各儀器水位（校正值/低頻趨勢/EWMA/原始異常值/1H平滑）、預報水位（諧和/天文潮）、儀器差值、颱風陸警色帶、高低潮極值標記。回傳的 `trace_meta`（`[{stid, field_type}, ...]`）供 `on_selection` 反查框選點屬於哪個測站。

`舊系統降級路徑`（`tide_meta` 為空）：只畫 `Obs`（藍線）與 `Pre`（綠點線）兩欄，避免 KeyError。

另有白底匯出版 `build_water_report_figure(bundles, land_range=None, zoom_range=None)`，樣式改為 `plotly_white`，供 `export_water_report` 使用。

---

### 3.5 `build_diagnostic_figure.py`

**2026-08-19 新增。** 詳見 §12，此處僅列表定位：唯讀四子圖診斷圖（水位／海氣象／暴潮與氣壓／波浪特性），對應 Dash 的 `tab-diagnostic`。

---

### 3.6 `build_vdc_figure.py`

**角色：仿 Van de Casteele 散佈圖繪圖模組**

```python
build_vdc_figure(bundles, diff_type="auto", zoom_range=None) -> (go.Figure, stats_summary)
```

縱軸為主儀器（音波式）水位值，橫軸為水位差值（音波式 − 雷達式 或 音波式 − 壓力式），顏色依觀測時間漸變。`diff_type`：`"auto"` 優先雷達式無則壓力式；也可強制指定。X 軸範圍預設各站獨立（mean ± 3σ），可透過 UI 手動覆蓋為統一對稱範圍。含 `scipy.stats.linregress` 回歸線，斜率與 R² 存入 `stats_summary` 供右側面板顯示。

`stats_summary` 結構：
```python
{
  stid: {
    "status": "success" | "no_data" | "no_fields" | "empty_data",
    "count": int, "mean": float, "std": float, "min": float, "max": float,
    "time_start": str, "time_end": str,
    "slope": float | None, "r2": float | None,
  }
}
```

另有匯出報表版 `build_vdc_report_figure(bundles, diff_type="auto", zoom_range=None)`：左欄 VdC 散佈圖，右欄差值分佈直方圖 + 常態曲線疊加，供 `export_vdc_report` 使用（需要 `kaleido`，測試環境 1.2.0 版本正常）。

---

### 3.7 `build_surge_report_figure.py`

**角色：颱風暴潮偏差圖繪圖模組（白底單站報表版 + 深色互動多站版）**

#### `get_tsuwawa_thresholds(conn, stid)`

查詢 `tsuwawa.warn` 表，取得大潮注意值（`STIDE`）與暴潮警戒值（`WARNVAL`），單位換算為 mm。查無資料回傳 `None`。

#### `build_surge_report_figure(bundle, typhoon_info, thresholds, pred_source="auto")`

單站白底報表圖（供 `mode="storm"` 本機儲存，以及 Dash 端單站匯出使用）。左軸水位（觀測 + 預報），右軸暴潮偏差；`pred_source` 控制預報來源優先序（`auto` 優先天文潮分析 `pred_a`，無則調和預報 `pred_h`）；含潮位注意值/警戒值水平線、海警/陸警色帶標註。

#### `build_surge_interactive_figure(bundles, typhoon_info, thresholds_map, pred_source="auto", legend_mode="shared")`

多站深色互動版，對應 `tab-surge`。`legend_mode="shared"`（預設）全圖共用一組圖例置底；`"individual"` 每個子圖各自一組圖例內嵌右上角，避免蓋到左上角子圖標題。

---

## 4. Tkinter 與 Dash 的資料流

**Tkinter → Dash（單向推送）：**
- `MainApp` 查詢到資料後呼叫 `dash_bridge.set_bundle(key, bundle, ...)`。
- `dcc.Interval` 每 500ms 輪詢 `dash_bridge.get_latest_key()`，`key` 變化時更新 `bundle-key-store`，觸發 `render_figure`。

**獨立分頁（Session Isolation）：**
- 透過 URL 參數：`http://127.0.0.1:{DASH_PORT}/?key={stid}_{timestamp}`。
- 每個瀏覽器分頁「鎖定」在特定查詢結果上，不會被後續查詢強制跳轉，可同時開多個分頁對照不同測站/時段。
- 若 URL 無參數，改用輪詢顯示最新一筆。

**Dash → Tkinter：** 無直接回傳。SQL 由使用者在 Dash 面板手動複製，貼到資料庫工具執行。

**埠號動態分配**：`DASH_PORT = _find_free_port(start=8050)`，從 8050 往後找第一個可用埠。

---

## 5. QC 框選產生 SQL 的機制

```
使用者在 tab-water 的 dcc.Graph 上以 Box Select（□）框選區域
    │
    ▼
on_selection callback（active_tab != "tab-water" 時直接短路，見 §3.3）
    ├─ 接收 selectedData = {points: [...], range: {"x"/"x2"/..., "y"/"y2"/...}}
    ├─ _adapt_selected_data() 轉換格式
    └─ 依 qc-mode 選擇 SQL builder
```

### Mode 1：更新 QC 旗標

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

`t1`/`t2` 來自 Box Select 的 x 範圍；`lo`/`hi` 來自 y 範圍，確保只更新落在框選區間的資料點。適用場景：整批標記某時間段內落在特定水位範圍的資料為異常（或恢復正常）。

### Mode 2：MIN 欄位四則運算

```
Time = DATATIME + N × 6min  →  N = Time.minute // 6  →  MIN{N} 欄位
DATATIME = Time.replace(minute=0, second=0)
```

```sql
UPDATE tide6
SET    MIN3 = MIN3 + 0.5,
       MIN4 = MIN4 + 0.5
WHERE  STID     = '{stid}'
  AND  DATATIME IN ('2024-07-15 14:00:00', '2024-07-15 15:00:00', ...);
```

多個被框選的 Time 若屬同一 DATATIME，合併為同一條 UPDATE；具有相同 SET 內容的多個 DATATIME 合併為 `IN (...)`，減少 SQL 筆數。**⚠️ 只支援 `tide6`**，見 §13。

### Mode 3：生成暴潮紀錄 INSERT SQL

框選目標時間範圍（或不框選使用全時段），對每個已載入 bundle 計算：`MAXRISE`/`MAXRISET`（觀測水位最大值與時間）、`MAXDEV`/`MAXDEVT`（暴潮偏差最大值與時間）、`MAXNEG`/`MAXNEGT`（暴潮偏差最小值與時間），產生：

```sql
INSERT INTO mrbank.surge
(ID, STID, MAXRISE, MAXRISET, MAXDEV, MAXDEVT, MAXNEG, MAXNEGT,
 QC, CNAME, KIND, PATH, INTENSITY, SPRING, PRES, VC, R7)
VALUES (
  '2504L', '1176', 2350, '2025-07-24 03:00:00',
  890, '2025-07-24 03:12:00', -120, '2025-07-23 22:00:00',
  'a', '丹娜絲', NULL, NULL, NULL, NULL, NULL, NULL, NULL
);
```

`QC='a'`（自動計算，待人工核閱）；`KIND`/`PATH`/`INTENSITY`/`SPRING`/`PRES`/`VC`/`R7` 固定 NULL，需人工填寫。多站時各站 INSERT 以兩個換行分隔。

### `stid-store` 的更新時機

`render_figure` 取得 bundle 後會同步更新 `stid-store`（`primary_stid = bundles[0].get("stid", ...)`），SQL builder 中的 `stid` 優先使用 `trace_meta` 反查框選點所屬的測站，查不到才 fallback 到 `stid-store` 目前的值。

---

## 6. 資料庫欄位與 QC 查詢慣例

| 資料表 | QC 欄位 | 合格值 | 說明 |
|--------|---------|--------|------|
| `tide6` | `qc`（小寫）| `'Q'`（大寫） | 水位 6min 觀測，mysql.connector 回傳小寫，需 `.upper()` 比對 |
| `tide6ha` | `QC`（大寫）| `'h'`（諧和預報）/ `'a'`（天文潮）（都小寫）| 水位預報 |
| `wind` | `qc`（小寫）| `'Q'`（大寫）| 風速風向，比對時需 `.upper()` |
| `stemp6` | `qc`（小寫） | `'Q'`（大寫）| 6min 溫度，潮位站用，有 QC 拆分邏輯 |
| `stemp1` | 非必要 | — | 浮標 1h 溫度，上游已供應品管Q資料，`fetch_bundle()` 目前不進行 QC 拆分 |
| `meteo` | 無 | — | 氣象站，直接使用，無 QC 欄位 |
| `pres6` | `qc`（小寫）| `'Q'`（大寫） | 氣壓（潮位站）|
| `pres1` | 無 | — | 氣壓（浮標），無 QC 欄位 |
| `curr` | 非必要 | — | 海流，上游已供應品管Q資料 |
| `wave` | 非必要 | — | 波浪，上游已供應品管Q資料 |

> **⚠️ 待釐清**：獨立開發中的浮球/浮標品管工具畫面顯示 `stemp1` 有可執行的 `UPDATE stemp1 SET qc=... WHERE ... AND qc='Q'`，代表 `stemp1` 資料表本身可能確實存在 `qc` 欄位（只是主程式目前選擇不拆分顯示）。這點在正式合併浮球/浮標工具前建議先用實際的資料庫欄位定義（`DESC stemp1;` 或類似指令）覆核，並更新本表，避免文件與實際 schema 不一致。

### mysql.connector 小寫欄位名問題

`mysql.connector` 原生連線（非 SQLAlchemy）執行 `pd.read_sql()` 時，**回傳的欄位名稱會被自動轉為小寫**，直接以大寫欄位名存取會拋 `KeyError`。標準修正（每次 `read_sql` 後立即執行）：

```python
df_obs.columns = [c.upper() for c in df_obs.columns]
```

### 浮球/浮標觀測表 QC 通則

> 以下六表的 `qc` 均為複合 PK 成員，UPDATE 必須含 `AND qc = '{old_qc}'`。

| 資料表 | PK（含 qc）| 更新單位 | 備註 |
|--------|-----------|---------|------|
| `wave1`、`wave` | STID, TIME, qc | TIME | 無 Z |
| `wind`、`curr`  | STID, TIME, Z, qc | TIME | Z INT |
| `pres1`  | STID, DATATIME, qc | DATATIME（日 00:00）| HR0~HR23 寬格式；無 Z |
| `stemp1` | STID, DATATIME, Z, qc | DATATIME（日 00:00）| HR0~HR23 寬格式；Z INT，-3=氣溫/0=海溫計1/1=海溫計2 |

---

## 7. 折線色系定義

| 參數 | 顏色代碼 | 線型 | 預設顯示 |
|------|---------|------|---------|
| 水位 — 音波式（主） | `#1f77b4`（深藍） | solid | ✅ |
| 水位 — 壓力式 | `#0d47a1`（深靛藍） | dash | legendonly |
| 水位 — 雷達式 | `#64b5f6`（淺藍） | dash | legendonly |
| 水位低頻趨勢（25h-MA） | `rgba(180,180,180,0.55)`（半透明灰） | solid | ✅（隨主線）|
| EWMA（α=0.05） | `rgba(255,200,100,0.7)`（半透明金黃） | solid | legendonly |
| 諧和預報水位（QC=h） | `#2ca02c`（綠） | dot | ✅ |
| 天文潮預報（QC=a） | `#98df8a`（淺綠） | dash | legendonly |
| 儀器差值 第1組 | `#ff7f0e`（橘） | markers only | ✅ |
| 儀器差值 第2組 | `#e377c2`（洋紅） | markers only | ✅ |
| 儀器差值 第3組 | `#17becf`（青） | markers only | ✅ |
| 暴潮偏差（Resi） | `#faafe4`（粉紅） | solid | ✅ |
| 暴潮偏差正規化（全年MR） | `#f8bbd0`（淺粉紅） | dash | legendonly |
| 暴潮偏差正規化（當月MR） | `#f8bbd0`（淺粉紅） | dot | legendonly |
| 氣壓 | `#8b4513`（棕） | dot | ✅ |
| 風速 | `#9467bd`（中紫） | solid | ✅ |
| 風向箭頭 | `#800080`（紫） | marker/arrow | ✅（無圖例）|
| 流速 | `#c5b0d5`（淡紫） | dash | legendonly |
| 流向箭頭 | `#a03fea`（亮紫） | marker/arrow | legendonly |
| 氣溫 | `#ee7373`（珊瑚紅） | solid | ✅ |
| 海溫 | `#ff9896`（淡粉紅） | dash | legendonly |
| 示性波高 | `#1b5e20`（深綠） | solid | ✅ |
| 平均週期 | `#81c784`（淺綠） | dot | ✅ |
| 異常值紅叉（所有參數） | `red`（純紅） | marker/x | ✅ |

`build_water_figure.py`、`build_diagnostic_figure.py`、`ocean_plot_dash.py` 的 `draw_diagnostic()` 三處共用同一套色系，異動任何一處請同步檢查另外兩處。

---

## 8. 視覺規範與樣式定義

**a. 核心配色表**

```
┌──────────────────────┬────────────────────────┬────────────────────────────────┐
│ 項目                 │ HEX 色碼               │ 視覺用途                       │
├──────────────────────┼────────────────────────┼────────────────────────────────┤
│ Global Background    │ #111820                │ 系統底色、下拉選單、狀態列背景 │
│ Plot/Text Background │ #1E1E1E                │ 圖表繪圖區背景、SQL 輸出區背景 │
│ Panel Background     │ #1E2A3A                │ 右側控制面板背景               │
│ Header/Accent        │ #1A3A5C                │ 頂部標題列、區塊標題底色       │
│ Border/Highlight     │ rgba(200,214,229,0.25) │ 邊框、格線、半透明強調色       │
│ Primary Text         │ #CCD0D4                │ 一般標籤與選單文字色           │
└──────────────────────┴────────────────────────┴────────────────────────────────┘
```

**b. 字體系統**

- UI 字體：標楷體, Noto Sans TC, Segoe UI, Arial, sans-serif
- 等寬字體：標楷體, Courier New, Consolas, monospace（SQL 輸出、測站代碼對齊）
- 圖表字體（互動深色頁籤）：標楷體, PingFang TC, Noto Sans CJK TC, Arial, sans-serif
- 圖表字體（白底匯出報表）：Microsoft JhengHei, Noto Sans TC, Arial, sans-serif（不含標楷體——主管反映標楷體用在正式簡報不好看，因此僅白底匯出版拿掉）

**c. 版面配置**

- 主內容區為左右兩欄 flex layout：左欄圖表 `flex: 3`；右欄控制面板固定寬度 **360px**（2026-08-19 由 320px 調寬，因為新增第 4 個頁籤後標籤列變得擁擠）。若日後頁籤持續增加、標籤仍嫌擠，可以考慮改成兩排標籤、或把面板寬度改為 vw 相對單位 + minWidth，而不是無限加大固定 px。
- 右側面板目前為 4 個頁籤：水位時序與QC／VdC 散佈圖／暴潮偏差／海洋參數（唯讀）。

**d. 圖表組件規範**

- 範本：統一套用 `plotly_dark`（白底匯出版另外指定 `plotly_white`）
- 交互模式：`hovermode='x unified'` 且 `uirevision=True`（鎖定縮放視圖）
- CSS 覆蓋：`assets/custom.css` 強制修正 Dash Dropdown 預設亮色樣式
- Tkinter 主題：`_apply_dark_theme(root)` 套用 `clam` 基底主題，色彩沿用上表 `_C` 字典

---

## 9. 資料管線詳細設計（fetch_bundle 完整流程）

### 9-1 `expand_data()` — MIN0~MIN9 / HR0~HR23 展開邏輯

tide6 原始資料格式：每列一個 DATATIME（整點），MIN0~MIN9 代表該小時內每 6 分鐘的觀測值。

```
原始一列：STID='1176', DATATIME='2025-07-06 00:00:00', MIN0=950, MIN1=940, ...

展開後：
  Time = 2025-07-06 00:00:00 → WL = 950   （MIN0，偏移 0 分鐘）
  Time = 2025-07-06 00:06:00 → WL = 940   （MIN1，偏移 6 分鐘）
  ...
```

實作：`pandas.melt()` 將寬表轉長表，再用 `pd.to_timedelta(N * step)` 計算偏移。`freq='6min'` 時 prefix 為 `MIN`，`freq='1h'` 時 prefix 為 `HR`（氣壓/浮標資料用）。

**執行流程**：
1. 判斷 prefix（`MIN` 或 `HR`）
2. `val_cols`：欄位名以 prefix 開頭者
3. `id_cols`：所有非 val_cols 且非 `LASTUPDATETIME` 的欄位（含 DATATIME、STID、QC）
4. `melt()` → 新增 `idx` 欄位（"MIN0"、"MIN1"…）
5. 計算 `Time = to_datetime(DATATIME) + to_timedelta(N * step, 'm')`，`.dt.floor('min')` 對齊
6. 回傳欄位：有 QC 就帶 `[Time, val_name, QC]`，否則 `[Time, val_name]`
7. `dropna(subset=[val_name])` → `drop_duplicates(subset=['Time'])` → `sort_values('Time')`

**呼叫前提**：呼叫端應已依 QC 分組（例如 `df[df['QC']=='Q']`），避免同一 Time 存在多筆不同 QC 的資料。

### 9-2 `query_multi_tide_data()` — QC 分流設計

查詢 tide6 後**不篩選 QC**，在 Python 端拆為兩份分別展開：

```python
df_q   = df_obs[df_obs['QC'].str.upper() == 'Q']   # 校正值
df_bad = df_obs[df_obs['QC'].str.upper() != 'Q']   # 原始機測值（異常）
```

各自 `expand_data()` 後以 Time 為鍵 `outer merge`：

| 欄位 | 內容 |
|------|------|
| `WL_{stid}` | QC=Q 的校正水位值 |
| `QC_{stid}` | 對應的 QC 代碼 |
| `WL_{stid}_raw` | QC≠Q 的原始機測值 |
| `QC_{stid}_raw` | 原始值的 QC 代碼（hover 顯示） |

**回傳 dict：**

```python
{
    'wl_data':     {STID: expanded_df, ...},   # 觀測值（含 QC 分流並排）
    'pred_data':   {STID: expanded_df, ...},   # 諧和預報 QC='h'（僅主測站）
    'pred_data_a': {STID: expanded_df, ...},   # 天文潮預報 QC='a'（僅主測站）
    'tide_meta':   {STID: {type, type_desc, stnac, is_primary, stid_obs, stid_new}, ...}
}
```

| tide_meta 鍵 | 說明 |
|----|------|
| `type` | 儀器類型代碼（2=音波式, 3=壓力式, 4=雷達式） |
| `type_desc` | 文字描述（供圖例顯示） |
| `stnac` | 儀器站名 |
| `is_primary` | 1=主測站（stid_new == stid_obs），0=備用儀器 |
| `stid_obs` / `stid_new` | 供 `build_surge_report_figure.py` 標題顯示新舊站碼 |

### 9-3 `fetch_bundle()` — 完整資料查詢流程

**函式簽名**：`fetch_bundle(stid: str, start: date, end: date) → dict`

#### Step 1：建立候選站名單（candidates）

從 `mapping_df` 取出主站的 QCID 候選清單，以 QCID 優先、自身墊底：`candidates = q_ids + [stid]`（去重保留順序）。同時一次性查詢所有 candidates 的 `st.kind`，建立 `kind_map`。

| kind 值 | 站種 |
|---------|------|
| 1/2/3 | 氣象站 |
| 6 | 潮位站 |
| 7 | 浮球（`fetch_bundle()` 目前未處理） |
| 8 | 浮標 |

#### Step 2：多儀器水位查詢（新系統）/ 單儀器降級（舊系統）

**新系統**（有 `stid_obs`）：`query_multi_tide_data()` 批量查詢，outer merge 組成 `main`。

**舊系統降級**（`fetch_tide_instruments` 回傳空）：查單一測站 tide6（無 QC 篩選）與 tide6ha（QC='h'），`Resi = Obs - Pre`。

#### Step 3：計算衍生欄位（僅主測站）

| 欄位 | 計算方式 |
|------|---------|
| `Resi` | `WL_{primary}` − `WL_{primary}_pred_h` |
| `WL_{primary}_lf` | 25h 中心移動平均（window=250點，min_periods=125） |
| `WL_{primary}_ewma` | EWMA（alpha=0.05，ignore_na=True）|
| `Resi_Norm` | `Resi / mr_mm × 100`（%），需先查 `tidestat` 取 MR |
| `Diff_{primary}_{other}` | 主儀器與各備用儀器的差值（兩者皆有值才計算）|

MR 查詢優先順序：全年 MR（`MONTH=0`）→ 當月 MR；條件 `tidestat WHERE STID='{stid}' AND YEAR={year} AND SL='S' AND QC='Q'`。

#### Step 4：環境資料迴圈（candidates 依序查詢，先到先得）

**A. 氣壓 (P)**

| kind | 資料表 | 欄位 | 備註 |
|------|--------|------|------|
| 1/2/3（氣象站） | `meteo` | `p×0.1 as P` | `min=0` 篩選整點 |
| 8（浮標） | `pres1` | expand_data(HR0~HR23) × 0.1 | freq='1h' |
| 6（潮位站） | `pres6` | expand_data × 0.1，限 QC='Q' | freq='1h' |

**B. 風速/風向 (WS/WD)**

| kind | 資料表 | 欄位 | 備註 |
|------|--------|------|------|
| 1/2/3 | `meteo` | `ws×0.1 as WS, wd as WD` | `min=0` |
| 6（潮位站） | `wind` | `VM×0.1 as WS, DM as WD`，Z='6' | QC 分流 WS/WS_raw |
| 8（浮標） | `wind` | 同上，Z IN ('2','3') | Z 小者優先 |

**C. 氣溫 (AT)**

| kind | 資料表 | 欄位 | 備註 |
|------|--------|------|------|
| 1/2/3 | `meteo` | `t×0.1 as AT` | `min=0` |
| 6（潮位站） | `stemp6` | Z='-3'，× 0.1 | QC 分流 AT/AT_raw |
| 8（浮標） | `stemp1` | Z='-3'，× 0.1 | freq='1h' |

**D. 海溫 (WT)**

| kind | 資料表 | 欄位 | 備註 |
|------|--------|------|------|
| 8（浮標） | `stemp1` | Z='0'，× 0.1 | freq='1h' |
| 6（潮位站） | `stemp6` | Z='0'，× 0.1 | QC 分流 WT/WT_raw |

P、W、AT 三項皆有資料後提早 break；WT 有資料即停止查詢。

#### Step 5：海洋資料（浮標專用，kind=8 的第一個候選 `b_id`）

**A. 波浪 (H_m / T_sec)**：`wave` 表，`YEAR IN (start.year, end.year)` 處理跨年，`H×0.01=H_m`，`TMEAN×0.1=T_sec`。

**B. 海流 (V / DIR)**：`curr` 表，`Z='4'`，`V×0.1`。

#### Step 6：最終合併與輸出

`outer join` 將各 param df 逐一 merge 進 `main`，切除頭尾超出時間範圍的列；異常值過濾（AT/WT > 40°C 或 ≤ 10°C 設為 NaN）；查詢所有 `src_ids` 對應站名組成 `src_names`。

**回傳 bundle dict：**

| 鍵 | 說明 |
|----|------|
| `stid` / `stname` | 主站代碼 / 名稱 |
| `df` | 合併完成的完整時序 DataFrame |
| `src_ids` / `src_names` | 各資料類型的實際來源站代碼 / 對應站名 |
| `tide_meta` | 水位儀器元數據 |
| `mr_full` / `mr_month` | 全年 / 當月平均潮差 |
| `tideh_df` / `tidehl_df` | 天文潮高低潮預報 / 觀測對應高低潮 |

---

## 10. VdC 散佈圖模組

見 §3.6。副儀器選擇邏輯、X 軸統一範圍 callback、匯出報表版的細節請直接參閱 `build_vdc_figure.py` 原始碼註解，內容已相當完整，本文件不重複列出實作細節。

---

## 11. 暴潮偏差報表圖模組

見 §3.7。

### 設計決策記錄

| 決策 | 結論 | 理由 |
|------|------|------|
| 暴潮偏差圖是否跟隨水位圖 zoom 範圍 | **不跟隨** | 颱風報告需呈現完整事件期間，局部裁切會導致海陸警色帶不完整，誤導判讀 |
| PNG 匯出是否同步 Y 軸手動範圍 | **不同步（已知限制）** | Y 軸 Patch 寫入後無法從 figure dict 直接反查範圍；列入 §13 |

---

## 12. 海洋參數診斷圖模組（唯讀，2026-08-19 新增）

### 背景

原本的「查看海洋參數」四子圖（水位／海氣象／暴潮與氣壓／波浪特性）只有 `draw_diagnostic()` 這條 HTML 路徑，每 3 站分頁、各自開新瀏覽器分頁，且不支援 QC 框選。這次把它整合進 Dash，成為第 4 個頁籤，但明確定位為**唯讀檢視**——QC 框選/SQL 產生功能仍只在「水位時序與 QC」頁籤提供。

### `build_diagnostic_figure.py`

```python
build_diagnostic_figure(bundles, land_range=None, typhoon_label=None) -> go.Figure
```

移植自 `draw_diagnostic()`，trace 建構邏輯（色系、每站四張子圖的欄位對應、高低潮/風向流向箭頭、警戒色帶等）與原版完全一致，只有以下差異：

1. **移除分頁**：原版「每 3 站一頁、開新瀏覽器分頁」的邏輯拿掉，改為單一可捲動的 Figure，一次涵蓋所有傳入的 bundles，作法與 `build_water_figure()` 對高站數（水位頁籤建議上限 45 站）的處理方式一致——不特別分頁，靠瀏覽器捲動。海洋參數模式沿用 Tkinter 端既有的 12 站建議上限，故最高約 `12 × 800px ≈ 9600px`。
2. **新增 `typhoon_label` 參數**：行為與 `build_water_figure()` 一致，非 None 時在每個子圖標題前綴「【颱風名】」。原版 `draw_diagnostic()` 沒有這個參數。
3. **移除 HTML 輸出副作用**：不再呼叫 `write_chart_html()` / `webbrowser.open()`，單純回傳 `go.Figure`。

### Dash 整合（Patch 6～10）

| Patch | 內容 |
|-------|------|
| 6 | `dash_app.py` import `build_diagnostic_figure` |
| 7 | 新增 `dcc.Tab(value="tab-diagnostic", label="海洋參數（唯讀）")`，內容為說明文字 + 統計摘要 Div（見 Patch 10） |
| 8 | `render_figure` 新增 `tab-diagnostic` 分支，讀 `dash_bridge` 目前快取的 bundle/land_range/typhoon_label 呼叫 `build_diagnostic_figure()`；`trace-meta-store` 回傳 `no_update`（此頁籤不支援框選） |
| 9（防呆） | `on_selection` 加入 `active_tab` 判斷，非 `tab-water` 時直接 `return no_update, no_update, no_update`，避免共用的 `main-graph` 元件在其他頁籤被誤用框選工具時寫出誤導性 SQL（此 guard 順帶也保護了 `tab-vdc`/`tab-surge`） |
| 10 | 在 `tab-diagnostic` 分支加入統計摘要（比照 Tkinter 版 `show_stats()`，見下） |

### 統計摘要（Patch 10）與一個順手修的 bug

Tkinter 版 `MainApp.show_stats()` 原本用 `'Obs' in df.columns` 判斷要不要顯示水位那一行統計。但 `'Obs'` 欄位只存在於「舊系統降級模式」（`tide_meta` 為空）；新系統多儀器站的水位欄位其實是 `WL_{primary_stid}`。也就是說**原版 `show_stats()` 對絕大多數現行測站（新系統多儀器站）根本不會顯示水位統計**，只有極少數還沒建 `stid_obs` 對應的舊測站才會顯示。

移植到 Dash 時一併修正：優先從 `tide_meta` 找出主測站算出 `WL_{primary_stid}`，找不到才退回 `'Obs'` 欄位。統計項目（有資料才顯示對應那一行）：

- 水位：`WL_{primary}` 或 `Obs`，平均/最高/最低（mm）
- 風速：`WS`，平均/最大（m/s）
- 示性波高：`H_m`，平均/最大（m）
- 流速：`V`，平均/最大（cm/s）

是否要回頭修正 Tkinter 版 `show_stats()` 本身（目前仍是舊寫法），留給你決定；Dash 版已經是正確版本，不影響任何現有功能。

### 已知限制

- `main-graph` 的 `dcc.Graph` config 是所有頁籤共用的單一設定，本頁籤沒有特別移除 Box Select／Lasso 按鈕；使用者仍看得到框選工具，但拖曳框選不會有作用（Patch 9 guard 會直接跳過）。要在 UI 上完全隱藏這兩顆按鈕，需要把 `config` 改成依 `active_tab` 動態輸出，屬於較大改動，目前判斷非必要。
- 尚未提供白底 PNG 匯出（對應 `build_water_report_figure()` 的角色）。若未來需要，可另外新增 `build_diagnostic_report_figure()`，白底版通常只需調整配色與 template，邏輯可直接複用 `build_diagnostic_figure.py` 的 trace 建構部分。

---

## 13. 已知限制與待辦事項

### 功能面

| 優先度 | 項目 | 說明 |
|--------|------|------|
| 🔴 高 | Mode 1/2/3 只支援 tide6 | 三個 SQL builder 都硬寫 `tide6`／`mrbank.surge`，不支援 wind / stemp6 / stemp1 等表。浮球/浮標整合會直接撞到這個限制，見下方「浮球/浮標整合」 |
| 🟡 中 | `draw_diagnostic`（HTML 版）QC 框選失效 | `mode="full"` 仍是舊 HTML 路徑，QC 框選功能不可用；Dash 的「海洋參數（唯讀）」頁籤本來就設計成唯讀，這點是預期行為，不是待修的 bug |
| 🟡 中 | 多測站 bundle 的 STID 切換 | 同時查詢多測站時，QC 面板的 STID 未提供切換 UI，`on_selection` 靠 `trace_meta` 反查框選點所屬測站，只有 tab-water 支援 |
| 🟡 中 | PNG 匯出 Y 軸範圍未同步 | 手動調整的 Y 軸範圍或 zoom 縮放均不會反映在匯出的白底 PNG 中；X 軸 zoom 範圍有同步 |
| 🟢 低 | 海洋參數（唯讀）頁籤沒有白底 PNG 匯出 | 見 §12 已知限制 |
| 🟢 低 | `uirevision` 應為動態版本號 | 目前固定為 `True`，多次 push 新資料後縮放狀態可能不正確重置 |
| 🟢 低 | `DEBUG print` 未清除 | `query_multi_tide_data` 等函式內仍有多行 `print("[DEBUG]...")` |
| 🟢 低 | `bundle-poll` 500ms 效率 | 未來可改以 websocket 推送取代輪詢 |
| ⬜ 追蹤中 | **浮球/浮標整合** | 獨立開發中的浮球/浮標品管工具（見對話紀錄）目前尚未併入主程式。已知落差：`fetch_bundle()` 完全沒有處理 kind=7（浮球）；`wind`/`stemp1`/`pres1`/`curr`/`wave` 各表的 QC 欄位命名慣例（大小寫、是否存在 QC 欄位）彼此不同，見 §6；三個 SQL builder 目前只認 `tide6`。整合前建議先確認：要不要跟主程式共用同一個 `dash_bridge` 快取與 Dash app 進程，還是維持獨立程式只共用 SQL 慣例。 |
| ⬜ 追蹤中 | `stemp1` 是否有 `qc` 欄位待覆核 | 見 §6 註記 |

### 架構面

- `dash_app.py` 中若還留有 `OceanDataEngine` Stub 類別（骨架階段佔位用），整合後可移除或改 import 真實版本。
- `plotly_qc_select.py`（舊版 HTTP callback server 整套機制）已被 Dash 完全取代，若專案目錄還留著這個檔案可以刪除。
- `draw_diagnostic()` 的 HTML 輸出路徑（`mode="full"`）目前與 Dash 的 `tab-diagnostic` 並存；等確認沒有人依賴舊路徑後，可以考慮從 Tkinter UI 拿掉「查看海洋參數」按鈕，只留 Dash 版。

---

## 14. 打包注意事項（PyInstaller）

### mysql.connector 必須使用 `--collect-all`

**錯誤做法（無效）：** `pyinstaller --hidden-import mysql.connector ...`

**正確做法：** `pyinstaller --collect-all mysql.connector ...`

`mysql.connector` 執行時會動態載入語系檔與驅動模組，`--hidden-import` 只能處理靜態 import，無法涵蓋這些執行期資源，打包後連線失敗時會因語系檔缺失觸發次生錯誤：

```
No localization support for language 'eng'
```

這個錯誤與**實際的連線問題無關**（如密碼錯誤、網路不通、host 無法解析），卻會出現在最上層，容易讓除錯方向偏向語系設定。

### 診斷方法：在 `login()` 的 except 區塊寫入完整 traceback

```python
except Exception as e:
    import traceback
    with open("error_log.txt", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
    raise
```

### 其他打包注意

- `babel` 語系資料：`frozen` 環境下需設定 `BABEL_DATA_PATH`（`ocean_plot_dash.py` 頂端已處理）
- `.env` 檔與 `對應站表格.csv` 必須與執行檔放在同一目錄
- Tkinter + Dash 雙執行緒打包後，`use_reloader=False` 是必要的；`debug=False` 在整合執行時也必須關閉
- 不使用 python-dotenv，一律手動解析 `.env`

---

## 15. 資料表說明（供 SQL 參考）

| 資料表 | 主要欄位 | 說明 |
|--------|---------|------|
| `tide6` | STID, DATATIME, MIN0~MIN9, QC | 6分鐘潮位原始資料 |
| `tide6ha` | STID, DATATIME, MIN0~MIN9, QC | 潮位預報（QC='h' 諧和，QC='a' 天文） |
| `st` | STID, stnac, stid_obs, stid_new, type, kind | 測站基本資料（type: 2=音波,3=壓力,4=雷達；kind: 1/2/3=氣象站,6=潮位站,7=浮球,8=浮標）|
| `stitemqc` | stid, qcid | 安內測站對應（MAPPING_SRC=db 時使用）|
| `tidestat` | STID, YEAR, MONTH, MR, SL, QC | 潮位統計（MR=平均潮差，MONTH=0 代表全年，SL='S' AND QC='Q' 篩選有效值）|
| `tideh` | STID, DATATIME, HEIGHT, HORL, QC | 天文潮高低潮預報極值（QC='a' 重建分析，QC='h' 調和預報）|
| `tidehl` | STID, DATATIME, HEIGHT, HORL, QC | 觀測對應高低潮（QC='Q' 品管通過）|
| `tsuwawa.warn` | STID, WARNVAL, STIDE | 大潮警戒值(WARNVAL)/注意值(STIDE)，單位 m，程式端 ×1000 換算 mm |
| `meteo` | stid, DATATIME, min, p, ws, wd, t | 氣象站氣壓/風速/風向/氣溫（min=0 為整點值，單位×0.1 還原）|
| `wind` | STID, TIME, VM, DM, Z, qc | 潮位站/浮標風速（VM×0.1=m/s）、風向（DM）；Z='6' 為潮位站，Z='2'/'3' 為浮標 |
| `pres1` | STID, DATATIME, HR0~HR23 | 浮標逐小時氣壓（HR 欄位，×0.1=hPa）|
| `pres6` | STID, DATATIME, HR0~HR23, QC | 潮位站逐小時氣壓（×0.1=hPa，僅用 QC='Q' 資料）|
| `stemp1` | STID, DATATIME, HR0~HR23, Z | 浮標溫度（Z='-3' 氣溫，Z='0' 海溫；×0.1=°C）；是否有 qc 欄位待覆核，見 §6 |
| `stemp6` | STID, DATATIME, HR0~HR23, Z, QC | 潮位站溫度（Z='-3' 氣溫，Z='0' 海溫；×0.1=°C；有 QC 分流）|
| `wave` | STID, YEAR, MONTH, DAY, HOUR, H, TMEAN | 浮標波浪（H×0.01=示性波高 m，TMEAN×0.1=平均週期 s）|
| `curr` | STID, TIME, V, D, Z | 浮標海流（V×0.1=流速 cm/s，D=流向，Z='4' 為主要層次）|
| `typhoonid` | id, cname, sponsor, WARN1BEG/END, WARN2BEG/END | 颱風資料（安外欄位小寫 warnSeaBeg，安內大寫 WARN1BEG；用 AS 統一）；`sponsor='LocalTime'` 篩選需加 `COLLATE utf8mb4_general_ci`（上游庫定序較舊，混用會拋 Illegal mix of collations）|

---

*本文件依 ocean_plot_dash.py、dash_bridge.py、dash_app.py、build_water_figure.py、build_diagnostic_figure.py、build_vdc_figure.py、build_surge_report_figure.py 七個檔案原始碼撰寫。如有修改請同步更新本文件，並記得刪除舊的 SYSTEM_DASH.md（內容已全部併入本檔）。*
