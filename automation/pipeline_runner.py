"""EIDOLON — Pipeline Execution Engine.

Executes sequential, chained workflows where the output payload of step N
becomes the input payload of step N+1.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from automation.tool_registry import ToolRegistry, global_tool_registry

logger = logging.getLogger("EidolonPipeline")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [PIPELINE] %(message)s",
        datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


@dataclass
class StepResult:
    step_index: int
    service: str
    action: str
    params: Dict[str, Any]
    output_preview: str
    duration_seconds: float
    success: bool
    error: Optional[str] = None


@dataclass
class PipelineResult:
    workflow_id: str
    workflow_name: str
    success: bool
    final_payload: Any
    total_steps: int
    executed_steps: int
    duration_seconds: float
    step_results: List[StepResult] = field(default_factory=list)
    error: Optional[str] = None


class PipelineRunner:
    """Orchestrates async execution of pipeline workflows."""

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        self.registry = registry or global_tool_registry

    async def run_workflow(
        self,
        workflow_data: Union[Dict[str, Any], str, Path],
        initial_payload: Any = None
    ) -> PipelineResult:
        """Executes a defined workflow sequentially.
        
        Args:
            workflow_data: Either a dictionary containing the workflow definition,
                           or a file path / JSON string pointing to it.
            initial_payload: Optional initial input data for the first step.
            
        Returns:
            PipelineResult containing the final status, output, and execution history.
        """
        # Parse input if path or raw JSON string
        if isinstance(workflow_data, (str, Path)):
            p = Path(workflow_data)
            if p.is_file():
                with open(p, "r", encoding="utf-8") as f:
                    workflow = json.load(f)
            else:
                workflow = json.loads(str(workflow_data))
        else:
            workflow = workflow_data

        wf_id = workflow.get("id", "wf_unknown")
        wf_name = workflow.get("name", wf_id)
        pipeline = workflow.get("pipeline", [])

        if not isinstance(pipeline, list) or not pipeline:
            logger.error(f"Workflow '{wf_id}' contient une liste d'étapes invalide ou vide.")
            return PipelineResult(
                workflow_id=wf_id,
                workflow_name=wf_name,
                success=False,
                final_payload=None,
                total_steps=0,
                executed_steps=0,
                duration_seconds=0.0,
                error="Pipeline definition is empty or not a list"
            )

        total_steps = len(pipeline)
        current_payload: Any = initial_payload
        step_results: List[StepResult] = []
        start_time_all = time.perf_counter()

        logger.info(f"Démarrage du workflow '{wf_name}' ({wf_id}) — {total_steps} étapes prévues.")

        for idx, step in enumerate(pipeline, start=1):
            service = str(step.get("service", "")).strip().lower()
            action = str(step.get("action", "")).strip().lower()
            params = step.get("params", {}) or {}

            logger.info(f"Étape {idx}/{total_steps} en cours : [{service}.{action}]...")

            handler = self.registry.get(service, action)
            if handler is None:
                err_msg = f"Action non trouvée dans le registre : service='{service}', action='{action}'"
                logger.error(f"Étape {idx}/{total_steps} ÉCHEC : {err_msg}")
                step_results.append(StepResult(
                    step_index=idx,
                    service=service,
                    action=action,
                    params=params,
                    output_preview="N/A",
                    duration_seconds=0.0,
                    success=False,
                    error=err_msg
                ))
                return PipelineResult(
                    workflow_id=wf_id,
                    workflow_name=wf_name,
                    success=False,
                    final_payload=None,
                    total_steps=total_steps,
                    executed_steps=idx - 1,
                    duration_seconds=round(time.perf_counter() - start_time_all, 3),
                    step_results=step_results,
                    error=err_msg
                )

            step_start = time.perf_counter()
            try:
                # Call async action handler, passing previous payload and current step params
                output = await handler(current_payload, params)
                step_duration = round(time.perf_counter() - step_start, 3)

                preview = str(output)[:100] + ("..." if len(str(output)) > 100 else "")
                logger.info(f"Étape {idx}/{total_steps} TERMINÉE en {step_duration}s. Sortie : {preview}")

                step_results.append(StepResult(
                    step_index=idx,
                    service=service,
                    action=action,
                    params=params,
                    output_preview=preview,
                    duration_seconds=step_duration,
                    success=True
                ))

                # Pass output as input payload for next step
                current_payload = output

            except Exception as e:
                step_duration = round(time.perf_counter() - step_start, 3)
                err_msg = f"Exception pendant l'étape [{service}.{action}] : {type(e).__name__} - {e}"
                logger.exception(f"Étape {idx}/{total_steps} ERREUR : {err_msg}")

                step_results.append(StepResult(
                    step_index=idx,
                    service=service,
                    action=action,
                    params=params,
                    output_preview="ERREUR",
                    duration_seconds=step_duration,
                    success=False,
                    error=err_msg
                ))

                # Stop pipeline cleanly on step failure
                total_duration = round(time.perf_counter() - start_time_all, 3)
                logger.error(f"Arrêt prématuré du workflow '{wf_id}' après {idx} étape(s). Durée : {total_duration}s")
                return PipelineResult(
                    workflow_id=wf_id,
                    workflow_name=wf_name,
                    success=False,
                    final_payload=None,
                    total_steps=total_steps,
                    executed_steps=idx - 1,
                    duration_seconds=total_duration,
                    step_results=step_results,
                    error=err_msg
                )

        total_duration = round(time.perf_counter() - start_time_all, 3)
        logger.info(f"Workflow '{wf_name}' ({wf_id}) SUCCÈS COMPLET en {total_duration}s.")

        return PipelineResult(
            workflow_id=wf_id,
            workflow_name=wf_name,
            success=True,
            final_payload=current_payload,
            total_steps=total_steps,
            executed_steps=total_steps,
            duration_seconds=total_duration,
            step_results=step_results
        )


# Global helper function for convenience
async def run_workflow(
    workflow_data: Union[Dict[str, Any], str, Path],
    initial_payload: Any = None,
    registry: Optional[ToolRegistry] = None
) -> PipelineResult:
    """Helper wrapper to execute a workflow using the default PipelineRunner."""
    runner = PipelineRunner(registry=registry)
    return await runner.run_workflow(workflow_data, initial_payload=initial_payload)
