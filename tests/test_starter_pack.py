import json
from pathlib import Path

from PIL import Image

from jiangmao_wallpaper.cache import ImageCache
from jiangmao_wallpaper.models import AppState, Wallpaper
from jiangmao_wallpaper.starter_pack import seed_starter_wallpapers
from jiangmao_wallpaper.state import StateStore
from jiangmao_wallpaper.ui.resources import resource_path


def write_starter_manifest(tmp_path):
    directory = tmp_path / "starter"
    directory.mkdir()
    image_path = directory / "starter.jpg"
    Image.new("RGB", (1920, 1080), "#315A67").save(image_path)
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "asset": image_path.name,
                    "title": "Offline starter",
                    "headline": "离线起始图",
                    "copyright": "Example Park · Public Domain · National Park Photography",
                    "startdate": "photo-starter",
                    "preview_url": "https://live.staticflickr.com/preview.jpg",
                    "full_url": "https://live.staticflickr.com/full.jpg",
                    "provider": "National Park Photography",
                    "copyright_link": "https://www.flickr.com/photos/example/starter",
                    "artist": "Example Park",
                    "license_name": "Public Domain Mark 1.0",
                    "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_fresh_state_gets_persistent_offline_starter(tmp_path):
    manifest = write_starter_manifest(tmp_path)
    store = StateStore(tmp_path / "data" / "state.json")
    cache = ImageCache(tmp_path / "data" / "cache")

    added = seed_starter_wallpapers(store, cache, manifest)

    state = store.load()
    assert added == 1
    assert len(state.wallpapers) == 1
    starter = state.wallpapers[0]
    assert starter.key == "photo-starter"
    assert starter.artist == "Example Park"
    assert starter.license_name == "Public Domain Mark 1.0"
    assert starter.local_preview.startswith(str(cache.directory))
    assert cache.get_path(starter, "preview") == Path(starter.local_preview)


def test_starter_seed_is_idempotent_and_enriches_existing_metadata(tmp_path):
    manifest = write_starter_manifest(tmp_path)
    store = StateStore(tmp_path / "data" / "state.json")
    store.save(
        AppState(
            wallpapers=[
                Wallpaper(
                    title="Offline starter",
                    copyright="",
                    startdate="photo-starter",
                    preview_url="https://live.staticflickr.com/preview.jpg",
                    full_url="https://live.staticflickr.com/full.jpg",
                )
            ]
        )
    )
    cache = ImageCache(tmp_path / "data" / "cache")

    first_added = seed_starter_wallpapers(store, cache, manifest)
    second_added = seed_starter_wallpapers(store, cache, manifest)

    state = store.load()
    assert first_added == 0
    assert second_added == 0
    assert len(state.wallpapers) == 1
    assert state.wallpapers[0].artist == "Example Park"
    assert state.wallpapers[0].license_url.endswith("/publicdomain/mark/1.0/")
    assert state.wallpapers[0].local_preview


def test_bundled_starter_pack_uses_only_public_domain_landscape_photos():
    manifest_path = resource_path(
        "jiangmao_wallpaper/ui/assets/starter/manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert len(payload) == 8
    assert {item["provider"] for item in payload} == {
        "National Park Photography",
        "Federal Landscape Photography",
    }
    assert {item["license_name"] for item in payload} == {"Public Domain Mark 1.0"}
    retired_terms = ("wikimedia", "smk", "cleveland", "painting", "artwork")
    for item in payload:
        metadata = json.dumps(item).casefold()
        assert not any(term in metadata for term in retired_terms)
        assert item["startdate"].startswith("photo-")
        assert item["preview_url"].startswith("https://live.staticflickr.com/")
        assert item["full_url"].startswith("https://live.staticflickr.com/")
        assert item["copyright_link"].startswith("https://www.flickr.com/photos/")
        with Image.open(manifest_path.parent / item["asset"]) as image:
            assert image.size == (1920, 1080)


def test_curated_catalog_contains_only_reviewed_official_landscape_photos():
    starter_directory = resource_path("jiangmao_wallpaper/ui/assets/starter")
    catalog = json.loads(
        (starter_directory / "catalog.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (starter_directory / "manifest.json").read_text(encoding="utf-8")
    )

    assert len(catalog) >= 14
    assert [item["startdate"] for item in catalog[: len(manifest)]] == [
        item["startdate"] for item in manifest
    ]
    assert len({item["startdate"] for item in catalog}) == len(catalog)
    assert {item["license_name"] for item in catalog} == {
        "Public Domain Mark 1.0"
    }
    assert {item["provider"] for item in catalog} == {
        "National Park Photography",
        "Federal Landscape Photography",
    }

    prohibited_terms = (
        "ai generated",
        "artwork",
        "jiangmao",
        "cartoon",
        "cleveland",
        "drawing",
        "illustration",
        "midjourney",
        "nasa",
        "oil painting",
        "painted",
        "painting",
        "poster",
        "rendering",
        "sketch",
        "smk",
        "stable diffusion",
        "watercolor",
        "watercolour",
        "wikimedia",
    )
    for item in catalog:
        metadata = json.dumps(item, ensure_ascii=False).casefold()
        assert not any(term in metadata for term in prohibited_terms)
        photo_id = item["startdate"].removeprefix("photo-")
        assert photo_id.isdigit()
        assert item["preview_url"].startswith("https://live.staticflickr.com/")
        assert item["full_url"].startswith("https://live.staticflickr.com/")
        assert f"/{photo_id}_" in item["preview_url"]
        assert f"/{photo_id}_" in item["full_url"]
        assert item["copyright_link"].startswith(
            "https://www.flickr.com/photos/"
        )
        assert item["copyright_link"].endswith(f"/{photo_id}")
        assert item["license_url"] == (
            "https://creativecommons.org/publicdomain/mark/1.0/"
        )
