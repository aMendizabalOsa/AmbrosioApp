"""
Agente de Tado: temperatura, humedad y estado de la calefacción.
Soporta consulta actual e histórica (por fecha y hora pasada).

Auth: OAuth2 device flow. Requiere haber ejecutado tado_setup.py una vez.
API: https://my.tado.com/api/v2
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

_TOKEN_URL  = "https://login.tado.com/oauth2/token"
_API_BASE   = "https://my.tado.com/api/v2"
_CLIENT_ID  = "1bb50063-6b0c-4d11-bd99-387f4a91cc46"
_TOKEN_FILE = Path("credentials/tado_token.json")
_UTC        = ZoneInfo("UTC")


class TadoAgent(BaseAgent):
    """
    Consulta temperatura, humedad y calefacción vía Tado API v2.

    Args (run):
        target_datetime: ISO-8601 opcional. Si se omite → estado actual.
                         Si se proporciona → datos históricos del dayReport.
    """

    def __init__(
        self,
        timezone: str = "Europe/Madrid",
    ) -> None:
        self._tz = ZoneInfo(timezone)
        self._token:         str | None      = None
        self._token_expiry:  datetime | None = None
        self._home_id:  int | None = None
        self._zone_id:  int | None = None
        self._zone_name: str = "Casa"

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def run(self, target_datetime: str | None = None) -> dict:
        self._ensure_auth()
        self._ensure_home_zone()

        if target_datetime:
            dt = datetime.fromisoformat(target_datetime)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self._tz)
            return self._get_historical(dt)
        return self._get_current()

    # ------------------------------------------------------------------
    # Auth y descubrimiento
    # ------------------------------------------------------------------

    def _ensure_auth(self) -> None:
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return
        if not _TOKEN_FILE.exists():
            raise RuntimeError(
                "No hay token de Tado. Ejecuta primero: python tado_setup.py"
            )
        saved = json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
        refresh_token = saved.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("Token de Tado inválido. Ejecuta: python tado_setup.py")

        resp = requests.post(
            _TOKEN_URL,
            data={
                "grant_type":    "refresh_token",
                "client_id":     _CLIENT_ID,
                "refresh_token": refresh_token,
            },
            timeout=10,
        )
        if not resp.ok:
            raise RuntimeError(f"Tado token refresh {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = datetime.now() + timedelta(seconds=data.get("expires_in", 600) - 30)
        if "refresh_token" in data:
            _TOKEN_FILE.write_text(
                json.dumps({"refresh_token": data["refresh_token"]}, indent=2),
                encoding="utf-8",
            )

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, **params) -> dict:
        resp = requests.get(
            f"{_API_BASE}{path}", headers=self._headers(), params=params, timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    def _ensure_home_zone(self) -> None:
        if self._home_id and self._zone_id:
            return
        me = self._get("/me")
        self._home_id = me["homes"][0]["id"]
        zones = self._get(f"/homes/{self._home_id}/zones")
        heating = [z for z in zones if z.get("type") == "HEATING"]
        if not heating:
            raise RuntimeError("No se encontró ninguna zona de calefacción en Tado.")
        self._zone_id   = heating[0]["id"]
        self._zone_name = heating[0].get("name", "Casa")

    # ------------------------------------------------------------------
    # Estado actual
    # ------------------------------------------------------------------

    def _get_current(self) -> dict:
        state = self._get(f"/homes/{self._home_id}/zones/{self._zone_id}/state")

        sensor   = state.get("sensorDataPoints", {})
        activity = state.get("activityDataPoints", {})
        setting  = state.get("setting", {})
        overlay  = state.get("overlay")

        temp     = _val(sensor.get("insideTemperature"), "celsius")
        humidity = _val(sensor.get("humidity"), "percentage")
        heat_pct = _val(activity.get("heatingPower"), "percentage") or 0

        # Setpoint: overlay manual tiene prioridad sobre el horario programado
        setpoint, manual = None, False
        if overlay:
            ov_s = overlay.get("setting", {})
            if ov_s.get("power") == "ON":
                setpoint = _val(ov_s.get("temperature"), "celsius")
                manual   = True
        if setpoint is None and setting.get("power") == "ON":
            setpoint = _val(setting.get("temperature"), "celsius")

        heating_on = setting.get("power") == "ON" and heat_pct > 0

        return {
            "mode":           "current",
            "zone":           self._zone_name,
            "temperature":    _r1(temp),
            "humidity":       _r1(humidity),
            "heating_on":     heating_on,
            "heating_power":  round(heat_pct),
            "setpoint":       _r1(setpoint),
            "manual_override": manual,
        }

    # ------------------------------------------------------------------
    # Datos históricos (dayReport)
    # ------------------------------------------------------------------

    def _get_historical(self, dt: datetime) -> dict:
        date_str = dt.astimezone(self._tz).strftime("%Y-%m-%d")
        report = self._get(
            f"/homes/{self._home_id}/zones/{self._zone_id}/dayReport",
            date=date_str,
        )
        dt_utc = dt.astimezone(_UTC)
        measured = report.get("measuredData", {})

        temp = _nearest_timestamp(
            measured.get("insideTemperature", {}).get("dataPoints", []),
            dt_utc, "celsius",
        )
        humidity = _nearest_timestamp(
            measured.get("humidity", {}).get("dataPoints", []),
            dt_utc, "percentage",
        )
        heat_pct = _find_in_intervals(
            report.get("callForHeat", {}).get("dataPoints", []),
            dt_utc,
        )
        # callForHeat value is in [0,1] → convert to %
        if heat_pct is not None:
            heat_pct = heat_pct * 100

        setpoint, heating_on = _find_setpoint(
            report.get("settings", {}).get("dataIntervals", []),
            dt_utc,
        )

        return {
            "mode":          "historical",
            "zone":          self._zone_name,
            "datetime":      dt.astimezone(self._tz).strftime("%d/%m/%Y %H:%M"),
            "temperature":   _r1(temp),
            "humidity":      _r1(humidity),
            "heating_on":    heating_on,
            "heating_power": round(heat_pct) if heat_pct is not None else None,
            "setpoint":      _r1(setpoint),
        }


# ------------------------------------------------------------------
# Helpers de extracción (fuera de la clase para claridad)
# ------------------------------------------------------------------

def _val(obj: dict | None, key: str) -> float | None:
    if not obj:
        return None
    v = obj.get(key)
    return float(v) if v is not None else None


def _r1(v: float | None) -> float | None:
    return round(v, 1) if v is not None else None


def _nearest_timestamp(
    datapoints: list, target: datetime, key: str
) -> float | None:
    best: float | None = None
    best_delta: float | None = None
    for dp in datapoints:
        ts_str = dp.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str).astimezone(_UTC)
        delta = abs((ts - target).total_seconds())
        if best_delta is None or delta < best_delta:
            v = dp.get("value")
            extracted = v.get(key) if isinstance(v, dict) else None
            if extracted is not None:
                best_delta = delta
                best = float(extracted)
    return best


def _find_in_intervals(datapoints: list, target: datetime) -> float | None:
    """Busca el valor de un intervalo from/to que contenga target."""
    for dp in datapoints:
        t_from = dp.get("from") or dp.get("timestamp")
        t_to   = dp.get("to")
        if not t_from:
            continue
        dt_from = datetime.fromisoformat(t_from).astimezone(_UTC)
        if t_to:
            dt_to = datetime.fromisoformat(t_to).astimezone(_UTC)
            if dt_from <= target < dt_to:
                v = dp.get("value")
                return float(v) if v is not None else None
    return None


def _find_setpoint(intervals: list, target: datetime) -> tuple[float | None, bool]:
    """Devuelve (setpoint, heating_on) para el intervalo que contiene target."""
    for interval in intervals:
        dt_from = datetime.fromisoformat(interval["from"]).astimezone(_UTC)
        dt_to   = datetime.fromisoformat(interval["to"]).astimezone(_UTC)
        if dt_from <= target < dt_to:
            val = interval.get("value", {})
            if val.get("power") == "ON":
                t = val.get("temperature") or {}
                return _r1(t.get("celsius")), True
            return None, False
    return None, False
