# EIDOLON

**Cross-Platform Real-Time Voice AI Desktop Assistant, Multi-Agent Command Center & IDE Review HUD**

[![Python](https://img.shields.io/badge/Python-3.11--3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![UI](https://img.shields.io/badge/UI-PyQt6-41CD52?logo=qt&logoColor=white)](https://www.riverbankcomputing.com/software/pyqt/)
[![AI](https://img.shields.io/badge/Engine-Gemini%20Live%20API-4285F4?logo=google&logoColor=white)](https://ai.google.dev/)
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](LICENSE)

Maintained by **[Aymerick (@AYMRIKNG)](https://github.com/AYMRIKNG)**

---

## Overview

**EIDOLON** is a native desktop AI assistant built with Python, PyQt6, and the Google Gemini API. It sits discreetly at the top of your screen as a frameless, per-pixel translucent floating pill HUD and combines real-time voice interaction, an expandable chat & confirmation interface, an in-HUD code review bridge for AI coding tools, a 12-expert C-level multi-agent team, reversible file automation, and a local encrypted mobile dashboard.

### Key Capabilities

| Area | Highlights |
|---|---|
| **Translucent Floating HUD** | Frameless, always-on-top PyQt6 pill overlay (`ui.py`) with 64-element reactive audio visualizer, expandable chat drawer, resource cards, volume pill, and reversible confirmation dialogs. |
| **Dual Voice Engines** | Native real-time audio streaming via **Gemini Live API** (`main.py`) plus a **Low-Token Voice Session** (`core/lowtoken_session.py`: local VAD → STT → streaming text LLM → Edge TTS / Kokoro) for cost-efficient long sessions. |
| **12-Expert Agent Team** | Multi-agent mission runner (`core/agent_team.py`, `core/agent_team_window.py`) backed by 12 specialized C-level profiles (`agents/registry.json`: CDAO, CRO, CCO, CISO, CFO, CMO, CSCO, CTO, CAO, CLO, CPO, CSO) with proactive clarification, parallel execution, peer review, and `.md`/`.docx` deliverables. |
| **IDE Bridge & Code Review HUD** | Live diff preview and approve/reject gate (`core/ide_bridge.py`, `core/code_review_panel.py`) with Claude Code hooks (`integrations/claude_code/eidolon_hook.py`) and folder watching for Antigravity, Cursor, Copilot, and scripts — including risky shell command confirmation. |
| **Smart File Manager & Undo** | Read-only planning, SHA-256 duplicate detection, 4 organization strategies (`by_type`, `by_date`, `by_extension`, `by_project`), strict system-path protection, JSONL audit logs, and full rollback (`core/smart_organizer.py`, `core/undo.py`, `actions/smart_file_manager.py`). |
| **Universal File Converter** | Convert TXT, Markdown, source code, DOCX, CSV/Excel, PPTX, images, and audio into PDF, DOCX, XLSX, or images with clickable HUD result cards (`actions/file_converter.py`). |
| **5-Tab Settings Interface** | Native PyQt6 modal (`core/settings_window.py`) covering **General**, **Models**, **Audio**, **Memory**, and **Integrations**, persisted locally to `config/api_keys.json`. |
| **Persistent & Recallable Memory** | Structured long-term user memory (`memory/memory_manager.py`) with on-demand recall so context stays bounded without losing facts. |
| **Automation Pipelines & Scheduler** | Chained JSON workflow runner and background scheduler (`automation/`, `workflows/`). |
| **Encrypted Local Remote Dashboard** | FastAPI + WebSocket remote control server (`dashboard/server.py`) with QR pairing and application-layer AES-256-CBC encryption. |
| **Auto-Discovered Actions & Plugins** | Drop a `.py` module into `actions/` or `plugins/` (`core/action_loader.py`, `core/plugin_loader.py`) to extend EIDOLON at next launch. |

---

## Architecture

```text
                         ┌───────────────────────────────────────┐
                         │         EIDOLON Entry Point           │
                         │               main.py                 │
                         └──────────┬────────────────┬───────────┘
                                    │                │
              Voice / LLM Engine    │                │  Native Desktop UI
      ┌─────────────────────────────▼──┐          ┌──▼──────────────────────────────┐
      │ Gemini Live / Low-Token Engine │          │    Floating Pill HUD (ui.py)    │
      │ core/gemini.py, lowtoken_...   │          │  Chat • Confirm • Resource Cards│
      │ core/stt.py, tts.py, echo.py   │          │  Settings • Code Review • Team  │
      └───────────────┬────────────────┘          └──────────────┬──────────────────┘
                      │                                          │
          ┌───────────┼──────────────────┬───────────────────────┼────────────┐
          ▼           ▼                  ▼                       ▼            ▼
   ┌────────────┐ ┌───────────┐  ┌───────────────┐      ┌────────────────┐ ┌───────────┐
   │  Actions   │ │  Plugins  │  │  Agent Team   │      │   IDE Bridge   │ │ Dashboard │
   │ actions/*  │ │ plugins/* │  │ core/agent_*  │      │ core/ide_bridge│ │ dashboard │
   │ (24 tools) │ │ (custom)  │  │ agents/*.json │      │ Claude / Watch │ │ FastAPI   │
   └────────────┘ └───────────┘  └───────────────┘      └────────────────┘ └───────────┘
```

---

## Quick Start

### Prerequisites

- **Python 3.11 – 3.13**
- A **Google Gemini API key** ([Google AI Studio](https://aistudio.google.com/))
- Windows, macOS, or Linux

### Installation

```bash
git clone https://github.com/AYMRIKNG/EIDOLON.git
cd EIDOLON

# Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# Install OS-appropriate dependencies and Playwright browsers
python setup.py
```

### Launch

```bash
python main.py
```

On first launch, EIDOLON opens the onboarding/settings flow where you enter your Gemini API key, display name, and audio preferences. Configuration is saved locally to `config/api_keys.json` (gitignored).

---

## Project Structure

```text
EIDOLON/
├── main.py                        # Main runtime, Gemini Live loop, tool dispatch
├── ui.py                          # Native PyQt6 translucent overlay pill HUD
├── setup.py                       # Cross-platform setup & Playwright installer
├── requirements.txt               # OS-marker filtered Python dependencies
├── core/                          # Core engines & UI windows
│   ├── branding.py                # Centralized EIDOLON brand & runtime identity
│   ├── action_loader.py           # Auto-discovery of actions/
│   ├── plugin_loader.py           # Auto-discovery of plugins/
│   ├── agent_registry.py          # 12 C-Level expert profiles loader
│   ├── agent_team.py              # Multi-agent mission planner, runner & reviewer
│   ├── agent_team_window.py       # Multi-agent live supervision window
│   ├── agent_events.py            # Pub/sub event bus for missions
│   ├── ide_bridge.py              # Local HTTP bridge & file watcher for coding AIs
│   ├── code_review_panel.py       # In-HUD syntax-highlighted diff review panel
│   ├── settings_window.py         # 5-tab PyQt6 Settings modal
│   ├── smart_organizer.py         # File analysis, SHA-256 dedup & safe executor
│   ├── lowtoken_session.py        # Low-token STT -> LLM -> TTS session
│   ├── avatar.py / avatar_mesh.py # Software-rendered holographic head & lip-sync
│   ├── confirm.py / undo.py       # Human confirmation & rollback stack
│   └── ...                        # Audio devices, echo guard, wake word, hotkey
├── actions/                       # Built-in voice & text tools (24 modules)
├── plugins/                       # User-extensible plugins (_template.py included)
├── agents/                        # Expert team registry (registry.json)
├── automation/                    # Chained pipeline runner & workflow scheduler
├── workflows/                     # Sample JSON automation workflows
├── dashboard/                     # FastAPI + AES-256 mobile remote dashboard
├── integrations/                  # External hooks (Claude Code review hook)
├── memory/                        # Config manager & persistent memory engine
├── benchmarks/                    # Token usage benchmark utilities
└── docs/                          # Deep-dive subsystem documentation
```

---

## Documentation

- [Multi-Agent Team & Orchestration](docs/MULTI_AGENT_ORCHESTRATOR.md)
- [Smart File Manager](docs/SMART_FILE_MANAGER.md)
- [Contributing Guide](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

---

## Security & Privacy

- API keys, OAuth tokens, TLS certificates, personal memory (`memory/long_term.json`), and runtime outputs (`output/`) are excluded via `.gitignore` and stay strictly on your local machine.
- High-impact actions (system power, risky shell commands via IDE bridge, bulk file changes) require explicit human confirmation and support undo where applicable.
- See [SECURITY.md](SECURITY.md) for responsible disclosure guidelines.

---

## License

**EIDOLON** — Copyright © 2026 **Aymerick ([@AYMRIKNG](https://github.com/AYMRIKNG))**.

Distributed under the **Creative Commons Attribution-NonCommercial 4.0 International License (CC BY-NC 4.0)**. Commercial use is not permitted. See [LICENSE](LICENSE) for full terms and notices.
