"""
Punto de entrada de AmbrosioApp.

Arranca el bot de Telegram con polling y el scheduler de recordatorios.

Uso:
    python bot_main.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import uvicorn

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from agents.calendar_agent import CalendarAgent
from agents.gmail_read_agent import GmailReadAgent
from agents.gmail_send_agent import GmailSendAgent
from agents.marine_agent import MarineAgent
from agents.reminder_agent import ReminderAgent
from agents.tado_agent import TadoAgent
from agents.traderepublic_agent import TradeRepublicAgent
from bot.dispatcher import ActionDispatcher
from bot.handlers import handle_error, handle_message, handle_reset, handle_start, handle_voice
from bot.tool_definitions import TOOLS
from gemini_brain import GeminiBrain
from web_dashboard import create_dashboard_app

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = Path("config.json")
    if not config_path.exists():
        raise FileNotFoundError(
            "No se encuentra config.json. Rellena tus credenciales antes de arrancar."
        )
    with open(config_path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    config = load_config()

    # --- Agentes que no necesitan referencia al bot ---
    gmail_accounts = config["gmail_accounts"]
    gmail_read_agent = GmailReadAgent(gmail_accounts)

    send_account = next(
        (a for a in gmail_accounts if a.get("can_send")),
        None,
    )
    if send_account is None:
        raise ValueError("Ninguna cuenta tiene 'can_send': true en config.json")

    gmail_send_agent = GmailSendAgent(send_account)
    tz = config["google_calendar"]["timezone"]

    calendar_agent = CalendarAgent(account=send_account, timezone=tz, read_only=False)

    # Construir dict de agentes de calendario para todas las cuentas habilitadas
    calendar_agents: dict[str, CalendarAgent] = {}
    for acc in gmail_accounts:
        alias = acc["alias"]
        if acc.get("can_send"):
            calendar_agents[alias] = CalendarAgent(acc, timezone=tz, read_only=False)
        elif acc.get("can_calendar"):
            calendar_agents[alias] = CalendarAgent(acc, timezone=tz, read_only=True)

    tado_agent = TadoAgent(timezone=config["google_calendar"]["timezone"])
    tr_agent = TradeRepublicAgent()

    marine_cfg = config.get("marine", {})
    marine_agent = MarineAgent(
        stormglass_api_key=marine_cfg.get("stormglass_api_key", ""),
        default_location=marine_cfg.get("default_location", "Zarautz"),
        timezone=config["google_calendar"]["timezone"],
    )

    brain = GeminiBrain(
        api_key=config["gemini"]["api_key"],
        model=config["gemini"]["model"],
        tools=TOOLS,
    )

    scheduler = AsyncIOScheduler(timezone="Europe/Madrid")

    # --- post_init: se ejecuta dentro del event loop de PTB ---
    # ReminderAgent y ActionDispatcher se construyen aquí para tener
    # acceso a la instancia Application ya inicializada.
    async def post_init(application: Application) -> None:
        try:
            reminder_agent = ReminderAgent(
                db_path=config["database"]["path"],
                scheduler=scheduler,
                bot_app=application,
                chat_id=config["telegram"]["chat_id"],
                calendar_agent=calendar_agent,
            )
            dispatcher = ActionDispatcher(
                gmail_read_agent=gmail_read_agent,
                gmail_send_agent=gmail_send_agent,
                calendar_agent=calendar_agent,
                reminder_agent=reminder_agent,
                calendar_agents=calendar_agents,
                marine_agent=marine_agent,
                tado_agent=tado_agent,
                tr_agent=tr_agent,
            )
            application.bot_data["brain"] = brain
            application.bot_data["dispatcher"] = dispatcher

            scheduler.start()
            logger.info("APScheduler iniciado.")
            await reminder_agent.initialize()
            logger.info("Recordatorios pendientes re-agendados.")

            dashboard_port = config.get("dashboard_port", 8080)
            dash_cfg = uvicorn.Config(
                create_dashboard_app(),
                host="127.0.0.1",
                port=dashboard_port,
                loop="none",
                log_level="warning",
            )
            dash_srv = uvicorn.Server(dash_cfg)
            asyncio.create_task(dash_srv.serve())
            logger.info("Dashboard disponible en http://127.0.0.1:%d", dashboard_port)

            logger.info("Bot inicializado correctamente. Esperando mensajes...")
        except Exception:
            logger.exception("ERROR CRÍTICO en post_init — el bot no responderá mensajes")
            raise

    async def post_shutdown(application: Application) -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        logger.info("APScheduler detenido.")

    # --- Construir la app con hooks de ciclo de vida ---
    app = (
        Application.builder()
        .token(config["telegram"]["token"])
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # --- Registrar handlers ---
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("reset", handle_reset))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(handle_error)

    logger.info("Iniciando AmbrosioApp...")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
