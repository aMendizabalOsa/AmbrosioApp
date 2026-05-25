"""
Setup de autenticación Tado (device flow, OAuth 2.0).

Ejecuta UNA vez para obtener y guardar el refresh_token:
    python tado_setup.py

El token se guarda en credentials/tado_token.json y es reutilizado
automáticamente por TadoAgent sin necesidad de credenciales de usuario.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

_CLIENT_ID        = "1bb50063-6b0c-4d11-bd99-387f4a91cc46"
_DEVICE_AUTH_URL  = "https://login.tado.com/oauth2/device_authorize"
_TOKEN_URL        = "https://login.tado.com/oauth2/token"
_TOKEN_FILE       = Path("credentials/tado_token.json")


def main() -> None:
    print("=== Tado Device Authorization ===\n")

    # 1. Solicitar device code
    resp = requests.post(
        _DEVICE_AUTH_URL,
        data={"client_id": _CLIENT_ID, "scope": "offline_access"},
        timeout=15,
    )
    if not resp.ok:
        print(f"Error al solicitar device code: {resp.status_code} {resp.text}")
        sys.exit(1)

    auth = resp.json()
    device_code      = auth["device_code"]
    user_code        = auth["user_code"]
    verification_uri = auth.get("verification_uri_complete") or auth.get("verification_uri")
    expires_in       = auth.get("expires_in", 300)
    interval         = auth.get("interval", 5)

    print(f"1. Abre este enlace en tu navegador:")
    print(f"   {verification_uri}\n")
    print(f"2. Si te pide código, introduce: {user_code}\n")
    print(f"Esperando aprobación ({expires_in}s disponibles)...\n")

    # 2. Polling hasta que el usuario apruebe
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        token_resp = requests.post(
            _TOKEN_URL,
            data={
                "client_id":   _CLIENT_ID,
                "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
            timeout=15,
        )
        data = token_resp.json()

        if token_resp.ok and "access_token" in data:
            _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_FILE.write_text(
                json.dumps({"refresh_token": data["refresh_token"]}, indent=2),
                encoding="utf-8",
            )
            print(f"✅ Autorización completada. Token guardado en {_TOKEN_FILE}")
            return

        error = data.get("error", "")
        if error == "authorization_pending":
            print(".", end="", flush=True)
            continue
        elif error == "slow_down":
            interval += 5
            continue
        else:
            print(f"\nError inesperado: {data}")
            sys.exit(1)

    print("\n⏰ Tiempo expirado. Vuelve a ejecutar el script.")
    sys.exit(1)


if __name__ == "__main__":
    main()
