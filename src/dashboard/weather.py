"""Current weather via Open-Meteo (no API key). Network-optional: any failure
returns "" so the page still renders. Ported from src_bak/generate.py."""

WEATHER_CODES = {
    0: ("☀️", "Clear"), 1: ("🌤️", "Mostly clear"), 2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"), 45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌦️", "Drizzle"), 55: ("🌧️", "Heavy drizzle"),
    61: ("🌧️", "Light rain"), 63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
    71: ("🌨️", "Light snow"), 73: ("🌨️", "Snow"), 75: ("❄️", "Heavy snow"),
    80: ("🌦️", "Light showers"), 81: ("🌧️", "Showers"), 82: ("⛈️", "Heavy showers"),
    95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "Thunderstorm w/ hail"), 99: ("⛈️", "Severe storm"),
}


def fetch_weather(lat, lon, tz) -> dict:
    """Fetch current weather from Open-Meteo. Returns {"ok": False} on any failure."""
    try:
        import requests  # only needed on this path; absent in no-network runs
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m"
            f"&wind_speed_unit=kmh&timezone={tz}"
        )
        c = requests.get(url, timeout=8).json()["current"]
        code = int(c.get("weather_code", 0))
        icon, desc = WEATHER_CODES.get(code, ("🌡️", ""))
        return {
            "icon": icon,
            "desc": desc,
            "temp": round(c.get("temperature_2m", 0)),
            "wind": round(c.get("wind_speed_10m", 0)),
            "ok": True,
        }
    except Exception as e:
        print(f"  Weather fetch failed: {e}")
        return {"ok": False}


def weather_html(lat, lon, tz) -> str:
    """The finished ``<span class="weather-widget">`` for the header, or "" on failure."""
    w = fetch_weather(lat, lon, tz)
    if not w["ok"]:
        return ""
    return (
        '<span class="weather-widget">'
        f'{w["icon"]} <strong>{w["temp"]}°C</strong>'
        f' · {w["desc"]}'
        f' · 💨 {w["wind"]} km/h'
        '</span>'
    )
