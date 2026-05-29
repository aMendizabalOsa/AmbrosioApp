"""
Setup inicial de MyInvestor.

Ejecutar una sola vez (o cuando la sesión expire):
    python mi_setup.py

Guarda las credenciales en credentials/mi_credentials.json.
El bot refresca el token automáticamente durante ~30 días.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

def _creds_file(owner: str) -> Path:
    new = Path(f"credentials/mi_credentials_{owner}.json")
    if not new.exists() and owner == "anjel":
        old = Path("credentials/mi_credentials.json")
        if old.exists():
            return old
    return new


def main() -> None:
    print("=== Setup de MyInvestor ===\n")

    owner_raw = input("¿Para qué usuario? (anjel/maitane) [anjel]: ").strip().lower()
    owner = owner_raw if owner_raw in ("anjel", "maitane") else "anjel"
    print(f"Configurando cuenta de: {owner.capitalize()}\n")

    CREDS_FILE = _creds_file(owner)

    # Leer credenciales existentes o pedir nuevas
    if CREDS_FILE.exists():
        creds = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
        username  = creds.get("username", "")
        password  = creds.get("password", "")
        device_id = creds.get("device_id") or str(uuid4())
        print(f"Credenciales existentes encontradas para: {username}")
        resp = input("¿Usar las mismas credenciales? [S/n]: ").strip().lower()
        if resp in ("n", "no"):
            username  = ""
            password  = ""
            device_id = str(uuid4())
    else:
        creds     = {}
        username  = ""
        password  = ""
        device_id = str(uuid4())

    if not username:
        username = input("Usuario / DNI: ").strip()
    if not password:
        import getpass
        password = getpass.getpass("Contraseña: ")

    # Importar función de login
    sys.path.insert(0, str(Path(__file__).parent))
    from agents.myinvestor_agent import login_api

    print("\nConectando a MyInvestor…")
    result = login_api(username, password, device_id)

    if result["code"] == "OTP_REQUIRED":
        print("\nMyInvestor ha enviado un código OTP a tu teléfono/email.")
        otp = input("Introduce el código OTP (6 dígitos): ").strip()
        if len(otp) != 6 or not otp.isdigit():
            print("Código OTP inválido.")
            sys.exit(1)
        result = login_api(
            username, password, device_id,
            otp_code=otp,
            otp_process_id=result["process_id"],
        )

    if result["code"] != "OK":
        print(f"\nError de login: {result.get('message', 'desconocido')}")
        sys.exit(1)

    print("\n✓ Login exitoso.")

    # Guardar credenciales
    refresh_ttl = result.get("refresh_ttl", 86400 * 30)
    creds.update({
        "username":           username,
        "password":           password,
        "device_id":          device_id,
        "access_token":       result["access_token"],
        "refresh_token":      result["refresh_token"],
        "refresh_expires_at": (datetime.now() + timedelta(seconds=refresh_ttl)).isoformat(),
        "token_saved_at":     datetime.now().isoformat(),
    })

    # Guardar siempre en el fichero con nombre de usuario (no en el legado)
    save_file = Path(f"credentials/mi_credentials_{owner}.json")
    save_file.parent.mkdir(parents=True, exist_ok=True)
    save_file.write_text(
        json.dumps(creds, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"✓ Credenciales guardadas en {save_file}")
    print(f"  La sesión es válida hasta aprox. {(datetime.now() + timedelta(seconds=refresh_ttl)).strftime('%d/%m/%Y')}")
    print("\nYa puedes arrancar el bot.")


if __name__ == "__main__":
    main()
