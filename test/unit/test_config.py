"""Unit tests for shared settings loading.

`config.load()` reads src/config.yaml but every key is independently optional:
a missing file, a missing key, or an explicit null all fall back to the
`Config` dataclass default. `challenge_start` is coerced to a string because
YAML parses a bare date. `generate.load_config()` additionally resolves the
announcement path against the repo root.
"""
from pathlib import Path

import pytest

from src import config
from src.dashboard import generate


def _yaml(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_is_all_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "absent.yaml")
    assert config.load() == config.Config()


def test_partial_file_overrides_one_key_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", _yaml(tmp_path, "challenge_start: 2026-01-01\n"))
    cfg = config.load()
    assert cfg.challenge_start == "2026-01-01"          # bare YAML date -> str
    assert cfg.club_id == config.Config().club_id       # untouched key still defaults


def test_nested_keys_are_read(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", _yaml(tmp_path, (
        "club:\n  name: Test Club\n"
        "weather:\n  latitude: 5.5\n"
        "browser:\n  headless: false\n"
    )))
    cfg = config.load()
    assert cfg.club_name == "Test Club"
    assert cfg.club_id == config.Config().club_id       # club.id absent -> default
    assert cfg.weather_lat == 5.5
    assert cfg.browser_headless is False


def test_explicit_null_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", _yaml(tmp_path, "timezone:\n"))
    assert config.load().timezone == config.Config().timezone


def test_generate_load_config_resolves_announcement_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "absent.yaml")
    cfg = generate.load_config()
    assert isinstance(cfg.announcement_path, Path)
    assert cfg.announcement_path == generate.REPO_ROOT / config.Config().announcement_path
