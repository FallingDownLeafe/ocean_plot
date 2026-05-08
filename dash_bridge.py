"""
dash_bridge.py
==============
Tkinter 主執行緒 → Dash (Flask) 工作執行緒 的資料快取橋接層。

職責範圍
--------
只負責「存」與「取」bundle dict；不含任何繪圖、layout 或 callback 邏輯。

執行緒安全
----------
Tkinter 主執行緒呼叫 set_bundle()，Dash callback 執行緒呼叫 get_bundle()。
兩者在同一個 process 內共用同一個模組實例，以 threading.Lock 防止 race condition。

使用方式
--------
Tkinter 端（MainApp.go() 查詢完成後）：
    import dash_bridge
    dash_bridge.set_bundle("main", bundle)          # bundle = fetch_bundle() 回傳值

Dash 端（callback 內）：
    import dash_bridge
    bundle = dash_bridge.get_bundle("main")         # None 表示尚無資料

dcc.Store 宣告位置說明
----------------------
在 dash_app.py 的 app.layout 最頂層（與其他 dcc.Store 並列）加入：

    dcc.Store(id="bundle-key-store", data=None),

當 Tkinter 呼叫 set_bundle() 後，同時更新此 Store 的值（例如寫入 key 字串或
遞增版本號），以觸發依賴它的 Dash callback，讓 callback 知道快取已有新資料
可以讀取。實際的觸發機制（dcc.Interval 輪詢 or 直接寫 Store）由 dash_app.py 決定；
本模組只提供存取快取的介面，不耦合任何 Dash 元件。
"""

import threading
from typing import Any

# ── 全域快取與鎖 ──────────────────────────────────────────────────────────────
_bundle_cache: dict[str, Any] = {}
_lock = threading.Lock()
_latest_key: str | None = None
_land_range = None


def set_bundle(key: str, bundle: Any, land_range=None) -> None:
    """
    將 bundle 存入快取。

    Parameters
    ----------
    key    : 識別此筆資料的字串，例如 "main" 或測站代碼 "1176"。
    bundle : OceanDataEngine.fetch_bundle() 的回傳 dict（或任何可序列化物件）。
    """
    global _latest_key, _land_range          # ← 必須在 with 區塊外面
    with _lock:
        _bundle_cache[key] = bundle
        _latest_key = key
        _land_range = land_range

def get_bundle(key: str) -> Any:
    """
    從快取取出 bundle。

    Parameters
    ----------
    key : 與 set_bundle() 呼叫時相同的字串。

    Returns
    -------
    bundle dict，若 key 不存在則回傳 None。
    """
    with _lock:
        return _bundle_cache.get(key)

def get_latest_key() -> str | None:
    with _lock:
        return _latest_key
    

def get_land_range():
    with _lock:
        return _land_range