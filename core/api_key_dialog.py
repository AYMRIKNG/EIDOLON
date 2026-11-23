"""
First-launch window: asks for the Gemini API key.

The key is never part of the code or of the installer — it is typed here once
and saved to config/api_keys.json next to the app, on this PC only. The same
window comes back if Google rejects the key later.

Same graphite / cyan language as the HUD: one small card, centred, on top.
"""
from __future__ import annotations

import platform
import webbrowser

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QBrush
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

KEY_URL = "https://aistudio.google.com/apikey"
FONT = "'Segoe UI', system-ui, sans-serif"


def detected_os() -> str:
    return {"Windows": "windows", "Darwin": "mac"}.get(platform.system(), "linux")


class ApiKeyDialog(QWidget):
    """Emits `saved(key)` once a plausible key has been entered."""

    saved = pyqtSignal(str)

    def __init__(self, assistant_name: str = "EIDOLON", invalid: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(440, 262)
        self._drag = None

        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 22)
        v.setSpacing(10)

        title = QLabel(f"Bienvenue dans {assistant_name}")
        title.setStyleSheet(f"color:#fff; font-family:{FONT}; font-size:17px; font-weight:600; background:transparent;")
        v.addWidget(title)
        intro = QLabel("Google a refusé la clé API — saisissez-en une nouvelle." if invalid else
                       "Pour commencer, collez votre clé API Gemini. Elle reste sur ce PC, "
                       "dans le dossier de l'application.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{'#ff8a80' if invalid else '#b5b5b0'}; font-family:{FONT}; "
                            f"font-size:12px; background:transparent;")
        v.addWidget(intro)
        v.addSpacing(4)

        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText("AIza…")
        self.field.setStyleSheet(f"""
            QLineEdit {{ background:#2b2b29; color:#fff; border:1px solid rgba(255,255,255,0.10);
                         border-radius:12px; padding:10px 14px; font-family:{FONT}; font-size:13px;
                         selection-background-color:#00a1c5; }}
            QLineEdit:focus {{ border:1px solid #5cc9c3; }}
        """)
        self.field.returnPressed.connect(self._save)
        self.field.textChanged.connect(lambda: self.error.setText(""))
        v.addWidget(self.field)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color:#ff8a80; font-family:{FONT}; font-size:11px; background:transparent;")
        v.addWidget(self.error)
        v.addStretch(1)

        row = QHBoxLayout()
        link = QPushButton("Obtenir une clé gratuite ↗")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setStyleSheet(f"QPushButton {{ color:#5cc9c3; background:transparent; border:none; "
                           f"font-family:{FONT}; font-size:12px; text-align:left; }} "
                           f"QPushButton:hover {{ color:#8fe3de; }}")
        link.clicked.connect(lambda: webbrowser.open(KEY_URL))
        row.addWidget(link)
        row.addStretch(1)
        quit_btn = QPushButton("Quitter")
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"QPushButton {{ color:#b5b5b0; background:transparent; border:none; "
                               f"font-family:{FONT}; font-size:12px; padding:8px 10px; }} "
                               f"QPushButton:hover {{ color:#fff; }}")
        quit_btn.clicked.connect(lambda: QApplication.instance().quit())
        row.addWidget(quit_btn)
        ok = QPushButton("Commencer")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"QPushButton {{ color:#06222a; background:#5cc9c3; border:none; border-radius:15px; "
                         f"padding:8px 20px; font-family:{FONT}; font-size:13px; font-weight:600; }} "
                         f"QPushButton:hover {{ background:#7fd9d4; }}")
        ok.clicked.connect(self._save)
        row.addWidget(ok)
        v.addLayout(row)

    def present(self):
        screen = QApplication.primaryScreen()
        if screen:
            g = screen.availableGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()
        self.field.setFocus()

    def _save(self):
        key = self.field.text().strip()
        if len(key) < 20 or " " in key:
            self.error.setText("Cette clé ne ressemble pas à une clé Gemini (elle commence en général par AIza).")
            return
        self.saved.emit(key)
        self.close()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0x1B, 0x1B, 0x1A)))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 22, 22)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None
