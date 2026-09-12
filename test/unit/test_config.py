"""Unit tests for shared settings loading.

`config.load()` reads src/config.yaml and every key is required: a missing
file, a missing key, or an explicit null all raise `ValueError`.
`challenge_start` is coerced to a string because YAML parses a bare date.
`generate.load_config()` additionally resolves the announcement path against
the repo root.
"""
from pathlib import Path

import pytest

from src import config
from src.dashboard import generate

FULL_YAML = (
    "club:\n  name: Test Club\n  id: '42'\n"
    "challenge_start: 2026-01-01\n"
    "timezone: Asia/Singapore\n"
    "weather:\n  latitude: 5.5\n  longitude: 6.6\n"
    "announcement_path: src/announcement.md\n"
    "browser:\n  channel: ''\n  headless: false\n"
)


def _yaml(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_full_file_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", _yaml(tmp_path, FULL_YAML))
    cfg = config.load()
    assert cfg.club_name == "Test Club"
    assert cfg.club_id == "42"
    assert cfg.challenge_start == "2026-01-01"          # bare YAML date -> str
    assert cfg.weather_lat == 5.5
    assert cfg.browser_headless is False


def test_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "absent.yaml")
    with pytest.raises(ValueError):
        config.load()


def test_missing_top_level_key_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", _yaml(tmp_path, "club:\n  name: Test Club\n  id: '42'\n"))
    with pytest.raises(ValueError):
        config.load()


def test_missing_nested_key_raises(tmp_path, monkeypatch):
    text = FULL_YAML.replace("  id: '42'\n", "")
    monkeypatch.setattr(config, "CONFIG_PATH", _yaml(tmp_path, text))
    with pytest.raises(ValueError):
        config.load()


def test_explicit_null_raises(tmp_path, monkeypatch):
    text = FULL_YAML.replace("timezone: Asia/Singapore\n", "timezone:\n")
    monkeypatch.setattr(config, "CONFIG_PATH", _yaml(tmp_path, text))
    with pytest.raises(ValueError):
        config.load()


def test_generate_load_config_resolves_announcement_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", _yaml(tmp_path, FULL_YAML))
    cfg = generate.load_config()
    assert isinstance(cfg.announcement_path, Path)
    assert cfg.announcement_path == generate.REPO_ROOT / "src/announcement.md"
