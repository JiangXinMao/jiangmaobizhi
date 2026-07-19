"""Minimal custom provider example. This module is not enabled by default."""

from __future__ import annotations

import os

import requests

from jiangmao_wallpaper.models import Wallpaper


class CustomWallpaperProvider:
    name = "My wallpaper API"
    endpoint = "https://api.example.com/v1/wallpapers"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("WALLPAPER_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("WALLPAPER_API_KEY is not configured")

    def fetch(self, count: int = 8, market: str = "zh-CN") -> list[Wallpaper]:
        response = requests.get(
            self.endpoint,
            params={"count": count, "market": market},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=(3.05, 15),
        )
        response.raise_for_status()
        payload = response.json()
        return [
            Wallpaper(
                title=item["title"],
                copyright=item.get("copyright", ""),
                startdate=str(item["id"]),
                preview_url=item["preview_url"],
                full_url=item["full_url"],
                provider=self.name,
                copyright_link=item.get("source_url", ""),
                artist=item.get("artist", ""),
                license_name=item.get("license_name", ""),
                license_url=item.get("license_url", ""),
            )
            for item in payload.get("items", [])
        ]
