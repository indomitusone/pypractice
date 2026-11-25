# -*- mode: python ; coding: utf-8 -*-
import os

codesign_identity = os.environ.get("PYPRACTICE_CODESIGN_IDENTITY") or None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/pypractice.ico', 'assets')],
    hiddenimports=[
        'themes',
        'safety',
        'diagnostics',
        'settings',
        'editor',
        'runner',
        'exercises',
        'debugger',
        'templates',
    ],
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
    name='PyPractice',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=codesign_identity,
    entitlements_file=None,
    icon=['assets/pypractice.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='PyPractice',
)
