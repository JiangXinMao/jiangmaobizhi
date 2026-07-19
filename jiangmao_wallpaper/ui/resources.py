from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon


def resource_path(relative_path: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / relative_path


def application_icon() -> QIcon:
    return QIcon(str(resource_path("jiangmao_wallpaper/ui/assets/app_icon.ico")))


def tray_icon() -> QIcon:
    return QIcon(str(resource_path("jiangmao_wallpaper/ui/assets/tray_icon.ico")))
