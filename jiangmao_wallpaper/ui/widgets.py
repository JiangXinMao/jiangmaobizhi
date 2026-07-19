from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QEasingCurve, QPointF, QRectF, QTimer, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from ..models import TASKBAR_MODES, Wallpaper
from .accent import FALLBACK_ACCENT, extract_accent
from .theme import CREAM, DARK, DESIGN_HEIGHT, DESIGN_WIDTH, GOLD, HERO_INFO_RECT, INK, LINE, MUTED, RAIL_RECT, WHITE


def ui_font(size: int, weight: int = QFont.Weight.Normal) -> QFont:
    families = set(QFontDatabase.families())
    family = "Inter" if "Inter" in families else "Microsoft YaHei UI"
    font = QFont(family, size)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def format_wallpaper_date(startdate: str) -> str:
    if len(startdate) == 8 and startdate.isdigit():
        return f"{startdate[:4]}.{startdate[4:6]}.{startdate[6:]} · ARCHIVE"
    return "精选 · OPEN COLLECTION"


class WallpaperStage(QWidget):
    action_requested = Signal(str)
    window_move_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.animation_duration = 280
        self.animation_enabled = True
        self.startup_enabled = False
        self.menu_open = False
        self.menu_progress = 0.0
        self.page_progress = 0.0
        self.page_is_exiting = False
        self.accent = QColor(FALLBACK_ACCENT)
        self._drag_global_position: QPointF | None = None
        self._hover_action = ""
        self._pressed_home_action = ""
        self._pressed_settings_action = ""
        self._settings_feedback_action = ""
        self._settings_feedback_timer = QTimer(self)
        self._settings_feedback_timer.setSingleShot(True)
        self._settings_feedback_timer.timeout.connect(self._clear_settings_action_feedback)
        self._menu_icon_renderers: dict[tuple[str, str], QSvgRenderer] = {}
        self._home_motion = {action: QPointF(1.0, 0.0) for action in self.home_motion_ids()}
        self._home_motion_animations: dict[str, QVariantAnimation] = {}
        for ident in self.home_motion_ids():
            animation = QVariantAnimation(self)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(lambda value, key=ident: self._set_home_motion_value(key, value))
            self._home_motion_animations[ident] = animation
        self.toggle_animation_duration = 300
        self._toggle_progress = {"startup": 0.0, "auto": 0.0, "animation": 1.0}
        self._toggle_animations: dict[str, QVariantAnimation] = {}
        for ident in ("startup", "auto", "animation"):
            animation = QVariantAnimation(self)
            animation.setDuration(self.toggle_animation_duration)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(lambda value, key=ident: self._set_toggle_progress(key, value))
            self._toggle_animations[ident] = animation
        self.auto_change_enabled = False
        self.auto_interval_label = "每天"
        self.taskbar_mode = "default"
        self.taskbar_intensity = 88
        self.taskbar_restore_on_start = True
        self.taskbar_all_displays = True
        self.taskbar_status = ""
        self.lock_screen_sync_enabled = False
        self.lock_screen_status = "等待开启"
        self._taskbar_slider_dragging = False
        self._taskbar_toggle_progress = {"restore": 1.0, "displays": 1.0}
        self._taskbar_toggle_animations: dict[str, QVariantAnimation] = {}
        for ident in ("restore", "displays"):
            animation = QVariantAnimation(self)
            animation.setDuration(self.toggle_animation_duration)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(lambda value, key=ident: self._set_taskbar_toggle_progress(key, value))
            self._taskbar_toggle_animations[ident] = animation
        self.animation_progress = 1.0
        self.transition_direction = 1
        self.metadata_progress = 1.0
        self.current_wallpaper: Wallpaper | None = None
        self._current_image = self._placeholder()
        self._previous_image: QImage | None = None
        self._page = "首页"
        self.settings_section = "general"
        self._favorites: set[str] = set()
        self._status = "正在连接高清摄影源…"
        self._toast = ""
        self.wallpaper_group_loading = False
        self.wallpaper_group_loading_progress = 0.0
        self._health: dict[str, object] = {}
        self._wallpapers: list[Wallpaper] = []
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(self.animation_duration)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_animation)
        self._animation.finished.connect(self._finish_animation)
        self._metadata_animation = QVariantAnimation(self)
        self._metadata_animation.setDuration(240)
        self._metadata_animation.setStartValue(0.0)
        self._metadata_animation.setEndValue(1.0)
        self._metadata_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._metadata_animation.valueChanged.connect(self._on_metadata_animation)
        self._metadata_timer = QTimer(self)
        self._metadata_timer.setSingleShot(True)
        self._metadata_timer.timeout.connect(self._metadata_animation.start)
        self._menu_animation = QVariantAnimation(self)
        self._menu_animation.setDuration(220)
        self._menu_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._menu_animation.valueChanged.connect(self._on_menu_animation)
        self._page_animation = QVariantAnimation(self)
        self._page_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._page_animation.valueChanged.connect(self._on_page_animation)
        self._page_animation.finished.connect(self._finish_page_animation)
        self._accent_animation = QVariantAnimation(self)
        self._accent_animation.setDuration(240)
        self._accent_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._accent_animation.valueChanged.connect(self._set_accent_value)
        self._wallpaper_group_loading_animation = QVariantAnimation(self)
        self._wallpaper_group_loading_animation.setDuration(1500)
        self._wallpaper_group_loading_animation.setStartValue(0.0)
        self._wallpaper_group_loading_animation.setEndValue(1.0)
        self._wallpaper_group_loading_animation.setLoopCount(-1)
        self._wallpaper_group_loading_animation.setEasingCurve(
            QEasingCurve.Type.InOutCubic
        )
        self._wallpaper_group_loading_animation.valueChanged.connect(
            self._set_wallpaper_group_loading_progress
        )
        self.operation_loading = False
        self.operation_loading_message = "正在准备壁纸…"
        self.operation_loading_progress = 0.0
        self._operation_loading_animation = QVariantAnimation(self)
        self._operation_loading_animation.setDuration(1200)
        self._operation_loading_animation.setStartValue(0.0)
        self._operation_loading_animation.setEndValue(1.0)
        self._operation_loading_animation.setLoopCount(-1)
        self._operation_loading_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._operation_loading_animation.valueChanged.connect(
            self._set_operation_loading_progress
        )

    @property
    def has_visible_background(self) -> bool:
        return not self._current_image.isNull()

    def _placeholder(self) -> QImage:
        image = QImage(DESIGN_WIDTH, DESIGN_HEIGHT, QImage.Format.Format_RGB32)
        image.fill(QColor("#111820"))
        painter = QPainter(image)
        gradient = QLinearGradient(0, 0, DESIGN_WIDTH, DESIGN_HEIGHT)
        gradient.setColorAt(0, QColor("#172A36"))
        gradient.setColorAt(0.48, QColor("#315A67"))
        gradient.setColorAt(1, QColor("#83633B"))
        painter.fillRect(image.rect(), gradient)
        painter.end()
        return image

    def set_content(
        self,
        wallpaper: Wallpaper,
        image: QImage,
        accent: QColor | None = None,
    ) -> None:
        if image.isNull():
            return
        self.current_wallpaper = wallpaper
        self._animate_accent(accent if accent is not None else extract_accent(image))
        if not self.animation_enabled or self._current_image.isNull():
            self._animation.stop()
            self._metadata_timer.stop()
            self._metadata_animation.stop()
            self._current_image = image
            self._previous_image = None
            self.animation_progress = 1.0
            self.metadata_progress = 1.0
            self.update()
            return
        self._animation.stop()
        self._metadata_timer.stop()
        self._metadata_animation.stop()
        self._previous_image = self._current_image
        self._current_image = image
        self.animation_progress = 0.0
        self.metadata_progress = 0.0
        self._animation.setDuration(self.animation_duration)
        self._animation.start()
        self._metadata_timer.start(50)

    def set_transition_direction(self, direction: int) -> None:
        self.transition_direction = 1 if direction >= 0 else -1

    def current_image_offset_x(self) -> float:
        return self.transition_direction * 28.0 * (1.0 - self.animation_progress)

    def previous_image_offset_x(self) -> float:
        return -self.transition_direction * 14.0 * self.animation_progress

    def current_image_scale(self) -> float:
        return 1.0 + 0.015 * (1.0 - self.animation_progress)

    def previous_image_scale(self) -> float:
        return 1.0 + 0.006 * self.animation_progress

    def metadata_offset_x(self) -> float:
        return self.transition_direction * 16.0 * (1.0 - self.metadata_progress)

    def metadata_opacity(self) -> float:
        return self.metadata_progress

    def set_page(self, page: str) -> None:
        target = page
        self._menu_animation.stop()
        self.menu_open = False
        self.menu_progress = 0.0
        self._page_animation.stop()
        if target == "首页":
            if self._page == "首页":
                self.page_progress = 0.0
                self.page_is_exiting = False
            elif self.animation_enabled:
                self.page_is_exiting = True
                self._page_animation.setDuration(180)
                self._page_animation.setStartValue(self.page_progress)
                self._page_animation.setEndValue(0.0)
                self._page_animation.start()
            else:
                self._page = "首页"
                self.page_progress = 0.0
                self.page_is_exiting = False
        else:
            self._page = target
            self.page_is_exiting = False
            if self.animation_enabled:
                self.page_progress = 0.0
                self._page_animation.setDuration(260)
                self._page_animation.setStartValue(0.0)
                self._page_animation.setEndValue(1.0)
                self._page_animation.start()
            else:
                self.page_progress = 1.0
        self.update()

    @property
    def current_page(self) -> str:
        return self._page

    def set_wallpapers(self, wallpapers: list[Wallpaper]) -> None:
        self._wallpapers = list(wallpapers)
        self.update()

    def set_favorites(self, favorites: set[str]) -> None:
        self._favorites = set(favorites)
        self.update()

    def set_status(self, status: str) -> None:
        self._status = status
        self.update()

    def set_health(self, health: dict[str, object]) -> None:
        self._health = health
        self.update()

    def set_settings_state(self, *, startup_enabled: bool, animation_enabled: bool, animate: bool = True) -> None:
        previous = {"startup": self.startup_enabled, "animation": self.animation_enabled}
        self.startup_enabled = startup_enabled
        self.animation_enabled = animation_enabled
        if previous["animation"] != animation_enabled and not animation_enabled:
            self._animation.stop()
            self._metadata_timer.stop()
            self._metadata_animation.stop()
            self.animation_progress = 1.0
            self.metadata_progress = 1.0
            self._previous_image = None
            self._menu_animation.stop()
            self._page_animation.stop()
            self.menu_progress = 1.0 if self.menu_open else 0.0
            if self.page_is_exiting:
                self._page = "首页"
                self.page_progress = 0.0
                self.page_is_exiting = False
            elif self._page != "首页":
                self.page_progress = 1.0
            for motion_animation in self._home_motion_animations.values():
                motion_animation.stop()
            for ident, taskbar_animation in self._taskbar_toggle_animations.items():
                taskbar_animation.stop()
                self._taskbar_toggle_progress[ident] = 1.0 if self._taskbar_toggle_enabled(ident) else 0.0
            hover_action = self._hover_action
            pressed_action = self._pressed_home_action
            self._hover_action = ""
            self._pressed_home_action = ""
            self.set_home_interaction(hover_action, pressed_action)
        current = {"startup": startup_enabled, "animation": animation_enabled}
        for ident, enabled in current.items():
            target = 1.0 if enabled else 0.0
            if animate and previous[ident] != enabled:
                animation = self._toggle_animations[ident]
                animation.stop()
                animation.setStartValue(self._toggle_progress[ident])
                animation.setEndValue(target)
                animation.start()
            else:
                self._toggle_progress[ident] = target
        self.update()

    def set_taskbar_state(
        self,
        *,
        mode: str,
        intensity: int,
        restore_on_start: bool,
        all_displays: bool,
        status: str = "",
    ) -> None:
        previous = {
            "restore": self.taskbar_restore_on_start,
            "displays": self.taskbar_all_displays,
        }
        self.taskbar_mode = mode if mode in TASKBAR_MODES else "default"
        self.taskbar_intensity = max(20, min(100, int(intensity)))
        self.taskbar_restore_on_start = restore_on_start
        self.taskbar_all_displays = all_displays
        self.taskbar_status = status
        for ident in ("restore", "displays"):
            target = 1.0 if self._taskbar_toggle_enabled(ident) else 0.0
            animation = self._taskbar_toggle_animations[ident]
            animation.stop()
            if self.animation_enabled and previous[ident] != self._taskbar_toggle_enabled(ident):
                animation.setStartValue(self._taskbar_toggle_progress[ident])
                animation.setEndValue(target)
                animation.start()
            else:
                self._taskbar_toggle_progress[ident] = target
        self.update()

    def set_auto_change_state(
        self, enabled: bool, interval_label: str, *, animate: bool = True
    ) -> None:
        previous = self.auto_change_enabled
        self.auto_change_enabled = bool(enabled)
        self.auto_interval_label = interval_label
        target = 1.0 if self.auto_change_enabled else 0.0
        animation = self._toggle_animations["auto"]
        animation.stop()
        if animate and previous != self.auto_change_enabled:
            animation.setStartValue(self._toggle_progress["auto"])
            animation.setEndValue(target)
            animation.start()
        else:
            self._toggle_progress["auto"] = target
        self.update()
    def set_lock_screen_state(
        self, enabled: bool, status: str = "", *, animate: bool = True
    ) -> None:
        previous = self.lock_screen_sync_enabled
        self.lock_screen_sync_enabled = bool(enabled)
        self.lock_screen_status = status
        animation = self._taskbar_toggle_animations["restore"]
        animation.stop()
        target = 1.0 if self.lock_screen_sync_enabled else 0.0
        if animate and self.animation_enabled and previous != self.lock_screen_sync_enabled:
            animation.setStartValue(self._taskbar_toggle_progress["restore"])
            animation.setEndValue(target)
            animation.start()
        else:
            self._taskbar_toggle_progress["restore"] = target
        self.update()

    @staticmethod
    def lock_screen_toggle_rect() -> QRectF:
        return QRectF(1012, 390, 52, 28)

    @staticmethod
    def lock_screen_sync_rect() -> QRectF:
        return QRectF(744, 486, 330, 52)

    @staticmethod
    def about_settings_rect() -> QRectF:
        return WallpaperStage.settings_nav_rects()["about"]

    @staticmethod
    def settings_sidebar_rect() -> QRectF:
        return QRectF(618, 185, 140, 543)

    @staticmethod
    def settings_content_rect() -> QRectF:
        return QRectF(782, 214, 330, 496)

    @staticmethod
    def settings_nav_rects() -> dict[str, QRectF]:
        return {
            "general": QRectF(628, 218, 112, 48),
            "storage": QRectF(628, 274, 112, 48),
            "about": QRectF(628, 330, 112, 48),
        }

    @staticmethod
    def settings_nav_labels() -> dict[str, str]:
        return {"general": "常规", "storage": "存储", "about": "关于"}

    @staticmethod
    def settings_label_font_size() -> int:
        return 13

    @staticmethod
    def settings_section_control_rects() -> dict[str, QRectF]:
        return {
            "startup": QRectF(1038, 272, 58, 30),
            "auto:toggle": QRectF(1038, 352, 58, 30),
            "auto:interval": QRectF(992, 430, 104, 38),
            "animation": QRectF(1038, 512, 58, 30),
            "folder": QRectF(992, 278, 104, 38),
            "cache": QRectF(992, 372, 104, 38),
        }

    def show_settings_action_feedback(self, action: str) -> None:
        if action not in {"auto:interval", "folder", "cache"}:
            return
        self._settings_feedback_action = action
        self._settings_feedback_timer.start(900)
        self.update()

    def _clear_settings_action_feedback(self) -> None:
        self._settings_feedback_action = ""
        self.update()

    def settings_action_at(self, point: QPointF) -> str:
        if self._page != "设置":
            return ""
        controls = self.settings_section_control_rects()
        if self.settings_section == "general":
            if self.auto_change_enabled and controls["auto:interval"].contains(point):
                return "auto:interval"
        elif self.settings_section == "storage":
            for action in ("folder", "cache"):
                if controls[action].contains(point):
                    return action
        return ""

    def set_settings_section(self, section: str) -> None:
        if section not in self.settings_nav_rects():
            return
        self.settings_section = section
        self.update()

    @staticmethod
    def about_info_items() -> tuple[tuple[str, str], ...]:
        return (
            ("产品", "匠猫壁纸"),
            ("版本", "1.0.0\nofficial"),
            ("开发者", "匠心猫"),
            ("开发品牌", "JiangMao Studio"),
        )

    @staticmethod
    def about_legal_notice() -> tuple[str, str]:
        return (
            "来源、版权与隐私",
            "壁纸来自国家公园及联邦自然资源机构的公共领域高清实拍；许可证与原始来源以照片详情页为准。",
        )

    @staticmethod
    def about_manifesto() -> tuple[str, str]:
        return (
            "让喜欢壁纸的人，更轻松地遇见好风景。",
            "匠猫壁纸由匠心猫开发与维护，专注于清晰、安静且可靠的桌面壁纸体验。",
        )

    @staticmethod
    def about_gallery_plate() -> tuple[str, str, str]:
        return (
            "把每日遇见的风景，安静地留在桌面。",
            "匠猫壁纸专注于高清壁纸浏览、收藏、下载与桌面应用。",
            "壁纸来自经许可索引核验的内置官方摄影目录，联网时优先从 NPS 官方风景源继续加载，并保留机构、许可证及原始来源。功能统计仅记录匿名设备、功能调用与崩溃日志，以改善应用。",
        )

    def _taskbar_toggle_enabled(self, ident: str) -> bool:
        return self.taskbar_restore_on_start if ident == "restore" else self.taskbar_all_displays

    def _set_taskbar_toggle_progress(self, ident: str, value) -> None:
        self._taskbar_toggle_progress[ident] = float(value)
        self.update()

    def taskbar_toggle_progress(self, ident: str) -> float:
        return self._taskbar_toggle_progress[ident]

    def _set_toggle_progress(self, ident: str, value) -> None:
        self._toggle_progress[ident] = float(value)
        self.update()

    @staticmethod
    def settings_control_kind(ident: str) -> str:
        return "toggle" if ident in {"startup", "auto", "animation"} else "button"

    def toggle_handle_x(self, ident: str) -> float:
        control = "auto:toggle" if ident == "auto" else ident
        box = self.settings_section_control_rects()[control]
        return self.toggle_handle_rect(box, self._toggle_progress[ident]).x()

    @staticmethod
    def toggle_handle_rect(box: QRectF, progress: float) -> QRectF:
        margin = max(3.0, min(5.0, box.height() / 6.0))
        diameter = max(8.0, box.height() - 2 * margin)
        travel = max(0.0, box.width() - 2 * margin - diameter)
        handle_x = box.x() + margin + travel * max(0.0, min(1.0, progress))
        return QRectF(handle_x, box.y() + margin, diameter, diameter)

    def settings_button_label(self, ident: str) -> str:
        if ident == "startup":
            return "已开启" if self.startup_enabled else "已关闭"
        if ident == "animation":
            return "已开启" if self.animation_enabled else "已关闭"
        return {"auto": self.auto_interval_label, "folder": "选择", "cache": "清理"}[ident]

    def show_toast(self, message: str) -> None:
        self._toast = message
        self.update()

    def set_wallpaper_group_loading(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.wallpaper_group_loading:
            return
        self.wallpaper_group_loading = enabled
        self._wallpaper_group_loading_animation.stop()
        self.wallpaper_group_loading_progress = 0.0
        if enabled:
            self._wallpaper_group_loading_animation.start()
        self.update()

    def set_operation_loading(self, enabled: bool, message: str = "正在准备壁纸…") -> None:
        enabled = bool(enabled)
        if enabled:
            self.operation_loading_message = message
        if enabled == self.operation_loading:
            self.update()
            return
        self.operation_loading = enabled
        self._operation_loading_animation.stop()
        self.operation_loading_progress = 0.0
        if enabled:
            self._operation_loading_animation.start()
        self.update()

    def _set_wallpaper_group_loading_progress(self, value) -> None:
        self.wallpaper_group_loading_progress = float(value)
        self.update()

    def _set_operation_loading_progress(self, value) -> None:
        self.operation_loading_progress = float(value)
        self.update()

    @staticmethod
    def home_control_ids() -> tuple[str, ...]:
        return ("prev", "favorite", "apply", "download", "next")

    @staticmethod
    def home_motion_ids() -> tuple[str, ...]:
        return ("prev", "favorite", "apply", "download", "next", "toggle-menu")

    @staticmethod
    def visible_navigation_labels() -> tuple[str, ...]:
        return ()

    @staticmethod
    def primary_action_label() -> str:
        return "设置壁纸"

    @staticmethod
    def primary_action_font_size() -> int:
        return 14

    def hero_title(self) -> str:
        wallpaper = self.current_wallpaper
        if not wallpaper:
            return "正在发现今日风景"
        return wallpaper.headline.strip() or wallpaper.title.strip() or "高清风景摄影"

    @staticmethod
    def menu_button_rect() -> QRectF:
        return QRectF(1038, 690, 52, 52)

    @staticmethod
    def control_island_rect() -> QRectF:
        return QRectF(586, 684, 510, 64)

    @staticmethod
    def home_control_rects() -> dict[str, QRectF]:
        return {
            "prev": QRectF(592, 690, 52, 52),
            "favorite": QRectF(656, 690, 52, 52),
            "apply": QRectF(726, 684, 166, 64),
            "download": QRectF(910, 690, 52, 52),
            "next": QRectF(974, 690, 52, 52),
        }

    @staticmethod
    def home_control_group_rects() -> dict[str, QRectF]:
        return {
            "navigation": QRectF(586, 684, 128, 64),
            "tools": QRectF(904, 684, 192, 64),
        }

    def home_control_scale(self, action: str) -> float:
        return self._home_motion[action].x()

    def home_control_offset_y(self, action: str) -> float:
        return self._home_motion[action].y()

    def set_home_interaction(self, hover_action: str = "", pressed_action: str = "") -> None:
        if hover_action == self._hover_action and pressed_action == self._pressed_home_action:
            return
        self._hover_action = hover_action
        self._pressed_home_action = pressed_action
        action_ids = self.home_motion_ids()
        hover_index = action_ids.index(hover_action) if hover_action in action_ids else -1
        for index, action in enumerate(action_ids):
            if action == pressed_action:
                target = QPointF(0.94, 0.0)
            elif action == hover_action:
                target = QPointF(1.03, -2.0) if action == "apply" else QPointF(1.10, -3.0)
            elif hover_index >= 0 and abs(index - hover_index) == 1:
                target = QPointF(1.03, 0.0)
            else:
                target = QPointF(1.0, 0.0)
            self._animate_home_motion(action, target)
        self.update()

    def _animate_home_motion(self, action: str, target: QPointF) -> None:
        animation = self._home_motion_animations[action]
        animation.stop()
        if not self.animation_enabled:
            self._set_home_motion_value(action, target)
            return
        current = self._home_motion[action]
        if current == target:
            return
        animation.setStartValue(QPointF(current))
        animation.setEndValue(target)
        if action == self._pressed_home_action:
            duration = 80
        elif target == QPointF(1.0, 0.0):
            duration = 120
        else:
            duration = 140
        animation.setDuration(duration)
        animation.start()

    def _set_home_motion_value(self, action: str, value) -> None:
        self._home_motion[action] = QPointF(value)
        self.update()

    def home_action_at(self, point: QPointF) -> str:
        if self._page != "首页":
            return ""
        menu_action = self.menu_action_at(point)
        if menu_action:
            return menu_action
        if self.menu_button_rect().contains(point):
            return "toggle-menu"
        for action, box in self.home_control_rects().items():
            if box.contains(point):
                return action
        if (
            self.current_wallpaper
            and self.current_wallpaper.copyright_link
            and self.home_source_rect().contains(point)
        ):
            return "open:wallpaper-source"
        return ""

    @staticmethod
    def home_source_rect() -> QRectF:
        return QRectF(48, 665, 520, 42)

    @staticmethod
    def window_control_rects() -> tuple[tuple[str, QRectF], ...]:
        return (
            ("window:minimize", QRectF(1072, 8, 40, 40)),
            ("window:maximize", QRectF(1112, 8, 40, 40)),
            ("window:close", QRectF(1152, 8, 40, 40)),
        )

    @staticmethod
    def window_control_group_rect() -> QRectF:
        return QRectF(1062, 8, 130, 40)

    @staticmethod
    def window_control_group_radius() -> float:
        return 14.0

    @staticmethod
    def window_brand_signal_rect() -> QRectF:
        return QRectF(1066, 17, 2, 22)

    @staticmethod
    def window_icon_rect(button_rect: QRectF) -> QRectF:
        center = button_rect.center()
        return QRectF(center.x() - 6, center.y() - 6, 12, 12)

    @staticmethod
    def minimize_icon_y(button_rect: QRectF) -> float:
        return button_rect.center().y()

    def maximize_icon_mode(self) -> str:
        window = self.window()
        return "restore" if window and window.isMaximized() else "maximize"

    def window_hover_fill(self, action: str) -> QColor:
        return QColor(76, 196, 104, 168)

    @staticmethod
    def menu_panel_rect() -> QRectF:
        return QRectF(682, 474, 408, 204)

    @staticmethod
    def menu_item_rects() -> dict[str, QRectF]:
        return {
            "锁屏同步": QRectF(698, 522, 184, 64),
            "收藏": QRectF(890, 522, 184, 64),
            "历史": QRectF(698, 594, 184, 64),
            "设置": QRectF(890, 594, 184, 64),
        }

    def taskbar_layout_is_compact(self) -> bool:
        return self.width() <= 840 or self.height() <= 560

    @staticmethod
    def taskbar_page_rect() -> QRectF:
        return QRectF(40, 72, 1104, 656)

    def taskbar_preview_rect(self) -> QRectF:
        if self.taskbar_layout_is_compact():
            return QRectF(72, 180, 1056, 220)
        return QRectF(72, 180, 582, 396)

    def taskbar_mode_rects(self) -> dict[str, QRectF]:
        if self.taskbar_layout_is_compact():
            return {
                "default": QRectF(72, 420, 328, 48),
                "transparent": QRectF(436, 420, 328, 48),
                "frosted": QRectF(800, 420, 328, 48),
            }
        return {
            "default": QRectF(92, 602, 174, 58),
            "transparent": QRectF(276, 602, 174, 58),
            "frosted": QRectF(460, 602, 174, 58),
        }

    def taskbar_strength_rect(self) -> QRectF:
        if self.taskbar_layout_is_compact():
            return QRectF(72, 500, 1056, 28)
        return QRectF(744, 300, 330, 28)

    def taskbar_switch_rects(self) -> dict[str, QRectF]:
        if self.taskbar_layout_is_compact():
            return {
                "restore": QRectF(1076, 558, 52, 28),
                "displays": QRectF(1076, 606, 52, 28),
            }
        return {"restore": QRectF(1012, 390, 52, 28), "displays": QRectF(1012, 466, 52, 28)}

    def taskbar_reset_rect(self) -> QRectF:
        if self.taskbar_layout_is_compact():
            return QRectF(72, 664, 1056, 42)
        return QRectF(744, 548, 330, 42)

    def menu_action_at(self, point: QPointF) -> str:
        if not self.menu_open:
            return ""
        for page, box in self.menu_item_rects().items():
            if box.contains(point):
                return f"page:{page}"
        return ""

    def toggle_menu(self) -> None:
        self._menu_animation.stop()
        self.menu_open = not self.menu_open
        if self.menu_open:
            if self.animation_enabled:
                self.menu_progress = 0.0
                self._menu_animation.setStartValue(0.0)
                self._menu_animation.setEndValue(1.0)
                self._menu_animation.start()
            else:
                self.menu_progress = 1.0
        else:
            self.menu_progress = 0.0
        self.update()

    def menu_motion_offset_y(self) -> float:
        return 12.0 * (1.0 - self.menu_progress)

    def menu_motion_scale(self) -> float:
        return 0.98 + 0.02 * self.menu_progress

    def menu_motion_opacity(self) -> float:
        return self.menu_progress

    def page_motion_offset_x(self) -> float:
        return 18.0 * (1.0 - self.page_progress) if not self.page_is_exiting else 10.0 * (1.0 - self.page_progress)

    def page_motion_scale(self) -> float:
        return 0.985 + 0.015 * self.page_progress

    def page_motion_opacity(self) -> float:
        return self.page_progress

    def _animate_accent(self, target: QColor) -> None:
        if not self.animation_enabled:
            self.accent = QColor(target)
            self.update()
            return
        self._accent_animation.stop()
        self._accent_animation.setStartValue(QColor(self.accent))
        self._accent_animation.setEndValue(QColor(target))
        self._accent_animation.start()

    def _set_accent_value(self, value) -> None:
        self.accent = QColor(value)
        self.update()

    def _on_animation(self, value) -> None:
        self.animation_progress = float(value)
        self.update()

    def _on_metadata_animation(self, value) -> None:
        self.metadata_progress = float(value)
        self.update()

    def _on_menu_animation(self, value) -> None:
        self.menu_progress = float(value)
        self.update()

    def _on_page_animation(self, value) -> None:
        self.page_progress = float(value)
        self.update()

    def _finish_page_animation(self) -> None:
        if self.page_is_exiting:
            self._page = "首页"
            self.page_progress = 0.0
            self.page_is_exiting = False
        else:
            self.page_progress = 1.0
        self.update()

    def _finish_animation(self) -> None:
        self.animation_progress = 1.0
        self._previous_image = None
        self.update()

    def _viewport(self) -> tuple[float, float, float]:
        scale = min(self.width() / DESIGN_WIDTH, self.height() / DESIGN_HEIGHT)
        return scale, (self.width() - DESIGN_WIDTH * scale) / 2, (self.height() - DESIGN_HEIGHT * scale) / 2

    def _logical_point(self, point: QPointF) -> QPointF:
        scale, offset_x, offset_y = self._viewport()
        return QPointF((point.x() - offset_x) / scale, (point.y() - offset_y) / scale)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        painter.fillRect(self.rect(), QColor("#07090C"))
        scale, offset_x, offset_y = self._viewport()
        background_target = QRectF(self.rect())
        if self._previous_image is not None:
            painter.setOpacity(1.0 - self.animation_progress)
            self._draw_transformed_cover(
                painter,
                self._previous_image,
                background_target,
                self.previous_image_offset_x() * scale,
                self.previous_image_scale(),
            )
        painter.setOpacity(self.animation_progress if self._previous_image is not None else 1.0)
        self._draw_transformed_cover(
            painter,
            self._current_image,
            background_target,
            (self.current_image_offset_x() * scale) if self._previous_image is not None else 0.0,
            self.current_image_scale() if self._previous_image is not None else 1.0,
        )
        painter.setOpacity(1.0)
        self._draw_readability_overlay(painter)
        painter.translate(offset_x, offset_y)
        painter.scale(scale, scale)
        if self._page == "首页":
            self._draw_home(painter)
        else:
            self._draw_page(painter)
        self._draw_window_controls(painter)
        if self.wallpaper_group_loading and self._page == "首页":
            self._draw_wallpaper_group_loading(painter)
        elif self.operation_loading and self._page == "首页":
            self._draw_operation_loading(painter)
        if self.menu_open:
            self._draw_menu(painter)
        if self._toast and not self.wallpaper_group_loading and not self.operation_loading:
            self._draw_toast(painter)
        painter.end()

    def _draw_cover(self, painter: QPainter, image: QImage, target: QRectF) -> None:
        source_ratio = image.width() / max(1, image.height())
        target_ratio = target.width() / target.height()
        if source_ratio > target_ratio:
            source_width = image.height() * target_ratio
            source = QRectF((image.width() - source_width) / 2, 0, source_width, image.height())
        else:
            source_height = image.width() / target_ratio
            source = QRectF(0, (image.height() - source_height) / 2, image.width(), source_height)
        painter.drawImage(target, image, source)

    def _draw_transformed_cover(self, painter: QPainter, image: QImage, target: QRectF, offset_x: float, image_scale: float) -> None:
        painter.save()
        center = target.center()
        coverage_scale = 1.0 + (2.0 * abs(offset_x) / target.width())
        image_scale = max(image_scale, coverage_scale)
        painter.translate(center.x() + offset_x, center.y())
        painter.scale(image_scale, image_scale)
        painter.translate(-center.x(), -center.y())
        self._draw_cover(painter, image, target)
        painter.restore()

    def _draw_readability_overlay(self, painter: QPainter) -> None:
        viewport = QRectF(self.rect())
        bottom = QLinearGradient(0, viewport.height() * 0.58, 0, viewport.height())
        bottom.setColorAt(0, QColor(0, 0, 0, 0))
        bottom.setColorAt(1, QColor(0, 0, 0, 132))
        painter.fillRect(viewport, bottom)

        information = QRadialGradient(
            QPointF(viewport.width() * 0.18, viewport.height() * 0.82),
            min(viewport.width() * 0.32, viewport.height() * 0.42),
        )
        information.setColorAt(0, QColor(0, 0, 0, 62))
        information.setColorAt(1, QColor(0, 0, 0, 0))
        painter.fillRect(viewport, information)

    def _rounded(self, painter: QPainter, box: QRectF, fill: str | QColor, radius: float, stroke: str | QColor | None = None) -> None:
        painter.setPen(QPen(QColor(stroke) if isinstance(stroke, str) else stroke, 1) if stroke else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill) if isinstance(fill, str) else fill)
        painter.drawRoundedRect(box, radius, radius)

    def _text(self, painter: QPainter, box: QRectF, text: str, color: str, size: int, weight=QFont.Weight.Normal, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) -> None:
        painter.setPen(QColor(color))
        painter.setFont(ui_font(size, weight))
        painter.drawText(box, align, text)

    def _fitted_text(self, painter: QPainter, box: QRectF, text: str, color: str, size: int, minimum: int, weight=QFont.Weight.Black, align=Qt.AlignmentFlag.AlignCenter) -> None:
        chosen = size
        while chosen > minimum:
            font = ui_font(chosen, weight)
            if QFontMetricsF(font).horizontalAdvance(text) <= box.width():
                break
            chosen -= 1
        self._text(painter, box, text, color, chosen, weight, align)

    def _draw_home(self, painter: QPainter) -> None:
        wallpaper = self.current_wallpaper
        date_label = format_wallpaper_date(wallpaper.startdate) if wallpaper else "精选 · OPEN COLLECTION"
        title = self.hero_title()
        copyright_text = wallpaper.copyright if wallpaper else self._status
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.accent)
        painter.drawRoundedRect(QRectF(48, 42, 3, 48), 1.5, 1.5)
        self._text(painter, QRectF(64, 42, 180, 24), "匠猫壁纸", WHITE, 15, QFont.Weight.Bold)
        self._text(painter, QRectF(64, 66, 180, 18), "DAILY GALLERY", "#CFD5DC", 9, QFont.Weight.DemiBold)
        painter.save()
        painter.setOpacity(self.metadata_opacity())
        painter.translate(self.metadata_offset_x(), 0)
        self._text(painter, QRectF(48, 576, 430, 22), date_label, self.accent.name(), 11, QFont.Weight.Black)
        self._fitted_text(painter, QRectF(48, 604, 540, 62), title, WHITE, 38, 24, QFont.Weight.Bold, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        source_hovered = self._hover_action == "open:wallpaper-source"
        source_color = self.accent.name() if source_hovered else "#D5DBE1"
        source_suffix = "  ↗" if wallpaper and wallpaper.copyright_link else ""
        self._fitted_text(
            painter,
            self.home_source_rect(),
            f"{copyright_text}{source_suffix}",
            source_color,
            11,
            8,
            QFont.Weight.DemiBold if source_hovered else QFont.Weight.Medium,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        painter.restore()
        self._draw_control_island(painter)

    def _draw_control_island(self, painter: QPainter) -> None:
        for group in self.home_control_group_rects().values():
            shadow = group.translated(0, 4)
            self._rounded(painter, shadow, QColor(0, 0, 0, 42), 22)
            self._rounded(painter, group, QColor(10, 18, 27, 184), 22, QColor(255, 255, 255, 52))
            painter.setPen(QPen(QColor(255, 255, 255, 24), 1))
            painter.drawLine(
                QPointF(group.left() + 18, group.top() + 1),
                QPointF(group.right() - 18, group.top() + 1),
            )
        boxes = self.home_control_rects()
        controls = [
            ("prev", boxes["prev"], False),
            ("favorite", boxes["favorite"], False),
            ("apply", boxes["apply"], True),
            ("download", boxes["download"], False),
            ("next", boxes["next"], False),
        ]
        for ident, box, dark in controls:
            painter.save()
            self._apply_home_motion_transform(painter, ident, box)
            if dark:
                fill = self.accent.lighter(108) if self._hover_action == ident else self.accent
                self._rounded(painter, box, fill, 20, fill)
            elif self._hover_action == ident:
                self._rounded(painter, box.adjusted(3, 3, -3, -3), QColor(255, 255, 255, 18), 16)
            self._draw_control_icon(painter, ident, box, DARK if dark else WHITE, self.current_wallpaper)
            painter.restore()
        self._draw_menu_button(painter)

    def _apply_home_motion_transform(self, painter: QPainter, action: str, box: QRectF) -> None:
        motion = self._home_motion[action]
        center = box.center()
        painter.translate(center.x(), center.y() + motion.y())
        painter.scale(motion.x(), motion.x())
        painter.translate(-center.x(), -center.y())

    def _draw_menu_button(self, painter: QPainter) -> None:
        box = self.menu_button_rect()
        hovered = self._hover_action == "toggle-menu"
        painter.save()
        self._apply_home_motion_transform(painter, "toggle-menu", box)
        if hovered:
            self._rounded(painter, box.adjusted(3, 3, -3, -3), QColor(255, 255, 255, 18), 16)
        painter.setPen(QPen(QColor("#F4F6F8"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for offset_y in (-8, 0, 8):
            painter.drawLine(
                QPointF(box.center().x() - 9, box.center().y() + offset_y),
                QPointF(box.center().x() + 9, box.center().y() + offset_y),
            )
        painter.restore()

    def _draw_control_icon(self, painter: QPainter, ident: str, box: QRectF, color: str, wallpaper: Wallpaper | None) -> None:
        pen = QPen(QColor(color), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        center = box.center()
        if ident in {"prev", "next"}:
            direction = -1 if ident == "prev" else 1
            path = QPainterPath(QPointF(center.x() - direction * 4, center.y() - 7))
            path.lineTo(QPointF(center.x() + direction * 4, center.y()))
            path.lineTo(QPointF(center.x() - direction * 4, center.y() + 7))
            painter.drawPath(path)
        elif ident == "download":
            painter.drawLine(QPointF(center.x(), center.y() - 9), QPointF(center.x(), center.y() + 5))
            painter.drawLine(QPointF(center.x() - 5, center.y()), QPointF(center.x(), center.y() + 5))
            painter.drawLine(QPointF(center.x() + 5, center.y()), QPointF(center.x(), center.y() + 5))
            painter.drawLine(QPointF(center.x() - 7, center.y() + 10), QPointF(center.x() + 7, center.y() + 10))
        elif ident == "favorite":
            path = QPainterPath(QPointF(center.x(), center.y() + 9))
            path.cubicTo(center.x() - 14, center.y() + 1, center.x() - 10, center.y() - 10, center.x(), center.y() - 4)
            path.cubicTo(center.x() + 10, center.y() - 10, center.x() + 14, center.y() + 1, center.x(), center.y() + 9)
            if wallpaper and wallpaper.key in self._favorites:
                painter.setBrush(QColor(color))
            painter.drawPath(path)
        else:
            screen = QRectF(box.x() + 17, box.center().y() - 6, 14, 10)
            painter.drawRoundedRect(screen, 1.5, 1.5)
            painter.drawLine(QPointF(screen.center().x(), screen.bottom()), QPointF(screen.center().x(), screen.bottom() + 4))
            self._text(
                painter,
                QRectF(box.x() + 38, box.y(), 114, box.height()),
                self.primary_action_label(),
                color,
                self.primary_action_font_size(),
                QFont.Weight.Black,
                Qt.AlignmentFlag.AlignCenter,
            )

    def _draw_window_controls(self, painter: QPainter) -> None:
        group = self.window_control_group_rect()
        self._rounded(
            painter,
            group,
            QColor(8, 12, 17, 168),
            self.window_control_group_radius(),
            QColor(255, 255, 255, 36),
        )
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawLine(QPointF(group.left() + 14, group.top() + 1), QPointF(group.right() - 14, group.top() + 1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.accent)
        painter.drawRoundedRect(self.window_brand_signal_rect(), 1, 1)
        for action, box in self.window_control_rects():
            ident = action.split(":", 1)[1]
            hovered = self._hover_action == action
            if hovered:
                fill = self.window_hover_fill(action)
                self._rounded(painter, box.adjusted(3, 3, -3, -3), fill, 10)
            painter.setPen(QPen(QColor("#F3F5F7"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            icon = self.window_icon_rect(box)
            if ident == "minimize":
                y = self.minimize_icon_y(box)
                painter.drawLine(QPointF(icon.left() + 1, y), QPointF(icon.right() - 1, y))
            elif ident == "maximize":
                if self.maximize_icon_mode() == "restore":
                    painter.drawRoundedRect(icon.adjusted(3.2, 0.8, -0.8, -3.2), 1, 1)
                    painter.drawRoundedRect(icon.adjusted(0.8, 3.2, -3.2, -0.8), 1, 1)
                else:
                    painter.drawRoundedRect(icon.adjusted(0.8, 0.8, -0.8, -0.8), 1, 1)
            else:
                painter.drawLine(icon.topLeft() + QPointF(1, 1), icon.bottomRight() - QPointF(1, 1))
                painter.drawLine(icon.topRight() + QPointF(-1, 1), icon.bottomLeft() + QPointF(1, -1))

    def _menu_icon_renderer(self, icon_name: str, color: QColor) -> QSvgRenderer | None:
        key = (icon_name, color.name())
        renderer = self._menu_icon_renderers.get(key)
        if renderer is not None:
            return renderer
        icon_path = Path(__file__).with_name("assets") / "menu" / f"{icon_name}.svg"
        try:
            svg = icon_path.read_bytes().replace(b"currentColor", color.name().encode("ascii"))
        except OSError:
            return None
        renderer = QSvgRenderer(QByteArray(svg), self)
        if not renderer.isValid():
            return None
        self._menu_icon_renderers[key] = renderer
        return renderer

    def _draw_menu_icon(self, painter: QPainter, icon_name: str, box: QRectF, color: QColor) -> None:
        renderer = self._menu_icon_renderer(icon_name, color)
        if renderer is not None:
            renderer.render(painter, box)

    def _draw_menu(self, painter: QPainter) -> None:
        panel = self.menu_panel_rect()
        opacity = self.menu_motion_opacity()
        painter.save()
        painter.setOpacity(opacity)
        origin = self.menu_button_rect().center()
        painter.translate(origin.x(), origin.y() + self.menu_motion_offset_y())
        painter.scale(self.menu_motion_scale(), self.menu_motion_scale())
        painter.translate(-origin.x(), -origin.y())

        self._rounded(painter, panel.translated(0, 4), QColor(0, 0, 0, 52), 20)
        self._rounded(painter, panel, QColor(8, 13, 17, 224), 20, QColor(255, 255, 255, 46))
        self._text(painter, QRectF(704, 492, 180, 18), "GALLERY MENU", self.accent.name(), 9, QFont.Weight.Black)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawLine(QPointF(886, 528), QPointF(886, 652))
        painter.drawLine(QPointF(704, 590), QPointF(1068, 590))
        painter.setPen(QPen(QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 112), 1.5))
        painter.drawLine(QPointF(1064, panel.bottom()), QPointF(1064, self.menu_button_rect().top()))

        items = [
            ("锁屏同步", "桌面与锁屏", "monitor-cog"),
            ("收藏", "珍藏的风景", "heart"),
            ("历史", "最近同步", "history"),
            ("设置", "自动化与存储", "settings-2"),
        ]
        boxes = self.menu_item_rects()
        for index, (label, subtitle, icon_name) in enumerate(items):
            box = boxes[label]
            hovered = self._hover_action == f"page:{label}"
            active = self._page == label
            start = index * 0.08
            item_progress = max(0.0, min(1.0, (self.menu_progress - start) / max(0.01, 1.0 - start)))
            painter.save()
            painter.setOpacity(opacity * item_progress)
            painter.translate(0, 5 * (1.0 - item_progress))
            if hovered or active:
                fill = QColor(
                    self.accent.red(),
                    self.accent.green(),
                    self.accent.blue(),
                    42 if active else 28,
                )
                self._rounded(painter, box, fill, 8)
            icon_color = self.accent.lighter(118) if hovered or active else QColor("#E8EDF0")
            self._draw_menu_icon(painter, icon_name, QRectF(box.x() + 14, box.y() + 18, 28, 28), icon_color)
            self._text(painter, QRectF(box.x() + 54, box.y() + 10, 104, 22), label, WHITE, 13, QFont.Weight.Bold)
            self._text(painter, QRectF(box.x() + 54, box.y() + 33, 112, 18), subtitle, "#A8B1BA", 9, QFont.Weight.Medium)
            if hovered or active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(icon_color)
                painter.drawEllipse(QRectF(box.right() - 14, box.center().y() - 2.5, 5, 5))
            painter.restore()
        painter.restore()

    def _draw_page(self, painter: QPainter) -> None:
        painter.fillRect(QRectF(0, 0, DESIGN_WIDTH, DESIGN_HEIGHT), QColor(3, 6, 10, 72))
        taskbar_page = self._page == "锁屏同步"
        panel = QRectF(40, 72, 1104, 656) if taskbar_page else QRectF(618, 72, 526, 656)
        painter.save()
        painter.setOpacity(self.page_motion_opacity())
        center = panel.center()
        painter.translate(center.x() + self.page_motion_offset_x(), center.y())
        painter.scale(self.page_motion_scale(), self.page_motion_scale())
        painter.translate(-center.x(), -center.y())
        self._rounded(painter, panel, QColor(12, 18, 25, 236), 28, QColor(255, 255, 255, 46))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.accent)
        header_x = 72 if taskbar_page else 658
        painter.drawRoundedRect(QRectF(panel.x(), 112, 3, 56), 1.5, 1.5)
        painter.fillRect(QRectF(panel.x() + 1, 73, panel.width() - 2, 112), QColor(255, 255, 255, 8))
        eyebrow = {"收藏": "SAVED MOMENTS", "历史": "PHOTO ARCHIVE", "设置": "PREFERENCES", "关于": "ABOUT JIANGMAO", "锁屏同步": "LOCK SCREEN"}.get(self._page, "WALLPAPER GALLERY")
        self._text(painter, QRectF(header_x, 103, 300, 22), eyebrow, self.accent.name(), 10, QFont.Weight.Black)
        self._text(painter, QRectF(header_x, 126, 360, 48), self._page, WHITE, 30, QFont.Weight.Black)
        if self._page == "关于":
            self._draw_back_button(painter)
        else:
            self._draw_close_button(painter)
        if taskbar_page:
            status = self.lock_screen_status
            self._text(painter, QRectF(744, 126, 294, 42), status, "#B9C1CB", 11, QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._draw_lock_screen_page(painter)
        elif self._page == "收藏":
            count = len(self._favorites)
            body = f"珍藏了 {count} 个风景瞬间" if count else "点亮首页的心形，把喜欢的风景留在这里。"
            self._text(painter, QRectF(658, 174, 410, 32), body, "#B9C1CB", 12, QFont.Weight.DemiBold)
            self._draw_library_rows(painter, [item for item in self._wallpapers if item.key in self._favorites])
        elif self._page == "历史":
            self._text(painter, QRectF(658, 174, 410, 32), "公共领域 · 官方实拍 · 高清原图", "#B9C1CB", 12, QFont.Weight.DemiBold)
            self._draw_library_rows(painter, self._wallpapers)
        elif self._page == "关于":
            self._draw_about(painter)
        else:
            self._draw_settings(painter)
        painter.restore()

    def _draw_close_button(self, painter: QPainter) -> None:
        box = QRectF(1072, 100, 40, 40)
        self._rounded(painter, box, QColor(255, 255, 255, 18), 20, QColor(255, 255, 255, 46))
        painter.setPen(QPen(QColor("#E9EDF2"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(1085, 113), QPointF(1099, 127))
        painter.drawLine(QPointF(1099, 113), QPointF(1085, 127))

    def _draw_back_button(self, painter: QPainter) -> None:
        box = QRectF(1072, 100, 40, 40)
        self._rounded(painter, box, QColor(255, 255, 255, 18), 20, QColor(255, 255, 255, 46))
        painter.setPen(QPen(QColor("#E9EDF2"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(1098, 112), QPointF(1086, 120))
        painter.drawLine(QPointF(1086, 120), QPointF(1098, 128))

    def _draw_about(self, painter: QPainter) -> None:
        info = dict(self.about_info_items())
        manifesto_title, manifesto_body = self.about_manifesto()
        legal_title, legal_body = self.about_legal_notice()
        self._text(
            painter,
            QRectF(650, 218, 462, 62),
            manifesto_title,
            WHITE,
            20,
            QFont.Weight.Black,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        )
        self._text(
            painter,
            QRectF(650, 290, 448, 46),
            manifesto_body,
            "#A7B0B9",
            10,
            QFont.Weight.Medium,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.accent)
        painter.drawRoundedRect(QRectF(650, 352, 44, 3), 1.5, 1.5)

        painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
        painter.drawLine(QPointF(814, 380), QPointF(814, 568))

        self._text(painter, QRectF(650, 380, 144, 18), "开发信息", self.accent.name(), 9, QFont.Weight.Black)
        self._text(
            painter,
            QRectF(650, 410, 144, 82),
            f"{info['开发者']}\n{info['开发品牌']}\n版本 {info['版本'].replace(chr(10), ' ')}",
            "#E8ECEA",
            12,
            QFont.Weight.DemiBold,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )

        self._text(painter, QRectF(834, 380, 278, 18), legal_title, self.accent.name(), 9, QFont.Weight.Black)
        self._text(
            painter,
            QRectF(834, 410, 278, 88),
            legal_body,
            "#D5DBE1",
            11,
            QFont.Weight.Medium,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        )
        self._text(
            painter,
            QRectF(834, 508, 278, 52),
            "功能统计仅记录匿名设备与功能调用；不读取壁纸内容、文件路径或个人文档。",
            "#8F99A3",
            9,
            QFont.Weight.Medium,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        )

        painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
        painter.drawLine(QPointF(650, 586), QPointF(1112, 586))
        painter.drawLine(QPointF(650, 632), QPointF(1112, 632))
        self._text(painter, QRectF(650, 587, 120, 45), "产品", "#8F99A3", 9, QFont.Weight.Medium)
        self._text(painter, QRectF(790, 587, 322, 45), info["产品"], WHITE, 10, QFont.Weight.Black, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._text(painter, QRectF(650, 650, 462, 18), "© 2026 JiangMao Studio", "#717C86", 9, QFont.Weight.DemiBold)

    @staticmethod
    def _taskbar_mode_label(mode: str) -> str:
        return {"default": "系统默认", "transparent": "完全透明", "frosted": "轻度磨砂"}.get(mode, "系统默认")

    def _draw_lock_screen_page(self, painter: QPainter) -> None:
        preview = self.taskbar_preview_rect()
        path = QPainterPath()
        path.addRoundedRect(preview, 8, 8)
        painter.save()
        painter.setClipPath(path)
        self._draw_cover(painter, self._current_image, preview)
        shade = QLinearGradient(0, preview.y(), 0, preview.bottom())
        shade.setColorAt(0, QColor(0, 0, 0, 6))
        shade.setColorAt(1, QColor(0, 0, 0, 100))
        painter.fillRect(preview, shade)
        self._text(
            painter,
            QRectF(preview.x() + 24, preview.bottom() - 66, preview.width() - 48, 28),
            "锁屏预览",
            WHITE,
            13,
            QFont.Weight.Black,
        )
        painter.restore()
        self._rounded(painter, preview, QColor(0, 0, 0, 0), 8, QColor(255, 255, 255, 52))

        self._text(painter, QRectF(744, 222, 330, 32), "锁屏同步", WHITE, 17, QFont.Weight.Black)
        self._text(
            painter,
            QRectF(744, 258, 330, 76),
            "设置桌面壁纸后，自动将同一张 4K 原图应用到 Windows 锁屏。",
            "#B9C1CB",
            11,
            QFont.Weight.Medium,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        )
        self._draw_taskbar_switch(
            painter, "restore", "自动同步", self.lock_screen_sync_enabled
        )
        sync = self.lock_screen_sync_rect()
        self._rounded(painter, sync, self.accent, 8, QColor(255, 255, 255, 72))
        self._text(painter, sync, "立即同步当前壁纸", "#111820", 11, QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)
        self._text(
            painter,
            QRectF(744, 558, 330, 52),
            "图片会保存在本机应用目录，清理缓存不会影响当前锁屏。",
            "#8F99A3",
            9,
            QFont.Weight.Medium,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        )
    def _draw_taskbar_page(self, painter: QPainter) -> None:
        self._draw_taskbar_preview(painter, self.taskbar_preview_rect())
        self._draw_taskbar_modes(painter)
        self._draw_taskbar_strength(painter)
        self._draw_taskbar_switch(painter, "restore", "启动时恢复", self.taskbar_restore_on_start)
        self._draw_taskbar_switch(painter, "displays", "多显示器同步", self.taskbar_all_displays)
        self._draw_taskbar_reset(painter, self.taskbar_reset_rect())

    def _draw_taskbar_preview(self, painter: QPainter, box: QRectF) -> None:
        path = QPainterPath()
        path.addRoundedRect(box, 8, 8)
        painter.save()
        painter.setClipPath(path)
        self._draw_cover(painter, self._current_image, box)
        shade = QLinearGradient(0, box.y(), 0, box.bottom())
        shade.setColorAt(0, QColor(0, 0, 0, 8))
        shade.setColorAt(1, QColor(0, 0, 0, 84))
        painter.fillRect(box, shade)
        taskbar = QRectF(box.x(), box.bottom() - 52, box.width(), 52)
        if self.taskbar_mode == "default":
            taskbar_fill = QColor(25, 29, 34, 238)
        elif self.taskbar_mode == "transparent":
            taskbar_fill = QColor(9, 14, 20, round(78 - 60 * self.taskbar_intensity / 100))
        else:
            taskbar_fill = QColor(32, 42, 48, round(92 + 96 * self.taskbar_intensity / 100))
        painter.fillRect(taskbar, taskbar_fill)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(5):
            fill = self.accent if index == 0 else QColor(241, 244, 247, 210)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(taskbar.x() + 20 + index * 36, taskbar.y() + 14, 24, 24), 6, 6)
        self._text(painter, QRectF(taskbar.right() - 132, taskbar.y(), 112, taskbar.height()), "10:28  2026/07/11", "#EDF1F4", 9, QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        painter.restore()
        painter.setPen(QPen(QColor(255, 255, 255, 48), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 8, 8)

    def _draw_taskbar_modes(self, painter: QPainter) -> None:
        labels = {"default": "系统默认", "transparent": "完全透明", "frosted": "轻度磨砂"}
        for mode, box in self.taskbar_mode_rects().items():
            selected = mode == self.taskbar_mode
            fill = QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 54) if selected else QColor(255, 255, 255, 12)
            stroke = self.accent if selected else QColor(255, 255, 255, 42)
            self._rounded(painter, box, fill, 8, stroke)
            self._text(painter, box, labels[mode], WHITE if selected else "#C4CBD2", 11, QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)

    def _draw_taskbar_strength(self, painter: QPainter) -> None:
        box = self.taskbar_strength_rect()
        enabled = self.taskbar_mode != "default"
        alpha = 255 if enabled else 92
        label_y = box.y() - (30 if self.taskbar_layout_is_compact() else 64)
        self._text(painter, QRectF(box.x(), label_y, 220, 28), "效果强度", QColor(255, 255, 255, alpha).name(QColor.NameFormat.HexArgb), 13, QFont.Weight.Black)
        self._text(painter, QRectF(box.right() - 60, label_y, 60, 28), f"{self.taskbar_intensity}%", QColor(self.accent.red(), self.accent.green(), self.accent.blue(), alpha).name(QColor.NameFormat.HexArgb), 11, QFont.Weight.Black, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        track = QRectF(box.x(), box.center().y() - 3, box.width(), 6)
        self._rounded(painter, track, QColor(255, 255, 255, 38 if enabled else 18), 3)
        ratio = (self.taskbar_intensity - 20) / 80
        active = QRectF(track.x(), track.y(), track.width() * ratio, track.height())
        self._rounded(painter, active, QColor(self.accent.red(), self.accent.green(), self.accent.blue(), alpha), 3)
        painter.setPen(QPen(QColor(255, 255, 255, alpha), 2))
        painter.setBrush(QColor(self.accent.red(), self.accent.green(), self.accent.blue(), alpha))
        painter.drawEllipse(QPointF(track.x() + track.width() * ratio, track.center().y()), 8, 8)
        if not enabled:
            self._text(painter, QRectF(box.x(), box.bottom() + 6, box.width(), 22), "选择透明或磨砂模式后可调节", "#7E8790", 9, QFont.Weight.Medium)

    def _draw_taskbar_switch(self, painter: QPainter, ident: str, label: str, enabled: bool) -> None:
        box = self.taskbar_switch_rects()[ident]
        progress = self._taskbar_toggle_progress[ident]
        label_x = 72 if self.taskbar_layout_is_compact() else 744
        self._text(painter, QRectF(label_x, box.y() - 8, box.x() - label_x - 12, 44), label, WHITE, 12, QFont.Weight.Black)
        track = QColor(
            round(75 + (self.accent.red() - 75) * progress),
            round(81 + (self.accent.green() - 81) * progress),
            round(88 + (self.accent.blue() - 88) * progress),
            230,
        )
        self._rounded(painter, box, track, 8, self.accent if enabled else QColor(255, 255, 255, 52))
        handle_x = box.x() + 3 + 24 * progress
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(handle_x, box.y() + 3, 22, 22))

    def _draw_taskbar_reset(self, painter: QPainter, box: QRectF) -> None:
        self._rounded(painter, box, QColor(255, 255, 255, 12), 8, QColor(255, 255, 255, 52))
        self._text(painter, box, "恢复系统默认", WHITE, 11, QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)

    def _set_taskbar_intensity_from_x(self, x: float) -> None:
        box = self.taskbar_strength_rect()
        ratio = max(0.0, min(1.0, (x - box.left()) / box.width()))
        intensity = max(20, min(100, round(20 + ratio * 80)))
        if intensity == self.taskbar_intensity:
            return
        self.taskbar_intensity = intensity
        self.update()
        self.action_requested.emit(f"taskbar:intensity:{intensity}")

    def _draw_library_rows(self, painter: QPainter, wallpapers: list[Wallpaper]) -> None:
        for index, wallpaper in enumerate(wallpapers[:4]):
            y = 230 + index * 104
            box = QRectF(650, y, 462, 88)
            self._rounded(painter, box, QColor(255, 255, 255, 9), 18, QColor(255, 255, 255, 28))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.accent if wallpaper is self.current_wallpaper else QColor(255, 255, 255, 34))
            painter.drawRoundedRect(QRectF(650, y + 18, 3, 52), 1.5, 1.5)
            self._text(painter, QRectF(674, y + 12, 292, 28), wallpaper.title, WHITE, 13, QFont.Weight.Black)
            self._text(painter, QRectF(674, y + 44, 292, 20), f"{self._compact_date(wallpaper.startdate)}  ·  {wallpaper.provider}", "#9FA9B5", 10, QFont.Weight.DemiBold)
            painter.setPen(QPen(QColor("#C7CFD7"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(1050, y + 36), QPointF(1058, y + 44))
            painter.drawLine(QPointF(1058, y + 44), QPointF(1050, y + 52))

    @staticmethod
    def _compact_date(startdate: str) -> str:
        if len(startdate) == 8 and startdate.isdigit():
            return f"{startdate[:4]}.{startdate[4:6]}.{startdate[6:]}"
        return "今日"

    def _draw_settings(self, painter: QPainter) -> None:
        sidebar = self.settings_sidebar_rect()
        painter.fillRect(sidebar, QColor(255, 255, 255, 5))
        painter.setPen(QPen(QColor(255, 255, 255, 24), 1))
        painter.drawLine(QPointF(sidebar.right(), sidebar.top()), QPointF(sidebar.right(), sidebar.bottom()))

        labels = self.settings_nav_labels()
        for section, box in self.settings_nav_rects().items():
            selected = section == self.settings_section
            hovered = self._hover_action == f"settings:section:{section}"
            if selected or hovered:
                fill = QColor(
                    self.accent.red(),
                    self.accent.green(),
                    self.accent.blue(),
                    38 if selected else 22,
                )
                self._rounded(painter, box, fill, 8)
            if selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.accent)
                painter.drawEllipse(QRectF(box.x() + 14, box.center().y() - 3, 6, 6))
            self._text(
                painter,
                QRectF(box.x() + (30 if selected else 18), box.y(), box.width() - 38, box.height()),
                labels[section],
                WHITE if selected else "#AEB6BE",
                self.settings_label_font_size(),
                QFont.Weight.Black if selected else QFont.Weight.DemiBold,
            )

        if self.settings_section == "storage":
            self._draw_settings_storage(painter)
        elif self.settings_section == "about":
            self._draw_settings_about(painter)
        else:
            self._draw_settings_general(painter)

    def _draw_settings_heading(self, painter: QPainter, title: str, subtitle: str) -> None:
        content = self.settings_content_rect()
        self._text(painter, QRectF(content.x(), 214, content.width(), 28), title, WHITE, 15, QFont.Weight.Black)
        self._text(painter, QRectF(content.x(), 242, content.width(), 20), subtitle, "#8F99A3", 10, QFont.Weight.Medium)

    def _draw_settings_row(
        self,
        painter: QPainter,
        y: float,
        title: str,
        subtitle: str,
    ) -> None:
        content = self.settings_content_rect()
        painter.setPen(QPen(QColor(255, 255, 255, 24), 1))
        painter.drawLine(QPointF(content.left(), y + 69), QPointF(content.right(), y + 69))
        self._text(
            painter,
            QRectF(content.x(), y + 8, 220, 24),
            title,
            WHITE,
            self.settings_label_font_size(),
            QFont.Weight.Black,
        )
        self._text(painter, QRectF(content.x(), y + 34, 230, 20), subtitle, "#9FA9B5", 9, QFont.Weight.Medium)

    def _draw_settings_action_button(
        self,
        painter: QPainter,
        action: str,
        label: str,
        icon_name: str,
        enabled: bool = True,
    ) -> None:
        box = self.settings_section_control_rects()[action]
        pressed = action == self._pressed_settings_action
        hovered = action == self._hover_action.removeprefix("settings:action:")
        completed = action == self._settings_feedback_action
        draw_box = box.adjusted(2, 2, -2, -2) if pressed else box
        if completed:
            fill = QColor(5, 196, 107, 24)
            border = QColor(49, 199, 120, 168)
            color = QColor("#92EBBC")
        elif hovered and enabled:
            fill = QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 24)
            border = QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 178)
            color = QColor("#F4F6F8")
        else:
            fill = QColor(255, 255, 255, 14 if enabled else 7)
            border = QColor(255, 255, 255, 44 if enabled else 20)
            color = QColor("#E8EDF0" if enabled else "#737C85")
        self._rounded(painter, draw_box, fill, 8, border)
        self._draw_menu_icon(
            painter,
            icon_name,
            QRectF(draw_box.x() + 12, draw_box.y() + 11, 16, 16),
            color,
        )
        self._text(
            painter,
            QRectF(draw_box.x() + 34, draw_box.y(), draw_box.width() - 40, draw_box.height()),
            label,
            color.name(),
            10,
            QFont.Weight.Black,
            Qt.AlignmentFlag.AlignCenter,
        )

    def _draw_settings_general(self, painter: QPainter) -> None:
        self._draw_settings_heading(painter, "常规", "启动、定时和界面体验")
        controls = self.settings_section_control_rects()
        rows = (
            (264, "开机自启动", "登录后自动打开匠猫壁纸"),
            (344, "自动切换", f"当前周期：{self.auto_interval_label}"),
            (424, "切换周期", "仅在自动切换开启时可用"),
            (504, "切换动画", "方向滑移与淡入"),
        )
        for y, title, subtitle in rows:
            self._draw_settings_row(painter, y, title, subtitle)
        self._draw_toggle_switch(painter, "startup", controls["startup"])
        self._draw_toggle_switch(painter, "auto", controls["auto:toggle"])
        self._draw_settings_action_button(
            painter,
            "auto:interval",
            self.auto_interval_label,
            "timer",
            self.auto_change_enabled,
        )
        self._draw_toggle_switch(painter, "animation", controls["animation"])

    def _draw_settings_storage(self, painter: QPainter) -> None:
        self._draw_settings_heading(painter, "存储", "下载位置与本地缓存")
        self._draw_settings_row(painter, 264, "下载目录", "保存 4K 原图的位置")
        self._draw_settings_row(painter, 358, "本地缓存", "管理预览图片占用空间")
        self._draw_settings_action_button(painter, "folder", "选择", "folder-open")
        self._draw_settings_action_button(painter, "cache", "清理", "trash-2")

    def _draw_settings_about(self, painter: QPainter) -> None:
        self._draw_settings_heading(painter, "关于", "产品、来源与版权信息")
        info = dict(self.about_info_items())
        plate_title, product_body, public_notice = self.about_gallery_plate()

        plate = QRectF(782, 278, 330, 116)
        self._rounded(
            painter,
            plate,
            QColor(8, 16, 22, 122),
            6,
            QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 72),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.accent)
        painter.drawRoundedRect(QRectF(782, 278, 3, 116), 1.5, 1.5)
        self._text(
            painter,
            QRectF(800, 292, 196, 18),
            "CURATED FOR DESKTOP",
            self.accent.name(),
            8,
            QFont.Weight.Black,
        )
        self._text(
            painter,
            QRectF(800, 316, 288, 50),
            plate_title,
            WHITE,
            16,
            QFont.Weight.Black,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        )
        self._text(
            painter,
            QRectF(800, 366, 288, 18),
            "JIANGMAO / 2026",
            "#AAB4BC",
            8,
            QFont.Weight.Black,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.drawLine(QPointF(914, 416), QPointF(914, 688))
        self._text(painter, QRectF(782, 416, 118, 18), "关于产品", self.accent.name(), 9, QFont.Weight.Black)
        self._text(
            painter,
            QRectF(782, 444, 118, 76),
            product_body,
            "#D5DBE1",
            9,
            QFont.Weight.Medium,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        )
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.drawLine(QPointF(782, 536), QPointF(900, 536))
        self._text(painter, QRectF(782, 550, 118, 18), "产品", "#8F99A3", 8, QFont.Weight.Medium)
        self._text(painter, QRectF(782, 572, 118, 20), info["产品"], WHITE, 10, QFont.Weight.Black)
        self._text(painter, QRectF(782, 612, 118, 18), "开发者", "#8F99A3", 8, QFont.Weight.Medium)
        self._text(
            painter,
            QRectF(782, 634, 118, 20),
            info["开发者"],
            WHITE,
            10,
            QFont.Weight.Black,
        )

        self._text(painter, QRectF(928, 416, 184, 18), "公开说明 / 隐私", self.accent.name(), 9, QFont.Weight.Black)
        self._text(
            painter,
            QRectF(928, 444, 184, 90),
            public_notice,
            "#D5DBE1",
            9,
            QFont.Weight.Medium,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        )

        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        for y in (538, 582, 626):
            painter.drawLine(QPointF(928, y), QPointF(1112, y))
        ledger = (
            (539, "版本", info["版本"].replace("\n", " "), WHITE),
            (583, "维护", info["开发品牌"], WHITE),
        )
        for y, label, value, color in ledger:
            self._text(painter, QRectF(928, y, 60, 43), label, "#8F99A3", 8, QFont.Weight.Medium)
            self._fitted_text(
                painter,
                QRectF(988, y, 124, 43),
                value,
                color,
                9,
                7,
                QFont.Weight.Black,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )

    def _draw_settings_legacy(self, painter: QPainter) -> None:
        rows = [
            ("开机自启动", "登录 Windows 后自动打开匠猫壁纸", "startup"),
            ("自动切换", "按设定周期自动应用下一张 4K 壁纸", "auto"),
            ("切换动画", "使用 280ms 方向滑移与淡入", "animation"),
            ("下载目录", "保存 4K 原图的位置", "folder"),
            ("缓存与接口", "查看数据源并清理本地预览缓存", "cache"),
        ]
        self._text(painter, QRectF(650, 194, 420, 22), "自动化", self.accent.name(), 9, QFont.Weight.Black)
        self._text(painter, QRectF(650, 476, 420, 22), "存储", self.accent.name(), 9, QFont.Weight.Black)
        y = 218
        for title, subtitle, ident in rows:
            box = QRectF(650, y, 462, 82)
            painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
            painter.drawLine(QPointF(box.x(), box.bottom()), QPointF(box.right(), box.bottom()))
            self._text(painter, QRectF(674, y + 10, 266, 26), title, WHITE, 13, QFont.Weight.Black)
            self._text(painter, QRectF(674, y + 38, 286, 26), subtitle, "#9FA9B5", 10, QFont.Weight.Medium)
            if ident == "auto":
                self._draw_toggle_switch(painter, ident, QRectF(918, y + 27, 64, 32))
                enabled = self.auto_change_enabled
                fill = QColor(246, 240, 227, 240 if enabled else 70)
                border = CREAM if enabled else QColor(255, 255, 255, 36)
                self._rounded(painter, QRectF(994, y + 24, 104, 38), fill, 8, border)
                color = INK if enabled else "#7D858D"
                self._text(painter, QRectF(994, y + 24, 104, 38), self.auto_interval_label, color, 10, QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)
            elif self.settings_control_kind(ident) == "toggle":
                self._draw_toggle_switch(painter, ident, QRectF(984, y + 21, 80, 40))
            else:
                self._rounded(painter, QRectF(984, y + 22, 100, 38), QColor(246, 240, 227, 240), 19, CREAM)
                label = self.settings_button_label(ident)
                self._text(painter, QRectF(984, y + 22, 100, 38), label, INK, 11, QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)
            y += 94

        about = self.about_settings_rect()
        hovered = self._hover_action == "page:关于"
        if hovered:
            self._rounded(
                painter,
                about.adjusted(0, 3, 0, -3),
                QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 28),
                8,
            )
        self._text(painter, QRectF(674, 682, 100, 22), "关于", WHITE, 12, QFont.Weight.Black)
        self._text(painter, QRectF(760, 682, 270, 22), "开发者、品牌与版本信息", "#8F99A3", 9, QFont.Weight.Medium)
        painter.setPen(QPen(self.accent if hovered else QColor("#C7CFD7"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(1078, 692), QPointF(1086, 700))
        painter.drawLine(QPointF(1086, 700), QPointF(1078, 708))

    def _draw_toggle_switch(self, painter: QPainter, ident: str, box: QRectF) -> None:
        progress = self._toggle_progress[ident]
        off = QColor("#D7D9DC")
        on = QColor("#05C46B")
        red = round(off.red() + (on.red() - off.red()) * progress)
        green = round(off.green() + (on.green() - off.green()) * progress)
        blue = round(off.blue() + (on.blue() - off.blue()) * progress)
        track = QColor(red, green, blue)
        off_border = QColor("#BEC3C9")
        on_border = QColor("#04B360")
        border = QColor(
            round(off_border.red() + (on_border.red() - off_border.red()) * progress),
            round(off_border.green() + (on_border.green() - off_border.green()) * progress),
            round(off_border.blue() + (on_border.blue() - off_border.blue()) * progress),
        )
        self._rounded(painter, box, track, 20, border)
        handle = self.toggle_handle_rect(box, progress)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(handle)
        painter.setPen(QPen(QColor(0, 0, 0, 32), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(handle)

    def _draw_toast(self, painter: QPainter) -> None:
        box = QRectF(438, 640, 324, 42)
        self._rounded(painter, box, QColor(14, 19, 25, 230), 18, QColor(255, 255, 255, 85))
        self._text(painter, box, self._toast, WHITE, 12, QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignCenter)

    def _draw_wallpaper_group_loading(self, painter: QPainter) -> None:
        box = QRectF(438, 626, 324, 42)
        self._rounded(painter, box.translated(0, 3), QColor(0, 0, 0, 54), 8)
        self._rounded(
            painter,
            box,
            QColor(10, 12, 17, 224),
            8,
            QColor(255, 255, 255, 46),
        )
        progress = self.wallpaper_group_loading_progress
        center = QPointF(box.x() + 19, box.center().y())
        pulse_radius = 5.0 + progress * 5.0
        painter.setPen(
            QPen(
                QColor(
                    self.accent.red(),
                    self.accent.green(),
                    self.accent.blue(),
                    round(105 * (1.0 - progress)),
                ),
                1.2,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, pulse_radius, pulse_radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.accent)
        painter.drawEllipse(center, 3.5, 3.5)
        self._text(
            painter,
            QRectF(box.x() + 35, box.y(), 190, box.height()),
            "正在扩展壁纸库",
            WHITE,
            11,
            QFont.Weight.Bold,
        )
        self._text(
            painter,
            QRectF(box.right() - 74, box.y(), 58, box.height()),
            "下一组",
            QColor(255, 255, 255, 128).name(),
            9,
            QFont.Weight.DemiBold,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    def _draw_operation_loading(self, painter: QPainter) -> None:
        box = QRectF(408, 626, 384, 42)
        self._rounded(painter, box.translated(0, 3), QColor(0, 0, 0, 54), 18)
        self._rounded(
            painter,
            box,
            QColor(10, 12, 17, 232),
            18,
            QColor(255, 255, 255, 58),
        )
        center = QPointF(box.x() + 20, box.center().y())
        progress = self.operation_loading_progress
        painter.setPen(QPen(QColor(255, 255, 255, 42), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, 8, 8)
        painter.setPen(QPen(self.accent, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(center.x() - 8, center.y() - 8, 16, 16), int(progress * 360 * 16), 220 * 16)
        self._text(
            painter,
            QRectF(box.x() + 42, box.y(), box.width() - 58, box.height()),
            self.operation_loading_message,
            WHITE,
            11,
            QFont.Weight.DemiBold,
        )
        track = QRectF(box.x(), box.bottom() - 2, box.width(), 2)
        painter.fillRect(track, QColor(255, 255, 255, 18))
        painter.fillRect(
            QRectF(track.x(), track.y(), track.width() * progress, track.height()),
            self.accent,
        )

    def mouseReleaseEvent(self, event) -> None:
        self._drag_global_position = None
        point = self._logical_point(event.position())
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_settings_action = ""
            self.update()
            self.set_home_interaction(self.home_action_at(point))
            if self._taskbar_slider_dragging:
                self._set_taskbar_intensity_from_x(point.x())
                self._taskbar_slider_dragging = False
                return
        for action, box in self.window_control_rects():
            if box.contains(point):
                self.action_requested.emit(action)
                return
        if self._page == "首页" and self.menu_button_rect().contains(point):
            self.action_requested.emit("toggle-menu")
            return
        if self.menu_open:
            for page, box in self.menu_item_rects().items():
                if box.contains(point):
                    self.action_requested.emit(f"page:{page}")
                    return
            if not self.menu_panel_rect().contains(point):
                self.action_requested.emit("toggle-menu")
            return
        if self._page != "首页" and QRectF(1072, 100, 40, 40).contains(point):
            self.action_requested.emit("close-page")
            return
        if self._page == "首页":
            if (
                self.home_source_rect().contains(point)
                and self.current_wallpaper
                and self.current_wallpaper.copyright_link
            ):
                self.action_requested.emit("open:wallpaper-source")
                return
            for action, box in self.home_control_rects().items():
                if box.contains(point):
                    self.action_requested.emit(action)
                    return
        elif self._page == "设置":
            for section, box in self.settings_nav_rects().items():
                if box.contains(point):
                    self.action_requested.emit(f"settings:section:{section}")
                    return
            controls = self.settings_section_control_rects()
            if self.settings_section == "general":
                for action in ("startup", "auto:toggle", "animation"):
                    if controls[action].contains(point):
                        self.action_requested.emit(action)
                        return
                if self.auto_change_enabled and controls["auto:interval"].contains(point):
                    self.action_requested.emit("auto:interval")
                    return
            elif self.settings_section == "storage":
                for action in ("folder", "cache"):
                    if controls[action].contains(point):
                        self.action_requested.emit(action)
                        return
        elif self._page == "锁屏同步":
            if self.lock_screen_toggle_rect().contains(point):
                self.action_requested.emit("lockscreen:toggle")
                return
            if self.lock_screen_sync_rect().contains(point):
                self.action_requested.emit("lockscreen:sync")
                return
        elif self._page in {"收藏", "历史"}:
            items = [item for item in self._wallpapers if self._page == "历史" or item.key in self._favorites]
            for index, wallpaper in enumerate(items[:4]):
                if QRectF(650, 230 + index * 104, 462, 88).contains(point):
                    self.action_requested.emit(f"select:{wallpaper.key}")
                    return
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        point = self._logical_point(event.position())
        if event.button() == Qt.MouseButton.LeftButton:
            settings_action = self.settings_action_at(point)
            if settings_action:
                self._pressed_settings_action = settings_action
                self.update()
            else:
                home_action = self.home_action_at(point)
                if home_action:
                    self.set_home_interaction(home_action, home_action)
                elif self._page == "首页" and point.y() < 110 and point.x() < 1030:
                    self._drag_global_position = event.globalPosition()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        point = self._logical_point(event.position())
        hover = ""
        for action, box in self.window_control_rects():
            if box.contains(point):
                hover = action
                break
        if not hover and self._page == "设置":
            settings_action = self.settings_action_at(point)
            if settings_action:
                hover = f"settings:action:{settings_action}"
            for section, box in self.settings_nav_rects().items():
                if not hover and box.contains(point):
                    hover = f"settings:section:{section}"
                    break
        if not hover and self._page == "首页":
            hover = self.home_action_at(point)
        if hover != self._hover_action:
            self.set_home_interaction(hover, self._pressed_home_action)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if hover == "open:wallpaper-source"
            else Qt.CursorShape.ArrowCursor
        )
        if self._taskbar_slider_dragging:
            self._set_taskbar_intensity_from_x(point.x())
        if self._drag_global_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition() - self._drag_global_position
            self._drag_global_position = event.globalPosition()
            self.window_move_requested.emit(delta)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._pressed_settings_action = ""
        self.set_home_interaction()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        point = self._logical_point(event.position())
        if event.button() == Qt.MouseButton.LeftButton and point.y() < 110 and point.x() < 1030:
            self.action_requested.emit("window:maximize")
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.menu_open:
                self.action_requested.emit("toggle-menu")
            elif self._page == "关于":
                self.action_requested.emit("page:设置")
            elif self._page != "首页":
                self.action_requested.emit("close-page")
        elif event.key() == Qt.Key.Key_M:
            self.action_requested.emit("toggle-menu")
        elif event.key() == Qt.Key.Key_Left:
            self.action_requested.emit("prev")
        elif event.key() == Qt.Key.Key_Right:
            self.action_requested.emit("next")
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.action_requested.emit("apply")
        else:
            super().keyPressEvent(event)
