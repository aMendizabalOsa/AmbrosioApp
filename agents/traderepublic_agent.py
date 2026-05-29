"""
Agente de Trade Republic: consulta el portfolio de inversiones.

Auth: weblogin con cookies. Requiere haber ejecutado tr_setup.py una vez.
Las credenciales (phone/PIN) se guardan en credentials/tr_credentials.
Las cookies de sesión en credentials/tr_cookies.{phone}.txt.

Nota: pytr.Portfolio filtra silenciosamente activos sin exchangeIds (p.ej. crypto
directo). Este agente implementa su propio loop para conservarlos con precio estimado.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .base_agent import BaseAgent

_COOKIES_DIR = Path("credentials")

def _credentials_file(owner: str) -> Path:
    new = Path(f"credentials/tr_credentials_{owner}")
    if not new.exists() and owner == "anjel":
        old = Path("credentials/tr_credentials")
        if old.exists():
            return old
    return new
_TICKER_TIMEOUT   = 8.0   # segundos esperando cada ráfaga de tickers
_DETAIL_TIMEOUT   = 10.0  # segundos esperando instrument_details

logger = logging.getLogger(__name__)


class TradeRepublicAgent(BaseAgent):
    """
    Consulta el portfolio de Trade Republic vía pytr.

    Credenciales en credentials/tr_credentials_{owner} (o tr_credentials para Anjel
    como fallback). Cookies en credentials/tr_cookies.{phone}.txt.
    """

    def __init__(self, owner: str = "anjel") -> None:
        self._owner = owner.lower()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """Ejecuta en un event loop nuevo (seguro desde asyncio.to_thread)."""
        return asyncio.run(self._async_run())

    def check_session(self) -> bool:
        """Devuelve True si la sesión guardada sigue siendo válida."""
        try:
            tr = self._make_api()
            return bool(tr.resume_websession())
        except Exception:
            return False

    def initiate_relogin(self) -> tuple:
        """
        Inicia el flujo de re-login: abre Playwright en background para obtener
        el token WAF y envía el OTP al teléfono.
        Devuelve (tr_instance, countdown_segundos). Puede tardar 15-30 s.
        """
        tr = self._make_api(waf_token="playwright")
        countdown = tr.initiate_weblogin()
        return tr, int(countdown)

    @staticmethod
    def complete_relogin(tr_instance, code: str) -> None:
        """Completa el re-login con el código OTP recibido por SMS."""
        tr_instance.complete_weblogin(code)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _make_api(self, waf_token: str | None = None):
        try:
            from pytr.api import TradeRepublicApi
        except ImportError:
            raise RuntimeError("pytr no está instalado. Ejecuta: pip install pytr")

        creds_file = _credentials_file(self._owner)
        if not creds_file.exists():
            raise RuntimeError(
                f"No hay credenciales de Trade Republic para '{self._owner}'. "
                "Ejecuta: python tr_setup.py"
            )

        with open(creds_file) as f:
            lines = f.readlines()
        phone        = lines[0].strip()
        cookies_file = _COOKIES_DIR / f"tr_cookies.{phone}.txt"

        kwargs: dict = dict(
            save_cookies=True,
            credentials_file=str(creds_file),
            cookies_file=str(cookies_file),
        )
        if waf_token:
            kwargs["waf_token"] = waf_token
        return TradeRepublicApi(**kwargs)

    async def _async_run(self) -> dict:
        tr = self._make_api()

        if not tr.resume_websession():
            raise RuntimeError(
                f"Sesión de Trade Republic caducada para '{self._owner}'. "
                "Envía /tr_login para renovarla sin salir del chat."
            )

        return await _fetch_portfolio(tr)


# ---------------------------------------------------------------------------
# Loop propio — evita que pytr descarte activos sin exchangeIds (crypto)
# ---------------------------------------------------------------------------

async def _fetch_portfolio(tr) -> dict:
    # 1. Suscribir compactPortfolio + cash en paralelo
    await tr.compact_portfolio()
    await tr.cash()

    raw_positions: list[dict] = []
    cash_list:     list[dict] = []
    pending = 2

    while pending:
        sid, sub, resp = await tr.recv()
        await tr.unsubscribe(sid)
        if sub["type"] == "compactPortfolio":
            raw_positions = resp.get("positions", [])
            pending -= 1
        elif sub["type"] == "cash":
            cash_list = resp if isinstance(resp, list) else [resp]
            pending -= 1

    # 2. Obtener nombre y exchangeIds de cada posición
    detail_subs: dict[str, dict] = {}
    for pos in raw_positions:
        sid = await tr.instrument_details(pos["instrumentId"])
        detail_subs[sid] = pos

    while detail_subs:
        try:
            sid, sub, resp = await asyncio.wait_for(tr.recv(), _DETAIL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Timeout en instrument_details; %d activos sin nombre", len(detail_subs))
            break
        await tr.unsubscribe(sid)
        if sub["type"] == "instrument":
            pos = detail_subs.pop(sid, None)
            if pos is not None:
                pos["_name"]        = resp.get("shortName") or resp.get("name") or pos["instrumentId"]
                pos["_exchangeIds"] = resp.get("exchangeIds") or []
                pos["_type"]        = resp.get("typeId", "")

    # Rellenar defaults para los que hayan sufrido timeout
    for pos in raw_positions:
        pos.setdefault("_name",        pos["instrumentId"])
        pos.setdefault("_exchangeIds", [])
        pos.setdefault("_type",        "")

    # 3. Suscribir tickers solo para posiciones con exchange conocido
    ticker_subs: dict[str, dict] = {}
    for pos in raw_positions:
        exchange_ids = pos["_exchangeIds"]
        if exchange_ids:
            sid = await tr.ticker(pos["instrumentId"], exchange=exchange_ids[0])
            ticker_subs[sid] = pos
        else:
            # Crypto u otros activos sin mercado convencional — sin ticker
            logger.info(
                "Activo sin exchangeIds: %s (%s) — se usará precio estimado",
                pos["_name"], pos["instrumentId"],
            )

    while ticker_subs:
        try:
            sid, sub, resp = await asyncio.wait_for(tr.recv(), _TICKER_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Timeout esperando tickers; %d activos sin precio real", len(ticker_subs))
            break
        await tr.unsubscribe(sid)
        if sub["type"] == "ticker":
            pos = ticker_subs.pop(sid, None)
            if pos is not None:
                last = resp.get("last") or {}
                pos["_price"] = last.get("price")

    await tr.close()
    return _build_result(raw_positions, cash_list)


def _build_result(raw_positions: list[dict], cash_list: list[dict]) -> dict:
    q2 = Decimal("0.01")

    # Efectivo
    cash_amount = Decimal("0")
    if cash_list:
        entry = cash_list[0] if isinstance(cash_list[0], dict) else {}
        cash_amount = Decimal(str(entry.get("amount", 0)))

    positions: list[dict] = []
    total_invested = Decimal("0")
    total_value    = Decimal("0")

    for pos in raw_positions:
        net_size = Decimal(str(pos.get("netSize", 0)))
        avg_buy  = Decimal(str(pos.get("averageBuyIn", 0)))

        raw_price = pos.get("_price")
        if raw_price is not None:
            price     = Decimal(str(raw_price))
            estimated = False
        else:
            # Sin ticker (crypto directo, etc.) → precio estimado = coste medio
            price     = avg_buy
            estimated = True

        net_val  = (price * net_size).quantize(q2, rounding=ROUND_HALF_UP)
        buy_cost = (avg_buy * net_size).quantize(q2, rounding=ROUND_HALF_UP)
        pnl      = net_val - buy_cost
        pnl_pct  = float(((net_val / buy_cost) - 1) * 100) if buy_cost else 0.0

        total_invested += buy_cost
        total_value    += net_val

        positions.append({
            "name":      pos["_name"],
            "isin":      pos["instrumentId"],
            "shares":    float(net_size),
            "price":     float(price),
            "avg_buy":   float(avg_buy),
            "value":     float(net_val),
            "pnl":       float(pnl),
            "pnl_pct":   round(pnl_pct, 1),
            "estimated": estimated,
        })

    positions.sort(key=lambda p: p["value"], reverse=True)

    total_pnl     = total_value - total_invested
    total_pnl_pct = float(((total_value / total_invested) - 1) * 100) if total_invested else 0.0

    return {
        "positions":      positions,
        "cash":           float(cash_amount),
        "total_value":    float(total_value + cash_amount),
        "total_invested": float(total_invested),
        "total_pnl":      float(total_pnl),
        "total_pnl_pct":  round(total_pnl_pct, 1),
    }
