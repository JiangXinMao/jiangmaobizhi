from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes


ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9


class SingleInstanceGuard:
    def __init__(
        self,
        mutex_name: str = r"Local\JiangMaoWallpaper.SingleInstance.v1",
        activation_event_name: str = r"Local\JiangMaoWallpaper.Activate.v1",
        window_title: str = "匠猫壁纸",
        *,
        kernel32=None,
        user32=None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ):
        self.mutex_name = mutex_name
        self.activation_event_name = activation_event_name
        self.window_title = window_title
        self._sleep = sleep
        self._monotonic = monotonic
        self._handle = None
        self._activation_handle = None
        self._kernel32 = kernel32
        self._user32 = user32
        if os.name == "nt" and kernel32 is None:
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._configure_kernel32(self._kernel32)
        if os.name == "nt" and user32 is None:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._configure_user32(self._user32)

    @staticmethod
    def _configure_kernel32(kernel32) -> None:
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.argtypes = ()
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CreateEventW.argtypes = (
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetCurrentThreadId.argtypes = ()
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    @staticmethod
    def _configure_user32(user32) -> None:
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = wintypes.HWND
        user32.IsIconic.argtypes = (wintypes.HWND,)
        user32.IsIconic.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = ()
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = (
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        )
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.ShowWindow.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = (wintypes.HWND,)
        user32.BringWindowToTop.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.SetFocus.argtypes = (wintypes.HWND,)
        user32.SetFocus.restype = wintypes.HWND
        user32.SwitchToThisWindow.argtypes = (wintypes.HWND, wintypes.BOOL)
        user32.SwitchToThisWindow.restype = None
        user32.FlashWindow.argtypes = (wintypes.HWND, wintypes.BOOL)
        user32.FlashWindow.restype = wintypes.BOOL

    def acquire(self) -> bool:
        if self._kernel32 is None:
            return True
        self.close()
        handle = self._kernel32.CreateMutexW(None, False, self.mutex_name)
        if not handle:
            raise ctypes.WinError(self._kernel32.GetLastError())
        self._handle = handle
        already_exists = self._kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        if already_exists:
            self.close()
            return False
        self._activation_handle = self._kernel32.CreateEventW(
            None,
            False,
            False,
            self.activation_event_name,
        )
        if not self._activation_handle:
            error = self._kernel32.GetLastError()
            self.close()
            raise ctypes.WinError(error)
        return True

    def request_activation(self) -> bool:
        if self._kernel32 is None:
            return False
        event = self._kernel32.OpenEventW(
            EVENT_MODIFY_STATE,
            False,
            self.activation_event_name,
        )
        if not event:
            return False
        try:
            return bool(self._kernel32.SetEvent(event))
        finally:
            self._kernel32.CloseHandle(event)

    def consume_activation_request(self) -> bool:
        if self._kernel32 is None or self._activation_handle is None:
            return False
        return (
            self._kernel32.WaitForSingleObject(self._activation_handle, 0)
            == WAIT_OBJECT_0
        )

    def activate_existing_window(self, timeout_seconds: float = 2.0) -> bool:
        if self._user32 is None:
            return False
        deadline = self._monotonic() + max(0.0, timeout_seconds)
        while True:
            window = self._user32.FindWindowW(None, self.window_title)
            if window:
                command = SW_RESTORE if self._user32.IsIconic(window) else SW_SHOW
                self._user32.ShowWindow(window, command)
                foreground = self._user32.GetForegroundWindow()
                current_thread = self._kernel32.GetCurrentThreadId()
                target_thread = self._user32.GetWindowThreadProcessId(window, None)
                foreground_thread = (
                    self._user32.GetWindowThreadProcessId(foreground, None)
                    if foreground
                    else 0
                )
                attached_threads = []
                for thread in (foreground_thread, target_thread):
                    if (
                        thread
                        and thread != current_thread
                        and thread not in attached_threads
                        and self._user32.AttachThreadInput(
                            current_thread, thread, True
                        )
                    ):
                        attached_threads.append(thread)
                self._user32.BringWindowToTop(window)
                activated = bool(self._user32.SetForegroundWindow(window))
                self._user32.SetFocus(window)
                self._user32.SwitchToThisWindow(window, True)
                for thread in reversed(attached_threads):
                    self._user32.AttachThreadInput(current_thread, thread, False)
                if not activated:
                    self._user32.FlashWindow(window, True)
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(0.05)

    def close(self) -> None:
        if self._activation_handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(self._activation_handle)
            self._activation_handle = None
        if self._handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
