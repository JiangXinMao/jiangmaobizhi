import json

import pytest

from jiangmao_wallpaper.models import AppSettings, AppState, Wallpaper
from jiangmao_wallpaper.state import StateStore


def test_state_store_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    expected = AppState(
        favorites={"20260710"},
        current_index=2,
        settings=AppSettings(auto_interval_minutes=30, animation_enabled=False),
    )

    store.save(expected)

    assert store.load() == expected
    assert not (tmp_path / "state.json.tmp").exists()


def test_taskbar_settings_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    expected = AppState(settings=AppSettings(
        taskbar_mode="frosted",
        taskbar_intensity=72,
        taskbar_restore_on_start=False,
        taskbar_all_displays=False,
    ))
    store.save(expected)
    assert store.load() == expected


def test_taskbar_settings_normalize_invalid_values():
    state = AppState.from_dict({"settings": {
        "taskbar_mode": "unknown",
        "taskbar_intensity": 999,
    }})
    assert state.settings.taskbar_mode == "default"
    assert state.settings.taskbar_intensity == 100


def test_taskbar_settings_clamp_intensity_to_lower_bound():
    state = AppState.from_dict({"settings": {"taskbar_intensity": -1}})

    assert state.settings.taskbar_intensity == 20


def test_taskbar_settings_use_default_for_nonnumeric_intensity():
    state = AppState.from_dict({"settings": {"taskbar_intensity": "invalid"}})

    assert state.settings.taskbar_intensity == 88


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("FALSE", False),
        ("on", True),
        ("Off", False),
        ("yes", True),
        ("NO", False),
        ("1", True),
        ("0", False),
        ("unknown", True),
        (2, True),
    ],
)
def test_taskbar_boolean_settings_normalize_legacy_values(raw, expected):
    settings = AppSettings(
        taskbar_restore_on_start=raw,
        taskbar_all_displays=raw,
    )

    assert settings.taskbar_restore_on_start is expected
    assert settings.taskbar_all_displays is expected


def test_state_store_string_false_cannot_enable_taskbar_startup_or_all_displays(
    tmp_path,
):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "settings": {
                    "taskbar_mode": "transparent",
                    "taskbar_restore_on_start": "false",
                    "taskbar_all_displays": "false",
                }
            }
        ),
        encoding="utf-8",
    )

    settings = StateStore(path).load().settings

    assert settings.taskbar_restore_on_start is False
    assert settings.taskbar_all_displays is False


def test_state_store_preserves_state_when_taskbar_mode_is_unhashable(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "favorites": ["20260710"],
            "settings": {"taskbar_mode": []},
        }),
        encoding="utf-8",
    )

    state = StateStore(path).load()

    assert state.favorites == {"20260710"}
    assert state.settings.taskbar_mode == "default"


def test_state_store_recovers_from_corrupt_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")

    assert StateStore(path).load() == AppState()


def test_state_store_marks_missing_animation_preference_version_as_legacy(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"settings": {"animation_enabled": False}}),
        encoding="utf-8",
    )

    state = StateStore(path).load()

    assert state.settings.animation_enabled is False
    assert state.settings.animation_preference_version == 0


def test_wallpaper_license_metadata_round_trips_through_state(tmp_path):
    store = StateStore(tmp_path / "state.json")
    wallpaper = Wallpaper(
        title="湖面晨光",
        copyright="John Marin · Public Domain · SMK Open",
        startdate="smk-KKS2004-95",
        preview_url="https://iip.smk.dk/preview.jpg",
        full_url="https://iip.smk.dk/full.jpg",
        provider="SMK Open",
        copyright_link="https://open.smk.dk/artwork/image/KKS2004-95",
        artist="示例作者",
        license_name="Public Domain Mark 1.0",
        license_url="https://creativecommons.org/publicdomain/mark/1.0/",
    )

    store.save(AppState(wallpapers=[wallpaper]))
    restored = store.load().wallpapers[0]

    assert restored.provider == "SMK Open"
    assert restored.artist == "示例作者"
    assert restored.license_name == "Public Domain Mark 1.0"
    assert restored.license_url.endswith("/publicdomain/mark/1.0/")


def test_wallpaper_headline_round_trips_through_state(tmp_path):
    store = StateStore(tmp_path / "state.json")
    wallpaper = Wallpaper(
        title="澳大利亚维多利亚州",
        headline="陆地与海洋的鸟瞰图",
        copyright="© Example",
        startdate="20260710",
        preview_url="https://example.com/1080.jpg",
        full_url="https://example.com/4k.jpg",
    )

    store.save(AppState(wallpapers=[wallpaper]))

    assert store.load().wallpapers[0].headline == "陆地与海洋的鸟瞰图"
def test_lock_screen_sync_setting_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    expected = AppState(settings=AppSettings(lock_screen_sync_enabled=True))

    store.save(expected)

    assert store.load().settings.lock_screen_sync_enabled is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("true", True), ("false", False), ("invalid", False)],
)
def test_lock_screen_sync_setting_normalizes_values(raw, expected):
    assert AppSettings(lock_screen_sync_enabled=raw).lock_screen_sync_enabled is expected
