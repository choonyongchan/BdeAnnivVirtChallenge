"""Shared dashboard + scraper settings from src/config.yaml. No secrets here.

Read once; every key is required, so a missing config.yaml or a missing key
fails loudly instead of silently falling back. (Unrelated to the retired
src_bak/config.py, the old .env Strava-API credential loader.)
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@dataclass
class Config:
    club_name: str
    club_id: str
    challenge_start: str
    timezone: str
    weather_lat: float
    weather_lon: float
    announcement_path: str   # repo-root-relative; generate.py resolves it
    browser_channel: str   # "" -> Playwright's bundled Chromium; "chrome"/"msedge" drive a system browser
    browser_headless: bool


def load() -> Config:
    """config.yaml -> Config; raises if the file or any required key is missing."""
    if not CONFIG_PATH.exists():
        raise ValueError(f"missing config file: {CONFIG_PATH}")
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}

    def g(*path):
        node = cfg
        for key in path:
            if not isinstance(node, dict) or key not in node or node[key] is None:
                raise ValueError(f"missing config key {'.'.join(path)!r} in {CONFIG_PATH}")
            node = node[key]
        return node

    return Config(
        club_name=g("club", "name"),
        club_id=g("club", "id"),
        challenge_start=str(g("challenge_start")),
        timezone=g("timezone"),
        weather_lat=g("weather", "latitude"),
        weather_lon=g("weather", "longitude"),
        announcement_path=g("announcement_path"),
        browser_channel=g("browser", "channel"),
        browser_headless=g("browser", "headless"),
    )


settings = load()
