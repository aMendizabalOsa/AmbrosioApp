"""
Parte de olas (Open-Meteo Marine) y mareas (Storm Glass) para un municipio costero.
Las horas siempre se devuelven en la timezone configurada (Europe/Madrid).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time as dt_time
from zoneinfo import ZoneInfo

import requests

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

_GEO_URL    = "https://geocoding-api.open-meteo.com/v1/search"
_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
_TIDES_URL  = "https://api.stormglass.io/v2/tide/extremes/point"

_COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSO","SO","OSO","O","ONO","NO","NNO"]

# Franjas horarias a mostrar en el parte de olas
_WAVE_SLOTS = {6, 8, 10, 12, 14, 16, 18, 20}


def _compass(deg: float | None) -> str:
    if deg is None:
        return "—"
    return _COMPASS[round(deg / 22.5) % 16]


class MarineAgent(BaseAgent):
    """
    Devuelve el parte de olas y las horas de pleamar/bajamar.

    Args (run):
        location:    nombre del municipio (default: configurado en el agent)
        target_date: fecha ISO-8601 YYYY-MM-DD (default: hoy en la timezone local)
    """

    def __init__(
        self,
        stormglass_api_key: str,
        default_location: str = "Zarautz",
        timezone: str = "Europe/Madrid",
    ) -> None:
        self._sg_key = stormglass_api_key
        self._default_location = default_location
        self._tz = ZoneInfo(timezone)
        self._tz_name = timezone
        # Caché de geocoding (nombre → (lat, lon)) y mareas ((lat,lon,date) → list)
        self._geo_cache: dict[str, tuple[float, float]] = {}
        self._tide_cache: dict[tuple, list] = {}

    def run(
        self,
        location: str | None = None,
        target_date: str | None = None,
    ) -> dict:
        loc_name = (location or self._default_location).strip()
        lat, lon, display_name = self._geocode(loc_name)

        if target_date:
            d = date.fromisoformat(target_date)
        else:
            d = datetime.now(self._tz).date()

        waves = self._get_waves(lat, lon, d)
        tides = self._get_tides(lat, lon, d)

        return {
            "location": display_name,
            "date": d.isoformat(),
            "waves": waves,
            "tides": tides,
            "tides_available": tides is not None,
        }

    # ------------------------------------------------------------------

    def _geocode(self, location: str) -> tuple[float, float, str]:
        key = location.lower()
        if key in self._geo_cache:
            lat, lon = self._geo_cache[key]
            return lat, lon, location

        resp = requests.get(
            _GEO_URL,
            params={"name": location, "count": 1, "language": "es"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results")
        if not results:
            raise ValueError(f"No se encontró la ubicación '{location}'.")
        r = results[0]
        lat, lon = r["latitude"], r["longitude"]
        display = r.get("name", location)
        self._geo_cache[key] = (lat, lon)
        return lat, lon, display

    def _get_waves(self, lat: float, lon: float, d: date) -> list[dict]:
        resp = requests.get(
            _MARINE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": (
                    "wave_height,wave_direction,wave_period,"
                    "swell_wave_height,swell_wave_direction,swell_wave_period,"
                    "wind_wave_height"
                ),
                "timezone": self._tz_name,
                "start_date": d.isoformat(),
                "end_date": d.isoformat(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        h = resp.json().get("hourly", {})
        times = h.get("time", [])

        slots = []
        for i, t in enumerate(times):
            hour = int(t[11:13])
            if hour not in _WAVE_SLOTS:
                continue
            slots.append({
                "hour": hour,
                "wave_height":    round(h["wave_height"][i] or 0, 1),
                "wave_direction": _compass(h["wave_direction"][i]),
                "wave_period":    round(h["wave_period"][i] or 0, 1),
                "swell_height":   round(h["swell_wave_height"][i] or 0, 1),
                "swell_direction":_compass(h["swell_wave_direction"][i]),
                "swell_period":   round(h["swell_wave_period"][i] or 0, 1),
                "wind_wave":      round(h["wind_wave_height"][i] or 0, 1),
            })
        return slots

    def _get_tides(self, lat: float, lon: float, d: date) -> list[dict] | None:
        if not self._sg_key:
            return None

        cache_key = (round(lat, 3), round(lon, 3), d.isoformat())
        if cache_key in self._tide_cache:
            return self._tide_cache[cache_key]

        start = datetime.combine(d, dt_time.min, tzinfo=self._tz)
        end   = datetime.combine(d, dt_time.max, tzinfo=self._tz)

        try:
            resp = requests.get(
                _TIDES_URL,
                params={"lat": lat, "lng": lon,
                        "start": start.isoformat(), "end": end.isoformat()},
                headers={"Authorization": self._sg_key},
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Storm Glass error: %s", exc)
            return None

        tides = []
        for item in resp.json().get("data", []):
            dt_local = datetime.fromisoformat(item["time"]).astimezone(self._tz)
            tides.append({
                "time":   dt_local.strftime("%H:%M"),
                "type":   item["type"],          # "high" | "low"
                "height": round(item.get("height") or 0, 2),
            })

        self._tide_cache[cache_key] = tides
        return tides
