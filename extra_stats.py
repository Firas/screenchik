"""Date / USD-RUB rate / tomorrow's weather for Moscow.

Both APIs are free, keyless, public:
- https://www.cbr-xml-daily.ru/daily_json.js  (CBR USD/RUB rate, mirrors cbr.ru)
- https://api.open-meteo.com/v1/forecast      (Open-Meteo, no key needed)
"""
import json
import time
import urllib.request
from datetime import date, timedelta

_LAT, _LON = 55.75, 37.62  # Moscow

_RATE_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
_WEATHER_URL = (
    f"https://api.open-meteo.com/v1/forecast?latitude={_LAT}&longitude={_LON}"
    "&daily=temperature_2m_max,temperature_2m_min,weathercode&timezone=auto&forecast_days=2"
)

_TTL = 1800  # 30 min — both values change slowly

_cache = {"rate": None, "weather": None, "ts": 0.0}

_MONTHS_RU = ["янв", "фев", "мар", "апр", "май", "июн",
              "июл", "авг", "сен", "окт", "ноя", "дек"]

_WMO_RU = {
    0: "ясно", 1: "почти ясно", 2: "переменная обл.", 3: "пасмурно",
    45: "туман", 48: "изморозь",
    51: "морось", 53: "морось", 55: "морось",
    61: "дождь", 63: "дождь", 65: "сильный дождь",
    71: "снег", 73: "снег", 75: "сильный снег",
    80: "ливень", 81: "ливень", 82: "сильный ливень",
    95: "гроза", 96: "гроза с градом", 99: "гроза с градом",
}


def _fetch_json(url, timeout=6):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _refresh():
    try:
        d = _fetch_json(_RATE_URL)
        _cache["rate"] = d["Valute"]["USD"]["Value"]
    except Exception:
        pass
    try:
        d = _fetch_json(_WEATHER_URL)
        daily = d["daily"]
        # index 1 = tomorrow (index 0 = today), forecast_days=2
        _cache["weather"] = {
            "tmax": daily["temperature_2m_max"][1],
            "tmin": daily["temperature_2m_min"][1],
            "code": daily["weathercode"][1],
        }
    except Exception:
        pass
    _cache["ts"] = time.time()


def get_extra():
    if time.time() - _cache["ts"] > _TTL or _cache["rate"] is None:
        _refresh()

    today = date.today()
    date_str = f"{today.day} {_MONTHS_RU[today.month - 1].upper()}"

    w = _cache["weather"]
    weather_desc = _WMO_RU.get(w["code"], "—") if w else "—"

    return {
        "date_str": date_str,
        "usd_rate": _cache["rate"],
        "weather_tmax": w["tmax"] if w else None,
        "weather_tmin": w["tmin"] if w else None,
        "weather_desc": weather_desc,
    }
