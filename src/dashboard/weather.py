"""Current weather via Open-Meteo (no API key); any failure yields "" so the page still renders."""
import json
import urllib.request

WEATHER_CODES = {
    0: ("☀️", "Clear"), 1: ("🌤️", "Mostly clear"), 2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"), 45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌦️", "Drizzle"), 55: ("🌧️", "Heavy drizzle"),
    61: ("🌧️", "Light rain"), 63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
    71: ("🌨️", "Light snow"), 73: ("🌨️", "Snow"), 75: ("❄️", "Heavy snow"),
    80: ("🌦️", "Light showers"), 81: ("🌧️", "Showers"), 82: ("⛈️", "Heavy showers"),
    95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "Thunderstorm w/ hail"), 99: ("⛈️", "Severe storm"),
}


def weather_html(lat, lon, tz) -> str:
    """The header's ``<span class="weather-widget">`` from Open-Meteo, or "" on any failure."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m"
        f"&wind_speed_unit=kmh&timezone={tz}"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            c = json.load(r)["current"]
        icon, desc = WEATHER_CODES.get(int(c.get("weather_code", 0)), ("🌡️", ""))
        temp, wind = round(c.get("temperature_2m", 0)), round(c.get("wind_speed_10m", 0))
    except Exception as e:
        print(f"  Weather fetch failed: {e}")
        return ""
    return (
        '<span class="weather-widget">'
        f'{icon} <strong>{temp}°C</strong>'
        f' · {desc}'
        f' · 💨 {wind} km/h'
        '</span>'
    )
