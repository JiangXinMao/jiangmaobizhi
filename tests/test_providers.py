import json

from PIL import Image

from jiangmao_wallpaper.models import Wallpaper
from jiangmao_wallpaper.providers import (
    BundledWallpaperProvider,
    HistoryPage,
    ProviderChain,
)


def make_manifest(tmp_path, count=3):
    payload = []
    for index in range(count):
        asset = f"wallpaper-{index}.jpg"
        Image.new("RGB", (32, 18), (index * 30, 80, 120)).save(tmp_path / asset)
        payload.append(
            {
                "title": f"Landscape {index}",
                "copyright": "Bundled sample",
                "startdate": f"bundled-{index}",
                "preview_url": "",
                "full_url": "",
                "asset": asset,
            }
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def test_bundled_provider_loads_only_local_assets(tmp_path):
    provider = BundledWallpaperProvider(make_manifest(tmp_path))

    wallpapers = provider.fetch(2, "zh-CN")

    assert len(wallpapers) == 2
    assert all(item.local_preview for item in wallpapers)
    assert all(item.local_full == item.local_preview for item in wallpapers)


def test_bundled_provider_pages_results(tmp_path):
    provider = BundledWallpaperProvider(make_manifest(tmp_path, 3))

    page = provider.fetch_page(2, 2)

    assert isinstance(page, HistoryPage)
    assert [item.key for item in page.wallpapers] == ["bundled-2"]
    assert page.total == 3
    assert page.has_more is False


def test_provider_chain_falls_back_after_failure():
    class BrokenProvider:
        name = "broken"

        def fetch(self, count, market):
            raise RuntimeError("offline")

    class WorkingProvider:
        name = "working"

        def fetch(self, count, market):
            return [Wallpaper("Landscape", "Bundled", "local-1", "", "")]

    result = ProviderChain([BrokenProvider(), WorkingProvider()]).fetch()

    assert result.provider == "working"
    assert len(result.wallpapers) == 1
    assert result.health["broken"].available is False
