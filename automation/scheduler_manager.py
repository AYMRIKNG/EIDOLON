"""EIDOLON — Workflow Scheduler & Background Manager.

Scans workflow JSON files, schedules them according to their timing rules,
and manages execution in an asynchronous background thread.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import schedule

from automation.pipeline_runner import PipelineResult, PipelineRunner
from automation.tool_registry import ToolRegistry, global_tool_registry

logger = logging.getLogger("EidolonScheduler")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [SCHEDULER] %(message)s",
        datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class WorkflowScheduler:
    """Manages scheduled execution of JSON-defined workflows in a background thread."""

    def __init__(
        self,
        workflows_dir: Path | str = "workflows",
        runner: Optional[PipelineRunner] = None,
        registry: Optional[ToolRegistry] = None
    ) -> None:
        self.workflows_dir = Path(workflows_dir)
        self.runner = runner or PipelineRunner(registry=registry or global_tool_registry)
        self._scheduler = schedule.Scheduler()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._loaded_workflows: Dict[str, Dict[str, Any]] = {}
        self.on_workflow_completed: Optional[Callable[[PipelineResult], None]] = None

    def load_workflows(self) -> int:
        """Scan workflows directory, clear existing schedules, and register jobs."""
        self._scheduler.clear()
        self._loaded_workflows.clear()

        if not self.workflows_dir.exists():
            logger.warning(f"Le dossier des workflows n'existe pas : {self.workflows_dir.resolve()}")
            self.workflows_dir.mkdir(parents=True, exist_ok=True)
            return 0

        json_files = list(self.workflows_dir.glob("*.json"))
        scheduled_count = 0

        for file_path in json_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                wf_id = data.get("id") or file_path.stem
                schedule_rule = data.get("schedule")

                self._loaded_workflows[wf_id] = data

                if not schedule_rule:
                    logger.info(f"Workflow '{wf_id}' chargé sans règle de planification (manuel).")
                    continue

                if self._register_schedule(wf_id, data, str(schedule_rule)):
                    scheduled_count += 1

            except Exception as e:
                logger.error(f"Erreur de lecture du workflow {file_path.name} : {e}")

        logger.info(f"Planification terminée : {scheduled_count}/{len(json_files)} workflows programmés.")
        return scheduled_count

    def _register_schedule(self, wf_id: str, workflow_data: Dict[str, Any], rule: str) -> bool:
        """Parses schedule rules such as '08:00', 'every 30 minutes', 'every 10 seconds'."""
        clean_rule = rule.strip().lower()

        # Job runner callback executed by schedule in the scheduler thread
        def job():
            logger.info(f"[TRIGGER] Declenchement automatique du workflow '{wf_id}' (Regle: '{rule}')")
            self._execute_async(workflow_data)

        # 1. Exact daily time: "08:00" or "08:00:00"
        time_match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", clean_rule)
        if time_match:
            hh = int(time_match.group(1))
            mm = int(time_match.group(2))
            formatted_time = f"{hh:02d}:{mm:02d}"
            self._scheduler.every().day.at(formatted_time).do(job).tag(wf_id)
            logger.info(f"Workflow '{wf_id}' planifié quotidiennement à {formatted_time}.")
            return True

        # 2. Interval rules: "every X minutes/hours/seconds"
        interval_match = re.match(r"^every\s+(\d+)\s+(second|seconds|minute|minutes|hour|hours)$", clean_rule)
        if interval_match:
            amount = int(interval_match.group(1))
            unit = interval_match.group(2)

            if "second" in unit:
                self._scheduler.every(amount).seconds.do(job).tag(wf_id)
            elif "minute" in unit:
                self._scheduler.every(amount).minutes.do(job).tag(wf_id)
            elif "hour" in unit:
                self._scheduler.every(amount).hours.do(job).tag(wf_id)

            logger.info(f"Workflow '{wf_id}' planifié toutes les {amount} {unit}.")
            return True

        logger.error(f"Format de planification non reconnu pour '{wf_id}' : '{rule}'")
        return False

    def _execute_async(self, workflow_data: Dict[str, Any]) -> None:
        """Runs the async workflow in a dedicated asyncio event loop on a worker thread."""
        def worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self.runner.run_workflow(workflow_data))
                if self.on_workflow_completed:
                    self.on_workflow_completed(result)
            finally:
                loop.close()

        threading.Thread(target=worker, name=f"wf-run-{workflow_data.get('id', 'job')}", daemon=True).start()

    def trigger_workflow(self, workflow_id: str) -> bool:
        """Manually trigger a loaded workflow immediately."""
        if workflow_id not in self._loaded_workflows:
            logger.warning(f"Impossible de déclencher '{workflow_id}' : non trouvé.")
            return False
        logger.info(f"Déclenchement manuel immédiat du workflow '{workflow_id}'.")
        self._execute_async(self._loaded_workflows[workflow_id])
        return True

    def start(self) -> None:
        """Starts the scheduler background daemon loop."""
        if self._thread and self._thread.is_alive():
            logger.warning("Le planificateur est déjà en cours d'exécution.")
            return

        self._stop_event.clear()
        self.load_workflows()

        def loop_worker():
            logger.info("Boucle de fond du planificateur démarrée.")
            while not self._stop_event.is_set():
                try:
                    self._scheduler.run_pending()
                except Exception as e:
                    logger.exception(f"Erreur dans la boucle du planificateur : {e}")
                time.sleep(1.0)
            logger.info("Boucle de fond du planificateur arrêtée.")

        self._thread = threading.Thread(target=loop_worker, name="eidolon-scheduler-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signals the background thread to stop cleanly."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None
