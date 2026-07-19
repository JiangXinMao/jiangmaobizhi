from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Protocol

from .models import Wallpaper


class WallpaperProvider(Protocol):
    """Extension point for bundled or user-configured wallpaper sources."""

    name: str

    def fetch(self, count: int, market: str) -> list[Wallpaper]: ...


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    available: bool
    latency_ms: int
    message: str = ""


@dataclass(slots=True)
class ProviderResult:
    wallpapers: list[Wallpaper]
    provider: str
    health: dict[str, ProviderHealth]


@dataclass(slots=True)
class HistoryPage:
    wallpapers: list[Wallpaper]
    page: int
    page_size: int
    total: int
    has_more: bool
    provider: str = ""


class BundledWallpaperProvider:
    """Loads the offline starter collection shipped with the application."""

    name = "Bundled wallpapers"
    manifest_resource = "jiangmao_wallpaper/ui/assets/starter/manifest.json"

    def __init__(self, manifest_path: Path | None = None):
        self.manifest_path = Path(manifest_path) if manifest_path else None

    def _manifest(self) -> Path:
        if self.manifest_path is not None:
            return self.manifest_path
        from .ui.resources import resource_path

        return resource_path(self.manifest_resource)

    def _load(self) -> list[Wallpaper]:
        manifest = self._manifest().resolve()
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Bundled wallpaper manifest must contain a list")

        wallpapers: list[Wallpaper] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            asset = item.get("asset")
            if not isinstance(asset, str) or not asset.strip():
                continue
            local_asset = (manifest.parent / asset).resolve()
            try:
                local_asset.relative_to(manifest.parent)
            except ValueError:
                continue
            if not local_asset.is_file():
                continue
            wallpaper = Wallpaper.from_dict(item)
            wallpaper.local_preview = str(local_asset)
            wallpaper.local_full = str(local_asset)
            wallpapers.append(wallpaper)
        return list({wallpaper.key: wallpaper for wallpaper in wallpapers}.values())

    def fetch(self, count: int = 8, market: str = "zh-CN") -> list[Wallpaper]:
        del market
        return self._load()[: max(1, int(count))]

    def fetch_page(self, page: int, page_size: int = 30) -> HistoryPage:
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        wallpapers = self._load()
        offset = (page - 1) * page_size
        selected = wallpapers[offset : offset + page_size]
        return HistoryPage(
            wallpapers=selected,
            page=page,
            page_size=page_size,
            total=len(wallpapers),
            has_more=offset + len(selected) < len(wallpapers),
            provider=self.name,
        )


class ProviderChain:
    def __init__(self, providers: list[WallpaperProvider]):
        self.providers = providers

    def fetch(self, count: int = 8, market: str = "zh-CN") -> ProviderResult:
        health: dict[str, ProviderHealth] = {}
        for provider in self.providers:
            started = perf_counter()
            try:
                wallpapers = provider.fetch(count, market)
                unique = list({item.key: item for item in wallpapers}.values())
                elapsed = round((perf_counter() - started) * 1000)
                health[provider.name] = ProviderHealth(bool(unique), elapsed)
                if unique:
                    return ProviderResult(unique, provider.name, health)
            except Exception as error:
                elapsed = round((perf_counter() - started) * 1000)
                health[provider.name] = ProviderHealth(False, elapsed, str(error))
        return ProviderResult([], "Local cache", health)

    def fetch_page(self, page: int, page_size: int = 30) -> HistoryPage:
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        for provider in self.providers:
            fetch_page = getattr(provider, "fetch_page", None)
            if callable(fetch_page):
                result = fetch_page(page, page_size)
                if result.wallpapers or result.has_more:
                    return result
        return HistoryPage([], page, page_size, 0, False, "Local cache")


def default_provider_chain() -> ProviderChain:
    return ProviderChain([BundledWallpaperProvider()])
