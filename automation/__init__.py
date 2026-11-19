"""EIDOLON — Automation Package."""
from automation.tool_registry import ToolRegistry, global_tool_registry
from automation.pipeline_runner import PipelineRunner, PipelineResult, run_workflow
from automation.scheduler_manager import WorkflowScheduler

__all__ = [
    "ToolRegistry",
    "global_tool_registry",
    "PipelineRunner",
    "PipelineResult",
    "run_workflow",
    "WorkflowScheduler",
]
