from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt

from jiangmao_wallpaper import app
from jiangmao_wallpaper.app import (
    high_dpi_rounding_policy,
    parse_options,
    purge_retired_wallpaper_sources,
    tray_menu_position,
)
from jiangmao_wallpaper.models import AppState, Wallpaper
from jiangmao_wallpaper.state import StateStore
from jiangmao_wallpaper.ui.resources import tray_icon


def test_high_dpi_rounding_policy_uses_qt_enum():
    assert high_dpi_rounding_policy() == Qt.HighDpiScaleFactorRoundingPolicy.PassThrough


def test_smoke_options_accept_hover_action():
    options, qt_args = parse_options(["--smoke-test", "--hover-action", "apply"])

    assert options.hover_action == "apply"
    assert qt_args == []


def test_smoke_options_accept_window_hover_action():
    options, qt_args = parse_options(["--smoke-test", "--hover-action", "window:maximize"])

    assert options.hover_action == "window:maximize"
    assert qt_args == []


def test_smoke_options_accept_transition_progress():
    options, qt_args = parse_options(["--smoke-test", "--transition-progress", "0.5"])

    assert options.transition_progress == 0.5
    assert qt_args == []


def test_smoke_options_accept_menu_and_page_progress():
    options, qt_args = parse_options(
        ["--smoke-test", "--menu", "--menu-progress", "0.5", "--page-progress", "0.5"]
    )

    assert options.menu_progress == 0.5
    assert options.page_progress == 0.5
    assert qt_args == []


def test_smoke_options_accept_about_secondary_page():
    options, qt_args = parse_options(["--smoke-test", "--page", "关于"])

    assert options.page == "关于"
    assert qt_args == []


def test_smoke_options_accept_settings_section():
    options, qt_args = parse_options(["--smoke-test", "--settings-section", "about"])

    assert options.settings_section == "about"
    assert qt_args == []


@pytest.mark.parametrize("page", ("首页", "锁屏同步", "收藏", "历史", "设置"))
def test_parse_options_accepts_exact_page_choices(page):
    options, unknown = parse_options(["--page", page])

    assert unknown == []
    assert options.page == page


@pytest.mark.parametrize("page", ["故事", "今日"])
def test_parse_options_rejects_removed_pages(page):
    with pytest.raises(SystemExit):
        parse_options(["--page", page])


def test_pyinstaller_bundle_excludes_translucenttb():
    spec = (Path(__file__).parents[1] / "JiangMaoWallpaper.spec").read_text("utf-8")

    assert "third_party/translucenttb" not in spec.replace("\\", "/")


def test_release_has_no_installer_entrypoints():
    root = Path(__file__).parents[1]

    assert not (root / "scripts" / "build_launcher.ps1").exists()
    assert not (root / "scripts" / "JiangMaoLauncher.cs").exists()
    assert "安装程序" not in (root / "README.md").read_text("utf-8")


def test_source_reset_removes_retired_sources_and_preserves_photo_source(tmp_path):
    store = StateStore(tmp_path / "JiangMaoWallpaper" / "state.json")
    store.save(
        AppState(
            favorites={"commons-1", "smk-KKS2004-95", "photo-51510852367"},
            current_index=3,
            wallpapers=[
                Wallpaper(
                    title="Retired source",
                    copyright="CC BY-SA 4.0",
                    startdate="commons-1",
                    preview_url="https://upload.wikimedia.org/commons/preview.jpg",
                    full_url="https://upload.wikimedia.org/commons/full.jpg",
                    provider="Wikimedia Commons",
                ),
                Wallpaper(
                    title="Valley Landscape",
                    copyright="Public Domain",
                    startdate="smk-KKS2004-95",
                    preview_url="https://iip.smk.dk/preview.jpg",
                    full_url="https://iip.smk.dk/full.jpg",
                    provider="SMK Open",
                ),
                Wallpaper(
                    title="Vale of Kashmir",
                    copyright="CC0",
                    startdate="cleveland-171296",
                    preview_url="https://openaccess-cdn.clevelandart.org/preview.jpg",
                    full_url="https://openaccess-cdn.clevelandart.org/full.jpg",
                    provider="Cleveland Museum of Art",
                ),
                Wallpaper(
                    title="Going-to-the-Sun Road at Sunrise",
                    copyright="Public Domain",
                    startdate="photo-51510852367",
                    preview_url="https://live.staticflickr.com/preview.jpg",
                    full_url="https://live.staticflickr.com/full.jpg",
                    provider="National Park Photography",
                ),
            ]
        )
    )

    removed = purge_retired_wallpaper_sources(store)

    state = store.load()
    assert removed == 3
    assert [item.key for item in state.wallpapers] == ["photo-51510852367"]
    assert state.favorites == {"photo-51510852367"}
    assert state.current_index == 0


def test_source_reset_detects_retired_wikimedia_url_without_provider_name(tmp_path):
    store = StateStore(tmp_path / "JiangMaoWallpaper" / "state.json")
    store.save(
        AppState(
            wallpapers=[
                Wallpaper(
                    title="Retired remote image",
                    copyright="CC BY-SA 4.0",
                    startdate="legacy-image",
                    preview_url="https://upload.wikimedia.org/preview.jpg",
                    full_url="https://upload.wikimedia.org/full.jpg",
                    provider="Legacy source",
                )
            ],
        )
    )

    removed = purge_retired_wallpaper_sources(store)

    state = store.load()
    assert removed == 1
    assert state.wallpapers == []
    assert state.current_index == 0


def test_tray_icon_has_notification_area_size(qapp):
    icon = tray_icon()
    pixmap = icon.pixmap(16, 16)

    assert icon.isNull() is False
    assert pixmap.isNull() is False
    assert pixmap.deviceIndependentSize().width() == 16
    assert pixmap.deviceIndependentSize().height() == 16


@pytest.mark.parametrize(
    ("tray_geometry", "expected"),
    [
        (QRect(1800, 1040, 24, 24), QPoint(1672, 734)),
        (QRect(1800, -24, 24, 24), QPoint(1672, 5)),
        (QRect(1880, 500, 24, 24), QPoint(1666, 361)),
        (QRect(-24, 500, 24, 24), QPoint(5, 361)),
    ],
)
def test_tray_menu_stays_attached_for_every_taskbar_edge(
    tray_geometry, expected
):
    position = tray_menu_position(
        tray_geometry,
        QSize(208, 300),
        QRect(0, 0, 1880, 1040),
        QPoint(1800, 1000),
    )

    assert position == expected


def test_tray_menu_uses_cursor_when_shell_geometry_is_unavailable():
    position = tray_menu_position(
        QRect(),
        QSize(208, 300),
        QRect(0, 0, 1920, 1040),
        QPoint(1900, 1020),
    )

    assert position == QPoint(1692, 720)


def test_duplicate_launch_activates_existing_window_before_qapplication(monkeypatch):
    calls = []

    class DuplicateGuard:
        def acquire(self):
            calls.append("acquire")
            return False

        def request_activation(self):
            calls.append("request")
            return True

        def activate_existing_window(self):
            calls.append("activate")
            return True

    class ForbiddenApplication:
        @staticmethod
        def setHighDpiScaleFactorRoundingPolicy(policy):
            raise AssertionError("duplicate launch must exit before QApplication")

    monkeypatch.setattr(app, "SingleInstanceGuard", DuplicateGuard)
    monkeypatch.setattr(app, "QApplication", ForbiddenApplication)

    assert app.run([]) == 0
    assert calls == ["acquire", "request", "activate"]


def test_page_choices_are_exactly_the_five_visible_pages():
    assert app.PAGE_CHOICES == ("首页", "锁屏同步", "收藏", "历史", "设置")
