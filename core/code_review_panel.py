"""
The term panel of the HUD — a preview of what your coding AI just changed.

It lives inside the overlay, like the chat and drop panels: the overlay grows
in height and this panel fills the space. It is a preview, not an editor — to
read a whole file you go to the tool itself (Claude Code, Antigravity…).

    left    project, tool, and what the agent is doing (Read, Edit, Bash… → Terminé)
    right   the selected file as a diff, with ‹ › to step through the changes,
            then Accepter / Refuser (or Garder / Annuler la modif when the
            change is already on disk)
    ⋯       link Claude Code, watch a folder, clear the history

Bridge events (core/ide_bridge.py) arrive on background threads; the overlay
marshals them here through its Qt signal bridge.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from core.ide_bridge import (
    LEVELS, Change, claude_hooks_installed, diff_rows, get_bridge, install_claude_hooks,
    uninstall_claude_hooks,
)

# HUD palette
CARD = "#40403e"
CODE_BG = "#141416"
CODE_HEAD = "#1c1d21"
TEXT = "#ffffff"
TEXT_2 = "#b5b5b0"
TEXT_3 = "#7c7c77"
OK = "#4cc38a"
WARN = "#f0a43b"
BAD = "#e5534b"
ACCENT = "#5cc9c3"
FONT = "'Segoe UI', system-ui, sans-serif"

SYN = {
    "kw": QColor("#8f9cf7"), "str": QColor("#9ece6a"), "num": QColor("#ff9e64"),
    "fn": QColor("#7dcfff"), "type": QColor("#e0af68"), "com": QColor("#5c6370"),
    "txt": QColor("#d6d8de"),
}
KEYWORDS = set("""
import from export default function return const let var if else for while do switch case break
continue new class extends super this try catch finally throw async await yield typeof instanceof
in of def lambda pass raise with as global nonlocal and or not is None True False elif except
self public private protected static void int float double bool boolean string interface type enum
struct fn pub use mod impl match where null undefined true false package func go defer chan
""".split())
TOKEN_RE = re.compile(
    r"(?P<com>#.*$|//.*$)|(?P<str>\"(?:\\.|[^\"\\])*\"?|'(?:\\.|[^'\\])*'?|`[^`]*`?)"
    r"|(?P<num>\b\d+(?:\.\d+)?\b)|(?P<word>[A-Za-z_$][\w$]*)|(?P<other>\s+|.)")
LANG_BADGE = {
    ".py": ("PY", "#3572A5"), ".ts": ("TS", "#3178c6"), ".tsx": ("TS", "#3178c6"),
    ".js": ("JS", "#b39200"), ".jsx": ("JS", "#b39200"), ".json": ("{}", "#6d6d6d"),
    ".html": ("<>", "#e34c26"), ".css": ("#", "#563d7c"), ".md": ("MD", "#4a4a4a"),
    ".rs": ("RS", "#b7410e"), ".go": ("GO", "#00add8"), ".java": ("JV", "#b07219"),
    ".cs": ("C#", "#178600"), ".cpp": ("C+", "#f34b7d"), ".c": ("C", "#555555"),
    ".sh": ("SH", "#4eaa25"), ".yml": ("YL", "#cb171e"), ".yaml": ("YL", "#cb171e"),
}


def _label(text: str = "", size: int = 12, color: str = TEXT, weight: int = 400) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(f"color:{color}; font-family:{FONT}; font-size:{size}px; "
                     f"font-weight:{weight}; background:transparent;")
    return lb


def _btn(text: str, style: str) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(f"QPushButton {{ font-family:{FONT}; font-size:12px; font-weight:600; "
                    f"border-radius:13px; padding:5px 14px; {style} }}")
    b.setFixedHeight(28)
    return b


def _mono(size: float) -> QFont:
    f = QFont()
    f.setFamilies(["Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", "Menlo", "monospace"])
    f.setPointSizeF(size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0


# ── Diff preview ─────────────────────────────────────────────────────────────

class DiffPreview(QWidget):
    LH = 19

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._font = _mono(9)
        self._strike = _mono(9)
        self._strike.setStrikeOut(True)
        self._fm = QFontMetricsF(self._font)
        self._empty = ""

    def set_change(self, change: Optional[Change], empty: str = ""):
        if change and change.command:
            # a command is read in full, never elided: wrap it instead
            import textwrap
            lines = [w for raw in (change.command.splitlines() or [""])
                     for w in (textwrap.wrap(raw, 50, break_on_hyphens=False, subsequent_indent="  ") or [""])]
            self._rows = [(None, i + 1, "$", line) for i, line in enumerate(lines)]
        else:
            self._rows = diff_rows(change.old, change.new, context=2) if change else []
        self._empty = empty
        self.setMinimumHeight(max(60, len(self._rows) * self.LH + 12))
        self.update()

    def first_change_y(self) -> int:
        for i, r in enumerate(self._rows):
            if r[2] in "+-$":
                return max(0, 6 + i * self.LH - 2 * self.LH)
        return 0

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(CODE_BG))
        if not self._rows:
            p.setPen(QColor(TEXT_3))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(self.rect().adjusted(16, 0, -16, 0),
                       int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), self._empty)
            return
        top = 6
        base = self._fm.ascent() + (self.LH - self._fm.height()) / 2
        clip = self.visibleRegion().boundingRect()
        first = max(0, int((clip.top() - top) / self.LH) - 1)
        last = min(len(self._rows), int((clip.bottom() - top) / self.LH) + 2)
        w = self.width()
        code_x, code_w = 58.0, w - 64.0
        for i in range(first, last):
            old_no, new_no, kind, text = self._rows[i]
            y = top + i * self.LH
            if kind == "-":
                p.fillRect(QRectF(0, y, w, self.LH), QColor(229, 83, 75, 38))
                p.fillRect(QRectF(0, y, 2, self.LH), QColor(BAD))
            elif kind == "+":
                p.fillRect(QRectF(0, y, w, self.LH), QColor(76, 195, 138, 32))
                p.fillRect(QRectF(0, y, 2, self.LH), QColor(OK))
            elif kind == "$":
                p.fillRect(QRectF(0, y, w, self.LH), QColor(240, 164, 59, 26))
                p.fillRect(QRectF(0, y, 2, self.LH), QColor(WARN))
            p.setFont(self._font)
            if kind == "…":
                p.setPen(QColor(TEXT_3))
                p.drawText(QRectF(10, y, 60, self.LH), int(Qt.AlignmentFlag.AlignVCenter), "⋯")
                continue
            no = new_no if new_no is not None else old_no
            p.setPen(QColor(BAD if kind == "-" else OK if kind == "+" else WARN if kind == "$" else "#4b4f58"))
            p.drawText(QRectF(4, y, 30, self.LH),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), str(no or ""))
            if kind in "+-$":
                p.drawText(QRectF(40, y, 12, self.LH), int(Qt.AlignmentFlag.AlignVCenter), kind)
            line = self._fm.elidedText(text.expandtabs(4), Qt.TextElideMode.ElideRight, code_w)
            self._draw_code(p, code_x, y + base, line, kind)

    def _draw_code(self, p: QPainter, x: float, y: float, text: str, kind: str):
        if kind == "-":
            p.setFont(self._strike)
            p.setPen(QColor(229, 120, 110, 190))
            p.drawText(QPointF(x, y), text)
            return
        for m in TOKEN_RE.finditer(text):
            tok, kind_ = m.group(0), m.lastgroup
            if kind_ == "word":
                if tok in KEYWORDS:
                    col = SYN["kw"]
                elif text[m.end():m.end() + 1] == "(":
                    col = SYN["fn"]
                elif tok[:1].isupper():
                    col = SYN["type"]
                else:
                    col = SYN["txt"]
            else:
                col = SYN.get(kind_, SYN["txt"])
            p.setPen(col)
            p.drawText(QPointF(x, y), tok)
            x += self._fm.horizontalAdvance(tok)


# ── Sidebar pieces ───────────────────────────────────────────────────────────

class ProjectBadge(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self._text = "?"

    def set_name(self, name: str):
        parts = re.findall(r"[A-Za-z0-9]+", name) or ["?"]
        self._text = (parts[0][0] + (parts[1][0] if len(parts) > 1 else parts[0][1:2])).upper()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, 38, 38)
        bg = QColor(ACCENT)
        bg.setAlphaF(0.18)
        p.setPen(QPen(QColor(ACCENT), 1))
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(r, 11, 11)
        f = QFont("Segoe UI", 11)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(TEXT))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self._text)


class StepIcon(QWidget):
    """✓ done · spinning ring running · red failed · hollow later."""

    def __init__(self, state: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._state, self._angle = state, 0
        if state == "running":
            self._t = QTimer(self)
            self._t.timeout.connect(self._spin)
            self._t.start(40)

    def _spin(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.5, 1.5, 13, 13)
        if self._state == "done":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(OK))
            p.drawEllipse(r)
            p.setPen(QPen(QColor("#10261b"), 1.8, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
            p.drawPolyline([QPointF(5, 8.2), QPointF(7, 10.2), QPointF(11, 6)])
        elif self._state == "running":
            p.setPen(QPen(QColor("#5a5a58"), 1.8))
            p.drawEllipse(r)
            p.setPen(QPen(QColor(TEXT), 1.8, cap=Qt.PenCapStyle.RoundCap))
            p.drawArc(r, -self._angle * 16, 100 * 16)
        elif self._state == "failed":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(BAD))
            p.drawEllipse(r)
        else:
            p.setPen(QPen(QColor(TEXT_3), 1.4))
            p.drawEllipse(r)


# ── Panel ────────────────────────────────────────────────────────────────────

class CodeReviewPanel(QWidget):
    """Child of the overlay; shown when the overlay is expanded on "term"."""

    all_answered = pyqtSignal()       # nothing left waiting — the overlay may fold back

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.bridge = get_bridge()
        self._selected: Optional[str] = None

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 8, 14, 12)
        root.setSpacing(12)

        # ── left column
        side = QVBoxLayout()
        side.setSpacing(2)
        side.setContentsMargins(2, 4, 0, 0)
        self.badge = ProjectBadge()
        side.addWidget(self.badge)
        side.addSpacing(6)
        self.project = _label("", 14, TEXT, 700)
        side.addWidget(self.project)
        self.source = _label("", 11, TEXT_2)
        side.addWidget(self.source)
        side.addSpacing(10)
        self.steps = QVBoxLayout()
        self.steps.setSpacing(5)
        side.addLayout(self.steps)
        side.addStretch(1)
        self.conn = _label("", 10, TEXT_3)
        self.conn.setTextFormat(Qt.TextFormat.RichText)
        side.addWidget(self.conn)
        sw = QWidget()
        sw.setLayout(side)
        sw.setFixedWidth(118)
        sw.setStyleSheet("background:transparent;")
        root.addWidget(sw)

        # ── code card
        card = QWidget()
        card.setObjectName("codecard")
        card.setStyleSheet(f"QWidget#codecard {{ background:{CODE_BG}; border-radius:12px; }}")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        head = QWidget()
        head.setObjectName("codehead")
        head.setFixedHeight(32)
        head.setStyleSheet(f"QWidget#codehead {{ background:{CODE_HEAD}; border-top-left-radius:12px; "
                           f"border-top-right-radius:12px; }}")
        hh = QHBoxLayout(head)
        hh.setContentsMargins(10, 0, 6, 0)
        hh.setSpacing(6)
        self.lang = QLabel()
        self.lang.setFixedHeight(16)
        hh.addWidget(self.lang)
        self.fname = QLabel()
        self.fname.setFont(_mono(9.5))
        self.fname.setStyleSheet(f"color:{TEXT}; background:transparent;")
        hh.addWidget(self.fname)
        self.dot = _label("●", 9, WARN)
        hh.addWidget(self.dot)
        hh.addStretch(1)
        self.counter = _label("", 10, TEXT_3)
        hh.addWidget(self.counter)
        nav_style = (f"QPushButton {{ color:{TEXT_2}; background:transparent; border:none; font-size:14px; "
                     f"padding:0 4px; }} QPushButton:hover {{ color:{TEXT}; }} "
                     f"QPushButton:disabled {{ color:#3c3c40; }}")
        self.prev_btn, self.next_btn, self.menu_btn = QPushButton("‹"), QPushButton("›"), QPushButton("⋯")
        for b, cb in ((self.prev_btn, lambda: self._step(-1)), (self.next_btn, lambda: self._step(1)),
                      (self.menu_btn, self._menu)):
            b.setStyleSheet(nav_style)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(22, 24)
            b.clicked.connect(cb)
            hh.addWidget(b)
        cv.addWidget(head)

        self.diff = DiffPreview()
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.diff)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{ background:{CODE_BG}; border:none; }}
            QScrollArea > QWidget > QWidget {{ background:{CODE_BG}; }}
            QScrollBar:vertical {{ width:4px; background:transparent; }}
            QScrollBar::handle:vertical {{ background:rgba(255,255,255,0.22); border-radius:2px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        cv.addWidget(self.scroll, 1)

        foot = QWidget()
        foot.setObjectName("codefoot")
        foot.setFixedHeight(42)
        foot.setStyleSheet(f"QWidget#codefoot {{ background:{CODE_HEAD}; border-bottom-left-radius:12px; "
                           f"border-bottom-right-radius:12px; }}")
        fh = QHBoxLayout(foot)
        fh.setContentsMargins(10, 0, 7, 0)
        fh.setSpacing(6)
        self.status = _label("", 11, TEXT_2)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        fh.addWidget(self.status, 1)
        self.reject_btn = _btn("Refuser", "color:#ff8a80; background:rgba(229,83,75,0.14); "
                                          "border:1px solid rgba(229,83,75,0.40);")
        self.reject_btn.clicked.connect(self._reject)
        fh.addWidget(self.reject_btn)
        self.accept_btn = _btn("Accepter", f"color:#0d1a13; background:{OK}; border:none;")
        self.accept_btn.clicked.connect(self._accept)
        fh.addWidget(self.accept_btn)
        cv.addWidget(foot)
        root.addWidget(card, 1)

        self.refresh()

    # ── public ──

    def focus_change(self, change_id: str):
        """Select a change (the one Claude Code is waiting on)."""
        self._selected = change_id
        self.refresh(scroll_to_change=True)

    def on_bridge_event(self, what: str, change_id: str):
        if what == "ask":
            self._selected = change_id
        elif what == "change":
            cur = self.bridge.get(self._selected) if self._selected else None
            if not cur or cur.status != "pending":
                self._selected = change_id
        self.refresh(scroll_to_change=what in ("ask", "change"))

    # ── rendering ──

    def _ordered(self) -> List[Change]:
        return sorted(self.bridge.changes, key=lambda c: (0 if c.status == "pending" else 1, -_ts(c.at)))

    def refresh(self, scroll_to_change: bool = False):
        changes = self._ordered()
        if self._selected and not self.bridge.get(self._selected):
            self._selected = None
        if not self._selected and changes:
            self._selected = changes[0].id
        sel = self.bridge.get(self._selected) if self._selected else None

        sess = self.bridge.current_session()
        name = sel.project if sel else (sess.project if sess else "Code")
        src = sel.source if sel else (sess.source if sess else "Aucune IA reliée")
        self.badge.set_name(name)
        fm = self.project.fontMetrics()
        self.project.setText(fm.elidedText(name, Qt.TextElideMode.ElideRight, 114))
        self.source.setText(src.replace(" (hors outil d'édition)", ""))

        while self.steps.count():
            w = self.steps.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        steps = sess.steps[-4:] if sess else []
        for s in steps:
            self.steps.addWidget(self._step_row(s.state, s.tool))
        if steps:
            self.steps.addWidget(self._step_row("done" if sess.done else "later", "Terminé"))

        if self.bridge.server_error:
            self.conn.setText(f"<span style='color:{BAD}'>●</span> pont indisponible")
        elif claude_hooks_installed():
            live = self.bridge.claude_session_active()
            level = {"strict": "tout", "balanced": "équilibré", "commands": "commandes",
                     "off": "off"}[self.bridge.level]
            self.conn.setText(f"<span style='color:{OK if live else TEXT_3}'>●</span> "
                              f"{'Claude Code actif' if live else 'Claude Code relié'} · {level}")
        else:
            self.conn.setText(f"<span style='color:{TEXT_3}'>●</span> non relié — menu ⋯")

        idx = next((i for i, c in enumerate(changes) if c.id == self._selected), -1)
        self.counter.setText(f"{idx + 1}/{len(changes)}" if changes else "")
        self.prev_btn.setEnabled(idx > 0)
        self.next_btn.setEnabled(0 <= idx < len(changes) - 1)
        self._render_change(sel)
        if scroll_to_change:
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.diff.first_change_y()))

    def _step_row(self, state: str, tool: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(7)
        h.addWidget(StepIcon(state))
        bright = state in ("running", "done")
        h.addWidget(_label(tool, 12, TEXT if bright else TEXT_3, 600 if state == "running" else 500), 1)
        return w

    def _render_change(self, c: Optional[Change]):
        if not c:
            self.lang.hide()
            self.dot.hide()
            self.fname.setText("")
            self.diff.set_change(None, "Aucune modification pour l'instant.\nLes fichiers touchés par "
                                       "votre IA de code apparaîtront ici.")
            self.status.setText("")
            self.accept_btn.hide()
            self.reject_btn.hide()
            return
        ext = Path(c.path).suffix.lower()
        txt, color = LANG_BADGE.get(ext, (ext[1:3].upper() or "TXT", "#555"))
        if c.command:
            txt, color = "SH", "#4eaa25"
        self.lang.setText(txt)
        self.lang.setStyleSheet(f"background:{color}; color:white; border-radius:4px; padding:0 4px; "
                                f"font-family:{FONT}; font-size:9px; font-weight:700;")
        self.lang.show()
        fm = self.fname.fontMetrics()
        title = f"commande · {c.project}" if c.command else c.rel
        self.fname.setText(fm.elidedText(title, Qt.TextElideMode.ElideLeft, 190))
        self.fname.setToolTip(c.command or c.path)
        pending = c.status == "pending"
        self.dot.setVisible(pending)
        self.diff.set_change(c)

        a, r = c.stats()
        counts = f"<span style='color:{OK}'>+{a}</span> <span style='color:{BAD}'>−{r}</span>"
        self.accept_btn.setVisible(pending)
        self.reject_btn.setVisible(pending)
        if c.command:
            counts = "<span style='color:#b5b5b0'>commande risquée</span>"
        why = f" · {c.why}" if c.why and pending else ""
        if pending and c.mode == "ask":
            self.status.setText(f"{counts}&nbsp; <span style='color:{WARN}'>l'IA attend{why}</span>")
            self.accept_btn.setText("Autoriser" if c.command else "Accepter")
            self.reject_btn.setText("Refuser")
        elif pending:
            self.status.setText(f"{counts}&nbsp; déjà écrit{why}")
            self.accept_btn.setText("Garder")
            self.reject_btn.setText("Annuler la modif")
        else:
            label, col = {"accepted": ("Acceptée", OK), "rejected": ("Refusée", BAD),
                          "expired": ("Sans réponse", TEXT_3)}.get(c.status, (c.status, TEXT_3))
            self.status.setText(f"{counts}&nbsp; <span style='color:{col}'>● {label}</span>")

    # ── actions ──

    def _step(self, delta: int):
        changes = self._ordered()
        idx = next((i for i, c in enumerate(changes) if c.id == self._selected), 0)
        idx = max(0, min(len(changes) - 1, idx + delta))
        if changes:
            self._selected = changes[idx].id
            self.refresh(scroll_to_change=True)

    def _after_decision(self):
        pending = self.bridge.pending()
        if pending:
            self._selected = pending[0].id
            self.refresh(scroll_to_change=True)
        else:
            self.refresh()
            self.all_answered.emit()

    def _accept(self):
        if self._selected and self.bridge.accept(self._selected):
            self._after_decision()

    def _reject(self):
        if self._selected and self.bridge.reject(self._selected):
            self._after_decision()

    def _menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background:#2b2b29; color:{TEXT}; border:1px solid rgba(255,255,255,0.08);
                     border-radius:8px; padding:4px; font-family:{FONT}; font-size:12px; }}
            QMenu::item {{ padding:6px 16px; border-radius:6px; }}
            QMenu::item:selected {{ background:#40403e; }}
            QMenu::separator {{ height:1px; background:rgba(255,255,255,0.08); margin:4px 6px; }}
        """)
        levels = menu.addMenu("Niveau de validation")
        levels.setStyleSheet(menu.styleSheet())
        for key, label in LEVELS.items():
            act = levels.addAction(label, lambda k=key: (self.bridge.set_level(k), self.refresh()))
            act.setCheckable(True)
            act.setChecked(self.bridge.level == key)
        menu.addSeparator()
        if claude_hooks_installed():
            menu.addAction("Délier Claude Code", self._unlink_claude)
        else:
            menu.addAction("Relier Claude Code", self._link_claude)
        menu.addAction("Surveiller un dossier (Antigravity, Cursor…)", self._add_root)
        for r in self.bridge.watch_roots:
            menu.addAction(f"Ne plus surveiller {Path(r).name}",
                           lambda r=r: (self.bridge.remove_root(r), self.refresh()))
        if self.bridge.pending():
            menu.addSeparator()
            menu.addAction(f"Tout accepter ({len(self.bridge.pending())})",
                           lambda: (self.bridge.accept_all(), self._after_decision()))
        menu.addSeparator()
        menu.addAction("Vider l'historique", lambda: (self.bridge.clear_history(), self.refresh()))
        menu.exec(self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft()))

    def _link_claude(self):
        ok = QMessageBox.question(
            self, "Relier Claude Code",
            "JARVIS ajoute ses hooks dans ~/.claude/settings.json (copie .bak gardée).\n\n"
            "Chaque modification de fichier demandée par Claude Code s'affichera dans ce panneau "
            "et attendra votre accord tant que JARVIS est ouvert.")
        if ok == QMessageBox.StandardButton.Yes:
            try:
                install_claude_hooks()
            except Exception as e:
                QMessageBox.warning(self, "Claude Code", f"Échec : {e}")
        self.refresh()

    def _unlink_claude(self):
        try:
            uninstall_claude_hooks()
        except Exception as e:
            QMessageBox.warning(self, "Claude Code", f"Échec : {e}")
        self.refresh()

    def _add_root(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier de projet à surveiller")
        if d:
            self.bridge.add_root(d)
            self.refresh()
