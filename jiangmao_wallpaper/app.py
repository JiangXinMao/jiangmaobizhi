from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .cache import ImageCache
from .providers import default_provider_chain
from .services import WallpaperService
from .single_instance import SingleInstanceGuard
from .starter_pack import seed_starter_wallpapers
from .state import StateStore
from .ui.main_window import MainWindow
from .ui.resources import application_icon, tray_icon


PAGE_CHOICES = ("首页", "锁屏同步", "收藏", "历史", "设置")


def tray_menu_position(
    tray_geometry: QRect,
    menu_size: QSize,
    available_geometry: QRect,
    cursor_position: QPoint,
) -> QPoint:
    gap = 6
    width = max(1, menu_size.width())
    height = max(1, menu_size.height())
    available = available_geometry

    def clamp(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(value, maximum))

    if tray_geometry.isValid() and not tray_geometry.isEmpty():
        center = tray_geometry.center()
        if center.y() >= available.bottom():
            x = center.x() - width // 2
            y = tray_geometry.top() - height - gap
        elif center.y() <= available.top():
            x = center.x() - width // 2
            y = tray_geometry.bottom() + gap
        elif center.x() >= available.right():
            x = tray_geometry.left() - width - gap
            y = center.y() - height // 2
        else:
            x = tray_geometry.right() + gap
            y = center.y() - height // 2
    else:
        x = cursor_position.x() - width
        y = cursor_position.y() - height

    return QPoint(
        clamp(x, available.left(), available.right() - width + 1),
        clamp(y, available.top(), available.bottom() - height + 1),
    )


def data_directory() -> Path:
    directory = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "JiangMaoWallpaper"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _is_retired_wallpaper_source(wallpaper) -> bool:
    metadata = " ".join(
        (
            wallpaper.provider,
            wallpaper.startdate,
            wallpaper.preview_url,
            wallpaper.full_url,
            wallpaper.copyright_link,
        )
    ).casefold()
    retired_markers = (
        "wikimedia",
        "commons.wikimedia.org",
        "smk open",
        "iip.smk.dk",
        "open.smk.dk",
        "cleveland museum of art",
        "openaccess-cdn.clevelandart.org",
        "nasa",
    )
    retired_prefixes = ("commons-", "smk-", "cleveland-")
    return any(marker in metadata for marker in retired_markers) or wallpaper.startdate.casefold().startswith(
        retired_prefixes
    )


def purge_retired_wallpaper_sources(state_store: StateStore) -> int:
    state = state_store.load()
    retired = [
        wallpaper
        for wallpaper in state.wallpapers
        if _is_retired_wallpaper_source(wallpaper)
    ]
    if not retired:
        return 0
    retired_keys = {wallpaper.key for wallpaper in retired}
    state.wallpapers = [
        wallpaper
        for wallpaper in state.wallpapers
        if wallpaper.key not in retired_keys
    ]
    state.favorites.difference_update(retired_keys)
    state.current_index = min(
        state.current_index,
        max(0, len(state.wallpapers) - 1),
    )
    state_store.save(state)
    return len(retired)


def configure_logging(directory: Path) -> None:
    logging.basicConfig(
        filename=directory / "jiangmao-wallpaper.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )


def high_dpi_rounding_policy():
    return Qt.HighDpiScaleFactorRoundingPolicy.PassThrough


def parse_options(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--screenshot")
    parser.add_argument("--page", choices=(*PAGE_CHOICES, "关于"))
    parser.add_argument("--settings-section", choices=("general", "storage", "about"))
    parser.add_argument("--menu", action="store_true")
    parser.add_argument(
        "--hover-action",
        choices=[
            "prev",
            "favorite",
            "apply",
            "download",
            "next",
            "toggle-menu",
            "window:minimize",
            "window:maximize",
            "window:close",
        ],
    )
    parser.add_argument("--transition-progress", type=float)
    parser.add_argument("--menu-progress", type=float)
    parser.add_argument("--page-progress", type=float)
    return parser.parse_known_args(argv)


def build_window(autoload: bool = True) -> MainWindow:
    directory = data_directory()
    cache = ImageCache(directory / "cache")
    state_store = StateStore(directory / "state.json")
    purge_retired_wallpaper_sources(state_store)
    seed_starter_wallpapers(state_store, cache)
    provider_chain = default_provider_chain()
    return MainWindow(
        state_store=state_store,
        provider_chain=provider_chain,
        wallpaper_service=WallpaperService(cache),
        image_cache=cache,
        history_provider=provider_chain,
        background_mode=True,
        autoload=autoload,
    )


def run(argv=None, single_instance=None) -> int:
    options, qt_args = parse_options(argv)
    if not options.smoke_test and single_instance is None:
        single_instance = SingleInstanceGuard()
        if not single_instance.acquire():
            single_instance.request_activation()
            single_instance.activate_existing_window()
            return 0
    QApplication.setHighDpiScaleFactorRoundingPolicy(high_dpi_rounding_policy())
    app = QApplication([sys.argv[0], *qt_args])
    if single_instance is not None:
        app.aboutToQuit.connect(single_instance.close)
        app._jiangmao_single_instance = single_instance
    icon = application_icon()
    app.setWindowIcon(icon)
    app.setApplicationName("匠猫壁纸")
    app.setOrganizationName("JiangMao")
    configure_logging(data_directory())
    window = build_window(autoload=not options.smoke_test)
    window.setWindowIcon(icon)
    window.setWindowTitle("匠猫壁纸")

    def show_main_window():
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.setWindowState(
            (window.windowState() & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive
        )
        window.raise_()
        window.activateWindow()

    tray = None
    if not options.smoke_test and QSystemTrayIcon.isSystemTrayAvailable():
        app.setQuitOnLastWindowClosed(False)
        notification_icon = tray_icon()
        tray = QSystemTrayIcon(notification_icon, app)
        tray.setToolTip("匠猫壁纸")
        menu = QMenu()
        menu.setWindowIcon(notification_icon)
        brand_action = menu.addAction(notification_icon, "匠猫壁纸")
        menu.addSeparator()
        show_action = menu.addAction("显示主窗口")
        next_action = menu.addAction("立即切换下一张")
        apply_action = menu.addAction("立即应用当前壁纸")
        menu.addSeparator()
        auto_action = menu.addAction("自动切换")
        auto_action.setCheckable(True)
        interval_menu = menu.addMenu("切换周期")
        interval_actions = {}
        for minutes in window.AUTO_INTERVALS:
            action = interval_menu.addAction(window._interval_label(minutes))
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked=False, value=minutes: window.set_auto_interval(value)
            )
            interval_actions[minutes] = action
        lock_action = menu.addAction("锁屏同步")
        lock_action.setCheckable(True)
        folder_action = menu.addAction("打开下载目录")
        settings_action = menu.addAction("设置")
        menu.addSeparator()
        quit_action = menu.addAction("退出匠猫壁纸")

        brand_action.triggered.connect(show_main_window)
        show_action.triggered.connect(show_main_window)
        next_action.triggered.connect(window.next_wallpaper)
        apply_action.triggered.connect(window.apply_current)
        auto_action.triggered.connect(window.toggle_auto_change)
        lock_action.triggered.connect(window.toggle_lock_screen_sync)
        folder_action.triggered.connect(
            lambda: os.startfile(window.state.settings.download_dir)
        )
        settings_action.triggered.connect(
            lambda: (
                window.showNormal(),
                window.stage.set_page("设置"),
                window.raise_(),
                window.activateWindow(),
            )
        )
        quit_action.triggered.connect(lambda: (window.request_exit(), app.quit()))

        def sync_tray_menu():
            settings = window.state.settings
            auto_action.setChecked(settings.auto_change_enabled)
            interval_menu.setEnabled(settings.auto_change_enabled)
            lock_action.setChecked(settings.lock_screen_sync_enabled)
            for minutes, action in interval_actions.items():
                action.setChecked(minutes == settings.auto_interval_minutes)

        menu.aboutToShow.connect(sync_tray_menu)

        def show_tray_menu():
            sync_tray_menu()
            menu.ensurePolished()
            menu.adjustSize()
            tray_geometry = tray.geometry()
            anchor = (
                tray_geometry.center()
                if tray_geometry.isValid() and not tray_geometry.isEmpty()
                else QCursor.pos()
            )
            screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
            available = screen.availableGeometry() if screen else QRect(anchor, QSize(1, 1))
            position = tray_menu_position(
                tray_geometry,
                menu.sizeHint(),
                available,
                QCursor.pos(),
            )
            if menu.isVisible():
                menu.hide()
            menu.popup(position)

        def handle_tray_activation(reason):
            if reason == QSystemTrayIcon.ActivationReason.Context:
                QTimer.singleShot(0, show_tray_menu)
            elif reason in (
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
            ):
                show_main_window()

        tray.activated.connect(handle_tray_activation)
        tray.show()
        app._jiangmao_tray_icon = tray
        app._jiangmao_tray_menu = menu
    window.show()
    if single_instance is not None:
        activation_timer = QTimer(app)
        activation_timer.setInterval(50)
        activation_timer.timeout.connect(
            lambda: show_main_window()
            if single_instance.consume_activation_request()
            else None
        )
        activation_timer.start()
        app._jiangmao_activation_timer = activation_timer
    if options.page:
        window.stage.set_page(options.page)
    if options.settings_section:
        window.stage.set_page("设置")
        window.stage.set_settings_section(options.settings_section)
    if options.menu:
        window.stage.toggle_menu()
    if options.hover_action:
        window.stage.set_home_interaction(options.hover_action)
    if options.transition_progress is not None:
        progress = min(1.0, max(0.0, options.transition_progress))
        window.stage._animation.stop()
        window.stage._metadata_timer.stop()
        window.stage._metadata_animation.stop()
        window.stage.animation_progress = progress
        window.stage.metadata_progress = max(0.0, min(1.0, (progress - 0.18) / 0.82))
        window.stage.update()
    if options.menu_progress is not None:
        window.stage._menu_animation.stop()
        window.stage.menu_progress = min(1.0, max(0.0, options.menu_progress))
        window.stage.update()
    if options.page_progress is not None:
        window.stage._page_animation.stop()
        window.stage.page_progress = min(1.0, max(0.0, options.page_progress))
        window.stage.update()
    if options.smoke_test:
        def finish():
            if options.screenshot:
                target = Path(options.screenshot)
                target.parent.mkdir(parents=True, exist_ok=True)
                image = window.grab().toImage()
                if image.width() != 1200 or image.height() != 800:
                    image = image.scaled(1200, 800)
                image.save(str(target))
            window.close()
            app.quit()

        QTimer.singleShot(800, finish)
    return app.exec()
