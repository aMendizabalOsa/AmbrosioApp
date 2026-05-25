"""
Envía emails desde anjel.mendizabal@gmail.com usando la Gmail API.
"""

from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .base_agent import BaseAgent

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class GmailSendAgent(BaseAgent):
    """
    Envía un email desde la cuenta configurada con can_send=true.

    Args (run):
        to:      lista de destinatarios (emails).
        subject: asunto del email.
        body:    cuerpo en texto plano.
        cc:      lista opcional de CC.

    Returns:
        dict {"message_id": str, "status": "sent"}
    """

    def __init__(self, account: dict) -> None:
        self.account = account

    def run(
        self,
        to: list[str] | str,
        subject: str,
        body: str,
        cc: list[str] | None = None,
    ) -> dict[str, str]:
        if to is None:
            raise ValueError("El campo 'to' (destinatario) no puede ser nulo.")
        if isinstance(to, str):
            to = [to]
        service = self._get_service()
        raw_msg = self._build_raw_message(to, subject, body, cc or [])
        result = service.users().messages().send(userId="me", body=raw_msg).execute()
        return {"message_id": result.get("id", ""), "status": "sent"}

    def _get_service(self) -> Any:
        token_path = Path(self.account["token_file"])
        creds_path = self.account["credentials_file"]

        creds: Credentials | None = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("gmail", "v1", credentials=creds)

    def _build_raw_message(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str],
    ) -> dict[str, str]:
        msg = MIMEMultipart("alternative")
        msg["From"] = self.account["email"]
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg.attach(MIMEText(body, "plain", "utf-8"))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        return {"raw": raw}
