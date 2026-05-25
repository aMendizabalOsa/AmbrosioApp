"""
Cerebro del bot: interpreta mensajes en lenguaje natural con Claude
y decide qué herramienta usar (o responde conversacionalmente).
"""

from __future__ import annotations

import os
from datetime import date

import anthropic

_SYSTEM_PROMPT = """\
Eres Ambrosio, el asistente personal de Telegram de Anjel.
Hoy es {today}. La zona horaria es Europe/Madrid.

Cuando el usuario pida algo concreto (recordatorio, cita, email, ver correos,
ver calendario), SIEMPRE usa las herramientas disponibles para ejecutar la acción.
No hagas la tarea en texto plano — usa la herramienta correspondiente.

Si el usuario no pide ninguna acción específica, responde de forma conversacional.
Si faltan datos para completar una herramienta (por ejemplo la hora de un recordatorio),
pregunta al usuario antes de llamar a la herramienta.

Cuando crees recordatorios o citas con fechas relativas ("mañana", "el viernes"),
calcula la fecha exacta en base a hoy: {today}.

Responde siempre en español.\
"""


class ClaudeBrain:
    """
    Gestiona la conversación con Claude via tool_use.

    process_message() devuelve:
        (text_reply, tool_name, tool_input, tool_use_id)
    Exactamente uno de (text_reply) o (tool_name, tool_input, tool_use_id) es no-nulo.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        tools: list[dict],
    ) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.client = anthropic.AsyncAnthropic(api_key=resolved_key)
        self.model = model
        self.max_tokens = max_tokens
        self.tools = tools
        self._history: dict[int, list[dict]] = {}

    async def process_message(
        self,
        chat_id: int,
        user_text: str,
    ) -> tuple[str | None, str | None, dict | None, str | None]:
        """
        Envía el mensaje a Claude con el historial de la conversación.

        Returns:
            (text_reply, tool_name, tool_input, tool_use_id)
        """
        history = self._history.setdefault(chat_id, [])
        history.append({"role": "user", "content": user_text})

        system = _SYSTEM_PROMPT.format(today=date.today().isoformat())

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=self.tools,  # type: ignore[arg-type]
            messages=history,
        )

        # Guardar el turno del asistente en el historial
        history.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_block = next(b for b in response.content if b.type == "tool_use")
            return None, tool_block.name, dict(tool_block.input), tool_block.id

        text = next((b.text for b in response.content if b.type == "text"), "")
        return text, None, None, None

    def record_tool_result(
        self,
        chat_id: int,
        tool_use_id: str,
        result_content: str,
    ) -> None:
        """
        Añade el resultado de la herramienta al historial para que Claude
        tenga contexto en el siguiente turno.
        """
        history = self._history.setdefault(chat_id, [])
        history.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result_content,
            }],
        })

    def clear_history(self, chat_id: int) -> None:
        """Borra el historial de conversación de un chat."""
        self._history.pop(chat_id, None)
