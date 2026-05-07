# 海洋動力診斷儀表板 — 操作與協作手冊

> 適用對象：新進操作人員、需要詢問 AI 協助維護的人員  
> 最後更新：2026-05

---

## 一、快速上手

### 1-1 啟動程式

直接執行 `ocean_plot.exe`（打包版）或：

```
C:/Python313/python.exe ocean_plot.py
```

### 1-2 登入

- 輸入資料庫密碼後按「登入系統」或 Enter
- 勾選「切換至 Localhost 測試模式」可連接本機測試資料庫

### 1-3 查看資料

1. 選擇颱風（可略過，直接手動填日期）
2. 設定起始與結束時間
3. 從清單勾選測站（可多選）
4. 點擊按鈕：
   - **🔍 查看水位細節**：單純水位大圖，適合 QC 作業，最多 45 站、365 天
   - **查看海洋參數**：四子圖完整診斷，最多 12 站、45 天

瀏覽器會自動開啟互動式圖表。

---

## 二、圖表操作

| 動作 | 方式 |
|------|------|
| 縮放 | 工具列選 🔍（zoom），滑鼠拖曳框選要放大的區域 |
| 平移 | 工具列選 ✋（pan），拖曳圖表 |
| 重置視圖 | 雙擊滑鼠左鍵 |
| 顯示/隱藏資料線 | 點擊右側圖例項目 |
| **框選 QC 範圍** | 工具列選 □（select），拖曳框選異常區段 |

> 框選後，Tkinter 主視窗會自動彈出 SQL 指令視窗。左鍵連擊兩次可取消框選範圍。

---

## 二-A、統計摘要

點擊主視窗的「**查看統計**」按鈕，可開啟獨立摘要視窗，顯示最近一次查詢結果中各測站的統計數據：

| 參數 | 統計項目 |
|------|---------|
| 水位（cm） | 平均、最高、最低 |
| 風速（m/s） | 平均、最大 |
| 示性波高（m） | 平均、最大 |
| 流速（cm/s） | 平均、最大 |

**注意**：統計功能使用的是上一次「查看海洋參數」或「查看水位細節」所載入的資料。若尚未查詢過資料，點擊「查看統計」會出現提示訊息。

---

## 三、QC 框選操作

### 3-1 事前設定

在主視窗「框選操作設定」區塊選擇模式：

**Mode 1 — 更新 QC 值**
- 用途：把框選範圍內的異常資料改標為指定 QC 碼
- 設定：右側數字為要寫入的新 QC 值（預設 9）

**Mode 2 — MIN 四則運算**
- 用途：對框選到的原始資料值進行加減乘除校正
- 設定：選擇運算符號（+、-、×、÷），輸入運算數值

### 3-2 框選流程

1. 在主視窗確認 Mode 和參數設定
2. 開啟圖表後，點工具列的 **□（select）** 切換至框選模式
3. 在圖表上拖曳選取要修改的資料範圍
4. Tkinter 主視窗會彈出 SQL 視窗（固定顯示在最前方）
5. 確認 SQL 內容後點「📋 複製 SQL」
6. 到資料庫執行工具貼上並執行

> 重複框選會在同一個 SQL 視窗更新，不會開新視窗。  
> SQL 視窗如果被擋住，重新框選一次它就會跳回最前方。

### 3-3 SQL 範例

**Mode 1 產生的 SQL：**
```sql
UPDATE tide6
SET    QC = 9
WHERE  STID     = '1176'
  AND  DATATIME BETWEEN '2025-07-06 00:00:00' AND '2025-07-07 12:00:00'
  AND  (   MIN0 BETWEEN 400.000000 AND 1200.000000
       OR  MIN1 BETWEEN 400.000000 AND 1200.000000
       ...
       OR  MIN9 BETWEEN 400.000000 AND 1200.000000
       );
```

**Mode 2 產生的 SQL（加 0.5 校正）：**
```sql
-- Mode 2：2 筆 DATATIME，共 3 個欄位
UPDATE tide6
SET    MIN2 = MIN2 + 0.5,
       MIN6 = MIN6 + 0.5
WHERE  STID     = '1176'
  AND  DATATIME = '2025-07-06 12:00:00';

UPDATE tide6
SET    MIN0 = MIN0 + 0.5
WHERE  STID     = '1176'
  AND  DATATIME = '2025-07-06 13:00:00';
```

---

## 四、常見問題

**Q：圖表開啟後是空白頁**  
A：通常是網路問題。確認 `plotly_qc_select.py` 的 `write_chart_html()` 裡 `include_plotlyjs=True`（不可為 `"cdn"`，安內無對外網路）。

**Q：框選後沒有跳出 SQL 視窗**  
A：依序確認：
1. 工具列有沒有切換到 □（select）模式
2. 主程式終端機有沒有印出 `[_poll_queue 錯誤]` 的訊息
3. `ocean_plot.py` 頂端有沒有 `import queue`

**Q：SQL 視窗跑到視窗後面找不到**  
A：在圖表上再框選一次，SQL 視窗會自動跳回最前方。

**Q：Mode 2 產生的 SQL 欄位不對**  
A：Mode 2 假設 tide6 的 DATATIME 一律在整點（00分00秒）。如果查詢的資料 DATATIME 不是整點，請改用 Mode 1 或手動校正 SQL。

**Q：打包後執行出現 `mysql.connector` 相關錯誤**  
A：重新打包時加上：
```
--hidden-import mysql.connector.plugins --hidden-import mysql.connector.plugins.mysql_native_password
```

---

## 五、遇到問題時如何詢問 AI

### 5-1 回報 Bug

把以下資訊一起提供給 AI：

```
【Bug 回報】
發生位置：ocean_plot.py / plotly_qc_select.py 的 [函式名稱]
操作步驟：[做了什麼動作]
預期結果：[應該要發生什麼]
實際結果：[實際發生什麼]
錯誤訊息：（貼上終端機完整錯誤訊息，包含 Traceback）
```

**小提醒**：終端機出現的 `UserWarning: pandas only supports SQLAlchemy...` 是正常的，不是 bug，不用特別回報。

### 5-2 提出優化需求

```
【優化需求】
目標功能：[說明你希望新增或改變什麼]
目前做法：[描述現有程式的行為]
期望效果：[你希望改成什麼樣子]
相關程式位置：[如果知道的話，說明在哪個函式]
```

### 5-3 給 AI 看程式碼的方式

- 直接把 `.py` 檔案上傳給 AI，比貼文字更不容易出錯
- 如果只有部分問題，可以只貼相關函式，但要說明函式在哪支檔案
- 說明「舊版這樣、新版那樣」時，如果有截圖請一起提供

### 5-4 修改程式的基本原則

- 每次修改後先在安外環境測試，確認正常再部署安內
- 部署安內前確認 `.env` 的 `MAPPING_SRC=db` 和 `TYPHOON_DB=mrbank` 設定正確
- `include_plotlyjs` 必須是 `True`，不可以改成 `"cdn"`
- 新增功能如果用到新的 Python 模組，記得在檔案頂端加 `import`

---

## 六、環境建置（新機器安裝）

```
pip install pandas numpy plotly mysql-connector-python tkcalendar pyinstaller
```

Python 版本需 3.10 以上（使用了 `str | None` 型別語法）。

打包指令（Windows PowerShell，單行執行）：

```
C:/Python313/python.exe -m PyInstaller --onefile --windowed --hidden-import mysql.connector --hidden-import mysql.connector.plugins --hidden-import mysql.connector.plugins.mysql_native_password --hidden-import tkcalendar --hidden-import babel.numbers ocean_plot.py
```

打包完成後，將 `dist/ocean_plot.exe` 複製到目標機器，同目錄放置 `.env` 檔即可執行。
