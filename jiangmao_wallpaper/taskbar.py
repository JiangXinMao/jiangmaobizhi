from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass


ACCENT_DISABLED = 0
ACCENT_ENABLE_TRANSPARENTGRADIENT = 2
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
TRANSPARENT_TINT = 0x202020
FROSTED_TINT = 0x606060
VALID_SCOPES = frozenset({"primary", "all", "secondary"})
_DEFAULT_USER32 = object()


@dataclass(frozen=True, slots=True)
class TaskbarApplyResult:
    success: bool
    requested_mode: str
    applied_mode: str
    applied_count: int
    total_count: int
    primary_applied: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class TaskbarTarget:
    handle: int
    is_primary: bool


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", wintypes.DWORD),
        ("AnimationId", ctypes.c_int),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attrib", ctypes.c_int),
        ("pvData", ctypes.c_void_p),
        ("cbData", ctypes.c_size_t),
    ]


class Win32TaskbarBackend:
    WCA_ACCENT_POLICY = 19

    def __init__(self, user32=_DEFAULT_USER32):
        if user32 is _DEFAULT_USER32:
            user32 = ctypes.windll.user32 if sys.platform == "win32" else None
        self.user32 = user32
        self.unsupported_error = (
            "当前平台不支持 Windows 任务栏美化"
            if self.user32 is None and sys.platform != "win32"
            else ""
        )
        if self.user32 is not None:
            self._bind_prototypes()

    def _bind_prototypes(self) -> None:
        self.user32.FindWindowW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
        ]
        self.user32.FindWindowW.restype = wintypes.HWND
        self.user32.FindWindowExW.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
        ]
        self.user32.FindWindowExW.restype = wintypes.HWND
        self.user32.SetWindowCompositionAttribute.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA),
        ]
        self.user32.SetWindowCompositionAttribute.restype = wintypes.BOOL

    def taskbar_targets(self, scope: str) -> list[TaskbarTarget]:
        if self.user32 is None:
            return []

        target_scope = scope if scope in VALID_SCOPES else "primary"
        targets = []
        if target_scope in {"primary", "all"}:
            primary = int(
                self.user32.FindWindowW("Shell_TrayWnd", None) or 0
            )
            if primary:
                targets.append(TaskbarTarget(primary, True))
        if target_scope == "primary":
            return targets

        after = 0
        while True:
            after = int(
                self.user32.FindWindowExW(
                    None, after, "Shell_SecondaryTrayWnd", None
                )
                or 0
            )
            if not after:
                break
            targets.append(TaskbarTarget(after, False))

        return targets

    def taskbar_handles(self, scope: str) -> list[int]:
        return [target.handle for target in self.taskbar_targets(scope)]

    def apply_accent(
        self, hwnd: int, accent_state: int, gradient_color: int
    ) -> bool:
        if self.user32 is None or not hwnd:
            return False

        accent_flags = (
            0
            if accent_state
            in {ACCENT_DISABLED, ACCENT_ENABLE_TRANSPARENTGRADIENT}
            else 2
        )
        policy = ACCENT_POLICY(
            accent_state, accent_flags, gradient_color, 0
        )
        data = WINDOWCOMPOSITIONATTRIBDATA(
            self.WCA_ACCENT_POLICY,
            ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
            ctypes.sizeof(policy),
        )
        return bool(
            self.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
        )


class TaskbarAppearanceService:
    def __init__(self, backend=None):
        self.backend = backend if backend is not None else Win32TaskbarBackend()

    @staticmethod
    def gradient_color(mode: str, intensity: int) -> int:
        strength = max(20, min(100, int(intensity)))
        if mode == "default":
            return 0
        if mode == "transparent":
            alpha = 1 + round((100 - strength) * 79 / 80)
            return (alpha << 24) | TRANSPARENT_TINT
        alpha = 48 + round((strength - 20) * 112 / 80)
        return (alpha << 24) | FROSTED_TINT

    def signature(self, include_secondary: bool) -> tuple[int, ...]:
        scope = "all" if include_secondary else "primary"
        return tuple(self.backend.taskbar_handles(scope))

    def apply(
        self, mode: str, intensity: int, scope: str
    ) -> TaskbarApplyResult:
        requested = (
            mode if mode in {"default", "transparent", "frosted"} else "default"
        )
        target_scope = scope if scope in VALID_SCOPES else "primary"
        targets = self.backend.taskbar_targets(target_scope)
        if not targets:
            if target_scope == "secondary":
                return TaskbarApplyResult(
                    True,
                    requested,
                    requested,
                    0,
                    0,
                    primary_applied=False,
                )
            return TaskbarApplyResult(
                False,
                requested,
                requested,
                0,
                0,
                primary_applied=False,
                error=(
                    getattr(self.backend, "unsupported_error", "")
                    or "未检测到 Windows 任务栏"
                ),
            )

        state = {
            "default": ACCENT_DISABLED,
            "transparent": ACCENT_ENABLE_TRANSPARENTGRADIENT,
            "frosted": ACCENT_ENABLE_ACRYLICBLURBEHIND,
        }[requested]
        color = self.gradient_color(requested, intensity)
        successes = [
            self.backend.apply_accent(target.handle, state, color)
            for target in targets
        ]
        applied_mode = requested

        if requested == "frosted" and not all(successes):
            blur_successes = [
                self.backend.apply_accent(
                    target.handle, ACCENT_ENABLE_BLURBEHIND, color
                )
                for target in targets
            ]
            final_effects = [
                "blur" if blur else "acrylic" if acrylic else ""
                for acrylic, blur in zip(successes, blur_successes)
            ]
            successes = [bool(effect) for effect in final_effects]
            applied_effects = {effect for effect in final_effects if effect}
            if applied_effects == {"blur"}:
                applied_mode = "frosted-compat"
            elif applied_effects == {"acrylic"}:
                applied_mode = "frosted"
            else:
                applied_mode = "mixed"

        count = sum(successes)
        primary_applied = any(
            target.is_primary and applied
            for target, applied in zip(targets, successes)
        )
        success = (
            count == len(targets)
            if target_scope == "secondary"
            else primary_applied
        )
        return TaskbarApplyResult(
            success,
            requested,
            applied_mode,
            count,
            len(targets),
            primary_applied=primary_applied,
            error=(
                "" if count == len(targets) else "部分显示器未更新"
            ),
        )
