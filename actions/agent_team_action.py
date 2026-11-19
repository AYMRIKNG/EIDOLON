"""
Voice entry to the agent team (core/agent_team.py).

The user says what they want; EIDOLON hands it to the team and keeps talking.
Every call returns at once — the work runs in the background and EIDOLON is
told through speak() when the deliverable is ready.
"""
from __future__ import annotations

from core.agent_team import get_team, Status


def agent_team(parameters: dict = None, player=None, speak=None, **kwargs) -> str:
    params = parameters or {}
    action = (params.get("action") or "start").lower().strip()
    text = (params.get("request") or params.get("text") or "").strip()
    mission_id = (params.get("mission_id") or "").strip()

    team = get_team()
    if speak:
        team.notifier = speak

    if action == "start":
        if not text:
            return "Demande à l'utilisateur ce qu'il veut que l'équipe produise."
        m = team.start(text, ask_questions=not params.get("skip_questions", False))
        _open(player, m.id)
        if m.status == Status.CLARIFYING:
            qs = "\n".join(f"- {q}" for q in m.questions)
            return (f"Mission {m.id} créée, en attente de cadrage. Pose ces questions à voix haute, "
                    f"simplement, puis appelle agent_team action='answer' avec ses réponses "
                    f"(ou answer avec request vide s'il veut lancer sans répondre) :\n{qs}")
        return (f"Mission {m.id} lancée en arrière-plan. Dis à l'utilisateur en une phrase que l'équipe "
                f"s'en occupe et que tu le préviendras quand le livrable sera prêt.")

    m = team.get(mission_id)
    if not m:
        return "Aucune mission d'équipe en cours."

    if action == "answer":
        if not team.answer(m.id, text):
            return f"La mission {m.id} n'attend pas de réponses (statut : {m.status_label()})."
        return "Réponses transmises, l'équipe est lancée. Dis-le en une phrase et rends la main."

    if action == "status":
        lines = [f"« {m.title} » — {m.status_label()}"]
        for a in m.assignments:
            lines.append(f"- {a.agent_id.upper()} ({a.status}) : {a.task}")
        if m.status == Status.DONE:
            lines.append(f"Résumé : {m.summary}")
        elif m.error:
            lines.append(f"Problème : {m.error}")
        return "\n".join(lines)

    if action == "read":
        if m.status != Status.DONE:
            return f"Le livrable n'est pas encore prêt ({m.status_label()})."
        return m.document[:6000]

    if action == "revise":
        if not text:
            return "Demande à l'utilisateur ce qu'il faut changer."
        if not team.revise(m.id, text):
            return f"Impossible de modifier maintenant ({m.status_label()})."
        return "Modification lancée. Dis-le en une phrase ; tu préviendras quand c'est prêt."

    if action == "cancel":
        return "Mission annulée." if team.cancel(m.id) else "Cette mission n'est plus en cours."

    if action == "open":
        _open(player, m.id)
        return "Fenêtre Équipe ouverte."

    return f"Action inconnue : {action}"


def _open(player, mission_id: str) -> None:
    if player and hasattr(player, "open_orchestrator"):
        try:
            player.open_orchestrator(mission_id)
        except Exception as e:
            print(f"[agent_team] could not open window: {e}")


TOOL = {
    "name": "agent_team",
    "description": (
        "Fait travailler l'équipe d'experts de l'utilisateur (ses profils : CFO, CMO, CTO, CLO, CISO, "
        "CSO, CPO, CRO, CAO, CCO, CDAO, CSCO) pour lui produire un vrai travail : business plan, "
        "stratégie, audit, contrat, plan marketing, étude… Utilise-le dès que la demande mérite "
        "l'expertise d'un ou plusieurs spécialistes. EIDOLON choisit lui-même les experts utiles, "
        "le travail tourne en arrière-plan et un document final est produit. "
        "Passe la demande complète de l'utilisateur dans 'request', avec ses mots et ses chiffres."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["start", "answer", "status", "read", "revise", "cancel", "open"],
                "description": (
                    "start : nouvelle mission. answer : réponses aux questions de cadrage. "
                    "status : où en est l'équipe. read : lire le livrable. "
                    "revise : modifier le livrable terminé. cancel : arrêter. open : afficher la fenêtre."
                ),
            },
            "request": {
                "type": "STRING",
                "description": "La demande (start), les réponses (answer) ou la modification (revise).",
            },
            "mission_id": {
                "type": "STRING",
                "description": "Optionnel — par défaut la mission la plus récente.",
            },
            "skip_questions": {
                "type": "BOOLEAN",
                "description": "true si l'utilisateur veut lancer tout de suite sans questions.",
            },
        },
        "required": ["action"],
    },
    "handler": agent_team,
}
