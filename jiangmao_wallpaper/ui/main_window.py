from __future__ import annotations

import hashlib
import logging
import re
import sys
from collections import OrderedDict
from datetime import date
from concurrent.futures import Future, ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from PySide6.QtCore import QObject, QPoint, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor, QCloseEvent, QDesktopServices, QImage
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMenu

from ..models import TASKBAR_MODES, AppState, Wallpaper
from ..services import LockScreenService
from ..state import StateStore
from ..taskbar import TaskbarAppearanceService
from ..windows import StartupManager
from .accent import extract_accent
from .theme import DESIGN_HEIGHT, DESIGN_WIDTH
from .widgets import WallpaperStage


LOGGER = logging.getLogger(__name__)


def physical_to_logical_local_point(
    screen_point: QPoint,
    physical_window_origin: QPoint,
    device_pixel_ratio: float,
) -> QPoint:
    ratio = max(1.0, device_pixel_ratio)
    return QPoint(
        round((screen_point.x() - physical_window_origin.x()) / ratio),
        round((screen_point.y() - physical_window_origin.y()) / ratio),
    )


class FutureRelay(QObject):
    completed = Signal(object, object)


@dataclass(frozen=True, slots=True)
class PreparedPreview:
    path: str
    image: QImage
    fingerprint: str
    accent: QColor


class MainWindow(QMainWindow):
    REMOTE_CHECK_INTERVAL_MS = 6 * 60 * 60_000
    NETWORK_RETRY_INTERVAL_MS = 30_000
    PREVIEW_CACHE_LIMIT = 10
    PREFETCH_RADIUS = 2
    NAVIGATION_SAVE_DELAY_MS = 250
    HTCLIENT = 1
    HTLEFT = 10
    HTRIGHT = 11
    HTTOP = 12
    HTTOPLEFT = 13
    HTTOPRIGHT = 14
    HTBOTTOM = 15
    HTBOTTOMLEFT = 16
    HTBOTTOMRIGHT = 17
    def __init__(
        self,
        state_store: StateStore,
        provider_chain,
        wallpaper_service,
        image_cache=None,
        startup_manager=None,
        history_provider=None,
        taskbar_service=None,
        lock_screen_service=None,
        background_mode: bool = False,
        autoload: bool = True,
    ):
        super().__init__()
        self.state_store = state_store
        self.provider_chain = provider_chain
        self.wallpaper_service = wallpaper_service
        self.image_cache = image_cache
        self.startup_manager = startup_manager or StartupManager()
        self.history_provider = provider_chain if history_provider is None else history_provider
        self.taskbar_service = (
            TaskbarAppearanceService()
            if taskbar_service is None
            else taskbar_service
        )
        self.lock_screen_service = (
            LockScreenService()
            if lock_screen_service is None
            else lock_screen_service
        )
        self._history_page = 1
        self._history_total = 0
        self._history_loading = False
        self._history_exhausted = False
        self._advance_after_history = False
        self._user_navigated = False
        self._wallpaper_change_pending = False
        self._refresh_pending = False
        self._network_available: bool | None = None
        self.background_mode = background_mode
        self._allow_close = False
        self.state: AppState = self.state_store.load()
        if self.state.settings.animation_preference_version < 1:
            self.state.settings.animation_enabled = True
            self.state.settings.animation_preference_version = 1
            self.state_store.save(self.state)
        self.wallpapers: list[Wallpaper] = list(self.state.wallpapers)
        self.current_index = self.startup_wallpaper_index(self.wallpapers)
        self._requested_index = self.current_index
        self._displayed_content_fingerprint = ""
        self._content_skip_indexes: set[int] = set()
        self._prepared_previews: OrderedDict[str, PreparedPreview] = OrderedDict()
        self._pending_previews: dict[str, tuple[Future, bool, object]] = {}
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jiangmao")
        self._preview_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="jiangmao-preview"
        )
        self._navigation_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="jiangmao-navigation"
        )
        self._relay = FutureRelay(self)
        self._relay.completed.connect(self._future_completed)
        self._future_callbacks: dict[int, object] = {}
        self._state_save_timer = QTimer(self)
        self._state_save_timer.setSingleShot(True)
        self._state_save_timer.timeout.connect(self._save_state)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self.stage.show_toast(""))
        self._network_retry_timer = QTimer(self)
        self._network_retry_timer.setSingleShot(True)
        self._network_retry_timer.setInterval(self.NETWORK_RETRY_INTERVAL_MS)
        self._network_retry_timer.timeout.connect(self.refresh_wallpapers)
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._auto_change)
        self._pending_taskbar_intensity = self.state.settings.taskbar_intensity
        self._taskbar_intensity_timer = QTimer(self)
        self._taskbar_intensity_timer.setSingleShot(True)
        self._taskbar_intensity_timer.timeout.connect(
            self._apply_pending_taskbar_intensity
        )
        self._taskbar_handle_timer = QTimer(self)
        self._taskbar_handle_timer.setInterval(3000)
        self._taskbar_handle_timer.timeout.connect(self._check_taskbar_handles)

        self.setWindowTitle("匠猫壁纸")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.resize(960, 640)
        self.setMinimumSize(720, 480)
        self.stage = WallpaperStage(self)
        try:
            self.state.settings.startup_enabled = self.startup_manager.is_enabled()
        except OSError:
            pass
        self.stage.set_settings_state(
            startup_enabled=self.state.settings.startup_enabled,
            animation_enabled=self.state.settings.animation_enabled,
            animate=False,
        )
        self.stage.set_auto_change_state(
            self.state.settings.auto_change_enabled,
            self._interval_label(self.state.settings.auto_interval_minutes),
            animate=False,
        )
        self._sync_taskbar_stage()
        self.stage.set_lock_screen_state(
            self.state.settings.lock_screen_sync_enabled,
            "已开启自动同步" if self.state.settings.lock_screen_sync_enabled else "等待开启",
            animate=False,
        )
        self._taskbar_signature = self.taskbar_service.signature(
            self.state.settings.taskbar_all_displays
        )
        self._taskbar_handle_timer.start()
        self.stage.set_favorites(self.state.favorites)
        self.stage.set_wallpapers(self.wallpapers)
        self.stage.action_requested.connect(self._handle_action)
        self.stage.window_move_requested.connect(self._move_window)
        self.setCentralWidget(self.stage)
        self._configure_auto_timer()

        if self.wallpapers:
            self.set_wallpapers(self.wallpapers, persist=False)
        if autoload:
            QTimer.singleShot(0, self.refresh_wallpapers)
        if (
            self.state.settings.taskbar_restore_on_start
            and self.state.settings.taskbar_mode != "default"
        ):
            QTimer.singleShot(0, self._restore_taskbar_on_start)

    @property
    def current_wallpaper(self) -> Wallpaper | None:
        if not self.wallpapers:
            return None
        return self.wallpapers[self.current_index % len(self.wallpapers)]

    @staticmethod
    def latest_wallpaper_index(wallpapers: list[Wallpaper]) -> int:
        dated = [
            (wallpaper.startdate, index)
            for index, wallpaper in enumerate(wallpapers)
            if len(wallpaper.startdate) == 8 and wallpaper.startdate.isdigit()
        ]
        return max(dated)[1] if dated else 0

    @staticmethod
    def startup_wallpaper_index(
        wallpapers: list[Wallpaper], today_key: str | None = None
    ) -> int:
        today = today_key or date.today().strftime("%Y%m%d")
        return next(
            (
                index
                for index, wallpaper in enumerate(wallpapers)
                if wallpaper.startdate == today
            ),
            MainWindow.latest_wallpaper_index(wallpapers),
        )

    def _submit_to_executor(self, executor, function, callback) -> Future:
        future = executor.submit(function)
        self._future_callbacks[id(future)] = callback
        future.add_done_callback(lambda completed: self._relay.completed.emit(completed, id(completed)))
        return future

    def submit(self, function, callback) -> Future:
        return self._submit_to_executor(self._executor, function, callback)

    def _submit_preview(self, function, callback, *, urgent: bool) -> Future:
        executor = self._navigation_executor if urgent else self._preview_executor
        return self._submit_to_executor(executor, function, callback)

    @Slot(object, object)
    def _future_completed(self, future: Future, future_id: int) -> None:
        callback = self._future_callbacks.pop(future_id, None)
        if callback is None:
            return
        if future.cancelled():
            return
        try:
            callback(future.result(), None)
        except Exception as error:
            LOGGER.exception("Background operation failed")
            callback(None, error)

    def set_wallpapers(self, wallpapers: list[Wallpaper], persist: bool = True) -> None:
        current_key = self.current_wallpaper.key if self.current_wallpaper else ""
        self.wallpapers = self.deduplicate_wallpapers(wallpapers)
        valid_preview_keys = {
            self._preview_cache_key(wallpaper) for wallpaper in self.wallpapers
        }
        for cache_key in tuple(self._prepared_previews):
            if cache_key not in valid_preview_keys:
                self._prepared_previews.pop(cache_key, None)
        self.stage.set_wallpapers(self.wallpapers)
        if not self.wallpapers:
            self.stage.set_status("暂无壁纸，请检查网络后重试")
            return
        if current_key:
            self.current_index = next(
                (index for index, wallpaper in enumerate(self.wallpapers) if wallpaper.key == current_key),
                min(self.current_index, len(self.wallpapers) - 1),
            )
        else:
            self.current_index = min(self.current_index, len(self.wallpapers) - 1)
        self._requested_index = self.current_index
        self._displayed_content_fingerprint = ""
        self._content_skip_indexes.clear()
        self._display_index(self.current_index, allow_sync=True)
        if persist:
            self._save_state()
        self._prefetch_neighbors()

    @staticmethod
    @lru_cache(maxsize=1024)
    def _image_identity_from_url(url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        identifier = query.get("id", [""])[0] or unquote(parsed.path)
        identifier = re.sub(
            r"_(?:UHD|\d{3,5}x\d{3,5})(?=\.jpe?g$)",
            "",
            identifier,
            flags=re.IGNORECASE,
        )
        return identifier.casefold()

    @staticmethod
    def wallpaper_image_identity(wallpaper: Wallpaper) -> str:
        url = wallpaper.full_url or wallpaper.preview_url
        return MainWindow._image_identity_from_url(url)

    @classmethod
    def wallpaper_identity(cls, wallpaper: Wallpaper) -> str:
        if len(wallpaper.startdate) == 8 and wallpaper.startdate.isdigit():
            return f"date:{wallpaper.startdate}"
        image_identity = cls.wallpaper_image_identity(wallpaper)
        return f"image:{image_identity}" if image_identity else f"key:{wallpaper.key}"

    @classmethod
    def same_wallpaper(cls, first: Wallpaper, second: Wallpaper) -> bool:
        first_date = first.startdate if len(first.startdate) == 8 and first.startdate.isdigit() else ""
        second_date = second.startdate if len(second.startdate) == 8 and second.startdate.isdigit() else ""
        if first_date and first_date == second_date:
            return True
        first_image = cls.wallpaper_image_identity(first)
        second_image = cls.wallpaper_image_identity(second)
        return bool(first_image and first_image == second_image)

    @classmethod
    def deduplicate_wallpapers(cls, wallpapers: list[Wallpaper]) -> list[Wallpaper]:
        unique: list[Wallpaper] = []
        seen_dates: set[str] = set()
        seen_images: set[str] = set()
        for wallpaper in wallpapers:
            official_date = (
                wallpaper.startdate
                if len(wallpaper.startdate) == 8 and wallpaper.startdate.isdigit()
                else ""
            )
            image_identity = cls.wallpaper_image_identity(wallpaper)
            if official_date and official_date in seen_dates:
                continue
            if image_identity and image_identity in seen_images:
                continue
            unique.append(wallpaper)
            if official_date:
                seen_dates.add(official_date)
            if image_identity:
                seen_images.add(image_identity)
        return unique

    @classmethod
    def merge_wallpapers(cls, existing: list[Wallpaper], incoming: list[Wallpaper]) -> list[Wallpaper]:
        merged = cls.deduplicate_wallpapers(existing)
        for wallpaper in incoming:
            matching_index = next(
                (index for index, current in enumerate(merged) if cls.same_wallpaper(current, wallpaper)),
                None,
            )
            if matching_index is None:
                merged.append(wallpaper)
                continue
            current = merged[matching_index]
            merged[matching_index] = replace(
                current,
                title=wallpaper.title or current.title,
                headline=wallpaper.headline or current.headline,
                copyright=wallpaper.copyright or current.copyright,
                preview_url=wallpaper.preview_url or current.preview_url,
                full_url=wallpaper.full_url or current.full_url,
                provider=wallpaper.provider or current.provider,
                copyright_link=wallpaper.copyright_link or current.copyright_link,
                artist=wallpaper.artist or current.artist,
                license_name=wallpaper.license_name or current.license_name,
                license_url=wallpaper.license_url or current.license_url,
                local_preview=current.local_preview or wallpaper.local_preview,
                local_full=current.local_full or wallpaper.local_full,
            )
        return sorted(merged, key=lambda item: item.startdate, reverse=True)

    def load_more_history(self, advance_when_ready: bool = False) -> None:
        if not self.history_provider or self._history_exhausted:
            return
        if advance_when_ready:
            self._advance_after_history = True
        if self._history_loading:
            return
        self._history_loading = True
        self._toast_timer.stop()
        self.stage.show_toast("")
        self.stage.set_wallpaper_group_loading(True)
        page = self._history_page

        def finished(result, error):
            self._history_loading = False
            self.stage.set_wallpaper_group_loading(False)
            if error or result is None:
                should_advance = self._advance_after_history
                self._advance_after_history = False
                self._network_available = False
                if should_advance and self._wrap_forward():
                    self._toast("网络暂不可用，已继续浏览本地缓存")
                else:
                    self._toast("精选图库加载失败，请稍后重试")
                return
            self._network_available = True
            current_key = self.current_wallpaper.key if self.current_wallpaper else ""
            previous_count = len(self.wallpapers)
            self.wallpapers = self.merge_wallpapers(self.wallpapers, result.wallpapers)
            self.stage.set_wallpapers(self.wallpapers)
            if current_key:
                self.current_index = next(
                    (index for index, item in enumerate(self.wallpapers) if item.key == current_key),
                    self.current_index,
                )
                self._requested_index = self.current_index
            self._history_page = result.page + 1
            self._history_total = result.total
            self._history_exhausted = not result.has_more
            if result.provider:
                self.stage.set_status(f"{result.provider} · 高清精选")
            should_advance = self._advance_after_history
            self._advance_after_history = False
            self._save_state()
            if should_advance and self.current_index + 1 < len(self.wallpapers):
                self.request_index(self.current_index + 1)
            elif result.has_more and len(self.wallpapers) == previous_count:
                self.load_more_history(advance_when_ready=should_advance)

        self.submit(lambda: self.history_provider.fetch_page(page, 30), finished)

    def refresh_wallpapers(self) -> None:
        self._network_retry_timer.stop()
        self._refresh_pending = True
        self.stage.set_status("正在连接高清摄影源…")

        if self.history_provider:
            self._history_loading = True

            def history_finished(result, error):
                self._history_loading = False
                if error or result is None or not result.wallpapers:
                    self._network_available = False
                    self.stage.set_status("网络暂不可用，继续展示本地缓存")
                    self._toast("摄影源连接失败，已使用本地缓存")
                    self._finish_refresh(False)
                    return
                self._network_available = True
                self._history_page = result.page + 1
                self._history_total = result.total
                self._history_exhausted = not result.has_more
                source = result.provider or "公共领域摄影"
                self.stage.set_status(f"{source} · 高清实拍")
                self.set_wallpapers(
                    self.merge_wallpapers(self.wallpapers, result.wallpapers)
                )
                if not self._user_navigated:
                    startup_index = self.startup_wallpaper_index(self.wallpapers)
                    if startup_index != self.current_index:
                        self.request_index(startup_index, user_initiated=False)
                self._toast(f"已同步 {len(result.wallpapers)} 张壁纸")
                self._finish_refresh(True)

            self.submit(lambda: self.history_provider.fetch_page(1, 30), history_finished)
            return

        self._refresh_from_fallback_chain()

    def _refresh_from_fallback_chain(self) -> None:
        self.stage.set_status("正在切换备用摄影源…")

        def finished(result, error):
            if error or not result.wallpapers:
                self._network_available = False
                self.stage.set_status("网络暂不可用，继续展示本地缓存")
                self._toast("接口连接失败，已使用缓存")
                self._finish_refresh(False)
                return
            self._network_available = True
            self.stage.set_health(result.health)
            self.stage.set_status(f"{result.provider} · 高清实拍")
            self.set_wallpapers(
                self.merge_wallpapers(self.wallpapers, result.wallpapers)
            )
            self._toast(f"已同步 {len(result.wallpapers)} 张壁纸")
            self._finish_refresh(True)

        self.submit(lambda: self.provider_chain.fetch(8, "zh-CN"), finished)

    def request_index(self, index: int, *, user_initiated: bool = True) -> None:
        if not self.wallpapers:
            self.refresh_wallpapers()
            return
        previous_index = self.current_index
        self._requested_index = index % len(self.wallpapers)
        self.current_index = self._requested_index
        if user_initiated:
            self._user_navigated = True
            if self._requested_index != previous_index:
                self._wallpaper_change_pending = True
        self._display_index(self.current_index)
        self._schedule_state_save()
        self._prefetch_neighbors()

    @staticmethod
    def _preview_cache_key(wallpaper: Wallpaper) -> str:
        return f"{wallpaper.key}\n{wallpaper.preview_url}"

    @staticmethod
    def _path_content_fingerprint(path: Path) -> str:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return ""

    def _cached_preview_path(self, wallpaper: Wallpaper) -> Path | None:
        local_path = Path(wallpaper.local_preview) if wallpaper.local_preview else None
        if local_path is not None and local_path.is_file():
            return local_path
        if not self.image_cache:
            return None
        get_path = getattr(self.image_cache, "get_path", None)
        if not callable(get_path):
            return None
        try:
            cached = get_path(wallpaper, "preview")
        except Exception:
            LOGGER.exception("Cached preview lookup failed")
            return None
        if cached is None or not Path(cached).is_file():
            return None
        wallpaper.local_preview = str(cached)
        return Path(cached)

    def _prepare_preview(self, wallpaper: Wallpaper) -> PreparedPreview:
        local_path = self._cached_preview_path(wallpaper)
        if local_path is None:
            if not self.image_cache:
                raise FileNotFoundError(wallpaper.local_preview or wallpaper.preview_url)
            local_path = self.image_cache.fetch(wallpaper, "preview")
        image = QImage(str(local_path))
        if image.isNull():
            raise ValueError(f"Invalid preview image: {local_path}")
        return PreparedPreview(
            path=str(local_path),
            image=image,
            fingerprint=self._path_content_fingerprint(local_path),
            accent=extract_accent(image),
        )

    def _remember_preview(self, cache_key: str, preview: PreparedPreview) -> None:
        self._prepared_previews[cache_key] = preview
        self._prepared_previews.move_to_end(cache_key)
        while len(self._prepared_previews) > self.PREVIEW_CACHE_LIMIT:
            self._prepared_previews.popitem(last=False)

    def _queue_preview(self, wallpaper: Wallpaper, *, urgent: bool = False) -> None:
        cache_key = self._preview_cache_key(wallpaper)
        if cache_key in self._prepared_previews:
            return
        local_path = self._cached_preview_path(wallpaper)
        requires_network = local_path is None
        if requires_network and (
            not self.image_cache or self._network_available is False
        ):
            return
        if urgent:
            for pending_key, (future, is_urgent, token) in tuple(
                self._pending_previews.items()
            ):
                if is_urgent and pending_key != cache_key and future.cancel():
                    self._pending_previews.pop(pending_key, None)
        pending = self._pending_previews.get(cache_key)
        if pending is not None:
            future, is_urgent, token = pending
            if not urgent or is_urgent or not future.cancel():
                return
            self._pending_previews.pop(cache_key, None)
        snapshot = replace(
            wallpaper,
            local_preview=str(local_path) if local_path is not None else "",
        )
        token = object()

        def finished(preview, error):
            pending = self._pending_previews.get(cache_key)
            if pending is None or pending[2] is not token:
                return
            self._pending_previews.pop(cache_key, None)
            requested = bool(self.wallpapers) and self._preview_cache_key(
                self.wallpapers[self._requested_index]
            ) == cache_key
            if error or preview is None:
                if requires_network:
                    self._network_available = False
                    if self.wallpapers:
                        self._network_retry_timer.start()
                if requested:
                    self._toast("图片加载失败，请稍后重试")
                    self._finish_wallpaper_change(False)
                    self._show_cached_fallback()
                return
            if requires_network:
                self._network_available = True
            self._remember_preview(cache_key, preview)
            for item in self.wallpapers:
                if self._preview_cache_key(item) == cache_key:
                    item.local_preview = preview.path
            if requested:
                index = self._requested_index
                self._commit_image(
                    index,
                    self.wallpapers[index],
                    preview.image,
                    preview.fingerprint,
                    preview.accent,
                )
            self._schedule_state_save()

        future = self._submit_preview(
            lambda: self._prepare_preview(snapshot),
            finished,
            urgent=urgent,
        )
        self._pending_previews[cache_key] = (future, urgent, token)

    def _display_index(self, index: int, *, allow_sync: bool = False) -> None:
        wallpaper = self.wallpapers[index]
        cache_key = self._preview_cache_key(wallpaper)
        preview = self._prepared_previews.get(cache_key)
        if preview is not None:
            self._prepared_previews.move_to_end(cache_key)
            wallpaper.local_preview = preview.path
            self._commit_image(
                index,
                wallpaper,
                preview.image,
                preview.fingerprint,
                preview.accent,
            )
            return
        local_path = Path(wallpaper.local_preview) if wallpaper.local_preview else None
        if allow_sync and local_path is not None and local_path.is_file():
            preview = self._prepare_preview(wallpaper)
            self._remember_preview(cache_key, preview)
            self._commit_image(
                index,
                wallpaper,
                preview.image,
                preview.fingerprint,
                preview.accent,
            )
            return
        self._queue_preview(wallpaper, urgent=True)

    def _commit_image(
        self,
        index: int,
        wallpaper: Wallpaper,
        image: QImage,
        fingerprint: str | None = None,
        accent: QColor | None = None,
    ) -> None:
        if index != self._requested_index or image.isNull():
            return
        if fingerprint is None:
            fingerprint = self._image_content_fingerprint(wallpaper)
        if fingerprint and fingerprint == self._displayed_content_fingerprint:
            if (
                self.stage.current_wallpaper is not None
                and self._preview_cache_key(self.stage.current_wallpaper)
                == self._preview_cache_key(wallpaper)
            ):
                return
            self._content_skip_indexes.add(index)
            target = self.adjacent_unique_index(
                self.stage.transition_direction,
                index,
                self._content_skip_indexes,
            )
            if target is not None:
                self.request_index(target)
            return
        self.current_index = index
        self._displayed_content_fingerprint = fingerprint
        self._content_skip_indexes.clear()
        self.stage.set_content(wallpaper, image, accent)
        self._finish_wallpaper_change(True)

    def _finish_wallpaper_change(self, success: bool) -> None:
        if not self._wallpaper_change_pending:
            return
        self._wallpaper_change_pending = False

    def _finish_refresh(self, success: bool) -> None:
        if not self._refresh_pending:
            return
        self._refresh_pending = False
        if success:
            self._network_retry_timer.stop()
        elif self.wallpapers:
            self._network_retry_timer.start()

    @staticmethod
    def _image_content_fingerprint(wallpaper: Wallpaper) -> str:
        path = wallpaper.local_preview
        if not path or not Path(path).is_file():
            return ""
        return MainWindow._path_content_fingerprint(Path(path))

    def _prefetch_neighbors(self) -> None:
        if len(self.wallpapers) < 2:
            return
        offsets = []
        for distance in range(1, self.PREFETCH_RADIUS + 1):
            offsets.extend(
                (self.stage.transition_direction * distance, -self.stage.transition_direction * distance)
            )
        queued: set[str] = set()
        for offset in offsets:
            wallpaper = self.wallpapers[(self.current_index + offset) % len(self.wallpapers)]
            cache_key = self._preview_cache_key(wallpaper)
            if cache_key in queued:
                continue
            queued.add(cache_key)
            self._queue_preview(wallpaper)

    def previous_wallpaper(self) -> None:
        self.stage.set_transition_direction(-1)
        self._content_skip_indexes.clear()
        target = self.adjacent_unique_index(
            -1,
            self._requested_index,
            available_only=True,
        )
        if target is None and self._network_available is not False:
            target = self.adjacent_unique_index(-1, self._requested_index)
        if target is not None:
            self.request_index(target)

    def next_wallpaper(self) -> None:
        self.stage.set_transition_direction(1)
        self._content_skip_indexes.clear()
        target = self.adjacent_unique_index(
            1,
            self._requested_index,
            available_only=True,
        )
        if target is None and self._network_available is not False:
            target = self.adjacent_unique_index(1, self._requested_index)
        if target is None:
            if (
                self.history_provider
                and not self._history_exhausted
                and self._network_available is not False
            ):
                self.load_more_history(advance_when_ready=True)
                return
            self._wrap_forward()
            return
        self.request_index(target)
        if (
            len(self.wallpapers) - self.current_index <= 3
            and self._network_available is not False
        ):
            self.load_more_history()

    def _wrap_forward(self) -> bool:
        target = self.adjacent_unique_index(
            1,
            self._requested_index,
            wrap=True,
            available_only=True,
        )
        if target is None and self._network_available is not False:
            target = self.adjacent_unique_index(
                1,
                self._requested_index,
                wrap=True,
            )
        if target is None:
            return False
        self.request_index(target)
        return True

    def _show_cached_fallback(self) -> bool:
        target = self.adjacent_unique_index(
            self.stage.transition_direction,
            self._requested_index,
            wrap=True,
            available_only=True,
        )
        if target is None:
            return False
        self.request_index(target, user_initiated=False)
        return True

    def adjacent_unique_index(
        self,
        direction: int,
        origin_index: int | None = None,
        excluded_indexes: set[int] | None = None,
        *,
        wrap: bool = False,
        available_only: bool = False,
    ) -> int | None:
        if len(self.wallpapers) < 2:
            return None
        origin = self.current_index if origin_index is None else origin_index % len(self.wallpapers)
        current = self.wallpapers[origin]
        if direction > 0:
            indexes = list(range(origin + 1, len(self.wallpapers)))
            if wrap:
                indexes.extend(range(0, origin))
        else:
            indexes = (
                (origin - step) % len(self.wallpapers)
                for step in range(1, len(self.wallpapers))
            )
        return next(
            (
                index
                for index in indexes
                if index not in (excluded_indexes or set())
                and not self.same_wallpaper(current, self.wallpapers[index])
                and (
                    not available_only
                    or self._preview_cache_key(self.wallpapers[index])
                    in self._prepared_previews
                    or self._cached_preview_path(self.wallpapers[index]) is not None
                )
                and not any(
                    self.same_wallpaper(self.wallpapers[index], earlier)
                    for earlier in self.wallpapers[:index]
                )
            ),
            None,
        )

    def toggle_favorite(self) -> None:
        wallpaper = self.current_wallpaper
        if not wallpaper:
            return
        if wallpaper.key in self.state.favorites:
            self.state.favorites.remove(wallpaper.key)
            message = "已取消收藏"
        else:
            self.state.favorites.add(wallpaper.key)
            message = "已加入收藏"
        self.stage.set_favorites(self.state.favorites)
        self._save_state()
        self._toast(message)

    def download_current(self) -> None:
        wallpaper = self.current_wallpaper
        if not wallpaper:
            return
        self.stage.set_operation_loading(True, "正在保存壁纸…")

        def finished(path, error):
            self.stage.set_operation_loading(False)
            quality = getattr(self.wallpaper_service, "last_download_quality", "uhd")
            self._toast(
                "下载失败，请检查网络或目录权限"
                if error
                else (
                    f"4K 原图已保存到 {path.parent}"
                    if quality == "uhd"
                    else f"高清预览图已保存到 {path.parent}"
                ),
                3500,
            )

        directory = Path(self.state.settings.download_dir)
        self.submit(lambda: self.wallpaper_service.download(wallpaper, directory), finished)

    def apply_current(self) -> None:
        wallpaper = self.current_wallpaper
        if not wallpaper:
            return
        self.stage.set_operation_loading(True, "正在准备桌面壁纸…")

        def finished(path, error):
            self.stage.set_operation_loading(False)
            if error:
                if "拒绝设置桌面壁纸" in str(error):
                    message = "设置失败：Windows 未允许更改桌面壁纸"
                else:
                    message = "设置失败：壁纸下载失败，请检查网络后重试"
                self._toast(message, 3500)
                return
            if self.state.settings.lock_screen_sync_enabled:
                self.stage.set_operation_loading(True, "桌面已更新，正在同步锁屏…")
                self.submit(
                    lambda: self.lock_screen_service.apply_path(path),
                    self._lock_screen_finished,
                )
            else:
                quality = getattr(self.wallpaper_service, "last_apply_quality", "uhd")
                message = "4K 壁纸已应用到桌面" if quality == "uhd" else "已使用缓存预览图应用到桌面"
                self._toast(message, 3000)

        self.submit(lambda: self.wallpaper_service.apply(wallpaper), finished)

    def _lock_screen_finished(self, path, error) -> None:
        self.stage.set_operation_loading(False)
        status = "锁屏同步失败，请检查 Windows 账户或组织策略" if error else "桌面与锁屏已同步"
        self.stage.set_lock_screen_state(
            self.state.settings.lock_screen_sync_enabled,
            status,
        )
        self._toast(status, 3500)

    def sync_lock_screen_now(self) -> None:
        wallpaper = self.current_wallpaper
        if not wallpaper:
            return
        self.stage.set_operation_loading(True, "正在下载壁纸并同步锁屏…")
        self.submit(
            lambda: self.lock_screen_service.apply_wallpaper(
                wallpaper, self.wallpaper_service.cache
            ),
            self._lock_screen_finished,
        )

    def toggle_lock_screen_sync(self) -> None:
        settings = self.state.settings
        settings.lock_screen_sync_enabled = not settings.lock_screen_sync_enabled
        self._save_state()
        status = "已开启，设置桌面壁纸时会同步锁屏" if settings.lock_screen_sync_enabled else "已关闭自动同步"
        self.stage.set_lock_screen_state(settings.lock_screen_sync_enabled, status)
        self._toast(status, 3000)
    @staticmethod
    def _taskbar_mode_label(mode: str) -> str:
        return {
            "default": "系统默认",
            "transparent": "通透",
            "frosted": "磨砂",
        }.get(mode, "系统默认")

    def _sync_taskbar_stage(
        self, status: str = "", intensity: int | None = None
    ) -> None:
        settings = self.state.settings
        self.stage.set_taskbar_state(
            mode=settings.taskbar_mode,
            intensity=(
                settings.taskbar_intensity if intensity is None else intensity
            ),
            restore_on_start=settings.taskbar_restore_on_start,
            all_displays=settings.taskbar_all_displays,
            status=status,
        )

    @staticmethod
    def _taskbar_result_complete(
        result, *, require_primary: bool = True
    ) -> bool:
        return (
            result.success
            and result.applied_count == result.total_count
            and (result.primary_applied or not require_primary)
        )

    @staticmethod
    def _taskbar_result_status(result) -> str:
        status = {
            "frosted-compat": "兼容磨砂",
            "mixed": "混合磨砂",
        }.get(result.applied_mode, "已应用")
        if result.applied_count < result.total_count:
            status = (
                f"{status} {result.applied_count}/{result.total_count}"
                " · 部分失败"
            )
        return status

    def _taskbar_failure(
        self,
        result,
        *,
        compensation_complete: bool,
        monitor_all: bool = False,
    ) -> None:
        count = f"{result.applied_count}/{result.total_count}"
        message = result.error or "任务栏效果应用失败"
        if result.applied_count < result.total_count:
            message = f"{message}（已应用 {count}）"
        LOGGER.warning(
            "Taskbar transition incomplete: mode=%s applied_mode=%s count=%s "
            "primary_applied=%s error=%s",
            result.requested_mode,
            result.applied_mode,
            count,
            result.primary_applied,
            result.error,
        )
        if compensation_complete:
            status = f"{message}；已恢复原设置"
        else:
            status = f"{message}；回滚失败，任务栏状态可能不一致"
            LOGGER.error(
                "Taskbar compensation failed after transition: mode=%s "
                "applied_mode=%s count=%s primary_applied=%s error=%s",
                result.requested_mode,
                result.applied_mode,
                count,
                result.primary_applied,
                result.error,
            )
        self._sync_taskbar_stage(status)
        self._toast(status, 5000 if not compensation_complete else 3500)
        if monitor_all:
            self._taskbar_signature = self.taskbar_service.signature(True)

    def _compensate_taskbar_mode(
        self, mode: str, intensity: int, scope: str
    ) -> bool:
        rollback = self.taskbar_service.apply(mode, intensity, scope)
        return self._taskbar_compensation_complete(rollback)

    def _taskbar_compensation_complete(
        self, result, *, require_primary: bool = True
    ) -> bool:
        complete = self._taskbar_result_complete(
            result, require_primary=require_primary
        )
        if not complete:
            LOGGER.error(
                "Taskbar compensation apply incomplete: mode=%s "
                "applied_mode=%s count=%s/%s primary_applied=%s error=%s",
                result.requested_mode,
                result.applied_mode,
                result.applied_count,
                result.total_count,
                result.primary_applied,
                result.error,
            )
        return complete

    def _compensate_disable_taskbar_displays(
        self, mode: str, intensity: int
    ) -> bool:
        rollback = self.taskbar_service.apply(mode, intensity, "secondary")
        return self._taskbar_compensation_complete(
            rollback, require_primary=False
        )

    def _compensate_enable_taskbar_displays(
        self, mode: str, intensity: int
    ) -> bool:
        primary = self.taskbar_service.apply(mode, intensity, "primary")
        secondary = self.taskbar_service.apply(
            "default", intensity, "secondary"
        )
        primary_ok = self._taskbar_compensation_complete(primary)
        secondary_ok = self._taskbar_compensation_complete(
            secondary, require_primary=False
        )
        return primary_ok and secondary_ok

    def apply_taskbar_mode(
        self,
        mode: str,
        *,
        persist: bool = True,
        intensity: int | None = None,
    ) -> bool:
        if mode not in TASKBAR_MODES:
            return False
        settings = self.state.settings
        previous_mode = settings.taskbar_mode
        previous_intensity = settings.taskbar_intensity
        target_intensity = (
            previous_intensity if intensity is None else intensity
        )
        scope = "all" if settings.taskbar_all_displays else "primary"
        result = self.taskbar_service.apply(
            mode, target_intensity, scope
        )
        complete = self._taskbar_result_complete(result)
        applied = (
            result.success
            and result.primary_applied
            and result.applied_count > 0
        )
        if (mode == "default" and not complete) or (
            mode != "default" and not applied
        ):
            compensation_complete = self._compensate_taskbar_mode(
                previous_mode, previous_intensity, scope
            )
            self._taskbar_failure(
                result, compensation_complete=compensation_complete
            )
            return False

        settings.taskbar_mode = mode
        settings.taskbar_intensity = target_intensity
        if persist:
            self._save_state()
        status = self._taskbar_result_status(result)
        self._sync_taskbar_stage(status)
        if result.applied_mode == "mixed":
            self._toast("混合磨砂已应用，部分任务栏使用兼容模糊", 3500)
        elif result.applied_count < result.total_count:
            self._toast(
                f"{self._taskbar_mode_label(mode)}已应用 "
                f"{result.applied_count}/{result.total_count}，部分显示器未更新",
                3500,
            )
        else:
            self._toast(f"{self._taskbar_mode_label(mode)}已应用")
        self._taskbar_signature = self.taskbar_service.signature(
            settings.taskbar_all_displays
        )
        return True

    def reset_taskbar(self) -> None:
        self.apply_taskbar_mode("default")

    def queue_taskbar_intensity(self, intensity: int) -> None:
        self._pending_taskbar_intensity = max(20, min(100, intensity))
        self._sync_taskbar_stage(intensity=self._pending_taskbar_intensity)
        self._taskbar_intensity_timer.start(120)

    def _apply_pending_taskbar_intensity(self) -> None:
        settings = self.state.settings
        if settings.taskbar_mode == "default":
            return
        self.apply_taskbar_mode(
            settings.taskbar_mode,
            intensity=self._pending_taskbar_intensity,
        )

    def toggle_taskbar_restore(self) -> None:
        settings = self.state.settings
        settings.taskbar_restore_on_start = not settings.taskbar_restore_on_start
        self._save_state()
        self._sync_taskbar_stage()

    def toggle_taskbar_displays(self) -> None:
        settings = self.state.settings
        enabling = not settings.taskbar_all_displays
        if settings.taskbar_mode == "default":
            settings.taskbar_all_displays = enabling
            self._save_state()
            status = (
                "多显示器同步偏好已开启"
                if enabling
                else "多显示器同步偏好已关闭"
            )
            self._sync_taskbar_stage(status)
            self._toast(status)
            return

        mode = settings.taskbar_mode if enabling else "default"
        scope = "all" if enabling else "secondary"
        result = self.taskbar_service.apply(
            mode, settings.taskbar_intensity, scope
        )
        if not self._taskbar_result_complete(
            result, require_primary=enabling
        ):
            if enabling:
                compensation_complete = (
                    self._compensate_enable_taskbar_displays(
                        settings.taskbar_mode,
                        settings.taskbar_intensity,
                    )
                )
            else:
                compensation_complete = (
                    self._compensate_disable_taskbar_displays(
                        settings.taskbar_mode,
                        settings.taskbar_intensity,
                    )
                )
            self._taskbar_failure(
                result,
                compensation_complete=compensation_complete,
                monitor_all=not enabling,
            )
            return

        settings.taskbar_all_displays = enabling
        self._save_state()
        self._sync_taskbar_stage(self._taskbar_result_status(result))
        self._taskbar_signature = self.taskbar_service.signature(enabling)

    def _restore_taskbar_on_start(self) -> None:
        settings = self.state.settings
        if (
            settings.taskbar_restore_on_start
            and settings.taskbar_mode != "default"
        ):
            self.apply_taskbar_mode(settings.taskbar_mode, persist=False)

    def _check_taskbar_handles(self) -> None:
        settings = self.state.settings
        signature = self.taskbar_service.signature(
            settings.taskbar_all_displays
        )
        if signature == self._taskbar_signature:
            return
        self._taskbar_signature = signature
        if settings.taskbar_mode != "default" and signature:
            self.apply_taskbar_mode(settings.taskbar_mode, persist=False)

    @Slot(str)
    def _handle_action(self, action: str) -> None:
        if action == "toggle-menu":
            self.stage.toggle_menu()
            return
        if action == "window:minimize":
            self.hide() if self.background_mode else self.showMinimized()
            return
        if action == "window:maximize":
            self.toggle_maximized()
            return
        if action == "window:close":
            self.close()
            return
        if action == "open:wallpaper-source":
            wallpaper = self.current_wallpaper
            opened = bool(
                wallpaper
                and wallpaper.copyright_link
                and QDesktopServices.openUrl(QUrl(wallpaper.copyright_link))
            )
            return
        if action == "close-page":
            self.stage.set_page("设置" if self.stage.current_page == "关于" else "首页")
            return
        if action.startswith("select:"):
            key = action.split(":", 1)[1]
            for index, wallpaper in enumerate(self.wallpapers):
                if wallpaper.key == key:
                    self.stage.set_page("首页")
                    self.request_index(index)
                    return
        if action.startswith("page:"):
            page = action.split(":", 1)[1]
            self.stage.set_page(page)
            return
        if action.startswith("settings:section:"):
            self.stage.set_settings_section(action.rsplit(":", 1)[1])
            return
        if action.startswith("taskbar:mode:"):
            parts = action.split(":")
            if len(parts) == 3 and parts[2] in TASKBAR_MODES:
                self.apply_taskbar_mode(parts[2])
            return
        if action.startswith("taskbar:intensity:"):
            try:
                intensity = int(action.rsplit(":", 1)[1])
            except ValueError:
                return
            self.queue_taskbar_intensity(intensity)
            return
        commands = {
            "prev": self.previous_wallpaper,
            "next": self.next_wallpaper,
            "favorite": self.toggle_favorite,
            "download": self.download_current,
            "apply": self.apply_current,
            "startup": self.toggle_startup,
            "auto:toggle": self.toggle_auto_change,
            "auto:interval": self.show_auto_interval_menu,
            "animation": self.toggle_animation,
            "folder": self.choose_download_folder,
            "cache": self.clear_cache,
            "taskbar:restore-toggle": self.toggle_taskbar_restore,
            "taskbar:displays-toggle": self.toggle_taskbar_displays,
            "taskbar:reset": self.reset_taskbar,
            "lockscreen:toggle": self.toggle_lock_screen_sync,
            "lockscreen:sync": self.sync_lock_screen_now,
        }
        command = commands.get(action)
        if command:
            command()

    def toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _move_window(self, delta) -> None:
        if not self.isMaximized():
            self.move(self.pos() + QPoint(round(delta.x()), round(delta.y())))

    def resize_hit_test(self, point: QPoint, border: int = 7) -> int:
        if self.isMaximized():
            return self.HTCLIENT
        left = point.x() < border
        right = point.x() >= self.width() - border
        top = point.y() < border
        bottom = point.y() >= self.height() - border
        if top and left:
            return self.HTTOPLEFT
        if top and right:
            return self.HTTOPRIGHT
        if bottom and left:
            return self.HTBOTTOMLEFT
        if bottom and right:
            return self.HTBOTTOMRIGHT
        if left:
            return self.HTLEFT
        if right:
            return self.HTRIGHT
        if top:
            return self.HTTOP
        if bottom:
            return self.HTBOTTOM
        return self.HTCLIENT

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG":
            import ctypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:
                screen_x = ctypes.c_short(msg.lParam & 0xFFFF).value
                screen_y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                window_rect = wintypes.RECT()
                if ctypes.windll.user32.GetWindowRect(msg.hWnd, ctypes.byref(window_rect)):
                    local = physical_to_logical_local_point(
                        QPoint(screen_x, screen_y),
                        QPoint(window_rect.left, window_rect.top),
                        self.devicePixelRatioF(),
                    )
                else:
                    local = self.mapFromGlobal(QPoint(screen_x, screen_y))
                hit = self.resize_hit_test(local)
                if hit != self.HTCLIENT:
                    return True, hit
        return super().nativeEvent(event_type, message)

    def choose_download_folder(self) -> None:
        dialog = QFileDialog(self, "选择 4K 壁纸保存目录", self.state.settings.download_dir)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.raise_()
        dialog.activateWindow()
        if not dialog.exec():
            return
        selected_files = dialog.selectedFiles()
        if not selected_files:
            return
        self.state.settings.download_dir = selected_files[0]
        self._save_state()
        self.stage.show_settings_action_feedback("folder")
        self._toast("下载目录已更新")

    def toggle_startup(self) -> None:
        try:
            enabled = not self.startup_manager.is_enabled()
            self.startup_manager.set_enabled(enabled)
        except OSError as error:
            LOGGER.exception("Failed to update startup setting")
            self._toast(f"开机启动设置失败：{error}", 3500)
            return
        self.state.settings.startup_enabled = enabled
        self.stage.set_settings_state(
            startup_enabled=enabled,
            animation_enabled=self.state.settings.animation_enabled,
        )
        self._save_state()
        self._toast("开机启动已开启" if enabled else "开机启动已关闭")

    def toggle_animation(self) -> None:
        self.state.settings.animation_enabled = not self.state.settings.animation_enabled
        self.stage.set_settings_state(
            startup_enabled=self.state.settings.startup_enabled,
            animation_enabled=self.state.settings.animation_enabled,
        )
        self._save_state()
        self._toast("切换动画已开启" if self.state.settings.animation_enabled else "切换动画已关闭")

    AUTO_INTERVALS = (5, 15, 30, 60, 480, 1440, 10080)

    def toggle_auto_change(self) -> None:
        settings = self.state.settings
        settings.auto_change_enabled = not settings.auto_change_enabled
        self._configure_auto_timer()
        self.stage.set_auto_change_state(
            settings.auto_change_enabled,
            self._interval_label(settings.auto_interval_minutes),
        )
        self._save_state()
        status = "自动切换已开启" if settings.auto_change_enabled else "自动切换已关闭"
        self._toast(status)

    def set_auto_interval(self, minutes: int) -> None:
        if minutes not in self.AUTO_INTERVALS:
            return
        settings = self.state.settings
        settings.auto_interval_minutes = minutes
        self._configure_auto_timer()
        self.stage.set_auto_change_state(
            settings.auto_change_enabled,
            self._interval_label(minutes),
        )
        self._save_state()
        self.stage.show_settings_action_feedback("auto:interval")
        self._toast(f"自动切换周期：{self._interval_label(minutes)}")

    def cycle_auto_interval(self) -> None:
        current = self.state.settings.auto_interval_minutes
        try:
            index = self.AUTO_INTERVALS.index(current)
        except ValueError:
            index = -1
        self.set_auto_interval(self.AUTO_INTERVALS[(index + 1) % len(self.AUTO_INTERVALS)])

    def show_auto_interval_menu(self) -> None:
        if not self.state.settings.auto_change_enabled:
            return
        menu = QMenu(self)
        for minutes in self.AUTO_INTERVALS:
            action = menu.addAction(self._interval_label(minutes))
            action.setCheckable(True)
            action.setChecked(minutes == self.state.settings.auto_interval_minutes)
            action.triggered.connect(
                lambda checked=False, value=minutes: self.set_auto_interval(value)
            )
        menu.exec(self.mapToGlobal(QPoint(850, 330)))
    @staticmethod
    def _interval_label(minutes: int) -> str:
        return {5: "5 分钟", 15: "15 分钟", 30: "30 分钟", 60: "1 小时", 480: "8 小时", 1440: "每天", 10080: "每周"}.get(minutes, f"{minutes} 分钟")

    def clear_cache(self) -> None:
        self._prepared_previews.clear()
        if self.image_cache:
            self.image_cache.clear()
        self.stage.show_settings_action_feedback("cache")
        self._toast("本地预览缓存已清理")

    def _configure_auto_timer(self) -> None:
        self._auto_timer.stop()
        if self.state.settings.auto_change_enabled:
            self._auto_timer.start(max(5, self.state.settings.auto_interval_minutes) * 60_000)

    def _auto_change(self) -> None:
        self.next_wallpaper()
        QTimer.singleShot(350, self.apply_current)

    def _save_state(self) -> None:
        self._state_save_timer.stop()
        self.state.current_index = self.current_index
        self.state.wallpapers = list(self.wallpapers)
        self.state_store.save(self.state)

    def _schedule_state_save(self) -> None:
        self._state_save_timer.start(self.NAVIGATION_SAVE_DELAY_MS)

    def _toast(self, message: str, duration: int = 2200) -> None:
        self.stage.show_toast(message)
        self._toast_timer.start(duration)

    def request_exit(self) -> None:
        self._allow_close = True
        self.close()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.background_mode and not self._allow_close:
            event.ignore()
            self.hide()
            self._save_state()
            return
        self._taskbar_intensity_timer.stop()
        self._taskbar_handle_timer.stop()
        self._save_state()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._preview_executor.shutdown(wait=False, cancel_futures=True)
        self._navigation_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)
