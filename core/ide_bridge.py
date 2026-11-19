"""
IDE bridge — see what a coding AI changes in your files, and say yes or no.

Two ways in, so it works with whatever you code with:

    hooks     Claude Code calls integrations/claude_code/eidolon_hook.py before
              every Edit / Write. The hook posts the proposed change here and
              WAITS: the change is shown in the review window and only reaches
              the disk if you accept it. Your refusal (and its reason) goes back
              to Claude. Every other tool call is reported too, so the window
              shows what the agent is doing (Read, Bash, …).

    watcher   For any other tool — Antigravity, Cursor, Copilot, a script — the
              watched folders are compared against a snapshot. A change appears
              after the fact; "Refuser" puts the previous content back and
              "Accepter" makes the new content the reference.

If EIDOLON is not running the hook gets no answer and steps aside, so Claude
Code behaves exactly as it does without it.

Everything runs on background threads. Listeners are told about every change
and must marshal to their own thread (the window does it with a Qt signal).
"""
from __future__ import annotations

import difflib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "ide_bridge.json"
HOOK_SCRIPT = BASE_DIR / "integrations" / "claude_code" / "eidolon_hook.py"

PORT = int(os.environ.get("EIDOLON_REVIEW_PORT") or os.environ.get("JARVIS_REVIEW_PORT") or "47321")
RUN_MARKER = BASE_DIR / "config" / f"ide_bridge.{PORT}.running"   # read by the hook: EIDOLON is up
DECISION_TIMEOUT = 570           # s — under the hook's own 600 s limit
POLL_SECONDS = 1.5
MAX_FILE_BYTES = 512 * 1024
MAX_FILES_PER_ROOT = 6000
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".idea",
             ".vs", "dist", "build", ".next", ".cache", ".mypy_cache", ".pytest_cache",
             ".gradle", "target", "out", "coverage", ".turbo", ".parcel-cache"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}

# Shell commands worth a yes/no before they run: they delete, publish, rewrite
# history or reach outside the machine. Everything else (ls, git status, tests…)
# runs untouched — asking about every command would make the panel noise.
# Extra patterns can be added under "risky_commands" in config/ide_bridge.json.
RISKY_COMMANDS = [
    r"\brm\s", r"\brmdir\b", r"\bdel\s", r"\berase\s", r"\brd\s+/s", r"Remove-Item",
    r"\bgit\s+push\b", r"\bgit\s+reset\s+--hard", r"\bgit\s+clean\b", r"\bgit\s+checkout\s+--\s",
    r"\bgit\s+restore\b", r"\bgit\s+branch\s+-D\b", r"\bgit\s+rebase\b", r"\bgit\s+stash\s+(drop|clear)",
    r"\bnpm\s+publish\b", r"\btwine\s+upload\b", r"\bcargo\s+publish\b",
    r"\bdocker\s+(rm|rmi|system\s+prune|volume\s+rm)\b", r"\bkubectl\s+(delete|apply)\b",
    r"\bterraform\s+(apply|destroy)\b", r"\b(drop|truncate)\s+(table|database|schema)\b",
    r"\bmkfs\b", r"\bformat\s+[a-z]:", r"\bshutdown\b", r"\bStop-Computer\b",
    r"(curl|wget|iwr|Invoke-WebRequest)[^|]*\|\s*(sh|bash|iex|python)", r"\bchmod\s+-R\b",
    r"\b(vercel|netlify)\b.*--prod", r"\bfirebase\s+deploy\b",
    r"\bgh\s+(pr\s+merge|release\s+create|repo\s+delete)\b",
    r"\bpip\s+uninstall\b", r"\bnpm\s+uninstall\s+-g\b",
]


# How much JARVIS asks you. Set from the term panel (⋯ → Niveau de validation)
# or by voice; stored in config/ide_bridge.json as "level".
LEVELS = {
    "strict":   "Tout valider — chaque fichier et chaque commande risquée",
    "balanced": "Équilibré — fichiers sensibles, grosses modifs, suppressions et commandes risquées",
    "commands": "Commandes seules — uniquement les commandes risquées",
    "off":      "Désactivé — rien à valider, simple suivi",
}
DEFAULT_LEVEL = "balanced"
BIG_CHANGE_LINES = 60           # balanced: more changed lines than this is worth a look

# Files whose change deserves a look even in balanced mode: secrets, config,
# dependencies, build / CI / deployment, database.
SENSITIVE_FILES = [
    r"(^|/)\.env", r"\.(pem|key|p12|pfx|crt)$", r"(^|/)(config|settings|secrets?)(/|\.|_)",
    r"(^|/)\.gitignore$", r"(^|/)(package|composer|cargo)\.json$", r"(^|/)Cargo\.toml$",
    r"(^|/)requirements[^/]*\.txt$", r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg)$",
    r"(^|/)(Dockerfile|docker-compose[^/]*|Makefile|Procfile)$", r"(^|/)\.github/", r"\.gitlab-ci",
    r"(^|/)(migrations?|alembic)/", r"\.sql$", r"(^|/)(terraform|k8s|helm|deploy|infra)/",
    r"\.(tf|tfvars)$", r"(^|/)(vercel|netlify|firebase)\.json$", r"\.claude/settings",
]


# ── Model ────────────────────────────────────────────────────────────────────

@dataclass
class Change:
    id: str
    source: str                  # "Claude Code", "Sur le disque"
    root: str                    # project folder
    path: str                    # absolute file path
    old: Optional[str]           # None = file did not exist
    new: Optional[str]           # None = file deleted
    mode: str                    # "ask" — not applied yet, waiting for you
                                 # "applied" — already on disk
    tool: str = ""
    command: str = ""            # for a shell command waiting for approval
    why: str = ""                # why it needs your look ("fichier sensible", …)
    status: str = "pending"      # pending | accepted | rejected | expired
    reason: str = ""             # your comment on a refusal
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def rel(self) -> str:
        try:
            return str(Path(self.path).relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return Path(self.path).name

    @property
    def project(self) -> str:
        return Path(self.root).name or self.root

    @property
    def kind(self) -> str:
        if self.command:
            return "command"
        if self.old is None:
            return "create"
        if self.new is None:
            return "delete"
        return "edit"

    def stats(self) -> tuple[int, int]:
        if self.command:
            return 0, 0
        added = removed = 0
        for line in difflib.ndiff((self.old or "").splitlines(), (self.new or "").splitlines()):
            if line.startswith("+ "):
                added += 1
            elif line.startswith("- "):
                removed += 1
        return added, removed


@dataclass
class Step:
    tool: str
    detail: str
    state: str = "running"      # running | done | failed
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class Session:
    id: str
    source: str
    root: str
    steps: List[Step] = field(default_factory=list)
    done: bool = False
    last_seen: float = field(default_factory=time.time)

    @property
    def project(self) -> str:
        return Path(self.root).name or self.root


def diff_rows(old: Optional[str], new: Optional[str], context: int = 3):
    """Rows for display: (old_no, new_no, kind, text) where kind is ' ', '-',
    '+' or '…' (a gap between hunks). Whole file when it is new or deleted."""
    a = (old or "").splitlines()
    b = (new or "").splitlines()
    rows = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    groups = list(sm.get_grouped_opcodes(context)) if (old is not None and new is not None) \
        else [[("replace", 0, len(a), 0, len(b))]]
    for gi, group in enumerate(groups):
        if gi:
            rows.append((None, None, "…", ""))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for k in range(i2 - i1):
                    rows.append((i1 + k + 1, j1 + k + 1, " ", a[i1 + k]))
                continue
            for k in range(i1, i2):
                rows.append((k + 1, None, "-", a[k]))
            for k in range(j1, j2):
                rows.append((None, k + 1, "+", b[k]))
    return rows


# ── Bridge ───────────────────────────────────────────────────────────────────

class IdeBridge:

    def __init__(self):
        self._lock = threading.RLock()
        self.changes: List[Change] = []
        self.sessions: Dict[str, Session] = {}
        self._listeners: List[Callable[[str, Optional[Change]], None]] = []
        self._snapshots: Dict[str, Dict[str, tuple]] = {}   # root -> path -> (mtime, size, text)
        self._expected: Dict[str, tuple] = {}               # path -> (text, deadline) from approved hooks
        self._server: Optional[ThreadingHTTPServer] = None
        self.watch_roots: List[str] = self._load_roots()
        self.level: str = self._load_config().get("level", DEFAULT_LEVEL)
        if self.level not in LEVELS:
            self.level = DEFAULT_LEVEL
        self.server_error = ""

    # ── lifecycle ──

    def start(self) -> None:
        if self._server:
            return
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", PORT), _make_handler(self))
            self._server.daemon_threads = True
            threading.Thread(target=self._server.serve_forever, daemon=True, name="ide-bridge-http").start()
            RUN_MARKER.write_text(str(os.getpid()), encoding="utf-8")
            import atexit
            atexit.register(lambda: RUN_MARKER.unlink(missing_ok=True))
        except OSError as e:
            self.server_error = f"port {PORT} occupé ({e})"
            print(f"[IdeBridge] server not started: {e}")
        threading.Thread(target=self._watch_loop, daemon=True, name="ide-bridge-watch").start()

    def subscribe(self, cb: Callable[[str, Optional[Change]], None]) -> None:
        self._listeners.append(cb)

    def _emit(self, what: str, change: Optional[Change] = None) -> None:
        for cb in list(self._listeners):
            try:
                cb(what, change)
            except Exception as e:
                print(f"[IdeBridge] listener error: {e}")

    # ── decisions ──

    def pending(self) -> List[Change]:
        with self._lock:
            return [c for c in self.changes if c.status == "pending"]

    def get(self, change_id: str) -> Optional[Change]:
        with self._lock:
            return next((c for c in self.changes if c.id == change_id), None)

    def accept(self, change_id: str) -> bool:
        c = self.get(change_id)
        if not c or c.status != "pending":
            return False
        if c.mode == "applied":
            snap = self._snapshots.setdefault(c.root, {})
            if c.new is None:
                snap.pop(c.path, None)
            else:
                snap[c.path] = _stat_text(c.path) or (0, 0, c.new)
        c.status = "accepted"
        c._event.set()
        self._emit("decided", c)
        return True

    def reject(self, change_id: str, reason: str = "") -> bool:
        c = self.get(change_id)
        if not c or c.status != "pending":
            return False
        c.reason = reason.strip()
        if c.mode == "applied":
            try:
                p = Path(c.path)
                if c.old is None:
                    p.unlink(missing_ok=True)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(c.old, encoding="utf-8", newline="")
                st = _stat_text(c.path)
                snap = self._snapshots.setdefault(c.root, {})
                if st:
                    snap[c.path] = st
                else:
                    snap.pop(c.path, None)
            except Exception as e:
                print(f"[IdeBridge] restore failed for {c.path}: {e}")
                return False
        c.status = "rejected"
        c._event.set()
        self._emit("decided", c)
        return True

    def accept_all(self) -> int:
        return sum(self.accept(c.id) for c in self.pending())

    def clear_history(self) -> None:
        with self._lock:
            self.changes = [c for c in self.changes if c.status == "pending"]
        self._emit("cleared")

    # ── watched folders ──

    def add_root(self, root: str) -> None:
        root = str(Path(root).resolve())
        with self._lock:
            if root in self.watch_roots or not Path(root).is_dir():
                return
            # a folder inside an already-watched one adds nothing
            if any(_is_inside(root, r) for r in self.watch_roots):
                return
            self.watch_roots.append(root)
        self._save_roots()
        self._emit("roots")

    def remove_root(self, root: str) -> None:
        with self._lock:
            if root in self.watch_roots:
                self.watch_roots.remove(root)
                self._snapshots.pop(root, None)
        self._save_roots()
        self._emit("roots")

    # ── review level ──

    def set_level(self, level: str) -> bool:
        if level not in LEVELS:
            return False
        self.level = level
        data = self._load_config()
        data["level"] = level
        self._write_config(data)
        self._emit("roots")
        return True

    def review_reason(self, change: Change) -> str:
        """Why this change needs you at the current level — empty when it can
        go through without asking."""
        if self.level == "off":
            return ""
        if change.command:
            return "commande risquée"   # only risky commands ever reach here
        if self.level == "commands":
            return ""
        if self.level == "strict":
            return "validation de chaque fichier"
        import re
        rel = change.rel.replace("\\", "/")
        if change.kind == "delete":
            return "suppression de fichier"
        if not _is_inside(change.path, change.root):
            return "fichier hors du projet"
        if any(re.search(p, rel, re.IGNORECASE) for p in SENSITIVE_FILES + self._load_config().get("sensitive_files", [])):
            return "fichier sensible"
        added, removed = change.stats()
        if added + removed > BIG_CHANGE_LINES:
            return f"grosse modification ({added + removed} lignes)"
        return ""

    def explain(self, change: Change, max_lines: int = 80) -> str:
        """What the change does, as text JARVIS can read and explain."""
        if change.command:
            return (f"{change.source} veut exécuter, dans {change.root} :\n{change.command}\n"
                    f"Raison de la demande : {change.why or 'commande risquée'}.")
        a, r = change.stats()
        head = (f"{change.source} veut {'créer' if change.kind == 'create' else 'supprimer' if change.kind == 'delete' else 'modifier'} "
                f"{change.rel} (+{a} −{r} lignes). Raison de la demande : {change.why or '—'}.")
        diff = list(difflib.unified_diff((change.old or "").splitlines(), (change.new or "").splitlines(),
                                         "avant", "après", n=2, lineterm=""))
        if len(diff) > max_lines:
            diff = diff[:max_lines] + [f"… ({len(diff) - max_lines} lignes de diff en plus)"]
        return head + "\n" + "\n".join(diff)

    def _load_config(self) -> dict:
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8") or "{}")
        except Exception:
            return {}

    def _write_config(self, data: dict) -> None:
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[IdeBridge] could not save config: {e}")

    def _load_roots(self) -> List[str]:
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return [r for r in data.get("watch", []) if Path(r).is_dir()]
        except Exception:
            return []

    def _save_roots(self) -> None:
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = self._load_config()
            data["watch"] = self.watch_roots
            self._write_config(data)
        except Exception as e:
            print(f"[IdeBridge] could not save config: {e}")

    def _watch_loop(self) -> None:
        while True:
            for root in list(self.watch_roots):
                try:
                    self._scan(root)
                except Exception as e:
                    print(f"[IdeBridge] scan {root}: {e}")
            time.sleep(POLL_SECONDS)

    def _scan(self, root: str) -> None:
        first = root not in self._snapshots
        snap = self._snapshots.setdefault(root, {})
        seen = set()
        for path in _walk(root):
            seen.add(path)
            try:
                st = os.stat(path)
            except OSError:
                continue
            prev = snap.get(path)
            if prev and prev[0] == st.st_mtime and prev[1] == st.st_size:
                continue
            cur = _stat_text(path)
            if cur is None:          # binary or too large — not reviewable
                snap.pop(path, None)
                continue
            if first:
                snap[path] = cur
                continue
            old_text = prev[2] if prev else None
            snap[path] = cur
            if old_text == cur[2]:
                continue
            self._on_disk_change(root, path, old_text, cur[2])
        if first:
            return
        for path in [p for p in snap if p not in seen]:
            old_text = snap.pop(path)[2]
            self._on_disk_change(root, path, old_text, None)

    def _on_disk_change(self, root: str, path: str, old: Optional[str], new: Optional[str]) -> None:
        exp = self._expected.get(path)
        if exp and time.time() < exp[1] and _same(exp[0], new):
            self._expected.pop(path, None)   # the change you already approved
            return
        with self._lock:
            # one pending card per file: keep the original "before", update "after"
            existing = next((c for c in self.changes if c.path == path and c.status == "pending"
                             and c.mode == "applied"), None)
            if existing:
                existing.new = new
                existing.at = datetime.now().isoformat(timespec="seconds")
                if existing.old == new:   # changed back by itself
                    existing.status = "expired"
                change = existing
            else:
                change = Change(id=uuid.uuid4().hex[:10], source=self._source_for(root), root=root,
                                path=path, old=old, new=new, mode="applied")
                change.why = self.review_reason(change)
                if not change.why:
                    return      # nothing worth your attention at this level
                self.changes.append(change)
                self._trim()
        self._emit("change", change)

    def _source_for(self, root: str) -> str:
        live = [s for s in self.sessions.values()
                if not s.done and time.time() - s.last_seen < 120
                and (_is_inside(s.root, root) or _is_inside(root, s.root))]
        return f"{live[0].source} (hors outil d'édition)" if live else "Sur le disque"

    def _trim(self) -> None:
        done = [c for c in self.changes if c.status != "pending"]
        if len(done) > 60:
            drop = {c.id for c in done[:-60]}
            self.changes = [c for c in self.changes if c.id not in drop]

    # ── hook events (HTTP thread) ──

    def handle_hook(self, payload: dict) -> dict:
        event = payload.get("hook_event_name", "")
        source = payload.get("_source") or "Claude Code"
        sid = str(payload.get("session_id") or "default")
        cwd = payload.get("cwd") or os.getcwd()
        tool = payload.get("tool_name", "")
        tin = payload.get("tool_input") or {}

        with self._lock:
            sess = self.sessions.get(sid)
            if not sess:
                sess = Session(id=sid, source=source, root=cwd)
                self.sessions[sid] = sess
            sess.last_seen = time.time()

        if event == "UserPromptSubmit":
            sess.steps.clear()
            sess.done = False
            self._emit("activity")
            return {}
        if event == "Stop":
            sess.done = True
            for s in sess.steps:
                if s.state == "running":
                    s.state = "done"
            self._emit("activity")
            return {}
        if event == "PostToolUse":
            for s in reversed(sess.steps):
                if s.tool == tool and s.state == "running":
                    s.state = "done"
                    break
            else:
                sess.steps.append(Step(tool, _describe(tool, tin, cwd), "done"))
            sess.done = False
            self._emit("activity")
            return {}
        if event != "PreToolUse":
            return {}

        sess.done = False
        sess.steps.append(Step(tool, _describe(tool, tin, cwd)))
        sess.steps[:] = sess.steps[-40:]
        self._emit("activity")
        if tool == "Bash":
            return self._handle_command(sess, source, cwd, str(tin.get("command") or ""))
        if tool not in EDIT_TOOLS:
            return {}

        path = str(Path(tin.get("file_path") or "").resolve()) if tin.get("file_path") else ""
        if not path:
            return {}
        old = _read(path)
        new = _apply_edit(tool, tin, old)
        if new is None:
            return {}   # cannot preview it (old_string not found…) — let Claude Code handle it
        change = Change(id=uuid.uuid4().hex[:10], source=source, root=cwd, path=path,
                        old=old, new=new, mode="ask", tool=tool)
        self.add_root(cwd)
        change.why = self.review_reason(change)
        if not change.why:
            # routine edit at this level: let it through, and keep the watcher quiet about it
            self._expected[path] = (new, time.time() + 60)
            return {}
        with self._lock:
            self.changes.append(change)
            self._trim()
        self._emit("ask", change)

        step = sess.steps[-1]
        if not change._event.wait(DECISION_TIMEOUT):
            change.status = "expired"
            self._emit("decided", change)
            return {"decision": "ask", "reason": "Pas de réponse dans EIDOLON — décision rendue à l'outil."}
        if change.status == "accepted":
            self._expected[path] = (new, time.time() + 60)
            return {"decision": "allow", "reason": "Accepté dans EIDOLON."}
        why = change.reason or "L'utilisateur a refusé cette modification dans EIDOLON."
        step.state = "failed"
        self._emit("activity")
        return {"decision": "deny", "reason": why}

    def _handle_command(self, sess: "Session", source: str, cwd: str, command: str) -> dict:
        if not command or self.level == "off" or not self.is_risky(command):
            return {}
        change = Change(id=uuid.uuid4().hex[:10], source=source, root=cwd, path=cwd,
                        old=None, new=None, mode="ask", tool="Bash", command=command,
                        why="commande risquée")
        with self._lock:
            self.changes.append(change)
            self._trim()
        step = sess.steps[-1]
        self._emit("ask", change)
        if not change._event.wait(DECISION_TIMEOUT):
            change.status = "expired"
            self._emit("decided", change)
            return {"decision": "ask", "reason": "Pas de réponse dans EIDOLON — décision rendue à l'outil."}
        if change.status == "accepted":
            return {"decision": "allow", "reason": "Commande autorisée dans EIDOLON."}
        step.state = "failed"
        self._emit("activity")
        return {"decision": "deny",
                "reason": change.reason or "L'utilisateur a refusé cette commande dans EIDOLON."}

    def is_risky(self, command: str) -> bool:
        import re
        patterns = RISKY_COMMANDS + self._extra_risky()
        return any(re.search(p, command, re.IGNORECASE) for p in patterns)

    def _extra_risky(self) -> List[str]:
        try:
            return list(json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("risky_commands", []))
        except Exception:
            return []

    def claude_session_active(self) -> bool:
        return any(time.time() - s.last_seen < 600 for s in self.sessions.values())

    def current_session(self) -> Optional[Session]:
        with self._lock:
            if not self.sessions:
                return None
            return max(self.sessions.values(), key=lambda s: s.last_seen)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_handler(bridge: IdeBridge):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/ping"):
                self._reply({"ok": True, "app": "eidolon"})
            else:
                self._reply({"error": "not found"}, 404)

        def do_POST(self):
            if not self.path.startswith("/hook"):
                return self._reply({"error": "not found"}, 404)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                return self._reply({"error": "bad json"}, 400)
            try:
                self._reply(bridge.handle_hook(payload))
            except Exception as e:
                print(f"[IdeBridge] hook error: {e}")
                self._reply({})

        def _reply(self, data: dict, code: int = 200):
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Handler


def _walk(root: str):
    count = 0
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        if e.name not in SKIP_DIRS and not e.name.startswith("."):
                            stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        count += 1
                        if count > MAX_FILES_PER_ROOT:
                            return
                        yield e.path
        except OSError:
            continue


def _stat_text(path: str):
    try:
        st = os.stat(path)
        if st.st_size > MAX_FILE_BYTES:
            return None
        raw = Path(path).read_bytes()
        if b"\x00" in raw[:4096]:
            return None
        return st.st_mtime, st.st_size, raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _same(a: Optional[str], b: Optional[str]) -> bool:
    """Equal once line endings are ignored — editors and tools disagree on CRLF."""
    if a is None or b is None:
        return a is b
    return a.replace("\r\n", "\n") == b.replace("\r\n", "\n")


def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _apply_edit(tool: str, tin: dict, old: Optional[str]) -> Optional[str]:
    if tool == "Write":
        return tin.get("content", "")
    text = old or ""
    edits = tin.get("edits") if tool == "MultiEdit" else [tin]
    for e in edits or []:
        a, b = e.get("old_string", ""), e.get("new_string", "")
        if a == "" and old is None:
            text = b
            continue
        if a not in text:
            return None
        text = text.replace(a, b) if e.get("replace_all") else text.replace(a, b, 1)
    return text


def _describe(tool: str, tin: dict, cwd: str) -> str:
    def rel(p):
        try:
            return str(Path(p).resolve().relative_to(Path(cwd).resolve())).replace("\\", "/")
        except Exception:
            return Path(p).name
    if tin.get("file_path"):
        return rel(tin["file_path"])
    if tin.get("command"):
        return str(tin["command"])[:80]
    if tin.get("pattern"):
        return str(tin["pattern"])[:80]
    if tin.get("url"):
        return str(tin["url"])[:80]
    if tin.get("description"):
        return str(tin["description"])[:80]
    return ""


def _is_inside(child: str, parent: str) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


# ── Claude Code hook installation ────────────────────────────────────────────

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"


def _hook_command() -> str:
    import sys
    py = Path(sys.executable)
    # pythonw has no console window to flash on Windows
    if py.name.lower() == "python.exe" and (py.parent / "pythonw.exe").exists():
        py = py.parent / "pythonw.exe"
    return f'"{py}" "{HOOK_SCRIPT}"'


def claude_hooks_installed() -> bool:
    try:
        content = CLAUDE_SETTINGS.read_text(encoding="utf-8")
        return "eidolon_hook.py" in content
    except Exception:
        return False


def install_claude_hooks() -> str:
    """Add EIDOLON's hooks to ~/.claude/settings.json (a .bak copy is kept)."""
    data = {}
    if CLAUDE_SETTINGS.exists():
        raw = CLAUDE_SETTINGS.read_text(encoding="utf-8")
        CLAUDE_SETTINGS.with_suffix(".json.bak").write_text(raw, encoding="utf-8")
        data = json.loads(raw or "{}")
    uninstall_from(data)
    cmd = _hook_command()
    hooks = data.setdefault("hooks", {})

    def add(event, matcher, timeout):
        entry = {"hooks": [{"type": "command", "command": cmd, "timeout": timeout}]}
        if matcher is not None:
            entry["matcher"] = matcher
        hooks.setdefault(event, []).append(entry)

    add("PreToolUse", "Edit|Write|MultiEdit|Bash", 600)
    add("PreToolUse", "Read|Grep|Glob|WebFetch|WebSearch|Task|NotebookEdit", 5)
    add("PostToolUse", "*", 5)
    add("UserPromptSubmit", None, 5)
    add("Stop", None, 5)
    CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(CLAUDE_SETTINGS)


def uninstall_from(data: dict) -> None:
    hooks = data.get("hooks") or {}
    for event in list(hooks):
        kept = [e for e in hooks[event]
                if not any(
                    ("eidolon_hook.py" in h.get("command", "") or "jarvis_hook.py" in h.get("command", ""))
                    for h in e.get("hooks", [])
                )]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if "hooks" in data and not data["hooks"]:
        del data["hooks"]


def uninstall_claude_hooks() -> None:
    if not CLAUDE_SETTINGS.exists():
        return
    data = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8") or "{}")
    uninstall_from(data)
    CLAUDE_SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


_BRIDGE: Optional[IdeBridge] = None
_BRIDGE_LOCK = threading.Lock()


def get_bridge() -> IdeBridge:
    global _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            _BRIDGE = IdeBridge()
            _BRIDGE.start()
        return _BRIDGE
