"""
Autoriza cada cuenta Gmail/Calendar por separado y guarda su token.

Uso:
    python setup_oauth.py

Notas:
  - Todas las cuentas comparten el mismo credentials_gmail.json.
  - La cuenta 'anjel' necesita scopes de Gmail (read+send) y Calendar.
  - Las demás cuentas solo necesitan gmail.readonly.
  - Si ya existe un token válido para una cuenta, se salta automáticamente.
  - Para forzar la re-autorización de una cuenta, borra su token_*.json.

Pasos previos:
  1. Crea un proyecto en Google Cloud Console.
  2. Activa las APIs: Gmail API y Google Calendar API.
  3. Configura la pantalla de consentimiento OAuth (tipo Desktop).
  4. Descarga el fichero credentials_gmail.json y colócalo en credentials/.
  5. Ejecuta este script y sigue las instrucciones.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

CONFIG_FILE = "config.json"

# Scopes por tipo de cuenta
_SCOPES_SEND_CALENDAR = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]
_SCOPES_READ_CALENDAR = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
_SCOPES_READ_ONLY = [
    "https://www.googleapis.com/auth/gmail.readonly",
]


def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _scopes_for_account(account: dict) -> list[str]:
    if account.get("can_send"):
        return _SCOPES_SEND_CALENDAR
    if account.get("can_calendar"):
        return _SCOPES_READ_CALENDAR
    return _SCOPES_READ_ONLY


def token_is_valid(token_path: Path, scopes: list[str]) -> bool:
    if not token_path.exists():
        return False
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        # Reject tokens that were granted fewer scopes than currently required
        if creds.scopes and not set(scopes).issubset(creds.scopes):
            return False
        if creds.valid:
            return True
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            return True
    except Exception:
        pass
    return False


def authorize_account(account: dict) -> bool:
    alias = account["alias"]
    credentials_file = account["credentials_file"]
    token_file = account["token_file"]
    scopes = _scopes_for_account(account)

    creds_path = Path(credentials_file)
    token_path = Path(token_file)

    if not creds_path.exists():
        print(f"  [ERROR] No se encuentra {credentials_file}")
        print(f"          Descárgalo de Google Cloud Console y colócalo ahí.")
        return False

    if token_is_valid(token_path, scopes):
        print(f"  [OK] Token ya válido — se omite la autorización.")
        return True

    scope_desc = (
        "Gmail (lectura + envío) + Google Calendar (escritura)" if account.get("can_send")
        else "Gmail (lectura) + Google Calendar (solo lectura)" if account.get("can_calendar")
        else "Gmail (solo lectura)"
    )
    print(f"  Scopes: {scope_desc}")
    print(f"  Abriendo el navegador...")
    print(f"  >> Asegúrate de iniciar sesión con la cuenta '{alias}' <<")
    input("     Pulsa ENTER cuando estés listo para abrir el navegador... ")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
        creds = flow.run_local_server(port=0, prompt="select_account")
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"  [OK] Token guardado en {token_file}")
        return True
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        return False


def main() -> None:
    config = load_config()
    accounts = config.get("gmail_accounts", [])
    total = len(accounts)

    print("=" * 65)
    print(f"  Autorización OAuth2 — AmbrosioApp ({total} cuenta(s))")
    print("=" * 65)
    print()
    print("  Necesitas el fichero 'credentials/credentials_gmail.json'")
    print("  descargado de Google Cloud Console.")
    print()
    print("  La cuenta con 'can_send': true requiere permisos adicionales")
    print("  (Gmail envío + Google Calendar). Las demás solo lectura.")
    print()

    results: dict[str, bool] = {}
    for idx, account in enumerate(accounts, start=1):
        alias = account["alias"]
        email = account.get("email", alias)
        can_send = account.get("can_send", False)
        tag = " [envío + calendar]" if can_send else " [solo lectura]"
        print(f"[{idx}/{total}] {alias} ({email}){tag}")
        ok = authorize_account(account)
        results[alias] = ok
        print()

    print("=" * 65)
    print("  Resumen:")
    for alias, ok in results.items():
        estado = "OK" if ok else "FALLO"
        print(f"    {alias:20s} → {estado}")
    print("=" * 65)

    if all(results.values()):
        print("\n  Todas las cuentas autorizadas. Puedes ejecutar bot_main.py\n")
    else:
        print("\n  Algunas cuentas fallaron. Revisa los errores y repite.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
