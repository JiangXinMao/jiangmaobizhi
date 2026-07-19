from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from time import sleep

import pytest
from PIL import Image

from jiangmao_wallpaper.cache import ImageCache, InvalidImageError
from jiangmao_wallpaper.models import Wallpaper


class FakeResponse:
    def __init__(self, content, content_type="image/jpeg"):
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def sample_wallpaper():
    return Wallpaper(
        title="湖面晨光",
        copyright="示例版权",
        startdate="20260710",
        preview_url="https://example.com/preview.jpg",
        full_url="https://example.com/full.jpg",
    )


def jpeg_bytes():
    buffer = BytesIO()
    Image.new("RGB", (32, 18), "navy").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_cache_rejects_non_image_response(tmp_path):
    cache = ImageCache(tmp_path, session=FakeSession(FakeResponse(b"<html>bad</html>", "text/html")))

    with pytest.raises(InvalidImageError):
        cache.fetch(sample_wallpaper(), "preview")

    assert list(tmp_path.iterdir()) == []


def test_cache_fetches_once_and_reuses_verified_file(tmp_path):
    session = FakeSession(FakeResponse(jpeg_bytes()))
    cache = ImageCache(tmp_path, session=session)

    first = cache.fetch(sample_wallpaper(), "uhd")
    second = cache.fetch(sample_wallpaper(), "uhd")

    assert first == second
    assert first.exists()
    assert len(session.calls) == 1
    assert session.calls[0][1]["timeout"] == (5, 30)


def test_cache_reuses_preview_when_full_url_is_identical(tmp_path):
    session = FakeSession(FakeResponse(jpeg_bytes()))
    cache = ImageCache(tmp_path, session=session)
    wallpaper = sample_wallpaper()
    wallpaper.full_url = wallpaper.preview_url

    preview = cache.fetch(wallpaper, "preview")
    full = cache.fetch(wallpaper, "uhd")

    assert full == preview
    assert len(session.calls) == 1


def test_concurrent_fetches_share_one_atomic_download(tmp_path):
    class SlowSession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            sleep(0.05)
            return self.response

    session = SlowSession(FakeResponse(jpeg_bytes()))
    cache = ImageCache(tmp_path, session=session)
    start = Barrier(3)

    def fetch():
        start.wait()
        return cache.fetch(sample_wallpaper(), "preview")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(fetch) for _ in range(2)]
        start.wait()
        paths = [future.result(timeout=2) for future in futures]

    assert paths[0] == paths[1]
    assert len(session.calls) == 1
    with Image.open(paths[0]) as image:
        image.verify()

def test_cache_invalidates_same_date_when_image_url_changes(tmp_path):
    session = FakeSession(FakeResponse(jpeg_bytes()))
    cache = ImageCache(tmp_path, session=session)
    original = sample_wallpaper()
    corrected = sample_wallpaper()
    corrected.preview_url = "https://example.com/corrected-preview.jpg"

    original_path = cache.fetch(original, "preview")
    corrected_path = cache.fetch(corrected, "preview")

    assert original_path != corrected_path
    assert len(session.calls) == 2
    assert session.calls[1][0] == corrected.preview_url


def test_cache_replaces_corrupted_existing_file(tmp_path):
    session = FakeSession(FakeResponse(jpeg_bytes()))
    cache = ImageCache(tmp_path, session=session)
    destination = cache._path(sample_wallpaper(), "preview")
    destination.write_bytes(b"partial image")

    result = cache.fetch(sample_wallpaper(), "preview")

    assert result == destination
    assert len(session.calls) == 1
    with Image.open(result) as image:
        image.verify()


def test_cache_get_path_ignores_corrupted_existing_file(tmp_path):
    cache = ImageCache(tmp_path, session=FakeSession(FakeResponse(jpeg_bytes())))
    destination = cache._path(sample_wallpaper(), "preview")
    destination.write_bytes(b"partial image")

    assert cache.get_path(sample_wallpaper(), "preview") is None
    assert not destination.exists()


def test_cache_promotes_valid_file_from_legacy_directory(tmp_path):
    legacy = tmp_path / "legacy"
    current = tmp_path / "current"
    legacy.mkdir()
    cache = ImageCache(current, session=FakeSession(FakeResponse(jpeg_bytes())), legacy_directories=(legacy,))
    legacy_path = legacy / cache._path(sample_wallpaper(), "preview").name
    legacy_path.write_bytes(jpeg_bytes())

    result = cache.get_path(sample_wallpaper(), "preview")

    assert result == current / legacy_path.name
    assert result.exists()
    assert result.read_bytes() == legacy_path.read_bytes()


def test_cache_accepts_valid_image_with_generic_content_type(tmp_path):
    cache = ImageCache(
        tmp_path,
        session=FakeSession(FakeResponse(jpeg_bytes(), "application/octet-stream")),
    )

    result = cache.fetch(sample_wallpaper(), "preview")

    assert result.exists()
    with Image.open(result) as image:
        image.verify()
