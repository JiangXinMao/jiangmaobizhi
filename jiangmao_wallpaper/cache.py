from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from threading import Lock, RLock

import requests
from PIL import Image, UnidentifiedImageError

from .models import APP_USER_AGENT, Wallpaper
from .network import create_http_session


class InvalidImageError(ValueError):
    pass


class ImageCache:
    DOWNLOAD_TIMEOUT = (5, 30)

    def __init__(self, directory: Path, session=None, legacy_directories=()):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.legacy_directories = tuple(Path(item) for item in legacy_directories)
        self.session = session if session is not None else create_http_session(retries=1)
        self._path_locks_guard = Lock()
        self._path_locks: dict[Path, RLock] = {}
        headers = getattr(self.session, "headers", None)
        if headers is not None:
            headers["User-Agent"] = APP_USER_AGENT

    def _path(self, wallpaper: Wallpaper, quality: str) -> Path:
        url = wallpaper.full_url if quality == "uhd" else wallpaper.preview_url
        cache_key = f"{wallpaper.key}:{quality}:{url}"
        digest = hashlib.sha256(cache_key.encode()).hexdigest()[:20]
        return self.directory / f"{digest}_{quality}.jpg"

    def _lock_for(self, path: Path) -> RLock:
        with self._path_locks_guard:
            lock = self._path_locks.get(path)
            if lock is None:
                lock = RLock()
                self._path_locks[path] = lock
            return lock

    def get_path(self, wallpaper: Wallpaper, quality: str = "preview") -> Path | None:
        if (
            quality == "uhd"
            and wallpaper.full_url
            and wallpaper.full_url == wallpaper.preview_url
        ):
            preview = self.get_path(wallpaper, "preview")
            if preview is not None:
                return preview
        path = self._path(wallpaper, quality)
        with self._lock_for(path):
            candidates = (path,) + tuple(
                directory / path.name for directory in self.legacy_directories
            )
            for candidate in candidates:
                if not candidate.exists():
                    continue
                if not self._is_valid_image(candidate):
                    if candidate == path:
                        candidate.unlink(missing_ok=True)
                    continue
                if candidate != path:
                    temporary = path.with_suffix(".legacy.tmp")
                    try:
                        shutil.copyfile(candidate, temporary)
                        temporary.replace(path)
                        return path
                    except OSError:
                        temporary.unlink(missing_ok=True)
                return candidate
        return None

    def fetch(self, wallpaper: Wallpaper, quality: str = "preview") -> Path:
        destination = self._path(wallpaper, quality)
        with self._lock_for(destination):
            cached = self.get_path(wallpaper, quality)
            if cached is not None:
                return cached
            url = wallpaper.full_url if quality == "uhd" else wallpaper.preview_url
            response = self.session.get(
                url,
                timeout=self.DOWNLOAD_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(response.content)
            try:
                with Image.open(temporary) as image:
                    image.load()
                    if image.format != "JPEG" or image.mode != "RGB":
                        normalized = destination.with_suffix(".normalized.tmp")
                        image.convert("RGB").save(
                            normalized,
                            format="JPEG",
                            quality=94,
                            optimize=True,
                        )
                        temporary.unlink(missing_ok=True)
                        normalized.replace(temporary)
            except (UnidentifiedImageError, OSError) as error:
                temporary.unlink(missing_ok=True)
                destination.with_suffix(".normalized.tmp").unlink(missing_ok=True)
                raise InvalidImageError("图片数据无效") from error
            temporary.replace(destination)
            return destination

    def import_file(
        self,
        wallpaper: Wallpaper,
        source: Path,
        quality: str = "preview",
    ) -> Path:
        destination = self._path(wallpaper, quality)
        with self._lock_for(destination):
            cached = self.get_path(wallpaper, quality)
            if cached is not None:
                return cached
            source = Path(source)
            if not source.is_file() or not self._is_valid_image(source):
                raise InvalidImageError(f"Invalid bundled image: {source}")
            temporary = destination.with_suffix(".import.tmp")
            try:
                shutil.copyfile(source, temporary)
                if not self._is_valid_image(temporary):
                    raise InvalidImageError(f"Invalid bundled image: {source}")
                temporary.replace(destination)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            return destination

    @staticmethod
    def _is_valid_image(path: Path) -> bool:
        try:
            with Image.open(path) as image:
                image.verify()
            return True
        except (UnidentifiedImageError, OSError):
            return False

    def clear(self) -> None:
        for path in self.directory.glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)
