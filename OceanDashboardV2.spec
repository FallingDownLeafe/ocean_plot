# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('dash_bridge.py', '.'), ('station_registry.py', '.'), ('data_engine.py', '.'), ('sql_builders.py', '.'), ('dash_app.py', '.'), ('build_water_figure.py', '.'), ('build_buoy_figure.py', '.'), ('build_diagnostic_figure.py', '.'), ('build_vdc_figure.py', '.'), ('build_surge_report_figure.py', '.'), ('buoy_checks.py', '.'), ('typhoon_bands.py', '.'), ('hover_rows.py', '.'), ('qcflag_engine.py', '.'), ('build_qcflag_figure.py', '.'), ('line_style.py', '.')]
binaries = []
hiddenimports = ['dash', 'dash.dcc', 'dash.html', 'dash._callback', 'dash._dash_renderer', 'flask', 'flask_compress', 'plotly', 'plotly.graph_objects', 'plotly.subplots', 'pandas', 'numpy', 'mysql.connector', 'tkcalendar', 'babel.numbers', 'babel.dates', 'babel.core']
tmp_ret = collect_all('babel')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('mysql.connector')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('kaleido')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('choreographer')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tkcalendar')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OceanDashboardV2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OceanDashboardV2',
)
