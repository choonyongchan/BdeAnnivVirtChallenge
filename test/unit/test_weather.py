"""Unit tests for the Open-Meteo weather widget.

The rule is "network-optional": any failure at all — no connection, a non-JSON
body, a missing key, an unknown weather code — must degrade to {"ok": False}
and an empty widget string, never an exception, so the dashboard still renders.
`requests` is imported lazily inside fetch_weather, so patching requests.get
here reaches it.
"""
import requests

from src.dashboard import weather


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _get_returning(payload):
    return lambda *a, **k: _Resp(payload)


def _get_raising(exc):
    def _get(*a, **k):
        raise exc
    return _get


def test_success_maps_code_and_rounds(monkeypatch):
    payload = {"current": {"temperature_2m": 30.4, "weather_code": 61, "wind_speed_10m": 12.6}}
    monkeypatch.setattr(requests, "get", _get_returning(payload))
    assert weather.fetch_weather(1.0, 2.0, "Asia/Singapore") == {
        "icon": "🌧️", "desc": "Light rain", "temp": 30, "wind": 13, "ok": True,
    }


def test_unknown_weather_code_still_ok(monkeypatch):
    payload = {"current": {"temperature_2m": 25, "weather_code": 1234, "wind_speed_10m": 5}}
    monkeypatch.setattr(requests, "get", _get_returning(payload))
    w = weather.fetch_weather(1, 2, "UTC")
    assert w["ok"] is True and w["icon"] == "🌡️" and w["desc"] == ""


def test_network_error_returns_not_ok(monkeypatch):
    monkeypatch.setattr(requests, "get", _get_raising(RuntimeError("timeout")))
    assert weather.fetch_weather(1, 2, "UTC") == {"ok": False}


def test_malformed_body_returns_not_ok(monkeypatch):
    monkeypatch.setattr(requests, "get", _get_returning(ValueError("not json")))
    assert weather.fetch_weather(1, 2, "UTC") == {"ok": False}


def test_missing_current_key_returns_not_ok(monkeypatch):
    monkeypatch.setattr(requests, "get", _get_returning({"hourly": {}}))
    assert weather.fetch_weather(1, 2, "UTC") == {"ok": False}


def test_weather_html_is_empty_on_failure(monkeypatch):
    monkeypatch.setattr(requests, "get", _get_raising(RuntimeError()))
    assert weather.weather_html(1, 2, "UTC") == ""


def test_weather_html_renders_when_ok(monkeypatch):
    payload = {"current": {"temperature_2m": 29, "weather_code": 0, "wind_speed_10m": 8}}
    monkeypatch.setattr(requests, "get", _get_returning(payload))
    html = weather.weather_html(1, 2, "UTC")
    assert html.startswith('<span class="weather-widget">') and html.endswith("</span>")
    assert "29°C" in html and "Clear" in html and "8 km/h" in html
