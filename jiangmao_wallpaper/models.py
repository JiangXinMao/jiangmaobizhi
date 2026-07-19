from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path


APP_USER_AGENT = "JiangMaoWallpaper/1.0"


@dataclass(slots=True)
class Wallpaper:
    title: str
    copyright: str
    startdate: str
    preview_url: str
    full_url: str
    headline: str = ""
    provider: str = "公共领域摄影"
    copyright_link: str = ""
    artist: str = ""
    license_name: str = ""
    license_url: str = ""
    local_preview: str = ""
    local_full: str = ""

    @property
    def key(self) -> str:
        return self.startdate or self.full_url or self.preview_url

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Wallpaper":
        fields = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in fields})


TASKBAR_MODES = frozenset({"default", "transparent", "frosted"})


def _normalize_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "on", "yes"}:
            return True
        if normalized in {"false", "0", "off", "no"}:
            return False
    return default


@dataclass(slots=True)
class AppSettings:
    auto_change_enabled: bool = False
    auto_interval_minutes: int = 1440
    animation_enabled: bool = True
    animation_preference_version: int = 1
    startup_enabled: bool = False
    download_dir: str = str(Path.home() / "Pictures" / "JiangMaoWallpaper")
    taskbar_mode: str = "default"
    taskbar_intensity: int = 88
    taskbar_restore_on_start: bool = True
    taskbar_all_displays: bool = True
    lock_screen_sync_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.taskbar_mode, str) or self.taskbar_mode not in TASKBAR_MODES:
            self.taskbar_mode = "default"
        try:
            intensity = int(self.taskbar_intensity)
        except (TypeError, ValueError):
            intensity = 88
        self.taskbar_intensity = max(20, min(100, intensity))
        self.taskbar_restore_on_start = _normalize_bool(
            self.taskbar_restore_on_start, True
        )
        self.taskbar_all_displays = _normalize_bool(
            self.taskbar_all_displays, True
        )
        self.lock_screen_sync_enabled = _normalize_bool(
            self.lock_screen_sync_enabled, False
        )


@dataclass(slots=True)
class AppState:
    favorites: set[str] = field(default_factory=set)
    current_index: int = 0
    settings: AppSettings = field(default_factory=AppSettings)
    wallpapers: list[Wallpaper] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "favorites": sorted(self.favorites),
            "current_index": self.current_index,
            "settings": asdict(self.settings),
            "wallpapers": [wallpaper.to_dict() for wallpaper in self.wallpapers],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppState":
        settings_data = data.get("settings", {})
        settings_fields = AppSettings.__dataclass_fields__
        settings = AppSettings(**{key: value for key, value in settings_data.items() if key in settings_fields})
        if "animation_preference_version" not in settings_data:
            settings.animation_preference_version = 0
        wallpapers = []
        for item in data.get("wallpapers", []):
            try:
                wallpapers.append(Wallpaper.from_dict(item))
            except (TypeError, ValueError):
                continue
        return cls(
            favorites=set(data.get("favorites", [])),
            current_index=max(0, int(data.get("current_index", 0))),
            settings=settings,
            wallpapers=wallpapers,
        )
