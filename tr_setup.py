"""
Setup interactivo de Trade Republic (ejecutar una sola vez).

Requisitos previos:
    pip install pytr
    playwright install chromium   (si no se ha hecho antes)

Uso:
    python tr_setup.py

Guarda las credenciales en credentials/tr_credentials y las cookies
de sesión en credentials/tr_cookies.{phone}.txt.
"""

from __future__ import annotations

import pathlib
import sys


def main() -> None:
    try:
        from pytr.api import TradeRepublicApi
    except ImportError:
        print("ERROR: pytr no está instalado. Ejecuta: pip install pytr")
        sys.exit(1)

    print("=== Configuración de Trade Republic ===\n")
    phone = input("Número de teléfono (formato internacional, ej: +34600123456): ").strip()
    pin   = input("PIN de 4 dígitos: ").strip()

    credentials_dir = pathlib.Path("credentials")
    credentials_dir.mkdir(exist_ok=True)

    credentials_file = credentials_dir / "tr_credentials"
    cookies_file     = credentials_dir / f"tr_cookies.{phone}.txt"

    credentials_file.write_text(f"{phone}\n{pin}", encoding="utf-8")
    print(f"\nCredenciales guardadas en {credentials_file}")

    tr = TradeRepublicApi(
        phone_no=phone,
        pin=pin,
        save_cookies=True,
        credentials_file=str(credentials_file),
        cookies_file=str(cookies_file),
        waf_token="playwright",
    )

    print("\nObteniendo token WAF (abre un navegador Chromium en segundo plano)...")
    try:
        countdown = tr.initiate_weblogin()
    except Exception as exc:
        print(f"ERROR al iniciar login: {exc}")
        sys.exit(1)

    print(f"Trade Republic ha enviado un código a tu teléfono ({phone}).")
    print(f"Tienes {countdown} segundos para introducirlo.")
    code = input("Código de verificación: ").strip()

    try:
        tr.complete_weblogin(code)
    except Exception as exc:
        print(f"ERROR al completar login: {exc}")
        sys.exit(1)

    print(f"\n✅ Login completado. Cookies guardadas en {cookies_file}")
    print("Ahora puedes preguntar a Ambrosio por tu portfolio de Trade Republic.")


if __name__ == "__main__":
    main()
