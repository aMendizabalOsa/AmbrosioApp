"""
Definiciones de herramientas (tool_use) que Claude puede invocar.
"""

TOOLS: list[dict] = [
    {
        "name": "set_reminder",
        "description": (
            "Programa un recordatorio para el usuario. "
            "Envía un mensaje Telegram a la hora indicada Y crea un evento en Google Calendar. "
            "Usar cuando el usuario quiera ser recordado de algo en el futuro."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Título corto del recordatorio (también se usará en el evento de Calendar).",
                },
                "remind_at": {
                    "type": "string",
                    "description": (
                        "Fecha y hora del recordatorio en formato ISO-8601, "
                        "por ejemplo '2026-05-21T10:00:00'. "
                        "Dedúcelo del lenguaje natural. Usa Europe/Madrid si no se especifica."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "Texto del mensaje Telegram que se enviará cuando llegue la hora.",
                },
                "create_calendar_event": {
                    "type": "boolean",
                    "description": "Si también crear un evento en Google Calendar. Por defecto true.",
                },
            },
            "required": ["title", "remind_at", "message"],
        },
    },
    {
        "name": "create_appointment",
        "description": (
            "Crea una cita o reunión en Google Calendar. "
            "Si se especifican invitados, reciben una invitación de Calendar por email. "
            "Usar cuando el usuario quiera agendar una reunión o cita con otras personas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Título del evento.",
                },
                "start_datetime": {
                    "type": "string",
                    "description": "Inicio del evento en ISO-8601, por ejemplo '2026-05-21T10:00:00'.",
                },
                "end_datetime": {
                    "type": "string",
                    "description": (
                        "Fin del evento en ISO-8601. "
                        "Si no se menciona, se asume 1 hora después del inicio."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Descripción opcional del evento o agenda de la reunión.",
                },
                "location": {
                    "type": "string",
                    "description": "Lugar opcional o enlace a videollamada.",
                },
                "guests": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de emails de los invitados (recibirán invitación de Calendar).",
                },
            },
            "required": ["title", "start_datetime"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Envía un email desde anjel.mendizabal@gmail.com. "
            "Usar cuando el usuario pida enviar, escribir o redactar un email a alguien."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de emails de los destinatarios.",
                },
                "subject": {
                    "type": "string",
                    "description": "Asunto del email.",
                },
                "body": {
                    "type": "string",
                    "description": "Cuerpo del email en texto plano.",
                },
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista opcional de emails en copia (CC).",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "get_email_summary",
        "description": (
            "Obtiene un resumen de los correos no leídos de las cuentas Gmail configuradas. "
            "Usar cuando el usuario quiera ver el correo, ver mensajes nuevos o pedir un resumen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "accounts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Lista opcional de alias de cuentas a consultar "
                        "(por ejemplo ['anjel', 'kaxuela']). "
                        "Si se omite, consulta todas las cuentas."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_upcoming_events",
        "description": (
            "Lista los próximos eventos del Google Calendar. "
            "Puede consultar el calendario de anjel, de kaxuela, o de ambos. "
            "Usar cuando el usuario pregunte qué tiene en el calendario, "
            "próximas reuniones o eventos agendados."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Número máximo de eventos a devolver. Por defecto 10.",
                },
                "accounts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Lista opcional de alias cuyo calendario consultar "
                        "(por ejemplo ['anjel', 'kaxuela']). "
                        "Si se omite, consulta todos los calendarios configurados."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_home_climate",
        "description": (
            "Consulta la temperatura, humedad y estado de la calefacción (encendida/apagada, "
            "setpoint) del hogar vía Tado. "
            "Puede devolver el estado actual o el de un momento pasado específico. "
            "Usar cuando el usuario pregunte por la temperatura de casa, la calefacción, "
            "la humedad interior, o quiera saber cómo estaba la casa en un momento concreto."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_datetime": {
                    "type": "string",
                    "description": (
                        "Fecha y hora pasada en ISO-8601 (ej: '2026-05-20T14:00:00'). "
                        "Si se omite, devuelve el estado actual."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_portfolio",
        "description": (
            "Consulta el portfolio de inversiones de Trade Republic: "
            "valor total, posiciones (acciones y ETFs), precio actual, "
            "coste medio y ganancia/pérdida de cada posición. "
            "Usar cuando el usuario pregunte por sus inversiones, acciones, "
            "ETFs, portfolio, Trade Republic, rentabilidad o cuánto lleva ganado/perdido."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_marine_forecast",
        "description": (
            "Obtiene el parte de olas (altura, periodo, swell) y las horas exactas de "
            "pleamar y bajamar para un municipio costero. "
            "Usar cuando el usuario pregunte por el mar, las olas, el surf, "
            "la marea, la pleamar o la bajamar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "Nombre del municipio costero. "
                        "Si el usuario no especifica uno, usa el municipio por defecto (Zarautz)."
                    ),
                },
                "target_date": {
                    "type": "string",
                    "description": (
                        "Fecha en formato YYYY-MM-DD. "
                        "Si no se menciona, usa la fecha de hoy."
                    ),
                },
            },
            "required": [],
        },
    },
]
