# Changelog

All notable changes to **EIDOLON** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.1] - 2026-11-25

### Added
- **Windows installer**: `EIDOLON-Setup-<version>.exe` (PyInstaller + Inno Setup), per-user install without admin rights; built and drafted as a GitHub Release when a `v*` tag is pushed.
- **First-launch API key window**: the Gemini key is asked in a small HUD-styled window (no console needed) and stored only on the user's PC.

### Fixed
- Release build no longer mistakes public library certificates (`certifi`) for personal data.

## [1.0.0] - 2026-11-19

### Added
- **EIDOLON Floating Pill HUD (`ui.py`)**: Native PyQt6 frameless, per-pixel translucent overlay with 64-element reactive voice visualizer, expandable chat drawer, resource cards, volume slider pill, and inline confirmation panel.
- **12-Expert Agent Team (`core/agent_team.py`, `core/agent_team_window.py`, `actions/agent_team_action.py`)**: Proactive clarification, dynamic team planning from `agents/registry.json`, parallel execution, independent review pass, and `.md`/`.docx` deliverable generation.
- **IDE Bridge & In-HUD Code Review (`core/ide_bridge.py`, `core/code_review_panel.py`, `integrations/claude_code/eidolon_hook.py`)**: Real-time diff preview, approve/reject gating for Claude Code and watched workspaces, and risky shell command interception.
- **Smart File Manager (`core/smart_organizer.py`, `actions/smart_file_manager.py`)**: Read-only planning, SHA-256 duplicate detection, safe quarantine, audit logging, and rollback support.
- **Universal File Converter (`actions/file_converter.py`)**: Multi-format conversion with clickable HUD result cards.
- **5-Tab Settings Window (`core/settings_window.py`)**: Dedicated PyQt6 configuration interface for General, Models, Audio, Memory, and Integrations.
- **Low-Token Voice Session (`core/lowtoken_session.py`)**: Alternative STT → streaming text LLM → Edge TTS / Kokoro pipeline.
- **Automation Pipelines & Scheduler (`automation/`, `workflows/`)**: Sequential JSON workflow execution and background scheduling.
- **Repository Standards**: English documentation (`README.md`, `docs/`), `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates, and GitHub Actions CI syntax validation.
