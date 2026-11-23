"""EIDOLON — Native PyQt6 overlay UI host.

The overlay is a small, frameless, transparent, always-on-top pill centred at the top of the screen.
It features:
- Left buttons: Conversation (chat panel toggle), Terminal, Add
- Centre: 64-element morphing voice visualizer (listening, thinking, speaking, idle)
- Right buttons: Settings, Volume (toggles popup volume slider pill), Microphone (toggle mute)
- Full per-pixel transparency (zero grey background or border box)
- Drag-and-drop repositioning for both main overlay and volume slider
- Expandable Chat Panel with cubic-bezier expansion and smooth fade-in

This module keeps the complete public interface required by main.py, actions, and plugins.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QTimer, QRectF, QByteArray, pyqtSignal, QObject, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QPainterPath, QImage, QPixmap, QFont
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QScrollArea, QLabel, QGraphicsOpacityEffect,
    QFileDialog, QFrame, QSizePolicy
)

# ── Paths & Config ───────────────────────────────────────────────────────────
def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE = CONFIG_DIR / "api_keys.json"
OVERLAY_TOP = 14  # px from the top edge of the screen

def _read_full_config() -> dict:
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _write_full_config(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")

# ── Spectrum Analysis ────────────────────────────────────────────────────────
SPEC_BANDS = 20
SPEC_HOP = 480  # samples @ 24 kHz = 20 ms
SPEC_WIN = 1024
_spec_cache: dict = {}

def _spectrum_frames(pcm, sr: int):
    """int16 PCM -> list of SPEC_BANDS-long lists in 0..1 (one per SPEC_HOP samples)."""
    x = np.asarray(pcm, dtype=np.float32)
    if x.size == 0:
        return []
    key = sr
    if key not in _spec_cache:
        freqs = np.fft.rfftfreq(SPEC_WIN, 1.0 / sr)
        edges = np.geomspace(90.0, min(6500.0, sr / 2.0 - 100.0), SPEC_BANDS + 1)
        idx = [np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0] for i in range(SPEC_BANDS)]
        for i, ix in enumerate(idx):
            if ix.size == 0:
                idx[i] = np.array([int(np.searchsorted(freqs, edges[i]))])
        mids = np.sqrt(edges[:-1] * edges[1:])
        tilt = 4.0 * np.log2(mids / 200.0)
        _spec_cache[key] = (np.hanning(SPEC_WIN).astype(np.float32), idx, tilt.astype(np.float32))
    win, idx, tilt = _spec_cache[key]
    out = []
    for s in range(0, x.size, SPEC_HOP):
        seg = x[s:s + SPEC_WIN]
        if seg.size < SPEC_WIN:
            seg = np.concatenate([seg, np.zeros(SPEC_WIN - seg.size, dtype=np.float32)])
        rms = float(np.sqrt(np.mean(x[s:s + SPEC_HOP] ** 2))) if x[s:s + SPEC_HOP].size else 0.0
        if rms < 60.0:
            out.append([0.0] * SPEC_BANDS)
            continue
        mag = np.abs(np.fft.rfft((seg - seg.mean()) * win))
        db = np.array([20.0 * np.log10(float(mag[ix].mean()) + 1.0) for ix in idx], dtype=np.float32) + tilt
        v = np.clip((db - 74.0) / 46.0, 0.0, 1.0) ** 1.35
        out.append([round(float(a), 2) for a in v])
    return out

# ── SVG Assets ───────────────────────────────────────────────────────────────
SVG_CHAT = b'''<svg viewBox="0 0 26 26" xmlns="http://www.w3.org/2000/svg">
<path fill="#ffffff" d="M5 3h16a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3h-8l-6 4.5V19H5a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3z"/>
</svg>'''

SVG_TERM = b'''<svg viewBox="0 0 26 26" xmlns="http://www.w3.org/2000/svg">
<rect fill="#ffffff" x="2" y="2" width="22" height="22" rx="5"/>
<path d="M7 9.5l4 3.5-4 3.5M13 17.5h6" fill="none" stroke="#1b1b1a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>'''

SVG_ADD = b'''<svg viewBox="0 0 26 26" xmlns="http://www.w3.org/2000/svg">
<path d="M13 3v20M3 13h20" fill="none" stroke="#ffffff" stroke-width="2.6" stroke-linecap="round"/>
</svg>'''

SVG_AGENTS = b'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
<path fill="none" stroke="#5cc9c3" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
</svg>'''

SVG_SETTINGS = b'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
<path fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z"/>
<path fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M19.4 13.5a7.7 7.7 0 0 0 .06-3l1.9-1.5-1.9-3.3-2.3.9a7.5 7.5 0 0 0-2.6-1.5L14.2 2.6h-3.8l-.4 2.5a7.5 7.5 0 0 0-2.6 1.5l-2.3-.9-1.9 3.3 1.9 1.5a7.7 7.7 0 0 0 0 3l-1.9 1.5 1.9 3.3 2.3-.9a7.5 7.5 0 0 0 2.6 1.5l.4 2.5h3.8l.4-2.5a7.5 7.5 0 0 0 2.6-1.5l2.3.9 1.9-3.3z" transform="translate(-.1 0) scale(.96) translate(.5 .5)"/>
</svg>'''

SVG_VOLUME = b'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
<path fill="#ffffff" d="M3 9.5h3.6L11.5 5v14l-4.9-4.5H3z"/>
<path fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M15 9.3a3.8 3.8 0 0 1 0 5.4M17.6 6.8a7.4 7.4 0 0 1 0 10.4"/>
</svg>'''

SVG_MIC = b'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
<rect fill="#ffffff" x="8.6" y="2.4" width="6.8" height="12.6" rx="3.4"/>
<path fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M5 11.6a7 7 0 0 0 14 0M12 18.6v3M8.6 21.6h6.8"/>
</svg>'''

SVG_MIC_OFF = b'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
<rect fill="#e2201c" x="8.6" y="2.4" width="6.8" height="12.6" rx="3.4"/>
<path fill="none" stroke="#e2201c" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M5 11.6a7 7 0 0 0 14 0M12 18.6v3M8.6 21.6h6.8"/>
<line x1="3" y1="3" x2="21" y2="21" stroke="#e2201c" stroke-width="2.2" stroke-linecap="round"/>
</svg>'''

SVG_FOLDER = b'''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M3 6a2 2 0 0 1 2-2h4.586a1 1 0 0 1 .707.293L12 6.586A1 1 0 0 0 12.707 7H19a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z" fill="#ffffff"/>
</svg>'''

SVG_FILE = b'''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#ffffff"/>
<path d="M14 2v6h6" stroke="#1b1b1a" stroke-width="2"/>
</svg>'''

SVG_LINK = b'''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<circle cx="12" cy="12" r="9" stroke="#ffffff" stroke-width="2"/>
<path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" stroke="#ffffff" stroke-width="2"/>
</svg>'''

SVG_APP = b'''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect x="3" y="4" width="18" height="15" rx="3" stroke="#ffffff" stroke-width="2"/>
<path d="M3 9h18" stroke="#ffffff" stroke-width="2"/>
</svg>'''

SVG_EXTERNAL = b'''<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M15 3h6v6M10 14L21 3" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
</svg>'''

def clamp(v, a, b):
    return max(a, min(b, v))

def mix3(a, b, k):
    return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]

WHITE = [255.0, 255.0, 255.0]
TEAL = [92.0, 201.0, 195.0]
RED = [226.0, 32.0, 28.0]
N = 64
HL = 20
LAYOUT = {"listening": (N, 63.0), "thinking": (16, 63.0), "speaking": (38, 56.5), "idle": (20, 63.0)}
NB = 20
TERM_PANEL_H = 420      # overlay height when the term (code review) panel is open
MISSION_STRIP_H = 26    # extra height under the header while an agent mission runs

# ── Thread-safe Qt Signal Bridge ─────────────────────────────────────────────
class _Bridge(QObject):
    sig_state = pyqtSignal(str)
    sig_level = pyqtSignal(float)
    sig_spec = pyqtSignal(float, float, list)
    sig_log = pyqtSignal(str)
    sig_show_resources = pyqtSignal(list)
    sig_confirm = pyqtSignal(object)
    sig_hide_confirm = pyqtSignal()
    sig_ask_key = pyqtSignal(bool)          # first launch / rejected key: show the key window
    sig_open_team = pyqtSignal(str)
    sig_code_event = pyqtSignal(str, str)   # IDE bridge event, change id
    sig_mission = pyqtSignal(str)           # agent-team mission id that changed
    sig_release_files = pyqtSignal()        # the attached documents have been used

# ── Volume Slider Pill Widget ────────────────────────────────────────────────
class VolumePill(QWidget):
    """Pill popup for controlling app audio volume (matches user design)."""
    def __init__(self, overlay: "OverlayWindow", ui: "JarvisUI"):
        super().__init__()
        self._overlay = overlay
        self._ui = ui

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(320, 38)
        self._dragging = False

        # Auto-hide timer: closes popup after 4 seconds of inactivity
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        # Static grain pixmap
        np.random.seed(42)
        noise = np.random.randint(0, 255, (38, 320), dtype=np.uint8)
        alpha = (noise > 190).astype(np.uint8) * 45
        rgba = np.zeros((38, 320, 4), dtype=np.uint8)
        rgba[:, :, :3] = 255
        rgba[:, :, 3] = alpha
        grain_img = QImage(rgba.data, 320, 38, 320 * 4, QImage.Format.Format_RGBA8888)
        self.grain_pixmap = QPixmap.fromImage(grain_img)

    def reset_autohide(self):
        self._hide_timer.start(4000)

    def showEvent(self, event):
        super().showEvent(event)
        self.reset_autohide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Pill background (#1b1b1a)
        pill_rect = QRectF(0, 0, 320, 38)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0x1B, 0x1B, 0x1A)))
        p.drawRoundedRect(pill_rect, 19, 19)

        # 2. Static film grain
        p.save()
        path = QPainterPath()
        path.addRoundedRect(pill_rect, 19, 19)
        p.setClipPath(path)
        p.setOpacity(0.22)
        p.drawPixmap(0, 0, self.grain_pixmap)
        p.restore()

        # 3. Slider Track
        track_left = 20.0
        track_w = 236.0
        track_y = 17.0
        track_h = 4.0

        v = max(0.0, min(1.0, self._ui.volume))
        fill_w = v * track_w

        # Unfilled white track (right part)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.drawRoundedRect(QRectF(track_left, track_y, track_w, track_h), 2, 2)

        # Filled cyan track (left part) - vibrant cyan from user image
        cyan_col = QColor(0, 161, 197)
        if fill_w > 0:
            p.setBrush(QBrush(cyan_col))
            p.drawRoundedRect(QRectF(track_left, track_y, fill_w, track_h), 2, 2)

        # Thumb (handle)
        thumb_w = 11.0
        thumb_h = 16.0
        thumb_x = track_left + fill_w - thumb_w / 2.0
        thumb_y = 11.0
        p.setBrush(QBrush(cyan_col))
        p.drawRoundedRect(QRectF(thumb_x, thumb_y, thumb_w, thumb_h), 3, 3)

        # 4. Percentage Text
        p.setPen(QColor(255, 255, 255))
        font = QFont("Inter", 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        p.setFont(font)
        pct_str = f"{int(round(v * 100))}%"
        text_rect = QRectF(262, 0, 48, 38)
        p.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), pct_str)

    def _update_from_pos(self, x):
        track_left = 20.0
        track_w = 236.0
        val = max(0.0, min(1.0, (x - track_left) / track_w))
        self._ui.volume = val
        self.reset_autohide()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_from_pos(event.position().x())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._update_from_pos(event.position().x())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def wheelEvent(self, event):
        delta = event.angleDelta().y() / 120.0 * 0.05
        self._ui.volume = max(0.0, min(1.0, self._ui.volume + delta))
        self.reset_autohide()
        self.update()

# ── Chat Panel Widget ────────────────────────────────────────────────────────
class ChatPanel(QWidget):
    """Inner chat panel matching user design with message history and input bar."""
    send_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(8)

        # Scrollable message area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { width: 4px; background: transparent; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,0.22); border-radius: 2px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        self.msg_container = QWidget()
        self.msg_container.setStyleSheet("background: transparent;")
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setContentsMargins(4, 4, 4, 4)
        self.msg_layout.setSpacing(6)
        self.msg_layout.addStretch()

        self.scroll.setWidget(self.msg_container)
        layout.addWidget(self.scroll, 1)

        # Bottom Input Bar (pill shape #5a5a58 from user design)
        input_bar = QWidget()
        input_bar.setFixedHeight(36)
        input_bar.setStyleSheet("""
            QWidget {
                background: #5a5a58;
                border-radius: 18px;
            }
        """)
        in_layout = QHBoxLayout(input_bar)
        in_layout.setContentsMargins(14, 2, 4, 2)
        in_layout.setSpacing(6)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Écrire à EIDOLON...")
        self.input_field.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: #ffffff;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 13px;
                selection-background-color: #00a1c5;
            }
            QLineEdit::placeholder {
                color: rgba(255, 255, 255, 0.55);
            }
        """)
        self.input_field.returnPressed.connect(self._on_send)
        in_layout.addWidget(self.input_field, 1)

        # Circular send button (➔)
        self.send_btn = QPushButton("➔")
        self.send_btn.setFixedSize(28, 28)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background: #e5e5e5;
                color: #1b1b1a;
                border-radius: 14px;
                font-size: 13px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover {
                background: #ffffff;
            }
            QPushButton:pressed {
                background: #cccccc;
            }
        """)
        self.send_btn.clicked.connect(self._on_send)
        in_layout.addWidget(self.send_btn)

        layout.addWidget(input_bar, 0)

    def _on_send(self):
        text = self.input_field.text().strip()
        if text:
            self.input_field.clear()
            self.add_message("You", text)
            self.send_message.emit(text)

    def add_message(self, sender: str, text: str):
        is_user = (sender.lower() == "you")
        is_sys = sender.upper() in ("SYS", "ERR")
        lbl = QLabel(f"<b>{sender}:</b> {text}" if not is_sys else f"<i>{text}</i>")
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        text_col = "#5cc9c3" if is_user else ("#e0e0e0" if not is_sys else "#999999")
        lbl.setStyleSheet(f"""
            QLabel {{
                color: {text_col};
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 12px;
                background: transparent;
                padding: 2px 4px;
            }}
        """)

        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 2, 0, 2)
        row_layout.setSpacing(0)

        if is_user:
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row_layout.addStretch(3)
            row_layout.addWidget(lbl, 7)
        elif is_sys:
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_layout.addWidget(lbl, 1)
        else:
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
            row_layout.addWidget(lbl, 7)
            row_layout.addStretch(3)

        cnt = self.msg_layout.count()
        self.msg_layout.insertWidget(cnt - 1, row)
        QTimer.singleShot(40, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        ))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Inner card background (#40403e from user design)
        card_rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0x40, 0x40, 0x3E)))
        p.drawRoundedRect(card_rect, 16, 16)

# ── Drop Panel Widget ────────────────────────────────────────────────────────
class DropPanel(QWidget):
    """File drop panel matching user design with drag-and-drop & file picker."""
    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._is_drag_over = False
        self._attached_files: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(14)

        self.title_lbl = QLabel("Drop your files here")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 15px;
                font-weight: 500;
                background: transparent;
            }
        """)
        layout.addWidget(self.title_lbl)

        # Pills container
        self.pills_widget = QWidget()
        self.pills_widget.setStyleSheet("background: transparent;")
        self.pills_layout = QHBoxLayout(self.pills_widget)
        self.pills_layout.setContentsMargins(0, 0, 0, 0)
        self.pills_layout.setSpacing(8)
        self.pills_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._update_pills()
        layout.addWidget(self.pills_widget)

    def _update_pills(self):
        while self.pills_layout.count():
            item = self.pills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total_slots = max(4, len(self._attached_files))
        for i in range(total_slots):
            if i < len(self._attached_files):
                f = self._attached_files[i]
                name = Path(f).name
                ext = Path(f).suffix.lower()
                disp = name if len(name) <= 12 else (name[:7] + ".." + ext)
                btn = QPushButton(f"{disp} ✕")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip(f"{name}\nCliquer pour retirer")
                btn.setStyleSheet("""
                    QPushButton {
                        background: #253835;
                        color: #5cc9c3;
                        border: 1px solid rgba(92, 201, 195, 0.45);
                        border-radius: 9px;
                        padding: 3px 9px;
                        font-family: 'Segoe UI', system-ui, sans-serif;
                        font-size: 11px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        background: #3b2222;
                        color: #ff6b6b;
                        border-color: rgba(255, 107, 107, 0.6);
                    }
                """)
                btn.clicked.connect(lambda _, path=f: self.remove_file(path))
                self.pills_layout.addWidget(btn)
            else:
                lbl = QLabel("...")
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet("""
                    QLabel {
                        background: #383836;
                        color: #7a7a78;
                        border-radius: 9px;
                        padding: 3px 14px;
                        font-family: 'Segoe UI', system-ui, sans-serif;
                        font-size: 11px;
                        font-weight: bold;
                    }
                """)
                self.pills_layout.addWidget(lbl)

        count = len(self._attached_files)
        if count == 0:
            self.title_lbl.setText("Drop your files here")
        elif count == 1:
            self.title_lbl.setText(f"✓ {Path(self._attached_files[0]).name}")
        else:
            self.title_lbl.setText(f"✓ {count} fichiers prêts")

    def clear_files(self):
        """Forget every attached document (they have been used)."""
        if self._attached_files:
            self._attached_files = []
            self._update_pills()

    def remove_file(self, file_path: str):
        if file_path in self._attached_files:
            self._attached_files.remove(file_path)
            self._update_pills()
            self.files_dropped.emit(list(self._attached_files))

    def handle_files(self, new_files: list[str]):
        added = False
        for f in new_files:
            if f and f not in self._attached_files:
                self._attached_files.append(f)
                added = True
        if added:
            self._update_pills()
            self.files_dropped.emit(list(self._attached_files))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0, 0, self.width(), self.height()).adjusted(1, 1, -1, -1)

        # Background
        bg_col = QColor(0x28, 0x28, 0x27) if self._is_drag_over else QColor(0x1E, 0x1E, 0x1D)
        p.setBrush(QBrush(bg_col))

        # Dashed border matching design
        pen_col = QColor(92, 201, 195) if self._is_drag_over else QColor(255, 255, 255, 45)
        pen = QPen(pen_col, 1.4, Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 4])
        p.setPen(pen)
        p.drawRoundedRect(rect, 16, 16)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            files, _ = QFileDialog.getOpenFileNames(
                self,
                "Sélectionner des fichiers à joindre",
                "",
                "Tous les fichiers (*.*);;PDF (*.pdf);;Images (*.png *.jpg *.jpeg);;Documents (*.txt *.docx *.csv *.xlsx);;Code (*.py *.js *.html *.json)"
            )
            if files:
                self.handle_files(files)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self._is_drag_over = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._is_drag_over = False
        self.update()

    def dropEvent(self, event):
        self._is_drag_over = False
        self.update()
        files = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if files:
            self.handle_files(files)
            event.acceptProposedAction()

# ── Resource Cards Panel (Generated Files / Folders / Links / Apps) ─────────
class _SvgIconWidget(QWidget):
    def __init__(self, svg_bytes: bytes, w: int = 16, h: int = 16, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.renderer = QSvgRenderer(QByteArray(svg_bytes))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.renderer.render(p, QRectF(0, 0, self.width(), self.height()))

class ResourceCard(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.setMinimumWidth(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(8)

        itype = item.get("type", "file").lower()
        if itype in ("folder", "dir", "directory"):
            svg_data = SVG_FOLDER
        elif itype in ("link", "url", "web"):
            svg_data = SVG_LINK
        elif itype in ("app", "application"):
            svg_data = SVG_APP
        else:
            svg_data = SVG_FILE

        self.icon_widget = _SvgIconWidget(svg_data, 16, 16)
        layout.addWidget(self.icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        name = item.get("name") or Path(item.get("target", "")).name or "Item"
        self.label = QLabel(name)
        self.label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 12px;
                font-weight: 500;
                background: transparent;
                border: none;
            }
        """)
        self.label.setToolTip(f"{name}\n{item.get('target', '')}")
        layout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)

        self.ext_widget = _SvgIconWidget(SVG_EXTERNAL, 13, 13)
        layout.addWidget(self.ext_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setStyleSheet("""
            ResourceCard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #222221, stop:1 #171716);
                border: 1px solid rgba(255, 255, 255, 0.09);
                border-radius: 12px;
            }
            ResourceCard:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2c2c2b, stop:1 #20201f);
                border: 1px solid rgba(255, 255, 255, 0.24);
            }
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item)
            self._open_target()

    def _open_target(self):
        target = self.item.get("target") or self.item.get("name")
        if not target:
            return
        itype = self.item.get("type", "").lower()
        try:
            if itype in ("link", "url", "web") or str(target).startswith("http://") or str(target).startswith("https://"):
                import webbrowser
                webbrowser.open(target)
            elif itype in ("folder", "dir", "directory", "file") or Path(target).exists():
                import os
                os.startfile(str(Path(target).resolve()))
            elif itype in ("app", "application"):
                import subprocess
                subprocess.Popen(f'start "" "{target}"', shell=True)
            else:
                import os
                if Path(target).exists():
                    os.startfile(str(Path(target).resolve()))
                else:
                    import webbrowser
                    webbrowser.open(target)
        except Exception as e:
            print(f"[UI:resource] Open error on {target}: {e}", flush=True)

# ── Confirmation Panel Widget (Accept / Reject with Expandable Text) ───────
class ConfirmationPanel(QWidget):
    """Card panel for confirmations and decision prompts with Accept and Reject buttons."""
    accepted = pyqtSignal(str)
    rejected = pyqtSignal(str)

    def __init__(self, overlay: "OverlayWindow", parent=None):
        super().__init__(parent)
        self._overlay = overlay
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        # Header badge + title
        header = QHBoxLayout()
        header.setSpacing(8)

        self.badge = QLabel("⚡ CONFIRMATION")
        self.badge.setStyleSheet("""
            QLabel {
                color: #5cc9c3;
                background: rgba(92, 201, 195, 0.15);
                border: 1px solid rgba(92, 201, 195, 0.35);
                border-radius: 6px;
                padding: 2px 8px;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }
        """)
        header.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title_lbl = QLabel("Action requise")
        self.title_lbl.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }
        """)
        header.addWidget(self.title_lbl, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        # Scrollable text container for message/question
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 4px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.2);
                border-radius: 2px;
            }
        """)

        self.text_widget = QWidget()
        self.text_widget.setStyleSheet("background: transparent;")
        text_layout = QVBoxLayout(self.text_widget)
        text_layout.setContentsMargins(2, 2, 2, 2)

        self.message_lbl = QLabel("")
        self.message_lbl.setWordWrap(True)
        self.message_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message_lbl.setStyleSheet("""
            QLabel {
                color: #e2e2e0;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 12px;
                line-height: 1.4;
                background: transparent;
            }
        """)
        text_layout.addWidget(self.message_lbl)

        # Dynamic container for duplicate / resource file items
        self.files_container = QWidget()
        self.files_container.setStyleSheet("background: transparent;")
        self.files_layout = QVBoxLayout(self.files_container)
        self.files_layout.setContentsMargins(0, 4, 0, 4)
        self.files_layout.setSpacing(4)
        text_layout.addWidget(self.files_container)

        self.scroll.setWidget(self.text_widget)
        layout.addWidget(self.scroll, 1)

        # Action command strings
        self._accept_cmd = ""
        self._reject_cmd = ""
        self._items = []

        # Buttons row: Reject (Red) & Accept (Cyan/Green)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.btn_reject = QPushButton("✕ Rejeter")
        self.btn_reject.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reject.setFixedHeight(32)
        self.btn_reject.setStyleSheet("""
            QPushButton {
                background: rgba(255, 75, 75, 0.12);
                color: #ff7b7b;
                border: 1px solid rgba(255, 75, 75, 0.35);
                border-radius: 10px;
                padding: 0 16px;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(255, 75, 75, 0.25);
                border-color: rgba(255, 75, 75, 0.65);
                color: #ffffff;
            }
            QPushButton:pressed {
                background: rgba(255, 75, 75, 0.4);
            }
        """)
        self.btn_reject.clicked.connect(self._on_reject)
        btn_layout.addWidget(self.btn_reject, 1)

        self.btn_accept = QPushButton("✓ Accepter")
        self.btn_accept.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_accept.setFixedHeight(32)
        self.btn_accept.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a4d47, stop:1 #24635b);
                color: #5cc9c3;
                border: 1px solid rgba(92, 201, 195, 0.5);
                border-radius: 10px;
                padding: 0 16px;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #236961, stop:1 #2f8277);
                border-color: #5cc9c3;
                color: #ffffff;
            }
            QPushButton:pressed {
                background: #143d38;
            }
        """)
        self.btn_accept.clicked.connect(self._on_accept)
        btn_layout.addWidget(self.btn_accept, 1)

        layout.addLayout(btn_layout)

    def set_content(self, title: str, message: str, accept_lbl: str = "✓ Accepter", reject_lbl: str = "✕ Rejeter", accept_cmd: str = "", reject_cmd: str = "", items: list = None):
        self.title_lbl.setText(title or "Confirmation requise")
        is_dup = "doublon" in (title or "").lower() or "doublon" in (message or "").lower()
        self.badge.setText("🔍 DOUBLONS" if is_dup else "⚡ CONFIRMATION")
        self.message_lbl.setText(message or "")
        self.btn_accept.setText(accept_lbl or "✓ Accepter")
        self.btn_reject.setText(reject_lbl or "✕ Rejeter")
        self._accept_cmd = accept_cmd
        self._reject_cmd = reject_cmd
        self._items = items or []

        # Clear existing file items
        while self.files_layout.count():
            item = self.files_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Add visual chip cards for items (e.g. duplicate files)
        if self._items:
            for it in self._items[:10]:
                target = it.get("target") if isinstance(it, dict) else str(it)
                name = it.get("name") if isinstance(it, dict) else Path(str(it)).name
                chip = QFrame()
                chip.setStyleSheet("""
                    QFrame {
                        background: rgba(255, 255, 255, 0.05);
                        border: 1px solid rgba(255, 255, 255, 0.1);
                        border-radius: 6px;
                    }
                    QFrame:hover {
                        background: rgba(92, 201, 195, 0.12);
                        border-color: rgba(92, 201, 195, 0.4);
                    }
                """)
                chip_l = QHBoxLayout(chip)
                chip_l.setContentsMargins(6, 3, 6, 3)
                chip_l.setSpacing(6)

                fn_lbl = QLabel(name)
                fn_lbl.setStyleSheet("color: #e0e0e0; font-family: 'Segoe UI', system-ui; font-size: 11px; background: transparent;")
                fn_lbl.setToolTip(str(target))
                chip_l.addWidget(fn_lbl, 1)

                if target and Path(target).exists():
                    open_btn = QPushButton("Ouvrir ↗")
                    open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    open_btn.setFixedHeight(20)
                    open_btn.setStyleSheet("""
                        QPushButton {
                            background: rgba(92, 201, 195, 0.2);
                            color: #5cc9c3;
                            border: none;
                            border-radius: 4px;
                            padding: 0 6px;
                            font-size: 10px;
                            font-weight: 600;
                        }
                        QPushButton:hover {
                            background: #5cc9c3;
                            color: #111110;
                        }
                    """)
                    open_btn.clicked.connect(lambda _, t=target: self._open_file(t))
                    chip_l.addWidget(open_btn, 0)

                self.files_layout.addWidget(chip)

            if len(self._items) > 10:
                more_lbl = QLabel(f"... et {len(self._items) - 10} autre(s) fichier(s)")
                more_lbl.setStyleSheet("color: #888886; font-size: 10px; font-style: italic; background: transparent;")
                self.files_layout.addWidget(more_lbl)

    def _open_file(self, target_path: str):
        try:
            import os
            p = Path(target_path)
            if p.exists():
                os.startfile(str(p.resolve()))
        except Exception as e:
            print(f"[UI:confirm] Open file error: {e}", flush=True)

    def _on_accept(self):
        try:
            import core.confirm as confirm
            confirm.resolve(True)
        except Exception:
            pass
        cmd = self._accept_cmd or "Oui, je confirme l'action. Confirme-moi vocalement à haute voix quand c'est fait."
        if hasattr(self._overlay._ui, "on_text_command") and callable(self._overlay._ui.on_text_command):
            self._overlay._ui.on_text_command(cmd)
        self.accepted.emit(self.message_lbl.text())
        self._overlay._collapse()

    def _on_reject(self):
        try:
            import core.confirm as confirm
            confirm.resolve(False)
        except Exception:
            pass
        cmd = self._reject_cmd or "Non, annuler l'action."
        if hasattr(self._overlay._ui, "on_text_command") and callable(self._overlay._ui.on_text_command):
            self._overlay._ui.on_text_command(cmd)
        self.rejected.emit(self.message_lbl.text())

        # If rejection corresponds to examining duplicates, switch to resource cards
        if "examiner" in (self._reject_cmd or "").lower() or "examiner" in self.btn_reject.text().lower():
            if self._overlay._current_resources:
                self._overlay._switch_panel("resources")
                return
        self._overlay._collapse()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card_rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0x27, 0x27, 0x25)))
        p.drawRoundedRect(card_rect, 16, 16)


class ResourceCardsPanel(QWidget):
    """Horizontal container for generated cards (folders, files, links, apps) with scroll support."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:horizontal {
                height: 4px;
                background: rgba(0, 0, 0, 0.2);
                border-radius: 2px;
                margin: 0px 20px 2px 20px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(92, 201, 195, 0.6);
                border-radius: 2px;
                min-width: 20px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
        """)

        self.container = QWidget()
        self.container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.main_layout = QHBoxLayout(self.container)
        self.main_layout.setContentsMargins(14, 4, 14, 8)
        self.main_layout.setSpacing(10)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.scroll.setWidget(self.container)
        outer_layout.addWidget(self.scroll)

    def enterEvent(self, event):
        super().enterEvent(event)
        parent = self.parent()
        if hasattr(parent, "_on_resources_panel_hover"):
            parent._on_resources_panel_hover(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        parent = self.parent()
        if hasattr(parent, "_on_resources_panel_hover"):
            parent._on_resources_panel_hover(False)

    def set_items(self, items: list[dict | str]):
        while self.main_layout.count():
            it = self.main_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        for raw in items:
            if isinstance(raw, str):
                target = raw
                if target.startswith("http://") or target.startswith("https://") or target.startswith("www."):
                    item_type = "link"
                    name = target.replace("https://", "").replace("http://", "").split("/")[0]
                elif Path(target).is_dir() or target.startswith("/") or target.endswith("/"):
                    item_type = "folder"
                    name = f"/{Path(target).name}" if not Path(target).name.startswith("/") else Path(target).name
                elif Path(target).is_file() or "." in Path(target).name:
                    item_type = "file"
                    name = Path(target).name
                else:
                    item_type = "app"
                    name = target
                item_dict = {"type": item_type, "name": name, "target": target}
            else:
                item_dict = raw

            card = ResourceCard(item_dict, self)
            self.main_layout.addWidget(card)

# ── Native Overlay Window ────────────────────────────────────────────────────
class OverlayWindow(QWidget):
    def __init__(self, ui: "JarvisUI", bridge: _Bridge):
        super().__init__()
        self._ui = ui
        self._bridge = bridge

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(512)
        self.setFixedHeight(48)

        # Center at top of screen
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move((geo.width() - 512) // 2, OVERLAY_TOP)

        # State
        self.is_expanded = False
        self.active_panel = None
        self._animating = False

        # Volume popup pill
        self._vol_overlay = VolumePill(self, self._ui)

        # Pre-render SVG icons
        self.renderers = {
            "chat": QSvgRenderer(QByteArray(SVG_CHAT)),
            "term": QSvgRenderer(QByteArray(SVG_TERM)),
            "add": QSvgRenderer(QByteArray(SVG_ADD)),
            "agents": QSvgRenderer(QByteArray(SVG_AGENTS)),
            "settings": QSvgRenderer(QByteArray(SVG_SETTINGS)),
            "volume": QSvgRenderer(QByteArray(SVG_VOLUME)),
            "mic": QSvgRenderer(QByteArray(SVG_MIC)),
            "mic_off": QSvgRenderer(QByteArray(SVG_MIC_OFF)),
        }

        # Buttons geometry (top header bar)
        self.buttons = {
            "chat": QRectF(18, 15, 18, 18),
            "term": QRectF(48, 15, 18, 18),
            "add": QRectF(78, 15, 18, 18),
            "agents": QRectF(108, 15, 18, 18),
            "settings": QRectF(416, 15, 18, 18),
            "volume": QRectF(446, 15, 18, 18),
            "mic": QRectF(476, 15, 18, 18),
        }
        self.hovered_btn = None

        # Static grain pixmap (covers the tallest expanded card, 512 x 420)
        np.random.seed(42)
        noise = np.random.randint(0, 255, (420, 512), dtype=np.uint8)
        alpha = (noise > 190).astype(np.uint8) * 45
        rgba = np.zeros((420, 512, 4), dtype=np.uint8)
        rgba[:, :, :3] = 255
        rgba[:, :, 3] = alpha
        grain_img = QImage(rgba.data, 512, 420, 512 * 4, QImage.Format.Format_RGBA8888)
        self.grain_pixmap = QPixmap.fromImage(grain_img)

        # Chat Panel child widget
        self.chat_panel = ChatPanel(self)
        self.chat_panel.setGeometry(12, 50, 488, 198)
        self._chat_opacity = QGraphicsOpacityEffect(self.chat_panel)
        self._chat_opacity.setOpacity(0.0)
        self.chat_panel.setGraphicsEffect(self._chat_opacity)
        self.chat_panel.send_message.connect(self._on_user_send)
        self.chat_panel.hide()

        # Drop Panel child widget
        self.drop_panel = DropPanel(self)
        self.drop_panel.setGeometry(12, 50, 488, 198)
        self._drop_opacity = QGraphicsOpacityEffect(self.drop_panel)
        self._drop_opacity.setOpacity(0.0)
        self.drop_panel.setGraphicsEffect(self._drop_opacity)
        self.drop_panel.files_dropped.connect(self._on_files_dropped)
        self.drop_panel.hide()

        # Resource Cards Panel child widget (generated files/folders/links/apps)
        self.resources_panel = ResourceCardsPanel(self)
        self.resources_panel.setGeometry(0, 50, 512, 54)
        self._resources_opacity = QGraphicsOpacityEffect(self.resources_panel)
        self._resources_opacity.setOpacity(0.0)
        self.resources_panel.setGraphicsEffect(self._resources_opacity)
        self.resources_panel.hide()

        # Confirmation Panel child widget (decision cards with Accept/Reject buttons)
        self.confirm_panel = ConfirmationPanel(self, self)
        self.confirm_panel.setGeometry(12, 50, 488, 198)
        self._confirm_opacity = QGraphicsOpacityEffect(self.confirm_panel)
        self._confirm_opacity.setOpacity(0.0)
        self.confirm_panel.setGraphicsEffect(self._confirm_opacity)
        self.confirm_panel.hide()

        # Code review panel (term button): diff preview of what the coding AI changed
        from core.code_review_panel import CodeReviewPanel
        self.term_panel = CodeReviewPanel(self)
        self.term_panel.setGeometry(0, 48, 512, TERM_PANEL_H - 48)
        self._term_opacity = QGraphicsOpacityEffect(self.term_panel)
        self._term_opacity.setOpacity(0.0)
        self.term_panel.setGraphicsEffect(self._term_opacity)
        self.term_panel.all_answered.connect(self._on_term_all_answered)
        self.term_panel.hide()
        self._term_auto_opened = False

        # Multi-resource accumulation & auto-hide timer (default 25s, configurable)
        self._current_resources: list[dict | str] = []
        self._resources_timer = QTimer(self)
        self._resources_timer.setSingleShot(True)
        self._resources_timer.timeout.connect(self._auto_hide_resources)

        # 64 morphing elements
        self.E = []
        for i in range(N):
            self.E.append({
                "u": (i / (N - 1)) * 2.0 - 1.0,
                "x": 0.0, "y": 0.0, "hw": 0.0, "hh": 0.0, "a": 0.0,
                "c": list(WHITE), "rot": 0.0
            })

        self.hist = [0.0] * HL
        self.state = "idle"
        self.level = 0.0
        self.peak = 0.2
        self.since = time.time()
        self.t = 0.0
        self.last_time = time.time()

        # Voice spectrum schedule
        self.spec_q = []
        self.spec_cur = [0.0] * NB
        self.spec_prev = [0.0] * NB
        self.spec_live = 0.0

        # Dragging support
        self._drag_pos = None

        # Connect signals
        self._bridge.sig_state.connect(self._on_set_state)
        self._bridge.sig_level.connect(self._on_set_level)
        self._bridge.sig_spec.connect(self._on_push_spec)
        self._bridge.sig_log.connect(self._on_log_received)
        self._bridge.sig_show_resources.connect(self._on_display_resources)
        self._bridge.sig_confirm.connect(self._on_show_confirm)
        self._bridge.sig_hide_confirm.connect(self._on_hide_confirm)
        self._bridge.sig_open_team.connect(self._open_agent_orchestrator)
        self._bridge.sig_code_event.connect(self._on_code_event)

        # Agent-team missions: a thin strip under the header while one runs.
        self._mission_id = ""
        self._mission_text = ""
        self._mission_color = QColor(0x5C, 0xC9, 0xC3)
        self._mission_frac = 0.0
        self._mission_hide = QTimer(self, singleShot=True)
        self._mission_hide.timeout.connect(lambda: self._set_mission_strip("", "", 0.0))
        self._bridge.sig_mission.connect(self._on_mission)
        self._bridge.sig_release_files.connect(self._on_release_files)

        # The drop panel folds itself away once files are attached (restarted
        # on every new drop, so several files can still be added in a row).
        self._drop_close = QTimer(self, singleShot=True, interval=1500)
        self._drop_close.timeout.connect(lambda: self._fold_if("add"))
        # The term panel empties and folds once nothing waits for an answer.
        self._term_close = QTimer(self, singleShot=True, interval=700)
        self._term_close.timeout.connect(self._finish_term)
        try:
            from core.agent_events import get_event_bus
            get_event_bus().subscribe(
                lambda ev: self._bridge.sig_mission.emit(ev.mission_id)
                if ev.event_type == "mission.update" else None)
        except Exception as e:
            print(f"[UI:team:err] {e}", flush=True)

        # IDE bridge: when a coding AI asks before editing a file, the overlay
        # unfolds on the term panel by itself.
        try:
            from core.ide_bridge import get_bridge
            get_bridge().subscribe(
                lambda what, c: self._bridge.sig_code_event.emit(what, c.id if c else ""))
        except Exception as e:
            print(f"[UI:code:err] IDE bridge not started: {e}", flush=True)

        # 60 FPS animation timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        self.timer.start(16)

        # Level sampler (30 Hz)
        self.level_timer = QTimer(self)
        self.level_timer.timeout.connect(self._sample_level)
        self.level_timer.start(33)

        self._settings_win = None
        self.apply_live_settings()

    # ── Panel Expansion Animations (Cubic Bezier + Fade-In) ───────────────────
    def _target_height_for_panel(self, panel_name: str) -> int:
        if panel_name == "resources":
            return 112
        if panel_name == "term":
            return TERM_PANEL_H
        return 260

    def _get_panel_widget_and_effect(self, panel_name: str):
        if panel_name == "chat":
            return self.chat_panel, self._chat_opacity
        elif panel_name == "add":
            return self.drop_panel, self._drop_opacity
        elif panel_name == "resources":
            return self.resources_panel, self._resources_opacity
        elif panel_name == "confirm":
            return self.confirm_panel, self._confirm_opacity
        elif panel_name == "term":
            return self.term_panel, self._term_opacity
        return None, None

    def _toggle_panel(self, panel_name: str):
        if self._animating:
            return
        if self.is_expanded:
            if self.active_panel == panel_name:
                self._collapse()
            else:
                self._switch_panel(panel_name)
        else:
            self._expand(panel_name)

    def _expand(self, panel_name: str):
        self._vol_overlay.hide()
        self.is_expanded = True
        self.active_panel = panel_name
        self._animating = True
        self.update()

        target_h = self._target_height_for_panel(panel_name)
        self._height_anim = QVariantAnimation(self)
        self._height_anim.setDuration(340)
        self._height_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._height_anim.setStartValue(self.height())
        self._height_anim.setEndValue(target_h)
        self._height_anim.valueChanged.connect(lambda h: (self.setFixedHeight(int(h)), self.update()))
        self._height_anim.finished.connect(self._on_expand_finished)
        self._height_anim.start()

    def _on_expand_finished(self):
        target_widget, target_effect = self._get_panel_widget_and_effect(self.active_panel)
        for p in (self.chat_panel, self.drop_panel, self.resources_panel, self.confirm_panel, self.term_panel):
            if p != target_widget:
                p.hide()
        if target_widget and target_effect:
            target_widget.show()
            target_effect.setOpacity(0.0)
            self._fade_anim = QVariantAnimation(self)
            self._fade_anim.setDuration(220)
            self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.valueChanged.connect(lambda o: target_effect.setOpacity(float(o)))
            self._fade_anim.finished.connect(self._on_fade_in_finished)
            self._fade_anim.start()

    def _on_fade_in_finished(self):
        self._animating = False
        if self.active_panel == "chat":
            self.chat_panel.input_field.setFocus()

    def _get_resources_duration(self) -> float:
        try:
            from memory.config_manager import get_resources_display_duration
            return float(get_resources_display_duration())
        except Exception:
            return 25.0

    def _on_resources_panel_hover(self, is_hovered: bool):
        if self.active_panel != "resources":
            return
        if is_hovered:
            if self._resources_timer.isActive():
                self._resources_timer.stop()
        else:
            if self.is_expanded and self.active_panel == "resources":
                duration = self._get_resources_duration()
                self._resources_timer.start(int(duration * 1000))

    def _auto_hide_resources(self):
        if self.is_expanded and self.active_panel == "resources" and not self._animating:
            self._collapse()

    def _switch_panel(self, panel_name: str):
        if self._resources_timer.isActive():
            self._resources_timer.stop()
        self._current_resources = []
        self._animating = True
        cur_widget, cur_effect = self._get_panel_widget_and_effect(self.active_panel)
        self.active_panel = panel_name
        self.update()

        target_h = self._target_height_for_panel(panel_name)

        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(120)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_anim.setStartValue(cur_effect.opacity() if cur_effect else 1.0)
        self._fade_anim.setEndValue(0.0)
        if cur_effect:
            self._fade_anim.valueChanged.connect(lambda o: cur_effect.setOpacity(float(o)))

        def _do_switch():
            for p in (self.chat_panel, self.drop_panel, self.resources_panel, self.confirm_panel, self.term_panel):
                p.hide()
            target_widget, target_effect = self._get_panel_widget_and_effect(self.active_panel)
            if self.height() != target_h:
                self._h_anim = QVariantAnimation(self)
                self._h_anim.setDuration(240)
                self._h_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
                self._h_anim.setStartValue(self.height())
                self._h_anim.setEndValue(target_h)
                self._h_anim.valueChanged.connect(lambda h: (self.setFixedHeight(int(h)), self.update()))
                def _height_done():
                    if target_widget and target_effect:
                        target_widget.show()
                        target_effect.setOpacity(0.0)
                        self._fade_anim2 = QVariantAnimation(self)
                        self._fade_anim2.setDuration(180)
                        self._fade_anim2.setEasingCurve(QEasingCurve.Type.InOutQuad)
                        self._fade_anim2.setStartValue(0.0)
                        self._fade_anim2.setEndValue(1.0)
                        self._fade_anim2.valueChanged.connect(lambda o: target_effect.setOpacity(float(o)))
                        self._fade_anim2.finished.connect(self._on_fade_in_finished)
                        self._fade_anim2.start()
                self._h_anim.finished.connect(_height_done)
                self._h_anim.start()
            else:
                if target_widget and target_effect:
                    target_widget.show()
                    target_effect.setOpacity(0.0)
                    self._fade_anim2 = QVariantAnimation(self)
                    self._fade_anim2.setDuration(180)
                    self._fade_anim2.setEasingCurve(QEasingCurve.Type.InOutQuad)
                    self._fade_anim2.setStartValue(0.0)
                    self._fade_anim2.setEndValue(1.0)
                    self._fade_anim2.valueChanged.connect(lambda o: target_effect.setOpacity(float(o)))
                    self._fade_anim2.finished.connect(self._on_fade_in_finished)
                    self._fade_anim2.start()

        self._fade_anim.finished.connect(_do_switch)
        self._fade_anim.start()

    def _collapse(self):
        if self._resources_timer.isActive():
            self._resources_timer.stop()
        self._current_resources = []
        self._animating = True
        _, cur_effect = self._get_panel_widget_and_effect(self.active_panel)

        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(160)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_anim.setStartValue(cur_effect.opacity() if cur_effect else 1.0)
        self._fade_anim.setEndValue(0.0)
        if cur_effect:
            self._fade_anim.valueChanged.connect(lambda o: cur_effect.setOpacity(float(o)))
        self._fade_anim.finished.connect(self._on_fade_out_finished)
        self._fade_anim.start()

    def _on_fade_out_finished(self):
        self.chat_panel.hide()
        self.drop_panel.hide()
        self.resources_panel.hide()
        self.confirm_panel.hide()
        self.term_panel.hide()
        self._term_auto_opened = False

        self._height_anim = QVariantAnimation(self)
        self._height_anim.setDuration(300)
        self._height_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._height_anim.setStartValue(self.height())
        self._height_anim.setEndValue(self._base_height())
        self._height_anim.valueChanged.connect(lambda h: (self.setFixedHeight(int(h)), self.update()))
        self._height_anim.finished.connect(self._on_collapse_finished)
        self._height_anim.start()

    def _on_collapse_finished(self):
        self.is_expanded = False
        self.active_panel = None
        self._animating = False
        self.setFixedHeight(self._base_height())
        self.update()

    # ── Agent-team mission strip ─────────────────────────────────────────────
    def _base_height(self) -> int:
        """Folded height: the header, plus the mission strip when one is shown."""
        return 48 + (MISSION_STRIP_H if self._mission_text else 0)

    def _on_mission(self, mission_id: str):
        try:
            from core.agent_team import get_team, Status
            m = get_team().get(mission_id)
        except Exception:
            return
        if not m:
            if mission_id == self._mission_id:
                self._set_mission_strip("", "", 0.0)
            return
        # Another mission is shown and still running: keep it.
        if self._mission_id and mission_id != self._mission_id and self._mission_hide.isActive() is False:
            cur = get_team().get(self._mission_id)
            if cur and cur.status in Status.ACTIVE + (Status.CLARIFYING,):
                return
        done, total = m.progress()
        working = [a.agent_id.upper() for a in m.assignments if a.status == "working"]
        title = m.title if len(m.title) <= 34 else m.title[:33] + "…"
        if m.status == Status.WORKING and total:
            text = f"Équipe · {title} — {done}/{total}" + (f" · {', '.join(working[:3])} au travail" if working else "")
        else:
            text = f"Équipe · {title} — {m.status_label()}"
        color = {Status.DONE: QColor(0x4C, 0xC3, 0x8A), Status.FAILED: QColor(0xE5, 0x53, 0x4B),
                 Status.CANCELLED: QColor(0x8C, 0x8C, 0x88), Status.CLARIFYING: QColor(0xF0, 0xA4, 0x3B)
                 }.get(m.status, QColor(0x5C, 0xC9, 0xC3))
        frac = 1.0 if m.status == Status.DONE else (done / total * 0.85 + 0.1 if total else 0.05)
        self._mission_color = color
        self._set_mission_strip(m.id, text, frac)
        if m.status in (Status.DONE, Status.FAILED, Status.CANCELLED):
            self._mission_hide.start(60_000 if m.status == Status.DONE else 20_000)
        else:
            self._mission_hide.stop()

    def _set_mission_strip(self, mission_id: str, text: str, frac: float):
        had = bool(self._mission_text)
        self._mission_id, self._mission_text, self._mission_frac = mission_id, text, frac
        if not self.is_expanded and not self._animating and had != bool(text):
            self._strip_anim = QVariantAnimation(self)
            self._strip_anim.setDuration(220)
            self._strip_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._strip_anim.setStartValue(self.height())
            self._strip_anim.setEndValue(self._base_height())
            self._strip_anim.valueChanged.connect(lambda h: (self.setFixedHeight(int(h)), self.update()))
            self._strip_anim.start()
        self.update()

    def _paint_mission_strip(self, p: QPainter):
        if self.is_expanded or not self._mission_text or self.height() <= 50:
            return
        y = 48
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(QRectF(18, y + 17, 476, 3), 1.5, 1.5)
        p.setBrush(self._mission_color)
        p.drawRoundedRect(QRectF(18, y + 17, max(6.0, 476 * self._mission_frac), 3), 1.5, 1.5)
        font = QFont("Segoe UI", 8)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 200))
        fm = p.fontMetrics()
        p.drawText(QRectF(18, y - 2, 476, 18), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   fm.elidedText(self._mission_text, Qt.TextElideMode.ElideRight, 476))
        p.restore()

    def _on_display_resources(self, items: list):
        if not items:
            return

        # If already showing resources, accumulate new items without duplicate targets or names
        if self.is_expanded and self.active_panel == "resources":
            for new_it in items:
                new_target = new_it.get("target") if isinstance(new_it, dict) else str(new_it)
                new_name = new_it.get("name") if isinstance(new_it, dict) else str(new_it)
                already = False
                for cur in self._current_resources:
                    c_target = cur.get("target") if isinstance(cur, dict) else str(cur)
                    c_name = cur.get("name") if isinstance(cur, dict) else str(cur)
                    if new_target and c_target and new_target == c_target:
                        already = True
                        break
                    if new_name and c_name and new_name == c_name:
                        already = True
                        break
                if not already:
                    self._current_resources.append(new_it)
        else:
            self._current_resources = list(items)

        self.resources_panel.set_items(self._current_resources)

        # Reset / start the auto-hide timer (default 25s, configurable in settings)
        duration = self._get_resources_duration()
        self._resources_timer.stop()
        self._resources_timer.start(int(duration * 1000))

        # If already expanded on resources, keep panel visible and refresh layout
        if self.is_expanded and self.active_panel == "resources":
            self.resources_panel.show()
            self._resources_opacity.setOpacity(1.0)
            self.update()
            return

        # Hide any open panel immediately
        for p in (self.chat_panel, self.drop_panel, self.confirm_panel, self.term_panel):
            p.hide()
        if hasattr(self, "_chat_opacity"):
            self._chat_opacity.setOpacity(0.0)
        if hasattr(self, "_drop_opacity"):
            self._drop_opacity.setOpacity(0.0)
        if hasattr(self, "_confirm_opacity"):
            self._confirm_opacity.setOpacity(0.0)

        self._vol_overlay.hide()
        self.is_expanded = True
        self.active_panel = "resources"
        self._animating = True
        self.update()

        target_h = 112
        self._height_anim = QVariantAnimation(self)
        self._height_anim.setDuration(280)
        self._height_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._height_anim.setStartValue(self.height())
        self._height_anim.setEndValue(target_h)
        self._height_anim.valueChanged.connect(lambda h: (self.setFixedHeight(int(h)), self.update()))
        self._height_anim.finished.connect(self._on_resources_expand_finished)
        self._height_anim.start()

    def _on_resources_expand_finished(self):
        self.resources_panel.show()
        self._resources_opacity.setOpacity(0.0)
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(220)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.valueChanged.connect(lambda o: self._resources_opacity.setOpacity(float(o)))
        self._fade_anim.finished.connect(lambda: setattr(self, "_animating", False))
        self._fade_anim.start()

    def _on_show_confirm(self, payload: Any, detail: str = "", accept_lbl: str = "✓ Confirmer", reject_lbl: str = "✕ Annuler", accept_cmd: str = "", reject_cmd: str = "", items: list = None):
        if isinstance(payload, dict):
            title = payload.get("title", "Confirmation requise")
            detail = payload.get("detail", "")
            accept_lbl = payload.get("accept_label", "✓ Confirmer")
            reject_lbl = payload.get("reject_label", "✕ Annuler")
            accept_cmd = payload.get("accept_cmd", "")
            reject_cmd = payload.get("reject_cmd", "")
            items = payload.get("items", [])
        else:
            title = str(payload)
        self.confirm_panel.set_content(title, detail, accept_lbl, reject_lbl, accept_cmd, reject_cmd, items)
        if self.is_expanded:
            if self.active_panel != "confirm":
                self._switch_panel("confirm")
        else:
            self._expand("confirm")

    def _on_hide_confirm(self):
        if self.is_expanded and self.active_panel == "confirm" and not self._animating:
            self._collapse()

    def _on_files_dropped(self, files: list[str]):
        if not files:   # last pill removed by hand
            self._ui._current_file = None
            self._ui.attached_files = []
            return
        names = [Path(f).name for f in files]
        self._ui._current_file = files[0]
        self._ui.attached_files = files
        self.chat_panel.add_message("SYS", f"📁 Fichier(s) joint(s) : {', '.join(names)}")
        print(f"[UI:files] Attached {len(files)} files: {names}", flush=True)
        self._drop_close.start()

    def _on_release_files(self):
        """JARVIS finished a turn that used the attached documents: forget them
        and give the screen back."""
        if not getattr(self._ui, "attached_files", None) and not self._ui._current_file:
            return
        self.drop_panel.clear_files()
        self._ui._current_file = None
        self._ui.attached_files = []
        print("[UI:files] Attached documents released", flush=True)
        self._fold_if("add")

    def _fold_if(self, panel: str):
        """Fold the overlay if it is showing `panel` — never closes what you
        switched to in the meantime."""
        if not (self.is_expanded and self.active_panel == panel):
            return
        if self._animating:
            QTimer.singleShot(400, lambda: self._fold_if(panel))
            return
        self._collapse()

    def _on_user_send(self, text: str):
        if self._ui.on_text_command:
            threading.Thread(target=self._ui.on_text_command, args=(text,), daemon=True).start()
        else:
            print(f"[Chat] {text}", flush=True)

    def _on_log_received(self, text: str):
        asst = self._ui.assistant_name
        if text.startswith("You: "):
            # Avoid duplicating messages sent directly from the chat box
            pass
        elif text.startswith(f"{asst}: "):
            msg = text[len(asst) + 2:]
            self.chat_panel.add_message(asst, msg)

            # Auto-detect if assistant specifically asks for an action confirmation
            # (e.g. duplicate files decision or explicit destructive action confirmation)
            msg_lower = msg.lower()
            is_dup_decision = (
                ("doublon" in msg_lower or "duplicate" in msg_lower or "identique" in msg_lower) and
                ("supprimer d'un coup" in msg_lower or "examiner individuellement" in msg_lower or "que voulez-vous faire" in msg_lower)
            )
            is_explicit_confirmation = (
                any(phrase in msg_lower for phrase in [
                    "confirmez-vous", "validez-vous", "êtes-vous sûr de vouloir", "etes-vous sur de vouloir",
                    "dois-je procéder", "voulez-vous que je supprime", "voulez-vous supprimer", "confirmer la suppression"
                ]) and not any(open_q in msg_lower for open_q in [
                    "quelle mission", "comment puis-je", "que puis-je", "quel ", "quelle ", "quels ", "quelles "
                ])
            )

            is_chat_active = (self.is_expanded and self.active_panel == "chat")
            if not is_chat_active and (is_dup_decision or is_explicit_confirmation):
                if is_dup_decision:
                    title = f"{asst} — Doublons détectés"
                    accept_lbl = "✓ Supprimer d'un coup"
                    reject_lbl = "✕ Examiner individuellement"
                    accept_cmd = "Supprimer les doublons d'un coup en conservant un seul original par groupe"
                    reject_cmd = "Examiner les doublons individuellement"
                else:
                    title = f"{asst} — Confirmation requise"
                    accept_lbl = "✓ Confirmer"
                    reject_lbl = "✕ Annuler"
                    accept_cmd = "Oui, je confirme l'action."
                    reject_cmd = "Non, annuler l'action."

                self._on_show_confirm({
                    "title": title,
                    "detail": msg,
                    "accept_label": accept_lbl,
                    "reject_label": reject_lbl,
                    "accept_cmd": accept_cmd,
                    "reject_cmd": reject_cmd,
                    "items": self._current_resources if is_dup_decision else []
                })
        elif text.startswith("SYS: "):
            self.chat_panel.add_message("SYS", text[5:])
        elif text.startswith("ERR: "):
            self.chat_panel.add_message("ERR", text[5:])

    # ── Settings Window & Live Settings Application ─────────────────────────
    def apply_live_settings(self, cfg: dict | None = None):
        if cfg is None:
            cfg = _read_full_config()

        # 1. Opacity au repos
        try:
            op = float(cfg.get("overlay_opacity", 1.0))
            self.setWindowOpacity(max(0.2, min(1.0, op)))
        except Exception:
            pass

        # 2. Toujours au premier plan (Always on top)
        try:
            ontop = bool(cfg.get("always_on_top", True))
            flags = self.windowFlags()
            if ontop:
                flags |= Qt.WindowType.WindowStaysOnTopHint
            else:
                flags &= ~Qt.WindowType.WindowStaysOnTopHint
            if flags != self.windowFlags():
                curr_pos = self.pos()
                self.setWindowFlags(flags)
                self.move(curr_pos)
                self.show()
        except Exception:
            pass

        # 3. Volume
        if "app_volume" in cfg:
            try:
                self._ui.volume = float(cfg["app_volume"])
            except Exception:
                pass

        # 4. Voix (only trigger reconnect if voice changed)
        v_name = cfg.get("voice_name")
        if v_name:
            if not hasattr(self, "_current_voice"):
                self._current_voice = v_name
            elif v_name != self._current_voice:
                self._current_voice = v_name
                if self._ui.on_voice_change:
                    try:
                        self._ui.on_voice_change(v_name)
                    except TypeError:
                        try:
                            self._ui.on_voice_change()
                        except Exception:
                            pass
                    except Exception:
                        pass

        # 4b. Moteur vocal : gemini (Live) <-> edgetts/kokoro (Low token)
        eng = str(cfg.get("tts_engine", "edgetts")).lower()
        low = eng in ("edgetts", "kokoro")
        if not hasattr(self, "_current_low_token"):
            self._current_low_token = low
        elif low != self._current_low_token:
            self._current_low_token = low
            if self._ui.on_voice_change:
                try:
                    self._ui.on_voice_change()
                except Exception:
                    pass

        # 4c. Source LLM : API keys <-> LM Studio / Local
        llm_cfg = (
            str(cfg.get("llm_source", "API keys")),
            str(cfg.get("llm_host_server", "http://localhost")),
            str(cfg.get("llm_host_port", 1234)),
            str(cfg.get("llm_host_name", "")),
        )
        if not hasattr(self, "_current_llm_cfg"):
            self._current_llm_cfg = llm_cfg
        elif llm_cfg != self._current_llm_cfg:
            self._current_llm_cfg = llm_cfg
            if self._ui.on_voice_change:
                try:
                    self._ui.on_voice_change()
                except Exception:
                    pass

        # 5. Audio Devices (only trigger reconnect if devices changed)
        in_dev = cfg.get("input_device", "")
        out_dev = cfg.get("output_device", "")
        dev_tuple = (in_dev, out_dev)
        if in_dev or out_dev:
            if not hasattr(self, "_current_audio_devices"):
                self._current_audio_devices = dev_tuple
            elif dev_tuple != self._current_audio_devices:
                self._current_audio_devices = dev_tuple
                if self._ui.on_audio_device_change:
                    try:
                        self._ui.on_audio_device_change(in_dev, out_dev)
                    except TypeError:
                        try:
                            self._ui.on_audio_device_change()
                        except Exception:
                            pass
                    except Exception:
                        pass

        # 6. Wake word
        if "wake_word_enabled" in cfg:
            if self._ui.on_wake_toggle:
                self._ui.on_wake_toggle(bool(cfg.get("wake_word_enabled", False)))

        self.update()

    def _open_settings(self):
        try:
            from core.settings_window import SettingsWindow
            if not hasattr(self, "_settings_win") or self._settings_win is None:
                self._settings_win = SettingsWindow(ui=self._ui, overlay=self)
                self._settings_win.settings_saved.connect(self.apply_live_settings)
            self._settings_win.show()
            self._settings_win.raise_()
            self._settings_win.activateWindow()
        except Exception as e:
            print(f"[UI:settings:err] {e}", flush=True)

    def _on_code_event(self, what: str, change_id: str):
        if what in ("ask", "change", "decided", "activity", "roots", "cleared"):
            self.term_panel.on_bridge_event(what, change_id)
        if what == "decided":
            from core.ide_bridge import get_bridge
            if get_bridge().pending():
                self._term_close.stop()
            else:
                self._term_close.start()
        elif what in ("ask", "change"):
            self._term_close.stop()
        if what == "ask":
            self.term_panel.focus_change(change_id)
            self._open_term(auto=True)
            self._announce_code_ask(change_id)
        elif what == "open":
            self._open_term(auto=False)

    def _announce_code_ask(self, change_id: str):
        """Say it out loud — the screen may not be in front of you. One line per
        burst: several edits asked within 20 s are announced once."""
        try:
            from core.ide_bridge import get_bridge
            c = get_bridge().get(change_id)
            say = self._ui.request_say
            if not c or not say or time.time() - getattr(self, "_last_code_announce", 0) < 20:
                return
            self._last_code_announce = time.time()
            if c.command:
                what = f"exécuter une commande risquée : {c.command[:80]}"
            else:
                verb = {"create": "créer", "delete": "supprimer"}.get(c.kind, "modifier")
                what = f"{verb} le fichier {Path(c.path).name}"
            n = len(get_bridge().pending())
            more = f" ({n} demandes en attente)" if n > 1 else ""
            # The content goes with the announcement, so "qu'est-ce que ça fait ?"
            # gets an answer straight away.
            detail = get_bridge().explain(c, max_lines=40)
            threading.Thread(target=say, daemon=True, args=(
                f"[Demande de {c.source} — {c.why or 'validation'}] Dis en une phrase courte, dans la "
                f"langue de l'utilisateur, que {c.source} veut {what}{more} et qu'il peut répondre dans "
                f"le panneau ou à la voix (« accepte » / « refuse »). Si ensuite il demande ce que ça "
                f"fait, explique-le simplement à partir de ce contenu (ou appelle code_review "
                f"action='explain'). N'accepte ni ne refuse sans son ordre.\n\n{detail}",)).start()
        except Exception as e:
            print(f"[UI:code:err] announce: {e}", flush=True)

    def _open_term(self, auto: bool):
        if self._animating:   # an animation is running — try again right after it
            QTimer.singleShot(450, lambda: self._open_term(auto))
            return
        self.show()
        self.raise_()
        if not self.is_expanded:
            self._term_auto_opened = auto
            self._expand("term")
        elif self.active_panel != "term":
            self._term_auto_opened = auto
            self._switch_panel("term")

    def _on_term_all_answered(self):
        self._term_close.start()

    def _finish_term(self):
        """Everything answered: empty the panel and fold the overlay — the app
        stays out of the way until something needs you again."""
        try:
            from core.ide_bridge import get_bridge
            bridge = get_bridge()
            if bridge.pending():
                return
            bridge.clear_history()
        except Exception as e:
            print(f"[UI:code:err] {e}", flush=True)
        self.term_panel.refresh()
        self._fold_if("term")

    def _open_agent_orchestrator(self, mission_id: str = ""):
        try:
            from core.agent_team_window import AgentTeamWindow
            if not hasattr(self, "_agent_win") or self._agent_win is None:
                self._agent_win = AgentTeamWindow(parent=None)
            if mission_id:
                self._agent_win.show_mission(mission_id)
            self._agent_win.show()
            self._agent_win.raise_()
            self._agent_win.activateWindow()
        except Exception as e:
            print(f"[UI:agents:err] {e}", flush=True)

    # ── Volume Toggle ────────────────────────────────────────────────────────
    def _toggle_volume(self):
        if self._vol_overlay.isVisible():
            self._vol_overlay.hide()
        else:
            vx = self.x() + (self.width() - self._vol_overlay.width()) // 2
            vy = self.y() + self.height() + 8
            self._vol_overlay.move(vx, vy)
            self._vol_overlay.show()
            self._vol_overlay.raise_()
            self._vol_overlay.reset_autohide()

    def _on_set_state(self, s: str):
        s = s.lower()
        if s in ("talk", "speaking"):
            s = "speaking"
        elif s in ("sleep", "idle", "muted"):
            s = "idle"
        elif s == "listening":
            s = "listening"
        elif s == "thinking":
            s = "thinking"
        if s != self.state:
            self.state = s
            self.since = time.time()
        self.update()

    def _on_set_level(self, lv: float):
        self.level = max(self.level, float(lv))

    def _on_push_spec(self, at: float, hop: float, frames: list):
        now = time.time()
        for i, b in enumerate(frames):
            t_frame = at + i * hop
            if t_frame >= now - 0.1:
                self.spec_q.append({"t": t_frame, "hop": hop, "b": b})

    def _sample_level(self):
        self.peak = max(self.level, self.peak * 0.995, 0.12)
        v = clamp(self.level / self.peak, 0.0, 1.0)
        self.hist.pop()
        self.hist.insert(0, v)
        self.level *= 0.88

    def _step_spec(self, dt: float):
        now = time.time()
        q = self.spec_q
        while len(q) > 1 and q[1]["t"] <= now:
            q.pop(0)
        b = None
        if len(q) and q[0]["t"] <= now and now < q[0]["t"] + q[0]["hop"] * 1.5:
            b = q[0]["b"]
        if b:
            self.spec_live = now
        up = 1.0 - math.exp(-dt * 16.0)
        down = 1.0 - math.exp(-dt * 7.0)
        for k in range(NB):
            self.spec_prev[k] += (self.spec_cur[k] - self.spec_prev[k]) * (1.0 - math.exp(-dt * 30.0))
            v = b[k] if b else 0.0
            self.spec_cur[k] += (v - self.spec_cur[k]) * (up if v > self.spec_cur[k] else down)

    def _target(self, s: str, i: int, t: float):
        e = self.E[i]
        M, span = LAYOUT[s]
        k = round((i / (N - 1)) * (M - 1))
        rep = round(k * (N - 1) / (M - 1))
        vis = (M == N) or (i == rep)
        ku = 0.0 if M == 1 else (k / (M - 1)) * 2.0 - 1.0
        x = ku * span
        p = {"x": x, "y": 0.0, "hw": 0.0, "hh": 0.0, "a": 1.0 if vis else 0.0, "c": WHITE}

        if s == "speaking":
            d = abs(ku)
            side = 0 if ku < 0 else 1
            band = clamp(round(d * (NB - 1)), 0, NB - 1)
            live = (time.time() - self.spec_live) < 0.6
            if live or self.spec_cur[band] > 0.02:
                v = (self.spec_prev if side else self.spec_cur)[band]
            else:
                idx = clamp(round(d * (HL - 2)) + side, 0, HL - 1)
                v = self.hist[idx] * (1.0 - 0.22 * d)
            p["hw"] = 0.55 if vis else 0.0
            p["hh"] = max(0.8, v * 16.5 * (1.0 - 0.25 * d)) if vis else 0.0
        elif s == "listening":
            idx = clamp(round(abs(e["u"]) * (HL - 1)), 0, HL - 1)
            taper = math.pow(max(0.0, math.cos(e["u"] * math.pi / 2.0)), 0.5)
            A = (1.5 + 7.5 * self.hist[idx]) * taper
            p["y"] = A * math.sin(e["u"] * 9.0 - t * 3.2)
            p["hh"] = 0.95
            p["hw"] = (span * 2.0 / (N - 1)) * 0.45
            w = math.exp(-math.pow((e["u"] - math.sin(t * 1.1) * 0.5) / 0.22, 2.0))
            p["c"] = mix3(WHITE, TEAL, w)
        elif s == "thinking":
            w = math.exp(-math.pow((ku - math.sin(t * 2.1) * 0.85) / 0.2, 2.0))
            p["hw"] = p["hh"] = (1.25 + 0.45 * w) if vis else 0.0
            p["y"] = -1.25 * w
            p["c"] = mix3(WHITE, TEAL, w)
        else:  # idle / sleep
            p["hw"] = p["hh"] = 1.05 if vis else 0.0
            p["a"] = (0.6 + 0.4 * (0.5 + 0.5 * math.sin(t * 1.6 + ku * 2.5))) if vis else 0.0
            p["c"] = RED
        return p

    def _step(self):
        now = time.time()
        dt = min(0.05, now - self.last_time)
        self.last_time = now
        self.t += dt
        self._step_spec(dt)

        moving = (now - self.since) < 0.8
        kSlow = 1.0 - math.exp(-dt * 9.0)
        kFast = 1.0 - math.exp(-dt * 28.0)
        kLive = kSlow if moving else kFast

        for i in range(N):
            e = self.E[i]
            p = self._target(self.state, i, self.t)
            stag = 1.0 - math.exp(-dt * (7.0 + 5.0 * (1.0 - abs(e["u"]))))
            e["x"] += (p["x"] - e["x"]) * stag
            e["hw"] += (p["hw"] - e["hw"]) * stag
            e["a"] += (p["a"] - e["a"]) * stag
            e["y"] += (p["y"] - e["y"]) * kLive
            e["hh"] += (p["hh"] - e["hh"]) * kLive
            e["c"] = mix3(e["c"], p["c"], kSlow)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 1. Background Card (#1b1b1a) - expands to full height
        card_rect = QRectF(0, 0, 512, self.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0x1B, 0x1B, 0x1A)))
        p.drawRoundedRect(card_rect, 24, 24)

        # 2. Subtle static film grain (clipped to card)
        p.save()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(card_rect, 24, 24)
        p.setClipPath(clip_path)
        p.setOpacity(0.22)
        p.drawPixmap(0, 0, self.grain_pixmap, 0, 0, 512, int(self.height()))
        p.restore()

        # 3. Render Header Buttons
        for name, rect in self.buttons.items():
            renderer_name = name
            if name == "mic" and self._ui.muted:
                renderer_name = "mic_off"
            renderer = self.renderers.get(renderer_name)
            if renderer:
                p.save()
                is_active = (name == self.active_panel and self.is_expanded)
                is_hovered = (self.hovered_btn == name)
                p.setOpacity(1.0 if (is_hovered or is_active) else 0.92)
                renderer.render(p, rect)
                p.restore()

        self._paint_mission_strip(p)

        # 4. Render Morphing Waveform in Centre of header (CX = 256, CY = 24)
        CX = 256.0
        CY = 24.0
        L = clamp((self.E[N >> 1]["hw"] * 2.0 - 3.0) / 1.2, 0.0, 1.0)
        for i in range(N):
            e = self.E[i]
            if e["a"] < 0.02 or (e["hw"] < 0.03 and e["hh"] < 0.03):
                continue
            prev_e = self.E[max(0, i - 1)]
            next_e = self.E[min(N - 1, i + 1)]
            rot = L * math.atan2(next_e["y"] - prev_e["y"], max(0.5, next_e["x"] - prev_e["x"]))
            hw = max(e["hw"], 0.01)
            hh = max(e["hh"], 0.01)
            r = min(hw, hh)

            p.save()
            p.translate(CX + e["x"], CY + e["y"])
            p.rotate(math.degrees(rot))
            p.setOpacity(clamp(e["a"], 0.0, 1.0))
            col = QColor(int(e["c"][0]), int(e["c"][1]), int(e["c"][2]))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(col))
            p.drawRoundedRect(QRectF(-hw, -hh, hw * 2.0, hh * 2.0), r, r)
            p.restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            clicked_btn = None
            for name, rect in self.buttons.items():
                if rect.contains(pos):
                    clicked_btn = name
                    break
            if clicked_btn:
                if clicked_btn == "chat":
                    self._toggle_panel("chat")
                elif clicked_btn == "add":
                    self._toggle_panel("add")
                elif clicked_btn == "mic":
                    self._ui.muted = not self._ui.muted
                    self._ui.set_state("MUTED" if self._ui.muted else "LISTENING")
                    self.update()
                elif clicked_btn == "volume":
                    self._toggle_volume()
                elif clicked_btn == "settings":
                    self._open_settings()
                elif clicked_btn == "agents":
                    self._open_agent_orchestrator()
                elif clicked_btn == "term":
                    self._term_auto_opened = False
                    self.term_panel.refresh()
                    self._toggle_panel("term")
                elif self._ui.on_overlay_action:
                    self._ui.on_overlay_action(clicked_btn)
                else:
                    print(f"[UI:overlay] {clicked_btn}", flush=True)
            elif not self.is_expanded and self._mission_text and pos.y() > 48:
                self._open_agent_orchestrator(self._mission_id)
            else:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position()
        hovered = None
        for name, rect in self.buttons.items():
            if rect.contains(pos):
                hovered = name
                break
        if hovered != self.hovered_btn:
            self.hovered_btn = hovered
            self.update()

        if self._drag_pos and (event.buttons() & Qt.MouseButton.LeftButton):
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
            if self._vol_overlay.isVisible():
                vx = new_pos.x() + (self.width() - self._vol_overlay.width()) // 2
                vy = new_pos.y() + self.height() + 8
                self._vol_overlay.move(vx, vy)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

# ── Root Shim for main.py ───────────────────────────────────────────────────
class _RootShim:
    """Keeps `ui.root.mainloop()` / `ui.root.protocol()` working for main.py."""
    def __init__(self, ui: "JarvisUI"):
        self._ui = ui

    def mainloop(self):
        self._ui._run()

    def protocol(self, *_):
        pass

# ── Main EidolonUI Class ────────────────────────────────────────────────────
class EidolonUI:
    def __init__(self, face_path: str = "", size=None):
        cfg = _read_full_config()
        self._assistant_name: str = (cfg.get("assistant_name") or "EIDOLON").strip()
        self._win = self
        self._ready: bool = self._check_config()
        self._muted: bool = False
        self._volume: float = float(cfg.get("app_volume", 0.70))
        self._current_file: str | None = None
        self.attached_files: list[str] = []
        self._phone_connected = False

        # Callbacks wired by JarvisLive
        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None
        self.on_voice_change = None
        self.on_audio_device_change = None
        self.get_plugins = None
        self.get_plugin_settings = None
        self.on_wake_toggle = None
        self.on_wake_manual = None
        self.on_wake_install = None
        self.wake_get_state = None
        self.wake_is_ready = None
        self.on_push_to_talk = None
        self.ptt_hold = None
        self.request_say = None
        self.on_overlay_action = None

        # PyQt6 GUI Application & Overlay
        self._app = QApplication.instance()
        if self._app is None:
            self._app = QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)
        self._bridge = _Bridge()
        self._overlay = OverlayWindow(self, self._bridge)
        self._key_dialog = None
        self._key_invalid = False
        self._bridge.sig_ask_key.connect(self._show_key_dialog)

        self.root = _RootShim(self)

    # ── lifecycle & execution ────────────────────────────────────────────────
    def _stdin_loop(self):
        """Typed console lines become text commands."""
        if sys.stdin is None or not sys.stdin.isatty():
            return   # packaged app: there is no console to read from
        while True:
            line = sys.stdin.readline()
            if line == "":
                return
            line = line.strip()
            if line and self.on_text_command:
                threading.Thread(target=self.on_text_command, args=(line,), daemon=True).start()

    def _run(self):
        """Runs the Qt event loop on the main thread."""
        print(f"[UI] {self._assistant_name} running. Type a message, Ctrl+C to quit.", flush=True)
        threading.Thread(target=self._stdin_loop, daemon=True, name="ui-stdin").start()
        try:
            self._overlay.show()
            self._app.exec()
            os._exit(0)
        except KeyboardInterrupt:
            pass

    @staticmethod
    def _check_config() -> bool:
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key"))
        except Exception:
            return False

    # ── public properties ────────────────────────────────────────────────────
    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, v: bool):
        self._muted = bool(v)
        self._overlay.update()

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, v: float):
        self._volume = max(0.0, min(1.0, float(v)))
        if hasattr(self, "_overlay") and hasattr(self._overlay, "_vol_overlay"):
            self._overlay._vol_overlay.update()
        try:
            data = _read_full_config()
            v_round = round(self._volume, 2)
            if data.get("app_volume") != v_round:
                data["app_volume"] = v_round
                _write_full_config(data)
        except Exception:
            pass

    @property
    def current_file(self) -> str | None:
        return self._current_file

    def release_attachments(self) -> None:
        """Thread-safe: the attached documents have served — clear and fold."""
        self._bridge.sig_release_files.emit()

    @property
    def assistant_name(self) -> str:
        return self._assistant_name

    # ── public methods (thread-safe via Qt signals) ──────────────────────────
    def set_state(self, state: str):
        s = str(state).upper()
        if self._muted and s in ("LISTENING", "IDLE", "SLEEPING"):
            s = "MUTED"
        self._bridge.sig_state.emit(s)

    def write_log(self, text: str):
        print(str(text), flush=True)
        self._bridge.sig_log.emit(str(text))

    def wait_for_api_key(self):
        """Blocks the calling (engine) thread until a key is saved. The key is
        asked in a small window on the GUI thread — never on a console, which
        the installed app does not have."""
        if not self._ready:
            self._bridge.sig_ask_key.emit(self._key_invalid)
        while not self._ready:
            time.sleep(0.1)

    def _show_key_dialog(self, invalid: bool):
        from core.api_key_dialog import ApiKeyDialog, detected_os
        if self._key_dialog is not None and self._key_dialog.isVisible():
            self._key_dialog.raise_()
            return

        def saved(key: str):
            data = _read_full_config()
            data["gemini_api_key"] = key
            data.setdefault("os_system", detected_os())
            _write_full_config(data)
            try:
                from core import gemini
                gemini.api_key(refresh=True)
            except Exception:
                pass
            self._key_invalid = False
            self._ready = True

        self._key_dialog = ApiKeyDialog(self._assistant_name, invalid=invalid)
        self._key_dialog.saved.connect(saved)
        self._key_dialog.present()

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")

    def set_audio_level(self, level: float) -> None:
        try:
            lv = min(1.0, max(0.0, float(level)))
            self._bridge.sig_level.emit(lv)
        except (TypeError, ValueError):
            pass

    def push_pcm(self, pcm, sr: int, at: float) -> None:
        """Called by main.py with each audio batch about to be played."""
        try:
            frames = _spectrum_frames(pcm, int(sr))
            if not frames:
                return
            self._spec_seen = time.time()
            hop = SPEC_HOP / float(sr)
            self._bridge.sig_spec.emit(float(at), float(hop), frames)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        try:
            if frames and time.time() - getattr(self, "_spec_seen", 0.0) > 1.0:
                self.set_audio_level(max(float(f[0]) for f in frames))
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        pass

    def show_resources(self, items: list[dict | str]):
        """Displays generated files, folders, links or apps as clickable card tiles."""
        self._bridge.sig_show_resources.emit(items)

    def show_content(self, title: str, text: str):
        print(f"[UI:content] {title}\n{text}", flush=True)
        self.write_log(f"SYS: [{title}] {text}")

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        print(f"[UI:review] {title}: {summary}", flush=True)
        self.write_log(f"SYS: [Review] {title}: {summary}")

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        print(f"[UI:quiz] {topic} ({len(list(questions or []))} questions)", flush=True)

    def hide_quiz(self) -> None:
        pass

    def show_confirm(self, title: str, detail: str, accept_label: str = "✓ Confirmer", reject_label: str = "✕ Annuler", accept_cmd: str = "", reject_cmd: str = "", items: list = None) -> None:
        print(f"[UI:confirm] {title} — {detail}", flush=True)
        try:
            payload = {
                "title": str(title),
                "detail": str(detail),
                "accept_label": str(accept_label),
                "reject_label": str(reject_label),
                "accept_cmd": str(accept_cmd),
                "reject_cmd": str(reject_cmd),
                "items": items or []
            }
            self._bridge.sig_confirm.emit(payload)
        except Exception as e:
            print(f"[UI:confirm] Error emitting confirm signal: {e}", flush=True)

    def hide_confirm(self) -> None:
        try:
            self._bridge.sig_hide_confirm.emit()
        except Exception:
            pass

    def prompt_reconfig(self):
        self._ready = False
        self._key_invalid = True
        self.wait_for_api_key()

    def notify_phone_connected(self) -> None:
        self._phone_connected = True

    def show_camera_frame(self, img_bytes: bytes):
        pass

    def start_camera_stream(self) -> None:
        pass

    def stop_camera_stream(self) -> None:
        pass

    def open_orchestrator(self, mission_id: str = "") -> None:
        """Open the Équipe window, from any thread (tools run off the GUI thread)."""
        self._bridge.sig_open_team.emit(mission_id or "")


# Backward-compatible alias for external plugins
JarvisUI = EidolonUI
