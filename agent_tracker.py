"""
Tracker de actividad de agentes. Registra cada llamada (quién, qué, cuándo,
duración, resultado) para exponerla en el dashboard web.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


_TOOL_TO_AGENT: dict[str, str] = {
    "set_reminder": "ReminderAgent",
    "create_appointment": "CalendarAgent",
    "send_email": "GmailSendAgent",
    "get_email_summary": "GmailReadAgent",
    "list_upcoming_events": "CalendarAgent",
    "get_marine_forecast": "MarineAgent",
    "get_home_climate":   "TadoAgent",
}

_ALL_AGENTS = ("GmailReadAgent", "GmailSendAgent", "CalendarAgent", "ReminderAgent", "MarineAgent", "TadoAgent")


@dataclass
class AgentCall:
    agent: str
    tool: str
    input_preview: str
    status: str  # "running" | "success" | "error"
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    summary: str = ""
    error: str = ""


class AgentTracker:
    def __init__(self, maxlen: int = 200) -> None:
        self._lock = threading.Lock()
        self._calls: deque[AgentCall] = deque(maxlen=maxlen)
        self._stats: dict[str, dict] = {
            name: {"total": 0, "errors": 0, "last_call": None, "last_status": None}
            for name in _ALL_AGENTS
        }

    def start_call(self, tool_name: str, tool_input: dict) -> AgentCall:
        agent = _TOOL_TO_AGENT.get(tool_name, "Unknown")
        preview = _make_preview(tool_name, tool_input)
        call = AgentCall(
            agent=agent,
            tool=tool_name,
            input_preview=preview,
            status="running",
            started_at=datetime.now(),
        )
        with self._lock:
            self._calls.append(call)
            if agent in self._stats:
                self._stats[agent]["last_status"] = "running"
        return call

    def finish_call(self, call: AgentCall, summary: str = "", error: str = "") -> None:
        now = datetime.now()
        with self._lock:
            call.ended_at = now
            call.duration_ms = (now - call.started_at).total_seconds() * 1000
            call.summary = summary[:150] if summary else ""
            if error:
                call.status = "error"
                call.error = error[:200]
                if call.agent in self._stats:
                    self._stats[call.agent]["errors"] += 1
                    self._stats[call.agent]["last_status"] = "error"
            else:
                call.status = "success"
                if call.agent in self._stats:
                    self._stats[call.agent]["last_status"] = "success"
            if call.agent in self._stats:
                self._stats[call.agent]["total"] += 1
                self._stats[call.agent]["last_call"] = now.isoformat()

    def get_recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            calls = list(reversed(list(self._calls)))[:limit]
        return [
            {
                "agent": c.agent,
                "tool": c.tool,
                "input_preview": c.input_preview,
                "status": c.status,
                "started_at": c.started_at.isoformat(),
                "ended_at": c.ended_at.isoformat() if c.ended_at else None,
                "duration_ms": round(c.duration_ms, 1) if c.duration_ms is not None else None,
                "summary": c.summary,
                "error": c.error,
            }
            for c in calls
        ]

    def get_stats(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self._stats.items()}


def _make_preview(tool_name: str, tool_input: dict) -> str:
    match tool_name:
        case "set_reminder":
            return f"'{tool_input.get('title', '')}' → {tool_input.get('remind_at', '')}"
        case "create_appointment":
            return f"'{tool_input.get('title', '')}' @ {tool_input.get('start_datetime', '')}"
        case "send_email":
            to = ", ".join(tool_input.get("to", []))
            return f"to: {to[:60]}"
        case "get_email_summary":
            accounts = tool_input.get("accounts")
            return f"accounts: {', '.join(accounts) if accounts else 'todas'}"
        case "list_upcoming_events":
            return f"max: {tool_input.get('max_results', 10)} eventos"
        case _:
            return str(tool_input)[:80]


tracker = AgentTracker()
