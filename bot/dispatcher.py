"""
Mapea los tool_use de Claude a los agentes correspondientes
y devuelve una respuesta formateada lista para enviar al usuario.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from agent_tracker import tracker

if TYPE_CHECKING:
    from agents.calendar_agent import CalendarAgent
    from agents.gmail_read_agent import GmailReadAgent
    from agents.gmail_send_agent import GmailSendAgent
    from agents.marine_agent import MarineAgent
    from agents.myinvestor_agent import MyInvestorAgent
    from agents.reminder_agent import ReminderAgent
    from agents.tado_agent import TadoAgent
    from agents.tapo_agent import TapoAgent
    from agents.traderepublic_agent import TradeRepublicAgent

import logging
logger = logging.getLogger(__name__)

_SUBJECT_LIMIT = 60
_PREVIEW_LIMIT = 5


class ActionDispatcher:
    def __init__(
        self,
        gmail_read_agent: "GmailReadAgent",
        gmail_send_agent: "GmailSendAgent",
        calendar_agent: "CalendarAgent",
        reminder_agent: "ReminderAgent",
        calendar_agents: "dict[str, CalendarAgent] | None" = None,
        marine_agent: "MarineAgent | None" = None,
        tado_agent: "TadoAgent | None" = None,
        tr_agents: "dict[str, TradeRepublicAgent] | None" = None,
        mi_agents: "dict[str, MyInvestorAgent] | None" = None,
        tapo_agent: "TapoAgent | None" = None,
    ) -> None:
        self._gmail_read = gmail_read_agent
        self._gmail_send = gmail_send_agent
        self._calendar = calendar_agent
        self._reminders = reminder_agent
        self._calendar_agents: dict[str, CalendarAgent] = calendar_agents or {"anjel": calendar_agent}
        self._marine = marine_agent
        self._tado = tado_agent
        self._tr_agents: dict[str, TradeRepublicAgent] = tr_agents or {}
        self._mi_agents: dict[str, MyInvestorAgent] = mi_agents or {}
        self._tapo = tapo_agent

    async def dispatch(self, tool_name: str, tool_input: dict) -> "str | dict":
        """
        Ejecuta la herramienta correspondiente.
        Devuelve str para respuestas de texto, o dict{"_photo": bytes, "_caption": str}
        para respuestas con imagen (cámara).
        """
        call = tracker.start_call(tool_name, tool_input)
        try:
            result = await self._execute(tool_name, tool_input)
            summary = result.get("_caption", str(result)) if isinstance(result, dict) else result
            tracker.finish_call(call, summary=summary)
            return result
        except Exception as exc:
            tracker.finish_call(call, error=str(exc))
            raise

    async def _execute(self, tool_name: str, tool_input: dict) -> str:
        match tool_name:
            case "set_reminder":
                return await asyncio.to_thread(self._set_reminder, **tool_input)
            case "create_appointment":
                return await asyncio.to_thread(self._create_appointment, **tool_input)
            case "send_email":
                return await asyncio.to_thread(self._send_email, **tool_input)
            case "get_email_summary":
                return await asyncio.to_thread(self._get_email_summary, **tool_input)
            case "list_upcoming_events":
                return await asyncio.to_thread(self._list_upcoming_events, **tool_input)
            case "get_portfolio":
                return await asyncio.to_thread(self._get_portfolio, **tool_input)
            case "get_myinvestor_summary":
                return await asyncio.to_thread(self._get_myinvestor_summary, **tool_input)
            case "analyze_myinvestor_portfolio":
                return await asyncio.to_thread(self._analyze_myinvestor_portfolio, **tool_input)
            case "get_marine_forecast":
                return await asyncio.to_thread(self._get_marine_forecast, **tool_input)
            case "get_home_climate":
                return await asyncio.to_thread(self._get_home_climate, **tool_input)
            case "get_camera_snapshot":
                return await asyncio.to_thread(self._get_camera_snapshot)
            case "get_camera_status":
                return await asyncio.to_thread(self._get_camera_status)
            case "camera_ptz":
                return await asyncio.to_thread(self._camera_ptz, **tool_input)
            case _:
                return f"Herramienta desconocida: {tool_name}"

    # ------------------------------------------------------------------
    # Handlers síncronos (se ejecutan en un thread pool vía to_thread)
    # ------------------------------------------------------------------

    def _set_reminder(
        self,
        title: str,
        remind_at: str,
        message: str,
        create_calendar_event: bool = True,
    ) -> str:
        result = self._reminders.run(
            title=title,
            remind_at=remind_at,
            message=message,
            create_calendar_event=create_calendar_event,
        )
        dt = datetime.fromisoformat(result["scheduled_at"])
        formatted = dt.strftime("%d/%m/%Y a las %H:%M")
        cal_note = ""
        if result.get("calendar_event_id"):
            cal_note = "\n📅 Evento creado en Google Calendar."
        return f"✅ Recordatorio programado para el {formatted}.{cal_note}"

    def _create_appointment(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str | None = None,
        description: str = "",
        location: str = "",
        guests: list[str] | None = None,
    ) -> str:
        result = self._calendar.create_event(
            title=title,
            start_datetime=start_datetime,
            end_datetime=end_datetime or "",
            description=description,
            location=location,
            guests=guests or [],
        )
        start_dt = datetime.fromisoformat(start_datetime)
        formatted_start = start_dt.strftime("%d/%m/%Y a las %H:%M")
        guests_note = ""
        if guests:
            guests_note = f"\n👥 Invitaciones enviadas a: {', '.join(guests)}"
        return (
            f"✅ Cita '{title}' creada para el {formatted_start}.{guests_note}\n"
            f"🔗 {result.get('html_link', '')}"
        )

    def _send_email(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
    ) -> str:
        result = self._gmail_send.run(to=to, subject=subject, body=body, cc=cc)
        to_str = ", ".join(to)
        return f"✅ Email enviado a {to_str}.\nAsunto: {subject}"

    def _get_email_summary(
        self,
        accounts: list[str] | None = None,
    ) -> str:
        all_data = self._gmail_read.run()

        if accounts:
            data = {alias: emails for alias, emails in all_data.items() if alias in accounts}
        else:
            data = all_data

        if not data:
            return "No hay cuentas configuradas."

        lines = ["📧 *Correos no leídos*\n"]
        total = 0

        for alias, emails in data.items():
            error_emails = [e for e in emails if e.get("error")]
            valid_emails = [e for e in emails if not e.get("error")]
            count = len(valid_emails)
            total += count

            lines.append(f"*{_esc(alias)}* — {count} correo(s)")

            if error_emails:
                lines.append(f"  ⚠️ Error: {_esc(error_emails[0]['error'])}")

            for em in valid_emails[:_PREVIEW_LIMIT]:
                nombre = _esc(em.get("nombre") or em.get("email", "Desconocido"))
                asunto = _esc((em.get("asunto") or "(sin asunto)")[:_SUBJECT_LIMIT])
                lines.append(f"  • {nombre}: _{asunto}_")

            if count > _PREVIEW_LIMIT:
                lines.append(f"  _…y {count - _PREVIEW_LIMIT} más_")

            lines.append("")

        lines.append(f"*Total:* {total} correo(s) no leído(s)")
        return "\n".join(lines)

    def _list_upcoming_events(
        self,
        max_results: int = 10,
        accounts: list[str] | None = None,
    ) -> str:
        agents_to_query = (
            {a: ag for a, ag in self._calendar_agents.items() if a in accounts}
            if accounts
            else self._calendar_agents
        )
        if not agents_to_query:
            return f"No hay calendarios configurados para: {', '.join(accounts or [])}."

        all_events: list[dict] = []
        errors: list[str] = []
        for alias, agent in agents_to_query.items():
            try:
                for ev in agent.list_upcoming(max_results=max_results):
                    ev["_cuenta"] = alias
                    all_events.append(ev)
            except Exception as exc:
                logger.exception("Error consultando calendario de %s", alias)
                errors.append(f"{alias}: {exc}")

        if not all_events and errors:
            return "⚠️ Error al consultar el calendario:\n" + "\n".join(errors)

        all_events.sort(key=lambda e: e.get("inicio", ""))
        all_events = all_events[:max_results]

        if not all_events:
            return "📅 No hay eventos próximos en el calendario."

        multi = len(agents_to_query) > 1
        lines = ["📅 *Próximos eventos*\n"]
        for ev in all_events:
            inicio_str = ev.get("inicio", "")
            try:
                inicio_dt = datetime.fromisoformat(inicio_str)
                inicio_fmt = inicio_dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                inicio_fmt = inicio_str

            titulo = _esc(ev.get("titulo", "(sin título)"))
            location = ev.get("location", "")
            asistentes = ev.get("asistentes", "")

            cuenta_tag = f" _{_esc(ev['_cuenta'])}_" if multi else ""
            cal_tag = f" ({_esc(ev['calendario'])})" if ev.get("calendario") else ""
            line = f"• *{titulo}* — {inicio_fmt}{cuenta_tag}{cal_tag}"
            if location:
                line += f"\n  📍 {_esc(location)}"
            if asistentes:
                line += f"\n  👥 {_esc(asistentes)}"
            lines.append(line)

        if errors:
            lines.append(f"\n⚠️ Sin datos de: {', '.join(errors)}")

        return "\n".join(lines)

    def _get_home_climate(
        self,
        target_datetime: str | None = None,
    ) -> str:
        if self._tado is None:
            return "⚠️ El agente Tado no está configurado."

        data = self._tado.run(target_datetime=target_datetime)
        zone = _esc(data.get("zone", "Casa"))

        if data["mode"] == "current":
            header = f"🏠 *{zone}* — ahora mismo"
        else:
            header = f"🏠 *{zone}* — {data['datetime']}"

        lines = [header, ""]

        temp = data.get("temperature")
        hum  = data.get("humidity")
        lines.append(f"🌡 Temperatura: *{temp}°C*" if temp is not None else "🌡 Temperatura: —")
        lines.append(f"💧 Humedad: *{hum}%*"       if hum  is not None else "💧 Humedad: —")

        heating_on  = data.get("heating_on", False)
        heat_pct    = data.get("heating_power")
        setpoint    = data.get("setpoint")
        manual      = data.get("manual_override", False)

        if heating_on:
            pct_str = f" ({heat_pct}%)" if heat_pct is not None else ""
            sp_str  = f" · setpoint {setpoint}°C" if setpoint is not None else ""
            mode_str = " _\\[manual\\]_" if manual else ""
            lines.append(f"🔥 Calefacción: *ON*{pct_str}{sp_str}{mode_str}")
        else:
            lines.append("❄️ Calefacción: *OFF*")

        return "\n".join(lines)

    def _get_marine_forecast(
        self,
        location: str | None = None,
        target_date: str | None = None,
    ) -> str:
        if self._marine is None:
            return "⚠️ El agente marino no está configurado."

        data = self._marine.run(location=location, target_date=target_date)
        loc   = _esc(data["location"])
        fecha = datetime.fromisoformat(data["date"]).strftime("%d/%m/%Y")

        lines = [f"🌊 *Parte de olas — {loc}* ({fecha})\n"]

        # --- Olas por franja horaria ---
        slots = data.get("waves") or []
        if slots:
            lines.append("*Olas (hora · altura · periodo · dirección)*")
            for s in slots:
                wh = s["wave_height"]
                wp = s["wave_period"]
                wd = s["wave_direction"]
                sh = s["swell_height"]
                sd = s["swell_direction"]
                # Solo mostrar swell si es significativamente distinto de la ola total
                swell_note = f"  _(swell {sh}m {sd})_" if sh >= 0.3 else ""
                lines.append(f"  {s['hour']:02d}h · {wh}m · {wp}s · {wd}{swell_note}")
        else:
            lines.append("_No hay datos de olas disponibles._")

        # --- Mareas ---
        lines.append("")
        tides = data.get("tides")
        if tides is None:
            lines.append("⏰ *Mareas:* no configuradas \\(añade la clave Storm Glass\\)")
        elif not tides:
            lines.append("⏰ *Mareas:* sin datos para esta fecha.")
        else:
            lines.append("⏰ *Mareas:*")
            for t in tides:
                icon   = "↑" if t["type"] == "high" else "↓"
                tipo   = "Pleamar" if t["type"] == "high" else "Bajamar"
                height = f"({t['height']}m)" if t.get("height") is not None else ""
                lines.append(f"  {icon} {tipo}: {t['time']}h {height}")

        return "\n".join(lines)


    def _get_myinvestor_summary(self, user: str = "anjel") -> str:
        mi = self._mi_agents.get(user.lower())
        if mi is None:
            return f"⚠️ No hay cuenta de MyInvestor configurada para {user.capitalize()}."

        data      = mi.run()
        checking  = data.get("checking", [])
        portfolio = data.get("portfolio", {})

        lines = [f"🏦 *MyInvestor — {user.capitalize()}*\n"]

        lines.append("*Cuenta corriente:*")
        for acc in checking:
            iban = _iban_short(acc.get("iban", ""))
            bal  = _fmt(acc.get("balance", 0))
            ret  = acc.get("witholdings", 0)
            ret_str = f"  _\\(retenciones: {_fmt(ret)} €\\)_" if ret else ""
            lines.append(f"  {iban}: *{bal} €*{ret_str}")

        pnl      = portfolio.get("pnl", 0)
        pnl_pct  = portfolio.get("pnl_pct", 0.0)
        mv_total = portfolio.get("total_market_value", 0)
        inv_total = portfolio.get("total_invested", 0)
        pnl_icon = "📈" if pnl >= 0 else "📉"
        pnl_sign = "+" if pnl >= 0 else ""
        pct_sign = "+" if pnl_pct >= 0 else ""

        lines.append(f"\n{pnl_icon} *Portfolio fondos/ETFs:*")
        lines.append(f"  Valor total: *{_fmt(mv_total)} €*")
        lines.append(f"  Invertido: {_fmt(inv_total)} €")
        lines.append(f"  P&L: *{pnl_sign}{_fmt(pnl)} €* ({pct_sign}{pnl_pct:.1f}%)")

        positions = portfolio.get("positions", [])
        if positions:
            lines.append("\n*Posiciones:*")
            for pos in positions[:10]:
                name = _esc(pos.get("name", "?"))
                mv   = _fmt(pos.get("market_value", 0))
                p    = pos.get("pnl", 0)
                pct  = pos.get("pnl_pct", 0.0)
                s    = "+" if p >= 0 else ""
                lines.append(f"  • *{name}* — {mv} €  ({s}{_fmt(p)} / {s}{pct:.1f}%)")

        return "\n".join(lines)

    def _analyze_myinvestor_portfolio(
        self,
        user: str = "anjel",
        include_deposits: bool = True,
        include_credits: bool = True,
    ) -> str:
        mi = self._mi_agents.get(user.lower())
        if mi is None:
            return f"⚠️ No hay cuenta de MyInvestor configurada para {user.capitalize()}."

        data = mi.get_full_analysis()

        checking = data.get("checking", [])
        funds    = data.get("funds", [])
        stocks   = data.get("stocks", [])
        deposits = data.get("deposits", []) if include_deposits else []
        credits  = data.get("credits", [])  if include_credits  else []

        lines = [f"🏦 *Análisis MyInvestor — {user.capitalize()}*\n"]

        # --- Cuentas corrientes ---
        if checking:
            lines.append("*Cuenta corriente:*")
            for acc in checking:
                iban  = _iban_short(acc.get("iban", ""))
                bal   = _fmt(acc.get("balance", 0))
                ret   = acc.get("withheld", 0)
                alias = _esc(acc.get("alias") or "")
                name  = f"{alias} ({iban})" if alias else iban
                ret_str = f"  _\\(ret\\. {_fmt(ret)} €\\)_" if ret else ""
                lines.append(f"  {name}: *{bal} €*{ret_str}")
            lines.append("")

        # --- Fondos de inversión ---
        all_investments = funds + stocks
        if all_investments:
            total_mv  = sum(p["market_value"]    for p in all_investments)
            total_ini = sum(p["invested_amount"] for p in all_investments)
            pnl       = total_mv - total_ini
            pnl_pct   = round(((total_mv / total_ini) - 1) * 100, 1) if total_ini else 0.0
            pnl_icon  = "📈" if pnl >= 0 else "📉"
            pnl_sign  = "+" if pnl >= 0 else ""
            pct_sign  = "+" if pnl_pct >= 0 else ""

            lines.append(f"{pnl_icon} *Inversiones \\(fondos \\+ ETFs\\):*")
            lines.append(f"  Valor total: *{_fmt(total_mv)} €*")
            lines.append(f"  Invertido: {_fmt(total_ini)} €")
            lines.append(f"  P&L global: *{pnl_sign}{_fmt(pnl)} €* \\({pct_sign}{pnl_pct:.1f}%\\)")
            lines.append("")

            # Fondos
            if funds:
                lines.append("*Fondos de inversión:*")
                for f in sorted(funds, key=lambda x: x["market_value"], reverse=True):
                    name = _esc(f["name"])
                    mv   = _fmt(f["market_value"])
                    p    = f["pnl"]
                    pct  = f["pnl_pct"]
                    s    = "+" if p >= 0 else ""
                    lines.append(f"  • *{name}*")
                    lines.append(f"    {mv} € · P&L: {s}{_fmt(p)} € \\({s}{pct:.1f}%\\)")
                lines.append("")

            # ETFs y acciones
            if stocks:
                lines.append("*ETFs / Acciones:*")
                for st in sorted(stocks, key=lambda x: x["market_value"], reverse=True):
                    name   = _esc(st["name"])
                    ticker = f" \\({_esc(st['ticker'])}\\)" if st.get("ticker") else ""
                    mv     = _fmt(st["market_value"])
                    p      = st["pnl"]
                    pct    = st["pnl_pct"]
                    s      = "+" if p >= 0 else ""
                    lines.append(f"  • *{name}*{ticker}")
                    lines.append(f"    {mv} € · P&L: {s}{_fmt(p)} € \\({s}{pct:.1f}%\\)")
                lines.append("")

        # --- Depósitos ---
        if deposits:
            total_dep = sum(d["amount"] for d in deposits)
            lines.append(f"*Depósitos a plazo \\({_fmt(total_dep)} €\\):*")
            for d in deposits:
                name     = _esc(d["name"])
                amount   = _fmt(d["amount"])
                tae      = d["interest_rate"]
                mat      = d.get("maturity", "")
                interest = _fmt(d["gross_interest"])
                mat_str  = f" · vence {mat}" if mat else ""
                lines.append(
                    f"  • *{name}* — {amount} € · {tae:.2f}% TAE{mat_str}"
                )
                if d["gross_interest"] > 0:
                    lines.append(f"    Interés bruto estimado: {interest} €")
            lines.append("")

        # --- Créditos ---
        if credits:
            lines.append("*Créditos activos:*")
            for c in credits:
                name    = _esc(c["name"])
                drawn   = _fmt(c["drawn_amount"])
                limit   = _fmt(c["credit_limit"])
                rate    = c["interest_rate"] * 100
                lines.append(
                    f"  • *{name}* — {drawn} € / {limit} € · TIN {rate:.2f}%"
                )
            lines.append("")

        if not (checking or all_investments or deposits or credits):
            return "No se encontraron posiciones en MyInvestor."

        return "\n".join(lines).rstrip()

    # ------------------------------------------------------------------ #
    # Cámara Tapo                                                          #
    # ------------------------------------------------------------------ #

    def _get_camera_snapshot(self) -> "str | dict":
        if self._tapo is None:
            return "⚠️ La cámara Tapo no está configurada."
        photo = self._tapo.get_snapshot()
        caption = f"📷 {datetime.now().strftime('%H:%M:%S')}"
        return {"_photo": photo, "_caption": caption}

    def _get_camera_status(self) -> str:
        if self._tapo is None:
            return "⚠️ La cámara Tapo no está configurada."
        data  = self._tapo.get_status()
        name  = _esc(data.get("name", "Cámara"))
        model = _esc(data.get("model", ""))
        fw    = _esc(data.get("firmware", ""))
        priv  = data.get("privacy_mode")
        mot   = data.get("motion_detection")

        lines = [f"📷 *{name}*"]
        if model:
            lines.append(f"  Modelo: {model}")
        if fw:
            lines.append(f"  Firmware: {fw}")
        if priv is not None:
            lines.append(f"  🔒 Privacidad: *{'ON' if priv else 'OFF'}*")
        if mot is not None:
            lines.append(f"  🎯 Detección mov\\.: *{'ON' if mot else 'OFF'}*")
        lines.append(f"  🟢 Estado: *online*")
        return "\n".join(lines)

    def _camera_ptz(self, direction: str, steps: int = 5) -> str:
        if self._tapo is None:
            return "⚠️ La cámara Tapo no está configurada."
        self._tapo.ptz_move(direction=direction, steps=steps)
        _dir_es = {"left": "izquierda", "right": "derecha", "up": "arriba", "down": "abajo"}
        dir_str = _dir_es.get(direction.lower(), direction)
        return f"✅ Cámara movida hacia {dir_str}."

    def _get_portfolio(self, user: str = "anjel") -> str:
        tr = self._tr_agents.get(user.lower())
        if tr is None:
            return f"⚠️ No hay cuenta de Trade Republic configurada para {user.capitalize()}."

        data = tr.run()
        positions = data["positions"]
        total_val = data["total_value"]
        total_inv = data["total_invested"]
        total_pnl = data["total_pnl"]
        total_pct = data["total_pnl_pct"]
        cash      = data["cash"]

        pnl_icon = "📈" if total_pnl >= 0 else "📉"
        pnl_sign = "+" if total_pnl >= 0 else ""
        pct_sign = "+" if total_pct >= 0 else ""

        lines = [
            f"{pnl_icon} *Portfolio Trade Republic — {user.capitalize()}*\n",
            f"💰 Valor total: *{_fmt(total_val)} €*",
            f"  📊 Invertido: {_fmt(total_inv)} €",
            f"  💵 Efectivo: {_fmt(cash)} €",
            f"  {'📈' if total_pnl >= 0 else '📉'} P&L: *{pnl_sign}{_fmt(total_pnl)} €* ({pct_sign}{total_pct:.1f}%)",
            "",
            "*Posiciones:*",
        ]

        has_estimated = any(p.get("estimated") for p in positions)

        for pos in positions:
            name = _esc(pos["name"])
            val  = _fmt(pos["value"])
            pnl  = pos["pnl"]
            pct  = pos["pnl_pct"]
            sign = "+" if pnl >= 0 else ""
            est_note = " _\\(est\\.\\)_" if pos.get("estimated") else ""
            lines.append(
                f"• *{name}* — {val} €{est_note} ({sign}{_fmt(pnl)} / {sign}{pct:.1f}%)"
            )

        if has_estimated:
            lines.append("\n_\\(est\\.\\) precio no disponible en tiempo real; se muestra el coste medio._")

        return "\n".join(lines)


def _iban_short(iban: str) -> str:
    return f"···{iban[-4:]}" if len(iban) >= 4 else iban


def _fmt(value: float) -> str:
    """Formatea número con separador de miles y 2 decimales (estilo europeo)."""
    formatted = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-{formatted}" if value < 0 else formatted


def _esc(text: str) -> str:
    """Escapa caracteres especiales de Markdown v1 de Telegram."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text
