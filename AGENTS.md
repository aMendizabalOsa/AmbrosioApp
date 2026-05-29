# AmbrosioApp — Contexto para agentes IA

Bot de Telegram personal que actúa como asistente doméstico inteligente.
Cerebro: **Gemini** (`gemini-2.5-flash-preview-05-20` vía `gemini_brain.py`).
Se arranca manualmente con `python bot_main.py`.

---

## Arquitectura general

```
bot_main.py          ← punto de entrada; instancia agentes y registra handlers
gemini_brain.py      ← LLM (Gemini); convierte mensajes en tool_calls o texto
bot/handlers.py      ← handlers PTB: mensajes de texto, voz, comandos
bot/dispatcher.py    ← mapea tool_name → agente; devuelve str o dict{_photo}
bot/tool_definitions.py ← schemas JSON de herramientas que ve el LLM
agents/              ← un fichero por servicio externo
agent_tracker.py     ← registro de llamadas para el dashboard web
web_dashboard.py     ← FastAPI en 127.0.0.1:8080; consume agent_tracker
```

El bot vive en un único evento loop (PTB + APScheduler + uvicorn como tasks).
Llamadas síncronas a agentes se ejecutan con `asyncio.to_thread`.

---

## Usuarios de la familia

El bot sirve a **dos usuarios**: **Anjel** y **Maitane**.

- Las cuentas financieras (MyInvestor, Trade Republic) son independientes por usuario.
- Las credenciales se nombran `{servicio}_{usuario}` (ej. `mi_credentials_anjel.json`).
- Fallback automático al fichero sin sufijo para Anjel (compatibilidad con ficheros anteriores).
- Las herramientas financieras aceptan parámetro `user: "anjel" | "maitane"`.
- El LLM infiere el usuario por contexto; si no hay contexto usa `anjel` por defecto.

---

## Checklist para añadir una herramienta nueva

1. `agents/<nombre>_agent.py` — clase con `run()` o métodos específicos; hereda `BaseAgent`.
2. `bot/tool_definitions.py` — añadir entrada en la lista `TOOLS` con schema JSON completo.
3. `bot/dispatcher.py`:
   - Añadir `case "nombre_herramienta":` en `_execute`.
   - Añadir método `_nombre_herramienta(...)` que formatea el resultado como string Markdown.
4. `agent_tracker.py`:
   - Añadir `"nombre_herramienta": "NombreAgent"` en `_TOOL_TO_AGENT`.
   - Añadir el agente en `_ALL_AGENTS` si es nuevo.
   - Añadir `case "nombre_herramienta":` en `_make_preview`.
5. `bot_main.py` — instanciar el agente y pasarlo al constructor de `ActionDispatcher`.

---

## Autenticación de servicios externos

| Servicio | Método | Renovación |
|---|---|---|
| Gmail / Calendar | OAuth2 (token en `credentials/`) | Automática vía `google-auth` |
| Tado | Usuario/contraseña en `config.json` | Automática |
| Trade Republic | Cookies WebSocket (pytr) | Manual: `/tr_login [anjel\|maitane]` en Telegram. Caduca ~24 h. APScheduler avisa 20 h después del último check. |
| MyInvestor | Bearer token + refresh token | Automática (probe → refresh → re-login silencioso). Si requiere OTP: `python mi_setup.py`. Login usa `curl_cffi` con impersonación Chrome para evitar CAPTCHA. |
| Tapo | IP local + contraseña en `config.json` | Sin sesión; cada llamada se conecta directamente |

**Credenciales nunca se commitean.** Directorios y ficheros excluidos de git:
- `credentials/` (tokens OAuth, cookies TR, tokens MI)
- `config.json`
- `db/`

---

## Convenciones de código

- **Idioma de respuestas**: el bot detecta el idioma del usuario y responde en el mismo.
- **Formato Telegram**: MarkdownV2 para respuestas con formato; escapar `_*[]()~>#+-=|{}.!` con `\`.
- **Helper `_esc(text)`** en `dispatcher.py` para escapar nombres de fondos/activos.
- **Helper `_fmt(value)`** en `dispatcher.py` para números con separador europeo (`.` miles, `,` decimales).
- Respuestas concisas: sin párrafos de relleno, sin confirmaciones verbosas.
- Sin comentarios salvo que el *por qué* sea no obvio.

---

## Comportamientos obligatorios

- **No ejecutar acciones irreversibles** (enviar email, crear/borrar evento, programar recordatorio) sin haberlas confirmado antes con el usuario.
- **No exponer credenciales, tokens ni contraseñas** en logs ni en respuestas al usuario.
- **Propagar excepciones** desde los agentes: el dispatcher no captura errores de agente; los re-lanza para que lleguen al handler y se muestren al usuario.
- **Nunca commitear** `credentials/`, `config.json` ni `db/`.

---

## Servicios activos

| Herramienta | Agente | Descripción |
|---|---|---|
| `set_reminder` | `ReminderAgent` | Recordatorio Telegram + evento Calendar |
| `create_appointment` | `CalendarAgent` | Evento Calendar con invitados opcionales |
| `send_email` | `GmailSendAgent` | Email desde cuenta principal |
| `get_email_summary` | `GmailReadAgent` | Correos no leídos de una o varias cuentas |
| `list_upcoming_events` | `CalendarAgent` | Próximos eventos de Anjel y/o Maitane |
| `get_home_climate` | `TadoAgent` | Temperatura, humedad, calefacción |
| `get_myinvestor_summary` | `MyInvestorAgent` | Cuenta corriente + portfolio resumido |
| `analyze_myinvestor_portfolio` | `MyInvestorAgent` | Análisis completo: fondos, ETFs, depósitos, créditos |
| `get_portfolio` | `TradeRepublicAgent` | Portfolio TR con P&L por posición |
| `get_marine_forecast` | `MarineAgent` | Olas, swell, mareas (Stormglass API) |
| `get_camera_snapshot` | `TapoAgent` | Foto en tiempo real (streaming MPEG-TS → JPEG) |
| `get_camera_status` | `TapoAgent` | Estado de la cámara Tapo C200 |
| `camera_ptz` | `TapoAgent` | Mover cámara (left/right/up/down) |

---

## Comandos de Telegram

| Comando | Descripción |
|---|---|
| `/start` | Bienvenida |
| `/reset` | Borra historial de conversación |
| `/tr_login [anjel\|maitane]` | Renueva sesión Trade Republic via OTP por SMS |
| `/modelo [nombre]` | Ver o cambiar modelo de OpenRouter (si se usa ese brain) |

---

## Notas técnicas relevantes

- **Tapo streaming**: la cámara C200 usa el protocolo propietario de puerto 8800. `cloudPassword` en el constructor de `Tapo()` debe ser la contraseña local del dispositivo para que el streaming autentique correctamente.
- **pytr + asyncio**: `TradeRepublicAgent.run()` usa `asyncio.run()` para crear un event loop propio; se llama siempre desde `asyncio.to_thread` en el dispatcher para no bloquear el loop de PTB.
- **curl_cffi para MyInvestor**: el login a `api.myinvestor.es` requiere huella TLS de Chrome; `httpx` puede triggear CAPTCHA (`SECURITY_001`) en cuentas no reconocidas.
- **Multi-usuario en agentes financieros**: `MyInvestorAgent(owner)` y `TradeRepublicAgent(owner)` aceptan `"anjel"` o `"maitane"`. El dispatcher mantiene dicts `mi_agents` y `tr_agents`.
- **APScheduler**: corre dentro del event loop de PTB. Los jobs de monitorización de sesiones (TR cada 20 h, MI cada 8 h) notifican por Telegram si detectan sesión caducada.
