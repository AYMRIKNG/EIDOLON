# EIDOLON Multi-Agent Team Architecture

This document describes EIDOLON's multi-agent mission subsystem, the 12 specialized C-Level expert profiles, the mission lifecycle, and the supervision interface.

---

## 1. Architecture Overview

When a user requests a complex, strategic, or cross-functional deliverable (e.g., security audit, business plan, cloud architecture, contract review), EIDOLON delegates the mission to a coordinated team of specialized expert profiles without blocking the live voice session.

```text
                        ┌─────────────────────────┐
                        │     User / EIDOLON      │
                        └────────────┬────────────┘
                                     │ Natural voice / text request
                                     ▼
                       ┌───────────────────────────┐
                       │   AgentTeamManager        │
                       │   (core/agent_team.py)    │
                       └─────┬───────────────┬─────┘
                             │               │
         Clarification, Plan │               │ Real-time events
         & Parallel Dispatch │               │
                             ▼               ▼
       ┌──────────────────────────┐    ┌──────────────────────────┐
       │   12 Expert Profiles     │    │      AgentEventBus       │
       │   (core/agent_registry)  │    │   (core/agent_events)    │
       │   agents/registry.json   │    └────────────┬─────────────┘
       └─────────────┬────────────┘                 │
                     │                              ▼
                     ▼                 ┌──────────────────────────┐
       ┌──────────────────────────┐    │   Agent Team Window      │
       │  Peer Review & Synthesis │    │ (core/agent_team_window) │
       └─────────────┬────────────┘    └──────────────────────────┘
                     │
                     ▼
       ┌──────────────────────────┐
       │ Deliverables (.md/.docx) │
       │    output/missions/      │
       └──────────────────────────┘
```

---

## 2. Mission Lifecycle

Every mission executed by `core/agent_team.py` moves through well-defined stages:

1. **`clarifying`**: EIDOLON checks whether essential parameters are missing and asks 2–3 focused questions before launching work.
2. **`planning`**: Reads `agents/registry.json` and selects only the experts needed for the brief, assigning concrete tasks and dependencies.
3. **`working`**: Selected experts run concurrently (up to `PARALLEL_AGENTS = 3`) in background threads with grounded web search when fresh information is required.
4. **`reviewing`**: An independent reviewer checks all contributions against the brief for contradictions or gaps and runs a correction pass if needed.
5. **`writing`**: Consolidates the reviewed work into a unified deliverable saved as `.md` and `.docx` under `output/missions/`.
6. **Terminal states**: `done`, `failed`, or `cancelled`.

---

## 3. The 12 Expert Profiles (`agents/registry.json`)

| Acronym | Role | Core Focus |
|---|---|---|
| **CDAO** | Chief Data & Automation Officer | Workflows (Make/n8n), RAG, autonomous AI agents, data pipelines |
| **CRO** | Chief Revenue Officer | B2B enterprise sales, MEDDIC/SPIN negotiation, RFP & closing |
| **CCO** | Chief Creative Officer | Art direction, cinematography, VFX & AI video generation |
| **CISO** | Chief Information Security Officer | InfoSec, ISO 27001, AI governance (ISO 42001, EU AI Act) |
| **CFO** | Chief Financial Officer | Financial modeling, cash flow, international tax & M&A |
| **CMO** | Chief Marketing Officer | Growth, neuromarketing, copywriting, SEO/AIO/GIO, funnels |
| **CSCO** | Chief Spatial Computing Officer | Web3D, Three.js, WebXR & immersive commerce |
| **CTO** | Chief Technology Officer | High-availability architecture, Cloud, Kubernetes & FinOps |
| **CAO** | Chief Accounting Officer | Accounting engineering, tax & social compliance |
| **CLO** | Chief Legal Officer | Corporate law, IP, GDPR & international contracts |
| **CPO** | Chief Product Officer | Product-Led Growth, SaaS architecture, UX/UI & JTBD |
| **CSO** | Chief Strategy Officer | Fundraising, DCF/multiples valuation, pitch decks & scaling |

---

## 4. HUD Integration & Voice Commands

- **Supervision Window (`core/agent_team_window.py`)**: Opened directly from the HUD pill or by voice command. Displays active missions, per-agent progress, live logs, and generated deliverables.
- **Voice / Chat Tool (`actions/agent_team_action.py`)**: Allows launching missions, answering clarification questions, checking live status, cancelling missions, and inspecting the expert roster naturally by voice or text.
- **Outputs**: Mission artifacts are saved locally under `output/missions/` (excluded from version control via `.gitignore`).
