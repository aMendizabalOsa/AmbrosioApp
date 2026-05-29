"""
Handlers de Telegram: reciben mensajes del usuario y orquestan
la llamada a Gemini y el despacho a los agentes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handler principal. Registrado para todos los mensajes de texto.

    Flujo:
        1. Indicador "escribiendo..."
        2. Gemini interpreta el mensaje y decide si llamar una herramienta
        3a. Si hay function_call → dispatcher ejecuta el agente →
            Gemini narra el resultado en lenguaje natural
        3b. Si es texto → respuesta conversacional de Gemini
    """
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id  # type: ignore[union-attr]
    user_text = update.message.text
    logger.info("Mensaje recibido de chat_id=%s: %r", chat_id, user_text[:60])

    # --- OTP pendiente de Trade Republic ---
    pending = context.bot_data.get("tr_otp_pending")
    if pending:
        if datetime.now() < pending["expires_at"]:
            code = user_text.strip()
            if code.isdigit() and len(code) in (4, 5, 6):
                context.bot_data.pop("tr_otp_pending")
                try:
                    from agents.traderepublic_agent import TradeRepublicAgent
                    await asyncio.to_thread(
                        TradeRepublicAgent.complete_relogin, pending["api"], code
                    )
                    await update.message.reply_text(
                        f"✅ Sesión de Trade Republic para *{pending['user'].capitalize()}* renovada. "
                        "Ya puedes consultar el portfolio.",
                        parse_mode="Markdown",
                    )
                except Exception as exc:
                    await update.message.reply_text(
                        f"❌ Error al completar login: `{exc}`", parse_mode="Markdown"
                    )
                return
        else:
            context.bot_data.pop("tr_otp_pending", None)

    try:
        brain = context.bot_data["brain"]
        dispatcher = context.bot_data["dispatcher"]

        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        text_reply, tool_name, tool_input = await brain.process_message(
            chat_id, user_text
        )
        logger.info("Gemini respondió — tool=%s text=%r", tool_name, (text_reply or "")[:60])

        if tool_name and tool_input is not None:
            try:
                result = await dispatcher.dispatch(tool_name, tool_input)
            except Exception as tool_exc:
                brain.record_tool_error(chat_id, tool_name, str(tool_exc))
                raise

            await _send_tool_result(update, context, brain, chat_id, tool_name, result)

        elif text_reply:
            await update.message.reply_text(text_reply)

        else:
            await update.message.reply_text("No he podido generar una respuesta. ¿Puedes reformularlo?")

    except Exception as exc:
        logger.exception("Error procesando mensaje de chat_id=%s", chat_id)
        try:
            await update.message.reply_text(f"Lo siento, algo salió mal:\n`{exc}`", parse_mode="Markdown")
        except Exception:
            pass


async def handle_voice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handler para mensajes de voz. Descarga el audio y lo envía directamente
    a Gemini, que lo transcribe e interpreta en una sola llamada.
    """
    if not update.message or not update.message.voice:
        return

    chat_id = update.effective_chat.id  # type: ignore[union-attr]
    voice = update.message.voice
    logger.info("Voz recibida de chat_id=%s (%.1fs)", chat_id, voice.duration)

    try:
        brain = context.bot_data["brain"]
        dispatcher = context.bot_data["dispatcher"]

        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        voice_file = await context.bot.get_file(voice.file_id)
        audio_bytes = bytes(await voice_file.download_as_bytearray())
        mime_type = voice.mime_type or "audio/ogg"

        text_reply, tool_name, tool_input = await brain.process_message(
            chat_id=chat_id,
            user_text="",
            audio_bytes=audio_bytes,
            audio_mime_type=mime_type,
        )
        logger.info("Gemini respondió al audio — tool=%s text=%r", tool_name, (text_reply or "")[:60])

        if tool_name and tool_input is not None:
            try:
                result = await dispatcher.dispatch(tool_name, tool_input)
            except Exception as tool_exc:
                brain.record_tool_error(chat_id, tool_name, str(tool_exc))
                raise

            await _send_tool_result(update, context, brain, chat_id, tool_name, result)
        elif text_reply:
            await update.message.reply_text(text_reply)
        else:
            await update.message.reply_text("No he podido entender el audio. ¿Puedes repetirlo?")

    except Exception as exc:
        logger.exception("Error procesando voz de chat_id=%s", chat_id)
        try:
            await update.message.reply_text(
                f"Lo siento, algo salió mal con el audio:\n`{exc}`",
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Error handler global de PTB — loguea todos los errores no capturados."""
    logger.exception("Error no capturado en PTB:", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text(
                f"Error inesperado: `{context.error}`", parse_mode="Markdown"
            )
        except Exception:
            pass


async def _send_tool_result(update, context, brain, chat_id, tool_name, result) -> None:
    """
    Envía el resultado de una herramienta al chat.
    Si result es un dict con '_photo', manda la foto + narración de Gemini.
    Si es str, manda solo texto con narración.
    """
    if isinstance(result, dict) and "_photo" in result:
        photo_bytes: bytes = result["_photo"]
        caption: str       = result.get("_caption", "")
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=photo_bytes,
            caption=caption or None,
        )
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        narration = await brain.process_tool_result(chat_id, tool_name, caption or "foto enviada")
        if narration:
            await update.message.reply_text(narration)
    else:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        narration = await brain.process_tool_result(chat_id, tool_name, result)
        await update.message.reply_text(narration or result)


async def handle_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Bienvenida cuando el usuario envía /start."""
    await update.message.reply_text(  # type: ignore[union-attr]
        "👋 Hola, soy *Ambrosio*, tu asistente personal.\n\n"
        "Puedes pedirme cosas como:\n"
        "• _Recuérdame llamar al dentista mañana a las 10_\n"
        "• _Crea una cita con Ana el viernes a las 15h e invítala_\n"
        "• _Envía un email a juan@ejemplo.com sobre la reunión_\n"
        "• _¿Qué correos sin leer tengo?_\n"
        "• _¿Qué tengo en el calendario esta semana?_",
        parse_mode="Markdown",
    )


async def handle_tr_login(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Renueva la sesión de Trade Republic sin salir del chat.
    Uso: /tr_login [anjel|maitane]
    """
    args = context.args or []
    user = args[0].lower() if args and args[0].lower() in ("anjel", "maitane") else "anjel"
    chat_id = update.effective_chat.id  # type: ignore[union-attr]

    await update.message.reply_text(  # type: ignore[union-attr]
        f"🔄 Iniciando sesión de Trade Republic para *{user.capitalize()}*…\n"
        "_\\(Playwright abre un navegador en segundo plano para el bypass WAF, puede tardar 20–30 s\\)_",
        parse_mode="MarkdownV2",
    )

    try:
        from agents.traderepublic_agent import TradeRepublicAgent
        agent = TradeRepublicAgent(user)
        tr_instance, countdown = await asyncio.to_thread(agent.initiate_relogin)
    except Exception as exc:
        await update.message.reply_text(  # type: ignore[union-attr]
            f"❌ Error al iniciar login: `{exc}`", parse_mode="Markdown"
        )
        return

    context.bot_data["tr_otp_pending"] = {
        "user":       user,
        "api":        tr_instance,
        "expires_at": datetime.now() + timedelta(seconds=countdown + 60),
    }

    await update.message.reply_text(  # type: ignore[union-attr]
        f"📱 Código enviado al teléfono de *{user.capitalize()}*\\.\n"
        f"Tienes *{countdown}s* — responde aquí con el código OTP recibido por SMS\\.",
        parse_mode="MarkdownV2",
    )


async def handle_reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Borra el historial de conversación con /reset."""
    chat_id = update.effective_chat.id  # type: ignore[union-attr]
    context.bot_data["brain"].clear_history(chat_id)
    await update.message.reply_text("🔄 Historial de conversación borrado.")  # type: ignore[union-attr]


async def handle_modelo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Muestra o cambia el modelo de OpenRouter.

    Uso:
        /modelo          → lista modelos disponibles y modelo actual
        /modelo <nombre> → cambia al modelo indicado y borra el historial
    """
    brain = context.bot_data.get("brain")
    available: list[str] = context.bot_data.get("available_models", [])
    chat_id = update.effective_chat.id  # type: ignore[union-attr]

    args = context.args or []

    if not args:
        current = getattr(brain, "model", "desconocido")
        lines = [f"🤖 Modelo actual: `{current}`"]
        if available:
            lines.append("\nModelos disponibles:")
            for m in available:
                marker = "▶" if m == current else "  "
                lines.append(f"{marker} `{m}`")
            lines.append("\nUsa `/modelo <nombre>` para cambiar\\.")
        await update.message.reply_text(  # type: ignore[union-attr]
            "\n".join(lines), parse_mode="MarkdownV2"
        )
        return

    new_model = args[0]
    if not hasattr(brain, "model"):
        await update.message.reply_text("⚠️ El brain actual no soporta cambio de modelo.")  # type: ignore[union-attr]
        return

    old_model = brain.model
    brain.model = new_model
    brain.clear_history(chat_id)
    await update.message.reply_text(  # type: ignore[union-attr]
        f"✅ Modelo cambiado:\n`{old_model}`\n→ `{new_model}`\n\n"
        f"_\\(Historial borrado\\)_",
        parse_mode="MarkdownV2",
    )
