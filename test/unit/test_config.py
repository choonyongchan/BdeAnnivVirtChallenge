"""Unit tests for config.load(): every key required (missing file/key/null -> ValueError),
challenge_start coerced to str because YAML parses a bare date."""
import pytest

from src import config

FULL_YAML = (
    "club:\n  name: Test Club\n  id: '42'\n"
    "challenge_start: 2026-01-01\n"
    "timezone: Asia/Singapore\n"
    "schedule:\n  recent_activities: '*'\n  member_scan: [23]\n"
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

