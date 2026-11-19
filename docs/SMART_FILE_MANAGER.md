# EIDOLON Smart File Manager

The `smart_file_manager` subsystem gives EIDOLON safe, auditable, and reversible capabilities to analyze, deduplicate, search, and organize local directories.

---

## 1. Architecture

The subsystem is split into three clean layers:

1. **Core Engine (`core/smart_organizer.py`)**:
   - `SmartFileAnalyzer`: Recursive tree scanning, file categorization (9 core categories + safe fallback), exact duplicate detection via size pre-filter + 64 KB chunked SHA-256 hashing, and empty-directory discovery.
   - `SmartClassificationPlanner`: Read-only dry-run planner supporting `by_type`, `by_date`, `by_extension`, and `by_project`.
   - `SecureSystemExecutor`: Safe execution layer with strict boundary enforcement, collision avoidance via numeric suffixes `(1)`, `(2)`, JSONL audit logging (`output/file_operations.log`), and automatic registration in the undo stack (`core/undo.py`).
   - `run_powershell_query`: Structured diagnostics on Windows without shell-injection risk.

2. **Auto-Discovered Action (`actions/smart_file_manager.py`)**:
   - Discovered at startup by `core/action_loader.py`.
   - Exposed to the voice/text LLM through its `TOOL` schema, with visual resource cards and HUD confirmation panels for bulk operations.

3. **Automation Pipeline Hooks (`automation/tool_registry.py`)**:
   - Registered as `files:analyze` and `files:organize` for scheduled JSON workflows.

---

## 2. Safety Perimeter

### Allowed User Shortcuts
- `downloads` — User Downloads folder
- `documents` — User Documents folder
- `desktop` — User Desktop folder
- `pictures`, `music`, `videos` — Standard user media folders
- `scratch` — Workspace temporary sandbox folder

### Strictly Blocked Paths
- Operating system directories (`C:\Windows`, `C:\Program Files`, `C:\Program Files (x86)`, `C:\ProgramData`, `C:\Recovery`, `C:\System Volume Information`, `/System`, `/usr`, `/etc`)
- Direct volume roots (`C:\`, `D:\`, `/`)
- Repository & environment internals (`.git`, `.venv`, `__pycache__`, `node_modules`)

---

## 3. Classification Strategies

| Strategy | Behavior | Example Layout |
|---|---|---|
| `by_type` (default) | Groups files by semantic category | `Documents/`, `Images/`, `Spreadsheets/`, `Code/`, `Archives/` |
| `by_date` | Groups chronologically by modification date | `2026/2026-10/`, `2026/2026-09/` |
| `by_extension` | Strict grouping by file extension | `PDF/`, `PNG/`, `DOCX/`, `XLSX/` |
| `by_project` | Clusters files by detected project prefix/keywords | `Project_Alpha/`, `Project_Report/`, `Misc/` |

---

## 4. Audit Logging & Undo

Every batch execution writes a JSONL audit entry to `output/file_operations.log`:

```json
{
  "timestamp": "2026-11-19T12:00:00.000000",
  "plan_id": "plan_20261119_120000_15",
  "source_folder": "<USER_HOME>/Downloads",
  "strategy": "by_type",
  "moved_count": 15,
  "skipped_count": 0,
  "error_count": 0,
  "pruned_folders": ["Empty_Folder_1"],
  "errors": []
}
```

Any organization or duplicate-quarantine action can be reverted cleanly through the undo stack (`core/undo.py`).
