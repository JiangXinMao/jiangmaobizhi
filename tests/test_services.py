from pathlib import Path

from jiangmao_wallpaper.models import Wallpaper
from jiangmao_wallpaper.services import LockScreenService, WallpaperService, safe_filename


class StubCache:
    def __init__(self, source: Path):
        self.source = source

    def fetch(self, wallpaper, quality):
        assert quality == "uhd"
        return self.source


class UhdTimeoutCache:
    def __init__(self, preview: Path):
        self.preview = preview
        self.calls = []

    def fetch(self, wallpaper, quality):
        self.calls.append(quality)
        if quality == "uhd":
            raise TimeoutError("UHD download timed out")
        return self.preview

    def get_path(self, wallpaper, quality):
        return self.preview if quality == "preview" else None


class PreviewFirstCache:
    def __init__(self, preview: Path):
        self.preview = preview
        self.calls = []

    def get_path(self, wallpaper, quality):
        self.calls.append(("get_path", quality))
        return self.preview if quality == "preview" else None

    def fetch(self, wallpaper, quality):
        self.calls.append(("fetch", quality))
        raise AssertionError("a verified local preview should be applied first")


class DownloadFallbackCache:
    def __init__(self, preview: Path):
        self.preview = preview

    def fetch(self, wallpaper, quality):
        if quality == "uhd":
            raise TimeoutError("UHD unavailable")
        return self.preview


def wallpaper():
    return Wallpaper(
        title="湖泊:晨光?",
        copyright="示例作者 · Public Domain · SMK Open",
        startdate="smk-KKS2004-95",
        preview_url="https://example.com/p.jpg",
        full_url="https://example.com/u.jpg",
        provider="SMK Open",
        copyright_link="https://open.smk.dk/artwork/image/KKS2004-95",
        artist="示例作者",
        license_name="Public Domain Mark 1.0",
        license_url="https://creativecommons.org/publicdomain/mark/1.0/",
    )


def test_safe_filename_removes_windows_reserved_characters():
    assert safe_filename('湖泊:晨光? <今日>|*') == "湖泊晨光 今日"


def test_download_uses_sanitized_4k_filename(tmp_path):
    source = tmp_path / "cache.jpg"
    source.write_bytes(b"jpeg")
    output = tmp_path / "downloads"
    service = WallpaperService(StubCache(source))

    target = service.download(wallpaper(), output)

    assert target.name == "湖泊晨光_smk-KKS2004-95_4K.jpg"
    assert target.read_bytes() == b"jpeg"
    license_file = output / "湖泊晨光_smk-KKS2004-95_4K.jpg.license.txt"
    assert license_file.read_text(encoding="utf-8") == (
        "标题: 湖泊:晨光?\n"
        "作者: 示例作者\n"
        "许可证: Public Domain Mark 1.0\n"
        "许可证链接: https://creativecommons.org/publicdomain/mark/1.0/\n"
        "原始来源: https://open.smk.dk/artwork/image/KKS2004-95\n"
        "来源平台: SMK Open\n"
    )


def test_apply_falls_back_to_cached_preview_when_uhd_download_times_out(tmp_path):
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"preview")
    applied = []
    service = WallpaperService(
        UhdTimeoutCache(preview),
        apply_function=applied.append,
    )

    result = service.apply(wallpaper())

    assert result == preview
    assert applied == [preview]
    assert service.last_apply_quality == "preview"


def test_apply_uses_local_preview_before_remote_uhd(tmp_path):
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"preview")
    applied = []
    cache = PreviewFirstCache(preview)
    service = WallpaperService(cache, apply_function=applied.append)

    result = service.apply(wallpaper())

    assert result == preview
    assert applied == [preview]
    assert service.last_apply_quality == "preview"
    assert cache.calls == [("get_path", "uhd"), ("get_path", "preview")]


def test_download_falls_back_to_preview_when_uhd_is_unavailable(tmp_path):
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"preview")
    service = WallpaperService(DownloadFallbackCache(preview))

    target = service.download(wallpaper(), tmp_path / "downloads")

    assert target.name.endswith("_Preview.jpg")
    assert target.read_bytes() == b"preview"
    assert (tmp_path / "downloads" / (target.name + ".license.txt")).exists()
    assert service.last_download_quality == "preview"


def test_download_uses_verified_preview_without_waiting_for_remote_uhd(tmp_path):
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"preview")
    cache = PreviewFirstCache(preview)
    service = WallpaperService(cache)

    target = service.download(wallpaper(), tmp_path / "downloads")

    assert target.name.endswith("_Preview.jpg")
    assert target.read_bytes() == b"preview"
    assert cache.calls == [("get_path", "uhd"), ("get_path", "preview")]
    assert service.last_download_quality == "preview"


class FakeAsyncOperation:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def test_lock_screen_service_copies_to_persistent_location(tmp_path, monkeypatch):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"lockscreen-image")
    target_dir = tmp_path / "persistent"
    service = LockScreenService(target_dir)
    calls = []

    class FakeStorageFile:
        @staticmethod
        def get_file_from_path_async(path):
            calls.append(("file", Path(path)))
            return FakeAsyncOperation(path)

    class FakeSettings:
        def try_set_lock_screen_image_async(self, storage_file):
            calls.append(("set", storage_file))
            return FakeAsyncOperation(True)

    class FakePersonalization:
        current = FakeSettings()

    class FakeLockScreen:
        @staticmethod
        def set_image_file_async(storage_file):
            calls.append(("fallback", storage_file))
            return FakeAsyncOperation(None)

    monkeypatch.setattr(LockScreenService, "is_supported", staticmethod(lambda: True))
    import winrt.windows.storage
    import winrt.windows.system.userprofile
    monkeypatch.setattr(winrt.windows.storage, "StorageFile", FakeStorageFile)
    monkeypatch.setattr(
        winrt.windows.system.userprofile,
        "UserProfilePersonalizationSettings",
        FakePersonalization,
    )
    monkeypatch.setattr(winrt.windows.system.userprofile, "LockScreen", FakeLockScreen)

    target = service.apply_path(source)

    assert target == target_dir / "current-lockscreen.jpg"
    assert target.read_bytes() == b"lockscreen-image"
    assert calls == [("file", target.resolve()), ("set", str(target.resolve()))]


def test_lock_screen_service_rejects_unsupported_system(tmp_path, monkeypatch):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"image")
    service = LockScreenService(tmp_path / "persistent")
    monkeypatch.setattr(LockScreenService, "is_supported", staticmethod(lambda: False))

    try:
        service.apply_path(source)
    except OSError as error:
        assert "不支持" in str(error)
    else:
        raise AssertionError("unsupported lock screen API must fail")
