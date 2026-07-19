from dataclasses import replace
from pathlib import Path
from threading import Event
from time import perf_counter

import pytest
from PIL import Image
from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from jiangmao_wallpaper.models import AppSettings, AppState, Wallpaper
from jiangmao_wallpaper.providers import HistoryPage
from jiangmao_wallpaper.state import StateStore
from jiangmao_wallpaper.taskbar import TaskbarApplyResult
from jiangmao_wallpaper.ui import main_window
from jiangmao_wallpaper.ui.main_window import MainWindow
from jiangmao_wallpaper.ui.theme import HERO_INFO_RECT, RAIL_RECT
from jiangmao_wallpaper.ui.widgets import WallpaperStage, format_wallpaper_date


class EmptyProviderChain:
    def fetch(self, count=8, market="zh-CN"):
        raise AssertionError("autoload=False must not access the network")


class StubService:
    def download(self, wallpaper, output_dir):
        return Path(output_dir) / "saved.jpg"

    def apply(self, wallpaper):
        return Path(wallpaper.local_full or wallpaper.local_preview)


class StubLockScreenService:
    def __init__(self, result=None):
        self.result = result or Path("lockscreen.jpg")
        self.paths = []
        self.wallpapers = []

    def apply_path(self, path):
        self.paths.append(Path(path))
        return self.result

    def apply_wallpaper(self, wallpaper, cache):
        self.wallpapers.append(wallpaper)
        return self.result

class StubStartupManager:
    def __init__(self, enabled=False):
        self.enabled = enabled

    def is_enabled(self):
        return self.enabled

    def set_enabled(self, enabled):
        self.enabled = enabled


class StubTaskbarService:
    def __init__(self, result=None):
        self.result = result or TaskbarApplyResult(
            True, "transparent", "transparent", 2, 2, primary_applied=True
        )
        self.calls = []
        self.current_signature = (10, 20)

    def apply(self, mode, intensity, scope):
        self.calls.append((mode, intensity, scope))
        return replace(self.result, requested_mode=mode)

    def signature(self, include_secondary):
        if include_secondary:
            return self.current_signature
        return self.current_signature[:1]


class SequencedTaskbarService(StubTaskbarService):
    def __init__(self, *results):
        super().__init__(results[0])
        self.results = list(results)

    def apply(self, mode, intensity, scope):
        self.calls.append((mode, intensity, scope))
        result = self.results.pop(0)
        return replace(result, requested_mode=mode)


def create_wallpapers(tmp_path):
    wallpapers = []
    for index, color in enumerate(("#254D70", "#8C5E3C", "#345B43")):
        path = tmp_path / f"wallpaper-{index}.jpg"
        Image.new("RGB", (1200, 800), color).save(path)
        wallpapers.append(
            Wallpaper(
                title=f"测试壁纸 {index + 1}",
                copyright="测试来源",
                startdate=f"202607{10 + index}",
                preview_url=f"https://example.com/preview-{index}.jpg",
                full_url=f"https://example.com/full-{index}.jpg",
                local_preview=str(path),
                local_full=str(path),
            )
        )
    return wallpapers


def build_window(
    tmp_path,
    animation=True,
    startup_manager=None,
    animation_preference_version=1,
    history_provider=None,
    taskbar_service=None,
    taskbar_mode="default",
    taskbar_intensity=88,
    taskbar_restore_on_start=True,
    taskbar_all_displays=True,
    lock_screen_service=None,
):
    store = StateStore(tmp_path / "state.json")
    store.save(
        AppState(
            settings=AppSettings(
                animation_enabled=animation,
                animation_preference_version=animation_preference_version,
                taskbar_mode=taskbar_mode,
                taskbar_intensity=taskbar_intensity,
                taskbar_restore_on_start=taskbar_restore_on_start,
                taskbar_all_displays=taskbar_all_displays,
            )
        )
    )
    return MainWindow(
        state_store=store,
        provider_chain=EmptyProviderChain(),
        wallpaper_service=StubService(),
        startup_manager=startup_manager or StubStartupManager(),
        history_provider=history_provider if history_provider is not None else False,
        lock_screen_service=lock_screen_service or StubLockScreenService(),
        taskbar_service=(
            taskbar_service
            if taskbar_service is not None
            else StubTaskbarService()
        ),
        autoload=False,
    )


def send_mouse_move(widget, position: QPoint) -> None:
    local_position = QPointF(position)
    global_position = QPointF(widget.mapToGlobal(position))
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        local_position,
        global_position,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def test_stage_has_placeholder_before_network(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.show()

    assert window.size().width() == 960
    assert window.size().height() == 640
    assert window.stage.has_visible_background
    assert window.stage.animation_duration == 280


def test_window_is_frameless_and_handles_controls(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)

    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    window._handle_action("window:maximize")
    assert window.isMaximized()
    window._handle_action("window:maximize")
    assert not window.isMaximized()


def test_frameless_window_preserves_edge_resize(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.resize(960, 640)

    assert window.resize_hit_test(QPoint(2, 2)) == 13
    assert window.resize_hit_test(QPoint(958, 2)) == 14
    assert window.resize_hit_test(QPoint(2, 638)) == 16
    assert window.resize_hit_test(QPoint(958, 638)) == 17
    assert window.resize_hit_test(QPoint(480, 320)) == 1


def test_native_physical_coordinates_are_scaled_before_resize_hit_test(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.resize(960, 640)

    physical_apply_center = QPoint(863, 726)
    physical_window_origin = QPoint(10, 10)
    local = main_window.physical_to_logical_local_point(
        physical_apply_center,
        physical_window_origin,
        device_pixel_ratio=1.25,
    )

    assert local == QPoint(682, 573)
    assert window.resize_hit_test(local) == window.HTCLIENT


def test_gallery_home_has_single_primary_action(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)

    assert window.stage.home_control_ids() == ("prev", "favorite", "apply", "download", "next")
    assert window.stage.visible_navigation_labels() == ()
    assert window.stage.menu_open is False
    assert window.stage.primary_action_label() == "设置壁纸"
    assert window.stage.primary_action_font_size() == 14


def test_home_title_prefers_headline_then_title(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    wallpapers = create_wallpapers(tmp_path)
    wallpapers[0].headline = "官方主题大字"
    window.set_wallpapers(wallpapers)

    assert window.stage.hero_title() == "官方主题大字"

    wallpapers[0].headline = ""
    window.stage.current_wallpaper = wallpapers[0]
    assert window.stage.hero_title() == wallpapers[0].title


def test_bottom_menu_has_safe_separation_and_alignment(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)

    menu = window.stage.menu_button_rect()
    panel = window.stage.menu_panel_rect()
    controls = dict(window.stage.window_control_rects())
    next_button = window.stage.home_control_rects()["next"]
    island = window.stage.control_island_rect()

    assert menu.getRect() == (1038, 690, 52, 52)
    assert panel.getRect() == (682, 474, 408, 204)
    assert window.stage.window_control_group_rect().getRect() == (1062, 8, 130, 40)
    assert controls["window:minimize"].getRect() == (1072, 8, 40, 40)
    assert controls["window:maximize"].getRect() == (1112, 8, 40, 40)
    assert controls["window:close"].getRect() == (1152, 8, 40, 40)
    assert window.stage.window_brand_signal_rect().getRect() == (1066, 17, 2, 22)
    assert controls["window:maximize"].left() == controls["window:minimize"].right()
    assert controls["window:close"].left() == controls["window:maximize"].right()
    assert menu.left() - next_button.right() == 12
    assert island.right() - menu.right() == 6
    assert menu.right() == panel.right()
    assert menu.top() - panel.bottom() == 12
    assert 1200 - controls["window:close"].right() == 8
    assert not menu.intersects(next_button)


def test_gallery_matrix_opens_lock_screen_sync(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.stage.toggle_menu()
    items = window.stage.menu_item_rects()
    assert items["锁屏同步"].getRect() == (698, 522, 184, 64)
    assert window.stage.menu_action_at(items["锁屏同步"].center()) == "page:锁屏同步"

def test_taskbar_page_exposes_three_modes_and_controls(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.stage.resize(1200, 800)
    window.stage.set_page("任务栏美化")

    assert set(window.stage.taskbar_mode_rects()) == {"default", "transparent", "frosted"}
    assert window.stage.taskbar_strength_rect().getRect() == (744, 300, 330, 28)
    assert set(window.stage.taskbar_switch_rects()) == {"restore", "displays"}


def test_taskbar_page_stacks_preview_above_contained_controls_at_minimum_size(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.resize(720, 480)
    stage.set_settings_state(
        startup_enabled=False, animation_enabled=False, animate=False
    )
    stage.set_page("任务栏美化")
    stage.set_taskbar_state(
        mode="transparent",
        intensity=60,
        restore_on_start=True,
        all_displays=True,
    )
    stage.show()

    preview = stage.taskbar_preview_rect()
    controls = [
        *stage.taskbar_mode_rects().values(),
        stage.taskbar_strength_rect(),
        *stage.taskbar_switch_rects().values(),
        stage.taskbar_reset_rect(),
    ]
    page = stage.taskbar_page_rect()

    assert stage.taskbar_layout_is_compact() is True
    assert all(preview.bottom() < control.top() for control in controls)
    assert page.contains(preview)
    assert all(page.contains(control) for control in controls)
    for first, second in zip(controls, controls[1:]):
        assert not first.intersects(second)


def test_lock_screen_controls_remain_clickable_at_minimum_size(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.resize(720, 480)
    stage.set_settings_state(startup_enabled=False, animation_enabled=False, animate=False)
    stage.set_page("锁屏同步")
    actions = []
    stage.action_requested.connect(actions.append)
    stage.show()
    scale, offset_x, offset_y = stage._viewport()
    def physical_center(box):
        center = box.center()
        return QPoint(round(offset_x + center.x() * scale), round(offset_y + center.y() * scale))
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=physical_center(stage.lock_screen_toggle_rect()))
    assert actions.pop() == "lockscreen:toggle"
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=physical_center(stage.lock_screen_sync_rect()))
    assert actions.pop() == "lockscreen:sync"

def test_taskbar_state_clamps_intensity_and_snaps_without_animation(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.set_settings_state(startup_enabled=False, animation_enabled=False, animate=False)

    stage.set_taskbar_state(
        mode="frosted",
        intensity=120,
        restore_on_start=True,
        all_displays=False,
        status="已应用",
    )

    assert stage.taskbar_mode == "frosted"
    assert stage.taskbar_intensity == 100
    assert stage.taskbar_restore_on_start is True
    assert stage.taskbar_all_displays is False
    assert stage.taskbar_status == "已应用"
    assert stage.taskbar_toggle_progress("restore") == 1.0
    assert stage.taskbar_toggle_progress("displays") == 0.0


def test_taskbar_state_normalizes_unknown_mode(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)

    stage.set_taskbar_state(
        mode="unknown",
        intensity=88,
        restore_on_start=True,
        all_displays=True,
    )

    assert stage.taskbar_mode == "default"


def test_lock_screen_controls_emit_exact_action_contracts(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.resize(1200, 800)
    stage.set_settings_state(startup_enabled=False, animation_enabled=False, animate=False)
    stage.set_page("锁屏同步")
    actions = []
    stage.action_requested.connect(actions.append)
    stage.show()
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=stage.lock_screen_toggle_rect().center().toPoint())
    qtbot.mouseClick(stage, Qt.MouseButton.LeftButton, pos=stage.lock_screen_sync_rect().center().toPoint())
    assert actions == ["lockscreen:toggle", "lockscreen:sync"]

def test_lock_screen_state_updates_toggle_and_status(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.set_settings_state(startup_enabled=False, animation_enabled=False, animate=False)
    stage.set_lock_screen_state(True, "已同步", animate=False)
    assert stage.lock_screen_sync_enabled is True
    assert stage.lock_screen_status == "已同步"
    assert stage.taskbar_toggle_progress("restore") == 1.0

def test_taskbar_mode_success_persists_and_updates_stage(qtbot, tmp_path):
    service = StubTaskbarService()
    window = build_window(tmp_path, taskbar_service=service)
    qtbot.addWidget(window)

    window._handle_action("taskbar:mode:transparent")

    assert service.calls[-1] == ("transparent", 88, "all")
    persisted = StateStore(tmp_path / "state.json").load().settings
    assert persisted.taskbar_mode == "transparent"
    assert window.stage.taskbar_mode == "transparent"


def test_taskbar_failure_rolls_back_without_persisting(qtbot, tmp_path):
    failure = TaskbarApplyResult(
        False,
        "transparent",
        "transparent",
        0,
        2,
        primary_applied=False,
        error="调用失败",
    )
    window = build_window(
        tmp_path, taskbar_service=StubTaskbarService(failure)
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:mode:transparent")

    assert window.state.settings.taskbar_mode == "default"
    assert window.stage.taskbar_mode == "default"
    persisted = StateStore(tmp_path / "state.json").load().settings
    assert persisted.taskbar_mode == "default"


def test_taskbar_mode_does_not_persist_when_only_secondaries_apply(qtbot, tmp_path):
    secondary_only = TaskbarApplyResult(
        True,
        "transparent",
        "transparent",
        2,
        2,
        primary_applied=False,
    )
    rollback = TaskbarApplyResult(
        True, "default", "default", 2, 2, primary_applied=True
    )
    service = SequencedTaskbarService(secondary_only, rollback)
    window = build_window(tmp_path, taskbar_service=service)
    qtbot.addWidget(window)

    window._handle_action("taskbar:mode:transparent")

    assert service.calls == [
        ("transparent", 88, "all"),
        ("default", 88, "all"),
    ]
    assert window.state.settings.taskbar_mode == "default"
    assert StateStore(tmp_path / "state.json").load().settings.taskbar_mode == "default"


@pytest.mark.parametrize(
    ("old_mode", "new_mode", "all_displays", "expected_calls"),
    [
        (
            "default",
            "transparent",
            True,
            [("transparent", 55, "all"), ("default", 55, "all")],
        ),
        (
            "transparent",
            "default",
            False,
            [("default", 55, "primary"), ("transparent", 55, "primary")],
        ),
    ],
)
def test_failed_mode_transition_compensates_previous_appearance(
    qtbot, tmp_path, old_mode, new_mode, all_displays, expected_calls
):
    failure = TaskbarApplyResult(
        False, new_mode, new_mode, 1, 2, primary_applied=False, error="native failure"
    )
    rollback = TaskbarApplyResult(
        True, old_mode, old_mode, 2, 2, primary_applied=True
    )
    service = SequencedTaskbarService(failure, rollback)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode=old_mode,
        taskbar_intensity=55,
        taskbar_restore_on_start=False,
        taskbar_all_displays=all_displays,
    )
    qtbot.addWidget(window)

    window.apply_taskbar_mode(new_mode)

    assert service.calls == expected_calls
    assert window.state.settings.taskbar_mode == old_mode
    assert "已恢复" in window.stage._toast


def test_failed_intensity_transition_compensates_previous_intensity(qtbot, tmp_path):
    failure = TaskbarApplyResult(
        False,
        "transparent",
        "transparent",
        1,
        2,
        primary_applied=False,
        error="native failure",
    )
    rollback = TaskbarApplyResult(
        True, "transparent", "transparent", 2, 2, primary_applied=True
    )
    service = SequencedTaskbarService(failure, rollback)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
        taskbar_intensity=55,
        taskbar_restore_on_start=False,
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:intensity:90")
    qtbot.waitUntil(lambda: len(service.calls) == 2, timeout=500)

    assert service.calls == [
        ("transparent", 90, "all"),
        ("transparent", 55, "all"),
    ]
    assert window.state.settings.taskbar_intensity == 55


@pytest.mark.parametrize(
    ("all_displays", "result", "scope"),
    [
        (
            True,
            TaskbarApplyResult(
                True,
                "default",
                "default",
                2,
                2,
                primary_applied=False,
            ),
            "all",
        ),
        (
            False,
            TaskbarApplyResult(
                False,
                "default",
                "default",
                0,
                0,
                primary_applied=False,
            ),
            "primary",
        ),
    ],
)
def test_taskbar_reset_requires_primary_outcome(
    qtbot, tmp_path, all_displays, result, scope
):
    rollback_count = 2 if scope == "all" else 1
    rollback = TaskbarApplyResult(
        True,
        "transparent",
        "transparent",
        rollback_count,
        rollback_count,
        primary_applied=True,
    )
    service = SequencedTaskbarService(result, rollback)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
        taskbar_restore_on_start=False,
        taskbar_all_displays=all_displays,
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:reset")

    assert service.calls == [
        ("default", 88, scope),
        ("transparent", 88, scope),
    ]
    assert window.state.settings.taskbar_mode == "transparent"
    assert StateStore(tmp_path / "state.json").load().settings.taskbar_mode == "transparent"


def test_enabling_display_sync_requires_primary_outcome(qtbot, tmp_path):
    secondary_only = TaskbarApplyResult(
        True,
        "transparent",
        "transparent",
        2,
        2,
        primary_applied=False,
    )
    service = StubTaskbarService(secondary_only)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
        taskbar_all_displays=False,
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:displays-toggle")

    assert window.state.settings.taskbar_all_displays is False
    assert StateStore(tmp_path / "state.json").load().settings.taskbar_all_displays is False


def test_taskbar_partial_reset_keeps_previous_mode(qtbot, tmp_path):
    partial = TaskbarApplyResult(
        True,
        "default",
        "default",
        1,
        2,
        primary_applied=True,
        error="部分显示器未更新",
    )
    service = StubTaskbarService(partial)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:reset")

    assert window.state.settings.taskbar_mode == "transparent"
    assert window.stage.taskbar_mode == "transparent"
    assert StateStore(tmp_path / "state.json").load().settings.taskbar_mode == "transparent"
    assert "1/2" in window.stage._toast


def test_taskbar_intensity_is_debounced_and_persisted(qtbot, tmp_path):
    service = StubTaskbarService()
    window = build_window(tmp_path, taskbar_service=service)
    qtbot.addWidget(window)
    window._handle_action("taskbar:mode:transparent")
    service.calls.clear()

    window._handle_action("taskbar:intensity:35")
    window._handle_action("taskbar:intensity:72")

    assert window.stage.taskbar_intensity == 72
    assert service.calls == []
    qtbot.waitUntil(lambda: len(service.calls) == 1, timeout=500)
    assert service.calls == [("transparent", 72, "all")]
    persisted = StateStore(tmp_path / "state.json").load().settings
    assert persisted.taskbar_intensity == 72


def test_disabling_all_displays_restores_only_secondary_taskbars(qtbot, tmp_path):
    service = StubTaskbarService()
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:displays-toggle")

    assert service.calls[-1] == ("default", 88, "secondary")
    assert window.state.settings.taskbar_all_displays is False
    assert window.stage.taskbar_all_displays is False


def test_partial_secondary_restore_keeps_all_displays_enabled(qtbot, tmp_path):
    partial = TaskbarApplyResult(
        True,
        "default",
        "default",
        1,
        2,
        primary_applied=False,
        error="部分显示器未更新",
    )
    service = StubTaskbarService(partial)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:displays-toggle")

    assert window.state.settings.taskbar_all_displays is True
    assert window.stage.taskbar_all_displays is True
    assert service.signature(True) == window._taskbar_signature
    assert "1/2" in window.stage._toast


def test_zero_secondary_taskbars_allows_disabling_sync(qtbot, tmp_path):
    no_secondaries = TaskbarApplyResult(
        True, "default", "default", 0, 0, primary_applied=False
    )
    service = StubTaskbarService(no_secondaries)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:displays-toggle")

    assert window.state.settings.taskbar_all_displays is False
    assert window.stage.taskbar_all_displays is False


def test_failed_disable_display_sync_restores_secondary_effect(qtbot, tmp_path):
    failure = TaskbarApplyResult(
        True, "default", "default", 1, 2, primary_applied=False, error="partial"
    )
    rollback = TaskbarApplyResult(
        True, "transparent", "transparent", 2, 2, primary_applied=False
    )
    service = SequencedTaskbarService(failure, rollback)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
        taskbar_intensity=63,
        taskbar_restore_on_start=False,
        taskbar_all_displays=True,
    )
    qtbot.addWidget(window)

    window.toggle_taskbar_displays()

    assert service.calls == [
        ("default", 63, "secondary"),
        ("transparent", 63, "secondary"),
    ]
    assert window.state.settings.taskbar_all_displays is True


def test_enabling_display_sync_applies_current_effect_before_persisting(qtbot, tmp_path):
    service = StubTaskbarService(
        TaskbarApplyResult(
            True,
            "transparent",
            "transparent",
            2,
            2,
            primary_applied=True,
        )
    )
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
        taskbar_all_displays=False,
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:displays-toggle")

    assert service.calls[-1] == ("transparent", 88, "all")
    assert window.state.settings.taskbar_all_displays is True
    assert StateStore(tmp_path / "state.json").load().settings.taskbar_all_displays is True


def test_enabling_display_sync_rolls_back_on_partial_apply(qtbot, tmp_path):
    partial = TaskbarApplyResult(
        True,
        "frosted",
        "frosted-compat",
        1,
        2,
        primary_applied=True,
        error="部分显示器未更新",
    )
    primary_rollback = TaskbarApplyResult(
        True, "frosted", "frosted", 1, 1, primary_applied=True
    )
    secondary_rollback = TaskbarApplyResult(
        True, "default", "default", 1, 1, primary_applied=False
    )
    service = SequencedTaskbarService(
        partial, primary_rollback, secondary_rollback
    )
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="frosted",
        taskbar_restore_on_start=False,
        taskbar_all_displays=False,
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:displays-toggle")

    assert window.state.settings.taskbar_all_displays is False
    assert window.stage.taskbar_all_displays is False
    assert service.calls == [
        ("frosted", 88, "all"),
        ("frosted", 88, "primary"),
        ("default", 88, "secondary"),
    ]
    assert "已恢复" in window.stage.taskbar_status
    assert "1/2" in window.stage._toast


def test_enabling_display_sync_in_default_mode_only_saves_preference(qtbot, tmp_path):
    service = StubTaskbarService()
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_all_displays=False,
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:displays-toggle")

    assert service.calls == []
    assert window.state.settings.taskbar_all_displays is True
    persisted = StateStore(tmp_path / "state.json").load().settings
    assert persisted.taskbar_all_displays is True
    assert window.stage.taskbar_all_displays is True
    assert window.stage._toast


def test_disabling_display_sync_in_default_mode_only_saves_preference(
    qtbot, tmp_path
):
    service = StubTaskbarService()
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="default",
        taskbar_all_displays=True,
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:displays-toggle")

    assert service.calls == []
    assert window.state.settings.taskbar_all_displays is False
    persisted = StateStore(tmp_path / "state.json").load().settings
    assert persisted.taskbar_all_displays is False
    assert window.stage.taskbar_all_displays is False
    assert window.stage._toast


def test_failed_enable_display_sync_restores_primary_only_topology(qtbot, tmp_path):
    failure = TaskbarApplyResult(
        True,
        "frosted",
        "frosted-compat",
        1,
        2,
        primary_applied=True,
        error="partial",
    )
    primary_rollback = TaskbarApplyResult(
        True, "frosted", "frosted", 1, 1, primary_applied=True
    )
    secondary_rollback = TaskbarApplyResult(
        True, "default", "default", 1, 1, primary_applied=False
    )
    service = SequencedTaskbarService(
        failure, primary_rollback, secondary_rollback
    )
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="frosted",
        taskbar_intensity=72,
        taskbar_restore_on_start=False,
        taskbar_all_displays=False,
    )
    qtbot.addWidget(window)

    window.toggle_taskbar_displays()

    assert service.calls == [
        ("frosted", 72, "all"),
        ("frosted", 72, "primary"),
        ("default", 72, "secondary"),
    ]
    assert window.state.settings.taskbar_all_displays is False


def test_failed_enable_display_sync_inspects_both_rollback_failures(
    qtbot, tmp_path, caplog
):
    transition = TaskbarApplyResult(
        False,
        "frosted",
        "frosted-compat",
        1,
        2,
        primary_applied=True,
        error="transition failed",
    )
    primary_rollback = TaskbarApplyResult(
        False,
        "frosted",
        "frosted",
        0,
        1,
        primary_applied=False,
        error="primary rollback failed",
    )
    secondary_rollback = TaskbarApplyResult(
        False,
        "default",
        "default",
        0,
        1,
        primary_applied=False,
        error="secondary rollback failed",
    )
    service = SequencedTaskbarService(
        transition, primary_rollback, secondary_rollback
    )
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="frosted",
        taskbar_intensity=72,
        taskbar_restore_on_start=False,
        taskbar_all_displays=False,
    )
    qtbot.addWidget(window)

    with caplog.at_level("ERROR"):
        window.toggle_taskbar_displays()

    assert service.calls == [
        ("frosted", 72, "all"),
        ("frosted", 72, "primary"),
        ("default", 72, "secondary"),
    ]
    assert "primary rollback failed" in caplog.text
    assert "secondary rollback failed" in caplog.text
    assert window.state.settings.taskbar_all_displays is False
    status = window.stage.taskbar_status
    assert "rollback" in status.lower() or "回滚失败" in status


def test_compensation_failure_surfaces_inconsistent_taskbar_warning(
    qtbot, tmp_path, caplog
):
    failure = TaskbarApplyResult(
        False,
        "transparent",
        "transparent",
        1,
        2,
        primary_applied=False,
        error="native failure",
    )
    rollback_failure = TaskbarApplyResult(
        False,
        "default",
        "default",
        0,
        2,
        primary_applied=False,
        error="rollback native failure",
    )
    service = SequencedTaskbarService(failure, rollback_failure)
    window = build_window(tmp_path, taskbar_service=service)
    qtbot.addWidget(window)

    with caplog.at_level("ERROR"):
        window.apply_taskbar_mode("transparent")

    assert service.calls == [
        ("transparent", 88, "all"),
        ("default", 88, "all"),
    ]
    assert "回滚失败" in window.stage.taskbar_status
    assert "回滚失败" in window.stage._toast
    assert "compensation failed" in caplog.text.lower()
    assert "rollback native failure" in caplog.text


@pytest.mark.parametrize(
    ("all_displays", "expected_calls"),
    [
        (
            True,
            [
                ("default", 64, "secondary"),
                ("transparent", 64, "secondary"),
            ],
        ),
        (
            False,
            [
                ("transparent", 64, "all"),
                ("transparent", 64, "primary"),
                ("default", 64, "secondary"),
            ],
        ),
    ],
)
def test_display_toggle_compensation_failure_keeps_old_setting_and_warns(
    qtbot, tmp_path, all_displays, expected_calls
):
    enabling = not all_displays
    transition_mode = "transparent" if enabling else "default"
    transition = TaskbarApplyResult(
        False,
        transition_mode,
        transition_mode,
        1,
        2,
        primary_applied=enabling,
        error="transition failed",
    )
    rollback_failure = TaskbarApplyResult(
        False,
        "transparent",
        "transparent",
        0,
        1,
        primary_applied=False,
        error="rollback failed",
    )
    results = (
        (transition, rollback_failure, rollback_failure)
        if enabling
        else (transition, rollback_failure)
    )
    service = SequencedTaskbarService(*results)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
        taskbar_intensity=64,
        taskbar_restore_on_start=False,
        taskbar_all_displays=all_displays,
    )
    qtbot.addWidget(window)

    window.toggle_taskbar_displays()

    assert service.calls == expected_calls
    assert window.state.settings.taskbar_all_displays is all_displays
    assert "回滚失败" in window.stage.taskbar_status


def test_partial_non_default_apply_persists_mode_and_reports_count(qtbot, tmp_path):
    partial = TaskbarApplyResult(
        True,
        "frosted",
        "frosted-compat",
        1,
        2,
        primary_applied=True,
        error="部分显示器未更新",
    )
    window = build_window(
        tmp_path, taskbar_service=StubTaskbarService(partial)
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:mode:frosted")

    assert window.state.settings.taskbar_mode == "frosted"
    assert "兼容磨砂" in window.stage.taskbar_status
    assert "1/2" in window.stage.taskbar_status
    assert "1/2" in window.stage._toast


def test_malformed_taskbar_mode_action_is_rejected(qtbot, tmp_path):
    service = StubTaskbarService()
    window = build_window(tmp_path, taskbar_service=service)
    qtbot.addWidget(window)

    window._handle_action("taskbar:mode:junk:transparent")

    assert service.calls == []
    assert window.state.settings.taskbar_mode == "default"


def test_pending_intensity_failure_restores_persisted_and_preview_value(qtbot, tmp_path):
    failure = TaskbarApplyResult(
        False,
        "transparent",
        "transparent",
        0,
        2,
        primary_applied=False,
        error="调用失败",
    )
    rollback = TaskbarApplyResult(
        True, "transparent", "transparent", 2, 2, primary_applied=True
    )
    service = SequencedTaskbarService(failure, rollback)
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
        taskbar_intensity=55,
        taskbar_restore_on_start=False,
    )
    qtbot.addWidget(window)

    window._handle_action("taskbar:intensity:90")
    qtbot.waitUntil(lambda: len(service.calls) == 2, timeout=500)

    assert window.state.settings.taskbar_intensity == 55
    assert window.stage.taskbar_intensity == 55
    assert StateStore(tmp_path / "state.json").load().settings.taskbar_intensity == 55


def test_startup_restore_reapplies_saved_effect(qtbot, tmp_path):
    service = StubTaskbarService(
        TaskbarApplyResult(
            True,
            "transparent",
            "transparent",
            2,
            2,
            primary_applied=True,
        )
    )
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
        taskbar_intensity=72,
    )
    qtbot.addWidget(window)

    qtbot.waitUntil(lambda: bool(service.calls), timeout=500)

    assert service.calls == [("transparent", 72, "all")]


def test_close_does_not_restore_taskbar(qtbot, tmp_path):
    service = StubTaskbarService()
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="transparent",
        taskbar_restore_on_start=False,
    )
    qtbot.addWidget(window)

    window.close()

    assert service.calls == []


def test_restore_toggle_only_updates_persisted_preference(qtbot, tmp_path):
    service = StubTaskbarService()
    window = build_window(tmp_path, taskbar_service=service)
    qtbot.addWidget(window)

    window._handle_action("taskbar:restore-toggle")

    assert service.calls == []
    assert window.stage.taskbar_restore_on_start is False
    persisted = StateStore(tmp_path / "state.json").load().settings
    assert persisted.taskbar_restore_on_start is False


def test_taskbar_handles_reapply_only_after_signature_changes(qtbot, tmp_path):
    service = StubTaskbarService()
    window = build_window(tmp_path, taskbar_service=service)
    qtbot.addWidget(window)
    window._handle_action("taskbar:mode:frosted")
    service.calls.clear()

    window._check_taskbar_handles()
    assert service.calls == []

    service.current_signature = (10, 20, 30)
    window._check_taskbar_handles()
    assert service.calls == [("frosted", 88, "all")]


@pytest.mark.parametrize("restore_on_start", [False, True])
def test_taskbar_handle_change_reapplies_saved_effect_while_running(
    qtbot, tmp_path, restore_on_start
):
    service = StubTaskbarService()
    window = build_window(
        tmp_path,
        taskbar_service=service,
        taskbar_mode="frosted",
        taskbar_restore_on_start=restore_on_start,
    )
    qtbot.addWidget(window)
    service.calls.clear()

    window._check_taskbar_handles()
    assert service.calls == []

    service.current_signature = (10, 20, 30)
    window._check_taskbar_handles()

    assert service.calls == [("frosted", 88, "all")]


def test_primary_missing_result_never_shows_applied_status(qtbot, tmp_path):
    result = TaskbarApplyResult(
        False,
        "transparent",
        "transparent",
        1,
        1,
        primary_applied=False,
        error="主任务栏未更新",
    )
    rollback = TaskbarApplyResult(
        True, "default", "default", 1, 1, primary_applied=True
    )
    window = build_window(
        tmp_path,
        taskbar_service=SequencedTaskbarService(result, rollback),
    )
    qtbot.addWidget(window)

    window.apply_taskbar_mode("transparent")

    assert "已应用" not in window.stage.taskbar_status
    assert window.stage.taskbar_status == "主任务栏未更新；已恢复原设置"


def test_split_frosted_result_persists_and_keeps_stage_aligned(qtbot, tmp_path):
    result = TaskbarApplyResult(
        True,
        "frosted",
        "mixed",
        2,
        2,
        primary_applied=True,
    )
    window = build_window(
        tmp_path, taskbar_service=StubTaskbarService(result)
    )
    qtbot.addWidget(window)

    assert window.apply_taskbar_mode("frosted") is True

    persisted = StateStore(tmp_path / "state.json").load().settings
    assert persisted.taskbar_mode == "frosted"
    assert window.stage.taskbar_mode == "frosted"
    assert window.stage.taskbar_status == "混合磨砂"
    assert "部分任务栏使用兼容模糊" in window.stage._toast


def test_gallery_matrix_menu_tracks_hover_and_click(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.show()
    window._handle_action("toggle-menu")
    settings = window.stage.menu_item_rects()["设置"].center()
    widget_point = QPoint(round(settings.x() * 0.8), round(settings.y() * 0.8))

    send_mouse_move(window.stage, widget_point)

    assert window.stage._hover_action == "page:设置"
    qtbot.mouseClick(window.stage, Qt.MouseButton.LeftButton, pos=widget_point)
    assert window.stage.current_page == "设置"
    assert window.stage.menu_open is False


def test_window_control_group_uses_single_brand_rail_surface(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)

    assert window.stage.window_control_group_radius() == 14
    assert window.stage.window_control_group_rect().contains(
        dict(window.stage.window_control_rects())["window:close"]
    )


def test_all_window_controls_have_green_hover_feedback(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)

    fills = []
    for action in ("window:minimize", "window:maximize", "window:close"):
        fill = window.stage.window_hover_fill(action)
        fills.append(fill.rgba())
        assert fill.alpha() >= 112
        assert fill.green() > fill.red()
        assert fill.green() > fill.blue()

    assert len(set(fills)) == 1


def test_window_control_hover_requests_repaint(qtbot, tmp_path):
    window = build_window(tmp_path, animation=True)
    qtbot.addWidget(window)
    repaint_requests = []
    window.stage.update = lambda: repaint_requests.append(True)

    window.stage.set_home_interaction("window:minimize")

    assert window.stage._hover_action == "window:minimize"
    assert repaint_requests


def test_widescreen_background_covers_viewport_without_side_bars(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.resize(1920, 1080)
    stage.show()
    qtbot.waitExposed(stage)

    rendered = stage.grab().toImage()
    black_bar_color = QColor("#07090C")

    assert rendered.pixelColor(8, rendered.height() // 2) != black_bar_color
    assert rendered.pixelColor(rendered.width() - 9, rendered.height() // 2) != black_bar_color


def test_widescreen_has_no_central_shadow_seam(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.resize(1600, 900)
    white = QImage(1200, 800, QImage.Format.Format_RGB32)
    white.fill(QColor("#FFFFFF"))
    stage._current_image = white
    rendered = QImage(1600, 900, QImage.Format.Format_RGB32)
    stage.render(rendered)

    central_canvas_left = 125
    outside = rendered.pixelColor(central_canvas_left - 2, 120).lightness()
    inside = rendered.pixelColor(central_canvas_left + 2, 120).lightness()

    assert abs(outside - inside) <= 3


def test_home_controls_use_magnetic_group_geometry(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)

    groups = window.stage.home_control_group_rects()

    assert groups["navigation"].getRect() == (586, 684, 128, 64)
    assert groups["tools"].getRect() == (904, 684, 192, 64)
    controls = window.stage.home_control_rects()
    assert controls["favorite"].left() - controls["prev"].right() == 12
    assert controls["apply"].left() - groups["navigation"].right() == 12
    assert groups["tools"].left() - controls["apply"].right() == 12
    assert controls["next"].left() - controls["download"].right() == 12


def test_window_icons_share_centered_visual_boxes(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)

    for _, button in window.stage.window_control_rects():
        icon = window.stage.window_icon_rect(button)
        assert icon.size().toTuple() == (12.0, 12.0)
        assert icon.center() == button.center()

    minimize_button = dict(window.stage.window_control_rects())["window:minimize"]
    assert window.stage.minimize_icon_y(minimize_button) == minimize_button.center().y()


def test_maximize_icon_switches_to_restore_when_window_is_maximized(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)

    assert window.stage.maximize_icon_mode() == "maximize"

    window.showMaximized()

    assert window.stage.maximize_icon_mode() == "restore"


def test_menu_button_has_hover_feedback(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.show()

    send_mouse_move(window.stage, QPoint(400, 300))
    send_mouse_move(window.stage, QPoint(856, 573))

    qtbot.waitUntil(lambda: window.stage._hover_action == "toggle-menu", timeout=500)


def test_home_control_has_hover_feedback(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.show()

    send_mouse_move(window.stage, QPoint(400, 300))
    send_mouse_move(window.stage, QPoint(510, 573))

    qtbot.waitUntil(lambda: window.stage._hover_action == "prev", timeout=500)


def test_dock_motion_targets_hover_neighbors_and_press(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)

    window.stage.set_home_interaction("favorite")
    assert window.stage.home_control_scale("favorite") == 1.10
    assert window.stage.home_control_offset_y("favorite") == -3.0
    assert window.stage.home_control_scale("prev") == 1.03
    assert window.stage.home_control_scale("apply") == 1.03

    window.stage.set_home_interaction("apply")
    assert window.stage.home_control_scale("apply") == 1.03
    assert window.stage.home_control_offset_y("apply") == -2.0

    window.stage.set_home_interaction("apply", pressed_action="apply")
    assert window.stage.home_control_scale("apply") == 0.94
    assert window.stage.home_control_offset_y("apply") == 0.0


def test_dock_motion_follows_mouse_press_and_release(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.show()
    previous_index = window.current_index

    send_mouse_move(window.stage, QPoint(400, 300))
    send_mouse_move(window.stage, QPoint(510, 573))
    assert window.stage.home_control_scale("prev") == 1.10

    qtbot.mousePress(window.stage, Qt.MouseButton.LeftButton, pos=QPoint(510, 573))
    assert window.stage.home_control_scale("prev") == 0.94

    qtbot.mouseRelease(window.stage, Qt.MouseButton.LeftButton, pos=QPoint(510, 573))
    assert window.stage.home_control_scale("prev") == 1.10
    assert window.current_index == previous_index


def test_disabling_animation_snaps_dock_to_current_target(qtbot, tmp_path):
    window = build_window(tmp_path, animation=True)
    qtbot.addWidget(window)

    window.stage.set_home_interaction("favorite")
    window.stage.set_settings_state(
        startup_enabled=False,
        animation_enabled=False,
        animate=False,
    )

    assert window.stage.home_control_scale("favorite") == 1.10
    assert window.stage.home_control_scale("prev") == 1.03
    assert window.stage.home_control_scale("apply") == 1.03


def test_bottom_menu_click_opens_menu_and_old_position_is_inactive(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.show()

    qtbot.mouseClick(window.stage, Qt.MouseButton.LeftButton, pos=QPoint(818, 51))
    assert window.stage.menu_open is False

    qtbot.mouseClick(window.stage, Qt.MouseButton.LeftButton, pos=QPoint(856, 573))
    assert window.stage.menu_open is True


def test_gallery_menu_routes_taskbar_and_escape_closes(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.show()

    window._handle_action("toggle-menu")
    assert window.stage.menu_open is True
    window._handle_action("page:任务栏美化")
    assert window.stage.current_page == "任务栏美化"
    assert window.stage.menu_open is False
    qtbot.keyClick(window.stage, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: window.stage.current_page == "首页", timeout=1000)


def test_menu_and_page_motion_use_approved_endpoints(qtbot, tmp_path):
    window = build_window(tmp_path, animation=True)
    qtbot.addWidget(window)

    window.stage.toggle_menu()
    assert window.stage.menu_progress == 0.0
    assert window.stage.menu_motion_offset_y() == 12.0
    assert window.stage.menu_motion_scale() == 0.98
    assert window.stage.menu_motion_opacity() == 0.0
    qtbot.waitUntil(lambda: window.stage.menu_progress == 1.0, timeout=1000)

    window.stage.set_page("设置")
    assert window.stage.page_progress == 0.0
    assert window.stage.page_motion_offset_x() == 18.0
    assert window.stage.page_motion_scale() == 0.985
    assert window.stage.page_motion_opacity() == 0.0
    qtbot.waitUntil(lambda: window.stage.page_progress == 1.0, timeout=1000)


def test_page_close_animates_before_returning_home(qtbot, tmp_path):
    window = build_window(tmp_path, animation=True)
    qtbot.addWidget(window)
    window.stage.set_page("设置")
    qtbot.waitUntil(lambda: window.stage.page_progress == 1.0, timeout=1000)

    window.stage.set_page("首页")

    assert window.stage.current_page == "设置"
    assert window.stage.page_is_exiting is True
    qtbot.waitUntil(lambda: window.stage.current_page == "首页", timeout=1000)
    assert window.stage.page_progress == 0.0


def test_disabling_animation_snaps_menu_and_page_motion(qtbot, tmp_path):
    menu_window = build_window(tmp_path / "menu", animation=True)
    qtbot.addWidget(menu_window)
    menu_window.stage.toggle_menu()

    menu_window.stage.set_settings_state(
        startup_enabled=False,
        animation_enabled=False,
        animate=False,
    )

    assert menu_window.stage.menu_progress == 1.0

    page_window = build_window(tmp_path / "page", animation=True)
    qtbot.addWidget(page_window)
    page_window.stage.set_page("设置")
    page_window.stage.set_settings_state(
        startup_enabled=False,
        animation_enabled=False,
        animate=False,
    )

    assert page_window.stage.page_progress == 1.0
    assert page_window.stage.page_motion_offset_x() == 0.0


def test_pencil_layout_constants_are_exact():
    assert HERO_INFO_RECT == (832, 535, 368, 142)
    assert RAIL_RECT == (126, 699, 948, 78)


def test_rapid_navigation_keeps_last_requested_index(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.set_wallpapers(create_wallpapers(tmp_path))

    window.request_index(1)
    window.request_index(2)

    qtbot.waitUntil(
        lambda: window.stage.current_wallpaper is not None
        and window.stage.current_wallpaper.key == "20260712",
        timeout=1000,
    )
    assert window.current_index == 2
    assert window.stage.current_wallpaper.key == "20260712"


class StubHistoryProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def fetch_page(self, page, page_size=30):
        self.calls.append((page, page_size))
        if self.error:
            raise self.error
        return self.result


class PagedHistoryProvider:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch_page(self, page, page_size=30):
        self.calls.append((page, page_size))
        return self.pages[page]


def test_wallpaper_merge_deduplicates_dates_and_enriches_metadata(tmp_path):
    existing = create_wallpapers(tmp_path)[:1]
    incoming = [
        Wallpaper(
            title="官方地点标题",
            headline="官方主题大字",
            copyright="官方版权",
            startdate=existing[0].startdate,
            preview_url="https://example.com/new-preview.jpg",
            full_url="https://example.com/new-4k.jpg",
            copyright_link="https://example.com/source",
        ),
        Wallpaper(
            title="更早壁纸",
            copyright="官方版权",
            startdate="20260709",
            preview_url="https://example.com/older-preview.jpg",
            full_url="https://example.com/older-4k.jpg",
        ),
    ]

    merged = MainWindow.merge_wallpapers(existing, incoming)

    assert [item.startdate for item in merged] == [existing[0].startdate, "20260709"]
    assert merged[0].headline == "官方主题大字"
    assert merged[0].local_preview == existing[0].local_preview
    assert merged[0].copyright_link == "https://example.com/source"


def test_wallpaper_list_deduplicates_same_official_date():
    first = Wallpaper(
        title="first",
        copyright="source",
        startdate="20260710",
        preview_url="https://one.example/preview.jpg",
        full_url="https://one.example/full.jpg",
    )
    duplicate_date = Wallpaper(
        title="duplicate",
        copyright="source",
        startdate="20260710",
        preview_url="https://two.example/preview.jpg",
        full_url="https://two.example/full.jpg",
    )

    unique = MainWindow.deduplicate_wallpapers([first, duplicate_date])

    assert unique == [first]


def test_wallpaper_list_deduplicates_resolution_variants():
    preview = Wallpaper(
        title="preview",
        copyright="source",
        startdate="",
        preview_url="https://upload.wikimedia.org/wikipedia/commons/1/1f/Sample_1920x1080.jpg",
        full_url="",
    )
    uhd = Wallpaper(
        title="uhd",
        copyright="source",
        startdate="",
        preview_url="",
        full_url="https://upload.wikimedia.org/wikipedia/commons/1/1f/Sample_UHD.jpg",
    )

    unique = MainWindow.deduplicate_wallpapers([preview, uhd])

    assert unique == [preview]


def test_navigation_skips_duplicate_wallpaper(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    wallpapers = create_wallpapers(tmp_path)
    duplicate_image = Wallpaper(
        title="duplicate image",
        copyright="source",
        startdate="20260713",
        preview_url="https://upload.wikimedia.org/wikipedia/commons/2/2f/Shared_1920x1080.jpg",
        full_url="https://upload.wikimedia.org/wikipedia/commons/2/2f/Shared_UHD.jpg",
        local_preview=wallpapers[0].local_preview,
    )
    wallpapers[0].preview_url = "https://upload.wikimedia.org/wikipedia/commons/2/2f/Shared_1920x1080.jpg"
    wallpapers[0].full_url = "https://upload.wikimedia.org/wikipedia/commons/2/2f/Shared_UHD.jpg"
    window.wallpapers = [wallpapers[0], duplicate_image, wallpapers[1]]
    window.current_index = 0
    window._requested_index = 0

    window.next_wallpaper()

    assert window.current_index == 2
    window.previous_wallpaper()
    assert window.current_index == 0


def test_next_at_history_end_advances_into_new_group_when_loaded(qtbot, tmp_path):
    wallpapers = list(reversed(create_wallpapers(tmp_path)))
    older = Wallpaper(
        title="更早壁纸",
        copyright="官方版权",
        startdate="20260709",
        preview_url="https://example.com/older-preview.jpg",
        full_url="https://example.com/older-4k.jpg",
    )
    provider = StubHistoryProvider(
        HistoryPage([older], page=1, page_size=30, total=4, has_more=False)
    )
    window = build_window(tmp_path, animation=False, history_provider=provider)
    qtbot.addWidget(window)
    window.set_wallpapers(wallpapers)
    window.request_index(len(wallpapers) - 1)
    queued = []
    window.submit = lambda function, callback: queued.append((function, callback))

    window.next_wallpaper()

    assert window.current_wallpaper.key == wallpapers[-1].key
    assert window.stage.wallpaper_group_loading is True
    assert len(queued) == 1

    function, callback = queued.pop()
    callback(function(), None)

    assert provider.calls == [(1, 30)]
    assert window.current_wallpaper.key == "20260709"
    assert len({item.startdate for item in window.wallpapers}) == len(window.wallpapers)
    assert window.stage.wallpaper_group_loading is False


def test_repeated_next_while_history_loads_does_not_duplicate_request(qtbot, tmp_path):
    provider = StubHistoryProvider()
    window = build_window(tmp_path, animation=False, history_provider=provider)
    qtbot.addWidget(window)
    wallpapers = create_wallpapers(tmp_path)
    window.set_wallpapers(wallpapers)
    window.request_index(len(wallpapers) - 1)
    queued = []
    window.submit = lambda function, callback: queued.append((function, callback))

    window.next_wallpaper()
    window.next_wallpaper()

    assert len(queued) == 1
    assert window.current_wallpaper.key == wallpapers[-1].key
    assert window._advance_after_history is True
    assert window.stage.wallpaper_group_loading is True


def test_wallpaper_group_loading_animation_starts_and_stops(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)

    stage.set_wallpaper_group_loading(True)

    assert stage.wallpaper_group_loading is True
    assert (
        stage._wallpaper_group_loading_animation.state()
        == QAbstractAnimation.State.Running
    )
    qtbot.waitUntil(lambda: stage.wallpaper_group_loading_progress > 0.0, timeout=500)

    stage.set_wallpaper_group_loading(False)

    assert stage.wallpaper_group_loading is False
    assert stage.wallpaper_group_loading_progress == 0.0
    assert (
        stage._wallpaper_group_loading_animation.state()
        == QAbstractAnimation.State.Stopped
    )


def test_operation_loading_animation_starts_and_stops(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)

    stage.set_operation_loading(True, "正在准备壁纸…")

    assert stage.operation_loading is True
    assert stage.operation_loading_message == "正在准备壁纸…"
    assert stage._operation_loading_animation.state() == QAbstractAnimation.State.Running
    qtbot.waitUntil(lambda: stage.operation_loading_progress > 0.0, timeout=500)

    stage.set_operation_loading(False)

    assert stage.operation_loading is False
    assert stage.operation_loading_progress == 0.0
    assert stage._operation_loading_animation.state() == QAbstractAnimation.State.Stopped


def test_navigation_skips_different_records_with_identical_image_content(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    wallpapers = create_wallpapers(tmp_path)
    duplicate_path = tmp_path / "duplicate-content.jpg"
    duplicate_path.write_bytes(Path(wallpapers[0].local_preview).read_bytes())
    wallpapers[1].local_preview = str(duplicate_path)
    wallpapers[1].local_full = str(duplicate_path)
    window.set_wallpapers(wallpapers)

    window.next_wallpaper()

    qtbot.waitUntil(lambda: window.current_index == 2, timeout=1000)
    assert window.current_index == 2
    assert window.current_wallpaper.key == wallpapers[2].key


def test_rapid_navigation_uses_prepared_previews_and_coalesces_state_writes(
    qtbot, tmp_path
):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.set_wallpapers(create_wallpapers(tmp_path))
    qtbot.waitUntil(lambda: len(window._prepared_previews) == 3, timeout=1000)
    saves = []
    window.state_store.save = lambda state: saves.append(state.current_index)

    started = perf_counter()
    for _ in range(500):
        window.next_wallpaper()
        window.previous_wallpaper()
    elapsed = perf_counter() - started

    assert elapsed < 1.0
    assert window.current_index == 0
    assert window.stage.current_wallpaper.key == "20260710"
    assert saves == []
    qtbot.waitUntil(lambda: len(saves) == 1, timeout=1000)
    assert saves == [0]


def test_prefetch_coalesces_duplicate_preview_requests(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    queued = []
    window._submit_preview = (
        lambda function, callback, urgent=False: queued.append(
            (function, callback, urgent)
        )
    )

    window.set_wallpapers(create_wallpapers(tmp_path))
    for _ in range(50):
        window._prefetch_neighbors()

    assert len(queued) == 2
    assert len(window._pending_previews) == 2


def test_rapid_uncached_navigation_cancels_stale_queued_target(qtbot, tmp_path):
    class PendingFuture:
        def __init__(self):
            self.cancel_calls = 0

        def cancel(self):
            self.cancel_calls += 1
            return True

    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.wallpapers = create_wallpapers(tmp_path)
    window.stage.set_wallpapers(window.wallpapers)
    window.current_index = 0
    window._requested_index = 0
    submitted = []

    def submit_preview(function, callback, *, urgent):
        future = PendingFuture()
        submitted.append((future, urgent))
        return future

    window._submit_preview = submit_preview

    window._display_index(1)
    first = submitted[-1][0]
    window._requested_index = 2
    window._display_index(2)

    assert first.cancel_calls == 1
    assert len(window._pending_previews) == 1
    pending_key = window._preview_cache_key(window.wallpapers[2])
    assert window._pending_previews[pending_key][1] is True


def test_latest_navigation_is_not_blocked_by_running_stale_download(qtbot, tmp_path):
    wallpapers = [
        replace(item, local_preview="", local_full="")
        for item in create_wallpapers(tmp_path)
    ]
    paths = {
        wallpaper.key: tmp_path / f"wallpaper-{index}.jpg"
        for index, wallpaper in enumerate(wallpapers)
    }

    class BlockingFirstCache:
        def __init__(self):
            self.first_started = Event()
            self.release_first = Event()

        def get_path(self, wallpaper, quality):
            return None

        def fetch(self, wallpaper, quality):
            if wallpaper.key == wallpapers[1].key:
                self.first_started.set()
                self.release_first.wait(2)
            return paths[wallpaper.key]

    cache = BlockingFirstCache()
    window = build_window(tmp_path / "window", animation=False)
    qtbot.addWidget(window)
    window.image_cache = cache
    window.wallpapers = wallpapers
    window.stage.set_wallpapers(wallpapers)
    window.current_index = 0
    window._requested_index = 0
    window._prefetch_neighbors = lambda: None

    try:
        window.request_index(1)
        qtbot.waitUntil(cache.first_started.is_set, timeout=500)
        window.request_index(2)
        qtbot.waitUntil(
            lambda: window.stage.current_wallpaper is not None
            and window.stage.current_wallpaper.key == wallpapers[2].key,
            timeout=500,
        )
    finally:
        cache.release_first.set()


def test_offline_prefetch_does_not_start_remote_downloads(qtbot, tmp_path):
    wallpapers = [
        replace(item, local_preview="", local_full="")
        for item in create_wallpapers(tmp_path)
    ]

    class OfflineCache:
        def __init__(self):
            self.fetches = []

        def get_path(self, wallpaper, quality):
            return None

        def fetch(self, wallpaper, quality):
            self.fetches.append((wallpaper.key, quality))
            raise TimeoutError("offline")

    cache = OfflineCache()
    window = build_window(tmp_path / "window", animation=False)
    qtbot.addWidget(window)
    window.image_cache = cache
    window.wallpapers = wallpapers
    window.stage.set_wallpapers(wallpapers)
    window.current_index = 0
    window._requested_index = 0
    window._network_available = False

    window._prefetch_neighbors()
    qtbot.wait(100)

    assert cache.fetches == []
    assert window._pending_previews == {}


def test_prepared_preview_cache_has_fixed_memory_bound(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    wallpaper = create_wallpapers(tmp_path)[0]
    preview = window._prepare_preview(wallpaper)

    for index in range(window.PREVIEW_CACHE_LIMIT + 25):
        window._remember_preview(f"preview-{index}", preview)

    assert len(window._prepared_previews) == window.PREVIEW_CACHE_LIMIT
    assert next(iter(window._prepared_previews)) == "preview-25"

def test_two_rapid_next_clicks_request_two_distinct_wallpapers(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    wallpapers = create_wallpapers(tmp_path)
    window.set_wallpapers(wallpapers)
    requested = []
    window._display_index = requested.append

    window.next_wallpaper()
    window.next_wallpaper()

    assert requested == [1, 2]
    assert len({wallpapers[index].key for index in requested}) == 2


def test_two_rapid_previous_clicks_request_two_distinct_wallpapers(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    wallpapers = create_wallpapers(tmp_path)
    window.set_wallpapers(wallpapers)
    window.request_index(2)
    requested = []
    window._display_index = requested.append

    window.previous_wallpaper()
    window.previous_wallpaper()

    assert requested == [1, 0]
    assert len({wallpapers[index].key for index in requested}) == 2

def test_history_load_failure_wraps_to_cached_gallery_start(qtbot, tmp_path):
    provider = StubHistoryProvider(error=RuntimeError("offline"))
    window = build_window(tmp_path, animation=False, history_provider=provider)
    qtbot.addWidget(window)
    wallpapers = create_wallpapers(tmp_path)
    window.set_wallpapers(wallpapers)
    window.request_index(len(wallpapers) - 1)

    window.next_wallpaper()

    qtbot.waitUntil(
        lambda: not window._history_loading
        and window.current_wallpaper.key == wallpapers[0].key,
        timeout=1000,
    )
    assert window.current_index == 0
    assert window._advance_after_history is False
    assert window.stage.wallpaper_group_loading is False


def test_refresh_prefers_uapi_history_page(qtbot, tmp_path):
    wallpaper = Wallpaper(
        title="官方地点",
        headline="官方主题",
        copyright="官方版权",
        startdate="20260710",
        preview_url="https://example.com/1080.jpg",
        full_url="https://example.com/4k.jpg",
    )
    provider = StubHistoryProvider(
        HistoryPage([wallpaper], page=1, page_size=30, total=1, has_more=False)
    )
    window = build_window(tmp_path, history_provider=provider)
    qtbot.addWidget(window)

    window.refresh_wallpapers()

    qtbot.waitUntil(lambda: bool(window.wallpapers), timeout=1000)
    assert provider.calls == [(1, 30)]
    assert window.wallpapers[0].headline == "官方主题"
    assert window._history_page == 2
    assert window._history_exhausted is True


def test_refresh_merge_preserves_current_wallpaper_date(qtbot, tmp_path):
    wallpapers = list(reversed(create_wallpapers(tmp_path)))
    provider = StubHistoryProvider(
        HistoryPage(
            [
                Wallpaper(
                    title="更新元数据",
                    headline="更新主题",
                    copyright="官方版权",
                    startdate=wallpapers[1].startdate,
                    preview_url="https://example.com/new-preview.jpg",
                    full_url="https://example.com/new-4k.jpg",
                )
            ],
            page=1,
            page_size=30,
            total=3,
            has_more=False,
        )
    )
    window = build_window(tmp_path, history_provider=provider)
    qtbot.addWidget(window)
    window.set_wallpapers(wallpapers)
    window.request_index(1)
    current_key = window.current_wallpaper.key

    window.refresh_wallpapers()

    qtbot.waitUntil(lambda: not window._history_loading, timeout=1000)
    assert window.current_wallpaper.key == current_key
    assert window.current_wallpaper.headline == "更新主题"


def test_refresh_merges_new_metadata_without_dropping_cached_gallery(qtbot, tmp_path):
    cached = create_wallpapers(tmp_path)
    incoming = Wallpaper(
        title="远程新增壁纸",
        copyright="官方版权",
        startdate="smk-new",
        preview_url="https://example.com/new-preview.jpg",
        full_url="https://example.com/new-4k.jpg",
    )
    provider = StubHistoryProvider(
        HistoryPage([incoming], page=1, page_size=30, total=4, has_more=False)
    )
    window = build_window(tmp_path, animation=False, history_provider=provider)
    qtbot.addWidget(window)
    window.set_wallpapers(cached)

    window.refresh_wallpapers()

    qtbot.waitUntil(lambda: not window._history_loading, timeout=1000)
    assert {item.key for item in cached}.issubset(
        {item.key for item in window.wallpapers}
    )
    assert incoming.key in {item.key for item in window.wallpapers}


def test_startup_defaults_to_latest_cached_official_date(qtbot, tmp_path):
    wallpapers = create_wallpapers(tmp_path)
    store = StateStore(tmp_path / "state.json")
    store.save(
        AppState(
            current_index=0,
            wallpapers=wallpapers,
            settings=AppSettings(animation_preference_version=1),
        )
    )

    window = MainWindow(
        state_store=store,
        provider_chain=EmptyProviderChain(),
        wallpaper_service=StubService(),
        startup_manager=StubStartupManager(),
        history_provider=False,
        taskbar_service=StubTaskbarService(),
        autoload=False,
    )
    qtbot.addWidget(window)

    assert window.current_wallpaper.startdate == "20260712"


def test_first_refresh_selects_today_unless_user_navigated(qtbot, tmp_path):
    cached = list(reversed(create_wallpapers(tmp_path)))
    latest = Wallpaper(
        title="当天壁纸",
        copyright="官方版权",
        startdate="20260713",
        preview_url="https://example.com/today-preview.jpg",
        full_url="https://example.com/today-4k.jpg",
    )
    provider = StubHistoryProvider(
        HistoryPage([latest, *cached], page=1, page_size=30, total=4, has_more=False)
    )
    window = build_window(tmp_path, history_provider=provider)
    qtbot.addWidget(window)
    window.set_wallpapers(cached)
    window.startup_wallpaper_index = lambda wallpapers: MainWindow.startup_wallpaper_index(wallpapers, "20260712")

    window.refresh_wallpapers()

    qtbot.waitUntil(lambda: not window._history_loading, timeout=1000)
    assert window.current_wallpaper.startdate == "20260712"

    second_provider = StubHistoryProvider(
        HistoryPage([latest, *cached], page=1, page_size=30, total=4, has_more=False)
    )
    second = build_window(tmp_path / "manual", history_provider=second_provider)
    qtbot.addWidget(second)
    second.set_wallpapers(cached)
    second.next_wallpaper()
    selected_key = second.current_wallpaper.key

    second.refresh_wallpapers()

    qtbot.waitUntil(lambda: not second._history_loading, timeout=1000)
    assert second.current_wallpaper.key == selected_key


def test_set_wallpapers_preserves_current_date_when_newer_items_reorder_list(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    wallpapers = list(reversed(create_wallpapers(tmp_path)))
    window.set_wallpapers(wallpapers)
    window.request_index(1)
    current_key = window.current_wallpaper.key
    newer = Wallpaper(
        title="更新壁纸",
        copyright="官方版权",
        startdate="20260713",
        preview_url="https://example.com/newest-preview.jpg",
        full_url="https://example.com/newest-4k.jpg",
    )

    window.set_wallpapers([newer, *wallpapers])

    assert window.current_wallpaper.key == current_key


def test_history_navigation_skips_fully_cached_page(qtbot, tmp_path):
    wallpapers = list(reversed(create_wallpapers(tmp_path)))
    duplicate_page = HistoryPage(
        list(wallpapers),
        page=1,
        page_size=30,
        total=31,
        has_more=True,
    )
    older = Wallpaper(
        title="更早壁纸",
        copyright="官方版权",
        startdate="20260709",
        preview_url="https://example.com/older-preview.jpg",
        full_url="https://example.com/older-4k.jpg",
    )
    provider = PagedHistoryProvider(
        {
            1: duplicate_page,
            2: HistoryPage([older], page=2, page_size=30, total=31, has_more=False),
        }
    )
    window = build_window(tmp_path, animation=False, history_provider=provider)
    qtbot.addWidget(window)
    window.set_wallpapers(wallpapers)
    window.request_index(len(wallpapers) - 1)

    window.next_wallpaper()

    qtbot.waitUntil(
        lambda: not window._history_loading
        and "20260709" in {item.key for item in window.wallpapers},
        timeout=1000,
    )
    assert window.current_wallpaper.key == "20260709"
    assert provider.calls == [(1, 30), (2, 30)]


def test_navigation_sets_directional_transition_state(qtbot, tmp_path):
    window = build_window(tmp_path, animation=True)
    qtbot.addWidget(window)
    window.set_wallpapers(create_wallpapers(tmp_path))

    window.next_wallpaper()
    assert window.stage.transition_direction == 1
    assert window.stage.animation_duration == 280
    assert window.stage.current_image_offset_x() == 28.0
    assert window.stage.current_image_scale() == 1.015
    assert window.stage.metadata_offset_x() == 16.0
    assert window.stage.metadata_opacity() == 0.0

    window.previous_wallpaper()
    assert window.stage.transition_direction == -1
    assert window.stage.current_image_offset_x() == -28.0
    assert window.stage.metadata_offset_x() == -16.0


def test_mid_transition_keeps_both_canvas_edges_covered(qtbot, tmp_path):
    window = build_window(tmp_path, animation=True)
    qtbot.addWidget(window)
    window.resize(1200, 800)
    window.show()
    white = QImage(1200, 800, QImage.Format.Format_RGB32)
    white.fill(QColor("#FFFFFF"))
    window.stage._previous_image = white
    window.stage._current_image = white
    window.stage.animation_progress = 0.5

    rendered = window.stage.grab().toImage()
    edge = rendered.pixelColor(1, 400).lightness()
    center = rendered.pixelColor(600, 400).lightness()

    assert abs(edge - center) <= 2


def test_legacy_disabled_animation_is_reenabled_once(qtbot, tmp_path):
    window = build_window(
        tmp_path,
        animation=False,
        animation_preference_version=0,
    )
    qtbot.addWidget(window)

    persisted = StateStore(tmp_path / "state.json").load().settings
    assert window.stage.animation_enabled is True
    assert persisted.animation_enabled is True
    assert persisted.animation_preference_version == 1


def test_current_disabled_animation_preference_is_preserved(qtbot, tmp_path):
    window = build_window(
        tmp_path,
        animation=False,
        animation_preference_version=1,
    )
    qtbot.addWidget(window)

    assert window.stage.animation_enabled is False


def test_disabling_animation_finishes_active_wallpaper_transition(qtbot, tmp_path):
    window = build_window(tmp_path, animation=True)
    qtbot.addWidget(window)
    window.set_wallpapers(create_wallpapers(tmp_path))

    window.next_wallpaper()
    window.stage.set_settings_state(
        startup_enabled=False,
        animation_enabled=False,
        animate=False,
    )

    assert window.stage.animation_progress == 1.0
    assert window.stage.metadata_progress == 1.0
    assert window.stage._previous_image is None


def test_favorite_toggle_persists_state(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.set_wallpapers(create_wallpapers(tmp_path))

    window.toggle_favorite()

    assert window.current_wallpaper.key in StateStore(tmp_path / "state.json").load().favorites


def test_auto_interval_cycles_through_supported_values(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.state.settings.auto_interval_minutes = 5

    window.cycle_auto_interval()

    assert window.state.settings.auto_interval_minutes == 15
    assert StateStore(tmp_path / "state.json").load().settings.auto_interval_minutes == 15


def test_settings_actions_are_dispatched(qtbot, tmp_path):
    window = build_window(tmp_path, animation=True)
    qtbot.addWidget(window)

    window._handle_action("animation")

    assert window.state.settings.animation_enabled is False
    assert window.stage.animation_enabled is False


class FakeFolderDialog:
    class FileMode:
        Directory = object()

    class Option:
        ShowDirsOnly = object()

    accepted = True
    selected_path = ""
    last_instance = None

    def __init__(self, parent, title, directory):
        self.parent = parent
        self.title = title
        self.directory = directory
        self.modality = None
        self.raised = False
        self.activated = False
        FakeFolderDialog.last_instance = self

    def setFileMode(self, mode):
        self.file_mode = mode

    def setOption(self, option, enabled):
        self.option = (option, enabled)

    def setWindowModality(self, modality):
        self.modality = modality

    def raise_(self):
        self.raised = True

    def activateWindow(self):
        self.activated = True

    def exec(self):
        return self.accepted

    def selectedFiles(self):
        return [self.selected_path]


def test_folder_picker_is_parented_foreground_and_persists_selection(qtbot, tmp_path, monkeypatch):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    selected = tmp_path / "downloads"
    FakeFolderDialog.accepted = True
    FakeFolderDialog.selected_path = str(selected)
    monkeypatch.setattr(main_window, "QFileDialog", FakeFolderDialog)

    window.choose_download_folder()

    dialog = FakeFolderDialog.last_instance
    assert dialog.parent is window
    assert dialog.directory == str(Path.home() / "Pictures" / "JiangMaoWallpaper")
    assert dialog.modality == Qt.WindowModality.ApplicationModal
    assert dialog.raised is True
    assert dialog.activated is True
    assert StateStore(tmp_path / "state.json").load().settings.download_dir == str(selected)


def test_folder_picker_cancel_keeps_existing_directory(qtbot, tmp_path, monkeypatch):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    existing = window.state.settings.download_dir
    FakeFolderDialog.accepted = False
    FakeFolderDialog.selected_path = str(tmp_path / "ignored")
    monkeypatch.setattr(main_window, "QFileDialog", FakeFolderDialog)

    window.choose_download_folder()

    assert window.state.settings.download_dir == existing


def test_startup_button_click_changes_visible_state(qtbot, tmp_path):
    startup = StubStartupManager(False)
    window = build_window(tmp_path, startup_manager=startup)
    qtbot.addWidget(window)
    window.show()
    window.stage.set_page("设置")

    qtbot.mouseClick(window.stage, Qt.MouseButton.LeftButton, pos=QPoint(847, 230))

    assert startup.enabled is True
    assert window.state.settings.startup_enabled is True
    assert window.stage.startup_enabled is True
    assert window.stage.settings_button_label("startup") == "已开启"


def test_animation_button_click_changes_visible_state(qtbot, tmp_path):
    window = build_window(tmp_path, animation=True)
    qtbot.addWidget(window)
    window.show()
    window.stage.set_page("设置")

    qtbot.mouseClick(window.stage, Qt.MouseButton.LeftButton, pos=QPoint(847, 422))

    assert window.state.settings.animation_enabled is False
    assert window.stage.animation_enabled is False
    assert window.stage.settings_button_label("animation") == "已关闭"


def test_settings_about_entry_opens_secondary_page(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.show()
    window.stage.set_page("设置")
    scale, offset_x, offset_y = window.stage._viewport()
    center = window.stage.about_settings_rect().center()
    point = QPoint(
        round(offset_x + center.x() * scale),
        round(offset_y + center.y() * scale),
    )

    qtbot.mouseClick(window.stage, Qt.MouseButton.LeftButton, pos=point)

    assert window.stage.current_page == "设置"
    assert window.stage.settings_section == "about"
    assert window.stage.about_info_items() == (
        ("产品", "匠猫壁纸"),
        ("版本", "1.0.0\nofficial"),
        ("开发者", "匠心猫"),
        ("开发品牌", "JiangMao Studio"),
    )


def test_about_exposes_wallpaper_source_and_copyright_notice(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)

    assert stage.about_legal_notice() == (
        "来源、版权与隐私",
        "壁纸来自国家公园及联邦自然资源机构的公共领域高清实拍；许可证与原始来源以照片详情页为准。",
    )
    assert not hasattr(stage, "about_contact_email")
    assert not hasattr(stage, "about_website_display")
    assert not hasattr(stage, "about_website_url")
    assert stage.about_manifesto() == (
        "让喜欢壁纸的人，更轻松地遇见好风景。",
        "匠猫壁纸由匠心猫开发与维护，专注于清晰、安静且可靠的桌面壁纸体验。",
    )
    assert stage.about_gallery_plate() == (
        "把每日遇见的风景，安静地留在桌面。",
        "匠猫壁纸专注于高清壁纸浏览、收藏、下载与桌面应用。",
        "壁纸来自经许可索引核验的内置官方摄影目录，联网时优先从 NPS 官方风景源继续加载，并保留机构、许可证及原始来源。功能统计仅记录匿名设备、功能调用与崩溃日志，以改善应用。",
    )


def test_removed_about_contact_area_has_no_click_action(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.resize(1280, 800)
    stage.set_page("设置")
    stage.set_settings_section("about")
    actions = []
    stage.action_requested.connect(actions.append)

    qtbot.mouseClick(
        stage,
        Qt.MouseButton.LeftButton,
        pos=QPoint(847, 644),
    )

    assert actions == []


def test_wallpaper_source_click_requests_original_work_page(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.resize(1200, 800)
    stage.current_wallpaper = Wallpaper(
        title="山湖晨光",
        copyright="Glacier National Park · Public Domain · National Park Photography",
        startdate="photo-51510852367",
        preview_url="https://live.staticflickr.com/preview.jpg",
        full_url="https://live.staticflickr.com/full.jpg",
        copyright_link="https://www.flickr.com/photos/43288043@N04/51510852367",
    )
    actions = []
    stage.action_requested.connect(actions.append)

    qtbot.mouseClick(
        stage,
        Qt.MouseButton.LeftButton,
        pos=stage.home_source_rect().center().toPoint(),
    )

    assert actions == ["open:wallpaper-source"]


def test_removed_official_website_action_is_ignored(qtbot, tmp_path, monkeypatch):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    opened_urls = []

    class DesktopServicesStub:
        @staticmethod
        def openUrl(url):
            opened_urls.append(url.toString())
            return True

    monkeypatch.setattr(main_window, "QDesktopServices", DesktopServicesStub)

    window._handle_action("open:official-website")

    assert opened_urls == []


def test_about_back_returns_to_settings(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.stage.set_page("关于")

    window._handle_action("close-page")

    assert window.stage.current_page == "设置"


def test_settings_sidebar_uses_same_font_size_as_content_titles(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)

    assert stage.settings_label_font_size() == 13


def test_settings_sidebar_leaves_more_width_for_content(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)

    sidebar = stage.settings_sidebar_rect()
    content = stage.settings_content_rect()

    assert sidebar.width() == 140
    assert content.width() == 330
    assert sidebar.right() < content.left()


def test_settings_controls_share_one_right_edge(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)

    right_edges = {
        round(box.right())
        for box in stage.settings_section_control_rects().values()
    }

    assert right_edges == {1096}


def test_storage_glass_buttons_have_hit_targets_and_press_feedback(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    stage.resize(1200, 800)
    stage.set_page("设置")
    stage.set_settings_section("storage")
    folder = stage.settings_section_control_rects()["folder"]

    assert stage.settings_action_at(folder.center()) == "folder"
    qtbot.mousePress(stage, Qt.MouseButton.LeftButton, pos=folder.center().toPoint())
    assert stage._pressed_settings_action == "folder"
    qtbot.mouseRelease(stage, Qt.MouseButton.LeftButton, pos=folder.center().toPoint())
    assert stage._pressed_settings_action == ""


def test_settings_action_success_feedback_is_temporary(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)

    stage.show_settings_action_feedback("cache")
    assert stage._settings_feedback_action == "cache"

    qtbot.waitUntil(lambda: stage._settings_feedback_action == "", timeout=1200)


def test_escape_from_about_returns_to_settings(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.show()
    window.stage.set_page("关于")

    qtbot.keyClick(window.stage, Qt.Key.Key_Escape)

    assert window.stage.current_page == "设置"


def test_boolean_settings_use_animated_toggle_geometry(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)

    assert window.stage.settings_control_kind("startup") == "toggle"
    assert window.stage.settings_control_kind("animation") == "toggle"
    assert window.stage.settings_control_kind("auto") == "toggle"
    assert window.stage.toggle_animation_duration == 300
    controls = window.stage.settings_section_control_rects()
    startup_off = window.stage.toggle_handle_rect(controls["startup"], 0.0).x()
    startup_on = window.stage.toggle_handle_rect(controls["startup"], 1.0).x()
    animation_off = window.stage.toggle_handle_rect(controls["animation"], 0.0).x()
    assert window.stage.toggle_handle_x("startup") == startup_off

    window.stage.set_settings_state(startup_enabled=True, animation_enabled=False, animate=False)

    assert window.stage.toggle_handle_x("startup") == startup_on
    assert window.stage.toggle_handle_x("animation") == animation_off


def test_toggle_switch_animates_between_endpoints(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.stage.set_settings_state(startup_enabled=True, animation_enabled=False, animate=True)

    qtbot.wait(100)

    box = window.stage.settings_section_control_rects()["startup"]
    start = window.stage.toggle_handle_rect(box, 0.0).x()
    end = window.stage.toggle_handle_rect(box, 1.0).x()
    assert start < window.stage.toggle_handle_x("startup") < end
    qtbot.waitUntil(lambda: window.stage.toggle_handle_x("startup") == end, timeout=1000)


def test_toggle_handles_stay_inside_compact_tracks(qtbot):
    stage = WallpaperStage()
    qtbot.addWidget(stage)
    controls = stage.settings_section_control_rects()

    for name in ("startup", "auto:toggle", "animation"):
        track = controls[name]
        assert track.contains(stage.toggle_handle_rect(track, 0.0))
        assert track.contains(stage.toggle_handle_rect(track, 1.0))


def test_secondary_page_close_returns_home(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.stage.set_page("设置")

    window._handle_action("close-page")

    qtbot.waitUntil(lambda: window.stage.current_page == "首页", timeout=1000)


def test_escape_closes_secondary_page(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.stage.set_page("收藏")

    qtbot.keyClick(window.stage, Qt.Key.Key_Escape)

    qtbot.waitUntil(lambda: window.stage.current_page == "首页", timeout=1000)


def test_wallpaper_date_is_formatted_as_date_not_position():
    assert format_wallpaper_date("20260709") == "2026.07.09 · ARCHIVE"
    assert format_wallpaper_date("smk-KKS2004-95") == "精选 · OPEN COLLECTION"


def test_selecting_library_item_returns_home_preview(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    wallpapers = create_wallpapers(tmp_path)
    window.set_wallpapers(wallpapers)
    window.stage.set_page("历史")

    window._handle_action(f"select:{wallpapers[2].key}")

    assert window.current_index == 2
    assert window.stage.current_page == "首页"
    qtbot.waitUntil(
        lambda: window.stage.current_wallpaper is not None
        and window.stage.current_wallpaper.key == wallpapers[2].key,
        timeout=1000,
    )
    assert window.stage.current_wallpaper.key == wallpapers[2].key

def test_startup_wallpaper_prefers_exact_today(tmp_path):
    wallpapers = create_wallpapers(tmp_path)

    index = MainWindow.startup_wallpaper_index(wallpapers, "20260711")

    assert wallpapers[index].startdate == "20260711"


def test_startup_wallpaper_falls_back_to_latest_available(tmp_path):
    wallpapers = create_wallpapers(tmp_path)

    index = MainWindow.startup_wallpaper_index(wallpapers, "20260720")

    assert wallpapers[index].startdate == "20260712"


def test_background_close_hides_window_until_explicit_exit(qtbot, tmp_path):
    window = build_window(tmp_path)
    qtbot.addWidget(window)
    window.background_mode = True
    window.show()

    window.close()

    assert window.isHidden()
    assert window._auto_timer.parent() is window
    window.request_exit()
    assert window._allow_close is True

def test_auto_switch_requires_enabled_toggle_and_restarts_timer(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    assert window.state.settings.auto_change_enabled is False
    assert window._auto_timer.isActive() is False
    assert window.stage.auto_change_enabled is False

    window.toggle_auto_change()

    assert window.state.settings.auto_change_enabled is True
    assert window._auto_timer.isActive() is True
    assert window._auto_timer.interval() == 1440 * 60_000
    assert window.stage.auto_change_enabled is True

    window.set_auto_interval(15)

    assert window.state.settings.auto_interval_minutes == 15
    assert window._auto_timer.interval() == 15 * 60_000
    assert window.stage.auto_interval_label == "15 分钟"
    assert window.stage._settings_feedback_action == "auto:interval"
    persisted = StateStore(tmp_path / "state.json").load().settings
    assert persisted.auto_change_enabled is True
    assert persisted.auto_interval_minutes == 15

    window.toggle_auto_change()
    assert window._auto_timer.isActive() is False


def test_auto_interval_click_is_disabled_until_switch_enabled(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    window.show()
    window.stage.set_page("设置")
    actions = []
    window.stage.action_requested.connect(actions.append)

    qtbot.mouseClick(window.stage, Qt.MouseButton.LeftButton, pos=QPoint(837, 358))
    assert "auto:interval" not in actions

    window.stage.set_auto_change_state(True, "每天", animate=False)
    qtbot.mouseClick(window.stage, Qt.MouseButton.LeftButton, pos=QPoint(837, 358))
    assert "auto:interval" in actions

def test_background_auto_timeout_advances_and_applies(qtbot, tmp_path):
    window = build_window(tmp_path, animation=False)
    qtbot.addWidget(window)
    calls = []
    window.next_wallpaper = lambda: calls.append("next")
    window.apply_current = lambda: calls.append("apply")

    window._auto_change()

    assert calls == ["next"]
    qtbot.waitUntil(lambda: calls == ["next", "apply"], timeout=1000)
