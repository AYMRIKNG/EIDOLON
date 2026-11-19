"""
Structured Real-Time Event Bus for EIDOLON Multi-Agent Orchestrator.
Supports dispatching events to PyQt6 UI, WebSockets (FastAPI dashboard), and loggers.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Callable, List, Dict, Any, Optional
import threading


@dataclass
class AgentEvent:
    event_type: str                  # e.g. "task.created", "agent.started", "report.generated"
    mission_id: str                  # Unique ID of overall mission
    subtask_id: Optional[str] = None # ID of specific subtask if applicable
    agent_id: Optional[str] = None   # Acronym/ID of agent if applicable (e.g. "ciso")
    state: str = "PENDING"           # State string
    progress: int = 0                # 0 - 100%
    message: str = ""                # Human-readable progress message
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class AgentEventBus:
    """Thread-safe event dispatcher for multi-agent missions."""

    def __init__(self, max_history: int = 200):
        self._listeners: List[Callable[[AgentEvent], None]] = []
        self._history: List[AgentEvent] = []
        self._lock = threading.Lock()
        self._max_history = max_history

    def subscribe(self, callback: Callable[[AgentEvent], None]) -> None:
        """Register an event listener."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def unsubscribe(self, callback: Callable[[AgentEvent], None]) -> None:
        """Unregister an event listener."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def emit(
        self,
        event_type: str,
        mission_id: str,
        subtask_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        state: str = "PENDING",
        progress: int = 0,
        message: str = "",
        data: Optional[Dict[str, Any]] = None
    ) -> AgentEvent:
        """Create and publish an event to all subscribers."""
        ev = AgentEvent(
            event_type=event_type,
            mission_id=mission_id,
            subtask_id=subtask_id,
            agent_id=agent_id,
            state=state,
            progress=max(0, min(100, int(progress))),
            message=message,
            data=data or {},
            timestamp=datetime.now().isoformat()
        )

        with self._lock:
            self._history.append(ev)
            if len(self._history) > self._max_history:
                self._history.pop(0)
            callbacks = list(self._listeners)

        # Notify subscribers
        for cb in callbacks:
            try:
                cb(ev)
            except Exception as e:
                print(f"[AgentEventBus] Listener error for {event_type}: {e}")

        # Also forward to dashboard if active
        self._forward_to_dashboard(ev)

        return ev

    def _forward_to_dashboard(self, ev: AgentEvent) -> None:
        try:
            import sys
            main_mod = sys.modules.get("main")
            if main_mod:
                engine_inst = getattr(main_mod, "_EIDOLON_INSTANCE", None) or getattr(main_mod, "_JARVIS_INSTANCE", None)
                if not engine_inst:
                    # Look in globals of main
                    for v in vars(main_mod).values():
                        if hasattr(v, "_dashboard") and getattr(v, "_dashboard") is not None:
                            engine_inst = v
                            break
                if engine_inst and getattr(engine_inst, "_dashboard", None):
                    import asyncio
                    loop = getattr(engine_inst, "_loop", None)
                    if loop and loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            engine_inst._dashboard.broadcast({
                                "type": "agent_event",
                                "event": ev.to_dict()
                            }),
                            loop
                        )
        except Exception:
            pass

    def get_history(self, mission_id: Optional[str] = None) -> List[AgentEvent]:
        """Get past events, optionally filtered by mission_id."""
        with self._lock:
            if mission_id:
                return [e for e in self._history if e.mission_id == mission_id]
            return list(self._history)

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


# Global Singleton instance
_EVENT_BUS = None

def get_event_bus() -> AgentEventBus:
    global _EVENT_BUS
    if _EVENT_BUS is None:
        _EVENT_BUS = AgentEventBus()
    return _EVENT_BUS
