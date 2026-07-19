from __future__ import annotations

import ctypes
from ctypes import wintypes

import pytest

import jiangmao_wallpaper.taskbar as taskbar_module
from jiangmao_wallpaper.taskbar import (
    ACCENT_POLICY,
    WINDOWCOMPOSITIONATTRIBDATA,
    TaskbarAppearanceService,
    TaskbarTarget,
    Win32TaskbarBackend,
)


class FakeBackend:
    def __init__(self, handles=None, failures=None, xaml=False):
        self.handles = handles or {
            "primary": [10],
            "all": [10, 20],
            "secondary": [20],
        }
        self.failures = set(failures or [])
        self.calls = []
        self.xaml = xaml

    def taskbar_handles(self, scope):
        return list(self.handles.get(scope, []))

    def taskbar_targets(self, scope):
        primary_handles = set(self.handles.get("primary", []))
        return [
            TaskbarTarget(handle, handle in primary_handles)
            for handle in self.taskbar_handles(scope)
        ]

    def apply_accent(self, hwnd, accent_state, gradient_color):
        self.calls.append((hwnd, accent_state, gradient_color))
        return (hwnd, accent_state) not in self.failures

    def has_xaml_taskbar(self):
        return self.xaml


class FakeWin32Function:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


class FakeUser32:
    def __init__(self, primary=0, secondary=None, accent_result=True):
        self.primary = primary
        self.secondary = list(secondary or [])
        self.accent_result = accent_result
        self.accent_payloads = []
        self.FindWindowW = FakeWin32Function(self._find_window)
        self.FindWindowExW = FakeWin32Function(self._find_window_ex)
        self.SetWindowCompositionAttribute = FakeWin32Function(
            self._set_window_composition_attribute
        )

    def _find_window(self, class_name, window_name):
        return self.primary

    def _find_window_ex(self, parent, after, class_name, window_name):
        if not self.secondary:
            return 0
        if not after:
            return self.secondary[0]
        try:
            return self.secondary[self.secondary.index(after) + 1]
        except (ValueError, IndexError):
            return 0

    def _set_window_composition_attribute(self, hwnd, data_pointer):
        data = ctypes.cast(
            data_pointer, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)
        ).contents
        policy = ctypes.cast(
            data.pvData, ctypes.POINTER(ACCENT_POLICY)
        ).contents
        self.accent_payloads.append(
            {
                "hwnd": hwnd,
                "attrib": data.Attrib,
                "size": data.cbData,
                "state": policy.AccentState,
                "flags": policy.AccentFlags,
                "color": policy.GradientColor,
                "animation": policy.AnimationId,
            }
        )
        return self.accent_result

def test_transparent_applies_to_every_requested_taskbar():
    backend = FakeBackend()

    result = TaskbarAppearanceService(backend).apply("transparent", 88, "all")

    assert result.success is True
    assert result.applied_count == 2
    assert result.total_count == 2
    assert {(hwnd, state) for hwnd, state, _ in backend.calls} == {
        (10, 2),
        (20, 2),
    }


def test_frosted_falls_back_to_blur_for_consistent_output():
    backend = FakeBackend(failures={(20, 4)})

    result = TaskbarAppearanceService(backend).apply("frosted", 70, "all")

    assert result.success is True
    assert result.applied_mode == "frosted-compat"
    assert backend.calls[-2][1] == backend.calls[-1][1] == 3


def test_frosted_partial_blur_fallback_reports_primary_success():
    backend = FakeBackend(failures={(20, 4), (20, 3)})

    result = TaskbarAppearanceService(backend).apply("frosted", 70, "all")

    assert result.success is True
    assert result.applied_mode == "frosted-compat"
    assert result.applied_count == 1
    assert result.total_count == 2
    assert result.error == "部分显示器未更新"


def test_frosted_fallback_preserves_each_targets_final_effect():
    backend = FakeBackend(failures={(20, 4), (10, 3)})

    result = TaskbarAppearanceService(backend).apply("frosted", 70, "all")

    assert result.success is True
    assert result.primary_applied is True
    assert result.applied_count == 2
    assert result.total_count == 2
    assert result.applied_mode == "mixed"
    assert result.error == ""


def test_frosted_fallback_keeps_acrylic_when_blur_also_fails():
    backend = FakeBackend(failures={(20, 4), (10, 3), (20, 3)})

    result = TaskbarAppearanceService(backend).apply("frosted", 70, "all")

    assert result.success is True
    assert result.primary_applied is True
    assert result.applied_count == 1
    assert result.applied_mode == "frosted"
    assert result.error == "部分显示器未更新"


def test_secondary_only_non_frosted_success_does_not_report_primary_success():
    backend = FakeBackend(failures={(10, 2)})

    result = TaskbarAppearanceService(backend).apply("transparent", 88, "all")

    assert result.success is False
    assert result.applied_count == 1
    assert result.total_count == 2


def test_all_scope_tracks_missing_primary_separately_from_secondaries():
    user32 = FakeUser32(primary=0, secondary=[20, 30])
    backend = Win32TaskbarBackend(user32=user32)

    result = TaskbarAppearanceService(backend).apply("transparent", 88, "all")

    assert result.primary_applied is False
    assert result.success is False
    assert result.applied_count == 2
    assert result.total_count == 2
    assert [item["hwnd"] for item in user32.accent_payloads] == [20, 30]


def test_missing_taskbar_returns_failure_without_calls():
    backend = FakeBackend(handles={"primary": []})

    result = TaskbarAppearanceService(backend).apply(
        "transparent", 88, "primary"
    )

    assert result.success is False
    assert result.error == "未检测到 Windows 任务栏"
    assert backend.calls == []


def test_missing_secondary_taskbar_is_successful_no_op():
    backend = FakeBackend(handles={"secondary": []})

    result = TaskbarAppearanceService(backend).apply(
        "default", 88, "secondary"
    )

    assert result.success is True
    assert result.applied_count == 0
    assert result.total_count == 0
    assert result.error == ""
    assert backend.calls == []


def test_default_disables_the_accent_policy():
    backend = FakeBackend()

    result = TaskbarAppearanceService(backend).apply("default", 88, "primary")

    assert result.requested_mode == "default"
    assert result.applied_mode == "default"
    assert backend.calls == [(10, 0, 0)]


def test_unknown_mode_and_scope_use_safe_defaults():
    backend = FakeBackend()

    result = TaskbarAppearanceService(backend).apply("unknown", 88, "unknown")

    assert result.requested_mode == "default"
    assert backend.calls == [(10, 0, 0)]


def test_non_frosted_partial_failure_reports_applied_count():
    backend = FakeBackend(failures={(20, 2)})

    result = TaskbarAppearanceService(backend).apply("transparent", 88, "all")

    assert result.success is True
    assert result.applied_count == 1
    assert result.total_count == 2
    assert result.error == "部分显示器未更新"


def test_signature_returns_current_handles_for_requested_display_set():
    backend = FakeBackend()
    service = TaskbarAppearanceService(backend)

    assert service.signature(include_secondary=False) == (10,)
    assert service.signature(include_secondary=True) == (10, 20)


@pytest.mark.parametrize(
    ("mode", "intensity", "expected"),
    [
        ("default", 88, 0),
        ("transparent", 88, (13 << 24) | 0x202020),
        ("transparent", 1000, (1 << 24) | 0x202020),
        ("frosted", 100, (160 << 24) | 0x606060),
        ("frosted", -1, (48 << 24) | 0x606060),
    ],
)
def test_gradient_color_maps_clamped_strength(mode, intensity, expected):
    assert TaskbarAppearanceService.gradient_color(mode, intensity) == expected


def test_transparent_strength_reaches_a_real_clear_policy_without_zero_alpha():
    low = TaskbarAppearanceService.gradient_color("transparent", 20)
    maximum = TaskbarAppearanceService.gradient_color("transparent", 100)

    assert (low & 0xFFFFFF) == 0x202020
    assert (maximum & 0xFFFFFF) == 0x202020
    assert (low >> 24) == 80
    assert (maximum >> 24) == 1


def test_frosted_strength_increases_a_visible_neutral_tint():
    low = TaskbarAppearanceService.gradient_color("frosted", 20)
    maximum = TaskbarAppearanceService.gradient_color("frosted", 100)

    assert (low & 0xFFFFFF) == 0x606060
    assert (maximum & 0xFFFFFF) == 0x606060
    assert (low >> 24) == 48
    assert (maximum >> 24) == 160


def test_transparent_policy_does_not_reuse_blur_accent_flags():
    user32 = FakeUser32(primary=0x1_0000_1234)
    backend = Win32TaskbarBackend(user32=user32)

    assert backend.apply_accent(0x1_0000_1234, 2, 0x01202020) is True

    assert user32.accent_payloads[-1]["flags"] == 0


def test_win32_backend_binds_pointer_safe_function_prototypes():
    user32 = FakeUser32()

    Win32TaskbarBackend(user32=user32)

    assert user32.FindWindowW.argtypes == [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
    ]
    assert user32.FindWindowW.restype is wintypes.HWND
    assert user32.FindWindowExW.argtypes == [
        wintypes.HWND,
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
    ]
    assert user32.FindWindowExW.restype is wintypes.HWND
    assert user32.SetWindowCompositionAttribute.argtypes == [
        wintypes.HWND,
        ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA),
    ]
    assert user32.SetWindowCompositionAttribute.restype is wintypes.BOOL


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("primary", [0x1_0000_0010]),
        ("secondary", [0x1_0000_0020, 0x1_0000_0030]),
        ("all", [0x1_0000_0010, 0x1_0000_0020, 0x1_0000_0030]),
    ],
)
def test_win32_backend_enumerates_pointer_sized_handles_by_scope(
    scope, expected
):
    user32 = FakeUser32(
        primary=0x1_0000_0010,
        secondary=[0x1_0000_0020, 0x1_0000_0030],
    )
    backend = Win32TaskbarBackend(user32=user32)

    assert backend.taskbar_handles(scope) == expected

    if scope in {"secondary", "all"}:
        assert [call[1] for call in user32.FindWindowExW.calls] == [
            0,
            0x1_0000_0020,
            0x1_0000_0030,
        ]


def test_win32_backend_secondary_scope_skips_primary_lookup():
    user32 = FakeUser32(
        primary=0x1_0000_0010,
        secondary=[0x1_0000_0020],
    )
    backend = Win32TaskbarBackend(user32=user32)

    assert backend.taskbar_handles("secondary") == [0x1_0000_0020]
    assert user32.FindWindowW.calls == []


def test_win32_backend_passes_exact_accent_structures():
    user32 = FakeUser32(accent_result=True)
    backend = Win32TaskbarBackend(user32=user32)

    result = backend.apply_accent(0x1_0000_0010, 4, 0x7F112233)

    assert result is True
    assert user32.accent_payloads == [
        {
            "hwnd": 0x1_0000_0010,
            "attrib": 19,
            "size": ctypes.sizeof(ACCENT_POLICY),
            "state": 4,
            "flags": 2,
            "color": 0x7F112233,
            "animation": 0,
        }
    ]


def test_win32_backend_without_user32_is_inert():
    backend = Win32TaskbarBackend(user32=None)

    assert backend.taskbar_handles("all") == []
    assert backend.apply_accent(10, 2, 0) is False


def test_win32_backend_default_constructor_is_inert_off_windows(monkeypatch):
    monkeypatch.setattr(taskbar_module.sys, "platform", "linux")

    backend = Win32TaskbarBackend()

    assert backend.user32 is None
    assert backend.taskbar_handles("all") == []
    assert backend.apply_accent(10, 2, 0) is False


def test_default_service_uses_native_backend_on_xaml_taskbar():
    service = TaskbarAppearanceService(FakeBackend(xaml=True))

    result = service.apply("transparent", 100, "all")

    assert result.success is True
    assert len(service.backend.calls) == 2

def test_real_backend_reports_unsupported_platform_explicitly(monkeypatch):
    monkeypatch.setattr(taskbar_module.sys, "platform", "linux")

    result = TaskbarAppearanceService().apply("transparent", 88, "primary")

    assert result.success is False
    assert result.error == "当前平台不支持 Windows 任务栏美化"
