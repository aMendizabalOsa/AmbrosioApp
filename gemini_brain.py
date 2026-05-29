"""
Cerebro del bot: interpreta mensajes en lenguaje natural con Gemini
y decide qué herramienta usar (o responde conversacionalmente).

Usa la SDK oficial google-genai (google.genai).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import base64

from google import genai
from google.genai import types

_SYSTEM_INSTRUCTION = """\
Eres Ambrosio, el asistente personal de Telegram de Anjel.
La zona horaria es Europe/Madrid.

Cuando el usuario pida algo concreto (recordatorio, cita, email, ver correos,
ver calendario), SIEMPRE usa las herramientas disponibles para ejecutar la acción.
No hagas la tarea en texto plano — usa la herramienta correspondiente.

Si el usuario no pide ninguna acción específica, responde de forma conversacional.
Si faltan datos para completar una herramienta, pregunta al usuario antes de llamarla.

Cuando crees recordatorios o citas con fechas relativas ("mañana", "el viernes"),
calcula la fecha exacta en base a la fecha de hoy que aparece en cada mensaje.

Responde siempre en el mismo idioma que use el usuario en cada mensaje.
Si escribe en euskera, responde en euskera.
Si escribe en castellano, responde en castellano.
Si escribe en inglés, responde en inglés.
Nunca traduzcas ni incluyas traducciones en la respuesta.\
"""

# Mapa de tipos JSON Schema → tipos Gemini (mayúsculas)
_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _to_gemini_schema(schema: dict) -> dict:
    """Convierte un JSON Schema (Anthropic) al formato de schema de Gemini."""
    result: dict = {}
    if "type" in schema:
        result["type"] = _TYPE_MAP.get(schema["type"], schema["type"].upper())
    if "description" in schema:
        result["description"] = schema["description"]
    if "properties" in schema:
        result["properties"] = {
            k: _to_gemini_schema({pk: pv for pk, pv in prop.items() if pk != "default"})
            for k, prop in schema["properties"].items()
        }
    if "required" in schema:
        result["required"] = schema["required"]
    if "items" in schema:
        result["items"] = _to_gemini_schema(schema["items"])
    return result


def _build_gemini_tools(tools: list[dict]) -> list[dict]:
    """Convierte la lista de tools de Anthropic al formato de Gemini."""
    return [{
        "function_declarations": [
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": _to_gemini_schema(t["input_schema"]),
            }
            for t in tools
        ]
    }]


def _extract_model_parts(response: Any) -> list[dict]:
    """Extrae las parts de una respuesta para guardar en historial."""
    try:
        candidate = response.candidates[0]
        content = candidate.content if candidate else None
        raw_parts = content.parts if content else None
        if not raw_parts:
            return [{"text": ""}]
        parts = []
        for part in raw_parts:
            if part.function_call is not None:
                parts.append({
                    "function_call": {
                        "name": part.function_call.name,
                        "args": dict(part.function_call.args or {}),
                    }
                })
            elif part.text:
                parts.append({"text": part.text})
        return parts or [{"text": ""}]
    except (IndexError, AttributeError, TypeError):
        return [{"text": ""}]


class GeminiBrain:
    """
    Gestiona la conversación con Gemini usando function calling.

    process_message()    → (text_reply, tool_name, tool_input)
    process_tool_result() → respuesta en lenguaje natural tras ejecutar la herramienta
    """

    def __init__(self, api_key: str, model: str, tools: list[dict]) -> None:
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._client = genai.Client(api_key=resolved_key)
        self._model = model
        self._config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION,
            tools=_build_gemini_tools(tools),  # type: ignore[arg-type]
        )
        self._history: dict[int, list[dict]] = {}

    async def process_message(
        self,
        chat_id: int,
        user_text: str,
        audio_bytes: bytes | None = None,
        audio_mime_type: str = "audio/ogg",
    ) -> tuple[str | None, str | None, dict | None]:
        """
        Envía el mensaje a Gemini con el historial del chat.
        Si audio_bytes está presente, se envía el audio directamente a Gemini.

        Returns:
            (text_reply, tool_name, tool_input) — uno de los dos grupos es no-nulo.
        """
        history = self._history.setdefault(chat_id, [])

        today = f"[Hoy: {date.today().isoformat()}]"
        if audio_bytes:
            parts = [
                {
                    "inline_data": {
                        "mime_type": audio_mime_type,
                        "data": base64.b64encode(audio_bytes).decode("utf-8"),
                    }
                },
                {
                    "text": (
                        f"{today}\n"
                        "[Mensaje de voz] Entiende el contenido del audio y actúa "
                        "como si el usuario lo hubiera escrito. Usa las herramientas si es necesario."
                    )
                },
            ]
        else:
            parts = [{"text": f"{today}\n{user_text}"}]

        history.append({"role": "user", "parts": parts})

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=history,  # type: ignore[arg-type]
            config=self._config,
        )

        history.append({"role": "model", "parts": _extract_model_parts(response)})

        # Buscar function_call en la respuesta
        try:
            candidate = response.candidates[0] if response.candidates else None
            content   = candidate.content if candidate else None
            parts     = content.parts if content else None
            if parts:
                for part in parts:
                    if part.function_call is not None and part.function_call.name:
                        return None, part.function_call.name, dict(part.function_call.args or {})
        except (IndexError, AttributeError, TypeError):
            pass

        return response.text, None, None

    async def process_tool_result(
        self,
        chat_id: int,
        tool_name: str,
        result: str,
    ) -> str:
        """
        Envía el resultado de la herramienta a Gemini y obtiene
        una respuesta en lenguaje natural para mostrar al usuario.
        """
        history = self._history.setdefault(chat_id, [])

        history.append({
            "role": "user",
            "parts": [{
                "function_response": {
                    "name": tool_name,
                    "response": {"result": result},
                }
            }],
        })

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=history,  # type: ignore[arg-type]
            config=self._config,
        )
        history.append({"role": "model", "parts": _extract_model_parts(response)})

        return response.text

    def record_tool_error(self, chat_id: int, tool_name: str, error: str) -> None:
        """
        Añade un function_response de error al historial sin llamar a Gemini.
        Necesario cuando el dispatcher falla, para que el historial quede
        completo y Gemini no quede en estado corrupto en la siguiente vuelta.
        """
        history = self._history.setdefault(chat_id, [])
        history.append({
            "role": "user",
            "parts": [{
                "function_response": {
                    "name": tool_name,
                    "response": {"error": error},
                }
            }],
        })

    def clear_history(self, chat_id: int) -> None:
        """Borra el historial de conversación de un chat."""
        self._history.pop(chat_id, None)
