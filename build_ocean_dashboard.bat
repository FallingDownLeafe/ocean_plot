@echo off
REM 海洋動力診斷儀表板 V2 -- PyInstaller onedir 打包腳本
REM 請在專案根目錄（跟 main_app.py 同一層）執行本檔案
REM 若你的專案沒有 assets 子資料夾，請刪除下面 assets 那一行 --add-data

python -m PyInstaller --noconfirm --onedir --windowed --name OceanDashboardV2 ^
  --add-data "dash_bridge.py;." ^
  --add-data "station_registry.py;." ^
  --add-data "data_engine.py;." ^
  --add-data "sql_builders.py;." ^
  --add-data "dash_app.py;." ^
  --add-data "build_water_figure.py;." ^
  --add-data "build_buoy_figure.py;." ^
  --add-data "build_diagnostic_figure.py;." ^
  --add-data "build_vdc_figure.py;." ^
  --add-data "build_surge_report_figure.py;." ^
  --add-data "buoy_checks.py;." ^
  --add-data "typhoon_bands.py;." ^
  --add-data "hover_rows.py;." ^
  --add-data "qcflag_engine.py;." ^
  --add-data "line_style.py;." ^
  --add-data "build_qcflag_figure.py;." ^
  --hidden-import dash ^
  --hidden-import dash.dcc ^
  --hidden-import dash.html ^
  --hidden-import dash._callback ^
  --hidden-import dash._dash_renderer ^
  --hidden-import flask ^
  --hidden-import flask_compress ^
  --hidden-import plotly ^
  --hidden-import plotly.graph_objects ^
  --hidden-import plotly.subplots ^
  --hidden-import pandas ^
  --hidden-import numpy ^
  --hidden-import mysql.connector ^
  --hidden-import tkcalendar ^
  --hidden-import babel.numbers ^
  --hidden-import babel.dates ^
  --hidden-import babel.core ^
  --collect-all babel ^
  --collect-all mysql.connector ^
  --collect-all kaleido ^
  --collect-all choreographer ^
  --collect-all tkcalendar ^
  main_app.py

pause
