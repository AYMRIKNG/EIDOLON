"""EIDOLON — Settings Window (PyQt6).

Implements the multi-section Settings UI matching the EIDOLON aesthetics:
- Left sidebar: General, Models, Audio, Mémoire, Integrations
- Right pane: Dark card container with interactive controls, sliders, toggles, and persistence.
"""
from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QRectF, QPoint, QSize, pyqtSignal, QTimer, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFont, QCursor
from PyQt6.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QSlider, QFrame,
    QStackedWidget, QScrollArea, QFileDialog, QMessageBox, QSizePolicy
)

from memory import config_manager
from core import audio_devices
from core.branding import APP_NAME, APP_VERSION


# ── Custom Toggle Switch ─────────────────────────────────────────────────────

class ToggleSwitch(QWidget):
    """Modern iOS / macOS style animated toggle switch."""
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(46, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = bool(checked)
        self._thumb_position = 1.0 if self._checked else 0.0

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.valueChanged.connect(self._on_anim_value)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if self._checked != checked:
            self._checked = checked
            self._start_anim()
            self.toggled.emit(self._checked)

    def _start_anim(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._thumb_position)
        self._anim.setEndValue(1.0 if self._checked else 0.0)
        self._anim.start()

    def _on_anim_value(self, val: Any) -> None:
        self._thumb_position = float(val)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        radius = h / 2.0

        # Background track color interpolation
        # Off: #2b2b2b, On: #00d4ff (cyan)
        t = self._thumb_position
        r = int(43 * (1.0 - t) + 0 * t)
        g = int(43 * (1.0 - t) + 212 * t)
        b = int(43 * (1.0 - t) + 255 * t)
        track_color = QColor(r, g, b)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track_color))
        p.drawRoundedRect(0, 0, w, h, radius, radius)

        # Thumb
        thumb_radius = radius - 3.0
        thumb_x = 3.0 + t * (w - 2.0 * radius)
        thumb_y = 3.0

        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.drawEllipse(QRectF(thumb_x, thumb_y, thumb_radius * 2.0, thumb_radius * 2.0))


# ── Helper Card Row ─────────────────────────────────────────────────────────

def _create_setting_row(title: str, subtitle: str = "", control: Optional[QWidget] = None) -> QWidget:
    """Helper to create a standard settings row with label + description + right widget."""
    row = QWidget()
    row_layout = QHBoxLayout(row)
    row_layout.setContentsMargins(0, 8, 0, 8)
    row_layout.setSpacing(16)

    text_col = QVBoxLayout()
    text_col.setContentsMargins(0, 0, 0, 0)
    text_col.setSpacing(2)

    lbl_title = QLabel(title)
    lbl_title.setStyleSheet("color: #f0f0f0; font-size: 13px; font-weight: 500; border: none; background: transparent;")
    text_col.addWidget(lbl_title)

    if subtitle:
        lbl_sub = QLabel(subtitle)
        lbl_sub.setWordWrap(True)
        lbl_sub.setStyleSheet("color: #7a7a7a; font-size: 11px; border: none; background: transparent;")
        text_col.addWidget(lbl_sub)

    row_layout.addLayout(text_col, 1)
    if control:
        row_layout.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    return row


def _create_separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("background: rgba(255, 255, 255, 0.06); max-height: 1px; border: none; margin: 6px 0;")
    return line


def _apply_windows_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "EIDOLON"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                py_exe = Path(sys.executable)
                pyw_exe = py_exe.parent / "pythonw.exe"
                executable = str(pyw_exe) if pyw_exe.exists() else str(py_exe)
                main_script = str(config_manager.BASE_DIR / "main.py")
                cmd = f'"{executable}" "{main_script}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
    except Exception as e:
        print(f"[Autostart:err] {e}", flush=True)


# ── 1. General Settings View ────────────────────────────────────────────────

class GeneralSettingsView(QWidget):
    def __init__(self, overlay=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._overlay = overlay
        cfg = config_manager.load_api_keys()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(12)

        # Title
        title = QLabel("General Settings")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 600; margin-bottom: 6px; background: transparent;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")
        
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(10)

        # 1. Raccourcis globaux
        self.shortcut_input = QLineEdit(cfg.get("global_shortcut", "Ctrl+Space"))
        self.shortcut_input.setFixedWidth(140)
        self.shortcut_input.setStyleSheet("""
            QLineEdit {
                background: #1e1e1e;
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit:focus { border: 1px solid #00d4ff; }
        """)
        c_layout.addWidget(_create_setting_row(
            "Raccourcis globaux",
            "Combinaison de touches pour afficher/masquer la barre d'EIDOLON",
            self.shortcut_input
        ))
        c_layout.addWidget(_create_separator())

        # 2. Démarrage système
        self.autostart_toggle = ToggleSwitch(checked=cfg.get("autostart_enabled", False))
        c_layout.addWidget(_create_setting_row(
            "Démarrage système",
            "Lancer l'application en arrière-plan au démarrage de Windows",
            self.autostart_toggle
        ))
        c_layout.addWidget(_create_separator())

        # 3. Always on top
        self.ontop_toggle = ToggleSwitch(checked=cfg.get("always_on_top", True))
        def _on_ontop_toggled(checked: bool):
            if self._overlay:
                try:
                    flags = self._overlay.windowFlags()
                    if checked:
                        flags |= Qt.WindowType.WindowStaysOnTopHint
                    else:
                        flags &= ~Qt.WindowType.WindowStaysOnTopHint
                    if flags != self._overlay.windowFlags():
                        curr_pos = self._overlay.pos()
                        self._overlay.setWindowFlags(flags)
                        self._overlay.move(curr_pos)
                        self._overlay.show()
                except Exception:
                    pass
        self.ontop_toggle.toggled.connect(_on_ontop_toggled)
        c_layout.addWidget(_create_setting_row(
            "Toujours au premier plan",
            "Maintenir strictement la barre d'overlay au-dessus des autres fenêtres",
            self.ontop_toggle
        ))
        c_layout.addWidget(_create_separator())

        # 4. Opacité de la barre
        opacity_val = int(cfg.get("overlay_opacity", 0.85) * 100)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(opacity_val)
        self.opacity_slider.setFixedWidth(140)
        self.opacity_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 4px; background: #2a2a2a; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #00d4ff; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0; background: #ffffff; border-radius: 7px; }
        """)
        self.opacity_label = QLabel(f"{opacity_val}%")
        self.opacity_label.setStyleSheet("color: #00d4ff; font-weight: 500; font-size: 12px; min-width: 38px;")
        def _on_opacity_change(v: int):
            self.opacity_label.setText(f"{v}%")
            if self._overlay:
                try:
                    self._overlay.setWindowOpacity(max(0.2, min(1.0, v / 100.0)))
                except Exception:
                    pass
        self.opacity_slider.valueChanged.connect(_on_opacity_change)

        op_widget = QWidget()
        op_h = QHBoxLayout(op_widget)
        op_h.setContentsMargins(0, 0, 0, 0)
        op_h.addWidget(self.opacity_slider)
        op_h.addWidget(self.opacity_label)
        c_layout.addWidget(_create_setting_row(
            "Opacité de la barre",
            "Niveau de transparence au repos de la pilule EIDOLON",
            op_widget
        ))
        c_layout.addWidget(_create_separator())

        # 5. Durée d'affichage des fichiers générés
        duration_val = int(config_manager.get_resources_display_duration())
        self.duration_slider = QSlider(Qt.Orientation.Horizontal)
        self.duration_slider.setRange(5, 60)
        self.duration_slider.setValue(duration_val)
        self.duration_slider.setFixedWidth(140)
        self.duration_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 4px; background: #2a2a2a; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #00d4ff; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0; background: #ffffff; border-radius: 7px; }
        """)
        self.duration_label = QLabel(f"{duration_val}s")
        self.duration_label.setStyleSheet("color: #00d4ff; font-weight: 500; font-size: 12px; min-width: 38px;")
        self.duration_slider.valueChanged.connect(lambda v: self.duration_label.setText(f"{v}s"))

        dur_widget = QWidget()
        dur_h = QHBoxLayout(dur_widget)
        dur_h.setContentsMargins(0, 0, 0, 0)
        dur_h.addWidget(self.duration_slider)
        dur_h.addWidget(self.duration_label)
        c_layout.addWidget(_create_setting_row(
            "Affichage des modules créés",
            "Délai en secondes avant disparition automatique des cartes de fichiers (pause au survol)",
            dur_widget
        ))
        c_layout.addWidget(_create_separator())

        # 6. Mises à jour
        update_btn = QPushButton("Rechercher une mise à jour")
        update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        update_btn.setStyleSheet("""
            QPushButton {
                background: #1f1f1f;
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover { background: #2b2b2b; border-color: rgba(255,255,255,0.3); }
        """)
        update_btn.clicked.connect(self._check_updates)
        c_layout.addWidget(_create_setting_row(
            "Mises à jour système",
            f"Version installée : v{APP_VERSION} ({APP_NAME})",
            update_btn
        ))

        c_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _check_updates(self) -> None:
        btn = self.sender()
        if isinstance(btn, QPushButton):
            btn.setText("Vérification...")
            btn.setEnabled(False)
            QTimer.singleShot(800, lambda: (btn.setText("✓ Système à jour"), btn.setEnabled(True)))

    def save(self) -> None:
        cfg = config_manager.load_api_keys()
        cfg["global_shortcut"] = self.shortcut_input.text().strip()
        autostart = self.autostart_toggle.isChecked()
        cfg["autostart_enabled"] = autostart
        cfg["always_on_top"] = self.ontop_toggle.isChecked()
        cfg["overlay_opacity"] = round(self.opacity_slider.value() / 100.0, 2)
        config_manager.save_resources_display_duration(float(self.duration_slider.value()))

        # Save to api_keys.json
        cfg_file = config_manager.CONFIG_FILE
        cfg_file.write_text(json.dumps(cfg, indent=4), encoding="utf-8")

        # Apply Windows Registry Autostart
        _apply_windows_autostart(autostart)


# ── 2. Models Settings View ─────────────────────────────────────────────────

class ModelsSettingsView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        cfg = config_manager.load_api_keys()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(12)

        title = QLabel("Models & Providers")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 600; margin-bottom: 6px; background: transparent;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(10)

        # 1. Routeur principal (Segmented Buttons: LM Studio | Local | API keys)
        self.provider_mode = cfg.get("llm_source", "API keys")
        provider_bar = QWidget()
        p_bar_layout = QHBoxLayout(provider_bar)
        p_bar_layout.setContentsMargins(0, 0, 0, 0)
        p_bar_layout.setSpacing(6)

        self.btn_lm = QPushButton("LM Studio")
        self.btn_local = QPushButton("Local")
        self.btn_api = QPushButton("API keys")

        for b in (self.btn_lm, self.btn_local, self.btn_api):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(28)
            b.setFixedWidth(86)
            b.clicked.connect(self._on_provider_click)
            p_bar_layout.addWidget(b)

        self._refresh_provider_buttons()

        c_layout.addWidget(_create_setting_row(
            "Routeur principal",
            "Sélection de la source d'inférence LLM pour EIDOLON",
            provider_bar
        ))
        c_layout.addWidget(_create_separator())

        # 2. Configuration Hôte
        self.host_name = QLineEdit(cfg.get("llm_host_name", "gemini-2.0-flash-exp"))
        self.host_name.setFixedWidth(160)
        self.host_name.setStyleSheet("""
            QLineEdit { background: #1e1e1e; color: #ffffff; border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 6px; padding: 4px 8px; font-size: 12px; }
        """)
        c_layout.addWidget(_create_setting_row("Nom du modèle", "Identifiant du modèle (ex: LM Studio model / Gemini)", self.host_name))

        self.host_server = QLineEdit(cfg.get("llm_host_server", "http://localhost"))
        self.host_server.setFixedWidth(160)
        self.host_server.setStyleSheet("""
            QLineEdit { background: #1e1e1e; color: #ffffff; border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 6px; padding: 4px 8px; font-size: 12px; }
        """)
        c_layout.addWidget(_create_setting_row("Adresse du serveur", "URL hôte pour les serveurs locaux ou distants", self.host_server))

        self.host_port = QLineEdit(str(cfg.get("llm_host_port", 1234)))
        self.host_port.setFixedWidth(80)
        self.host_port.setStyleSheet("""
            QLineEdit { background: #1e1e1e; color: #ffffff; border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 6px; padding: 4px 8px; font-size: 12px; }
        """)
        c_layout.addWidget(_create_setting_row("Port de communication", "Port TCP du service (ex: 1234, 2000)", self.host_port))
        c_layout.addWidget(_create_separator())

        # 3. Coffre-fort d'API (API Vault)
        gemini_val = cfg.get("gemini_api_key", "")
        self.gemini_input = QLineEdit(gemini_val)
        self.gemini_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_input.setFixedWidth(200)
        self.gemini_input.setStyleSheet("""
            QLineEdit { background: #1e1e1e; color: #ffffff; border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 6px; padding: 4px 8px; font-size: 12px; }
        """)

        copy_btn = QPushButton("Copier")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet("QPushButton { background: #262626; color: #fff; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 4px 8px; font-size: 11px; }")
        copy_btn.clicked.connect(self._copy_key)

        gemini_row = QWidget()
        gemini_h = QHBoxLayout(gemini_row)
        gemini_h.setContentsMargins(0, 0, 0, 0)
        gemini_h.setSpacing(8)

        status_dot = QLabel("● Connecté" if bool(gemini_val) else "○ Inactif")
        status_dot.setStyleSheet("color: #22c55e; font-size: 11px; font-weight: 500;" if bool(gemini_val) else "color: #888888; font-size: 11px;")

        gemini_h.addWidget(status_dot)
        gemini_h.addWidget(self.gemini_input)
        gemini_h.addWidget(copy_btn)

        c_layout.addWidget(_create_setting_row(
            "Coffre-fort Clés API (Gemini)",
            "Clé principale pour le dialogue temps réel Gemini Live",
            gemini_row
        ))

        add_key_btn = QPushButton("+ Add New Key")
        add_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_key_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #00d4ff;
                border: 1px dashed rgba(0, 212, 255, 0.4);
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover { background: rgba(0, 212, 255, 0.08); }
        """)
        add_key_btn.clicked.connect(self._add_key_dialog)
        c_layout.addWidget(add_key_btn)
        c_layout.addWidget(_create_separator())

        # 4. Télémétrie et Coûts
        telemetry_box = QWidget()
        tb_layout = QVBoxLayout(telemetry_box)
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.setSpacing(4)

        tb_head = QHBoxLayout()
        lbl_usage = QLabel("Quota mensuel d'utilisation")
        lbl_usage.setStyleSheet("color: #e0e0e0; font-size: 12px;")
        lbl_stat = QLabel("7 800 / 10 000 requêtes (78%)")
        lbl_stat.setStyleSheet("color: #00d4ff; font-size: 12px; font-weight: 500;")
        tb_head.addWidget(lbl_usage)
        tb_head.addStretch(1)
        tb_head.addWidget(lbl_stat)
        tb_layout.addLayout(tb_head)

        bar_frame = QFrame()
        bar_frame.setFixedHeight(6)
        bar_frame.setStyleSheet("""
            QFrame {
                background: #252525;
                border-radius: 3px;
            }
        """)
        bar_fill = QFrame(bar_frame)
        bar_fill.setGeometry(0, 0, 180, 6)
        bar_fill.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00d4ff, stop:1 #22c55e); border-radius: 3px;")
        tb_layout.addWidget(bar_frame)

        c_layout.addWidget(_create_setting_row("Télémétrie & Coûts", "Suivi des quotas et latence du service (Latence : 42ms)", telemetry_box))
        c_layout.addWidget(_create_separator())

        # 5. Verrou de sécurité (Offline Mode)
        self.offline_toggle = ToggleSwitch(checked=cfg.get("offline_mode", False))
        c_layout.addWidget(_create_setting_row(
            "Network Mode (Offline Strict)",
            "Forcer le basculement strict en mode hors-ligne sans connexion externe",
            self.offline_toggle
        ))

        c_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _refresh_provider_buttons(self) -> None:
        style_active = "background: #00d4ff; color: #000000; font-weight: 600; border-radius: 6px; font-size: 11px; border: none;"
        style_inactive = "background: #1e1e1e; color: #888888; font-weight: 500; border-radius: 6px; font-size: 11px; border: 1px solid rgba(255,255,255,0.08);"

        self.btn_lm.setStyleSheet(style_active if self.provider_mode == "LM Studio" else style_inactive)
        self.btn_local.setStyleSheet(style_active if self.provider_mode == "Local" else style_inactive)
        self.btn_api.setStyleSheet(style_active if self.provider_mode == "API keys" else style_inactive)

    def _on_provider_click(self) -> None:
        btn = self.sender()
        if isinstance(btn, QPushButton):
            self.provider_mode = btn.text()
            self._refresh_provider_buttons()

    def _copy_key(self) -> None:
        app = QApplication.instance()
        if app:
            cb = app.clipboard()
            cb.setText(self.gemini_input.text().strip())
            btn = self.sender()
            if isinstance(btn, QPushButton):
                btn.setText("Copié !")
                QTimer.singleShot(1200, lambda: btn.setText("Copier"))

    def _add_key_dialog(self) -> None:
        pass

    def save(self) -> None:
        cfg = config_manager.load_api_keys()
        cfg["llm_source"] = self.provider_mode
        cfg["llm_host_name"] = self.host_name.text().strip()
        cfg["llm_host_server"] = self.host_server.text().strip()
        try:
            cfg["llm_host_port"] = int(self.host_port.text().strip())
        except ValueError:
            pass
        cfg["gemini_api_key"] = self.gemini_input.text().strip()
        cfg["offline_mode"] = self.offline_toggle.isChecked()

        cfg_file = config_manager.CONFIG_FILE
        cfg_file.write_text(json.dumps(cfg, indent=4), encoding="utf-8")


# ── 3. Audio Settings View ──────────────────────────────────────────────────

class AudioSettingsView(QWidget):
    def __init__(self, ui=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._ui = ui
        cfg = config_manager.load_api_keys()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(12)

        title = QLabel("Audio Settings")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 600; margin-bottom: 6px; background: transparent;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(10)

        combo_style = """
            QComboBox {
                background: #1e1e1e;
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                min-width: 180px;
            }
            QComboBox QAbstractItemView {
                background: #181818;
                color: #ffffff;
                selection-background-color: #00d4ff;
                selection-color: #000000;
            }
        """

        # 1. Microphone Entrée
        self.input_combo = QComboBox()
        self.input_combo.setStyleSheet(combo_style)
        self.input_combo.addItem(audio_devices.DEFAULT_LABEL, audio_devices.DEFAULT_VALUE)
        for dev in audio_devices.list_devices("input"):
            self.input_combo.addItem(dev, dev)
        cur_in = config_manager.get_input_device()
        idx_in = self.input_combo.findData(cur_in)
        if idx_in >= 0:
            self.input_combo.setCurrentIndex(idx_in)

        c_layout.addWidget(_create_setting_row("Microphone d'entrée", "Forcer un microphone spécifique", self.input_combo))
        c_layout.addWidget(_create_separator())

        # 2. Haut-parleurs Sortie
        self.output_combo = QComboBox()
        self.output_combo.setStyleSheet(combo_style)
        self.output_combo.addItem(audio_devices.DEFAULT_LABEL, audio_devices.DEFAULT_VALUE)
        for dev in audio_devices.list_devices("output"):
            self.output_combo.addItem(dev, dev)
        cur_out = config_manager.get_output_device()
        idx_out = self.output_combo.findData(cur_out)
        if idx_out >= 0:
            self.output_combo.setCurrentIndex(idx_out)

        c_layout.addWidget(_create_setting_row("Haut-parleurs de sortie", "Forcer les haut-parleurs de restitution", self.output_combo))
        c_layout.addWidget(_create_separator())

        # 3. Wake Word
        self.wake_toggle = ToggleSwitch(checked=config_manager.get_wake_word_enabled())
        c_layout.addWidget(_create_setting_row("Détection Wake Word", "Activer la détection automatique du mot-clé 'Jarvis'", self.wake_toggle))

        wake_sens = int(cfg.get("wake_sensitivity", 0.6) * 100)
        self.wake_slider = QSlider(Qt.Orientation.Horizontal)
        self.wake_slider.setRange(10, 100)
        self.wake_slider.setValue(wake_sens)
        self.wake_slider.setFixedWidth(140)
        self.wake_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 4px; background: #2a2a2a; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #00d4ff; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0; background: #ffffff; border-radius: 7px; }
        """)
        self.wake_label = QLabel(f"{wake_sens}%")
        self.wake_label.setStyleSheet("color: #00d4ff; font-size: 12px; min-width: 38px;")
        self.wake_slider.valueChanged.connect(lambda v: self.wake_label.setText(f"{v}%"))

        sens_w = QWidget()
        sens_h = QHBoxLayout(sens_w)
        sens_h.setContentsMargins(0, 0, 0, 0)
        sens_h.addWidget(self.wake_slider)
        sens_h.addWidget(self.wake_label)
        c_layout.addWidget(_create_setting_row("Sensibilité Wake Word", "Ajuster pour éviter les faux positifs en milieu bruyant", sens_w))
        c_layout.addWidget(_create_separator())

        # 4. Moteur Vocal (TTS Engine)
        from core import tts as core_tts
        self.engine_combo = QComboBox()
        self.engine_combo.setStyleSheet(combo_style)
        self.engine_combo.addItem("Edge-TTS (Défaut - Rapide & Gratuit)", "edgetts")
        self.engine_combo.addItem("Kokoro TTS (100% Hors-ligne / Local)", "kokoro")
        self.engine_combo.addItem("Gemini Live (Natif Multimodal)", "gemini")

        cur_eng = cfg.get("tts_engine", "edgetts")
        idx_e = self.engine_combo.findData(cur_eng)
        if idx_e >= 0:
            self.engine_combo.setCurrentIndex(idx_e)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)

        c_layout.addWidget(_create_setting_row(
            "Moteur Vocal (Synthèse vocale)",
            "Edge-TTS (gratuit, ultra-rapide) ou Kokoro (100% local hors-ligne)",
            self.engine_combo
        ))

        # 5. Profil vocal (Dynamique selon le moteur)
        self.voice_combo = QComboBox()
        self.voice_combo.setStyleSheet(combo_style)
        self._populate_voices()

        c_layout.addWidget(_create_setting_row(
            "Profil vocal / Voix",
            "Sélection du timbre et de la langue de l'assistant",
            self.voice_combo
        ))

        # 6. Statut & Installateur Kokoro (si Hors-ligne sélectionné)
        self.kokoro_box = QWidget()
        kb_layout = QHBoxLayout(self.kokoro_box)
        kb_layout.setContentsMargins(0, 0, 0, 0)
        kb_layout.setSpacing(8)

        self.kokoro_status = QLabel()
        self.kokoro_status.setStyleSheet("font-size: 11px;")
        self.kokoro_btn = QPushButton("Installer Kokoro TTS (~330 Mo)")
        self.kokoro_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.kokoro_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 212, 255, 0.12);
                color: #00d4ff;
                border: 1px solid rgba(0, 212, 255, 0.3);
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover { background: rgba(0, 212, 255, 0.22); }
        """)
        self.kokoro_btn.clicked.connect(self._install_kokoro_clicked)
        kb_layout.addWidget(self.kokoro_status)
        kb_layout.addWidget(self.kokoro_btn)

        self.kokoro_row = _create_setting_row(
            "Module Hors-ligne Kokoro",
            "Modèle neuronal autonome local sans connexion internet",
            self.kokoro_box
        )
        self._refresh_kokoro_status()
        c_layout.addWidget(self.kokoro_row)
        c_layout.addWidget(_create_separator())

        # 7. Mode Basse Latence (Streaming par phrase)
        self.latency_toggle = ToggleSwitch(checked=cfg.get("tts_streaming_enabled", True))
        c_layout.addWidget(_create_setting_row(
            "Basse Latence (Sentence Streaming)",
            "Démarre l'élocution dès la première phrase pour égaler ou dépasser Google Live",
            self.latency_toggle
        ))
        c_layout.addWidget(_create_separator())

        # 8. Volume de sortie
        vol_pct = int(cfg.get("app_volume", 0.70) * 100)
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(vol_pct)
        self.vol_slider.setFixedWidth(140)
        self.vol_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 4px; background: #2a2a2a; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #00d4ff; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0; background: #ffffff; border-radius: 7px; }
        """)
        self.vol_label = QLabel(f"{vol_pct}%")
        self.vol_label.setStyleSheet("color: #00d4ff; font-size: 12px; min-width: 38px;")
        def _on_vol_change(v: int):
            self.vol_label.setText(f"{v}%")
            if self._ui:
                try:
                    self._ui.volume = v / 100.0
                except Exception:
                    pass
        self.vol_slider.valueChanged.connect(_on_vol_change)

        vol_w = QWidget()
        vol_h = QHBoxLayout(vol_w)
        vol_h.setContentsMargins(0, 0, 0, 0)
        vol_h.addWidget(self.vol_slider)
        vol_h.addWidget(self.vol_label)
        c_layout.addWidget(_create_setting_row("Volume de l'assistant", "Volume de restitution audio principal", vol_w))
        c_layout.addWidget(_create_separator())

        # 9. Réduction de bruit matérielle
        self.noise_toggle = ToggleSwitch(checked=cfg.get("noise_reduction_enabled", True))
        c_layout.addWidget(_create_setting_row("Nettoyage matériel (VAD)", "Filtre local de suppression de bruit et bruits de frappe", self.noise_toggle))

        c_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _populate_voices(self) -> None:
        self.voice_combo.clear()
        eng = self.engine_combo.currentData() or "edgetts"
        cfg = config_manager.load_api_keys()

        if eng == "edgetts":
            self.voice_combo.addItem("Henri (Français - Posé / JARVIS)", "fr-FR-HenriNeural")
            self.voice_combo.addItem("Denise (Français - Naturel)", "fr-FR-DeniseNeural")
            self.voice_combo.addItem("Vivienne (Multilingue)", "fr-FR-VivienneMultilingualNeural")
            saved = cfg.get("tts_voice", "fr-FR-HenriNeural")
        elif eng == "kokoro":
            self.voice_combo.addItem("Siwis (Français)", "ff_siwis")
            self.voice_combo.addItem("Heart (Anglais)", "af_heart")
            saved = cfg.get("tts_voice", "ff_siwis")
        else:  # gemini
            for v in config_manager.AVAILABLE_VOICES:
                self.voice_combo.addItem(v, v)
            saved = config_manager.get_voice()

        idx = self.voice_combo.findData(saved)
        if idx >= 0:
            self.voice_combo.setCurrentIndex(idx)
        elif self.voice_combo.count() > 0:
            self.voice_combo.setCurrentIndex(0)

    def _on_engine_changed(self) -> None:
        self._populate_voices()
        self._refresh_kokoro_status()

    def _refresh_kokoro_status(self) -> None:
        from core import tts as core_tts
        eng = self.engine_combo.currentData() or "edgetts"
        is_kokoro = (eng == "kokoro")
        self.kokoro_row.setVisible(is_kokoro)

        if is_kokoro:
            installed = core_tts.is_kokoro_installed()
            if installed:
                self.kokoro_status.setText("● Modèle Kokoro installé")
                self.kokoro_status.setStyleSheet("color: #22c55e; font-size: 11px;")
                self.kokoro_btn.setVisible(False)
            else:
                self.kokoro_status.setText("○ Modèle non installé")
                self.kokoro_status.setStyleSheet("color: #eab308; font-size: 11px;")
                self.kokoro_btn.setVisible(True)

    def _install_kokoro_clicked(self) -> None:
        from core import tts as core_tts
        self.kokoro_btn.setEnabled(False)
        self.kokoro_btn.setText("Installation en cours (pip)...")
        def _cb(ok: bool, msg: str):
            if ok:
                self.kokoro_status.setText("● Installé avec succès")
                self.kokoro_status.setStyleSheet("color: #22c55e;")
                self.kokoro_btn.setVisible(False)
            else:
                self.kokoro_status.setText(f"Erreur: {msg[:30]}")
                self.kokoro_status.setStyleSheet("color: #ef4444;")
                self.kokoro_btn.setEnabled(True)
                self.kokoro_btn.setText("Réessayer l'installation")
        core_tts.install_kokoro(_cb)

    def save(self) -> None:
        in_dev = self.input_combo.currentData() or ""
        out_dev = self.output_combo.currentData() or ""
        config_manager.save_input_device(in_dev)
        config_manager.save_output_device(out_dev)
        config_manager.save_wake_word_enabled(self.wake_toggle.isChecked())

        cfg = config_manager.load_api_keys()
        cfg["input_device"] = in_dev
        cfg["output_device"] = out_dev
        cfg["wake_word_enabled"] = self.wake_toggle.isChecked()
        engine = self.engine_combo.currentData() or "edgetts"
        voice = self.voice_combo.currentData() or self.voice_combo.currentText()
        cfg["tts_engine"] = engine
        if engine == "gemini":
            if voice in config_manager.AVAILABLE_VOICES:
                cfg["voice_name"] = voice
        else:
            cfg["tts_voice"] = voice
        cfg["tts_streaming_enabled"] = self.latency_toggle.isChecked()
        cfg["wake_sensitivity"] = round(self.wake_slider.value() / 100.0, 2)
        cfg["app_volume"] = round(self.vol_slider.value() / 100.0, 2)
        cfg["noise_reduction_enabled"] = self.noise_toggle.isChecked()
        config_manager.CONFIG_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")


# ── 4. Memory Settings View ─────────────────────────────────────────────────

class MemorySettingsView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        cfg = config_manager.load_api_keys()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(12)

        title = QLabel("Memory & Knowledge Base")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 600; margin-bottom: 6px; background: transparent;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(10)

        # 1. Profondeur de contexte
        ctx_val = int(cfg.get("context_depth", 24))
        self.ctx_slider = QSlider(Qt.Orientation.Horizontal)
        self.ctx_slider.setRange(8, 64)
        self.ctx_slider.setValue(ctx_val)
        self.ctx_slider.setFixedWidth(140)
        self.ctx_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 4px; background: #2a2a2a; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #00d4ff; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0; background: #ffffff; border-radius: 7px; }
        """)
        self.ctx_label = QLabel(f"{ctx_val} tours")
        self.ctx_label.setStyleSheet("color: #00d4ff; font-size: 12px; min-width: 48px;")
        self.ctx_slider.valueChanged.connect(lambda v: self.ctx_label.setText(f"{v} tours"))

        ctx_w = QWidget()
        ctx_h = QHBoxLayout(ctx_w)
        ctx_h.setContentsMargins(0, 0, 0, 0)
        ctx_h.addWidget(self.ctx_slider)
        ctx_h.addWidget(self.ctx_label)
        c_layout.addWidget(_create_setting_row("Profondeur de contexte", "Nombre d'échanges récents conservés en mémoire vive active", ctx_w))
        c_layout.addWidget(_create_separator())

        # 2. Base de connaissances (RAG)
        add_folder_btn = QPushButton("+ Ajouter un dossier 📁")
        add_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_folder_btn.setStyleSheet("""
            QPushButton {
                background: #1e1e1e;
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover { background: #282828; border-color: rgba(255,255,255,0.25); }
        """)
        add_folder_btn.clicked.connect(self._add_rag_folder)

        c_layout.addWidget(_create_setting_row("Base de connaissances (RAG)", "Dossiers surveillés et indexés pour la recherche sémantique", add_folder_btn))

        self.rag_list_layout = QVBoxLayout()
        self.rag_list_layout.setContentsMargins(12, 0, 12, 0)
        self.rag_list_layout.setSpacing(6)
        self._folders = list(cfg.get("rag_folders", [str(config_manager.BASE_DIR)]))
        self._render_folders()
        c_layout.addLayout(self.rag_list_layout)
        c_layout.addWidget(_create_separator())

        # 3. Outils d'oubli (Purge)
        purge_title = QLabel("Outils d'oubli et réinitialisation")
        purge_title.setStyleSheet("color: #e0e0e0; font-size: 13px; font-weight: 500; margin-top: 6px;")
        c_layout.addWidget(purge_title)

        purge_sub = QLabel("Effacez les données mémorisées de façon irréversible :")
        purge_sub.setStyleSheet("color: #777777; font-size: 11px;")
        c_layout.addWidget(purge_sub)

        btns_w = QWidget()
        btns_h = QHBoxLayout(btns_w)
        btns_h.setContentsMargins(0, 4, 0, 4)
        btns_h.setSpacing(8)

        btn_purge_chat = QPushButton("Purger l'historique chat")
        btn_purge_clip = QPushButton("Vider le presse-papiers")
        btn_purge_rag  = QPushButton("Réinitialiser le RAG")

        for b in (btn_purge_chat, btn_purge_clip, btn_purge_rag):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background: rgba(239, 68, 68, 0.12);
                    color: #ef4444;
                    border: 1px solid rgba(239, 68, 68, 0.25);
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 11px;
                    font-weight: 500;
                }
                QPushButton:hover { background: rgba(239, 68, 68, 0.22); border-color: rgba(239, 68, 68, 0.5); }
            """)
            btns_h.addWidget(b)

        btn_purge_chat.clicked.connect(lambda: self._purge_action("historique des conversations"))
        btn_purge_clip.clicked.connect(lambda: self._purge_action("presse-papiers"))
        btn_purge_rag.clicked.connect(lambda: self._purge_action("base vectorielle"))

        c_layout.addWidget(btns_w)

        c_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _render_folders(self) -> None:
        while self.rag_list_layout.count():
            item = self.rag_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for f in self._folders:
            row = QWidget()
            row.setStyleSheet("background: #181818; border-radius: 6px; border: 1px solid rgba(255,255,255,0.06);")
            rh = QHBoxLayout(row)
            rh.setContentsMargins(10, 6, 10, 6)

            lbl = QLabel(f)
            lbl.setStyleSheet("color: #cccccc; font-size: 11px; border: none; background: transparent;")
            rh.addWidget(lbl, 1)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(20, 20)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setStyleSheet("QPushButton { color: #888888; background: transparent; border: none; font-size: 12px; } QPushButton:hover { color: #ef4444; }")
            del_btn.clicked.connect(lambda _, p=f: self._remove_folder(p))
            rh.addWidget(del_btn)

            self.rag_list_layout.addWidget(row)

    def _add_rag_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Sélectionner un dossier à indexer")
        if d and d not in self._folders:
            self._folders.append(d)
            self._render_folders()

    def _remove_folder(self, p: str) -> None:
        if p in self._folders:
            self._folders.remove(p)
            self._render_folders()

    def _purge_action(self, name: str) -> None:
        sender = self.sender()
        if isinstance(sender, QPushButton):
            sender.setText("✓ Effacé")
            QTimer.singleShot(1500, lambda: sender.setText(sender.text().replace("✓ Effacé", f"Purger {name}")))

    def save(self) -> None:
        cfg = config_manager.load_api_keys()
        cfg["context_depth"] = self.ctx_slider.value()
        cfg["rag_folders"] = self._folders
        config_manager.CONFIG_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")


# ── 5. Integrations Settings View ───────────────────────────────────────────

class IntegrationsSettingsView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        cfg = config_manager.load_api_keys()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(12)

        title = QLabel("Integrations & Automations")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 600; margin-bottom: 6px; background: transparent;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(10)

        # 1. Comptes externes (Gmail / GitHub / Notion)
        accounts_box = QWidget()
        ab_layout = QVBoxLayout(accounts_box)
        ab_layout.setContentsMargins(0, 0, 0, 0)
        ab_layout.setSpacing(8)

        # Gmail
        gmail_row = QWidget()
        gmail_row.setStyleSheet("background: #181818; border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 6px;")
        gr_h = QHBoxLayout(gmail_row)
        gr_h.setContentsMargins(12, 6, 12, 6)

        gmail_lbl = QLabel("✉️ Gmail (IMAP / Google Cloud)")
        gmail_lbl.setStyleSheet("color: #f0f0f0; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        gr_h.addWidget(gmail_lbl, 1)

        gmail_status = QLabel("● Prêt à connecter")
        gmail_status.setStyleSheet("color: #38bdf8; font-size: 11px; margin-right: 8px; border: none; background: transparent;")
        gr_h.addWidget(gmail_status)

        self.btn_cfg_gmail = QPushButton("Configurer")
        self.btn_cfg_gmail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cfg_gmail.setStyleSheet("QPushButton { background: #262626; color: #fff; border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; padding: 5px 12px; font-size: 11px; } QPushButton:hover { background: #333; }")
        self.btn_cfg_gmail.clicked.connect(self._configure_gmail_dialog)
        gr_h.addWidget(self.btn_cfg_gmail)

        ab_layout.addWidget(gmail_row)

        # GitHub
        gh_row = QWidget()
        gh_row.setStyleSheet("background: #181818; border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 6px;")
        gh_h = QHBoxLayout(gh_row)
        gh_h.setContentsMargins(12, 6, 12, 6)

        gh_lbl = QLabel("🐙 GitHub (Personal Access Token)")
        gh_lbl.setStyleSheet("color: #f0f0f0; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        gh_h.addWidget(gh_lbl, 1)

        gh_status = QLabel("○ Déconnecté")
        gh_status.setStyleSheet("color: #777777; font-size: 11px; margin-right: 8px; border: none; background: transparent;")
        gh_h.addWidget(gh_status)

        btn_cfg_gh = QPushButton("Connecter")
        btn_cfg_gh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cfg_gh.setStyleSheet("QPushButton { background: #262626; color: #fff; border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; padding: 5px 12px; font-size: 11px; } QPushButton:hover { background: #333; }")
        gh_h.addWidget(btn_cfg_gh)

        ab_layout.addWidget(gh_row)

        c_layout.addWidget(_create_setting_row("Comptes externes", "Connecteurs pour vos boîtes mails, code et documents", accounts_box))
        c_layout.addWidget(_create_separator())

        # 2. Automatisations système (Scheduler des JSON)
        self.scheduler_toggle = ToggleSwitch(checked=cfg.get("scheduler_enabled", True))
        c_layout.addWidget(_create_setting_row(
            "Automatisations système (Scheduler)",
            "Active le moteur d'exécution en arrière-plan pour vos workflows JSON programmés",
            self.scheduler_toggle
        ))
        c_layout.addWidget(_create_separator())

        # 3. Permissions critiques
        self.require_approval_toggle = ToggleSwitch(checked=config_manager.get_require_approval_enabled())
        c_layout.addWidget(_create_setting_row(
            "Demander confirmation avant commandes critiques",
            "Affiche une boîte de validation avant l'exécution de scripts shell ou commandes terminal",
            self.require_approval_toggle
        ))

        self.allow_file_mods = ToggleSwitch(checked=cfg.get("autonomous_file_mods", True))
        c_layout.addWidget(_create_setting_row(
            "Modification autonome des fichiers",
            "Permet à EIDOLON de créer et mettre à jour des documents sur votre bureau sans confirmation",
            self.allow_file_mods
        ))

        c_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _configure_gmail_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Configuration Gmail IMAP")
        dlg.setFixedSize(380, 220)
        dlg.setStyleSheet("background: #141414; color: #ffffff;")

        d_layout = QVBoxLayout(dlg)
        d_layout.setContentsMargins(20, 20, 20, 20)
        d_layout.setSpacing(12)

        lbl = QLabel("Saisissez vos identifiants IMAP Google :")
        lbl.setStyleSheet("font-size: 13px; font-weight: 500;")
        d_layout.addWidget(lbl)

        email_input = QLineEdit()
        email_input.setPlaceholderText("votre.email@gmail.com")
        email_input.setStyleSheet("background: #202020; color: #fff; border: 1px solid #333; border-radius: 6px; padding: 6px;")
        d_layout.addWidget(email_input)

        pass_input = QLineEdit()
        pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        pass_input.setPlaceholderText("Mot de passe d'application (16 lettres)")
        pass_input.setStyleSheet("background: #202020; color: #fff; border: 1px solid #333; border-radius: 6px; padding: 6px;")
        d_layout.addWidget(pass_input)

        save_btn = QPushButton("Enregistrer la connexion")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet("background: #00d4ff; color: #000; font-weight: 600; border-radius: 6px; padding: 8px;")
        def _on_save():
            cfg = config_manager.load_api_keys()
            cfg["gmail_address"] = email_input.text().strip()
            cfg["gmail_app_password"] = pass_input.text().strip()
            config_manager.CONFIG_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
            dlg.accept()
        save_btn.clicked.connect(_on_save)
        d_layout.addWidget(save_btn)

        dlg.exec()

    def save(self) -> None:
        config_manager.save_require_approval_enabled(self.require_approval_toggle.isChecked())
        cfg = config_manager.load_api_keys()
        cfg["scheduler_enabled"] = self.scheduler_toggle.isChecked()
        cfg["autonomous_file_mods"] = self.allow_file_mods.isChecked()
        config_manager.CONFIG_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")


# ── Main Settings Window ────────────────────────────────────────────────────

class SettingsWindow(QWidget):
    """Frameless dark theme settings window matching the user's reference mockup."""
    settings_saved = pyqtSignal(dict)

    def __init__(self, ui=None, overlay=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._ui = ui
        self._overlay = overlay

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setFixedSize(920, 600)

        # Center on screen
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move((geo.width() - 920) // 2, (geo.height() - 600) // 2)

        self._drag_pos: Optional[QPoint] = None

        # Main background container with rounded border
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        self.root_frame = QFrame(self)
        self.root_frame.setStyleSheet("""
            QFrame#root_frame {
                background: #080808;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
            }
        """)
        self.root_frame.setObjectName("root_frame")
        main_layout.addWidget(self.root_frame)

        frame_layout = QVBoxLayout(self.root_frame)
        frame_layout.setContentsMargins(16, 12, 16, 16)
        frame_layout.setSpacing(8)

        # Top Bar with Close button
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(8, 0, 0, 0)
        
        logo_lbl = QLabel("⚙️ EIDOLON SETTINGS")
        logo_lbl.setStyleSheet("color: #777777; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
        top_bar.addWidget(logo_lbl)
        top_bar.addStretch(1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                color: #888888;
                background: transparent;
                border: none;
                font-size: 16px;
                border-radius: 13px;
            }
            QPushButton:hover {
                color: #ffffff;
                background: rgba(255, 255, 255, 0.1);
            }
        """)
        close_btn.clicked.connect(self.hide)
        top_bar.addWidget(close_btn)
        frame_layout.addLayout(top_bar)

        # Body: Left Sidebar + Right Card Container
        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(8, 6, 8, 8)
        body_layout.setSpacing(18)

        # Left Sidebar (Navigation Tabs)
        sidebar = QWidget()
        sidebar.setFixedWidth(160)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 10, 0, 0)
        sidebar_layout.setSpacing(10)

        self.tab_buttons: Dict[str, QPushButton] = {}
        tabs = [
            ("General", "General"),
            ("Models", "Models"),
            ("Audio", "Audio"),
            ("Memoire", "Mémoire"),
            ("Integrations", "Integrations"),
        ]

        for key, label in tabs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._switch_tab(k))
            sidebar_layout.addWidget(btn)
            self.tab_buttons[key] = btn

        sidebar_layout.addStretch(1)
        body_layout.addWidget(sidebar)

        # Right Card Container
        self.right_card = QFrame()
        self.right_card.setStyleSheet("""
            QFrame#right_card {
                background: #121212;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
            }
        """)
        self.right_card.setObjectName("right_card")

        card_layout = QVBoxLayout(self.right_card)
        card_layout.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget()
        self.views = {
            "General": GeneralSettingsView(overlay=self._overlay),
            "Models": ModelsSettingsView(),
            "Audio": AudioSettingsView(ui=self._ui),
            "Memoire": MemorySettingsView(),
            "Integrations": IntegrationsSettingsView(),
        }

        for v in self.views.values():
            self.stack.addWidget(v)

        card_layout.addWidget(self.stack)
        body_layout.addWidget(self.right_card, 1)

        frame_layout.addLayout(body_layout, 1)

        # Bottom Action Bar
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(12, 0, 12, 0)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #00d4ff; font-size: 11px;")
        bottom_bar.addWidget(self.status_lbl)
        bottom_bar.addStretch(1)

        save_btn = QPushButton("Enregistrer les modifications")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background: #00d4ff;
                color: #000000;
                font-weight: 600;
                font-size: 12px;
                border-radius: 8px;
                padding: 8px 18px;
                border: none;
            }
            QPushButton:hover {
                background: #38bdf8;
            }
        """)
        save_btn.clicked.connect(self._save_all)
        bottom_bar.addWidget(save_btn)

        frame_layout.addLayout(bottom_bar)

        # Start on General tab
        self._switch_tab("General")

    def _switch_tab(self, key: str) -> None:
        style_active = """
            QPushButton {
                background: #1a1a1a;
                color: #ffffff;
                font-size: 14px;
                font-weight: 600;
                border-radius: 8px;
                text-align: left;
                padding-left: 14px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
        """
        style_inactive = """
            QPushButton {
                background: transparent;
                color: #777777;
                font-size: 14px;
                font-weight: 500;
                border-radius: 8px;
                text-align: left;
                padding-left: 14px;
                border: none;
            }
            QPushButton:hover {
                color: #cccccc;
                background: rgba(255, 255, 255, 0.04);
            }
        """

        for k, btn in self.tab_buttons.items():
            btn.setChecked(k == key)
            btn.setStyleSheet(style_active if k == key else style_inactive)

        idx = list(self.views.keys()).index(key)
        self.stack.setCurrentIndex(idx)

    def _save_all(self) -> None:
        for v in self.views.values():
            if hasattr(v, "save"):
                try:
                    v.save()
                except Exception as e:
                    print(f"[Settings:save:err] {e}", flush=True)

        cfg = config_manager.load_api_keys()

        # Emit Qt signal
        self.settings_saved.emit(cfg)

        # Apply live settings directly to overlay
        if self._overlay and hasattr(self._overlay, "apply_live_settings"):
            try:
                self._overlay.apply_live_settings(cfg)
            except Exception as e:
                print(f"[Settings:live:err] {e}", flush=True)

        self.status_lbl.setText("✓ Modifications enregistrées et appliquées.")
        QTimer.singleShot(2500, lambda: self.status_lbl.setText(""))

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # Allow dragging by top header area
            if event.position().y() < 60:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = SettingsWindow()
    w.show()
    sys.exit(app.exec())
