"""
Agente de MyInvestor usando la API moderna (api.myinvestor.es).

No requiere Playwright — autenticación directa con Bearer token.
Ejecutar mi_setup.py una vez para guardar las credenciales iniciales.
El agente refresca el token automáticamente mientras el refresh_token sea válido (~30 días).

Requiere: pip install httpx
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

from .base_agent import BaseAgent

# ---------------------------------------------------------------------------
# HTTP helper — usa curl_cffi (Chrome TLS fingerprint) o httpx como fallback
# ---------------------------------------------------------------------------

def _post(url: str, body: dict, headers: dict) -> object:
    """
    POST con huella TLS de Chrome (curl_cffi) para evitar bloqueos WAF/captcha.
    Fallback a httpx si curl_cffi no está disponible.
    """
    try:
        from curl_cffi import requests as _cffi
        return _cffi.post(
            url, json=body, headers=headers,
            timeout=_TIMEOUT, impersonate="chrome120",
        )
    except ImportError:
        return httpx.post(url, json=body, headers=headers, timeout=_TIMEOUT)

def _creds_file(owner: str) -> Path:
    new = Path(f"credentials/mi_credentials_{owner}.json")
    if not new.exists() and owner == "anjel":
        old = Path("credentials/mi_credentials.json")
        if old.exists():
            return old
    return new
_API        = "https://api.myinvestor.es"
_TIMEOUT    = 20
_REFRESH_MARGIN = timedelta(hours=1)   # refrescar 1 h antes de que expire

logger = logging.getLogger(__name__)

_BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "User-Agent":   (
        "Mozilla/5.0 (Linux; Android 11; moto g(20)) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/95.0.4638.74 Mobile Safari/537.36"
    ),
    "Referer": _API,
    "Origin":  _API,
}


# ---------------------------------------------------------------------------
# Función pública de login (usada también en mi_setup.py)
# ---------------------------------------------------------------------------

def login_api(
    username: str,
    password: str,
    device_id: str,
    otp_code: str | None = None,
    otp_process_id: str | None = None,
) -> dict:
    """
    Autenticación directa contra api.myinvestor.es.

    Returns dict con una de estas claves:
      code="OK"           → access_token, refresh_token, refresh_ttl
      code="OTP_REQUIRED" → process_id  (pasar en la segunda llamada)
      code="ERROR"        → message
    """
    headers = {
        **_BASE_HEADERS,
        "x-device-id":        device_id,
        "x-myinvestor-app":   "version=3.117.0,platform=web",
    }

    body: dict = {
        "customerId":  username,
        "accessType":  "USERNAME",
        "password":    password,
        "deviceId":    device_id,
        "platform":    "BROWSER",
    }

    if otp_code and otp_process_id:
        otp_id, sig_id = otp_process_id.split("|", 1)
        body["otpId"]               = otp_id
        body["signatureRequestId"]  = sig_id
        body["code"]                = otp_code

    try:
        resp = _post(f"{_API}/login/api/v1/auth/token", body, headers)
    except Exception as exc:
        return {"code": "ERROR", "message": f"Error de red: {exc}"}

    if resp.status_code == 202:
        data = resp.json().get("payload", {}).get("data", {})
        otp_id  = data.get("otpId", "")
        sig_id  = data.get("signatureRequestId", "")
        return {"code": "OTP_REQUIRED", "process_id": f"{otp_id}|{sig_id}"}

    if resp.status_code in (200, 201):
        data = resp.json().get("payload", {}).get("data", {})
        return {
            "code":          "OK",
            "access_token":  data.get("accessToken", ""),
            "refresh_token": data.get("refreshToken", ""),
            "refresh_ttl":   int(data.get("refreshExpiresIn", 86400 * 30)),
        }

    if resp.status_code == 400:
        return {"code": "ERROR", "message": "Credenciales incorrectas"}

    if resp.status_code == 403:
        err = resp.json().get("status", {})
        return {"code": "ERROR", "message": f"Acceso denegado ({err.get('code','')}): {err.get('message','')}"}

    return {"code": "ERROR", "message": f"Respuesta inesperada HTTP {resp.status_code}"}


# ---------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------

class MyInvestorAgent(BaseAgent):
    """
    Accede a MyInvestor usando la API REST moderna.
    Requiere mi_setup.py ejecutado al menos una vez.
    """

    def __init__(self, owner: str = "anjel") -> None:
        self._owner = owner.lower()
        self._creds_file = _creds_file(self._owner)
        self._client: httpx.Client | None = None

    def run(self) -> dict:
        """Compatibilidad con el dispatcher anterior: cuentas + portfolio fondos/ETFs."""
        self._ensure_authenticated()
        return {
            "checking":  self._get_checking_accounts(),
            "portfolio": self._get_portfolio_summary(),
        }

    def check_session(self) -> bool | None:
        """
        Intenta autenticarse proactivamente.
        Devuelve True si OK, False si falla irrecuperablemente, None si no hay credenciales.
        """
        if not self._creds_file.exists():
            return None
        try:
            self._ensure_authenticated()
            return True
        except RuntimeError as exc:
            if "OTP" in str(exc):
                raise
            return False

    def get_full_analysis(self) -> dict:
        """Análisis completo: cuentas, fondos, ETFs, depósitos, créditos."""
        self._ensure_authenticated()
        investments = self._get_investments()
        return {
            "checking":    self._get_checking_accounts(),
            "funds":       investments["funds"],
            "stocks":      investments["stocks"],
            "deposits":    self._get_deposits(),
            "credits":     self._get_credits(),
        }

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def _ensure_authenticated(self) -> None:
        if not self._creds_file.exists():
            raise RuntimeError(
                f"No hay credenciales de MyInvestor para '{self._owner}'. "
                "Ejecuta: python mi_setup.py"
            )

        creds = json.loads(self._creds_file.read_text(encoding="utf-8"))
        access_token  = creds.get("access_token", "")
        refresh_token = creds.get("refresh_token", "")

        # 1 — probar el access token guardado
        if access_token:
            self._setup_client(access_token)
            if self._probe():
                return

        # 2 — intentar refrescar con el refresh token
        if refresh_token:
            logger.info("MyInvestor: refrescando token…")
            if self._do_refresh(refresh_token, creds):
                return

        # 3 — re-login silencioso con usuario/contraseña
        username  = creds.get("username", "")
        password  = creds.get("password", "")
        device_id = creds.get("device_id") or str(uuid4())

        if not username or not password:
            raise RuntimeError(
                f"No hay credenciales de MyInvestor para '{self._owner}'. "
                "Ejecuta: python mi_setup.py"
            )

        logger.info("MyInvestor: re-autenticando con usuario/contraseña…")
        result = login_api(username, password, device_id)

        if result["code"] == "OTP_REQUIRED":
            raise RuntimeError(
                "MyInvestor requiere código OTP para iniciar sesión. "
                "Ejecuta: python mi_setup.py"
            )
        if result["code"] != "OK":
            raise RuntimeError(
                f"Login MyInvestor fallido: {result.get('message')}"
            )

        self._save_tokens(creds, result)
        self._setup_client(result["access_token"])

    def _setup_client(self, access_token: str) -> None:
        self._client = httpx.Client(
            headers={**_BASE_HEADERS, "Authorization": f"Bearer {access_token}"},
            timeout=_TIMEOUT,
        )

    def _probe(self) -> bool:
        """Devuelve True si el cliente actual tiene un token válido."""
        try:
            r = self._client.get(
                f"{_API}/cperf-server/api/v2/cash-accounts/self", timeout=10
            )
            return r.status_code != 401
        except Exception:
            return False

    def _do_refresh(self, refresh_token: str, creds: dict) -> bool:
        try:
            resp = _post(
                f"{_API}/login/api/v1/auth/token/refresh",
                {"refreshToken": refresh_token},
                _BASE_HEADERS,
            )
            if resp.status_code >= 400:
                return False
            data = resp.json().get("payload", {}).get("data", {})
            if not data.get("accessToken"):
                return False
            result = {
                "access_token":  data["accessToken"],
                "refresh_token": data.get("refreshToken", refresh_token),
                "refresh_ttl":   int(data.get("refreshExpiresIn", 86400 * 30)),
            }
            self._save_tokens(creds, result)
            self._setup_client(result["access_token"])
            return True
        except Exception as exc:
            logger.warning("Refresh token fallido: %s", exc)
            return False

    def _save_tokens(self, creds: dict, result: dict) -> None:
        creds["access_token"]      = result["access_token"]
        creds["refresh_token"]     = result["refresh_token"]
        creds["refresh_expires_at"] = (
            datetime.now() + timedelta(seconds=result["refresh_ttl"])
        ).isoformat()
        creds["token_saved_at"] = datetime.now().isoformat()
        self._creds_file.write_text(
            json.dumps(creds, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Llamadas HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str) -> object:
        resp = self._client.get(f"{_API}{path}")
        if resp.status_code == 401:
            raise RuntimeError("Token rechazado (401). Ejecuta: python mi_setup.py")
        resp.raise_for_status()
        body = resp.json()
        return body.get("payload", {}).get("data", body)

    # ------------------------------------------------------------------
    # Cuentas corrientes
    # ------------------------------------------------------------------

    def _get_checking_accounts(self) -> list[dict]:
        accounts = self._get("/cperf-server/api/v2/cash-accounts/self")
        result = []
        for acc in (accounts if isinstance(accounts, list) else []):
            if acc.get("status") != "ACTIVE":
                continue
            if not any(h.get("me") for h in acc.get("holders", [])):
                continue
            result.append({
                "iban":     acc.get("iban", ""),
                "alias":    acc.get("alias", ""),
                "type":     acc.get("accountType", ""),
                "balance":  float(acc.get("enabledBalance") or 0),
                "withheld": float(acc.get("withheldBalance") or 0),
                "currency": acc.get("currency", "EUR"),
            })
        return result

    # ------------------------------------------------------------------
    # Fondos e ETFs / Acciones
    # ------------------------------------------------------------------

    def _get_investments(self) -> dict:
        sec_accounts = self._get("/cperf-server/api/v2/securities-accounts/self-basic")
        funds: list[dict] = []
        stocks: list[dict] = []

        for sec_acc in (sec_accounts if isinstance(sec_accounts, list) else []):
            sec_id = sec_acc.get("accountId")
            if not sec_id:
                continue
            try:
                details = self._get(
                    f"/cperf-server/api/v2/securities-accounts/{sec_id}"
                )
                investments = (
                    details.get("securitiesAccountInvestments", {})
                    if isinstance(details, dict) else {}
                )

                # Fondos de inversión (indexados y tradicionales)
                for cat in ("FUND", "INDEXED_FUND"):
                    for fund in investments.get(cat, {}).get("investmentList", []):
                        shares = float(fund.get("shares") or 0)
                        price  = float(fund.get("originCurrencyLiquidationValue") or 0)
                        mv     = shares * price
                        ini    = float(fund.get("initialInvestmentCurrency") or 0)
                        funds.append({
                            "name":            fund.get("investmentName", "?"),
                            "isin":            fund.get("isin", ""),
                            "type":            "FUND",
                            "subtype":         cat,
                            "shares":          shares,
                            "price":           price,
                            "market_value":    mv,
                            "invested_amount": ini,
                            "pnl":             mv - ini,
                            "pnl_pct":         round(((mv / ini) - 1) * 100, 1) if ini else 0.0,
                            "currency":        fund.get("liquidationValueCurrency", "EUR"),
                        })

                # Acciones y ETFs (broker)
                for stock in investments.get("BROKER", {}).get("investmentList", []):
                    shares = float(stock.get("shares") or 0)
                    price  = float(stock.get("originCurrencyLiquidationValue") or 0)
                    mv     = shares * price
                    ini    = float(stock.get("initialInvestmentCurrency") or 0)
                    prod_type = (
                        "STOCK" if stock.get("brokerProductType") == "RV" else "ETF"
                    )
                    stocks.append({
                        "name":            stock.get("investmentName", "?"),
                        "isin":            stock.get("isin", ""),
                        "ticker":          stock.get("ticker", ""),
                        "type":            prod_type,
                        "shares":          shares,
                        "price":           price,
                        "market_value":    mv,
                        "invested_amount": ini,
                        "pnl":             mv - ini,
                        "pnl_pct":         round(((mv / ini) - 1) * 100, 1) if ini else 0.0,
                        "currency":        stock.get("liquidationValueCurrency", "EUR"),
                    })
            except Exception as exc:
                logger.warning("Error leyendo cuenta valores %s: %s", sec_id, exc)

        return {"funds": funds, "stocks": stocks}

    def _get_portfolio_summary(self) -> dict:
        """Portfolio resumido compatible con el dispatcher existente."""
        inv = self._get_investments()
        all_pos = inv["funds"] + inv["stocks"]
        total_mv  = sum(p["market_value"]    for p in all_pos)
        total_ini = sum(p["invested_amount"] for p in all_pos)
        pnl       = total_mv - total_ini
        pnl_pct   = round(((total_mv / total_ini) - 1) * 100, 1) if total_ini else 0.0
        all_pos.sort(key=lambda p: p["market_value"], reverse=True)
        return {
            "total_market_value": total_mv,
            "total_invested":     total_ini,
            "pnl":                pnl,
            "pnl_pct":            pnl_pct,
            "positions":          all_pos,
        }

    # ------------------------------------------------------------------
    # Depósitos
    # ------------------------------------------------------------------

    def _get_deposits(self) -> list[dict]:
        try:
            raw = self._get("/cperf-server/api/v2/deposits/self")
            result = []
            for d in (raw if isinstance(raw, list) else []):
                result.append({
                    "name":           d.get("depositName", "Depósito"),
                    "amount":         float(d.get("amount") or 0),
                    "interest_rate":  float(d.get("tae") or 0),
                    "gross_interest": float(d.get("grossInterest") or 0),
                    "maturity":       (d.get("expirationDate") or "")[:10],
                    "currency":       "EUR",
                })
            return result
        except Exception as exc:
            logger.warning("Error leyendo depósitos: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Créditos
    # ------------------------------------------------------------------

    def _get_credits(self) -> list[dict]:
        try:
            raw = self._get("/loan/api/v1/credit-accounts/self")
            result = []
            for c in (raw if isinstance(raw, list) else []):
                if not c.get("enabled"):
                    continue
                lending = c.get("lendingRequest", {})
                result.append({
                    "name":          c.get("alias", "Crédito"),
                    "credit_limit":  float(c.get("creditLimit") or 0),
                    "drawn_amount":  float(c.get("currentAmount") or 0),
                    "interest_rate": float(lending.get("tin") or 0) / 100,
                    "currency":      "EUR",
                })
            return result
        except Exception as exc:
            logger.warning("Error leyendo créditos: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Excepción (retrocompatibilidad con mi_setup.py antiguo)
# ---------------------------------------------------------------------------

class OTPRequiredError(Exception):
    def __init__(self, process_id: str):
        super().__init__("OTP requerido")
        self.process_id = process_id
