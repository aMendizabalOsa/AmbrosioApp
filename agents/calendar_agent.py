"""
Gestiona eventos de Google Calendar: crear citas e listar próximos eventos.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .base_agent import BaseAgent

_SCOPES_FULL = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]
_SCOPES_READ = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


class CalendarAgent(BaseAgent):
    """
    Crea y lista eventos en Google Calendar.

    Constructor:
        account:   dict con credentials_file, token_file, email.
        timezone:  zona horaria por defecto (p.ej. 'Europe/Madrid').
        read_only: True para cuentas con solo calendar.readonly (no pueden crear eventos).
    """

    def __init__(
        self,
        account: dict,
        timezone: str = "Europe/Madrid",
        read_only: bool = False,
    ) -> None:
        self.account = account
        self.timezone = timezone
        self._tz = ZoneInfo(timezone)
        self._scopes = _SCOPES_READ if read_only else _SCOPES_FULL
        self.read_only = read_only

    def run(self, action: str, **kwargs: Any) -> Any:
        if action == "create_event":
            return self.create_event(**kwargs)
        if action == "list_upcoming":
            return self.list_upcoming(**kwargs)
        raise ValueError(f"Acción desconocida: {action}")

    def create_event(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str | None = None,
        description: str = "",
        guests: list[str] | None = None,
        location: str = "",
        send_notifications: bool = True,
    ) -> dict[str, str]:
        """
        Crea un evento en Google Calendar.
        Si se incluyen guests, reciben invitación por email automáticamente.

        Returns:
            {"event_id": str, "html_link": str, "status": "created"}
        """
        start_dt = self._parse_dt(start_datetime)

        if end_datetime:
            end_dt = self._parse_dt(end_datetime)
        else:
            end_dt = start_dt + timedelta(hours=1)

        event_body: dict = {
            "summary": title,
            "description": description,
            "location": location,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": self.timezone,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": self.timezone,
            },
        }

        if guests:
            event_body["attendees"] = [{"email": g} for g in guests]

        service = self._get_service()
        result = (
            service.events()
            .insert(
                calendarId="primary",
                body=event_body,
                sendNotifications=send_notifications,
            )
            .execute()
        )

        return {
            "event_id": result.get("id", ""),
            "html_link": result.get("htmlLink", ""),
            "status": "created",
        }

    def list_calendars(self) -> list[dict[str, str]]:
        """Devuelve todos los calendarios activos de la cuenta."""
        service = self._get_service()
        result = service.calendarList().list(minAccessRole="reader").execute()
        return [
            {"id": c["id"], "nombre": c.get("summary", c["id"])}
            for c in result.get("items", [])
            if not c.get("deleted") and c.get("selected", True)
        ]

    def list_upcoming(
        self,
        max_results: int = 10,
        time_min: str | None = None,
    ) -> list[dict[str, str]]:
        """
        Lista los próximos eventos de TODOS los calendarios activos de la cuenta.
        Los eventos duplicados (mismo ID en varios calendarios) se deduplicán.

        Returns:
            lista de dicts con título, inicio, fin, location, asistentes, calendario.
        """
        time_min_dt = (
            self._parse_dt(time_min).isoformat()
            if time_min
            else datetime.now(tz=self._tz).isoformat()
        )

        service = self._get_service()
        calendars = self.list_calendars()
        if not calendars:
            calendars = [{"id": "primary", "nombre": "Principal"}]

        seen: set[str] = set()
        events: list[dict[str, str]] = []

        for cal in calendars:
            try:
                result = (
                    service.events()
                    .list(
                        calendarId=cal["id"],
                        timeMin=time_min_dt,
                        maxResults=max_results,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )
            except Exception:
                continue  # calendario inaccesible, lo saltamos

            for item in result.get("items", []):
                event_id = item.get("id", "")
                if event_id in seen:
                    continue
                seen.add(event_id)

                start = item.get("start", {})
                end = item.get("end", {})
                attendees = [a.get("email", "") for a in item.get("attendees", [])]
                events.append({
                    "titulo": item.get("summary", "(sin título)"),
                    "inicio": start.get("dateTime") or start.get("date", ""),
                    "fin": end.get("dateTime") or end.get("date", ""),
                    "location": item.get("location", ""),
                    "asistentes": ", ".join(attendees),
                    "link": item.get("htmlLink", ""),
                    "calendario": cal["nombre"] if cal["id"] != "primary" else "",
                })

        events.sort(key=lambda e: e.get("inicio", ""))
        return events[:max_results]

    def _get_service(self) -> Any:
        token_path = Path(self.account["token_file"])
        creds_path = self.account["credentials_file"]

        creds: Credentials | None = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), self._scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, self._scopes)
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("calendar", "v3", credentials=creds)

    def _parse_dt(self, dt_str: str) -> datetime:
        """Parsea ISO-8601 y asigna la timezone del agente si no tiene."""
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._tz)
        return dt
