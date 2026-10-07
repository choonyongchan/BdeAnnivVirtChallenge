"""Dashboard + scraper settings from shared/config.yaml (no secrets).
Every key is required, so a missing file or key fails loudly (KeyError) instead of falling back."""
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
    members_hours: object   # "*" or a list of local hours
    feed_hours: object
    leaderboard_hours: object
    member_scan_hours: object
    weather_lat: float
    weather_lon: float
    announcement_path: str   # repo-root-relative; generate.py resolves it
    browser_channel: str   # "" -> Playwright's bundled Chromium; "chrome"/"msedge" drive a system browser
    browser_headless: bool


def load() -> Config:
    """config.yaml -> Config; ValueError if the file is missing, KeyError if a key is."""
    if not CONFIG_PATH.exists():
        raise ValueError(f"missing config file: {CONFIG_PATH}")
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return Config(
        club_name=cfg["club"]["name"],
        club_id=cfg["club"]["id"],
        challenge_start=str(cfg["challenge_start"]),
        timezone=cfg["timezone"],
        members_hours=cfg["schedule"]["members"],
        feed_hours=cfg["schedule"]["feed"],
        leaderboard_hours=cfg["schedule"]["leaderboard"],
        member_scan_hours=cfg["schedule"]["member_scan"],
        weather_lat=cfg["weather"]["latitude"],
        weather_lon=cfg["weather"]["longitude"],
        announcement_path=cfg["announcement_path"],
        browser_channel=cfg["browser"]["channel"],
        browser_headless=cfg["browser"]["headless"],
    )


settings = load()
