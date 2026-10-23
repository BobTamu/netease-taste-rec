# -*- coding: utf-8 -*-
"""City-name -> weather -> recommendation tone, for netease-taste-rec.

Why this exists
---------------
The skill used to call Open-Meteo with hard-coded coordinates for one city and a
hard-coded `timezone`. That silently breaks for any other city:

  * Open-Meteo wants latitude/longitude, not a city name, so a geocoding step
    is required before the forecast call;
  * if the timezone is wrong, "today" and the 深夜/清晨 judgement shift by hours —
    a wrong date means the weekday, the holiday countdown and the whole context
    line are wrong.

This script resolves a city name to coordinates + timezone, pulls the forecast,
and derives a tone hint using the rules in SKILL.md §3. It never invents weather:
if a field is missing it is reported as null.

Usage
-----
    python weather.py "北京"
    python weather.py "Tokyo"
    python weather.py "墨尔本" --json
    python weather.py --lat 39.9042 --lon 116.4074 --tz Asia/Shanghai

Exit codes: 0 ok, 1 geocoding failed, 2 forecast failed.
"""
import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO = {
    0: "晴", 1: "大部晴朗", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "小阵雪", 86: "大阵雪",
    95: "雷雨", 96: "雷雨伴小冰雹", 99: "雷雨伴大冰雹",
}

RAIN_CODES = set(range(51, 68)) | {80, 81, 82, 95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
CLEAR_CODES = {0, 1}

# Tone thresholds — kept explicit so the derived label can be audited.
HUMID_HIGH = 80      # %  -> indoor, low-frequency
WARM_HOT_MIN = 25    # °C -> bright / road-trip
WARM_HOT_MAX = 33    # °C
COLD_MAX = 10        # °C -> warm, thick low end
WINDY_KMH = 25       # km/h -> counts as 大风
SMALL_CITY_POP = 500_000  # below this, a geocoding hit is suspicious


def get_json(url, params, timeout=25):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url + "?" + q,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s)


FEATURE_RANK = {"PPLC": 4, "PPLA": 3, "PPLA2": 2, "PPL": 1}


def geocode(name, pick=None):
    """Resolve a city name to the most plausible match.

    Naively taking `results[0]` is WRONG: querying "New York" with language=zh
    returns 约克/內布拉斯加州 (population 7,864) ahead of New York City
    (8,804,190).

    Naively ranking by population alone is ALSO wrong: the zh and en queries
    return different localised names for the same place (墨尔本 vs Melbourne),
    so an exact-name test against the raw query silently fails for the real city
    and a same-named village wins (Melbourne/England, pop 793, beat
    Melbourne/Australia, pop 5,435,590). Fix: keep an alias set per coordinate.

    Returns (chosen, ordered_candidates).
    """
    cands = {}
    for lang in ("zh", "en"):
        try:
            d = get_json(GEOCODE_URL, {"name": name, "count": 10, "language": lang, "format": "json"})
        except Exception:
            continue
        for r in d.get("results") or []:
            if r.get("latitude") is None or r.get("longitude") is None:
                continue
            key = (round(r["latitude"], 3), round(r["longitude"], 3))
            nm = r.get("name")
            if key not in cands:
                cands[key] = {"r": r, "names": {nm} if nm else set(),
                              "pop": r.get("population") or 0}
            else:
                c = cands[key]
                if nm:
                    c["names"].add(nm)
                # keep the record carrying the larger population / richer label
                if (r.get("population") or 0) > c["pop"]:
                    c["r"] = r
                    c["pop"] = r.get("population") or 0
    if not cands:
        return None, []

    q = _norm(name)

    def exact(c):
        return any(_norm(n) == q for n in c["names"])

    def feat(c):
        return FEATURE_RANK.get(c["r"].get("feature_code"), 0)

    ranked = sorted(cands.values(), key=lambda c: (exact(c), c["pop"], feat(c)), reverse=True)
    chosen = ranked[pick] if pick is not None and 0 <= pick < len(ranked) else ranked[0]
    r = chosen["r"]
    out = {
        "name": r.get("name"),
        "admin1": r.get("admin1"),
        "country": r.get("country"),
        "country_code": r.get("country_code"),
        "latitude": r.get("latitude"),
        "longitude": r.get("longitude"),
        "timezone": r.get("timezone"),
        "population": r.get("population"),
        "exact_name_match": exact(chosen),
    }
    alts = [
        {
            "name": c["r"].get("name"),
            "admin1": c["r"].get("admin1"),
            "country": c["r"].get("country"),
            "population": c["r"].get("population"),
        }
        for c in ranked[:5]
    ]
    return out, alts


def forecast(lat, lon, tz):
    return get_json(
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                       "precipitation,weather_code,wind_speed_10m,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,sunrise,sunset",
            "timezone": tz,
            "forecast_days": 4,
        },
    )


def tone(cur, today, tomorrow):
    """Derive a tone label from the rules in SKILL.md §3a. Returns (label, reasons)."""
    reasons = []
    code = cur.get("weather_code")
    dcode = today.get("weather_code")
    rh = cur.get("relative_humidity_2m")
    temp = cur.get("temperature_2m")
    wind = cur.get("wind_speed_10m")
    pprob = today.get("precipitation_probability_max")
    tmax = today.get("temperature_2m_max")
    tmin = today.get("temperature_2m_min")

    wet = (code in RAIN_CODES) or (code in SNOW_CODES) or ((pprob or 0) >= 60)
    if wet:
        reasons.append(
            f"降水（当前 {WMO.get(code, code)}／今日 {WMO.get(dcode, dcode)}，降水概率 {pprob}%）"
        )
    if rh is not None and rh >= HUMID_HIGH:
        reasons.append(f"湿度 {rh}% ≥ {HUMID_HIGH}%")
    if wet or (rh is not None and rh >= HUMID_HIGH):
        base = "室内 · 低频 · 湿润感（城市夜景、雨、孤独但不丧）"
    elif code in CLEAR_CODES and tmax is not None and WARM_HOT_MIN <= tmax <= WARM_HOT_MAX:
        base = "轻快 · 明亮 · 公路"
        reasons.append(f"晴，日最高 {tmax}℃ 落在 {WARM_HOT_MIN}-{WARM_HOT_MAX}℃")
    else:
        base = "中性 · 留白（天气信号不强，交给星期与时间决定）"
        reasons.append(
            f"无强天气信号（当前 {WMO.get(code, code)}／今日 {WMO.get(dcode, dcode)}，"
            f"湿度 {rh}%，日最高 {tmax}℃）"
        )

    if (tmin is not None and tmin < COLD_MAX) or (wind is not None and wind >= WINDY_KMH):
        extra = []
        if tmin is not None and tmin < COLD_MAX:
            extra.append(f"最低 {tmin}℃ < {COLD_MAX}℃")
        if wind is not None and wind >= WINDY_KMH:
            extra.append(f"风速 {wind}km/h ≥ {WINDY_KMH}")
        reasons.extend(extra)
        base += " + 温暖厚实（低频贝斯、木吉他）"

    # tomorrow swing
    if tmax is not None and tomorrow.get("temperature_2m_max") is not None:
        delta = round(tomorrow["temperature_2m_max"] - tmax, 1)
        if abs(delta) >= 4:
            reasons.append(f"明日最高温变化 {delta:+.1f}℃（≥4℃ 记一次换季感）")

    return base, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("city", nargs="?", help="city name, e.g. 北京 / Tokyo")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--tz", default=None)
    ap.add_argument("--pick", type=int, default=None, help="pick the Nth geocoding candidate (0-based)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    alts = []
    loc = None
    if a.lat is not None and a.lon is not None:
        loc = {"name": a.city or f"{a.lat},{a.lon}", "country_code": None,
               "latitude": a.lat, "longitude": a.lon, "timezone": a.tz or "auto"}
    else:
        if not a.city:
            print("need a city name or --lat/--lon", file=sys.stderr)
            return 1
        loc, alts = geocode(a.city, a.pick)
        if not loc:
            print(f"geocoding failed for {a.city!r} — check the spelling; "
                  f"do NOT fall back to guessing coordinates", file=sys.stderr)
            return 1
        if a.tz:
            loc["timezone"] = a.tz

    try:
        fc = forecast(loc["latitude"], loc["longitude"], loc["timezone"])
    except Exception as e:
        print(f"forecast request failed: {e}", file=sys.stderr)
        return 2

    cur = fc.get("current") or {}
    daily = fc.get("daily") or {}
    days = daily.get("time") or []

    def day(i):
        if i >= len(days):
            return {}
        return {
            "date": days[i],
            "weather_code": (daily.get("weather_code") or [None])[i],
            "temperature_2m_max": (daily.get("temperature_2m_max") or [None])[i],
            "temperature_2m_min": (daily.get("temperature_2m_min") or [None])[i],
            "precipitation_probability_max": (daily.get("precipitation_probability_max") or [None])[i],
        }

    today, tomorrow = day(0), day(1)
    label, reasons = tone(cur, today, tomorrow)

    import datetime as _dt
    local = cur.get("time") or (days[0] if days else None)
    weekday = None
    if local:
        try:
            weekday = _dt.date.fromisoformat(local[:10]).strftime("%A")
        except Exception:
            weekday = None

    out = {
        "location": loc,
        "local_time": local,
        "weekday": weekday,
        "current": {
            "temperature_c": cur.get("temperature_2m"),
            "apparent_c": cur.get("apparent_temperature"),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "precipitation_mm": cur.get("precipitation"),
            "weather_code": cur.get("weather_code"),
            "weather": WMO.get(cur.get("weather_code")),
            "wind_kmh": cur.get("wind_speed_10m"),
            "is_day": cur.get("is_day"),
        },
        "today": {**today, "weather": WMO.get(today.get("weather_code"))},
        "tomorrow": {**tomorrow, "weather": WMO.get(tomorrow.get("weather_code"))},
        "tone": label,
        "tone_reasons": reasons,
        "geocode_candidates": alts,
        # 节气 / 法定节假日 are China-specific. Flag it so the caller does not
        # apply a Chinese holiday table to a foreign city.
        "cn_holiday_table_applicable": loc.get("country_code") == "CN",
    }

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print(f"地点     : {loc['name']} {loc.get('admin1') or ''} {loc.get('country') or ''}".rstrip())
    print(f"坐标/时区: {loc['latitude']}, {loc['longitude']} / {loc['timezone']}")
    if loc.get("exact_name_match") is False:
        print("⚠ 警告   : 没有精确同名城市，已按人口取最近似项，务必核对下面的候选")
    print(f"当地时间 : {local}（{weekday}）")
    c = out["current"]
    print(f"当前     : {c['temperature_c']}℃（体感 {c['apparent_c']}℃）{c['weather']} 湿度 {c['humidity_pct']}% 风 {c['wind_kmh']}km/h")
    t, tm = out["today"], out["tomorrow"]
    print(f"今日     : {t.get('weather')} {t.get('temperature_2m_max')}/{t.get('temperature_2m_min')}℃ 降水概率 {t.get('precipitation_probability_max')}%")
    print(f"明日     : {tm.get('weather')} {tm.get('temperature_2m_max')}/{tm.get('temperature_2m_min')}℃ 降水概率 {tm.get('precipitation_probability_max')}%")
    print(f"基调     : {label}")
    print("依据     :")
    for r in reasons:
        print(f"  - {r}")
    # Only dump the candidate list when the pick is genuinely doubtful.
    # A clean hit like a 20M+ megacity should stay quiet; a 13k-population
    # "朝阳" should not, because that is usually a wrong pick.
    chosen_pop = loc.get("population") or 0
    rival_pop = max([(x.get("population") or 0) for x in alts[1:]] or [0])
    doubtful = (
        (not loc.get("exact_name_match"))
        or chosen_pop < SMALL_CITY_POP
        or (rival_pop and rival_pop >= chosen_pop * 0.5)
    )
    if doubtful and len(alts) > 1:
        if chosen_pop and chosen_pop < SMALL_CITY_POP:
            print(f"⚠ 警告   : 选中地点人口仅 {chosen_pop:,}，很可能不是你要的城市")
        print("候选     :（若上面地点不对，用 --pick N 指定）")
        for i, x in enumerate(alts):
            pop = f" 人口 {x['population']:,}" if x.get("population") else ""
            mark = " ←" if (x["name"] == loc["name"] and x.get("admin1") == loc.get("admin1")) else ""
            print(f"  [{i}] {x['name']} / {x.get('admin1') or '-'} / {x.get('country') or '-'}{pop}{mark}")
    if not out["cn_holiday_table_applicable"]:
        print("注意     : 非中国城市，节气表与中国法定节假日表不适用，需另建假期表")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
