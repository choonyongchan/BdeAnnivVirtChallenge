"""Shared dashboard + scraper settings from src/config.yaml. No secrets here.

Read once; any missing key falls back to the Config field defaults, so the
pipeline still runs if config.yaml is absent or partial. (Unrelated to the
retired src_bak/config.py, the old .env Strava-API credential loader.)
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@dataclass
class Config:
    club_name: str = "8SAB 50th Anniversary Virtual Challenge"
    club_id: str = "2211123"
    challenge_start: str = "2026-09-14"
    timezone: str = "Asia/Singapore"
    weather_lat: float = 1.3835
    weather_lon: float = 103.7478
    announcement_path: str = "src/announcement.md"   # repo-root-relative; generate.py resolves it
    browser_channel: str = ""   # "" -> Playwright's bundled Chromium; "chrome"/"msedge" drive a system browser
    browser_headless: bool = True


def load() -> Config:
    """config.yaml -> Config; any missing key keeps its dataclass default."""
    cfg = {}
    if CONFIG_PATH.exists():
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}

    def g(*path, default):
        node = cfg
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return default if node is None else node

    d = Config()
    return Config(
        club_name=g("club", "name", default=d.club_name),
        club_id=g("club", "id", default=d.club_id),
        challenge_start=str(g("challenge_start", default=d.challenge_start)),
        timezone=g("timezone", default=d.timezone),
        weather_lat=g("weather", "latitude", default=d.weather_lat),
        weather_lon=g("weather", "longitude", default=d.weather_lon),
        announcement_path=g("announcement_path", default=d.announcement_path),
        browser_channel=g("browser", "channel", default=d.browser_channel),
        browser_headless=g("browser", "headless", default=d.browser_headless),
    )


settings = load()
