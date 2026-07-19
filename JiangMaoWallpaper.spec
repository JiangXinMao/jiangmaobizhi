# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


hiddenimports = (
    collect_submodules("PySide6.QtImageFormats")
    + collect_submodules("PySide6.QtSvg")
    + collect_submodules("winrt.windows.foundation")
    + collect_submodules("winrt.windows.storage")
    + collect_submodules("winrt.windows.system.userprofile")
)
a = Analysis(
    ["main.py"],
    pathex=["D:\\CODEX\\jiangzhi"],
    binaries=[],
    datas=[
        ("jiangmao_wallpaper/ui/assets/menu", "jiangmao_wallpaper/ui/assets/menu"),
        ("jiangmao_wallpaper/ui/assets/app_icon.ico", "jiangmao_wallpaper/ui/assets"),
        ("jiangmao_wallpaper/ui/assets/app_icon.png", "jiangmao_wallpaper/ui/assets"),
        ("jiangmao_wallpaper/ui/assets/tray_icon.ico", "jiangmao_wallpaper/ui/assets"),
        ("jiangmao_wallpaper/ui/assets/tray_icon.png", "jiangmao_wallpaper/ui/assets"),
        ("jiangmao_wallpaper/ui/assets/starter", "jiangmao_wallpaper/ui/assets/starter"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="匠猫壁纸",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="jiangmao_wallpaper/ui/assets/app_icon.ico",
)
