from __future__ import annotations

import ctypes
import shutil
from pathlib import Path

from .cache import ImageCache
from .models import Wallpaper


def safe_filename(value: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("" if character in forbidden else character for character in value)
    return " ".join(cleaned.split()).strip(" .")[:80] or "精选壁纸"


def set_desktop_wallpaper(path: Path) -> None:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    user32 = ctypes.windll.user32
    user32.SystemParametersInfoW.argtypes = [
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    ]
    user32.SystemParametersInfoW.restype = ctypes.c_bool
    success = user32.SystemParametersInfoW(20, 0, str(path), 0x01 | 0x02)
    if not success:
        raise OSError("Windows 拒绝设置桌面壁纸")


def attribution_text(wallpaper: Wallpaper) -> str:
    return "\n".join(
        (
            f"标题: {wallpaper.title}",
            f"作者: {wallpaper.artist or wallpaper.copyright}",
            f"许可证: {wallpaper.license_name or '请以来源页为准'}",
            f"许可证链接: {wallpaper.license_url or '未提供'}",
            f"原始来源: {wallpaper.copyright_link or '未提供'}",
            f"来源平台: {wallpaper.provider}",
        )
    ) + "\n"


class WallpaperService:
    def __init__(self, cache: ImageCache, apply_function=set_desktop_wallpaper):
        self.cache = cache
        self.apply_function = apply_function
        self.last_apply_quality = "uhd"
        self.last_download_quality = "uhd"

    def download(self, wallpaper: Wallpaper, output_dir: Path) -> Path:
        source = self._cached_source(wallpaper, "uhd")
        quality = "uhd"
        if source is None:
            source = self._cached_preview_source(wallpaper)
            quality = "preview"
        if source is None:
            quality = "uhd"
            try:
                source = self.cache.fetch(wallpaper, quality)
            except Exception as uhd_error:
                source = self._preview_source(wallpaper)
                if source is None:
                    raise uhd_error
                quality = "preview"
        self.last_download_quality = quality
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        label = "4K" if quality == "uhd" else "Preview"
        target = output_dir / f"{safe_filename(wallpaper.title)}_{wallpaper.startdate}_{label}{source.suffix or '.jpg'}"
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        attribution_target = target.with_suffix(f"{target.suffix}.license.txt")
        attribution_temporary = attribution_target.with_suffix(".tmp")
        try:
            shutil.copyfile(source, temporary)
            attribution_temporary.write_text(
                attribution_text(wallpaper),
                encoding="utf-8",
            )
            temporary.replace(target)
            attribution_temporary.replace(attribution_target)
        except Exception:
            temporary.unlink(missing_ok=True)
            attribution_temporary.unlink(missing_ok=True)
            raise
        return target

    def apply(self, wallpaper: Wallpaper) -> Path:
        cached_uhd = self._cached_source(wallpaper, "uhd")
        if cached_uhd is not None:
            self.last_apply_quality = "uhd"
            self.apply_function(cached_uhd)
            return cached_uhd

        # The gallery already has a verified local preview. Apply it immediately
        # so a slow or temporarily unavailable remote original cannot block the
        # primary desktop workflow.
        preview = self._cached_preview_source(wallpaper)
        if preview is not None:
            self.last_apply_quality = "preview"
            self.apply_function(preview)
            return preview

        try:
            source = self.cache.fetch(wallpaper, "uhd")
            self.last_apply_quality = "uhd"
        except Exception as uhd_error:
            source = self._preview_source(wallpaper)
            if source is None:
                raise uhd_error
            self.last_apply_quality = "preview"
        self.apply_function(source)
        return source

    def _cached_source(self, wallpaper: Wallpaper, quality: str) -> Path | None:
        get_path = getattr(self.cache, "get_path", None)
        if not callable(get_path):
            return None
        cached = get_path(wallpaper, quality)
        if cached is None or not Path(cached).is_file():
            return None
        return Path(cached)

    def _cached_preview_source(self, wallpaper: Wallpaper) -> Path | None:
        local_preview = Path(wallpaper.local_preview) if wallpaper.local_preview else None
        if local_preview is not None and local_preview.is_file():
            return local_preview
        return self._cached_source(wallpaper, "preview")

    def _preview_source(self, wallpaper: Wallpaper) -> Path | None:
        cached = self._cached_preview_source(wallpaper)
        if cached is not None:
            return cached
        try:
            return self.cache.fetch(wallpaper, "preview")
        except Exception:
            return None
class LockScreenService:
    def __init__(self, storage_dir: Path | None = None):
        self.storage_dir = storage_dir or (
            Path.home() / "AppData" / "Local" / "JiangMaoWallpaper" / "lockscreen"
        )

    @staticmethod
    def is_supported() -> bool:
        try:
            from winrt.windows.system.userprofile import UserProfilePersonalizationSettings

            return bool(UserProfilePersonalizationSettings.is_supported())
        except (ImportError, OSError, RuntimeError):
            return False

    def apply_path(self, source: Path) -> Path:
        if not self.is_supported():
            raise OSError("当前 Windows 版本或账户策略不支持更改锁屏")
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix.lower() if source.suffix else ".jpg"
        target = self.storage_dir / f"current-lockscreen{suffix}"
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(target)

        from winrt.windows.storage import StorageFile
        from winrt.windows.system.userprofile import (
            LockScreen,
            UserProfilePersonalizationSettings,
        )

        storage_file = StorageFile.get_file_from_path_async(str(target.resolve())).get()
        settings = UserProfilePersonalizationSettings.current
        if not settings.try_set_lock_screen_image_async(storage_file).get():
            try:
                LockScreen.set_image_file_async(storage_file).get()
            except (OSError, RuntimeError) as error:
                raise OSError(
                    "Windows 拒绝设置锁屏图片，请检查账户或组织策略"
                ) from error
        return target
    def apply_wallpaper(self, wallpaper: Wallpaper, cache: ImageCache) -> Path:
        return self.apply_path(cache.fetch(wallpaper, "uhd"))
