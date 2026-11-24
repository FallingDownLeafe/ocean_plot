# 🌊 海洋動力診斷儀表板 — SYSTEM.md

> 文件版本：2026-11-19 補記浮標波浪圖游標修正與週期子圖（§13）；前一版 2026-09-14（R16：潮位站環境擴充氣溫/氣壓、浮標與潮位站環境非Q紅叉、pres1/pres6 欄位修正、浮標圖改用 SVG 解決 WebGL 問題）

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
│  main_app.py                                                  │
│  ├─ LoginApp                                                  │
│  │    → 建立 OceanDataEngine（data_engine.py，DB 連線）        │
│  │    → 找可用 port，daemon 執行緒啟動 dash_app.app.run()      │
│  │    → 隱藏登入視窗，開啟 MainApp（Toplevel）                 │
│  └─ MainApp                                                   │
│       → 查詢控制 UI：颱風年份/事件、日期範圍、                 │
│         測站選擇（Notebook：🌊潮位站 / 📍浮球浮標，皆多選）    │
│       → _on_query() → 背景執行緒 _do_query()                  │
│            ├─ 逐站 fetch_bundle()（潮位站 + 浮標站合併查詢）   │
│            ├─ 浮標站另呼叫 fetch_wave1/wind/curr/wave/         │
│            │   pres1/stemp1 組成 buoy_qc_data                  │
│            ├─ 逐站 get_thresholds() 組成 thresholds_map        │
│            ├─ dash_bridge.bridge.set_bundle(bundles,           │
│            │   typhoon_info, thresholds_map, buoy_qc_data)     │
│            ├─ dash_bridge.bridge.set_selection(...)            │
│            └─ webbrowser.open(f'.../?key={key}')               │
│         （Dash 端三大工作區 —— QC 工作台／診斷總覽／事件報告 ── │
│          全部共用同一份透過 key 快取的查詢結果）                │
└──────────────────┬───────────────────────────────────────────┘
                   │  dash_bridge（同 process 內共用記憶體）
                   ▼
┌──────────────────────────────────────────────────────────────┐
│  Dash / Flask（daemon 執行緒，啟動於 LoginApp._connect()）      │
│  dash_app.py                                                  │
│  ├─ dcc.Interval（每 500ms 輪詢）→ poll_bundle callback        │
│  │     └─ dash_bridge.get_latest_key() 偵測新資料             │
│  ├─ render_water      → build_water_figure()   → water-graph  │
│  ├─ render_buoy       → 8×build_*_figure()     → buoy-graph-* │
│  ├─ render_diag       → build_diagnostic_figure()→ diag-graph │
│  ├─ render_vdc        → build_vdc_figure()      → vdc-graph   │
│  ├─ render_surge      → build_surge_interactive_figure()      │
│  ├─ render_report     → 逐站 _report_card()     → report-list │
│  ├─ auto_switch_tab   → qc-tabs.value（依 tide_meta 分流）     │
│  ├─ generate_sql      → sql-output（water-graph 框選）         │
│  └─ generate_buoy_sql → buoy-sql-output（buoy-graph-* 框選）   │
└──────────────────────────────────────────────────────────────┘

---

## 2. 專案目錄與環境設定

```
專案目錄/
├── main_app.py # 主程式（Tkinter GUI：LoginApp + MainApp，查詢流程調度）
├── data_engine.py # OceanDataEngine 資料引擎（所有 DB 查詢邏輯集中於此，無 UI 邏輯）
├── dash_app.py # Dash 儀表板（Layout + Callbacks）
├── dash_bridge.py # Tkinter ↔ Dash 跨執行緒資料橋接
├── station_registry.py # 測站分類常數（KIND_TIDE / KIND_BUOY）與 REGISTRY 參數表
├── sql_builders.py # QC SQL 產生器（Mode 1/2/3 + 浮標 build_buoy_qc_sql）
├── build_water_figure.py # 水位時序圖（互動深色版 + 白底匯出版）
├── build_buoy_figure.py # 浮球/浮標 8 種感測器圖（wave1/wind/curr/wave/pres1/stemp1）
├── build_diagnostic_figure.py # 海洋參數四子圖診斷圖（唯讀）
├── build_vdc_figure.py # Van de Casteele 散佈圖（互動版 + 匯出報表版）
├── build_surge_report_figure.py # 暴潮偏差圖（白底單站版 + 互動多站版）
├── qcflag_engine.py # 潮位自動檢核旗標讀取與整理（唯讀，2026-11-20；§3.9）
├── build_qcflag_figure.py # 潮位自動檢核頁籤的圖（2026-11-20；§3.9）
├── 對應站表格.csv # 安外用測站對應表（安內改從 DB 讀取）
└── .env # 環境變數（DB_HOST_INT、DB_HOST_EXT、DB_USER...）
```


### `.env` 可設定的變數

| 變數 | 說明 | 範例 |
|------|------|------|
| `DB_HOST_INT` | 主資料庫主機 IP | `61.56.13.160` |
| `DB_HOST_EXT` | 外部伺服器 IP（warn 連線用）| `61.56.13.104` |
| `DB_PORT` | DB port | `3306` |
| `DB_USER` | DB 帳號（登入表單預設值來源） | |
| `DB_PASS` | DB 密碼（登入表單預設值來源） | |
| `DB_NAME` | 主 DB 名稱 | `mrbank` |
| `TYPHOON_DB` | 颱風 DB 名稱 | `med_data`（安外）/ `mrbank`（安內）|
| `MAPPING_SRC` | 測站對應來源 | `csv` / `db` |
| `MAPPING_CSV` | CSV 路徑 | `對應站表格.csv` |
| `QC_DB_HOST`／`QC_DB_PORT`／`QC_DB_USER`／`QC_DB_PASS`／`QC_DB_NAME` | 潮位自動檢核旗標庫（唯讀頁籤、水位大圖疊點用，2026-11-20；§3.9）。和登入畫面的主資料庫分開，只從 `.env` 讀；`QC_DB_PORT` 可省略（3306）。**沒設定或連不上時只有該頁籤顯示提示**，啟動與其他頁籤不受影響。開發機指向本機 `test_db`；部署到 .70 的 `.env` 不要放 | `localhost`／`test_db` |

> ~~`DASH_PORT`~~：舊版文件列過這個變數，但目前完全沒有程式碼讀取它。Dash port 一律由 `_find_free_port(start=8050)` 自動往後找可用埠，不吃 `.env`。如果之後想讓 port 可設定，這是待新增功能，不是文件錯誤。

`main_app.py` 建立 `OceanDataEngine` 的實際方式（`LoginApp._connect()`）：

```python
# host / user / password 來自登入表單 Entry（表單初始值用 os.getenv(key, '') 帶入，
# 使用者可在登入畫面手動覆蓋，不是直接讀 .env）
engine = OceanDataEngine(
    host=host, user=user, password=pwd,
    database=os.getenv('DB_NAME', 'mrbank'),
    port=int(os.getenv('DB_PORT', '3306')),
    warn_host=os.getenv('DB_HOST_EXT', '61.56.13.104'),   # ⚠️ 沒設就靜默連到這個外部 IP
    mapping_src=os.getenv('MAPPING_SRC', 'csv'),
    typhoon_db=os.getenv('TYPHOON_DB', 'med_data'),
)
```

不使用 python-dotenv，`main_app.py` 頂端自帶 `_load_env()` 手動解析器（模組載入時自動呼叫一次）。`data_engine.py` 內另外定義了一份邏輯幾乎相同的 `load_dotenv_manual()`，但目前的呼叫鏈裡沒有任何地方呼叫它——是重複但目前無害的程式碼，列入 §13。

---

## 3. 各模組職責

### 3.1 `main_app.py` 與 `data_engine.py`

**角色分工：`data_engine.py` 是純資料層（`OceanDataEngine`，不含任何 UI 邏輯）；`main_app.py` 是純 Tkinter GUI（`LoginApp` + `MainApp`），透過 `from data_engine import OceanDataEngine` 匯入使用，不含任何 DB 查詢邏輯。**

#### `OceanDataEngine`（`data_engine.py`）

| 方法 | 說明 |
|------|------|
| `__init__()` | 建立主連線（`self.conn`）與 warn 連線（`self.warn_conn`，`warn_host` 未設或連線失敗則為 `None`），呼叫 `load_mapping()` |
| `load_mapping()` | 載入 STID ↔ QCID 對應，並從 `st` 表補充中文站名（`self.name_map`）、站種（`self.kind_map`）、業務單位（`self.sponsor_map`，供 `MainApp` 測站列表標籤顯示「站名（單位）」用）。另從 `st` 同時查詢 `datebeg AS DATEBEG`、`dateend AS DATEEND` 一併存入 `mapping_df`，供 `_fill_stations()` 時間重疊篩選使用（不影響 `kind_map`/`name_map`/`sponsor_map` 的現有邏輯）。 |
| `fetch_years()` / `fetch_typhoons()` | 查詢颱風資料庫（`TYPHOON_DB` env 控制），安內/安外欄位大小寫不同各自 fallback |
| `expand_data(df, val_name, freq)` | `MIN0`～`MIN9`（`freq='6min'`）或 `HR0`～`HR23`（`freq='1h'`）寬表展開為時序長格式，回傳欄位 `Time`（title case） |
| `fetch_tide_instruments(stid)` | 查詢測站下所有水位儀器（音波/壓力/雷達），以 `stid_obs` 為連結鍵 |
| `query_multi_tide_data()` | 多儀器批量查詢 `tide6`/`tide6ha`，QC 拆分（`QC=Q` vs `QC≠Q`）後 outer merge |
| `fetch_tidal_extrema()` | 查詢 `tideh`/`tidehl`，供高低潮標記 |
| `get_thresholds(stid)` | 查 `med.p_setting_warn`（走 `self.warn_conn`），回傳 `{'stide_mm','warnval_mm'}` 或 `None` |
| `fetch_wind/fetch_stemp6/fetch_pres6/fetch_wave1/fetch_stemp1/fetch_wave/fetch_curr/fetch_pres1` | QC 工作台／浮標 QC 用的各資料表獨立查詢方法，方法名稱對應 `station_registry.REGISTRY[kind]['params'][i]['fetch_method']` |
| `fetch_bundle(stid, start, end)` | 主要查詢入口，整合水位、環境、海洋資料，完整流程見 §9 |

kind=7（浮球）不在 `fetch_bundle()` 的環境資料合併迴圈（Step 4）與海洋資料區塊（Step 5）處理範圍內（這兩處只認 kind ∈ {'1','2','3','6','8'}）。但 R7 起，`fetch_bundle()` 額外針對 kind ∈ {'7','8'} 組裝浮標感測器原始 DataFrame（`wave1_df` 兩者皆有；`wind_df`/`curr_df`/`wave_df`/`pres1_df` 只有 kind=8 才查）供浮球/浮標 QC 頁籤使用——也就是說 kind=7 在「衍生環境參數併入主 df」這件事上仍未支援，但在「浮標 QC 頁籤原始資料」這件事上已部分支援。完整浮球/浮標環境整合仍是待辦事項，見 §13。

#### `MainApp`（`main_app.py`，Tkinter 主介面）

- **登入視窗（`LoginApp`）**：填入 DB IP／帳號／密碼 → 按「連線」建立 `OceanDataEngine` → 找可用 port、daemon 執行緒啟動 Dash → 隱藏登入視窗、開啟 `MainApp` Toplevel
- **查詢條件區塊**：`tk.LabelFrame` + `grid` 排版，颱風年份/事件下拉選單、起訖日期（`tkcalendar.DateEntry`，未安裝時退化為 `Entry`）
  - 選颱風後 `_auto_fill_date()` 會依海警期間（`WARN1BEG`/`WARN1END`，防禦性查找多種欄位別名）自動填入起訖日期（前後各 +1 天）
- **測站選單**：`ttk.Notebook` 兩頁籤「🌊 潮位站」「📍 浮球/浮標」，依 `station_registry.KIND_TIDE`/`KIND_BUOY` 分流填入多選 `Listbox`（`exportselection=False`，避免切換分頁時選取被清空）；每站標籤格式為 `{stid}  {站名}（{sponsor}）`；`_fill_stations()` 依查詢起訖日期與 `mapping_df.DATEBEG`/`DATEEND` 做時間重疊篩選，只列出與查詢期間有交集的測站；任一欄位為 `NaN` 則不過濾該站。日期異動（手動輸入或 `_auto_fill_date()` 填入颱風日期後）自動重新篩選。
- **查詢按鈕**：`_on_query()` 同時取兩個 Listbox 的選取站碼（潮位+浮標），驗證至少選一站、日期格式與順序正確後，起背景執行緒跑 `_do_query()`
  - `_do_query()`：對所有選取站合併呼叫 `fetch_bundle()`；浮標站額外呼叫 `fetch_wave1`/`fetch_wind`/`fetch_curr`/`fetch_wave`/`fetch_pres1`/`fetch_stemp1` 組成 `buoy_qc_data`；逐站呼叫 `self.ENGINE.get_thresholds(sid)` 組成 `thresholds_map`；推送到 `dash_bridge.bridge.set_bundle(...)`（含 `buoy_qc_data`）與 `set_selection(...)`；產生帶 key 的 URL，回主執行緒顯示完成訊息並 `webbrowser.open()`

> V1 版本原有的「只列有資料站」動態篩選按鈕、「選氣象署／非氣象署」單位快速勾選按鈕、時間範圍上限防呆，在 V2 重寫後**沒有帶入**，目前測站分頁只有基本多選 Listbox，沒有進階篩選/批次勾選/時間範圍上限。若日後需要，屬於功能新增，不是文件校正範圍。

**`get_thresholds` 補充**：舊版文件曾寫成獨立函式 `get_tsuwawa_thresholds(conn, stid)`，實際上是 `OceanDataEngine` 的 instance method（見上表），呼叫端用 `self.ENGINE.get_thresholds(sid)`。回傳 key 名稱為 `stide_mm`/`warnval_mm`（R10 已核實，詳見 §3.7）。

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

以 daemon=True 的執行緒在 LoginApp._connect() 啟動（Dash server 先啟動、MainApp 視窗才開啟），監聽 `127.0.0.1:{DASH_PORT}`（動態找可用 port，從 8050 開始往後搜尋）。

#### Layout 結構

```
app.layout
├─ dcc.Location(id='url')
├─ dcc.Interval(id='bundle-poll', 500ms)
├─ dcc.Store(id='bundle-key-store')
├─ dcc.Store(id='zoom-range-store')       # water-graph zoom x
├─ dcc.Store(id='trace-meta-store')       # curveNumber→STID（qc-water）
├─ Header
└─ dcc.Tabs(id='ws-tabs')
    ├─ ws-qc：QC 工作台
    │   └─ dcc.Tabs(id='qc-tabs')         # auto_switch_tab 依站種切換
    │       ├─ qc-water
    │       │   ├─ dcc.Graph(id='water-graph', select2d/lasso2d)
    │       │   └─ 右側控制面板（360px）
    │       │       ├─ 自動檢核（試行）疊點勾選（autoqc-toggle）＋筆數／提示（autoqc-status）（2026-11-24；§3.9）
    │       │       ├─ Mode 1/2/3 RadioItems（qc-mode）
    │       │       ├─ 新 QC 值（qc-new-val）
    │       │       ├─ Mode 2 運算符/數（mode2-op / mode2-operand）
    │       │       ├─ Mode 3 按鈕（mode3-btn）
    │       │       ├─ SQL Textarea（sql-output）
    │       │       │   ├─ 進階圖層顯示（adv-layer-toggle，R11）
    │       │       │   ├─ 進階圖層目標儀器（adv-layer-target，R15）
    │       │       └─ Y 軸控制（apply-yaxis-btn / reset-yaxis-btn）
    │       ├─ qc-vdc
    │       ├─ qc-tideenv
    │       │   ├─ 4× dcc.Graph（tideenv-graph-wind/stemp6/stemp6-air/pres6，後兩項 R16 新增）
    │       │   └─ 右側控制面板（360px）
    │       │       ├─ 新旗標（tideenv-qc-new）/ 舊旗標（tideenv-qc-old）
    │       │       └─ SQL Textarea（tideenv-sql-output）
    │       └─ qc-buoy
    │           ├─ 8× dcc.Graph（buoy-graph-wave1/wind/curr/wave/
    │           │                 pres1/stemp-air/stemp-s0/stemp-s1）
    │           └─ 右側控制面板（360px）
    │               ├─ 新旗標（buoy-qc-new）/ 舊旗標（buoy-qc-old）
    │               └─ SQL Textarea（buoy-sql-output）
    ├─ ws-diag：診斷總覽
    │   └─ dcc.Tabs(id='diag-tabs')
    │       ├─ diag-ocean（潮位站專用）
    │       │   └─ dcc.Graph(id='diag-graph', 無 select2d)
    │       └─ diag-qcflag 潮位自動檢核（唯讀，2026-11-20；§3.9；2026-11-24 前叫「多采潮位旗標」）
    │           ├─ dcc.Graph(id='qcflag-graph', 無 select2d)
    │           └─ 右側面板：儀器選單（qcflag-inst）、多采模式列勾選（qcflag-show-dc）、
    │              狀態（qcflag-status）、事件表／筆數／判定方式（qcflag-info）
    └─ ws-event
        └─ dcc.Tabs(id='event-tabs')
            ├─ event-surge   R9 接通
            └─ event-report  R10 接通（白底 PNG 下載 + zip 打包）
```

> **V2 修正**：以下這段原文描述「`main-graph` 四頁籤共用單一實體＋`on_selection`」是 V1 舊架構，與本節稍後「`on_selection` 在 V2 中均已移除」自相矛盾，予以改寫。

V2 每個頁籤各自有獨立的 `dcc.Graph` 元件（`water-graph`／`vdc-graph`／`surge-graph`／`diag-graph`），各自獨立的 `config` 與獨立的 render callback，不共用單一實體。Box Select 工具按鈕（`modeBarButtonsToAdd: ["select2d", "lasso2d"]`）只寫在 `water-graph`（§3.4）與 8 個 `buoy-graph-*`（§3.1 浮標 QC）的 config 裡；`vdc-graph`／`surge-graph`／`diag-graph` 沒有加這個 config，工具列上不會出現 Box Select 按鈕，也就無從框選出無意義的 SQL——這是 V2 用「不給工具」取代 V1「給工具但選了也沒用」的設計差異。

#### Callbacks

| Callback | 觸發 | 作用 |
|----------|------|------|
| `poll_bundle` |	`bundle-poll`（500ms） |	`dash_bridge.get_latest_key()` → `bundle-key-store` |
| `render_water` |	`bundle-key-store`、`url.search`、`ws-tabs`、`qc-tabs`、`autoqc-toggle`（2026-11-24） |	→ `build_water_figure()` → `water-graph`；無 `tide_meta` 站顯示友善提示；回傳 `trace-meta-store`、`autoqc-status`。新增：R12 tab-guard（`ws-tabs='ws-qc'` 且 `qc-tabs='qc-water'` 時才執行），否則 `no_update`；命中 `_water_fig_cache` 直接回傳（快取 key＝(bundle_key, 是否疊自動檢核)；旗標庫沒設定／連不上時不快取） |
| `render_buoy` |	`bundle-key-store`、`url.search` |	浮標 bundle → 8×`build_*_figure()` → `buoy-graph-*`（figure+style）；17 Output；多站合併 trace 並加站名後綴 |
| `render_tideenv`（R14，R16 擴充） |	`bundle-key-store`、`url.search`、`ws-tabs`、`qc-tabs` |	潮位站 bundle → `build_wind_figure()`/`build_stemp1_figure(df, z=0)`/`build_stemp1_figure(df, z=-3)`/`build_pres1_figure(df)` → 對應 4 張圖；需 `ws-qc`+`qc-tideenv`，否則 `no_update`；命中 `_tideenv_fig_cache` 直接回傳 |
| `render_diag` |	`bundle-key-store`、`url.search`、`ws-tabs`、`diag-tabs` |	過濾 `tide_meta` bundle → `build_diagnostic_figure()` → `diag-graph`；純浮標查詢 `no_update`。新增：`ws-tabs='ws-diag'` 且 `diag-tabs='diag-ocean'`（2026-11-20 加）時才執行，否則 `no_update`；命中 `_diag_fig_cache` 直接回傳 |
| `update_qcflag_insts`（2026-11-20） | `bundle-key-store`、`url.search` | 本次查詢各潮位站 `tide_meta` 的儀器 → `qcflag-inst` 選項（值＝「主站代碼、直線符號、儀器代碼」，主儀器在前）；目前選的不在新查詢裡就改選第一台。不連旗標庫 |
| `render_qcflag`（2026-11-20） | `bundle-key-store`、`url.search`、`ws-tabs`、`diag-tabs`、`qcflag-inst`、`qcflag-show-dc` | 需 `ws-diag`＋`diag-qcflag`。旗標庫讀取全部在 try 內：沒設定／連不上／該期間沒旗標 → 空白圖＋提示文字（不快取連線失敗，下次切回再試）。否則 → `build_qcflag_figure()` → `qcflag-graph`＋狀態＋右側表格；快取 key＝(bundle_key, 儀器, 是否顯示 dc_*) |
| `auto_switch_tab` |	`bundle-key-store`、`url.search` |	依 `tide_meta` 切換 `qc-tabs.value`（`qc-water`/`qc-buoy`）；已在目標頁籤則 `no_update` |
| `generate_sql` |	`water-graph.selectedData`、`mode3-btn`、`qc-mode` |	`ctx.triggered_id` 決定路徑：Mode 3 走按鈕；Mode 1/2 需框選；→ `sql-output`。2026-11-24：先用 `_drop_autoqc_points()` 拿掉自動檢核疊點（`trace_meta` field_type＝`autoqc`，和觀測點重疊會一起被框到），SQL 只依觀測點產生 |
| `generate_buoy_sql` |	8×`buoy-graph-*.selectedData` |	`ctx.triggered_id` → `_BUOY_GRAPH_META` → `build_buoy_qc_sql()` → `buoy-sql-output`；多站固定取第一個浮標 bundle stid |
| `generate_tideenv_sql`（R14，R16 擴充） |	4 張圖各自的 `selectedData` |	`ctx.triggered_id` → `_TIDEENV_GRAPH_META` → `build_buoy_qc_sql()` → `tideenv-sql-output`；stemp6／stemp6-air／pres6 皆走 `min_format=True`（DATATIME 只截到整點）；多站固定取第一個潮位站 bundle stid |
| `capture_zoom` |	`water-graph.relayoutData` |	`xaxis.range` → `zoom-range-store` |
| `toggle_mode_ui` | 	`qc-mode` | 	顯示/隱藏 Mode 2/3 附屬控制 | 
| `update_adv_layer_targets`（R15） | `trace-meta-store` | 依本次查詢的 `trace_meta`（`lf`/`ewma`/`smooth` 項目）動態列出有進階圖層的儀器清單 → `adv-layer-target` 選項；原選的儀器不在新清單裡才重置為「全部儀器」（2026-11-24 前一律重置；勾自動檢核會重畫同一查詢的圖、更新 `trace-meta-store`，不該把選擇洗掉） |
| `toggle_adv_layers`（R15 改版） | `adv-layer-toggle`、`adv-layer-target`、`trace-meta-store` | 依「圖層類型」+「目標儀器」雙重篩選切換 overlay 的 `visible`；`trace-meta-store` 由 State 改 Input，修正「換站後勾選框與畫面不同步」的問題 |
| `update_ty_label` |	`bundle-key-store` |	`get_typhoon_info()` → `mode3-ty-label` |
| `apply_yaxis` / `reset_yaxis` |	Y 軸按鈕 |	Patch `water-graph` layout；需 Dash ≥ 2.9 |
| `render_vdc` | `bundle-key-store`、`url.search`、`vdc-diff-type`、`zoom-range-store`、`ws-tabs`、`qc-tabs` | 潮位站 bundle → `build_vdc_figure()` → `vdc-graph`；`zoom-range-store` 格式轉換（xmin/xmax → x_start/x_end）；stats_summary → `vdc-stats-panel`。新增：需 `ws-qc`+`qc-vdc`，否則 `no_update`；快取 key = (bundle_key, diff_type, zoom_range) |
| `render_surge` | `bundle-key-store`、`url.search`、`surge-pred-source`、`surge-legend-mode`、`ws-tabs`、`event-tabs` | 潮位站 bundle → `build_surge_interactive_figure()` → `surge-graph`；不跟隨 `zoom-range-store`。新增：需 `ws-event`+`event-surge`，否則 `no_update`；快取 key = (bundle_key, pred_source, legend_mode) |
| `render_report`（R10） | `bundle-key-store`、`url.search`、`zoom-range-store` | 逐站 `_report_card()`（暴潮偏差 + 水位白底預覽）→ `report-list`；僅水位預覽套用 zoom-range-store，暴潮偏差固定完整事件期間 |
| `download_surge_report`（R10） | `{'type':'dl-surge-btn','index':MATCH}` | 單站暴潮偏差 PNG（1600×700）→ `dcc.Download` |
| `download_water_report`（R10） | `{'type':'dl-water-btn','index':MATCH}` | 單站水位 PNG（1900×600，套用 zoom-range-store）→ `dcc.Download` |
| `download_all_reports_zip`（R10） | `dl-all-btn.n_clicks` | 全部潮位站暴潮偏差+水位打包 zip；同步生成 + `dcc.Loading` 轉圈；單站失敗不影響其他站，失敗清單寫入 zip 內 `_失敗清單.txt` |

事件報告白底預覽圖 R10 起關閉拖曳縮放（`dragmode=False`），避免誤以為在預覽上縮放會影響下載；水位下載範圍固定由 zoom-range-store 決定。

`render_figure`（V1 單一大 callback）與 `on_selection` 在 V2 中均已移除。各工作區有各自的 render callback，`generate_sql` 天然只接收 `water-graph.selectedData`，無需 tab guard。

**R12 tab-guard + FIFO 快取（佈局 bug 最終修正，2026-09-07）**：
`render_water`/`render_diag`/`render_vdc`/`render_surge` 均加入對應 Tab Input 作為 guard，非目標頁籤時一律回傳 `no_update`，強制切回目標頁籤時重新呼叫 `build_*_figure()`（解決 `dcc.Tabs` 以 CSS `display:none` 隱藏時 Plotly 無法正確 resize 的根因）。各 callback 前置 FIFO 記憶體快取（`_water_fig_cache`、`_diag_fig_cache`、`_vdc_fig_cache`、`_surge_fig_cache`，上限各 10 筆，以 `_cache_put()` 維護 FIFO 淘汰）；同一查詢參數再次切回分頁時直接回傳快取，不重算。純 browser-side `Plotly.Plots.resize()` clientside callback 已確認不足，tab-guard 為目前唯一有效方案。VdC 散佈圖因「無副儀器」站產生空子圖、標題重疊的問題（與 tab-switch resize 問題為兩個獨立成因）測試確認已隨同解決；若復發請檢查 `build_vdc_figure.py` 的空子圖 `domain`/`height` 計算邏輯。

**_BUOY_GRAPH_META（generate_buoy_sql 用）：**
|Graph ID |	table |	Z |	hr_format |
|----------|------|------|------|
|`buoy-graph-wave1` |	 `wave1` |	None |	False |
|`buoy-graph-wind` |	`wind` |	'2' |	False |
|`buoy-graph-curr` |	`curr` |	'4' |	False |
|`buoy-graph-wave` |	`wave` |	None |	False |
|`buoy-graph-pres1` |	`pres1` |	None |	True（DATATIME）|
|`buoy-graph-stemp-air` | `stemp1` |	'-3' |	True（DATATIME）|
|`buoy-graph-stemp-s0` |`stemp1` |	'0' |	True（DATATIME）|
|`buoy-graph-stemp-s1` |	`stemp1` |	'1' |	True（DATATIME）|

**_TIDEENV_GRAPH_META（generate_tideenv_sql 用，R16 補記並擴充後兩列）：**
|Graph ID |	table |	Z |	min_format |
|----------|------|------|------|
|`tideenv-graph-wind` |	`wind` |	'6' |	False |
|`tideenv-graph-stemp6` |	`stemp6` |	'0' |	True |
|`tideenv-graph-stemp6-air` |	`stemp6` |	'-3' |	True |
|`tideenv-graph-pres6` |	`pres6` |	None |	True |

#### SQL 工具函式（獨立於 Tkinter）

| 函式 | 說明 |
|------|------|
| `_clean_ts(ts)` | 清理 Plotly 時間戳（`T`→空格、去毫秒），轉為 MySQL 可接受格式 |
| `build_mode1_sql(sel, stid, new_qc)` | Mode 1：產生按時間範圍與 y 值範圍篩選的 `UPDATE tide6 SET QC=...` |
| `build_mode2_sql_by_time(sel, stid, op, operand)` | Mode 2：從被框選點的展開 Time 反推 `DATATIME` 與 `MIN{N}` 欄位，產生 `MIN{N} = MIN{N} OP operand` |
| `build_mode3_sql(t_start, t_end, stid, bundle_key)` | Mode 3：對所有已載入 bundles 計算暴潮統計值，產生 `INSERT INTO mrbank.surge` 語句 |
| `_adapt_selected_data(selected_data)` | 將 Dash `selectedData` 格式轉換為 SQL builder 所需的 `sel` dict，支援多子圖的 `x2`/`x3`… range key |

Mode 1/2/3 這三個 builder **目前只支援 `tide6`**，是已知限制，見 §13。
浮標 QC 走 `build_buoy_qc_sql(sel, stid, table, old_qc, new_qc, z, hr_format)`，位於 `sql_builders.py`，見 §5 浮標 Mode 1。
`_adapt_selected_data` 在 V2 改名為 `_extract_sel_dict`（功能相同）。

---

### 3.4 `build_water_figure.py`

**角色：純函式水位圖繪製器（無副作用）**

```python
build_water_figure(bundles, land_range=None, typhoon_label=None, autoqc_marks=None) -> (go.Figure, trace_meta)
```

`autoqc_marks`（2026-11-24）：`{主站 stid: 標記表}`，自動檢核（試行）彙總 3／4 的點（§3.9「水位大圖疊點」）。每列最多兩個 trace（`自動檢核 不通過（試行）` 空心圈、`自動檢核 可疑（試行）` 空心菱形，同列各儀器合在一起；點超過 300 個時縮小），在儀器差值之後、颱風色帶之前加入，所以會算進該列圖例高度。`trace_meta` 記 `field_type='autoqc'`。hovertemplate 含 `%{x}`，`hover_rows` 視為自己顯示時間的稀疏標記。

每個 bundle 佔一列，`shared_xaxes=True`，雙 y 軸（左水位、右差值）。繪製內容：各儀器水位（校正值/低頻趨勢/EWMA/原始異常值/1H平滑）、預報水位（諧和/天文潮）、儀器差值、颱風陸警色帶、高低潮極值標記。回傳的 `trace_meta`（`[{stid, field_type}, ...]`）供 `generate_sql` 反查框選點屬於哪個測站。

`舊系統降級路徑`（`tide_meta` 為空）：只畫 `Obs`（藍線）與 `Pre`（綠點線）兩欄，避免 KeyError。

另有白底匯出版 `build_water_report_figure(bundles, land_range=None, zoom_range=None)`，樣式改為 `plotly_white`，供 `export_water_report` 使用。

**R15 圖例改版（多站/多儀器圖例重疊問題）**：

原本各測站的多重圖例（`legend`/`legend2`/...）雖然用 `y_top = domain[1]`
對齊了子圖頂端，但沒有依「該列圖例實際項目數」保留對應高度，儀器數多
的測站（如 3 儀器混合站）圖例項目一多就會溢出、蓋到下一列子圖的圖例。
全域等比例拉高 `height`（例如 `600 * n` 改 `800 * n`）無法解決，因為
各測站儀器數不同，等比例放大對「項目少」的站是浪費空間，對「項目多」
的站仍然不夠。

**R15 解法（`build_water_figure()` 內部 §6.5～§7）：**
1. 低頻趨勢(25h-MA)／EWMA／1H平滑 三種 overlay 改為 `showlegend=False`，
   不再佔原生圖例名額（原本即使 `visible="legendonly"` 也會佔一個圖例
   列位）；改由 `dash_app.py` 的「進階圖層顯示」勾選框控制顯示/隱藏
   （見下方 R15 dash_app.py 段落）。
2. 掃描 `fig.data`，依 `trace.yaxis` 反推每個 trace 屬於第幾列，統計
   各列「會進圖例」的 trace 數（`legend_counts`），計算每列所需高度：
   `row_px = max(_ROW_BASE_PX, _LEGEND_PAD_PX + cnt * _LEGEND_ITEM_PX)`。
3. 依 `row_px` 重新分配各列 `yaxis.domain`（取代 `make_subplots` 預設的
   等分 domain），子圖標題 annotation 的 y 值同步位移，圖例的 `y`（對齊
   子圖頂端）也依新 domain 重新計算。
4. 全域 `height` 從固定 `600 * n` 改為 `sum(row_px) + gap + margin` 動態
   加總，不再是每列等高。

`_LEGEND_ITEM_PX`（單一圖例列估計高度）/`_LEGEND_PAD_PX`（圖例與標題
間留白）/`_ROW_BASE_PX`（子圖最低高度）為經驗估計值，測試環境上述常數
取 20/60/420 運作正常；若後續字體或項目密度改變，可視情況微調。

`trace_meta` 的 `lf`/`ewma`/`smooth` 三種 `field_type` 項目新增 `label`
欄位（人類可讀的儀器標籤，如「淡海(11005)-壓力式」），供 `dash_app.py`
的「進階圖層目標儀器」選單使用。

---

### 3.5 `build_diagnostic_figure.py`

**2026-08-19 新增。** 詳見 §12，此處僅列表定位：唯讀四子圖診斷圖（水位／海氣象／暴潮與氣壓／波浪特性），對應 Dash 的 `diag-ocean` 頁籤。只接受潮位站 bundle；浮標 bundle 由 render_diag 過濾排除。

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

#### `OceanDataEngine.get_thresholds(self, stid)`（data_engine.py）

查詢 `med.p_setting_warn` 表（走 `self.warn_conn`），取得大潮注意值（`STIDE`）與暴潮警戒值（`WARNVAL`），單位換算為 mm，回傳 `{'stide_mm': int, 'warnval_mm': int}`。`warn_conn` 未建立（安內環境）或查無資料時回傳 `None`，不拋例外。

> 舊版文件寫成獨立函式 `get_tsuwawa_thresholds(conn, stid)`，實際上是 `OceanDataEngine` 的 instance method（詳見 §3.1）。R10 已核實回傳 key 名稱為 `stide_mm`/`warnval_mm`（過去猜測 key 名稱如 `'注意值_mm'` 曾造成 KeyError）。

#### `build_surge_report_figure(bundle, typhoon_info, thresholds, pred_source="auto")`

單站白底報表圖，供 Dash `event-report` 頁籤的預覽/下載使用。`thresholds` 即上面 `get_thresholds()` 的回傳值；`_resolve_threshold_values()` 統一解析 `stide_mm`/`warnval_mm`，並保留中文 key 名稱的相容 fallback。左軸水位（觀測 + 預報），右軸暴潮偏差；`pred_source` 控制預報來源優先序（`auto` 優先天文潮分析 `pred_a`，無則調和預報 `pred_h`）；含潮位注意值/警戒值水平線、海警/陸警色帶標註。

#### `build_surge_interactive_figure(bundles, typhoon_info, thresholds_map, pred_source="auto", legend_mode="shared", y_axis_mode="auto")`

多站深色互動版，對應 `event-surge`。`legend_mode="shared"`（預設）全圖共用一組圖例置底；`"individual"` 每個子圖各自一組圖例內嵌右上角，避免蓋到左上角子圖標題。`y_axis_mode="auto"` 各站獨立動態留白；`"fixed"` 跨站全域統一範圍，詳見 §11。

---

### 3.8 `typhoon_bands.py`、`buoy_checks.py`、`hover_rows.py`（2026-11-20 新增）

- `hover_rows.py`：`apply_row_hover(fig)` 統一游標規則（規則全文在檔頭；待辦表 §13「x unified 游標誤植」）。`dash_app` 各 render callback 回傳前呼叫；畫圖模組本身不呼叫。已自帶載體的子圖（`trace.meta == 'row_hover'`，浮標波浪圖）會跳過。
- `line_style.py`（2026-11-20 新增）：全系統折線粗細三級，各 `build_*_figure.py` 都 import。`MAIN` 2.2＝觀測值本身；`SUB` 1.6＝同一子圖的配角（一律虛線／點線：天文潮與預報、HMAX／VG 最大值、診斷總覽同子圖第二個 y 軸的變數、低頻趨勢／EWMA、注意值／警戒值、VdC 回歸線）；`GUIDE` 1.0＝參考線（事件報告颱風起訖垂直線、VdC 直方圖 0／平均／標準差線，原本就刻意細，沒改）。原本主線 1.2～1.5、配角 1.0～1.2 混用（同一種量在不同頁籤粗細不同，例：風速浮標頁 1.5、診斷總覽 1.2），統一後只改這一檔。標記（marker）大小與邊框不在統一範圍。

以下兩者都是純函式（DataFrame／dict 進出，不 import Dash），畫圖模組只讀它們的結果。

- `typhoon_bands.py`：`normalize_typhoon_info(info)` 補齊 `id`／`cname`／`warnSeaBeg`／`warnSeaEnd`／`warnLandBeg`／`warnLandEnd`（原欄位保留）；`warn_ranges(info, start, end)` 回傳 `{'sea': {...}|None, 'land': {...}|None}`，每段含實際起訖 `beg`／`end`（給文字）與裁到查詢期間的 `x0`／`x1`（給色帶），都不在查詢期間時回傳 None；`add_warn_bands(fig, ranges, row, col, theme='dark'|'light', label, layer)` 畫色帶與兩行文字（相容舊格式 `(beg, end)` tuple，視為陸警）。`main_app` 在查詢完成時呼叫前兩者，結果經 bridge 的 `typhoon_info`、`land_range` 傳下去。
- `buoy_checks.py`：`CHECKS`（代碼→名稱、等級、判定說明）、`wave_checks(df, h_col, t_col, depth_m)` 回傳標記表（`TIME, CHECK, LEVEL, LAYER, Y, NOTE`）、`summarize(marks)` 給右側面板的文字。`render_buoy` 在勾選框打開時對每站 wave1／wave 計算，傳給 `build_wave1_figure(df, marks=)`／`build_wave_figure(df, marks=)`。日後多采潮位旗標也用同一標記表格式（§13）。

### 3.9 `qcflag_engine.py`、`build_qcflag_figure.py`（潮位自動檢核唯讀頁籤＋水位大圖疊點）

**名稱（2026-11-24 使用者決定改）**：畫面上一律叫「自動檢核（試行）」，不寫「多采」。這套檢核規則是檢核提列測試專案以多采初步程式、既有程式與國際文獻整理出來的，不是多采廠商的檢核；寫「多采」會誤導。「多采模式列（dc_*）」真的是多采原程式的結果，保留原名。程式內部名稱（`qcflag_*`、專案資料夾 `2026多采\`）不改。

位置：診斷總覽 → 「潮位自動檢核（唯讀）」（2026-11-20 新增，原名「多采潮位旗標（唯讀）」）。只顯示，不改 qc、不產生 SQL。旗標由多采檢核提列測試專案（`...\4. 委外廠商\0. 新開案\2026多采\檢核提列測試\`）算好寫在本機 `test_db`；整理邏輯參考該專案 `qc_viewer.py`，**複製改寫、不 import**（打包後拿不到）。該專案的旗標表或子項欄位改了，要回頭對 `qcflag_engine.SUB_COLS`、`NAME_ZH`。

- 資料來源：旗標（`tide6_qc`、勾選時 `tide6_qc_dc`）、基準偏移事件 `qc_datum_event`、鄰站推估 `tide6_nearby_est`、判定方式 `qc_test_def` 從旗標庫（`.env` 的 `QC_DB_*`，§2）讀；**觀測、天文潮、舊 qc 用本次查詢的 bundle**（主資料庫），觀測＝`WL_{儀器}`（Q）與 `WL_{儀器}_raw`（非 Q）合併，舊 qc 非 Q＝該時間沒有 Q 值只有非 Q 值；天文潮只有主站的 `WL_{主站}_pred_h`，同址各儀器共用。
- `qcflag_engine.py`（純 DataFrame，不 import Dash／Plotly）：`missing_config()`、`connect()`（`charset=33`、逾時 5 秒）、`load_flags()`、`load_events()`（期間前後各多 1 天）、`load_nearby_est(candidates)`（本儀器 → 同址主儀器 → 其他，取第一台有值的）、`flag_range()`、`stids_with_flags()`、`obs_from_bundle()`、`merge_view()`、`to_hourly()`（> 62 天改逐時，旗標取該小時最嚴重者：4 > 3 > 9 > 2 > 1）、`strip_columns()`（整段都是 2 的子項不畫，多半是此儀器不適用）、`group_events()`（相隔 ≤ 1 天的斷點併成一個事件）、`summary_marks()`（`qc_summary` 3／4 → `buoy_checks` 同格式的標記表）、`code_counts()`。
- `build_qcflag_figure.py`：三列共用時間軸。(1) 水位：觀測、天文潮、鄰站推估、彙總 3／4（空心菱形／空心圈，同浮標自動檢查樣式；點超過 300 個時縮小）、基準偏移事件垂直虛線（只畫同一事件各方法中**最早**的斷點）、颱風海警／陸警底色（`add_warn_bands`）。(2) 檢核結果方格：`go.Heatmap`，第 1 列舊人工 qc 非 Q、第 2 列新規則彙總 `qc_summary`（兩列相鄰方便對照），細線以下是各子項；空白＝通過，未檢測 2 接近底色、缺漏 9 淺灰（明暗關係同多采檢視器）、3 黃、4 紅、舊 qc 藍。(3) 觀測 − 鄰站推估，灰帶＝± 門檻（沒有推估不畫）。**圖例**（2026-11-20 使用者看畫面後改）：每張子圖各一組圖例，放在該子圖標題同一行右側（`legend`＝水位、`legend2`＝差值），點圖例只開關那張子圖的線或點；方格顏色不放圖例（Heatmap 是一個 trace，點圖例關不掉單一顏色），改成方格標題列右側的色塊文字。**線寬與標記大小**集中在檔頭 `WIDTH`、`MARK_SIZE`、`MARK_SIZE_DENSE`、`OUT_SIZE`（使用者嫌線細、標記肥，觀測 1.5→2.2、標記 10→7；比較圖在 `改版截圖_20261120_多采旗標\7_線寬比較.png`）。三列各一條游標載體（`meta='row_hover'`，`apply_row_hover` 會跳過），停在任一列都列出同一時間的觀測、天文潮、推估與差、舊 qc、彙總、非通過子項與基準偏移事件（每個方法一行）；子項有 3／4 而彙總通過時加一行「彙總依例外規則不計」。
- 右側面板（2026-11-20 精簡）：狀態只留彙總 4／3 筆數、逐時提示、無鄰站推估、無 dc 結果；基準偏移事件表預設展開（斷點含年份、參考來源、偵測方式、偏移 mm；tideshift.sn 全空時不列這欄）。以下收合：各項筆數（彙總在第一列，只列不通過／可疑）、彙總規則（`qc_test_def` 的 `qc_summary` 說明，回答「子項不通過、彙總卻通過」）、各檢核項目判定方式、資料來源與限制（不改 qc、資料庫來源差異、整段未檢測而不畫的列）。
- 颱風期間：bridge 只存最後一次查詢的颱風期間；用舊分頁看別的期間時，本頁籤先裁到本次期間，不重疊就不畫（其他頁籤沒有這個處理）。
- **水位大圖疊點**（2026-11-24，§13 設計原則 (5) 的前一半）：QC 工作台 → 水位時序 + QC 右側面板「▸ 自動檢核（試行）」勾選框「疊自動檢核結果：可疑／不通過」，預設不勾。勾了才連旗標庫：`dash_app._load_autoqc_marks()` 對本次查詢每個潮位站的每台儀器呼叫 `qcflag_engine.load_summary_marks()`（只 SELECT `qc_summary IN (3,4)` 的列，逐 6 分鐘、不改逐時），Y 取 bundle 該儀器觀測（Q 與非 Q 合併）；該時間沒有觀測值的點畫不出來，筆數另外列。面板列出各儀器不通過／可疑筆數、沒有檢核結果的儀器。沒設定、連不上、讀取失敗：只在面板顯示提示，水位圖與框選照常，且不快取（下次重勾再試）。只疊彙總；子項看唯讀頁籤。「交人工」的點仍等多采專案的 AI 覆核流程。框選：疊點會和觀測點一起被框到，`generate_sql` 先拿掉（見 §3.3 callback 表）；驗證時框到 57 個疊點，勾選前後 Mode 1、Mode 2 SQL 逐字相同。
- 打包：兩個新模組已加進 `build_ocean_dashboard.bat`、`OceanDashboardV2.spec` 的 `--add-data`；不需新套件（`mysql.connector` 已 collect-all）。

## 4. Tkinter 與 Dash 的資料流

**Tkinter → Dash（單向推送）：**
- `MainApp._do_query()` 查詢到資料後呼叫 `dash_bridge.bridge.set_bundle(key=key, bundles=bundles, ...)`。
- `dcc.Interval` 每 500ms 輪詢 `dash_bridge.get_latest_key()`，`key` 變化時更新 `bundle-key-store`，觸發三個 render callback（render_water / render_buoy / render_diag）。

**獨立分頁（Session Isolation）：**
- 透過 URL 參數：`http://127.0.0.1:{DASH_PORT}/?key={stid}_{timestamp}`。
- 每個瀏覽器分頁「鎖定」在特定查詢結果上，不會被後續查詢強制跳轉，可同時開多個分頁對照不同測站/時段。
- 若 URL 無參數，改用輪詢顯示最新一筆。

**Dash → Tkinter：** 無直接回傳。SQL 由使用者在 Dash 面板手動複製，貼到資料庫工具執行。

**埠號動態分配**：`DASH_PORT = _find_free_port(start=8050)`，從 8050 往後找第一個可用埠。

---

## 5. QC 框選產生 SQL 的機制

```
使用者在 water-graph 上以 Box Select（□）框選區域
    │
    ▼
generate_sql callback（Input 直接綁 water-graph.selectedData，見 §3.3）
    ├─ 接收 selectedData = {points: [...], range: {"x"/"x2"/..., "y"/"y2"/...}}
    ├─ _extract_sel_dict() 轉換格式
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

### stid 解析邏輯

水位 SQL（Mode 1/2）的 stid：`trace-meta-store` 反查框選點的 `curveNumber`，找不到才 fallback `bundles[0].get('stid', '')`。`stid-store` 在 V2 已移除。

浮標 SQL 的 stid：`generate_buoy_sql` 固定取 `buoy_bundles[0].get('stid', '')`（已知限制：多站查詢只對應第一站）。

### 浮標/浮球 Mode 1：QC 旗標更新

Box Select 框選任一 `buoy-graph-*` → `generate_buoy_sql` → `build_buoy_qc_sql()` → `buoy-sql-output`。

### TIME 格式表（wave1、wave、wind、curr）：
```sql
UPDATE {table}
SET qc = '{new_qc}'
WHERE STID = '{stid}'
  AND TIME BETWEEN '{t1}' AND '{t2}'
  [AND Z = '{z}']
  AND qc = '{old_qc}';
```

### DATATIME 格式表（pres1、stemp1）：
```sql
UPDATE {table}
SET qc = '{new_qc}'
WHERE STID = '{stid}'
  AND DATATIME BETWEEN '{d1}' AND '{d2}'
  [AND Z = '{z}']
  AND qc = '{old_qc}';
```

pres1/stemp1 的 DATATIME 欄是整日 00:00:00；`d1`/`d2` 由框選 x 取日期部分換算（`.replace(hour=0, minute=0, second=0)`）。qc 為複合 PK 成員，`AND qc='{old_qc}'` 為必要條件（§6）。多站查詢時 stid 只對應第一個浮標 bundle。


### 潮位站環境 QC（wind Z='6' + stemp6 Z='0'/'-3' + pres6，R14 新增，R16 擴充氣溫/氣壓）

獨立頁籤 `qc-tideenv`，不掛在 `qc-buoy` 底下（Graph ID 會撞名）。

wind（Z='6'）走 TIME 格式，與浮標 wind 同一張表、同一套 SQL 邏輯，直接重用；
繪圖直接重用 `build_wind_figure()`，不另寫函式。

stemp6（Z='0' 海溫／Z='-3' 氣溫）QC 掛在「小時列」（MIN0~MIN9，與 tide6
同構），與 stemp1/pres1「QC 掛在天列」（HR0~HR23）顆粒度不同，不可套用
`hr_format=True`。`build_buoy_qc_sql()` 的 `min_format=True` 參數處理這類表：

```sql
UPDATE stemp6 SET qc = '{new_qc}'
WHERE STID = '{stid}' AND DATATIME BETWEEN '{d1}' AND '{d2}'
  AND Z = '-3' AND qc = '{old_qc}';
```

`pres6` 無 Z 欄，SQL 不帶 `AND Z=...`：

```sql
UPDATE pres6 SET qc = '{new_qc}'
WHERE STID = '{stid}' AND DATATIME BETWEEN '{d1}' AND '{d2}' AND qc = '{old_qc}';
```

`fetch_pres6()` 曾誤寫 HR0~HR23（應為 MIN0~MIN9，與 R14 `fetch_stemp6()`
同類 bug），R16 新增本頁籤時一併修正。`fetch_bundle()` 對 kind=6 站組裝
`wind_z6_df`/`stemp6_z0_df`/`stemp6_zm3_df`/`pres6_df`（後兩項 R16 新增）。

原本「氣溫/氣壓不做 QC」的政策限制已於 R16 取消，詳見 §13。

---

## 6. 資料庫欄位與 QC 查詢慣例

| 資料表 | QC 欄位 | 合格值 | 說明 |
|--------|---------|--------|------|
| `tide6` | `qc`（小寫）| `'Q'`（大寫） | 水位 6min 觀測，mysql.connector 回傳小寫，需 `.upper()` 比對 |
| `tide6ha` | `QC`（大寫）| `'h'`（諧和預報）/ `'a'`（天文潮）（都小寫）| 水位預報 |
| `wind` | `qc`（小寫）| `'Q'`（大寫）| 風速風向，比對時需 `.upper()` |
| `stemp6` | `qc`（小寫） | `'Q'`（大寫）| 6min 溫度，潮位站用，有 QC 拆分邏輯 |
| `stemp1` | `qc`（小寫，複合PK成員）| `'Q'`（大寫）| 浮標 1h 溫度；`qc` 為複合 PK 成員（見下方浮球/浮標通則表），上游資料通常已為 Q，`fetch_bundle()` 目前刻意不做 QC 拆分（設計決策，見上方已覆核說明）|
| `meteo` | 無 | — | 氣象站，直接使用，無 QC 欄位 |
| `pres6` | `qc`（小寫）| `'Q'`（大寫） | 氣壓（潮位站）|
| `pres1` | `qc`（小寫，複合PK成員）| `'Q'`（大寫）| 氣壓（浮標）；R16 前 `fetch_pres1()` 未查詢 qc（誤解為無此欄位），已修正 |
| `curr` | 非必要 | — | 海流，上游已供應品管Q資料 |
| `wave` | 非必要 | — | 波浪，上游已供應品管Q資料 |

> **✅ 已覆核（2026-09-08，DDL 確認）**：`stemp1` 的 `qc CHAR(2) NOT NULL` 確實存在，且是複合主鍵成員：`PRIMARY KEY (STID, YEAR, MONTH, DAY, Z, qc)`。`stemp1` 不是「沒有 QC 欄位」，只是 `fetch_bundle()` 目前刻意選擇不對它做 QC 拆分（上游資料通常已為 'Q'）——這是設計決策，不是資料表缺欄位，過去文件的措辭容易誤導成後者，此處澄清。

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

`build_water_figure.py`、`build_diagnostic_figure.py` 兩處共用同一套色系，異動任一處請同步檢查另一處。（V2 無 `ocean_plot_dash.py`，舊版「三處共用」的描述已不適用）

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
`dash_app.py` Dash UI 字體大小集中定義於 `_FS_SYS_TITLE`/`_FS_SUB_TITLE`/`_FS_PANEL_H`/`_FS_BODY`/`_FS_SMALL`/`_FS_TINY` 常數群（R13 完成）；Plotly 圖表字體另有 `_FS_LEGEND`/`_FS_AXIS_TITLE`/`_FS_AXIS_TICK` 三個整數常數，各 render callback 統一以 `fig.update_layout(legend_font_size=_FS_LEGEND)` 套用。

**c. 版面配置**

- 主內容區為左右兩欄 flex layout：左欄圖表 `flex: 3`；右欄控制面板固定寬度 **360px**（2026-08-19 由 320px 調寬，因為新增第 4 個頁籤後標籤列變得擁擠）。若日後頁籤持續增加、標籤仍嫌擠，可以考慮改成兩排標籤、或把面板寬度改為 vw 相對單位 + minWidth，而不是無限加大固定 px。
- 右側面板目前為 4 個頁籤：水位時序與QC／VdC 散佈圖／暴潮偏差／海洋參數（唯讀）。

**d. 圖表組件規範**

- 範本：統一套用 `plotly_dark`（白底匯出版另外指定 `plotly_white`）
- 交互模式：`hovermode='x unified'` 且 `uirevision=True`（鎖定縮放視圖）
  - ⚠️ `x unified` 的陷阱：某條線在游標時間是空值（NaN）時，Plotly 不顯示「空」，而是改抓該線離游標最近、有值的點，且不註明是別的時間（2026-10-23 成功浮球 08 時 HMAX 空，游標卻顯示 07 時的 1608 cm）。浮標波浪圖已改用「游標載體」解決（§13 2026-11-19 條）；其他圖尚未處理。
- CSS 覆蓋：`assets/custom.css` 強制修正 Dash Dropdown 預設亮色樣式
- Tkinter 主題：`_apply_dark_theme(root)` 套用 `clam` 基底主題，色彩沿用上表 `_C` 字典；2026-08-19 新增 `TNotebook` / `TNotebook.Tab` 樣式，讓測站選單分頁（潮位站／浮標浮球）套用 Dark Navy 配色（未選取：`panel` 底色 + `text_dim` 文字；選取時：`header` 底色 + `accent` 文字，並以 `expand` 做選取放大效果）

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
| 7 | 浮球（環境資料迴圈 Step 4／海洋資料 Step 5 未處理；R7 起浮標感測器區塊有部分支援，僅 wave1_df + stemp1_dfs，詳見 §3.1）|
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
| 6（潮位站） | `pres6` | expand_data × 0.1，限 QC='Q' | freq='6min' |

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

### Y 軸模式切換

`event-surge` 頁籤右側面板提供 `surge-yaxis-mode` 切換：

- **`auto`（動態範圍）**：各子圖依自身資料跨度加 20% 留白，各站 Y 軸獨立，適合觀察各站細部變化。
- **`fixed`（全域統一範圍）**：進入主迴圈前先掃描所有站的觀測、預報、門檻值，計算跨站全域 min/max 加 10% 留白，存入 `_global_y_range` / `_global_y2_range`，統一套用在所有子圖，可跨站直接比較暴潮嚴重程度。

⚠️ `y_axis_mode` 已納入 `_surge_fig_cache` 的 `cache_key`；若日後新增其他影響圖形的參數，同樣需要一起加進 key。

### 颱風資訊 key 正規化

安外/安內颱風表欄位大小寫不同（`WARN1BEG` vs `warnSeaBeg`）。`render_surge` 在進入 `build_surge_interactive_figure()` 前，統一將 `typhoon_info` 轉換為 `warnSeaBeg`/`warnSeaEnd`/`warnLandBeg`/`warnLandEnd` 小寫格式，使繪圖函式只處理單一欄位命名規則。

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

### R8 Dash 整合

**A 系列（診斷圖接通）**
import `build_diagnostic_figure`；新增 `_diag_tab()`（`diag-graph`，無 select2d）；diag-ocean tab 改用 `_diag_tab()`
新增 `render_diag` callback：`tide_bundles = [b for b in bundles if b.get('tide_meta')]` → `build_diagnostic_figure()` → `diag-graph`；純浮標查詢 `no_update`
浮標不傳診斷圖：水位子圖無資料時 Y 軸異常撐高；子圖規劃與浮標資料結構不符；待後續另立浮標診斷面板

**B 系列（浮標 SQL）**
`sql_builders.py` 末尾追加 `build_buoy_qc_sql()`
`_buoy_tab()` 右側面板：新/舊旗標輸入 + `buoy-sql-output` Textarea + 多站限制警告文字
`_BUOY_GRAPH_META dict`；`generate_buoy_sql` callback

**C/D 系列（bug 修正）**
`build_diagnostic_figure.py`：`sids = b.get('src_ids') or {}`；9 處 `sids['x']` → `sids.get('x', 'None')`；暴潮偏差三處加 `'Resi' in df.columns` guard
`render_diag`：`tide_bundles` 過濾
`data_engine.py`：確認 stemp6/pres6 均為 MIN 格式（非 HR）；SQL SELECT 欄位與 `expand_data(freq='6min')` 已修正

**已知限制（§12 範圍）**
`diag-graph` 只顯示潮位站；浮標診斷面板尚未設計（待後續另立）
diag 頁籤無白底 PNG 匯出

---

## 13. 已知限制與待辦事項

### 功能面

| 優先度 | 項目 | 說明 |
|--------|------|------|
| 🔴 高 | Mode 1/2/3 只支援 tide6 | 三個 SQL builder 都硬寫 `tide6`／`mrbank.surge`，不支援 wind / stemp6 / stemp1 等表。浮球/浮標整合會直接撞到這個限制，見下方「浮球/浮標整合」 |
| 🟢 低 | ~~潮位站 wind/stemp6/pres6 QC 框選介面未實作~~（已解決，R14；R16 擴充氣溫/氣壓） | wind（Z='6'）+ stemp6（Z='0'）已於 2026-09-11 實作，詳見 §5。R16 取消氣溫/氣壓政策限制，開放 stemp6（Z='-3'）與 pres6；過程中發現 `fetch_pres6()` 有跟 `fetch_stemp6()` 同類欄位錯誤（誤寫 HR0~HR23），已一併修正。 |
| 🟡 中 | 多測站 bundle 的 STID 切換 | 同時查詢多測站時，QC 面板的 STID 未提供切換 UI，`generate_sql` 靠 `trace-meta-store` 反查框選點所屬測站，只有 `water-graph` 支援 |
| 🟡 中 | PNG 匯出 Y 軸範圍未同步 | 手動調整的 Y 軸範圍或 zoom 縮放均不會反映在匯出的白底 PNG 中；X 軸 zoom 範圍有同步 |
| 🟢 低 | 海洋參數（唯讀）頁籤沒有白底 PNG 匯出 | 見 §12 已知限制 |
| 🟢 低 | 浮標 QC 頁籤沒有白底 PNG 匯出 | 此條為使用者新增。且原先 V1 還沒有設計出浮標的白底圖函式 |
| 🟢 低 | `uirevision` 應為動態版本號 | 目前固定為 `True`，多次 push 新資料後縮放狀態可能不正確重置 |
| 🟢 低 | ~~颱風警報底色在水位大圖、診斷總覽、事件報告都不出現~~（已解決，2026-11-20，未打包） | 原因：(1) `main_app.py` 推資料時寫死 `land_range=None`（V2 改寫時留的 `TODO R6`；V1 `改DASH架構\ocean_plot_dash.py` 有算、沒搬過來）；(2) `fetch_typhoons()` 回傳 `ID`／`CNAME`／`WARN1BEG`…，下游各讀 `id`／`cname`／`warnSeaBeg`…，只有 `render_surge` 自己轉過（§11）→ 事件報告暴潮圖與 PNG 沒有色帶、標題第一行（颱風編號名稱）空白，**Mode 3 暴潮 INSERT SQL 的 ID、CNAME 也是空字串**。修法：新模組 `typhoon_bands.py`（§3.8）；`main_app._do_query()` 用 `normalize_typhoon_info()` 一次補齊統一欄位名（原欄位保留），用 `warn_ranges()` 算海警＋陸警期間（裁到查詢期間內，否則色帶會把 x 軸撐開），經 bridge 的 `land_range`（名稱沿用、內容改為 dict）傳給水位圖、診斷圖、報告水位圖，三者改呼叫 `add_warn_bands()`：海警藍、陸警紅兩層疊（重疊處淡紫），兩行文字放在**海警期間中點**上下對齊（沒有海警才退回陸警中點）、只標在第一列。診斷總覽左欄 x 軸有 rangeslider，`layer='below'` 的色帶會被蓋住（實測），改傳 `layer='above'`。白底暴潮報表圖：標題第一行出現後和海警文字重疊 → 海警／陸警文字改用像素位移（`yshift`）、上邊界 130→160；`land_mid` 在海警欄位為 NULL 時退回陸警中點（原本會 NameError，整張圖畫不出來）。驗證：.160 薔琵（2026-10-20～25，1276、1256、46761F、C6S94）照 main_app 流程推 bridge、直接呼叫 callback 產圖截圖；Mode 3 SQL 確認帶出 `2629L`、`薔琵` |
| 🟢 低 | `DEBUG print` 未清除 | `query_multi_tide_data` 等函式內仍有多行 `print("[DEBUG]...")` |
| 🟢 低 | `bundle-poll` 500ms 效率 | 未來可改以 websocket 推送取代輪詢 |
| 🟢 低 | 浮標診斷面板 | `diag-ocean` 只顯示潮位站；浮標需另立面板（子圖：波浪/風速/海溫/海流）|
| 🟡 中 | 浮標／浮球品管功能改良（2026-11-11 記；波浪自動檢查 2026-11-20 已做、未打包） | 需求與規則在 `...\6. 浮標資料相關\Ocean_plot浮標品管需求.md`。**已做**：波浪自動檢查標記（下一列）。**決定**（2026-11-20 使用者）：浮標、浮球都維持可框選產生 SQL，不為權責開特例（浮標由成大品管是目前政策，未來可能變）。**未做**：需求檔其餘自動檢查（浮球離水、海溫鄰站比、兩支海溫計）、stemp1 一天一個 qc 的寫法；動工前先向使用者說明、確認 |
| 🟢 低 | 浮標波浪自動檢查標記（2026-11-20 新增，未打包） | 浮標 QC 頁籤右側勾選框「顯示波浪自動檢查標記」（預設關）。規則在 `buoy_checks.py`（§3.8），**只標示、不改 qc**：碎波上限（HMAX > 0.78×水深，不通過）、波高 0 但週期 > 0（不通過）、週期跳動（週期 ≥ 前後一小時平均×1.3 且波高 ≥ 2 m，可疑；浮球看 T3、浮標看 TMEAN）。水深由 `fetch_station_depth()` 從站表 `st.location` 文字取（「水深約28公尺」），找不到就不做碎波上限；站表只記目前位置，浮標換點前的歷史資料仍用目前水深。圖上：空心圈＝不通過、空心菱形＝可疑（和紅叉「資料庫 qc 非 Q」用形狀區分），游標多一行「⚠ 自動檢查・…：數值說明」；右側面板列出各站各檢查筆數與時間。浮標 wave 圖另加畫 HMAX（`fetch_wave` 加查 `hmax`）。驗證：.160 浮球 2018 年 H3＝0 但有週期 127 筆；薔琵 10/21 20 時週期跳動 1 筆（與浮標資料夾試算一致）；人工輸入 10/23 09 時未入庫值（HMAX 23.87 m、T3 20.5 s）兩條都標到 |
| 🟢 低 | ~~浮標波浪圖游標誤植、缺列不斷線、沒有週期~~（已解決，2026-11-19） | `build_wave1_figure`（浮球）、`build_wave_figure`（浮標）改為上下兩層共用時間軸：上層波高、下層週期（浮球 T3、TMEAN；浮標尖峰週期 T、TMEAN；資料庫單位 0.1 s，圖上換成 s）。`fetch_wave1` 加查 T3、TMEAN，`fetch_wave` 加查 T。`_hourly_grid()` 把整小時沒有資料列的時段補一列空值，折線在缺列處斷開（原有非整點列保留）。游標改由每格都有點的透明載體 trace（y=0、`_row_hover_trace()`）提供，一次列出同一列的所有欄位，空值寫「無值」、缺列寫「此小時無資料列」；其他 trace 設 `hoverinfo='skip'`。qc 標示「整列」，因 wave1 只有一個 qc，上游單項不通過只會讓該欄變空值。驗證：用 .104 薔琵資料（46761F、C6S94 2026-10-21～25）以無頭瀏覽器模擬游標截圖，含多站合併。尚未打包 exe |
| 🟢 低 | ~~其他圖的 `x unified` 游標誤植~~（已解決，2026-11-20，未打包） | 新模組 `hover_rows.py`（§3.8）在 `dash_app` 各 render callback 回傳前統一套用 `apply_row_hover(fig)`：水位大圖、診斷總覽、浮標 8 張、潮位站環境 4 張、暴潮互動圖、事件報告預覽。規則見該檔檔頭（每個 x 軸一條透明載體，列出各線「剛好這個時間」的值或「無值」；取樣較疏的線標出它自己的時間；自己會顯示時間的稀疏標記照舊）。載體 `mode='lines'`，實測框選不會選到；加在最後，不影響 `trace_meta`。一個子圖超過 30,000 個時間點（6 分鐘資料約 125 天）不加載體，維持 Plotly 原行為。註：開發機 Plotly 6.3（plotly.js 3.1）本身會在借用別的時間時用括號標出時間，.70 上 exe 的 Plotly 版本未確認。驗證：無頭瀏覽器實際移動滑鼠讀游標文字（.160 薔琵資料）、本機啟動 Dash 切各頁籤確認每張圖都有載體且無前端錯誤 |
| 🟢 低 | 框選／套索模式下雙擊不會回到初始範圍（2026-11-20 已處理，未打包） | Plotly 設計：縮放模式雙擊＝重設，框選模式雙擊只清除框選。浮標圖預設框選模式所以「雙擊沒反應」，水位圖切到框選後也一樣。`dash_app._DBLCLICK_RESET_JS`（寫在 `app.index_string`，不需 assets）每秒替頁面上的圖掛 `plotly_deselect` 監聽，框選／套索模式下收到時代按工具列「重設座標軸」。瀏覽器原生 `dblclick` 被 Plotly 拖曳遮罩吃掉，不能用。實測：本機 Dash 水位圖、浮球圖框選模式縮放後雙擊回到初始範圍；雙擊同時仍會清除框選 |
| 🔴 高 | 水位圖框選 SQL 的 STID 是 dict 字串（V2 起，2026-11-20 修正，未打包） | `build_water_figure` 的 `trace_meta` 元素是 `{'stid','field_type'}`，`_resolve_stid` 卻直接 `str()` → Mode 1／2 產生 `WHERE STID = '{'stid': '1275', 'field_type': 'wl'}'`，對不到任何資料（執行也不會改到東西）。改為取 `['stid']`。實測本機 Dash 框選產生 `WHERE STID = '1276'`。浮標 SQL 用的是 `buoy_bundles[0]['stid']`，不受影響 |
| 🟡 中 | 診斷總覽多站時前端出錯、游標讀到別站（V2 起，2026-11-20 修正，未打包） | (1) 每站左欄兩張都開 rangeslider，多站時 plotly.js 3 丟例外（Cannot read 'rangemode'），游標、縮放失效 → 只保留第一張子圖的滑桿（時間軸全連動）。(2) 某站某子圖主 y 軸沒有 trace（例：該站查無水位，暴潮偏差沒畫、副軸氣壓有畫）時 plotly.js 省略該主軸，副軸變整張圖高 → 氣壓線橫穿其他子圖、滑鼠感應區蓋住整欄（使用者截圖裡橫穿的橘線）→ 每個主 y 軸補一條空的隱形 trace（x 要給 ISO 字串時間，numpy datetime64 會被序列化成奈秒整數、軸變數字軸）。(3) 颱風文字改成一行放在子圖標題上方（`add_warn_bands(label_pos='above')`），不擋線 |
| 🟢 低 | ~~浮標 QC、潮位站環境頁籤數值是資料庫原始單位~~（2026-11-20 使用者選換算成物理單位，已改、未打包：`build_buoy_figure._to_unit()` 在 wind／curr／pres1／stemp1 圖函式內乘 0.1，潮位站環境共用同一組函式；.104 欄位註解：wind 0.1 m/s、curr mm/s、pres 0.1 hPa；溫度原始值 274＝27.4 °C） | 風速 VM／VG（0.1 m/s）、海流 V、氣壓 P（0.1 hPa）、溫度 T（0.1 °C）都直接畫原始整數，縱軸卻標 m/s、hPa、°C（例：氣壓 10084「hPa」＝1008.4 hPa、風速 176「m/s」＝17.6 m/s）。診斷總覽有換算（`data_engine` 的 `* 0.1`）。選項：換算成物理單位（框選 SQL 只用時間範圍，不受影響），或縱軸改標「0.1 m/s」等原始單位。海流 V 資料庫單位 mm/s（乘 0.1＝圖上的 cm/s） |
| 🟡 中 | 潮位自動檢核旗標唯讀頁籤＋水位大圖疊點（原名多采潮位旗標；2026-11-20 頁籤、2026-11-24 疊點已做；2026-11-24 已打包、待部署 .70；實作見 §3.9） | **2026-11-20 使用者看過第一版**（在開發機開畫面）後改：各子圖圖例分開放、方格色塊說明不再當圖例（原本點了關不掉方格）、彙總列移到舊人工 qc 下方、線加粗標記縮小、右側面板收合、游標加「彙總依例外規則不計」。改後畫面使用者**尚未再看**。其他圖（QC 工作台、浮標、診斷總覽、事件報告）的線寬要不要一起加粗，待使用者決定。看畫面時發現 1176 2025-07-06 丹娜絲 23:00 尖峰（1810 mm）人工標 A／C、新規則因颱風例外放行：多采 RESULTS「秒資料」節已查明是壞秒值被平均進 6 分鐘值（上游問題），不必改規則。**已做**：診斷總覽「多采潮位旗標（唯讀）」頁籤；旗標庫連線命名定為 `QC_DB_HOST／PORT／USER／PASS／NAME`（§2）；原則 (1)～(4) 已照做，(3) 的「偏移期間」用方格的 `qc_datum`、`qc_site_offset` 列表示（`qc_datum_event` 只有斷點時間、沒有期間）。**已做（2026-11-24，未打包）**：(5) 的前一半——水位大圖疊彙總 3／4 的點（勾選框、預設不勾；§3.9「水位大圖疊點」），同日畫面上的「多采」改稱「自動檢核（試行）」（頁籤改名「潮位自動檢核（唯讀）」，理由見 §3.9）；**使用者尚未看畫面**。**未做**：(5) 的後一半「交人工」的點（定義要等檢核提列測試專案的 AI 覆核流程定案）；「過期（需重跑）」列（要搬多采專案 `stale.py` 的 tideshift 比對，暫不做）；同址佐證差值圖、日平均差值圖（多采檢視器勾選才畫的兩張）。驗證：.104 唯讀資料（高雄 1486／1485 2026-10-15～28 薔琵、基隆 1516／1515 2026-07-15～08-20 與 05-01～09-30）推 bridge、無頭瀏覽器截圖與移動游標；彙總筆數與直接 SQL 計數一致。**原設計原則**（2026-11-20 與使用者討論）： | (1) **相容**：旗標庫另設一組連線（命名待定，例 `QC_DB_*`），沒設定或連不上時頁籤只顯示提示、其他頁籤照常；所有讀取包 try/except，**不因旗標庫不存在而無法啟動或打包**，所以安內 .71 版可以照常打包。(2) **交接格式**沿用 `buoy_checks` 的標記表（TIME、CHECK、LEVEL 3／4、NOTE，區間型另加 TIME_END），畫圖共用同一套樣式（空心圈／菱形，顏色同多采狀態色）。(3) **疊層規則**：整片底色只留給背景狀況（颱風海警／陸警）；基準偏移在主圖只畫開始時間的垂直虛線，各方法的偏移期間畫在主圖下方的細長色帶列，不和颱風底色疊；逐點判斷用標記形狀。(4) 基準偏移：多采的 `qc_datum` 已是綜合判斷（±1 天內兩個不同參考來源都偵測到→4，只有一個→3；事件各方法分存在 `qc_datum_event`），垂直線先用「同一事件各方法中最早的時間」，游標列出各方法時間，看過效果再調。(5) QC 工作台只畫綜合結果與「交人工」的點；各子項細節放唯讀頁籤 |
| 🟢 低 | 打包成品偏大（2026-11-24） | 約 1.6 GB，多為用不到的 torch、polars 等（§14「其他打包注意」）。要縮小時在 spec 加 `excludes`，排除後要實際跑過各頁籤與 PNG 匯出 |
| 🟢 低 | 風向箭頭未實作 | `build_wind_figure` 標注「目前未繪製 DM 欄」；邏輯可參考 `build_diagnostic_figure.py` |
| 🟢 低 | 多站浮標 SQL 只對應第一站 | `generate_buoy_sql` 固定取 `buoy_bundles[0]`；右側面板有警告文字 |
| 🟢 低 | 事件報告 zip 打包同步生成、無進度條 | 12 站可能需數十秒，僅 `dcc.Loading` 轉圈提示；太慢可 R11+ 改背景 callback |
| 🟢 低 | data_engine.load_dotenv_manual() 未被呼叫	| 與 main_app._load_env() 邏輯重複，目前無害的死碼，可考慮移除或統一|
| 🟡 中 | §9-3「回傳 bundle dict」表格未列出 R7 新增欄位	| fetch_bundle() 實際用 **_buoy_dfs 展開回傳 wave1_df/wind_df/curr_df/wave_df/pres1_df/stemp1_dfs，表格漏列；建議下一輪校對 §9 全文時一併補上（這次沒有處理，避免範圍過度擴大）|
| 🟢 低 | stemp1 qc 欄位（已解決） | 已用 DESC/DDL 確認 qc 為複合 PK 成員；fetch_bundle 不拆分是設計決策非缺陷。若未來浮球/浮標工具正式整合進水位頁籤才需要補拆分邏輯 |
| 🟡 中 | 水位圖多站（5+）偶發只顯示部分站 | R12 觀察到：同一 key 快取的 figure 有時只顯示部分站，重新整理可恢復；懷疑 Scattergl WebGL context 數量限制。R16 在浮球/浮標圖的類似現象（相機匯出後折線消失）中，Console 實測確認「Too many active WebGL contexts」，已將 `build_buoy_figure.py` 六函式改為 `go.Scatter` 解決。水位圖本身尚未套用（資料量遠大於浮球子圖，SVG 是否可行需另評估），復現時仍請先截圖 Console 確認 |
| 🟢 低 | ~~多儀器同類 overlay（1H平滑/EWMA/低頻趨勢）混疊~~（已解決，R15） | 原本「進階圖層顯示」勾選框無法區分儀器，勾選後同子圖內多儀器的 1H平滑/EWMA/低頻趨勢線會全部一起顯示，難以單獨比對特定儀器。R15 新增「進階圖層目標儀器」選單（`adv-layer-target`），依 `trace_meta` 的 `stid` 篩選；同時修正原本「換站後勾選框狀態與畫面不同步」的已知限制（`trace-meta-store` 改為 Input，新查詢時自動套用目前勾選狀態）。 |
| 🟢 低 | 浮標/浮球六表＋潮位站環境四表新增「非Q資料紅叉標記」（R16） | `build_buoy_figure.py` 新增 `_non_q_mask()` + 紅色 X marker（沿用 §7 異常值紅叉慣例），涵蓋 wave1/wind/curr/wave/pres1/stemp1 與潮位站環境 wind/stemp6/pres6。 |
| 🟢 低 | ~~pres1 無 QC 欄位~~（已解決，R16） | 舊版 `fetch_pres1()` 未查詢 qc（誤以為無此欄位，DDL 證實有且為 PK 成員）。R16 新增紅叉功能才暴露：`_expand()` 缺 QC 時補 `df['QC']=''`，被誤判非Q，pres1 整條線幾乎被紅叉蓋滿。已補 `qc AS QC` 修正。 |
| 🟢 低 | ~~pres6 欄位誤寫 HR0~HR23~~（已解決，R16） | `fetch_pres6()` 誤植 HR0~HR23（應為 MIN0~MIN9，與 R14 stemp6 同類 bug），因先前無呼叫端使用未被觸發。新增潮位站氣壓 QC 頁籤時修正。 |

### 架構面

- `dash_app.py` 中若還留有 `OceanDataEngine` Stub 類別（骨架階段佔位用），整合後可移除或改 import 真實版本。
- `plotly_qc_select.py`（舊版 HTTP callback server 整套機制）已被 Dash 完全取代，若專案目錄還留著這個檔案可以刪除。
- ~~`draw_diagnostic()` 舊 HTML 路徑與「查看海洋參數」按鈕~~：已對照 `main_app.py` 原始碼確認 V2 從未存在這顆按鈕或舊路徑（V2 是重寫非遷移），此條目移除，不再是待辦。

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

### PNG 匯出：kaleido + choreographer 須同時 collect-all

`fig.to_image()` 靠 kaleido 呼叫內建 Chrome 執行渲染；Chrome 實際上由 `choreographer` 套件提供，存在 `choreographer/cli/browser_exe/chrome-win64/` 目錄下。`--collect-all kaleido` 只打包 kaleido 本身，不會把 choreographer 的目錄結構一併帶入；打包後 kaleido 找不到 bundled Chrome，fallback 使用系統瀏覽器（安內環境為 Edge），Edge 在 exe 執行 context 下無法啟動，導致 zip 批量下載失敗。

**正確做法（兩條都必須）：**

```bat
--collect-all kaleido ^
--collect-all choreographer ^
```

`build_ocean_dashboard.bat` 已包含這兩行。若日後 kaleido 升版後行為改變，先確認 `kaleido.get_chrome_sync()` 回傳的路徑是否仍在 choreographer 目錄下。


### 其他打包注意

- `babel` 語系資料：`frozen` 環境下可能需設定 `BABEL_DATA_PATH`（`tkcalendar` 依賴 babel）；V2 無 `ocean_plot_dash.py`，若確認仍需要這個設定，應加在 `main_app.py` 頂端。**尚未核實 V2 實際是否需要**，列入 R13 打包驗證項目
- `.env` 檔與 `對應站表格.csv` 必須與執行檔放在同一目錄
- Tkinter + Dash 雙執行緒打包後，`use_reloader=False` 是必要的；`debug=False` 在整合執行時也必須關閉
- 不使用 python-dotenv，一律手動解析 `.env`
- 從命令列打包（2026-11-24）：`cmd /c build_ocean_dashboard.bat` 在中文路徑下找不到檔案，改用 `python -m PyInstaller --noconfirm OceanDashboardV2.spec`（內容同 bat；改模組清單時兩邊都要改）。
- 成品偏大（2026-11-24：約 1.6 GB、1 萬多個檔；zip 約 650 MB）：開發機 Python 3.13 裝了很多套件，PyInstaller 順著 pandas 等的選用依賴把 torch（317 MB）、polars、llvmlite、pyarrow、vl_convert、transformers、duckdb、botocore 都帶進來；choreographer 的 Chrome 361 MB 是必要的。可在 spec 加 `excludes` 縮小，但要逐項確認沒用到再排除（正式作業工具，求穩定），列在 §13。

---

## 15. 資料表說明（供 SQL 參考）

| 資料表 | 主要欄位 | 說明 |
|--------|---------|------|
| `tide6` | STID, DATATIME, MIN0~MIN9, QC | 6分鐘潮位原始資料 |
| `tide6ha` | STID, DATATIME, MIN0~MIN9, QC | 6分鐘潮位預報（QC='h' 調和，QC='a' 天文） |
| `st` | STID, stnac, stid_obs, stid_new, type, kind | 測站基本資料（type: 2=音波,3=壓力,4=雷達；kind: 1/2/3=氣象站,6=潮位站,7=浮球,8=浮標）|
| `stitemqc` | stid, qcid | 安內測站對應（MAPPING_SRC=db 時使用）|
| `tidestat` | STID, YEAR, MONTH, MR, SL, QC | 潮位統計（MR=平均潮差，MONTH=0 代表全年，SL='S' AND QC='Q' 篩選有效值）|
| `tideh` | STID, DATATIME, HEIGHT, HORL, QC | 天文潮高低潮預報極值（QC='a' 重建分析，QC='h' 調和預報）|
| `tidehl` | STID, DATATIME, HEIGHT, HORL, QC | 觀測對應高低潮（QC='Q' 品管通過）|
| `med.p_setting_warn` | STID, WARNVAL, STIDE | 大潮警戒值(WARNVAL)/注意值(STIDE)，單位 m，程式端 ×1000 換算 mm |
| `meteo` | stid, DATATIME, min, p, ws, wd, t | 氣象站氣壓/風速/風向/氣溫（min=0 為整點值，單位×0.1 還原）|
| `wind` | STID, TIME, VM, DM, Z, qc | 潮位站/浮標風速（VM×0.1=m/s）、風向（DM）；Z='6' 為潮位站，Z='2'/'3' 為浮標 |
| `pres1` | STID, DATATIME, HR0~HR23 | 浮標逐小時氣壓（HR 欄位，×0.1=hPa）|
| `pres6` | STID, DATATIME, MIN0~MIN9, QC | 潮位站逐6分鐘氣壓（×0.1=hPa，僅用 QC='Q' 資料）|
| `stemp1` | STID, DATATIME, HR0~HR23, Z | 浮標溫度（Z='-3' 氣溫，Z='0' 海溫；×0.1=°C）；資料表有 qc 欄位但預設全部資料都是Q，見 §6 |
| `stemp6` | STID, DATATIME, MIN0~MIN9, Z, QC | 潮位站逐6分鐘溫度（Z='-3' 氣溫，Z='0' 海溫；×0.1=°C；有 QC 分流）|
| `wave` | STID, YEAR, MONTH, DAY, HOUR, H, TMEAN | 浮標波浪（H×0.01=示性波高 m，TMEAN×0.1=平均週期 s）|
| `curr` | STID, TIME, V, D, Z | 浮標海流（V×0.1=流速 cm/s，D=流向，Z='4' 為主要層次）|
| `typhoonid` | id, cname, sponsor, WARN1BEG/END, WARN2BEG/END | 颱風資料（安外欄位小寫 warnSeaBeg，安內大寫 WARN1BEG；用 AS 統一）；`sponsor='LocalTime'` 篩選需加 `COLLATE utf8mb4_general_ci`（上游庫定序較舊，混用會拋 Illegal mix of collations）|

---

*本文件依 main_app.py、data_engine.py、station_registry.py、sql_builders.py、dash_bridge.py、dash_app.py、build_water_figure.py、build_diagnostic_figure.py、build_vdc_figure.py、build_surge_report_figure.py 等檔案原始碼撰寫（V2 無 `ocean_plot_dash.py`）。如有修改請同步更新本文件。*
