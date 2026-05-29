"""
Lee correos no leídos de múltiples cuentas Gmail.

Cada cuenta necesita:
  - credentials_file: OAuth2 client secrets de Google Cloud Console
  - token_file:       token de acceso por cuenta (creado en el primer arranque)
"""

from __future__ import annotations

from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .base_agent import BaseAgent

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailReadAgent(BaseAgent):
    """
    Recupera todos los correos no leídos de las cuentas configuradas.

    Args:
        accounts: lista de dicts con 'alias', 'credentials_file', 'token_file'.

    Returns:
        dict {alias: [EmailDict, ...]}

    EmailDict: nombre, email, asunto, fecha, snippet.
    """

    def __init__(self, accounts: list[dict]) -> None:
        self.accounts = accounts

    def run(
        self,
        unread_only: bool = True,
        newer_than: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
    ) -> dict[str, list[dict[str, str]]]:
        query = self._build_query(unread_only, newer_than, sender, subject)
        max_results = 500 if unread_only else 100
        result: dict[str, list[dict[str, str]]] = {}
        for account in self.accounts:
            alias = account["alias"]
            try:
                result[alias] = self._fetch_for_account(account, query, max_results)
            except Exception as exc:
                result[alias] = [{"error": str(exc), "nombre": "", "email": "", "asunto": "", "fecha": "", "snippet": ""}]
        return result

    @staticmethod
    def _build_query(
        unread_only: bool,
        newer_than: str | None,
        sender: str | None,
        subject: str | None,
    ) -> str:
        parts: list[str] = []
        if unread_only:
            parts.append("is:unread")
        if newer_than:
            parts.append(f"newer_than:{newer_than}")
        if sender:
            parts.append(f"from:{sender}")
        if subject:
            parts.append(f"subject:{subject}")
        return " ".join(parts) if parts else "in:inbox"

    def _get_service(self, account: dict) -> Any:
        token_path = Path(account["token_file"])
        creds_path = account["credentials_file"]

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

    def _fetch_for_account(
        self, account: dict, query: str, max_results: int = 500
    ) -> list[dict[str, str]]:
        service = self._get_service(account)

        raw_messages: list[dict] = []
        page_token: str | None = None
        while True:
            kwargs: dict = {"userId": "me", "q": query, "maxResults": min(max_results, 500)}
            if page_token:
                kwargs["pageToken"] = page_token
            page = service.users().messages().list(**kwargs).execute()
            raw_messages.extend(page.get("messages", []))
            page_token = page.get("nextPageToken")
            if not page_token or len(raw_messages) >= max_results:
                break
        raw_messages = raw_messages[:max_results]

        emails: list[dict[str, str]] = []
        for msg in raw_messages:
            full_msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            emails.append(self._parse_message(full_msg))

        return emails

    @staticmethod
    def _parse_message(full_msg: dict) -> dict[str, str]:
        headers = {
            h["name"]: h["value"]
            for h in full_msg.get("payload", {}).get("headers", [])
        }

        from_raw = headers.get("From", "")
        if "<" in from_raw:
            nombre = from_raw.split("<")[0].strip().strip('"')
            email = from_raw.split("<")[1].rstrip(">").strip()
        else:
            nombre = ""
            email = from_raw.strip()

        date_raw = headers.get("Date", "")
        try:
            fecha = parsedate_to_datetime(date_raw).isoformat()
        except Exception:
            fecha = date_raw

        return {
            "nombre": nombre,
            "email": email,
            "asunto": headers.get("Subject", "(sin asunto)"),
            "fecha": fecha,
            "snippet": full_msg.get("snippet", ""),
        }
