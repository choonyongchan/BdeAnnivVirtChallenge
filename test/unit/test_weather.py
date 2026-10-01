"""Unit tests for the Open-Meteo weather widget: any failure must degrade to "", never raise.
urllib.request.urlopen is patched, so no network is touched."""
import io
import json
import urllib.request

from src.dashboard import weather


def _urlopen_returning(body):
    return lambda *a, **k: io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())


def _urlopen_raising(exc):
    def _open(*a, **k):
        raise exc
    return _open


def test_success_maps_code_and_rounds(monkeypatch):
    payload = {"current": {"temperature_2m": 30.4, "weather_code": 61, "wind_speed_10m": 12.6}}
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning(payload))
    assert weather.weather_html(1.0, 2.0, "Asia/Singapore") == (
        '<span class="weather-widget">🌧️ <strong>30°C</strong> · Light rain · 💨 13 km/h</span>')


def test_unknown_weather_code_still_renders(monkeypatch):
    payload = {"current": {"temperature_2m": 25, "weather_code": 1234, "wind_speed_10m": 5}}
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning(payload))
    assert weather.weather_html(1, 2, "UTC").startswith('<span class="weather-widget">🌡️ ')


def test_network_error_returns_empty(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_raising(RuntimeError("timeout")))
    assert weather.weather_html(1, 2, "UTC") == ""


def test_malformed_body_returns_empty(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning(b"not json"))
    assert weather.weather_html(1, 2, "UTC") == ""


def test_missing_current_key_returns_empty(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning({"hourly": {}}))
    assert weather.weather_html(1, 2, "UTC") == ""


def test_renders_temp_desc_and_wind(monkeypatch):
    payload = {"current": {"temperature_2m": 29, "weather_code": 0, "wind_speed_10m": 8}}
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning(payload))
    html = weather.weather_html(1, 2, "UTC")
    assert html.startswith('<span class="weather-widget">') and html.endswith("</span>")
    assert "29°C" in html and "Clear" in html and "8 km/h" in html
