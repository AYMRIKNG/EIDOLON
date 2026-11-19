"""
actions/smart_file_manager.py — JARVIS Smart File and Folder Management Action.

Auto-discovered by core/action_loader.py.
Provides intelligent analysis, duplicate detection, rule-based classification,
project scaffolding, safe execution with collision protection, and undo.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.smart_organizer import (
    SmartFileAnalyzer,
    SmartClassificationPlanner,
    SecureSystemExecutor,
    OrganizationPlan,
    PlannedMove,
    is_path_safe,
    get_default_safe_roots,
    format_size
)

try:
    from core.undo import undo_last, can_undo, peek as undo_peek
    _HAS_UNDO = True
except ImportError:
    _HAS_UNDO = False

# Cache for recent generated plans awaiting confirmation: plan_id -> OrganizationPlan
_PENDING_PLANS: Dict[str, OrganizationPlan] = {}


def _resolve_shortcut(raw_path: str) -> Path:
    """Resolves Windows user directory shortcuts and relative paths."""
    home = Path.home()
    shortcuts = {
        "desktop": home / "Desktop",
        "downloads": home / "Downloads",
        "documents": home / "Documents",
        "pictures": home / "Pictures",
        "music": home / "Music",
        "videos": home / "Videos",
        "home": home,
    }
    raw = raw_path.strip().strip('"').strip("'")
    lower = raw.lower()
    if lower in shortcuts:
        return shortcuts[lower]

    # Handle shortcut/subfolder (e.g. "downloads/projets")
    normalized = raw.replace("\\", "/")
    head, sep, rest = normalized.partition("/")
    if sep and head.lower() in shortcuts:
        return shortcuts[head.lower()] / rest.strip("/")

    return Path(raw).expanduser().resolve()


def smart_file_manager(
    parameters: dict = None,
    player=None,
    speak=None,
    response=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "analyze").lower().strip()
    raw_path = params.get("path", "downloads")
    strategy = params.get("strategy", "by_type").lower().strip()
    confirmed = params.get("confirmed", False)
    plan_id = params.get("plan_id", "").strip()

    target_dir = _resolve_shortcut(raw_path)

    if player:
        player.write_log(f"[smart_files] {action} on {target_dir}")

    analyzer = SmartFileAnalyzer()
    planner = SmartClassificationPlanner()
    executor = SecureSystemExecutor()

    try:
        # ── 1. Action: ANALYZE ──────────────────────────────────────────────
        if action in ("analyze", "scan", "inspect"):
            report = analyzer.analyze_folder(target_dir, detect_duplicates=True)
            if report.errors:
                return f"Erreur lors de l'analyse de {target_dir}:\n" + "\n".join(report.errors)

            lines = [
                f"📊 Rapport d'analyse pour : {target_dir.name}/ ({target_dir})",
                f"• Nombre total de fichiers : {report.total_files}",
                f"• Nombre de dossiers : {report.total_folders}",
                f"• Espace total occupé : {report.total_size_str}",
                "",
                "📂 Répartition par catégories :",
            ]
            for cat, count in sorted(report.categories_breakdown.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  - {cat} : {count} fichier(s)")

            if report.empty_folders:
                lines.append(f"\n🗑️ Dossiers vides détectés ({len(report.empty_folders)}) :")
                for ef in report.empty_folders[:5]:
                    lines.append(f"  - {Path(ef).name}/")
                if len(report.empty_folders) > 5:
                    lines.append(f"  ... et {len(report.empty_folders) - 5} autres.")

            if report.duplicate_groups:
                lines.append(f"\n⚠️ Doublons exacts détectés ({len(report.duplicate_groups)} groupe(s)) :")
                for dg in report.duplicate_groups[:3]:
                    names = [Path(p).name for p in dg["paths"]]
                    lines.append(f"  - Taille {dg['file_size']} : {', '.join(names)}")
                if len(report.duplicate_groups) > 3:
                    lines.append(f"  ... et {len(report.duplicate_groups) - 3} autres groupes.")

            if report.unclassified_files:
                lines.append(f"\n❓ Fichiers à examiner ({len(report.unclassified_files)}) :")
                for uf in report.unclassified_files[:3]:
                    lines.append(f"  - {Path(uf).name}")

            lines.append("\n💡 Pour organiser ce dossier, demandez : 'organise ce dossier par type' ou 'organise par date'.")
            return "\n".join(lines)

        # ── 2. Action: DUPLICATES ───────────────────────────────────────────
        elif action in ("duplicates", "find_duplicates", "doublons"):
            report = analyzer.analyze_folder(target_dir, detect_duplicates=True)
            if not report.duplicate_groups:
                return f"Aucun doublon exact détecté dans {target_dir.name}/ ({report.total_files} fichiers analysés)."

            # Build graphical resource cards for every duplicate file found
            res_items = []
            for idx, dg in enumerate(report.duplicate_groups, 1):
                for p_str in dg["paths"]:
                    p = Path(p_str)
                    res_items.append({
                        "type": "file",
                        "name": f"[Doublon G{idx}] {p.name}",
                        "target": str(p.resolve())
                    })

            # 1. Display graphical resource cards on overlay HUD
            if player and hasattr(player, "show_resources") and callable(player.show_resources):
                player.show_resources(res_items)

            # 2. Open confirmation panel on HUD with Accept / Reject buttons
            if player and hasattr(player, "show_confirm") and callable(player.show_confirm):
                total_dups = sum(len(dg["paths"]) - 1 for dg in report.duplicate_groups)
                player.show_confirm(
                    title="Doublons détectés",
                    detail=(
                        f"Voici la liste des doublons, monsieur. "
                        f"J'ai identifié {len(report.duplicate_groups)} groupes de fichiers identiques ({total_dups} copie(s) superflue(s)). "
                        "Que voulez-vous faire : les supprimer d'un coup, ou préférez-vous les examiner individuellement ?"
                    ),
                    accept_label="✓ Supprimer d'un coup",
                    reject_label="✕ Examiner individuellement",
                    accept_cmd="Supprimer les doublons d'un coup en conservant un seul original par groupe. Confirme-moi vocalement à haute voix quand c'est fait.",
                    reject_cmd="Examiner les doublons individuellement",
                    items=res_items
                )

            lines = [
                f"🔍 Doublons exacts dans {target_dir.name}/ :",
                f"Total : {len(report.duplicate_groups)} groupe(s) de fichiers identiques (SHA-256).",
                ""
            ]
            for idx, dg in enumerate(report.duplicate_groups, 1):
                lines.append(f"Groupe {idx} ({dg['file_size']} chacun) :")
                for p in dg["paths"]:
                    lines.append(f"  • {p}")
            lines.append("\n💡 Les cartes graphiques de ces fichiers ont été affichées sur l'interface.")
            lines.append("Vous pouvez cliquer sur 'Supprimer d'un coup' pour nettoyer ou 'Examiner' pour les ouvrir.")
            return "\n".join(lines)

        # ── 3. Action: SEARCH ───────────────────────────────────────────────
        elif action in ("search", "find", "chercher"):
            query = params.get("query", "")
            ext = params.get("extension", "")
            cat = params.get("category", "")
            content_q = params.get("content_query", "")

            results = analyzer.search_files(
                folder_path=target_dir,
                query=query,
                extension=ext,
                category=cat,
                content_query=content_q,
                max_results=30
            )

            if not results:
                return f"Aucun fichier correspondant trouvé dans {target_dir.name}/."

            lines = [f"🔍 Résultats de recherche dans {target_dir.name}/ ({len(results)} trouvés) :"]
            for r in results:
                if "error" in r:
                    return f"Erreur : {r['error']}"
                lines.append(f"  📄 {r['name']} ({r['size']}, {r['category']}) — {r['path']}")
            return "\n".join(lines)

        # ── 4. Action: PLAN_ORGANIZE ────────────────────────────────────────
        elif action in ("plan", "plan_organize", "simuler"):
            plan = planner.generate_plan(
                folder_path=target_dir,
                strategy=strategy,
                clean_empty_folders=True
            )
            _PENDING_PLANS[plan.plan_id] = plan

            if not plan.planned_moves and not plan.empty_folders_to_prune:
                return f"Le dossier {target_dir.name}/ est déjà parfaitement ordonné selon la stratégie '{strategy}'."

            lines = [
                f"📋 Plan d'organisation proposé pour : {target_dir.name}/",
                f"• Stratégie : {strategy}",
                f"• ID du plan : {plan.plan_id}",
                f"• Déplacements prévus : {len(plan.planned_moves)} fichier(s)",
                f"• Dossiers cibles à créer : {len(plan.directories_to_create)} ({', '.join(Path(d).name for d in plan.directories_to_create[:5])})",
                f"• Dossiers vides à nettoyer : {len(plan.empty_folders_to_prune)}",
                "",
                "Aperçu des mouvements :"
            ]
            for m in plan.planned_moves[:6]:
                conflict_note = " [Conflit résolu par suffixe]" if m.conflict_detected else ""
                lines.append(f"  - {Path(m.source_path).name} ➔ {m.category}/{Path(m.dest_path).name}{conflict_note}")
            if len(plan.planned_moves) > 6:
                lines.append(f"  ... et {len(plan.planned_moves) - 6} autres déplacements.")

            lines.append("\n⚠️ Sécurité : Aucun fichier n'a encore été modifié.")
            lines.append("Pour confirmer l'exécution, dites : 'Jarvis, confirme le rangement'.")
            return "\n".join(lines)

        # ── 5. Action: EXECUTE ──────────────────────────────────────────────
        elif action in ("execute", "organize", "ranger", "classer"):
            # Check if a plan already exists or generate a fresh one
            plan = _PENDING_PLANS.get(plan_id) if plan_id else None
            if plan is None:
                plan = planner.generate_plan(folder_path=target_dir, strategy=strategy, clean_empty_folders=True)

            if not plan.planned_moves and not plan.empty_folders_to_prune:
                return f"Aucun réarrangement nécessaire pour {target_dir.name}/ (déjà classé)."

            # Safety gate: if more than 50 files and not confirmed, ask for confirmation
            if len(plan.planned_moves) > 50 and not confirmed:
                _PENDING_PLANS[plan.plan_id] = plan
                return (
                    f"⚠️ Opération massive détectée ({len(plan.planned_moves)} fichiers à déplacer dans {target_dir.name}/). "
                    f"Plan généré : {plan.plan_id}. Veuillez confirmer explicitement avant exécution."
                )

            # Execute
            res = executor.execute_plan(plan, on_conflict="rename", prune_empty=True)
            if not res["success"]:
                return f"Erreur lors de l'exécution du plan : {res.get('error', 'Erreur inconnue')}"

            # Remove from pending
            _PENDING_PLANS.pop(plan.plan_id, None)

            vocal_summary = (
                f"C'est fait, monsieur. Le rangement de {target_dir.name} est terminé. "
                f"{res['moved_count']} fichiers ont été organisés."
            )
            lines = [
                f"[CONFIRMATION_VOCALE_OBLIGATOIRE : Tu dois impérativement t'adresser à voix haute à l'utilisateur avec ta voix pour lui dire : \"{vocal_summary}\"]\n",
                f"✅ Organisation terminée pour : {target_dir.name}/",
                f"• Fichiers déplacés avec succès : {res['moved_count']}",
                f"• Dossiers vides supprimés : {res['pruned_folders_count']}",
            ]
            if res.get("skipped_count"):
                lines.append(f"• Fichiers ignorés : {res['skipped_count']}")
            if res.get("details"):
                lines.append("\nExemples de déplacements :")
                for d in res["details"][:5]:
                    lines.append(f"  • {d}")

            lines.append("\n↩️ Vous pouvez annuler cette opération à tout moment avec 'Jarvis, annule le rangement'.")
            return "\n".join(lines)

        # ── 6. Action: CREATE_PROJECT ───────────────────────────────────────
        elif action in ("create_project", "nouveau_projet", "scaffold"):
            project_name = params.get("project_name", "Nouveau_Projet").strip()
            project_dir = target_dir / project_name
            safe, err = is_path_safe(project_dir)
            if not safe:
                return f"Création refusée : {err}"

            structure = [
                "docs",
                "src",
                "data/raw",
                "data/processed",
                "output",
                "archive"
            ]
            created = []
            for sub in structure:
                sub_path = project_dir / sub
                sub_path.mkdir(parents=True, exist_ok=True)
                created.append(sub)

            readme = project_dir / "README.md"
            if not readme.exists():
                readme.write_text(f"# {project_name}\n\nCréé par JARVIS le {datetime.now().strftime('%Y-%m-%d %H:%M')}.\n", encoding="utf-8")

            vocal_summary = f"C'est fait, monsieur. La structure du projet {project_name} est créée."
            return (
                f"[CONFIRMATION_VOCALE_OBLIGATOIRE : Tu dois impérativement t'adresser à voix haute à l'utilisateur avec ta voix pour lui dire : \"{vocal_summary}\"]\n\n"
                f"📁 Structure de projet créée avec succès dans : {project_dir}\n"
                f"Sous-dossiers créés : {', '.join(created)}\n"
                f"Fichier initialisé : README.md"
            )

        # ── 7. Action: CLEAN_EMPTY ──────────────────────────────────────────
        elif action in ("clean_empty", "nettoyer_dossiers_vides"):
            report = analyzer.analyze_folder(target_dir, detect_duplicates=False)
            if not report.empty_folders:
                return f"Aucun dossier vide trouvé dans {target_dir.name}/."

            pruned = []
            for ef in report.empty_folders:
                try:
                    p = Path(ef)
                    if p.exists() and p.is_dir() and not any(p.iterdir()):
                        p.rmdir()
                        pruned.append(p.name)
                except Exception:
                    pass

            vocal_summary = f"C'est fait, monsieur. J'ai nettoyé les {len(pruned)} dossiers vides dans {target_dir.name}."
            return (
                f"[CONFIRMATION_VOCALE_OBLIGATOIRE : Tu dois impérativement t'adresser à voix haute à l'utilisateur avec ta voix pour lui dire : \"{vocal_summary}\"]\n\n"
                f"🧹 {len(pruned)} dossier(s) vide(s) nettoyé(s) dans {target_dir.name}/ : {', '.join(pruned)}"
            )

        # ── 8. Action: CLEAN_DUPLICATES ─────────────────────────────────────
        elif action in ("clean_duplicates", "delete_duplicates", "supprimer_doublons"):
            keep_policy = params.get("keep", "first").lower().strip()
            report = analyzer.analyze_folder(target_dir, detect_duplicates=True)
            if not report.duplicate_groups:
                return f"Aucun doublon trouvé à supprimer dans {target_dir.name}/."

            res = executor.clean_duplicates(
                duplicate_groups=report.duplicate_groups,
                keep=keep_policy
            )
            if not res["success"]:
                return f"Erreur lors de la suppression des doublons : {res.get('error', 'Inconnue')}"

            vocal_summary = (
                f"C'est fait, monsieur. J'ai supprimé les {res['deleted_count']} copies en double et "
                f"conservé un original par groupe. {res['freed_size_str']} ont été libérés."
            )
            lines = [
                f"[CONFIRMATION_VOCALE_OBLIGATOIRE : Tu dois impérativement t'adresser à voix haute à l'utilisateur avec ta voix pour lui dire : \"{vocal_summary}\"]\n",
                f"🗑️ Nettoyage des doublons terminé pour : {target_dir.name}/",
                f"• Groupes traités : {res['groups_count']}",
                f"• Exemplaires originaux conservés : {res['kept_count']}",
                f"• Copies redondantes supprimées : {res['deleted_count']}",
                f"• Espace disque libéré : {res['freed_size_str']}",
            ]
            if res.get("details"):
                lines.append("\nExemples de copies supprimées (mises en quarantaine réversible) :")
                for d in res["details"][:5]:
                    lines.append(f"  • {d}")

            lines.append("\n↩️ Vous pouvez annuler cette suppression à tout moment avec 'Jarvis, annule'.")
            return "\n".join(lines)

        # ── 9. Action: UNDO ─────────────────────────────────────────────────
        elif action in ("undo", "annuler"):
            if not _HAS_UNDO or not can_undo():
                return "Aucune opération réversible n'est enregistrée dans la pile d'annulation."
            status = undo_last()
            vocal_summary = "Opération annulée, monsieur. Vos fichiers ont été restaurés à leur emplacement d'origine."
            return (
                f"[CONFIRMATION_VOCALE_OBLIGATOIRE : Tu dois impérativement t'adresser à voix haute à l'utilisateur avec ta voix pour lui dire : \"{vocal_summary}\"]\n\n"
                f"↩️ {status}"
            )

        else:
            return f"Action inconnue pour smart_file_manager : '{action}'"

    except Exception as e:
        return f"Erreur dans smart_file_manager ({action}): {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "smart_file_manager",
    "description": (
        "Gestionnaire et organisateur intelligent de fichiers et dossiers sous Windows. "
        "Permet d'analyser l'arborescence, rechercher des documents par nom/extension/contenu, "
        "détecter les doublons exacts, supprimer les copies de doublons en conservant 1 original, "
        "planifier un rangement propre par type/date/projet sans rien casser, "
        "exécuter le classement avec gestion anti-collision et annulation (undo), "
        "nettoyer les dossiers vides, et créer des structures de projets standardisées."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "analyze: analyse complète et statistiques | "
                    "plan: simule le classement sans déplacer aucun fichier | "
                    "execute: exécute le rangement | "
                    "duplicates: recherche uniquement les doublons exacts | "
                    "clean_duplicates: supprime les doublons en conservant un seul exemplaire original | "
                    "search: recherche multi-critères (nom, extension, contenu) | "
                    "clean_empty: supprime les répertoires vides | "
                    "create_project: crée une arborescence de projet standardisée | "
                    "undo: annule la dernière opération (rangement ou suppression)"
                )
            },
            "path": {
                "type": "STRING",
                "description": "Dossier cible ou raccourci : downloads, documents, desktop, pictures, videos, music, ou chemin absolu."
            },
            "strategy": {
                "type": "STRING",
                "description": "Stratégie de rangement : 'by_type' (Documents, Images, Code...), 'by_date' (Année/Mois), 'by_extension' (PDF, PNG...), 'by_project' (par préfixe de projet)."
            },
            "keep": {
                "type": "STRING",
                "description": "Pour clean_duplicates : 'first' (conserve le nom le plus propre), 'oldest' (le plus ancien), ou 'newest' (le plus récent)."
            },
            "query": {
                "type": "STRING",
                "description": "Terme de recherche dans le nom du fichier."
            },
            "extension": {
                "type": "STRING",
                "description": "Extension à rechercher ou filtrer (ex: .pdf, .jpg)."
            },
            "content_query": {
                "type": "STRING",
                "description": "Terme à rechercher à l'intérieur du contenu textuel des fichiers."
            },
            "project_name": {
                "type": "STRING",
                "description": "Nom du projet pour l'action create_project."
            },
            "confirmed": {
                "type": "BOOLEAN",
                "description": "Confirmation explicite pour les opérations massives."
            },
            "plan_id": {
                "type": "STRING",
                "description": "ID d'un plan précédemment généré à exécuter."
            }
        },
        "required": ["action"]
    },
    "handler": smart_file_manager,
}
