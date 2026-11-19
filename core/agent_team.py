"""
EIDOLON agent team — you ask, EIDOLON runs the project, your experts do the work.

How a mission goes:

    brief      you say what you want ("prépare-moi un business plan pour…")
    cadrage    EIDOLON checks whether something essential is missing and, if so,
               asks you 2-3 questions. Judged by the model, not by keywords.
    plan       EIDOLON reads the roster of YOUR profiles (agents/registry.json)
               and picks only the experts the job needs, each with a concrete
               task and the colleagues' work it builds on.
    travail    the experts work in the background, with their full profile as
               system prompt. A task that needs fresh facts gets a grounded web
               search first. Several experts run at once when nothing links them.
    revue      a reviewer reads every contribution against the brief and sends
               back what is wrong or contradictory — one correction round.
    livrable   one document, in the form you asked for, written from the
               contributions. Saved as .md and .docx under output/missions/.

Nothing here blocks the voice session: every mission runs on one background
thread with its own event loop, and the result is announced through the
notifier (EIDOLON's speak()) when it is ready. The window and the voice tool
both read the same Mission objects, pushed through the agent event bus.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.agent_events import get_event_bus
from core.agent_registry import get_agent_registry, AgentProfile

BASE_DIR = Path(__file__).resolve().parent.parent
MISSIONS_DIR = BASE_DIR / "output" / "missions"

MAX_ASSIGNMENTS = 6
PARALLEL_AGENTS = 3
AGENT_TIMEOUT_MS = 180_000
WRITER_TIMEOUT_MS = 240_000


# ── Mission state ────────────────────────────────────────────────────────────

class Status:
    CLARIFYING = "clarifying"   # waiting for your answers
    PLANNING = "planning"
    WORKING = "working"
    REVIEWING = "reviewing"
    WRITING = "writing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    ACTIVE = (PLANNING, WORKING, REVIEWING, WRITING)
    LABELS = {
        CLARIFYING: "En attente de vos réponses",
        PLANNING: "EIDOLON compose l'équipe",
        WORKING: "L'équipe travaille",
        REVIEWING: "Relecture croisée",
        WRITING: "Rédaction du livrable",
        DONE: "Livrable prêt",
        FAILED: "Échec",
        CANCELLED: "Annulée",
    }


@dataclass
class Assignment:
    id: str
    agent_id: str
    task: str
    needs: List[str] = field(default_factory=list)  # ids of assignments it builds on
    research: str = ""                               # web query, empty when none
    status: str = "waiting"                          # waiting | working | done | failed
    output: str = ""
    revision: str = ""                               # reviewer's or your correction
    error: str = ""
    started_at: str = ""
    finished_at: str = ""


@dataclass
class FeedItem:
    at: str
    who: str        # "eidolon", "vous" or an agent id
    text: str


@dataclass
class Mission:
    id: str
    brief: str
    title: str = ""
    deliverable: str = ""                 # what the final document is
    status: str = Status.PLANNING
    questions: List[str] = field(default_factory=list)
    answers: str = ""
    assignments: List[Assignment] = field(default_factory=list)
    review: str = ""
    document: str = ""                    # final markdown
    summary: str = ""                     # 2-3 sentences for the voice
    files: List[str] = field(default_factory=list)
    feed: List[FeedItem] = field(default_factory=list)
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    # ── helpers ──
    def get(self, assignment_id: str) -> Optional[Assignment]:
        return next((a for a in self.assignments if a.id == assignment_id), None)

    def progress(self) -> tuple[int, int]:
        done = sum(1 for a in self.assignments if a.status == "done")
        return done, len(self.assignments)

    def status_label(self) -> str:
        label = Status.LABELS.get(self.status, self.status)
        if self.status == Status.WORKING and self.assignments:
            done, total = self.progress()
            label += f" — {done}/{total}"
        return label

    def full_brief(self) -> str:
        if not self.answers:
            return self.brief
        return f"{self.brief}\n\nPrécisions de l'utilisateur :\n{self.answers}"

    @property
    def folder(self) -> Path:
        return MISSIONS_DIR / self.id

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "mission.json").write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Mission":
        d = json.loads(path.read_text(encoding="utf-8"))
        d["assignments"] = [Assignment(**a) for a in d.get("assignments", [])]
        d["feed"] = [FeedItem(**f) for f in d.get("feed", [])]
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


class _Cancelled(Exception):
    pass


# ── LLM plumbing ─────────────────────────────────────────────────────────────

def _llm(prompt: str, system: str = "", smart: bool = True,
         timeout_ms: int = 60_000, temperature: float = 0.4) -> str:
    from core import gemini
    from google.genai import types as gtypes
    cfg = gtypes.GenerateContentConfig(
        system_instruction=system or None, temperature=temperature)
    return gemini.text(prompt, gemini.SMART if smart else gemini.FAST, cfg, timeout_ms)


def _llm_json(prompt: str, system: str = "", smart: bool = True,
              timeout_ms: int = 60_000):
    from core import gemini
    from google.genai import types as gtypes
    cfg = gtypes.GenerateContentConfig(
        system_instruction=system or None, temperature=0.2)
    return gemini.as_json(prompt, gemini.SMART if smart else gemini.FAST, cfg, timeout_ms)


def _web_research(query: str) -> str:
    """Grounded search for an expert who needs current facts. Empty on failure."""
    from core import gemini
    try:
        resp = gemini.call(
            f"Recherche des informations factuelles, récentes et chiffrées sur : {query}\n"
            f"Réponds en français, en points concis, avec les sources quand tu les as.",
            tier=gemini.SEARCH,
            config={"tools": [{"google_search": {}}]},
            timeout_ms=45_000)
        return (getattr(resp, "text", None) or "").strip() if resp else ""
    except Exception as e:
        print(f"[AgentTeam] research failed: {e}")
        return ""


def _roster(agents: List[AgentProfile]) -> str:
    lines = []
    for a in agents:
        domains = "; ".join(d.split(":")[0].strip() for d in a.domains_of_expertise[:5])
        lines.append(f"- {a.id} ({a.acronym}, {a.name}) : {a.specialty}. Domaines : {domains}")
    return "\n".join(lines)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    return re.sub(r"[\s_-]+", "-", s).strip("-")[:48] or "livrable"


# ── Engine ───────────────────────────────────────────────────────────────────

class AgentTeam:
    """Owns every mission and the background loop that runs them."""

    def __init__(self):
        self.registry = get_agent_registry()
        self.bus = get_event_bus()
        self.notifier: Optional[Callable[[str], None]] = None
        self._missions: Dict[str, Mission] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True,
                         name="agent-team").start()
        self._load_history()

    # ── public API (thread-safe, never blocks on the model except clarify) ──

    def start(self, brief: str, ask_questions: bool = True) -> Mission:
        """Create a mission. When `ask_questions` and something essential is
        missing, it stops in CLARIFYING with `questions` filled; otherwise the
        team starts right away in the background."""
        brief = brief.strip()
        m = Mission(id=datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4],
                    brief=brief, title=brief[:70])
        with self._lock:
            self._missions[m.id] = m
        self._say(m, "vous", brief)

        if ask_questions:
            q = self._clarify(m)
            if q:
                m.questions = q
                m.status = Status.CLARIFYING
                self._say(m, "eidolon", "Avant de lancer l'équipe, j'ai besoin de quelques précisions.")
                self._push(m)
                return m

        self._launch(m)
        return m

    def answer(self, mission_id: str, answers: str) -> Optional[Mission]:
        """Your answers to the cadrage questions (or empty to go without)."""
        m = self.get(mission_id)
        if not m or m.status != Status.CLARIFYING:
            return None
        m.answers = answers.strip()
        if m.answers:
            self._say(m, "vous", m.answers)
        self._launch(m)
        return m

    def revise(self, mission_id: str, instruction: str) -> Optional[Mission]:
        """A change request on a finished deliverable. EIDOLON decides which
        experts have to redo their part, then the document is rewritten."""
        m = self.get(mission_id)
        if not m or m.status in Status.ACTIVE or m.status == Status.CLARIFYING:
            return None
        self._say(m, "vous", instruction)
        self._cancelled.discard(m.id)
        m.error = ""
        m.status = Status.WORKING
        self._push(m)
        asyncio.run_coroutine_threadsafe(self._run_revision(m, instruction), self._loop)
        return m

    def cancel(self, mission_id: str) -> bool:
        m = self.get(mission_id)
        if not m or m.status in (Status.DONE, Status.FAILED, Status.CANCELLED):
            return False
        self._cancelled.add(m.id)
        m.status = Status.CANCELLED
        for a in m.assignments:
            if a.status == "working":
                a.status = "waiting"
        self._say(m, "eidolon", "Mission annulée.")
        self._push(m)
        return True

    def delete(self, mission_id: str) -> bool:
        m = self.get(mission_id)
        if not m or m.status in Status.ACTIVE:
            return False
        with self._lock:
            self._missions.pop(m.id, None)
        try:
            import shutil
            shutil.rmtree(m.folder, ignore_errors=True)
        except Exception:
            pass
        self.bus.emit("mission.deleted", mission_id=m.id)
        return True

    def get(self, mission_id: str = "") -> Optional[Mission]:
        """A mission by id, or the most recent one when the id is empty."""
        with self._lock:
            if mission_id:
                return self._missions.get(mission_id)
            ms = self.missions()
            return ms[0] if ms else None

    def missions(self) -> List[Mission]:
        with self._lock:
            return sorted(self._missions.values(), key=lambda m: m.created_at, reverse=True)

    def agent(self, agent_id: str) -> Optional[AgentProfile]:
        return self.registry.get(agent_id)

    # ── steps ──

    def _clarify(self, m: Mission) -> List[str]:
        data = _llm_json(
            f"Demande de l'utilisateur :\n{m.brief}\n\n"
            f"Équipe d'experts disponible :\n{_roster(self.registry.list_all())}\n\n"
            "Tu es EIDOLON, chef de projet. Décide si une information INDISPENSABLE manque pour "
            "produire un travail utile (montant, cible, objectif chiffré, contexte légal, etc.). "
            "Ne pose pas de question si on peut avancer avec des hypothèses raisonnables.\n"
            'Réponds en JSON : {"title": "titre court du projet", '
            '"questions": ["question 1", ...]} — 0 à 3 questions, courtes, à poser à voix haute.',
            smart=False, timeout_ms=20_000) or {}
        if isinstance(data, dict):
            if data.get("title"):
                m.title = str(data["title"])[:80]
            return [str(q) for q in (data.get("questions") or [])][:3]
        return []

    def _launch(self, m: Mission) -> None:
        m.status = Status.PLANNING
        self._push(m)
        asyncio.run_coroutine_threadsafe(self._run(m), self._loop)

    async def _run(self, m: Mission) -> None:
        try:
            await self._plan(m)
            await self._work(m)
            await self._review(m)
            await self._write(m)
            self._finish(m)
        except _Cancelled:
            pass
        except Exception as e:
            traceback.print_exc()
            self._fail(m, str(e))

    async def _run_revision(self, m: Mission, instruction: str) -> None:
        try:
            ids = ", ".join(f"{a.id} ({a.agent_id.upper()} : {a.task[:80]})" for a in m.assignments)
            data = await asyncio.to_thread(
                _llm_json,
                f"Livrable actuel (résumé) : {m.summary}\nTâches de l'équipe : {ids}\n\n"
                f"Demande de modification de l'utilisateur : {instruction}\n\n"
                'Quelles tâches doivent être refaites ? JSON : {"redo": ["a1", ...]} '
                "(liste vide si seule la rédaction finale doit changer).",
                "", False, 20_000) or {}
            redo = [m.get(i) for i in (data.get("redo") or []) if m.get(i)] if isinstance(data, dict) else []
            m.answers = (m.answers + "\n" if m.answers else "") + f"Modification demandée : {instruction}"
            if redo:
                self._say(m, "eidolon", "Je renvoie le travail à " +
                          ", ".join(a.agent_id.upper() for a in redo) + ".")
                for a in redo:
                    a.status, a.revision = "waiting", instruction
                await self._work(m)
            await self._write(m, change=instruction)
            self._finish(m)
        except _Cancelled:
            pass
        except Exception as e:
            traceback.print_exc()
            self._fail(m, str(e))

    async def _plan(self, m: Mission) -> None:
        self._check(m)
        self._say(m, "eidolon", "J'analyse la demande et je compose l'équipe.")
        agents = self.registry.list_all()
        data = await asyncio.to_thread(
            _llm_json,
            f"DEMANDE :\n{m.full_brief()}\n\n"
            f"TES EXPERTS (n'utilise QUE ces identifiants) :\n{_roster(agents)}\n\n"
            "Tu es EIDOLON, chef de projet. Construis le plan de travail :\n"
            "- choisis UNIQUEMENT les experts réellement utiles (1 à "
            f"{MAX_ASSIGNMENTS}), pas toute l'équipe ;\n"
            "- donne à chacun une tâche concrète et précise (ce qu'il doit produire, avec quelles données) ;\n"
            "- \"needs\" liste les tâches dont il doit lire le résultat avant de commencer (sinon []) ;\n"
            "- \"research\" : requête web si la tâche demande des faits récents (prix, lois, marché), sinon \"\".\n"
            "- \"deliverable\" décrit le document final attendu par l'utilisateur (type, sections).\n"
            'JSON : {"title": "...", "deliverable": "...", "assignments": '
            '[{"id": "a1", "agent": "cfo", "task": "...", "needs": [], "research": ""}]}',
            "", True, 60_000)
        self._check(m)

        valid = {a.id for a in agents}
        plan: List[Assignment] = []
        if isinstance(data, dict):
            m.title = str(data.get("title") or m.title)[:80]
            m.deliverable = str(data.get("deliverable") or "")
            for i, raw in enumerate((data.get("assignments") or [])[:MAX_ASSIGNMENTS]):
                agent_id = str(raw.get("agent", "")).lower().strip()
                if agent_id not in valid:
                    continue
                plan.append(Assignment(
                    id=str(raw.get("id") or f"a{i + 1}"), agent_id=agent_id,
                    task=str(raw.get("task") or m.brief),
                    needs=[str(n) for n in raw.get("needs") or []],
                    research=str(raw.get("research") or "")))
        if not plan:  # model unavailable or unusable answer: keyword fallback
            for i, ag in enumerate(self.registry.match_for_task(m.full_brief(), top_k=3) or agents[:1]):
                plan.append(Assignment(id=f"a{i + 1}", agent_id=ag.id,
                                       task=f"Traiter la demande sous l'angle {ag.specialty}."))
        # Keep dependencies only on earlier tasks — guarantees no cycle.
        seen: set[str] = set()
        for a in plan:
            a.needs = [n for n in a.needs if n in seen]
            seen.add(a.id)
        m.assignments = plan
        m.status = Status.WORKING
        self._say(m, "eidolon", "Équipe constituée : " + ", ".join(
            f"{a.agent_id.upper()} ({a.task[:60]}…)" if len(a.task) > 60 else f"{a.agent_id.upper()} ({a.task})"
            for a in plan))
        self._push(m)

    async def _work(self, m: Mission) -> None:
        sem = asyncio.Semaphore(PARALLEL_AGENTS)
        while True:
            self._check(m)
            pending = [a for a in m.assignments if a.status == "waiting"]
            if not pending:
                break
            done_ids = {a.id for a in m.assignments if a.status in ("done", "failed")}
            ready = [a for a in pending if all(n in done_ids for n in a.needs)]
            if not ready:  # only possible with a broken plan — run the rest anyway
                ready = pending

            async def run(a: Assignment):
                async with sem:
                    await self._do_assignment(m, a)
            await asyncio.gather(*(run(a) for a in ready))
        if all(a.status == "failed" for a in m.assignments):
            raise RuntimeError("aucun expert n'a pu produire sa contribution")

    async def _do_assignment(self, m: Mission, a: Assignment) -> None:
        agent = self.registry.get(a.agent_id)
        if not agent:
            a.status, a.error = "failed", "profil introuvable"
            return
        a.status, a.started_at, a.error = "working", _now(), ""
        self._say(m, a.agent_id, f"Je commence : {a.task}")

        research = ""
        if a.research:
            research = await asyncio.to_thread(_web_research, a.research)
            self._check(m)

        inputs = []
        for n in a.needs:
            dep = m.get(n)
            if dep and dep.output:
                inputs.append(f"### Travail de {dep.agent_id.upper()} — {dep.task}\n{dep.output}")
        prompt = (
            f"PROJET : {m.title}\n\nDEMANDE DE L'UTILISATEUR :\n{m.full_brief()}\n\n"
            f"DOCUMENT FINAL PRÉVU : {m.deliverable or 'à définir par le chef de projet'}\n\n"
            f"TA TÂCHE (confiée par EIDOLON, chef de projet) :\n{a.task}\n\n"
            + (f"TRAVAIL DÉJÀ PRODUIT PAR TES COLLÈGUES :\n" + "\n\n".join(inputs) + "\n\n" if inputs else "")
            + (f"RECHERCHE WEB RÉCENTE :\n{research}\n\n" if research else "")
            + (f"TA VERSION PRÉCÉDENTE :\n{a.output}\n\nCORRECTION DEMANDÉE :\n{a.revision}\n\n"
               if a.revision and a.output else "")
            + "Produis TA contribution uniquement, pas le document final. Sois concret : chiffres, "
              "étapes, recommandations actionnables. Appuie-toi sur les données fournies ; si une "
              "donnée manque, pose une hypothèse explicite. Markdown, en français."
        )
        out = await asyncio.to_thread(_llm, prompt, agent.system_prompt, True, AGENT_TIMEOUT_MS, 0.4)
        self._check(m)
        if not out or len(out) < 40:
            a.status, a.error = "failed", "pas de réponse du modèle"
            self._say(m, a.agent_id, "Je n'ai pas pu produire ma contribution.")
        else:
            a.output, a.status, a.revision = out, "done", ""
            self._say(m, a.agent_id, "Ma contribution est prête.")
        a.finished_at = _now()
        self._push(m)

    async def _review(self, m: Mission) -> None:
        self._check(m)
        done = [a for a in m.assignments if a.status == "done"]
        if len(done) < 1:
            return
        m.status = Status.REVIEWING
        self._say(m, "eidolon", "Je relis le travail de l'équipe.")
        self._push(m)
        work = "\n\n".join(f"[{a.id}] {a.agent_id.upper()} — {a.task}\n{a.output[:6000]}" for a in done)
        data = await asyncio.to_thread(
            _llm_json,
            f"DEMANDE :\n{m.full_brief()}\n\nCONTRIBUTIONS :\n{work}\n\n"
            "Relis comme un chef de projet exigeant. Signale seulement les vrais problèmes : "
            "contradiction entre experts, chiffre incohérent, partie de la demande non traitée, "
            "hypothèse non justifiée. JSON : "
            '{"ok": true|false, "notes": "synthèse courte", '
            '"fixes": [{"id": "a1", "fix": "ce qu\'il doit corriger"}]}',
            "", True, 60_000)
        self._check(m)
        if not isinstance(data, dict):
            return
        m.review = str(data.get("notes") or "")
        fixes = [(m.get(str(f.get("id"))), str(f.get("fix") or "")) for f in data.get("fixes") or []]
        fixes = [(a, fx) for a, fx in fixes if a and a.status == "done" and fx][:3]
        if fixes:
            self._say(m, "eidolon", "Corrections demandées à " + ", ".join(
                f"{a.agent_id.upper()} : {fx}" for a, fx in fixes))
            for a, fx in fixes:
                a.status, a.revision = "waiting", fx
            m.status = Status.WORKING
            self._push(m)
            await self._work(m)
        elif m.review:
            self._say(m, "eidolon", m.review)

    async def _write(self, m: Mission, change: str = "") -> None:
        self._check(m)
        m.status = Status.WRITING
        self._say(m, "eidolon", "Je rédige le livrable final.")
        self._push(m)
        work = "\n\n".join(f"## Contribution {a.agent_id.upper()} — {a.task}\n{a.output}"
                           for a in m.assignments if a.status == "done")
        previous = f"\n\nVERSION PRÉCÉDENTE DU DOCUMENT :\n{m.document}\n\nMODIFICATION À APPLIQUER : {change}" \
            if change and m.document else ""
        doc = await asyncio.to_thread(
            _llm,
            f"DEMANDE :\n{m.full_brief()}\n\nDOCUMENT ATTENDU : {m.deliverable or 'le plus utile pour la demande'}\n\n"
            f"TRAVAIL DE L'ÉQUIPE :\n{work}{previous}\n\n"
            "Rédige LE document final demandé par l'utilisateur, prêt à l'emploi, en Markdown. "
            "Fusionne les contributions en un texte cohérent (pas un copier-coller par expert), "
            "garde tous les chiffres et recommandations utiles, tranche les contradictions. "
            "Commence par un titre '# ' puis un court résumé. Termine par les prochaines étapes concrètes.",
            "Tu es EIDOLON, chef de projet et rédacteur en chef. Tu écris en français, clair et professionnel.",
            True, WRITER_TIMEOUT_MS, 0.3)
        self._check(m)
        if not doc:
            raise RuntimeError("la rédaction finale n'a pas abouti")
        m.document = doc.strip()
        m.summary = await asyncio.to_thread(
            _llm,
            f"Résume ce document en 2 phrases parlées, pour l'annoncer à voix haute :\n{m.document[:8000]}",
            "", False, 20_000, 0.3) or ""
        m.files = self._export(m)

    def _finish(self, m: Mission) -> None:
        m.status = Status.DONE
        self._say(m, "eidolon", "Le livrable est prêt.")
        self._push(m)
        if self.notifier:
            try:
                self.notifier(
                    f"[Mission d'équipe terminée — « {m.title} »] Annonce-le à l'utilisateur en une ou deux "
                    f"phrases, dans sa langue : {m.summary or 'le livrable est prêt.'} "
                    f"Précise que le document est dans la fenêtre Équipe.")
            except Exception as e:
                print(f"[AgentTeam] notifier failed: {e}")

    def _fail(self, m: Mission, why: str) -> None:
        m.status, m.error = Status.FAILED, why
        self._say(m, "eidolon", f"La mission a échoué : {why}")
        self._push(m)
        if self.notifier:
            try:
                self.notifier(f"[Mission d'équipe en échec — « {m.title} »] Dis-le brièvement à l'utilisateur : {why}")
            except Exception:
                pass

    # ── output ──

    def _export(self, m: Mission) -> List[str]:
        m.folder.mkdir(parents=True, exist_ok=True)
        name = _slug(m.title)
        md = m.folder / f"{name}.md"
        md.write_text(m.document, encoding="utf-8")
        files = [str(md)]
        try:
            files.append(str(_markdown_to_docx(m.document, m.folder / f"{name}.docx")))
        except Exception as e:
            print(f"[AgentTeam] docx export skipped: {e}")
        return files

    # ── internals ──

    def _check(self, m: Mission) -> None:
        if m.id in self._cancelled:
            raise _Cancelled()

    def _say(self, m: Mission, who: str, text: str) -> None:
        m.feed.append(FeedItem(at=_now(), who=who, text=text))
        self._push(m)

    def _push(self, m: Mission) -> None:
        try:
            m.save()
        except Exception as e:
            print(f"[AgentTeam] save failed: {e}")
        self.bus.emit("mission.update", mission_id=m.id, state=m.status,
                      message=m.feed[-1].text if m.feed else "")

    def _load_history(self) -> None:
        if not MISSIONS_DIR.exists():
            return
        for f in MISSIONS_DIR.glob("*/mission.json"):
            try:
                m = Mission.load(f)
            except Exception as e:
                print(f"[AgentTeam] skipped {f}: {e}")
                continue
            if m.status in Status.ACTIVE:  # the app closed mid-mission
                m.status, m.error = Status.FAILED, "interrompue par la fermeture d'EIDOLON"
            self._missions[m.id] = m


def _markdown_to_docx(markdown: str, target: Path) -> Path:
    """Headings, bullets, numbered lists, bold and tables — enough for a report."""
    from docx import Document
    doc = Document()
    table_rows: List[List[str]] = []

    def flush_table():
        rows = [r for r in table_rows if not all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in r)]
        table_rows.clear()
        if not rows:
            return
        t = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
        t.style = "Table Grid"
        for i, r in enumerate(rows):
            for j, c in enumerate(r):
                t.cell(i, j).text = c.strip().replace("**", "")

    def add_runs(par, text):
        for k, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
            if part:
                par.add_run(part).bold = bool(k % 2)

    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            table_rows.append(s.strip("|").split("|"))
            continue
        flush_table()
        if not s:
            continue
        h = re.match(r"(#{1,4})\s+(.*)", s)
        if h:
            doc.add_heading(h.group(2).replace("**", ""), level=min(len(h.group(1)), 4) - 1 or 0)
        elif re.match(r"[-*•]\s+", s):
            add_runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^[-*•]\s+", "", s))
        elif re.match(r"\d+[.)]\s+", s):
            add_runs(doc.add_paragraph(style="List Number"), re.sub(r"^\d+[.)]\s+", "", s))
        elif re.fullmatch(r"-{3,}|\*{3,}", s):
            continue
        else:
            add_runs(doc.add_paragraph(), s)
    flush_table()
    doc.save(str(target))
    return target


_TEAM: Optional[AgentTeam] = None
_TEAM_LOCK = threading.Lock()


def get_team() -> AgentTeam:
    global _TEAM
    with _TEAM_LOCK:
        if _TEAM is None:
            _TEAM = AgentTeam()
        return _TEAM
