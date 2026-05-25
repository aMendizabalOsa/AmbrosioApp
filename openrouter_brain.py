"""
Cerebro del bot usando OpenRouter (API compatible con OpenAI).
Permite seleccionar cualquier modelo disponible en openrouter.ai.
"""

from __future__ import annotations

import json
import os
from datetime import date

from openai import AsyncOpenAI

_SYSTEM_PROMPT = """\
Eres Ambrosio, el asistente personal de Telegram de Anjel.
La zona horaria es Europe/Madrid.

Cuando el usuario pida algo concreto (recordatorio, cita, email, ver correos,
ver calendario), SIEMPRE usa las herramientas disponibles para ejecutar la acción.
No hagas la tarea en texto plano — usa la herramienta correspondiente.

Si el usuario no pide ninguna acción específica, responde de forma conversacional.
Si faltan datos para completar una herramienta, pregunta al usuario antes de llamarla.

Cuando crees recordatorios o citas con fechas relativas ("mañana", "el viernes"),
calcula la fecha exacta en base a la fecha de hoy que aparece en cada mensaje.

Responde siempre en español.\
"""


def _to_openai_tool(tool: dict) -> dict:
    """Convierte el formato Anthropic/Gemini al formato OpenAI/OpenRouter."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


class OpenRouterBrain:
    """
    Gestiona la conversación con cualquier modelo de OpenRouter.

    process_message()     → (text_reply, tool_name, tool_input)
    process_tool_result() → respuesta en lenguaje natural tras ejecutar la herramienta
    record_tool_error()   → añade el error al historial sin llamar al modelo
    """

    def __init__(self, api_key: str, model: str, tools: list[dict]) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            base_url="https://openrouter.ai/api/v1",
            default_headers={"X-Title": "AmbrosioBot"},
        )
        self._model = model
        self._tools = [_to_openai_tool(t) for t in tools]
        self._history: dict[int, list[dict]] = {}

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = value

    def _system_message(self) -> dict:
        return {
            "role": "system",
            "content": f"{_SYSTEM_PROMPT}\nHoy es {date.today().isoformat()}.",
        }

    async def process_message(
        self,
        chat_id: int,
        user_text: str,
        audio_bytes: bytes | None = None,
        audio_mime_type: str = "audio/ogg",
    ) -> tuple[str | None, str | None, dict | None]:
        """
        Envía el mensaje al modelo seleccionado en OpenRouter.

        Returns:
            (text_reply, tool_name, tool_input) — uno de los dos grupos es no-nulo.
        """
        if audio_bytes:
            return (
                "Los mensajes de voz no están disponibles con el modelo actual. "
                "Por favor escribe tu mensaje.",
                None,
                None,
            )

        history = self._history.setdefault(chat_id, [])
        history.append({
            "role": "user",
            "content": f"[Hoy: {date.today().isoformat()}]\n{user_text}",
        })

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[self._system_message()] + history,
            tools=self._tools,
            tool_choice="auto",
        )

        message = response.choices[0].message

        if message.tool_calls:
            tc = message.tool_calls[0]
            try:
                tool_input = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_input = {}

            history.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": t.id,
                        "type": "function",
                        "function": {
                            "name": t.function.name,
                            "arguments": t.function.arguments,
                        },
                    }
                    for t in message.tool_calls
                ],
            })
            return None, tc.function.name, tool_input

        history.append({"role": "assistant", "content": message.content or ""})
        return message.content, None, None

    async def process_tool_result(
        self,
        chat_id: int,
        tool_name: str,
        result: str,
    ) -> str:
        """
        Añade el resultado de la herramienta al historial y pide al modelo
        que lo narre en lenguaje natural.
        """
        history = self._history.setdefault(chat_id, [])
        history.append({
            "role": "tool",
            "tool_call_id": self._last_tool_call_id(history),
            "content": result,
        })

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[self._system_message()] + history,
            tools=self._tools,
        )

        message = response.choices[0].message
        history.append({"role": "assistant", "content": message.content or ""})
        return message.content or ""

    def record_tool_error(self, chat_id: int, tool_name: str, error: str) -> None:
        """
        Añade un error de herramienta al historial sin llamar al modelo.
        Evita que el historial quede en estado corrupto tras un fallo.
        """
        history = self._history.setdefault(chat_id, [])
        history.append({
            "role": "tool",
            "tool_call_id": self._last_tool_call_id(history),
            "content": f"ERROR: {error}",
        })

    def clear_history(self, chat_id: int) -> None:
        self._history.pop(chat_id, None)

    @staticmethod
    def _last_tool_call_id(history: list[dict]) -> str:
        for msg in reversed(history):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                return msg["tool_calls"][0]["id"]
        return "unknown"
