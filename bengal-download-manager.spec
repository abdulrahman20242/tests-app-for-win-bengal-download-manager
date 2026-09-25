# -*- mode: python ; coding: utf-8 -*-
import platform
from PyInstaller.utils.hooks import collect_all

_is_windows = platform.system() == "Windows"

datas = [('assets', 'assets')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('core')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('ui')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['src/main.py'],
    pathex=['src'],
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
    a.binaries,
    a.datas,
    [],
    name='bengal-download-manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX-compressed PyInstaller executables are a well-known source of Windows
    # Defender/SmartScreen and third-party AV false positives; skip UPX on Windows
    # only. Linux/macOS behavior is unchanged.
    upx=not _is_windows,
    upx_exclude=[],
    runtime_tmpdir=None,
    # A windowed GUI app should not carry a visible console on Windows. Left as
    # console=True on Linux/macOS, matching the previous (and still current) debug
    # behavior there -- this only changes what happens when built ON Windows.
    console=not _is_windows,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
