"""EIDOLON — Automation Pipeline & Scheduler Demo.

Demonstrates initializing the tool registry, executing a chained pipeline,
and running the scheduler in a non-blocking background thread.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from automation.pipeline_runner import PipelineRunner
from automation.scheduler_manager import WorkflowScheduler
from automation.tool_registry import global_tool_registry


async def main():
    print("=" * 65)
    print("[EIDOLON] MOTEUR D'AUTOMATISATION & PIPELINES")
    print("=" * 65)

    # 1. Vérification du Registre d'Actions
    registered_actions = global_tool_registry.list_actions()
    print(f"\n[1] Registre d'actions initialise ({len(registered_actions)} actions enregistrees) :")
    for act in registered_actions:
        print(f"    - {act}")

    # 2. Chargement du workflow exemple
    workflow_path = Path("workflows/briefing_matin.json")
    if not workflow_path.exists():
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        sample_wf = {
            "id": "briefing_matin_001",
            "name": "Briefing matinal mails",
            "schedule": "08:00",
            "pipeline": [
                {"service": "gmail", "action": "fetch_unread", "params": {"max": 3}},
                {"service": "llm", "action": "summarize", "params": {"tone": "concise"}},
                {"service": "tts", "action": "generate_audio", "params": {"voice": "jarvis_fr"}}
            ]
        }
        workflow_path.write_text(json.dumps(sample_wf, indent=2), encoding="utf-8")

    print(f"\n[2] Chargement du fichier workflow : {workflow_path}")
    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow_def = json.load(f)
    print(f"    Nom : {workflow_def.get('name')}")
    print(f"    Planification : {workflow_def.get('schedule')}")
    print(f"    Nombre d'etapes : {len(workflow_def.get('pipeline', []))}")

    # 3. Exécution directe en pipeline (Chaînage de payload étape par étape)
    print("\n[3] Execution directe de la chaine de taches (PipelineRunner)...")
    runner = PipelineRunner(registry=global_tool_registry)
    result = await runner.run_workflow(workflow_def)

    print("\n--- Resultat de l'execution ---")
    print(f"Succes : {result.success}")
    print(f"Duree totale : {result.duration_seconds}s")
    print(f"Etapes completees : {result.executed_steps}/{result.total_steps}")
    print(f"Payload final (sortie TTS) :\n  -> {result.final_payload}")

    # 4. Lancement du Planificateur en tâche de fond (Thread séparé)
    print("\n[4] Demarrage du WorkflowScheduler en arriere-plan...")
    scheduler = WorkflowScheduler(workflows_dir="workflows", runner=runner)

    def on_complete(res):
        print(f"\n[SCHEDULER NOTIFICATION] Workflow '{res.workflow_id}' termine ! Sortie : {res.final_payload}")

    scheduler.on_workflow_completed = on_complete
    scheduler.start()

    print("    Le planificateur tourne en tache de fond sans bloquer l'application.")
    print("    (Pour tester immediatement un declenchement : scheduler.trigger_workflow('briefing_matin_001'))")

    # Simulation : déclenchement manuel immédiat via le scheduler
    print("\n[5] Simulation d'un declenchement asynchrone via le scheduler...")
    scheduler.trigger_workflow("briefing_matin_001")

    # Laisser tourner brièvement pour observer la fin de la tâche asynchrone
    await asyncio.sleep(2.0)

    # Arrêt propre du planificateur
    print("\n[6] Arret propre du planificateur.")
    scheduler.stop()
    print("=" * 65)
    print("[SUCCESS] Demonstration terminee avec succes.")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
