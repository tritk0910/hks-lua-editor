# PyInstaller spec for HKS Lua Editor — build with:  pyinstaller hks_lua_editor.spec
# Produces a single windowed executable at "dist/HKS Lua Editor.exe".

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[("assets/icon.png", "assets")],   # bundled so _load_icon() finds it
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HKS Lua Editor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # windowed GUI app — no console window
    disable_windowed_traceback=False,
    icon="assets/icon.png",      # PyInstaller converts PNG->ICO (needs Pillow)
)
