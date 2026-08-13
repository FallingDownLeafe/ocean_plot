"""
demo_surge_report.py
=====================
獨立測試腳本：串接既有 OceanDataEngine + get_tsuwawa_thresholds +
build_surge_report_figure，匯出單站颱風暴潮偏差報表 PNG。

執行前提：
    本檔案與 build_surge_report_figure.py 必須跟 ocean_plot_dash.py、
    plotly_qc_select.py、dash_bridge.py、.env、對應站表格.csv
    放在同一個目錄下（沿用現有專案的 import 與環境設定）。
    building_surge_report_figure.py 不會啟動 Tkinter GUI（有 __main__ 保護），
    只是借用 OceanDataEngine 類別本身。

執行方式：
    python demo_surge_report.py

會互動式詢問 DB 密碼（dps 帳號，純 SELECT 即可，本腳本不寫入資料庫、
不會用到 upsert 用的 mrbank 高權限帳號）。
"""

import datetime
import getpass

from ocean_plot_dash import OceanDataEngine
from build_surge_report_figure import get_tsuwawa_thresholds, build_surge_report_figure

# ============================================================
# 測試參數（先手動指定；跑通後可以改成 command-line 參數或迴圈跑多站）
# ============================================================
# TEST_STID = "1366"          # 淡海音波式，先用你查到的四站之一測試
TEST_STID = "1826"          # 淡海音波式，先用你查到的四站之一測試
TEST_TYPHOON_ID = "2504L"    # 丹娜絲
TEST_YEAR = "2025"

# 海警起 -1 天 ~ 陸警迄 +1 天。
# 依你查到的丹娜絲時間手動先算好：
#   海警起 2025-07-05 08:30 -> -1 天 -> 2025-07-04
#   陸警迄 2025-07-07 11:30 -> +1 天 -> 2025-07-08
# 之後要動態化，可以改成從 typhoon_info 的 warnSeaBeg/warnLandEnd 算。
QUERY_START = datetime.date(2025, 7, 4)
QUERY_END = datetime.date(2025, 7, 8)

OUTPUT_PNG = "surge_report_demo.png"


def main():
    password = getpass.getpass("輸入 dps 密碼: ")
    engine = OceanDataEngine(password=password)

    # 1. 抓颱風資訊（沿用既有的安內/安外欄位切換邏輯）
    typhoons = engine.fetch_typhoons(TEST_YEAR)
    row = typhoons[typhoons['id'] == TEST_TYPHOON_ID]
    if row.empty:
        raise RuntimeError(
            f"查無颱風 {TEST_TYPHOON_ID}（年份 {TEST_YEAR}），"
            f"請確認 TEST_YEAR 或 .env 裡的 TYPHOON_DB 設定"
        )
    typhoon_info = row.iloc[0].to_dict()

    # 2. 抓 bundle（水位、預報、Resi 都在裡面，沿用既有查詢邏輯）
    bundle = engine.fetch_bundle(TEST_STID, QUERY_START, QUERY_END)

    # 3. 抓門檻值（新函式，查 tsuwawa.warn）
    thresholds = get_tsuwawa_thresholds(engine.conn, TEST_STID)
    if thresholds is None:
        print(f"[警告] 測站 {TEST_STID} 查無 tsuwawa 門檻值，圖上將顯示查無資料提示")

    # 4. 組圖並匯出 PNG（kaleido）
    fig = build_surge_report_figure(bundle, typhoon_info, thresholds)
    fig.write_image(OUTPUT_PNG, width=1400, height=500, scale=2)
    print(f"已輸出：{OUTPUT_PNG}")


if __name__ == "__main__":
    main()
