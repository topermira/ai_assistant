"""Погода через wttr.in."""
import re
import json
import urllib.parse
import urllib.request

_RE_WEATHER_CITY = re.compile(
    r"погод[аыуе]?\s+(?:сейчас\s+|на\s+улице\s+|сегодня\s+)?(?:в|во|на)\s+([А-Яа-яЁёA-Za-z\-]+(?:\s+[А-Яа-яЁёA-Za-z\-]+)?)",
    re.IGNORECASE
)
_RE_WEATHER_CITY_ALT = re.compile(
    r"погод[аыуе]?\s+(?:сейчас\s+|на\s+улице\s+|сегодня\s+)?([А-Яа-яЁёA-Za-z\-]+(?:\s+[А-Яа-яЁёA-Za-z\-]+)?)",
    re.IGNORECASE
)
_WEATHER_NOISE = {"сейчас", "сегодня", "на", "улице", "завтра", "пожалуйста", "погода", "во", "в"}
_WEATHER_TRAIL_STOP = {
    "на", "в", "во", "у", "по", "при", "около", "возле", "рядом",
    "улица", "улице", "улицу", "проспект", "проспекте",
    "переулок", "переулке", "площадь", "площади", "шоссе", "бульвар", "бульваре",
}

_WEATHER_DESC_RU = {
    "sunny": "солнечно", "clear": "ясно", "partly cloudy": "переменная облачность",
    "cloudy": "облачно", "overcast": "пасмурно", "mist": "туман", "fog": "туман",
    "freezing fog": "ледяной туман",
    "patchy rain nearby": "местами дождь", "patchy rain possible": "возможен дождь",
    "patchy light rain": "местами небольшой дождь", "patchy light drizzle": "местами слабая морось",
    "patchy drizzle": "местами морось", "light drizzle": "слабая морось",
    "light rain": "небольшой дождь", "light rain shower": "небольшой ливень",
    "light rain showers": "небольшие ливни", "moderate rain": "умеренный дождь",
    "moderate rain at times": "временами умеренный дождь", "heavy rain": "сильный дождь",
    "heavy rain at times": "временами сильный дождь", "heavy rain shower": "сильный ливень",
    "torrential rain shower": "проливной ливень", "light freezing rain": "ледяной дождь",
    "moderate or heavy freezing rain": "сильный ледяной дождь", "freezing drizzle": "ледяная морось",
    "light snow": "небольшой снег", "light snow showers": "небольшие снегопады",
    "light snow shower": "небольшой снегопад", "patchy light snow": "местами небольшой снег",
    "patchy snow nearby": "местами снег", "patchy snow possible": "возможен снег",
    "patchy moderate snow": "местами умеренный снег", "patchy heavy snow": "местами сильный снег",
    "moderate snow": "умеренный снег", "heavy snow": "сильный снег", "blizzard": "метель",
    "blowing snow": "позёмок",
    "patchy sleet possible": "возможен мокрый снег", "patchy sleet nearby": "местами мокрый снег",
    "light sleet": "небольшой мокрый снег", "moderate or heavy sleet": "сильный мокрый снег",
    "sleet": "мокрый снег",
    "patchy freezing drizzle nearby": "местами ледяная морось",
    "patchy freezing drizzle possible": "возможна ледяная морось",
    "thundery outbreaks possible": "возможны грозы", "thundery outbreaks in nearby": "местами грозы",
    "patchy light rain with thunder": "местами дождь с грозой",
    "patchy light rain in area with thunder": "местами дождь с грозой",
    "moderate or heavy rain with thunder": "ливни с грозой",
    "moderate or heavy rain shower": "сильные ливни",
    "moderate or heavy rain in area with thunder": "сильные ливни с грозой",
    "patchy light rain with thunderstorm": "местами дождь с грозой",
}

_RE_DAY_WORDS = re.compile(r"\b(?:на\s+)?(?:послезавтра|завтра(?:шн\w+)?|сегодня(?:шн\w+)?)\b", re.IGNORECASE)
_DAY_NAMES = {0: "Сегодня", 1: "Завтра", 2: "Послезавтра"}


def parse_weather_day(text):
    """0 — сегодня/сейчас, 1 — завтра, 2 — послезавтра (wttr.in отдаёт прогноз на 3 дня)."""
    low = (text or "").lower()
    if re.search(r"\bпослезавтра\b", low):
        return 2
    if re.search(r"\bзавтра(?:шн\w+)?\b", low):
        return 1
    return 0


def _extract_weather_city(text):
    # «погода завтра в Москве» / «на завтра в Москве»: убираем слова про день, иначе «завтра в» принималось за город
    text = _RE_DAY_WORDS.sub(" ", text or "")
    text = re.sub(r"\s+", " ", text)
    for pat in (_RE_WEATHER_CITY, _RE_WEATHER_CITY_ALT):
        m = pat.search(text)
        if m:
            city = m.group(1).strip(" .,!?")
            words = city.split()
            while words and words[-1].lower() in _WEATHER_TRAIL_STOP: words.pop()
            city = " ".join(words).strip(" .,!?")
            if city and city.lower() not in _WEATHER_NOISE: return city
    return None

def _pick_weather_desc(current):
    candidates = []
    lang_ru = current.get("lang_ru")
    if lang_ru and lang_ru[0].get("value"): candidates.append(lang_ru[0]["value"])
    candidates.append(current.get("weatherDesc", [{}])[0].get("value", ""))
    for c in candidates:
        if not c: continue
        low = c.strip().lower()
        if low in _WEATHER_DESC_RU: return _WEATHER_DESC_RU[low]
        if re.search(r"[А-Яа-яЁё]", c): return c.strip()
    return candidates[-1] if candidates else "неизвестно"

def _format_forecast(data, city, day):
    """Чистая функция (без сети): прогноз на день `day` из ответа wttr.in j1."""
    days = data.get("weather") or []
    if day >= len(days):
        return None
    d = days[day]
    hourly = d.get("hourly") or []
    noon = hourly[4] if len(hourly) > 4 else (hourly[len(hourly) // 2] if hourly else {})
    desc = _pick_weather_desc(noon) if noon else "неизвестно"

    def _num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    rain = max([_num(h.get("chanceofrain")) for h in hourly] or [0])
    snow = max([_num(h.get("chanceofsnow")) for h in hourly] or [0])
    where = f" в {city}" if city else ""
    out = (f"{_DAY_NAMES.get(day, 'Потом')}{where}: днём {desc.lower()}, "
           f"от {d.get('mintempC', '?')} до {d.get('maxtempC', '?')}°C")
    if snow >= 30 and snow >= rain:
        out += f", вероятность снега {snow}%"
    elif rain >= 30:
        out += f", вероятность дождя {rain}%"
    else:
        out += ", осадков не ждут"
    return out + "."


def get_weather(city=None, day=0):
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1&lang=ru&m" if city else "https://wttr.in/?format=j1&lang=ru&m"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0", "Accept-Language": "ru,en;q=0.5"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8", errors="replace")
        if "Unknown location" in raw or "Location not found" in raw: return None
        data = json.loads(raw)
        if day:
            return _format_forecast(data, city, day)
        c = data["current_condition"][0]
        desc = _pick_weather_desc(c)
        where = f"В {city}" if city else "Сейчас на улице"
        return (f"{where} {desc.lower()}, {c['temp_C']}°C "
                f"(ощущается как {c['FeelsLikeC']}°C), влажность {c['humidity']}%, ветер {c['windspeedKmph']} км/ч.")
    except Exception as e:
        print(f"⚠️ Погода: {e}")
        return None
