# 海洋動力診斷儀表板 — 系統技術文件

> 維護對象：有程式背景的維運人員、AI 協作  
> 最後更新：2026-05

---

## 一、系統概述

本系統為海洋測站潮位資料的視覺化診斷與 QC 編修工具。  
使用者可透過 Tkinter GUI 選擇測站與時間範圍，於瀏覽器互動式圖表中檢視潮位資料，並以滑鼠框選異常區段自動產生 UPDATE SQL 指令。

**程式語言**：Python 3.13  
**主要套件**：pandas、numpy、plotly、mysql-connector-python、tkinter、tkcalendar

---

## 二、檔案結構

```
專案目錄/
├── ocean_plot.py          # 主程式（GUI、資料查詢、繪圖）
├── plotly_qc_select.py    # QC 框選模組（HTTP server、SQL 產生、SQL 視窗）
├── 對應站表格.csv          # 安外用測站對應表（安內改從 DB 讀取）
└── .env                   # 環境變數（DB_IP、DB_USER、MAPPING_SRC、TYPHOON_DB）
```

### `.env` 可設定的變數

| 變數 | 安外預設值 | 安內設定值 | 說明 |
|------|-----------|-----------|------|
| `DB_IP` | `61.56.13.160` | 實際 IP | 資料庫主機 |
| `DB_USER` | `dps` | 實際帳號 | 資料庫帳號 |
| `MAPPING_SRC` | `csv` | `db` | 測站對應來源 |
| `TYPHOON_DB` | `med_data` | `mrbank` | 颱風資料庫名稱 |

---

## 三、模組架構

### 3-1 `ocean_plot.py`

```
OceanDataEngine          # 資料層
  ├── __init__()         # 建立 DB 連線、讀取 .env 設定、呼叫 load_mapping()
  ├── load_mapping()     # 載入測站對應（CSV 或 DB），建立 name_map
  ├── fetch_years()      # 查颱風年份清單（供 GUI 下拉選單）
  ├── fetch_typhoons()   # 查指定年份的颱風資料（名稱、警報時間）
  ├── expand_data()      # 將 MIN0~MIN9（或 HR0~HR23）欄位展開為時序列
  ├── fetch_tide_instruments()    # 查詢同地點所有水位儀器（音波/壓力/雷達）
  ├── query_multi_tide_data()     # 批量查詢多儀器 tide6/tide6ha，QC 分流展開
  └── fetch_bundle()     # 查詢單一測站完整資料，組裝並回傳 bundle dict

draw_diagnostic()        # 繪製四子圖海洋參數大圖（每次最多 3 站一頁）
draw_water_only()        # 繪製水位細節單圖（多站疊圖，QC 框選主要使用此圖）

LoginWin                 # 登入視窗
MainApp                  # 主控 GUI
  ├── build_ui()         # 建立 UI 元件
  ├── go()               # 執行查詢與繪圖，記錄 _last_stid
  ├── _poll_queue()      # 每 200ms 輪詢 QC 框選 queue
  ├── _on_selection()    # 收到框選資料，產生 SQL 並顯示視窗
  └── show_stats()       # 顯示已載入 bundles 的統計摘要視窗
```

#### OceanDataEngine 完整職責與方法說明

**職責**：所有 MySQL 資料存取、展開、合併皆集中在此類別。上層繪圖函式與 GUI 只消費 `fetch_bundle()` 回傳的 bundle dict，不直接碰資料庫。

| 方法 | 輸入 | 輸出 | 說明 |
|------|------|------|------|
| `__init__(password, host, user, database, tables)` | DB 連線參數 | — | 讀取 `.env`、建立 `self.conn`、呼叫 `load_mapping()` |
| `load_mapping()` | — | `self.mapping_df`（STID/QCID 對應）、`self.name_map`（STID→站名） | 依 `MAPPING_SRC` 切換 CSV 或 `stitemqc` DB 來源 |
| `fetch_years()` | — | `list[str]`（年份清單，如 `["2025","2024",…]`） | 查 `typhoonid` 取近年有颱風資料的年份 |
| `fetch_typhoons(yr_full)` | 四位數年份字串 | `DataFrame`（id, cname, warnSeaBeg/End, warnLandBeg/End） | 依安內/安外環境差異處理欄位名稱大小寫 |
| `expand_data(df, val_name, freq)` | 寬表 df、目標欄位名、頻率（'6min' 或 '1h'） | 長表 `DataFrame`（Time, val_name, QC?） | 詳見 §5-2 |
| `fetch_tide_instruments(stid)` | STID 字串 | `DataFrame`（STID, stid_obs, stid_new, type, stnac, is_primary） | 找 stid_obs → 查同地點所有儀器 |
| `query_multi_tide_data(tide_instruments_df, start, end, start_str, end_str)` | 儀器清單 DataFrame、時間範圍 | `dict`（wl_data, pred_data, pred_data_a, tide_meta） | 批量查詢 tide6/tide6ha，QC 分流展開；詳見 §5-3 |
| `fetch_bundle(stid, start, end)` | STID 字串、start/end `date` 物件 | bundle `dict`（stid, stname, df, src_ids, src_names, tide_meta, mr_full, mr_month） | 整合所有資料查詢與計算；詳見 §5-4 |

**典型呼叫順序（`MainApp.go()` 內部）：**
```
OceanDataEngine.__init__()          ← 登入時執行一次
    └── load_mapping()

MainApp.go(mode)                     ← 每次點擊查詢按鈕
    └── fetch_bundle(stid, start, end)
          ├── fetch_tide_instruments(stid)
          └── query_multi_tide_data(...)
                └── expand_data() × N

    → draw_water_only(bundles) 或 draw_diagnostic(bundles)
          └── write_chart_html(fig) → webbrowser.open()
```

### 3-2 `plotly_qc_select.py`

```
_SelectionHandler        # HTTP callback server（接收瀏覽器 POST）
_selection_queue         # thread-safe queue（HTTP thread → Tkinter thread）

write_chart_html()       # 輸出 HTML，注入框選監聽 JS
_build_js()              # 產生注入的 JavaScript 片段

build_mode1_sql()        # 產生 QC UPDATE SQL（OR 條件覆蓋所有 MIN 欄）
build_mode2_sql_by_time()# 產生 MIN 值四則運算 SQL（從 Time 反推 DATATIME+欄位）

SqlDialog                # SQL 顯示視窗（topmost，重複框選原地更新）
OceanQcApp               # 獨立測試用 GUI（不整合進 ocean_plot.py 使用）
```

---

## 四、跨 thread 通訊架構

```
瀏覽器 plotly_selected 事件
    │
    │ fetch POST JSON（框選的 x/y 範圍與點資料）
    ▼
_SelectionHandler（daemon thread，port 18765）
    │
    │ queue.put(data)
    ▼
_selection_queue（queue.Queue，thread-safe）
    │
    │ root.after(200ms) 輪詢
    ▼
MainApp._poll_queue()（Tkinter 主 thread）
    │
    ▼
_on_selection() → SqlDialog.update_content()
```

**為什麼用輪詢**：Tkinter 要求所有 UI 操作必須在主 thread 執行。HTTP server 在 daemon thread 裡，不能直接呼叫 Tkinter。`queue.Queue` 是 thread-safe 的資料橋樑，`root.after()` 讓主 thread 定時來收資料。

---

## 五、資料管線詳細設計

### 5-1 整體資料流

```
使用者點擊「查看水位」/ 「查看海洋參數」
    │
    ▼
MainApp.go()
    │ 讀取 UI 選擇的 STID 清單、時間範圍
    │ 記錄 self._last_stid（供 QC 框選使用）
    ▼
對每個 STID 呼叫 OceanDataEngine.fetch_bundle(stid, start, end)
    │
    ├── fetch_tide_instruments(stid)       → 查 st 表，取同地點所有水位儀器
    ├── query_multi_tide_data(...)         → 批量查詢各儀器的 tide6、tide6ha
    │     └── expand_data() × N           → MIN0~MIN9 展開為 6min 時序
    ├── 查詢氣象（wind）、氣溫（airtemp/stemp）
    ├── 查詢氣壓（pres1）
    ├── 查詢波浪（wavedata）
    ├── 查詢流速（curr）
    └── 計算 Resi（暴潮偏差）、Resi_Norm（正規化偏差）、低頻濾波線
    │
    ▼
bundle dict（含 df、stid、stname、src_ids、tide_meta 等）
    │
    ├── draw_water_only(bundles)           → 水位細節大圖
    └── draw_diagnostic(bundles)          → 四子圖海洋參數圖
          │
          └── write_chart_html(fig)       → HTML 嵌入 Plotly.js → 瀏覽器開啟
```

---

### 5-2 `expand_data()` — MIN0~MIN9 展開邏輯

tide6 原始資料格式：每列一個 DATATIME（整點），MIN0~MIN9 代表該小時內每 6 分鐘的觀測值。

```
原始一列：STID='1176', DATATIME='2025-07-06 00:00:00', MIN0=950, MIN1=940, ...

展開後：
  Time = 2025-07-06 00:00:00 → WL = 950   （MIN0，偏移 0 分鐘）
  Time = 2025-07-06 00:06:00 → WL = 940   （MIN1，偏移 6 分鐘）
  ...
  Time = 2025-07-06 00:54:00 → WL = xxx   （MIN9，偏移 54 分鐘）
```

實作：`pandas.melt()` 將寬表轉長表，再用 `pd.to_timedelta(N * step)` 計算偏移。`freq='6min'` 時 prefix 為 `MIN`，`freq='1h'` 時 prefix 為 `HR`（氣壓資料用）。

**完整執行流程（對應原始碼）：**

```
1. 判斷 prefix：freq='6min' → 'MIN'；freq='1h' → 'HR'
2. val_cols：df.columns 中以 prefix 開頭的欄位（MIN0~MIN9 或 HR0~HR23）
3. id_cols：所有非 val_cols 且非 LASTUPDATETIME 的欄位（包含 DATATIME、STID、QC 等）
4. pandas.melt(id_vars=id_cols, value_vars=val_cols)
   → 寬表轉長表，新增 'idx' 欄位（值為 "MIN0"、"MIN1"… 等字串）
5. 計算 Time：
   step = 6（6min）或 60（1h）
   Time = pd.to_datetime(DATATIME) + pd.to_timedelta(int(idx[-digits]) * step, unit='m')
   再 .dt.floor('min') 對齊分鐘（去除秒數誤差）
6. 回傳欄位：
   - 如果原始 df 有 QC 欄位 → 回傳 [Time, val_name, QC]
   - 否則 → 回傳 [Time, val_name]
7. dropna(subset=[val_name])  → 移除 MIN 欄位為 NULL 的列
8. drop_duplicates(subset=['Time'])  → 保險用，正常情況同一 QC 群組內不應有重複 Time
9. sort_values('Time')  → 確保時序正確
```

**呼叫前提**：呼叫此函式前，呼叫端應已依 QC 分組（例如 `df[df['QC']=='Q']`），避免同一 Time 存在多筆不同 QC 的資料，確保 `drop_duplicates` 只作保險而非修正錯誤。

---

### 5-3 `query_multi_tide_data()` — QC 分流設計

查詢 tide6 後**不篩選 QC**，而是在 Python 端將同一測站的資料拆為兩份分別展開：

```python
df_q   = df_obs[df_obs['QC'].str.upper() == 'Q']   # 校正值
df_bad = df_obs[df_obs['QC'].str.upper() != 'Q']   # 原始機測值（異常）
```

兩份各自 `expand_data()` 後以 Time 為鍵 `outer merge`，欄位命名規則：

| 欄位 | 內容 |
|------|------|
| `WL_{stid}` | QC=Q 的校正水位值 |
| `QC_{stid}` | 對應的 QC 代碼 |
| `WL_{stid}_raw` | QC≠Q 的原始機測值 |
| `QC_{stid}_raw` | 原始值的 QC 代碼（繪圖時顯示於 hover） |

這樣同一時間點可同時保留校正值（藍線）與異常原始值（紅叉），不會互相覆蓋。

**回傳 dict 結構：**

```python
{
    'wl_data':    {STID: expanded_df, ...},          # 觀測值（含 QC 分流並排）
    'pred_data':  {STID: expanded_df, ...},          # 諧和預報 QC='h'（僅主測站）
    'pred_data_a':{STID: expanded_df, ...},          # 天文潮預報 QC='a'（僅主測站）
    'tide_meta':  {STID: {type, type_desc, stnac, is_primary}, ...}
}
```

**tide_meta 欄位說明：**

| 鍵 | 說明 |
|----|------|
| `type` | 儀器類型代碼（2=音波式, 3=壓力式, 4=雷達式） |
| `type_desc` | 文字描述（供圖例顯示） |
| `stnac` | 儀器站名 |
| `is_primary` | 1=主測站（stid_new == stid_obs），0=備用儀器 |

---

### 5-4 `fetch_bundle()` — 完整資料查詢流程

**函式簽名**：`fetch_bundle(stid: str, start: date, end: date) → dict`

---

#### Step 1：建立候選站名單（candidates）

從 `mapping_df` 取出主站的 QCID 候選清單，以 QCID 優先、自身墊底的順序組成 candidates：

```python
candidates = q_ids + [stid]  # 去重保留順序
```

同時一次性查詢所有 candidates 的 `st.kind`，建立 `kind_map`，避免後續迴圈重複查詢。

| kind 值 | 站種 |
|---------|------|
| 1/2/3 | 氣象站 |
| 7 | 潮位站 |
| 8 | 浮標 |

---

#### Step 2：多儀器水位查詢（新系統）/ 單儀器降級（舊系統）

呼叫 `fetch_tide_instruments(stid)` 取得同地點所有儀器清單。

**新系統（有 stid_obs 欄位）：**

呼叫 `query_multi_tide_data()` 批量查詢 tide6 / tide6ha，QC 分流展開後 outer merge 組成 `main`：

```
main = Time 基準（第一個儀器）
     outer merge WL_{stid_A}, QC_{stid_A}, WL_{stid_A}_raw, QC_{stid_A}_raw
     outer merge WL_{stid_B}, ...
     outer merge WL_{primary}_pred_h  （諧和預報）
     outer merge WL_{primary}_pred_a  （天文潮預報）
```

**舊系統降級模式（fetch_tide_instruments 回傳空）：**

直接查單一測站 tide6（無 QC 篩選）與 tide6ha（QC='h'），各自 expand_data 後 outer merge：

```
main = merge(obs[Time, Obs], pre[Time, Pre])
Resi = Obs - Pre
```

---

#### Step 3：計算衍生欄位

在 main 上計算以下欄位（僅主測站）：

| 欄位 | 計算方式 |
|------|---------|
| `Resi` | `WL_{primary}` − `WL_{primary}_pred_h` |
| `WL_{primary}_lf` | 25h 中心移動平均（window=250點，min_periods=125） |
| `WL_{primary}_ewma` | EWMA（alpha=0.05，ignore_na=True）|
| `Resi_Norm` | `Resi / mr_mm × 100`（%），需先查 `tidestat` 取 MR |
| `Diff_{primary}_{other}` | 主儀器與各備用儀器的差值（兩者皆有值才計算）|

MR 查詢優先順序：全年 MR（`MONTH=0`）→ 當月 MR；查詢條件 `tidestat WHERE STID='{stid}' AND YEAR={year} AND SL='S' AND QC='Q'`。

---

#### Step 4：環境資料迴圈（candidates 依序查詢）

對每個 cid 依 kind 查詢氣壓、風、氣溫（先到先得，有資料即停止對該參數的查詢）：

**A. 氣壓 (P)**

| kind | 資料表 | 欄位 | 備註 |
|------|--------|------|------|
| 1/2/3（氣象站） | `meteo` | `p×0.1 as P` | `min=0` 篩選整點 |
| 8（浮標） | `pres1` | expand_data(HR0~HR23) × 0.1 | freq='1h' |
| 7（潮位站） | `pres6` | expand_data × 0.1，限 QC='Q' | freq='1h' |

**B. 風速/風向 (WS/WD)**

| kind | 資料表 | 欄位 | 備註 |
|------|--------|------|------|
| 1/2/3（氣象站） | `meteo` | `ws×0.1 as WS, wd as WD` | `min=0` 篩選整點 |
| 7（潮位站） | `wind` | `VM×0.1 as WS, DM as WD`，Z='6' | QC 分流為 WS/WS_raw |
| 8（浮標） | `wind` | `VM×0.1 as WS, DM as WD`，Z IN ('2','3') | Z 小者優先 |

**C. 氣溫 (AT)**

| kind | 資料表 | 欄位 | 備註 |
|------|--------|------|------|
| 1/2/3（氣象站） | `meteo` | `t×0.1 as AT` | `min=0` 篩選整點 |
| 7（潮位站） | `stemp6` | expand_data，Z='-3'，× 0.1 | QC 分流為 AT/AT_raw |
| 8（浮標） | `stemp1` | expand_data，Z='-3'，× 0.1 | freq='1h' |

**D. 海溫 (WT)**（同迴圈，各種 kind 的邏輯類似氣溫）

| kind | 資料表 | 欄位 | 備註 |
|------|--------|------|------|
| 8（浮標） | `stemp1` | Z='0'，× 0.1 | freq='1h' |
| 7（潮位站） | `stemp6` | Z='0'，× 0.1 | QC 分流為 WT/WT_raw |

三項（P, W, AT）皆有資料後提早 break，WT 有資料即停止。

---

#### Step 5：海洋資料（浮標專用）

從 candidates 中取 kind=8 的第一個浮標（`b_id`）：

**A. 波浪 (H_m / T_sec)**

```sql
SELECT YEAR, MONTH, DAY, HOUR, H, TMEAN FROM wave WHERE STID='{b_id}' AND YEAR IN (start.year, end.year)
```
組合時間欄位 → `H × 0.01 as H_m`，`TMEAN × 0.1 as T_sec`，Python 端切割時間範圍（處理跨年）。

**B. 海流 (V / DIR)**

```sql
SELECT TIME as Time, (V*0.1) as V, D as DIR FROM curr WHERE STID='{b_id}' AND Z='4' AND TIME BETWEEN ...
```

---

#### Step 6：最終合併與輸出

以 `outer join` 將 p_data, w_data, at_data, wt_data, wv_data, cu_data 逐一 merge 進 main，接著：

- 切除 outer join 產生的頭尾超出時間範圍的列
- 異常值過濾：AT/WT > 40°C 或 ≤ 10°C 設為 NaN
- 查詢所有 src_ids 對應站名，組成 `src_names` dict

**回傳 bundle dict 欄位一覽：**

| 鍵 | 說明 |
|----|------|
| `stid` | 主站代碼 |
| `stname` | 主站名稱 |
| `df` | 合併完成的完整時序 DataFrame |
| `src_ids` | 各資料類型的實際來源站代碼（`{p, w, wv, wt}`） |
| `src_names` | src_ids 對應的站名（供圖例顯示） |
| `tide_meta` | 水位儀器元數據（type, type_desc, stnac, is_primary） |
| `mr_full` | 全年平均潮差（可為 None） |
| `mr_month` | 當月平均潮差（可為 None） |

---

**重要**：`include_plotlyjs=True` 將 Plotly.js 直接嵌入 HTML（約 3MB）。安內環境無對外網路，不可改為 `"cdn"`。

---

## 六、折線色系定義（現行版本）

> 附圖「色系定義_微調前」與現行程式差異：氣溫由 `#d62728` 改為 `#ee7373`（較柔和的珊瑚紅）；EWMA 趨勢線為新增。

### draw_diagnostic() 色系

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

---

## 七、SQL 產生邏輯

### Mode 1：更新 QC

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

### Mode 2：更新 MIN 值（四則運算）

**`ocean_plot.py` 整合版使用 `build_mode2_sql_by_time()`**（不依賴 traceName）：

從選中點的 x 值（展開後的 Time）反推 DATATIME 和 MIN 欄位：

```
Time = DATATIME + N × 6min
→ N = Time.minute // 6
→ MIN 欄位 = MIN{N}
→ DATATIME = Time.replace(minute=0)
```

每個 DATATIME 產生一條 UPDATE，同一 DATATIME 有多個 MIN 被框到則合併為一條：

```sql
UPDATE tide6
SET    MIN2 = MIN2 + 0.5,
       MIN6 = MIN6 + 0.5
WHERE  STID     = '{stid}'
  AND  DATATIME = '2024-03-01 00:00:00';
```

**`build_mode2_sql()`**（Demo 版，依賴 trace name 必須為 MIN0~MIN9）：僅供獨立測試 `OceanQcApp` 使用。`ocean_plot.py` 的圖表 trace name 通常為測站名稱，不符合此假設，因此整合時不使用此版本。

---

## 八、打包指令（Windows PowerShell）

```
C:/Python313/python.exe -m PyInstaller --onefile --windowed --hidden-import mysql.connector --hidden-import mysql.connector.plugins --hidden-import mysql.connector.plugins.mysql_native_password --hidden-import tkcalendar --hidden-import babel.numbers ocean_plot.py
```

**注意事項**：
- `plotly_qc_select.py` 因頂端有 import，PyInstaller 會自動偵測，不需要 `--hidden-import`
- 安內部署不需要打包 `對應站表格.csv`（`MAPPING_SRC=db` 時不讀取 CSV）
- 安外部署需要將 `對應站表格.csv` 放在執行檔同目錄
- `.env` 不打包，由各環境自行放置在執行檔旁邊

---

## 九、已知限制與注意事項

| 項目 | 說明 |
|------|------|
| Mode 2 反推假設 | 假設 tide6 的 DATATIME 一律為整點（HH:00:00）。若資料庫有非整點的 DATATIME，反推結果會有偏差 |
| 框選 JS 監聽 | 使用 `plotly_selected` 事件，需用工具列的 □ 按鈕切換至框選模式（預設為 zoom）|
| `draw_diagnostic` 框選按鈕 | `remove_buttons` 清單不可包含 `select2d`，否則框選功能在診斷圖中無法使用 |
| Scattergl 框選 | `go.Scattergl` 在部分環境下框選事件回傳點數可能為 0，改用 `go.Scatter` 可解決但效能較低 |
| 安內網路 | `include_plotlyjs` 必須為 `True`，不可為 `"cdn"` |

---

## 十、資料表說明（供 SQL 參考）

| 資料表 | 主要欄位 | 說明 |
|--------|---------|------|
| `tide6` | STID, DATATIME, MIN0~MIN9, QC | 6分鐘潮位原始資料 |
| `tide6ha` | STID, DATATIME, MIN0~MIN9, QC | 潮位預報（QC='h' 諧和，QC='a' 天文） |
| `st` | STID, stnac, stid_obs, stid_new, type, kind | 測站基本資料（type: 2=音波,3=壓力,4=雷達；kind: 1/2/3=氣象站,7=潮位站,8=浮標）|
| `stitemqc` | stid, qcid | 安內測站對應（MAPPING_SRC=db 時使用）|
| `tidestat` | STID, YEAR, MONTH, MR, SL, QC | 潮位統計（MR=平均潮差，MONTH=0 代表全年，SL='S' AND QC='Q' 篩選有效值）|
| `meteo` | stid, DATATIME, min, p, ws, wd, t | 氣象站氣壓/風速/風向/氣溫（min=0 為整點值，單位×0.1 還原）|
| `wind` | STID, TIME, VM, DM, Z, qc | 潮位站/浮標風速（VM×0.1=m/s）、風向（DM）；Z='6' 為潮位站，Z='2'/'3' 為浮標 |
| `pres1` | STID, DATATIME, HR0~HR23 | 浮標逐小時氣壓（HR 欄位，×0.1=hPa）|
| `pres6` | STID, DATATIME, HR0~HR23, QC | 潮位站逐小時氣壓（×0.1=hPa，僅用 QC='Q' 資料）|
| `stemp1` | STID, DATATIME, HR0~HR23, Z | 浮標溫度（Z='-3' 氣溫，Z='0' 海溫；×0.1=°C）|
| `stemp6` | STID, DATATIME, HR0~HR23, Z, QC | 潮位站溫度（Z='-3' 氣溫，Z='0' 海溫；×0.1=°C；有 QC 分流）|
| `wave` | STID, YEAR, MONTH, DAY, HOUR, H, TMEAN | 浮標波浪（H×0.01=示性波高 m，TMEAN×0.1=平均週期 s）|
| `curr` | STID, TIME, V, D, Z | 浮標海流（V×0.1=流速 cm/s，D=流向，Z='4' 為主要層次）|
| `typhoonid` | id, cname, sponsor, WARN1BEG/END, WARN2BEG/END | 颱風資料（安外欄位小寫 warnSeaBeg，安內大寫 WARN1BEG；用 AS 統一）|
