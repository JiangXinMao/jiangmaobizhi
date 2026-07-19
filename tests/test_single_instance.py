from jiangmao_wallpaper.single_instance import (
    ERROR_ALREADY_EXISTS,
    SW_RESTORE,
    SW_SHOW,
    SingleInstanceGuard,
)


class FakeKernel32:
    def __init__(self, error=0, handle=101, wait_result=1):
        self.error = error
        self.handle = handle
        self.event_handle = 202
        self.opened_event_handle = 303
        self.wait_result = wait_result
        self.closed = []
        self.events = []

    def CreateMutexW(self, security, initial_owner, name):
        return self.handle

    def GetLastError(self):
        return self.error

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True

    def CreateEventW(self, security, manual_reset, initial_state, name):
        self.events.append(("create", name))
        return self.event_handle

    def OpenEventW(self, access, inherit, name):
        self.events.append(("open", name))
        return self.opened_event_handle

    def SetEvent(self, handle):
        self.events.append(("set", handle))
        return True

    def WaitForSingleObject(self, handle, timeout):
        self.events.append(("wait", handle, timeout))
        return self.wait_result

    def GetCurrentThreadId(self):
        return 11


class FakeUser32:
    def __init__(self, window=202, iconic=False, foreground=True):
        self.window = window
        self.iconic = iconic
        self.foreground = foreground
        self.calls = []

    def FindWindowW(self, class_name, title):
        self.calls.append(("find", class_name, title))
        return self.window

    def IsIconic(self, window):
        self.calls.append(("iconic", window))
        return self.iconic

    def GetForegroundWindow(self):
        self.calls.append(("get_foreground",))
        return 404

    def GetWindowThreadProcessId(self, window, process_id):
        self.calls.append(("thread", window))
        return 22 if window == 404 else 33

    def AttachThreadInput(self, first, second, attach):
        self.calls.append(("attach", first, second, attach))
        return True

    def ShowWindow(self, window, command):
        self.calls.append(("show", window, command))
        return True

    def BringWindowToTop(self, window):
        self.calls.append(("top", window))
        return True

    def SetForegroundWindow(self, window):
        self.calls.append(("foreground", window))
        return self.foreground

    def SetFocus(self, window):
        self.calls.append(("focus", window))
        return window

    def SwitchToThisWindow(self, window, alternate):
        self.calls.append(("switch", window, alternate))

    def FlashWindow(self, window, invert):
        self.calls.append(("flash", window, invert))
        return True


def test_first_instance_keeps_mutex_until_close():
    kernel32 = FakeKernel32()
    guard = SingleInstanceGuard(kernel32=kernel32, user32=FakeUser32())

    assert guard.mutex_name == r"Local\JiangMaoWallpaper.SingleInstance.v1"
    assert guard.acquire() is True
    assert kernel32.closed == []

    guard.close()

    assert kernel32.closed == [202, 101]


def test_duplicate_instance_releases_its_mutex_handle():
    kernel32 = FakeKernel32(error=ERROR_ALREADY_EXISTS)
    guard = SingleInstanceGuard(kernel32=kernel32, user32=FakeUser32())

    assert guard.acquire() is False
    assert kernel32.closed == [101]


def test_duplicate_signals_primary_activation_event():
    kernel32 = FakeKernel32(error=ERROR_ALREADY_EXISTS)
    guard = SingleInstanceGuard(kernel32=kernel32, user32=FakeUser32())

    assert guard.acquire() is False
    assert guard.request_activation() is True
    assert ("set", 303) in kernel32.events
    assert kernel32.closed == [101, 303]


def test_primary_consumes_activation_event_once_signaled():
    kernel32 = FakeKernel32(wait_result=0)
    guard = SingleInstanceGuard(kernel32=kernel32, user32=FakeUser32())

    assert guard.acquire() is True
    assert guard.consume_activation_request() is True
    assert ("wait", 202, 0) in kernel32.events


def test_duplicate_restores_and_foregrounds_minimized_window():
    user32 = FakeUser32(iconic=True)
    guard = SingleInstanceGuard(kernel32=FakeKernel32(), user32=user32)

    assert guard.activate_existing_window() is True
    assert ("show", 202, SW_RESTORE) in user32.calls
    assert ("top", 202) in user32.calls
    assert ("foreground", 202) in user32.calls


def test_duplicate_shows_hidden_window_and_flashes_if_foreground_is_denied():
    user32 = FakeUser32(iconic=False, foreground=False)
    guard = SingleInstanceGuard(kernel32=FakeKernel32(), user32=user32)

    assert guard.activate_existing_window() is True
    assert ("show", 202, SW_SHOW) in user32.calls
    assert ("flash", 202, True) in user32.calls


def test_activation_waits_for_first_instance_window_creation():
    windows = iter((0, 0, 303))
    sleeps = []
    user32 = FakeUser32()
    user32.FindWindowW = lambda class_name, title: next(windows)
    guard = SingleInstanceGuard(
        kernel32=FakeKernel32(),
        user32=user32,
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
    )

    assert guard.activate_existing_window() is True
    assert sleeps == [0.05, 0.05]
    assert ("show", 303, SW_SHOW) in user32.calls
