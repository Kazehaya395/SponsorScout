# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['greenlet', 'pyee']
datas += collect_data_files('sponsorscout')
hiddenimports += collect_submodules('sponsorscout')
hiddenimports += collect_submodules('PySide6')
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['sponsorscout/main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pandas', 'PIL', 'bs4', 'lxml', 'tkinter', 'pytest', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2', 'PySide6.QtQuickTest', 'PySide6.QtQml', 'PySide6.QtQmlFeatures', 'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtSql', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtUiTools', 'PySide6.QtXml', 'PySide6.QtTest', 'PySide6.QtConcurrent'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SponsorScout',
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
    name='SponsorScout',
)
