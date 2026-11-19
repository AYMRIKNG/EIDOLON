"""
Voice control of the Code window (core/ide_bridge.py): "EIDOLON, accepte",
"refuse, il ne faut pas toucher à ce fichier", "qu'est-ce que l'IA a modifié ?".
"""
from __future__ import annotations

from core.ide_bridge import get_bridge


def code_review(parameters: dict = None, player=None, **kwargs) -> str:
    params = parameters or {}
    action = (params.get("action") or "list").lower().strip()
    bridge = get_bridge()
    pending = bridge.pending()

    if action == "open":
        if player and hasattr(player, "_bridge"):
            player._bridge.sig_code_event.emit("open", "")
        return "Panneau code ouvert dans le HUD."

    if action == "set_level":
        level = (params.get("level") or "").lower().strip()
        if not bridge.set_level(level):
            return "Niveaux possibles : strict, balanced, commands, off."
        from core.ide_bridge import LEVELS
        return f"Niveau de validation : {LEVELS[level]}."

    if action == "list":
        if not pending:
            return "Aucune modification de code en attente."
        lines = [f"{len(pending)} modification(s) en attente :"]
        for c in pending:
            a, r = c.stats()
            what = f"commande « {c.command[:60]} »" if c.command else f"{c.rel}, +{a} −{r}"
            lines.append(f"- {what} ({c.source}, "
                         f"{'attend votre accord' if c.mode == 'ask' else 'déjà appliquée'})")
        return "\n".join(lines)

    if not pending:
        return "Il n'y a aucune modification à valider."
    target = pending[0]
    name = (params.get("file") or "").lower().strip()
    if name:
        target = next((c for c in pending if name in c.rel.lower()), None)
        if not target:
            return f"Aucune modification en attente pour « {name} »."

    if action == "explain":
        return bridge.explain(target) + (
            "\n\nExplique simplement à l'utilisateur ce que ça change et s'il y a un risque. "
            "N'accepte ni ne refuse sans son ordre.")

    if action == "accept_all":
        return f"{bridge.accept_all()} modification(s) acceptée(s)."
    if action == "accept":
        bridge.accept(target.id)
        return f"{'Commande autorisée' if target.command else f'Modification de {target.rel} acceptée'}."
    if action == "reject":
        bridge.reject(target.id, params.get("reason") or "")
        return f"{'Commande refusée' if target.command else f'Modification de {target.rel} refusée'}."
    return f"Action inconnue : {action}"


TOOL = {
    "name": "code_review",
    "description": (
        "Valide ou refuse les modifications de fichiers faites ou proposées par une IA de code "
        "(Claude Code, Antigravity, Cursor…), affichées dans le panneau term du HUD d'EIDOLON. "
        "Utilise-le quand l'utilisateur dit « accepte », « refuse », « garde », « annule la modif » "
        "à propos du code, demande ce que l'IA a modifié, ce que fait une modification ou une "
        "commande en attente (action 'explain' : renvoie le diff ou la commande à expliquer), "
        "ou veut moins / plus de demandes de validation (action 'set_level')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["list", "explain", "accept", "reject", "accept_all", "set_level", "open"],
                "description": "list : modifications en attente. explain : contenu de la demande en "
                               "attente (diff ou commande) pour l'expliquer. accept / reject : la plus "
                               "récente (ou 'file'). accept_all : tout accepter. set_level : régler la "
                               "sensibilité. open : afficher le panneau.",
            },
            "file": {"type": "STRING", "description": "Optionnel : nom (ou partie du nom) du fichier visé."},
            "reason": {"type": "STRING", "description": "Pour reject : la raison, transmise à l'IA."},
            "level": {
                "type": "STRING",
                "enum": ["strict", "balanced", "commands", "off"],
                "description": "Pour set_level. strict : chaque fichier ; balanced : fichiers sensibles, "
                               "grosses modifs, suppressions et commandes risquées ; commands : seulement "
                               "les commandes risquées ; off : jamais.",
            },
        },
        "required": ["action"],
    },
    "handler": code_review,
}
