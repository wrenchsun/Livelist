# -*- mode: python ; coding: utf-8 -*-
# source/ から実行: index.html は一階層上のルートにある

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=[('../index.html', '.')],   # ルートの index.html を _MEIPASS に同梱
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# 通常版（コンソールあり）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Livelist',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)

# バックグラウンド版（コンソールなし）
exe_bg = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LivelistBG',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
