"""
Gestiona recordatorios: los guarda en SQLite, los agenda con APScheduler,
y dispara un mensaje Telegram a la hora programada.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from .base_agent import BaseAgent

if TYPE_CHECKING:
    from telegram.ext import Application
    from .calendar_agent import CalendarAgent

_TZ = ZoneInfo("Europe/Madrid")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    remind_at TEXT NOT NULL,
    calendar_event_id TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL
)
"""


class ReminderAgent(BaseAgent):
    """
    Crea recordatorios con persistencia en SQLite y disparo vía APScheduler.

    Los recordatorios supervivían reinicios: initialize() los re-agenda al arrancar.
    """

    def __init__(
        self,
        db_path: str,
        scheduler: AsyncIOScheduler,
        bot_app: "Application",
        chat_id: str | int,
        calendar_agent: "CalendarAgent",
    ) -> None:
        self.db_path = db_path
        self.scheduler = scheduler
        self.bot_app = bot_app
        self.chat_id = str(chat_id)
        self.calendar_agent = calendar_agent
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Interfaz pública
    # ------------------------------------------------------------------

    def run(
        self,
        title: str,
        remind_at: str,
        message: str,
        create_calendar_event: bool = True,
    ) -> dict[str, Any]:
        """
        Crea un recordatorio:
          1. Guarda en SQLite.
          2. Agenda en APScheduler.
          3. Crea evento en Google Calendar (opcional).

        Returns:
            {"reminder_id": int, "scheduled_at": str, "calendar_event_id": str|None}
        """
        remind_dt = self._parse_dt(remind_at)

        reminder_id = self._insert_reminder(title, message, remind_dt.isoformat())

        self.scheduler.add_job(
            self._fire_reminder,
            trigger=DateTrigger(run_date=remind_dt),
            args=[reminder_id],
            id=f"reminder_{reminder_id}",
            replace_existing=True,
        )

        calendar_event_id: str | None = None
        if create_calendar_event:
            end_dt = remind_dt + timedelta(minutes=30)
            try:
                cal_result = self.calendar_agent.create_event(
                    title=title,
                    start_datetime=remind_dt.isoformat(),
                    end_datetime=end_dt.isoformat(),
                    description=message,
                )
                calendar_event_id = cal_result.get("event_id")
                if calendar_event_id:
                    self._update_calendar_event_id(reminder_id, calendar_event_id)
            except Exception:
                pass  # El recordatorio Telegram sigue activo aunque Calendar falle

        return {
            "reminder_id": reminder_id,
            "scheduled_at": remind_dt.isoformat(),
            "calendar_event_id": calendar_event_id,
        }

    async def initialize(self) -> None:
        """Re-agenda los recordatorios pendientes tras un reinicio del bot."""
        now = datetime.now(tz=_TZ)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, remind_at FROM reminders WHERE status='pending'"
            ) as cursor:
                rows = await cursor.fetchall()

        for reminder_id, remind_at_str in rows:
            remind_dt = self._parse_dt(remind_at_str)
            if remind_dt > now:
                self.scheduler.add_job(
                    self._fire_reminder,
                    trigger=DateTrigger(run_date=remind_dt),
                    args=[reminder_id],
                    id=f"reminder_{reminder_id}",
                    replace_existing=True,
                )

    # ------------------------------------------------------------------
    # Callback de APScheduler (async → corre en el event loop de PTB)
    # ------------------------------------------------------------------

    async def _fire_reminder(self, reminder_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT title, message FROM reminders WHERE id=?", (reminder_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                return
            title, message = row
            await db.execute(
                "UPDATE reminders SET status='fired' WHERE id=?", (reminder_id,)
            )
            await db.commit()

        text = f"⏰ *{_esc(title)}*\n\n{message}"
        await self.bot_app.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode="Markdown",
        )

    # ------------------------------------------------------------------
    # SQLite helpers (síncronos, llamados desde run())
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE)
            conn.commit()

    def _insert_reminder(self, title: str, message: str, remind_at: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO reminders (title, message, remind_at, created_at) VALUES (?, ?, ?, ?)",
                (title, message, remind_at, datetime.now().isoformat()),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    def _update_calendar_event_id(self, reminder_id: int, event_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE reminders SET calendar_event_id=? WHERE id=?",
                (event_id, reminder_id),
            )
            conn.commit()

    @staticmethod
    def _parse_dt(dt_str: str) -> datetime:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ)
        return dt


def _esc(text: str) -> str:
    """Escapa caracteres especiales de Markdown v1 de Telegram."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text
