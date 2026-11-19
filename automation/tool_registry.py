"""EIDOLON — Tool & Action Registry for Automation Pipelines.

Maps service/action identifiers to asynchronous Python handler functions.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

ActionHandler = Callable[[Any, Dict[str, Any]], Awaitable[Any]]


class ToolRegistry:
    """Registry maintaining mappings of (service, action) -> async handler."""

    def __init__(self) -> None:
        self._handlers: Dict[Tuple[str, str], ActionHandler] = {}

    def register(
        self,
        service: str,
        action: str,
        func: Optional[ActionHandler] = None
    ) -> Callable[[ActionHandler], ActionHandler] | None:
        """Register an async function for a given service and action.
        
        Can be used as a direct method call or as a decorator:
            @registry.register("gmail", "fetch_unread")
            async def my_handler(payload, params): ...
        """
        def decorator(handler: ActionHandler) -> ActionHandler:
            key = (service.strip().lower(), action.strip().lower())
            self._handlers[key] = handler
            return handler

        if func is not None:
            decorator(func)
            return None
        return decorator

    def get(self, service: str, action: str) -> Optional[ActionHandler]:
        """Retrieve handler for the specified service and action."""
        key = (service.strip().lower(), action.strip().lower())
        return self._handlers.get(key)

    def has(self, service: str, action: str) -> bool:
        """Check if an action is registered."""
        key = (service.strip().lower(), action.strip().lower())
        return key in self._handlers

    def list_actions(self) -> List[str]:
        """Return all registered action identifiers formatted as 'service:action'."""
        return [f"{s}:{a}" for s, a in self._handlers.keys()]


# Global default instance
global_tool_registry = ToolRegistry()


# ── Built-in Mock Actions ───────────────────────────────────────────────────

@global_tool_registry.register("gmail", "fetch_unread")
async def gmail_fetch_unread(payload: Any, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Simulate fetching unread emails from Gmail.
    
    Args:
        payload: Previous pipeline payload (unused at first step).
        params: Config parameters, e.g. {"max": 5}.
    """
    max_count = int(params.get("max", 3))
    await asyncio.sleep(0.3)  # Simulate I/O network delay

    all_mock_emails = [
        {
            "id": "msg_001",
            "sender": "elon@x.com",
            "subject": "Starship Launch Confirmation & Next Steps",
            "snippet": "Flight test 7 is green for launch tomorrow morning.",
            "received_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        },
        {
            "id": "msg_002",
            "sender": "github@updates.com",
            "subject": "[GitHub] Security Advisory for pywebview",
            "snippet": "A new patch has been released for CVE-2026-9921.",
            "received_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        },
        {
            "id": "msg_003",
            "sender": "team@eidolon.local",
            "subject": "Compte rendu réunion EIDOLON",
            "snippet": "Le module de pipeline automatisé avance selon les spécifications.",
            "received_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        },
        {
            "id": "msg_004",
            "sender": "news@dailytech.io",
            "subject": "Daily Digest: Quantum Computing Milestones",
            "snippet": "New breakthroughs in error mitigation reported this week.",
            "received_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
    ]
    return all_mock_emails[:max_count]


@global_tool_registry.register("llm", "summarize")
async def llm_summarize(payload: Any, params: Dict[str, Any]) -> str:
    """Simulate an LLM summarizing the input emails.
    
    Args:
        payload: List of emails from the previous step.
        params: Options such as {"tone": "concise"}.
    """
    tone = params.get("tone", "concise")
    await asyncio.sleep(0.4)  # Simulate LLM inference

    if not isinstance(payload, list):
        raise ValueError(f"Expected list of emails in payload, received: {type(payload).__name__}")

    lines = []
    lines.append(f"Bonjour. Voici votre synthèse ({tone}) de vos {len(payload)} courriels récents :")
    for mail in payload:
        sender = mail.get("sender", "Inconnu")
        subj = mail.get("subject", "Sans titre")
        snip = mail.get("snippet", "")
        lines.append(f"- De {sender} concernant '{subj}' : {snip}")

    return "\n".join(lines)


@global_tool_registry.register("tts", "generate_audio")
async def tts_generate_audio(payload: Any, params: Dict[str, Any]) -> str:
    """Simulate text-to-speech generating an audio/video file.
    
    Args:
        payload: Text summary to synthesize.
        params: TTS parameters, e.g. {"voice": "eidolon_fr"}.
    """
    voice = params.get("voice", "eidolon_fr")
    await asyncio.sleep(0.5)  # Simulate neural TTS rendering

    if not isinstance(payload, str) or not payload.strip():
        raise ValueError("Payload must be a non-empty string of text for TTS.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("output/audio")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"briefing_{voice}_{timestamp}.mp4"

    # Simulate creating the audio/video file placeholder
    if not out_file.exists():
        out_file.write_text(f"MOCK AUDIO [{voice}]:\n{payload}", encoding="utf-8")

    return str(out_file.resolve())


# ── File Organization Pipeline Actions ─────────────────────────────────────

@global_tool_registry.register("files", "analyze")
async def files_analyze(payload: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Analyzes a folder's files and categories asynchronously.
    
    Args:
        payload: Previous pipeline payload or unused.
        params: {"path": "downloads", "detect_duplicates": True}.
    """
    from core.smart_organizer import SmartFileAnalyzer
    folder_path = params.get("path", "downloads")
    detect_dup = params.get("detect_duplicates", True)

    analyzer = SmartFileAnalyzer()
    report = analyzer.analyze_folder(folder_path, detect_duplicates=detect_dup)
    return {
        "target_folder": report.target_folder,
        "total_files": report.total_files,
        "total_size": report.total_size_str,
        "categories": report.categories_breakdown,
        "duplicate_groups_count": len(report.duplicate_groups),
        "empty_folders_count": len(report.empty_folders)
    }


@global_tool_registry.register("files", "organize")
async def files_organize(payload: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Organizes a folder according to the requested strategy.
    
    Args:
        payload: Previous pipeline payload or unused.
        params: {"path": "downloads", "strategy": "by_type", "clean_empty": True}.
    """
    from core.smart_organizer import SmartClassificationPlanner, SecureSystemExecutor
    folder_path = params.get("path", "downloads")
    strategy = params.get("strategy", "by_type")
    clean_empty = params.get("clean_empty", True)

    planner = SmartClassificationPlanner()
    executor = SecureSystemExecutor()

    plan = planner.generate_plan(folder_path, strategy=strategy, clean_empty_folders=clean_empty)
    result = executor.execute_plan(plan, on_conflict="rename", prune_empty=clean_empty)
    return result

