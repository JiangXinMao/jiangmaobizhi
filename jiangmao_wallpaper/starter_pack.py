from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

from .cache import ImageCache
from .models import Wallpaper
from .state import StateStore
from .ui.resources import resource_path


LOGGER = logging.getLogger(__name__)
STARTER_MANIFEST = "jiangmao_wallpaper/ui/assets/starter/manifest.json"


def _starter_entries(manifest_path: Path) -> list[tuple[Wallpaper, Path]]:
    manifest_path = Path(manifest_path).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Starter manifest must contain a list")
    entries: list[tuple[Wallpaper, Path]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        asset = item.get("asset")
        if not isinstance(asset, str) or not asset.strip():
            continue
        source = (manifest_path.parent / asset).resolve()
        try:
            source.relative_to(manifest_path.parent)
        except ValueError:
            continue
        wallpaper = Wallpaper.from_dict(item)
        if not wallpaper.key or not wallpaper.preview_url or not wallpaper.full_url:
            continue
        entries.append((wallpaper, source))
    return entries


def seed_starter_wallpapers(
    state_store: StateStore,
    cache: ImageCache,
    manifest_path: Path | None = None,
) -> int:
    path = manifest_path or resource_path(STARTER_MANIFEST)
    try:
        entries = _starter_entries(path)
    except (OSError, ValueError, json.JSONDecodeError):
        LOGGER.exception("Unable to load bundled wallpaper manifest")
        return 0

    state = state_store.load()
    index_by_key = {wallpaper.key: index for index, wallpaper in enumerate(state.wallpapers)}
    added = 0
    changed = False
    for starter, source in entries:
        try:
            local_preview = cache.import_file(starter, source, "preview")
        except (OSError, ValueError):
            LOGGER.exception("Unable to import bundled wallpaper %s", starter.key)
            continue
        starter.local_preview = str(local_preview)
        existing_index = index_by_key.get(starter.key)
        if existing_index is None:
            index_by_key[starter.key] = len(state.wallpapers)
            state.wallpapers.append(starter)
            added += 1
            changed = True
            continue
        existing = state.wallpapers[existing_index]
        enriched = replace(
            existing,
            title=existing.title or starter.title,
            headline=existing.headline or starter.headline,
            copyright=existing.copyright or starter.copyright,
            preview_url=existing.preview_url or starter.preview_url,
            full_url=existing.full_url or starter.full_url,
            provider=existing.provider or starter.provider,
            copyright_link=existing.copyright_link or starter.copyright_link,
            artist=existing.artist or starter.artist,
            license_name=existing.license_name or starter.license_name,
            license_url=existing.license_url or starter.license_url,
            local_preview=str(local_preview),
        )
        if enriched != existing:
            state.wallpapers[existing_index] = enriched
            changed = True
    if changed:
        state.current_index = min(
            state.current_index,
            max(0, len(state.wallpapers) - 1),
        )
        state_store.save(state)
    return added
