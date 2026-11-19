"""
The Équipe window — where you see your experts work for you.

    left    your missions, newest first, with where each one stands
    centre  the team JARVIS put together: one card per expert, with the task
            it was given and whether it is waiting, working, done or redoing
    right   what you came for — the deliverable — plus the team's thread and
            any expert's own contribution when you click its card
    bottom  ask for a change, open the .docx, open the folder, or stop

A new mission starts on the brief page: you write (or say) what you want, and
JARVIS does the rest. If it needs answers first, they are asked right on the
mission page.

Same graphite / cyan language as the HUD. Engine updates arrive from the
agent-team thread through the event bus and are marshalled to the GUI thread
with a Qt signal, then coalesced so a burst of events is one repaint.
"""
from __future__ import annotations

import html
import os
import subprocess
import sys
from datetime import datetime
from typing import Dict, Optional

from PyQt6.QtCore import Qt, QObject, QRectF, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QFontMetrics
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget, QMessageBox,
)

from core.agent_events import get_event_bus
from core.agent_team import get_team, Mission, Assignment, Status

# ── Palette (matches the HUD) ────────────────────────────────────────────────
BG = "#141413"
PANEL = "#1b1b1a"
CARD = "#262624"
CARD_HOVER = "#2f2f2d"
RAISED = "#3a3a38"
BORDER = "rgba(255,255,255,0.07)"
TEXT = "#f2f2f0"
TEXT_2 = "#a8a8a2"
TEXT_3 = "#6f6f6a"
ACCENT = "#00a1c5"
ACCENT_SOFT = "#5cc9c3"
OK = "#4cc38a"
WARN = "#e0a43b"
BAD = "#e5534b"
FONT = "'Segoe UI', 'Inter', system-ui, sans-serif"

STATUS_COLOR = {
    Status.CLARIFYING: WARN, Status.PLANNING: ACCENT, Status.WORKING: ACCENT,
    Status.REVIEWING: ACCENT, Status.WRITING: ACCENT, Status.DONE: OK,
    Status.FAILED: BAD, Status.CANCELLED: TEXT_3,
}
TASK_LABEL = {"waiting": "En attente", "working": "Au travail", "done": "Terminé", "failed": "Échec"}
TASK_COLOR = {"waiting": TEXT_3, "working": ACCENT, "done": OK, "failed": BAD}

STEPS = ["Brief", "Équipe", "Travail", "Revue", "Livrable"]
STEP_OF = {Status.CLARIFYING: 0, Status.PLANNING: 1, Status.WORKING: 2,
           Status.REVIEWING: 3, Status.WRITING: 4, Status.DONE: 5}

DOC_CSS = f"""
body {{ color: {TEXT}; font-family: {FONT}; font-size: 14px; line-height: 150%; }}
h1 {{ font-size: 22px; font-weight: 600; margin: 4px 0 10px 0; }}
h2 {{ font-size: 17px; font-weight: 600; margin: 18px 0 6px 0; color: {TEXT}; }}
h3 {{ font-size: 15px; font-weight: 600; margin: 14px 0 4px 0; color: {ACCENT_SOFT}; }}
p, li {{ color: #dcdcd6; }}
code {{ background: {RAISED}; }}
table {{ border-collapse: collapse; }}
th, td {{ border: 1px solid #3a3a38; padding: 4px 8px; }}
a {{ color: {ACCENT_SOFT}; }}
"""


def _btn(text: str, kind: str = "ghost") -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setProperty("kind", kind)
    return b


def _label(text: str = "", size: int = 13, color: str = TEXT, weight: int = 400) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(f"color:{color}; font-size:{size}px; font-weight:{weight}; background:transparent;")
    return lb


def _when(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso)
    except Exception:
        return ""
    today = datetime.now().date()
    if d.date() == today:
        return d.strftime("%H:%M")
    return d.strftime("%d/%m %H:%M")


# ── Small painted widgets ────────────────────────────────────────────────────

class Avatar(QWidget):
    """Coloured disc with the expert's acronym; a ring breathes while working."""

    def __init__(self, acronym: str, color: str, size: int = 40, parent=None):
        super().__init__(parent)
        self._text, self._color, self._size = acronym, QColor(color), size
        self._working, self._phase = False, 0.0
        self.setFixedSize(size + 8, size + 8)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_working(self, on: bool) -> None:
        self._working = on
        if on and not self._timer.isActive():
            self._timer.start(50)
        elif not on:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.06) % 1.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(4, 4, self._size, self._size)
        if self._working:
            ring = QColor(ACCENT)
            ring.setAlphaF(0.85 - 0.6 * abs(self._phase - 0.5) * 2)
            p.setPen(QPen(ring, 2.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.adjusted(-3, -3, 3, 3))
        bg = QColor(self._color)
        bg.setAlphaF(0.20)
        p.setPen(QPen(self._color, 1.2))
        p.setBrush(QBrush(bg))
        p.drawEllipse(r)
        f = QFont("Segoe UI", max(7, int(self._size / (3.2 if len(self._text) <= 3 else 3.9))))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(TEXT))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self._text)


class Stepper(QWidget):
    """Brief → Équipe → Travail → Revue → Livrable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._step, self._bad = 0, False
        self.setFixedHeight(46)

    def set_state(self, status: str, last_step: int) -> None:
        self._bad = status in (Status.FAILED, Status.CANCELLED)
        self._step = STEP_OF.get(status, last_step)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(STEPS)
        w = self.width()
        xs = [40 + i * (w - 80) / (n - 1) for i in range(n)]
        y = 14
        for i in range(n - 1):
            p.setPen(QPen(QColor(ACCENT if i < self._step else RAISED), 2))
            p.drawLine(int(xs[i] + 8), y, int(xs[i + 1] - 8), y)
        f = QFont("Segoe UI", 9)
        p.setFont(f)
        for i, x in enumerate(xs):
            done, current = i < self._step, i == self._step
            if current and self._bad:
                col = QColor(BAD)
            else:
                col = QColor(ACCENT if (done or current) else RAISED)
            p.setPen(QPen(col, 2))
            p.setBrush(QBrush(col if done else QColor(PANEL)))
            p.drawEllipse(QRectF(x - 6, y - 6, 12, 12))
            p.setPen(QColor(TEXT if (done or current) else TEXT_3))
            p.drawText(QRectF(x - 50, y + 10, 100, 18), int(Qt.AlignmentFlag.AlignHCenter), STEPS[i])


class Pill(QLabel):
    def set(self, text: str, color: str) -> None:
        self.setText(f"●  {text}")
        c = QColor(color)
        self.setStyleSheet(
            f"color:{color}; background: rgba({c.red()},{c.green()},{c.blue()},0.13);"
            f"border-radius: 11px; padding: 3px 12px; font-size: 12px; font-weight: 600;")


# ── Expert card ──────────────────────────────────────────────────────────────

class ExpertCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, a: Assignment, parent=None):
        super().__init__(parent)
        self.assignment_id = a.id
        team = get_team()
        prof = team.agent(a.agent_id)
        color = getattr(prof, "color", ACCENT) or ACCENT
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("expert")

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 12, 14, 12)
        row.setSpacing(12)
        self.avatar = Avatar(a.agent_id.upper(), color, 38)
        row.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(3)
        head = QHBoxLayout()
        name = prof.name if prof else a.agent_id.upper()
        self.name = _label(f"{a.agent_id.upper()} · {name}", 13, TEXT, 600)
        self.name.setWordWrap(True)
        self.status = _label("", 12, TEXT_3, 600)
        head.addWidget(self.name, 1)
        head.addWidget(self.status)
        col.addLayout(head)
        self.task = _label("", 12, TEXT_2)
        self.task.setWordWrap(True)
        col.addWidget(self.task)
        row.addLayout(col, 1)
        self.update_from(a, selected=False)

    def update_from(self, a: Assignment, selected: bool) -> None:
        label = TASK_LABEL.get(a.status, a.status)
        color = TASK_COLOR.get(a.status, TEXT_3)
        if a.status == "waiting" and a.revision:
            label, color = "À corriger", WARN
        if a.status == "working" and a.revision:
            label = "Corrige"
        self.status.setText(label)
        self.status.setStyleSheet(f"color:{color}; font-size:12px; font-weight:600; background:transparent;")
        task = a.task if len(a.task) <= 220 else a.task[:217] + "…"
        self.task.setText(task)
        self.avatar.set_working(a.status == "working")
        border = ACCENT if selected else BORDER
        self.setStyleSheet(
            f"QFrame#expert {{ background:{CARD}; border:1px solid {border}; border-radius:12px; }}"
            f"QFrame#expert:hover {{ background:{CARD_HOVER}; }}")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.assignment_id)


# ── Mission list row ─────────────────────────────────────────────────────────

class MissionRow(QWidget):
    def __init__(self, m: Mission, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(2)
        title = m.title or m.brief
        fm = QFontMetrics(QFont("Segoe UI", 10))
        t = _label(fm.elidedText(title, Qt.TextElideMode.ElideRight, 200), 13, TEXT, 600)
        v.addWidget(t)
        color = STATUS_COLOR.get(m.status, TEXT_3)
        s = _label(f"<span style='color:{color}'>●</span>&nbsp; {m.status_label()}"
                   f"<span style='color:{TEXT_3}'> · {_when(m.created_at)}</span>", 11, TEXT_2)
        s.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(s)


# ── Bridge from the engine thread ────────────────────────────────────────────

class _Bridge(QObject):
    changed = pyqtSignal(str)
    launched = pyqtSignal(str)


# ── Window ───────────────────────────────────────────────────────────────────

class AgentTeamWindow(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.team = get_team()
        self.setWindowTitle("Équipe — EIDOLON")
        self.resize(1220, 780)
        self.setMinimumSize(980, 620)
        self._current: Optional[str] = None
        self._selected_assignment: Optional[str] = None
        self._view = "doc"          # doc | feed | brief | contrib
        self._cards: Dict[str, ExpertCard] = {}
        self._dirty: set[str] = set()

        self._bridge = _Bridge()
        self._bridge.changed.connect(self._on_changed)
        self._bridge.launched.connect(self._after_launch)
        self._coalesce = QTimer(self, singleShot=True, interval=120)
        self._coalesce.timeout.connect(self._flush)
        get_event_bus().subscribe(self._on_bus)

        self._build()
        self._refresh_list()
        ms = self.team.missions()
        if ms:
            self.show_mission(ms[0].id)
        else:
            self.show_new()

    # ── layout ──

    def _build(self):
        self.setStyleSheet(f"""
            QWidget {{ background:{BG}; color:{TEXT}; font-family:{FONT}; font-size:13px; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; border:none; }}
            QScrollBar:vertical {{ background:transparent; width:8px; margin:2px; }}
            QScrollBar::handle:vertical {{ background:{RAISED}; border-radius:4px; min-height:30px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
            QPushButton {{ border-radius:10px; padding:8px 16px; font-weight:600; font-size:13px; }}
            QPushButton[kind="primary"] {{ background:{ACCENT}; color:#ffffff; border:none; }}
            QPushButton[kind="primary"]:hover {{ background:#12b3d8; }}
            QPushButton[kind="primary"]:disabled {{ background:{RAISED}; color:{TEXT_3}; }}
            QPushButton[kind="ghost"] {{ background:{CARD}; color:{TEXT}; border:1px solid {BORDER}; }}
            QPushButton[kind="ghost"]:hover {{ background:{CARD_HOVER}; }}
            QPushButton[kind="danger"] {{ background:transparent; color:{BAD}; border:1px solid rgba(229,83,75,0.35); }}
            QPushButton[kind="danger"]:hover {{ background:rgba(229,83,75,0.10); }}
            QPushButton[kind="tab"] {{ background:transparent; color:{TEXT_2}; border:none; padding:6px 12px; border-radius:8px; }}
            QPushButton[kind="tab"]:checked {{ background:{RAISED}; color:{TEXT}; }}
            QLineEdit, QTextEdit {{ background:{CARD}; border:1px solid {BORDER}; border-radius:12px;
                                   padding:10px 12px; color:{TEXT}; selection-background-color:{ACCENT}; }}
            QLineEdit:focus, QTextEdit:focus {{ border:1px solid {ACCENT}; }}
            QTextBrowser {{ background:transparent; border:none; }}
            QListWidget {{ background:transparent; border:none; outline:0; }}
            QListWidget::item {{ border-radius:10px; margin:2px 0; }}
            QListWidget::item:selected {{ background:{CARD}; }}
            QListWidget::item:hover:!selected {{ background:#1f1f1d; }}
            QCheckBox {{ color:{TEXT_2}; spacing:8px; background:transparent; }}
            QToolTip {{ background:{RAISED}; color:{TEXT}; border:none; padding:6px; }}
        """)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # sidebar
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(268)
        side.setStyleSheet(f"QFrame#side {{ background:{PANEL}; border-right:1px solid {BORDER}; }}")
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 20, 12, 16)
        sv.setSpacing(12)
        sv.addWidget(_label("Équipe", 20, TEXT, 600))
        sv.addWidget(_label(f"{self.team.registry.count()} experts à votre service", 12, TEXT_3))
        new_btn = _btn("＋  Nouvelle mission", "primary")
        new_btn.clicked.connect(self.show_new)
        sv.addWidget(new_btn)
        sv.addSpacing(4)
        sv.addWidget(_label("MISSIONS", 11, TEXT_3, 600))
        self.list = QListWidget()
        self.list.itemClicked.connect(lambda it: self.show_mission(it.data(Qt.ItemDataRole.UserRole)))
        sv.addWidget(self.list, 1)
        root.addWidget(side)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_new_page())
        self.stack.addWidget(self._build_mission_page())
        root.addWidget(self.stack, 1)

    def _build_new_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        holder = QWidget()
        scroll.setWidget(holder)
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        body.setMaximumWidth(900)
        body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hl.addWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(56, 52, 56, 40)
        v.setSpacing(14)

        v.addWidget(_label("Que doit produire l'équipe ?", 26, TEXT, 600))
        sub = _label("Décrivez le résultat voulu, avec vos chiffres et contraintes. EIDOLON choisit les "
                     "experts utiles, répartit le travail et vous remet un document final.", 14, TEXT_2)
        sub.setWordWrap(True)
        v.addWidget(sub)

        self.brief = QTextEdit()
        self.brief.setPlaceholderText(
            "Ex. : Prépare un business plan pour mon application de coaching sportif, lancement en "
            "France, budget de départ 30 000 €, objectif 1 000 abonnés la première année.")
        self.brief.setMinimumHeight(150)
        self.brief.setStyleSheet(f"font-size:14px;")
        v.addWidget(self.brief)

        row = QHBoxLayout()
        self.ask_first = QCheckBox("Me poser des questions si une info manque")
        self.ask_first.setChecked(True)
        row.addWidget(self.ask_first, 1)
        self.launch_btn = _btn("Lancer l'équipe  →", "primary")
        self.launch_btn.clicked.connect(self._launch)
        row.addWidget(self.launch_btn)
        v.addLayout(row)

        v.addSpacing(18)
        v.addWidget(_label("VOS EXPERTS", 11, TEXT_3, 600))
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, ag in enumerate(self.team.registry.list_all()):
            chip = QFrame()
            chip.setObjectName("chip")
            chip.setStyleSheet(f"QFrame#chip {{ background:{CARD}; border:1px solid {BORDER}; border-radius:12px; }}")
            chip.setToolTip(ag.specialty)
            h = QHBoxLayout(chip)
            h.setContentsMargins(10, 8, 10, 8)
            h.setSpacing(10)
            h.addWidget(Avatar(ag.acronym, ag.color or ACCENT, 30))
            tv = QVBoxLayout()
            tv.setSpacing(0)
            tv.addWidget(_label(ag.acronym, 12, TEXT, 600))
            nm = _label(ag.name, 11, TEXT_3)
            nm.setWordWrap(True)
            tv.addWidget(nm)
            h.addLayout(tv, 1)
            grid.addWidget(chip, i // 3, i % 3)
        v.addLayout(grid)
        v.addStretch(1)
        return page

    def _build_mission_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 22, 28, 18)
        v.setSpacing(12)

        head = QHBoxLayout()
        self.title = _label("", 21, TEXT, 600)
        self.title.setWordWrap(True)
        head.addWidget(self.title, 1)
        self.pill = Pill()
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(head)
        self.stepper = Stepper()
        v.addWidget(self.stepper)

        # clarification card (only while waiting for answers)
        self.ask_card = QFrame()
        self.ask_card.setObjectName("ask")
        self.ask_card.setStyleSheet(
            f"QFrame#ask {{ background:rgba(224,164,59,0.08); border:1px solid rgba(224,164,59,0.35); border-radius:14px; }}")
        av = QVBoxLayout(self.ask_card)
        av.setContentsMargins(18, 14, 18, 14)
        av.setSpacing(8)
        av.addWidget(_label("EIDOLON a besoin de quelques précisions", 14, WARN, 600))
        self.ask_questions = _label("", 13, TEXT)
        self.ask_questions.setWordWrap(True)
        self.ask_questions.setTextFormat(Qt.TextFormat.RichText)
        av.addWidget(self.ask_questions)
        self.ask_input = QTextEdit()
        self.ask_input.setPlaceholderText("Vos réponses…")
        self.ask_input.setFixedHeight(80)
        av.addWidget(self.ask_input)
        ar = QHBoxLayout()
        ar.addStretch(1)
        skip = _btn("Lancer sans répondre")
        skip.clicked.connect(lambda: self._answer(skip=True))
        ar.addWidget(skip)
        send = _btn("Envoyer et lancer", "primary")
        send.clicked.connect(lambda: self._answer(skip=False))
        ar.addWidget(send)
        av.addLayout(ar)
        v.addWidget(self.ask_card)

        body = QHBoxLayout()
        body.setSpacing(18)

        # team column
        left = QVBoxLayout()
        left.setSpacing(8)
        self.team_title = _label("ÉQUIPE MOBILISÉE", 11, TEXT_3, 600)
        left.addWidget(self.team_title)
        tscroll = QScrollArea()
        tscroll.setWidgetResizable(True)
        tscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tw = QWidget()
        self.cards_box = QVBoxLayout(tw)
        self.cards_box.setContentsMargins(0, 0, 4, 0)
        self.cards_box.setSpacing(8)
        self.cards_box.addStretch(1)
        tscroll.setWidget(tw)
        left.addWidget(tscroll, 1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(380)
        body.addWidget(lw)

        # viewer
        viewer = QFrame()
        viewer.setObjectName("viewer")
        viewer.setStyleSheet(f"QFrame#viewer {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:16px; }}")
        vv = QVBoxLayout(viewer)
        vv.setContentsMargins(14, 10, 14, 10)
        vv.setSpacing(6)
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.tab_btns: Dict[str, QPushButton] = {}
        for key, text in (("doc", "Livrable"), ("feed", "Fil de l'équipe"), ("brief", "Demande"), ("contrib", "Contribution")):
            b = _btn(text, "tab")
            b.setCheckable(True)
            b.clicked.connect(lambda _, k=key: self._set_view(k))
            tabs.addWidget(b)
            self.tab_btns[key] = b
        tabs.addStretch(1)
        vv.addLayout(tabs)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{BORDER};")
        vv.addWidget(sep)
        self.reader = QTextBrowser()
        self.reader.setOpenExternalLinks(True)
        self.reader.document().setDefaultStyleSheet(DOC_CSS)
        self.reader.setStyleSheet(f"QTextBrowser {{ padding: 8px 10px; }}")
        vv.addWidget(self.reader, 1)
        body.addWidget(viewer, 1)
        v.addLayout(body, 1)

        # action bar
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.revise_input = QLineEdit()
        self.revise_input.setPlaceholderText("Demander une modification à l'équipe… (ex. « refais les chiffres avec 50 000 € »)")
        self.revise_input.returnPressed.connect(self._revise)
        bar.addWidget(self.revise_input, 1)
        self.revise_btn = _btn("Envoyer", "primary")
        self.revise_btn.clicked.connect(self._revise)
        bar.addWidget(self.revise_btn)
        self.open_doc_btn = _btn("Ouvrir le document")
        self.open_doc_btn.clicked.connect(self._open_doc)
        bar.addWidget(self.open_doc_btn)
        self.open_dir_btn = _btn("Dossier")
        self.open_dir_btn.clicked.connect(self._open_dir)
        bar.addWidget(self.open_dir_btn)
        self.cancel_btn = _btn("Arrêter", "danger")
        self.cancel_btn.clicked.connect(self._cancel)
        bar.addWidget(self.cancel_btn)
        self.delete_btn = _btn("Supprimer", "danger")
        self.delete_btn.clicked.connect(self._delete)
        bar.addWidget(self.delete_btn)
        v.addLayout(bar)
        return page

    # ── navigation ──

    def show_new(self):
        self._current = None
        self.list.clearSelection()
        self.stack.setCurrentIndex(0)
        self.brief.setFocus()

    def show_mission(self, mission_id: str):
        m = self.team.get(mission_id)
        if not m:
            return
        if mission_id != self._current:
            self._current = mission_id
            self._selected_assignment = None
            self._view = "doc" if m.status == Status.DONE else "feed"
            self._rebuild_cards(m)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == mission_id:
                self.list.setCurrentItem(it)
        self.stack.setCurrentIndex(1)
        self._render(m)

    # ── engine events ──

    def _on_bus(self, ev):  # engine thread
        if ev.event_type.startswith("mission."):
            self._bridge.changed.emit(ev.mission_id)

    def _on_changed(self, mission_id: str):
        self._dirty.add(mission_id)
        if not self._coalesce.isActive():
            self._coalesce.start()

    def _flush(self):
        dirty, self._dirty = self._dirty, set()
        self._refresh_list()
        if self._current in dirty:
            m = self.team.get(self._current)
            if m:
                self._render(m)
            else:
                self.show_new()

    # ── rendering ──

    def _refresh_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        for m in self.team.missions():
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, m.id)
            row = MissionRow(m)
            it.setSizeHint(QSize(220, row.sizeHint().height() + 10))
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
            if m.id == self._current:
                it.setSelected(True)
        self.list.blockSignals(False)

    def _rebuild_cards(self, m: Mission):
        while self.cards_box.count() > 1:
            w = self.cards_box.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._cards.clear()
        for a in m.assignments:
            c = ExpertCard(a)
            c.clicked.connect(self._select_assignment)
            self.cards_box.insertWidget(self.cards_box.count() - 1, c)
            self._cards[a.id] = c

    def _render(self, m: Mission):
        self.title.setText(m.title or m.brief[:80])
        self.pill.set(m.status_label(), STATUS_COLOR.get(m.status, TEXT_3))
        last = 2 if m.assignments else (1 if m.status != Status.CLARIFYING else 0)
        self.stepper.set_state(m.status, last)

        # clarification
        asking = m.status == Status.CLARIFYING
        self.ask_card.setVisible(asking)
        if asking:
            self.ask_questions.setText("<br>".join(f"<b>{i}.</b> {html.escape(q)}" for i, q in enumerate(m.questions, 1)))

        # team cards — rebuild when the plan changed, update otherwise
        if set(self._cards) != {a.id for a in m.assignments}:
            self._rebuild_cards(m)
        for a in m.assignments:
            self._cards[a.id].update_from(a, selected=a.id == self._selected_assignment)
        done, total = m.progress()
        if total:
            self.team_title.setText(f"ÉQUIPE MOBILISÉE · {done}/{total}")
        elif asking:
            self.team_title.setText("L'ÉQUIPE SERA COMPOSÉE APRÈS VOS RÉPONSES")
        elif m.status == Status.PLANNING:
            self.team_title.setText("EIDOLON COMPOSE L'ÉQUIPE…")
        else:
            self.team_title.setText("AUCUN EXPERT MOBILISÉ")

        # tabs
        self.tab_btns["contrib"].setVisible(self._selected_assignment is not None)
        if self._view == "contrib" and not self._selected_assignment:
            self._view = "feed"
        for k, b in self.tab_btns.items():
            b.setChecked(k == self._view)
        self._render_view(m)

        # actions
        busy = m.status in Status.ACTIVE
        finished = m.status == Status.DONE
        self.revise_input.setEnabled(finished)
        self.revise_btn.setEnabled(finished)
        self.revise_input.setPlaceholderText(
            "Demander une modification à l'équipe… (ex. « refais les chiffres avec 50 000 € »)" if finished
            else "Les modifications seront possibles une fois le livrable prêt.")
        self.open_doc_btn.setVisible(bool(m.files))
        self.open_dir_btn.setVisible(m.folder.exists())
        self.cancel_btn.setVisible(busy or asking)
        self.delete_btn.setVisible(not busy and not asking)

    def _set_markdown(self, text: str):
        """setMarkdown, then give headings and paragraphs room to breathe —
        Qt's markdown importer ignores the stylesheet's margins."""
        from PyQt6.QtGui import QTextCursor
        self.reader.setMarkdown(text)
        doc = self.reader.document()
        cur = QTextCursor(doc)
        block = doc.begin()
        while block.isValid():
            fmt = block.blockFormat()
            level = fmt.headingLevel()
            if level:
                fmt.setTopMargin(18 if level <= 2 else 12)
                fmt.setBottomMargin(6)
            else:
                fmt.setBottomMargin(6)
                fmt.setLineHeight(135, 1)  # ProportionalHeight
            cur.setPosition(block.position())
            cur.setBlockFormat(fmt)
            block = block.next()

    def _render_view(self, m: Mission):
        bar = self.reader.verticalScrollBar()
        keep = bar.value()
        if self._view == "doc":
            if m.document:
                self._set_markdown(m.document)
            else:
                self.reader.setHtml(self._placeholder(m))
        elif self._view == "feed":
            self.reader.setHtml(self._feed_html(m))
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
            return
        elif self._view == "brief":
            txt = f"## Votre demande\n\n{m.brief}\n"
            if m.answers:
                txt += f"\n## Précisions\n\n{m.answers}\n"
            if m.deliverable:
                txt += f"\n## Document prévu par EIDOLON\n\n{m.deliverable}\n"
            if m.review:
                txt += f"\n## Relecture\n\n{m.review}\n"
            self._set_markdown(txt)
        elif self._view == "contrib":
            a = m.get(self._selected_assignment or "")
            if a:
                prof = self.team.agent(a.agent_id)
                head = f"# {a.agent_id.upper()} — {prof.name if prof else ''}\n\n**Tâche :** {a.task}\n\n"
                if a.revision:
                    head += f"**Correction demandée :** {a.revision}\n\n"
                body = a.output or ("*Au travail…*" if a.status == "working" else
                                    f"*Échec : {a.error}*" if a.status == "failed" else "*En attente de son tour…*")
                self._set_markdown(head + "---\n\n" + body)
        bar.setValue(keep)

    def _placeholder(self, m: Mission) -> str:
        if m.status == Status.FAILED:
            msg = f"La mission a échoué : {html.escape(m.error)}"
        elif m.status == Status.CANCELLED:
            msg = "Mission annulée."
        elif m.status == Status.CLARIFYING:
            msg = "Répondez aux questions ci-dessus pour lancer l'équipe."
        else:
            msg = "Le livrable apparaîtra ici dès que l'équipe aura terminé.<br>Suivez l'avancement dans « Fil de l'équipe »."
        return (f"<div style='color:{TEXT_3}; font-size:14px; margin-top:60px;' align='center'>{msg}</div>")

    def _feed_html(self, m: Mission) -> str:
        rows = []
        for f in m.feed:
            if f.who in ("eidolon", "jarvis"):
                name, color = "EIDOLON", ACCENT_SOFT
            elif f.who == "vous":
                name, color = "Vous", TEXT
            else:
                prof = self.team.agent(f.who)
                name, color = f.who.upper(), (prof.color if prof and prof.color else TEXT_2)
            rows.append(
                f"<tr><td width='64' valign='top' style='color:{TEXT_3}; font-size:11px; padding:6px 0;'>{_when(f.at)}</td>"
                f"<td valign='top' style='padding:6px 0;'><span style='color:{color}; font-weight:600;'>{name}</span>"
                f"<br><span style='color:#dcdcd6;'>{html.escape(f.text)}</span></td></tr>")
        return f"<table width='100%' cellspacing='0'>{''.join(rows)}</table>"

    # ── actions ──

    def _set_view(self, key: str):
        self._view = key
        m = self.team.get(self._current or "")
        if m:
            self._render(m)

    def _select_assignment(self, assignment_id: str):
        self._selected_assignment = None if self._selected_assignment == assignment_id else assignment_id
        self._view = "contrib" if self._selected_assignment else "doc"
        m = self.team.get(self._current or "")
        if m:
            self._render(m)

    def _launch(self):
        text = self.brief.toPlainText().strip()
        if not text:
            self.brief.setFocus()
            return
        self.launch_btn.setEnabled(False)
        self.launch_btn.setText("EIDOLON analyse…")
        ask = self.ask_first.isChecked()

        # start() may ask the model for cadrage questions — keep the GUI free.
        import threading

        def work():
            m = self.team.start(text, ask_questions=ask)
            self._bridge.launched.emit(m.id)
        threading.Thread(target=work, daemon=True).start()

    def _after_launch(self, mission_id: str):
        self.launch_btn.setEnabled(True)
        self.launch_btn.setText("Lancer l'équipe  →")
        self.brief.clear()
        self._refresh_list()
        self.show_mission(mission_id)

    def _answer(self, skip: bool):
        if not self._current:
            return
        self.team.answer(self._current, "" if skip else self.ask_input.toPlainText())
        self.ask_input.clear()

    def _revise(self):
        text = self.revise_input.text().strip()
        if text and self._current and self.team.revise(self._current, text):
            self.revise_input.clear()
            self._view = "feed"

    def _cancel(self):
        if self._current:
            self.team.cancel(self._current)

    def _delete(self):
        m = self.team.get(self._current or "")
        if not m:
            return
        if QMessageBox.question(self, "Supprimer la mission",
                                f"Supprimer « {m.title} » et ses fichiers ?") == QMessageBox.StandardButton.Yes:
            self.team.delete(m.id)

    def _open_doc(self):
        m = self.team.get(self._current or "")
        if m and m.files:
            docx = [f for f in m.files if f.endswith(".docx")]
            _open_path((docx or m.files)[0])

    def _open_dir(self):
        m = self.team.get(self._current or "")
        if m:
            _open_path(str(m.folder))

    def closeEvent(self, e):
        self.hide()
        e.ignore()


def _open_path(path: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"[AgentTeam:ui] open failed: {e}")
