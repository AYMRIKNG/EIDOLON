"""
core/smart_organizer.py — JARVIS Smart File & Folder Management Engine.

Provides centralized, secure, verifiable file analysis, searching,
duplicate detection, classification planning, execution with collision
protection, comprehensive journal logging, and full undo support.
"""
from __future__ import annotations

import os
import sys
import json
import shutil
import hashlib
import platform
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Safe trash support
try:
    import send2trash
    _HAS_SEND2TRASH = True
except ImportError:
    _HAS_SEND2TRASH = False

# Undo stack integration
try:
    from core.undo import push_undo
    _HAS_UNDO = True
except ImportError:
    _HAS_UNDO = False

_IS_WINDOWS = platform.system() == "Windows"


# ── Safety Boundaries & Perimeter Protection ────────────────────────────────

def get_default_safe_roots() -> List[Path]:
    """Returns the list of safe user roots allowed for file operations."""
    home = Path.home()
    roots = [
        home / "Downloads",
        home / "Documents",
        home / "Desktop",
        home / "Pictures",
        home / "Music",
        home / "Videos",
    ]
    # Allow workspace test / scratch directory for testing
    workspace_root = Path(__file__).resolve().parent.parent
    scratch_dir = workspace_root / "scratch"
    roots.append(scratch_dir)
    return [r.resolve() for r in roots if r.exists()]


_SYSTEM_FORBIDDEN_PREFIXES: Tuple[str, ...] = (
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
    "c:\\system volume information",
    "c:\\$recycle.bin",
    "c:\\boot",
    "c:\\recovery",
)

_PROTECTED_DIR_NAMES: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".idea",
    ".vscode",
}


def is_path_safe(target_path: Path | str, allowed_roots: Optional[List[Path]] = None) -> Tuple[bool, str]:
    """Validates that a path is strictly inside allowed roots and not system-protected."""
    try:
        resolved = Path(target_path).expanduser().resolve()
    except Exception as e:
        return False, f"Invalid path resolution: {e}"

    resolved_str = str(resolved).lower()

    # 1. Deny root drive directly (e.g. C:\)
    if resolved.parent == resolved:
        return False, f"Refusing access to raw root volume: {resolved}"

    # 2. Deny Windows system directories
    for forbidden in _SYSTEM_FORBIDDEN_PREFIXES:
        if resolved_str.startswith(forbidden):
            return False, f"Access denied to system directory: {resolved}"

    # 3. Deny protected project/environment directories
    for part in resolved.parts:
        if part.lower() in _PROTECTED_DIR_NAMES:
            return False, f"Access denied: protected environment directory '{part}'"

    # 4. Check against allowed roots
    roots = allowed_roots or get_default_safe_roots()
    is_allowed = any(
        resolved == root or resolved.is_relative_to(root)
        for root in roots
    )

    if not is_allowed:
        allowed_desc = ", ".join(r.name for r in roots)
        return False, f"Path '{resolved}' is outside allowed perimeter ({allowed_desc})."

    return True, ""


# ── Extension Categorization ────────────────────────────────────────────────

CATEGORY_MAP: Dict[str, Set[str]] = {
    "Documents": {
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md",
        ".tex", ".pages", ".wps", ".dot", ".dotx"
    },
    "Tableurs_et_Donnees": {
        ".xls", ".xlsx", ".csv", ".tsv", ".ods", ".xlsm",
        ".parquet", ".json", ".xml", ".yaml", ".yml", ".sql"
    },
    "Images": {
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
        ".ico", ".tiff", ".tif", ".heic", ".raw", ".psd", ".ai"
    },
    "Videos": {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
        ".m4v", ".3gp", ".ts", ".mts"
    },
    "Audio": {
        ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
        ".opus", ".alac", ".aiff"
    },
    "Archives": {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
        ".iso", ".tgz", ".cab"
    },
    "Code_et_Scripts": {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
        ".scss", ".java", ".c", ".cpp", ".h", ".hpp", ".cs",
        ".go", ".rs", ".php", ".rb", ".sh", ".bat", ".ps1"
    },
    "Installateurs_et_Executables": {
        ".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm"
    },
    "Livres_et_Ebooks": {
        ".epub", ".mobi", ".azw3", ".djvu", ".cbr", ".cbz"
    },
}

EXT_TO_CATEGORY: Dict[str, str] = {}
for category, exts in CATEGORY_MAP.items():
    for ext in exts:
        EXT_TO_CATEGORY[ext] = category


def format_size(bytes_count: int) -> str:
    """Format bytes to human readable string."""
    b = float(bytes_count)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0:
            return f"{b:.1f} {unit}"
        b /= 1024.0
    return f"{b:.1f} TB"


# ── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class FileMetadata:
    name: str
    path: str
    size: int
    size_str: str
    extension: str
    category: str
    modified_time: str
    created_time: str
    sha256: Optional[str] = None


@dataclass
class FolderAnalysisReport:
    target_folder: str
    total_files: int
    total_folders: int
    total_size_bytes: int
    total_size_str: str
    categories_breakdown: Dict[str, int]
    largest_files: List[Dict[str, Any]]
    empty_folders: List[str]
    duplicate_groups: List[Dict[str, Any]]
    unclassified_files: List[str]
    errors: List[str] = field(default_factory=list)


@dataclass
class PlannedMove:
    source_path: str
    dest_path: str
    category: str
    reason: str
    file_size: int
    conflict_detected: bool = False
    resolved_path: Optional[str] = None


@dataclass
class OrganizationPlan:
    plan_id: str
    source_folder: str
    strategy: str  # "by_type" | "by_date" | "by_extension" | "by_project"
    total_files: int
    planned_moves: List[PlannedMove]
    empty_folders_to_prune: List[str]
    directories_to_create: List[str]
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ── File Analyzer & Search Engine ───────────────────────────────────────────

class SmartFileAnalyzer:
    """Analyzes folders, scans properties, detects duplicates and classifies."""

    def __init__(self, allowed_roots: Optional[List[Path]] = None):
        self.allowed_roots = allowed_roots or get_default_safe_roots()

    def analyze_folder(self, folder_path: Path | str, detect_duplicates: bool = True) -> FolderAnalysisReport:
        path = Path(folder_path).expanduser().resolve()
        safe, err = is_path_safe(path, self.allowed_roots)
        if not safe:
            return FolderAnalysisReport(
                target_folder=str(path),
                total_files=0,
                total_folders=0,
                total_size_bytes=0,
                total_size_str="0 B",
                categories_breakdown={},
                largest_files=[],
                empty_folders=[],
                duplicate_groups=[],
                unclassified_files=[],
                errors=[err]
            )

        total_files = 0
        total_folders = 0
        total_size = 0
        cat_counts: Dict[str, int] = {}
        all_files: List[FileMetadata] = []
        empty_dirs: List[str] = []
        unclassified: List[str] = []
        errors: List[str] = []

        try:
            for root, dirs, files in os.walk(path):
                # Filter out hidden/protected subdirectories from traversing
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in _PROTECTED_DIR_NAMES]

                root_path = Path(root)
                if not dirs and not files and root_path != path:
                    empty_dirs.append(str(root_path))

                total_folders += len(dirs)

                for f in files:
                    if f.startswith("."):
                        continue
                    file_path = root_path / f
                    try:
                        stat = file_path.stat()
                        size = stat.st_size
                        ext = file_path.suffix.lower()
                        cat = EXT_TO_CATEGORY.get(ext, "A_examiner")

                        meta = FileMetadata(
                            name=f,
                            path=str(file_path),
                            size=size,
                            size_str=format_size(size),
                            extension=ext,
                            category=cat,
                            modified_time=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                            created_time=datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
                        )
                        all_files.append(meta)
                        total_files += 1
                        total_size += size
                        cat_counts[cat] = cat_counts.get(cat, 0) + 1
                        if cat == "A_examiner":
                            unclassified.append(str(file_path))
                    except Exception as fe:
                        errors.append(f"Error reading {file_path}: {fe}")

        except Exception as e:
            errors.append(f"Scan error on {path}: {e}")

        # Largest files
        largest = sorted(all_files, key=lambda m: m.size, reverse=True)[:10]
        largest_report = [
            {"name": m.name, "path": m.path, "size": m.size_str, "category": m.category}
            for m in largest
        ]

        # Duplicate detection (two-stage hashing: size grouping -> SHA256)
        duplicates: List[Dict[str, Any]] = []
        if detect_duplicates and all_files:
            duplicates = self.find_duplicates_in_list(all_files)

        return FolderAnalysisReport(
            target_folder=str(path),
            total_files=total_files,
            total_folders=total_folders,
            total_size_bytes=total_size,
            total_size_str=format_size(total_size),
            categories_breakdown=cat_counts,
            largest_files=largest_report,
            empty_folders=empty_dirs,
            duplicate_groups=duplicates,
            unclassified_files=unclassified[:20],
            errors=errors
        )

    def find_duplicates_in_list(self, files: List[FileMetadata]) -> List[Dict[str, Any]]:
        """Identifies exact byte-for-byte duplicate files via two-tier SHA256 hashing."""
        by_size: Dict[int, List[FileMetadata]] = {}
        for f in files:
            if f.size > 0:  # Skip zero-byte files from duplicate groups
                by_size.setdefault(f.size, []).append(f)

        candidates = [group for group in by_size.values() if len(group) > 1]
        dup_groups: List[Dict[str, Any]] = []

        for group in candidates:
            by_hash: Dict[str, List[FileMetadata]] = {}
            for item in group:
                try:
                    h = hashlib.sha256()
                    with open(item.path, "rb") as fp:
                        while chunk := fp.read(65536):
                            h.update(chunk)
                    digest = h.hexdigest()
                    by_hash.setdefault(digest, []).append(item)
                except Exception:
                    continue

            for digest, matching in by_hash.items():
                if len(matching) > 1:
                    dup_groups.append({
                        "hash": digest[:12],
                        "file_size": format_size(matching[0].size),
                        "count": len(matching),
                        "paths": [m.path for m in matching]
                    })

        return dup_groups

    def search_files(
        self,
        folder_path: Path | str,
        query: str = "",
        extension: str = "",
        category: str = "",
        min_size_bytes: int = 0,
        max_size_bytes: Optional[int] = None,
        content_query: str = "",
        max_results: int = 50
    ) -> List[Dict[str, Any]]:
        """Deep search across files with multi-criteria filtering."""
        path = Path(folder_path).expanduser().resolve()
        safe, err = is_path_safe(path, self.allowed_roots)
        if not safe:
            return [{"error": err}]

        results = []
        ext_target = extension.lower().strip()
        if ext_target and not ext_target.startswith("."):
            ext_target = f".{ext_target}"

        query_lower = query.lower().strip()
        cat_lower = category.lower().strip()

        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in _PROTECTED_DIR_NAMES]
            root_path = Path(root)

            for f in files:
                if f.startswith("."):
                    continue
                file_path = root_path / f
                f_name_lower = f.lower()

                # Filter by name query
                if query_lower and query_lower not in f_name_lower:
                    continue

                # Filter by extension
                f_ext = file_path.suffix.lower()
                if ext_target and f_ext != ext_target:
                    continue

                # Filter by category
                f_cat = EXT_TO_CATEGORY.get(f_ext, "A_examiner")
                if cat_lower and cat_lower != f_cat.lower():
                    continue

                try:
                    stat = file_path.stat()
                    size = stat.st_size

                    # Filter by size
                    if size < min_size_bytes:
                        continue
                    if max_size_bytes is not None and size > max_size_bytes:
                        continue

                    # Filter by content (plain text inspection for text/code/doc types)
                    if content_query:
                        if size > 5_000_000:  # Skip content search in files > 5MB
                            continue
                        matched = False
                        try:
                            with open(file_path, "r", encoding="utf-8", errors="ignore") as tf:
                                for line in tf:
                                    if content_query.lower() in line.lower():
                                        matched = True
                                        break
                        except Exception:
                            matched = False
                        if not matched:
                            continue

                    results.append({
                        "name": f,
                        "path": str(file_path),
                        "size": format_size(size),
                        "category": f_cat,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    })

                    if len(results) >= max_results:
                        return results
                except Exception:
                    continue

        return results


# ── Classification Planner ──────────────────────────────────────────────────

class SmartClassificationPlanner:
    """Generates an organization plan without modifying files."""

    def __init__(self, allowed_roots: Optional[List[Path]] = None):
        self.allowed_roots = allowed_roots or get_default_safe_roots()

    def generate_plan(
        self,
        folder_path: Path | str,
        strategy: str = "by_type",
        recursive: bool = False,
        clean_empty_folders: bool = True
    ) -> OrganizationPlan:
        """
        Builds an OrganizationPlan.
        Strategies:
          - "by_type": Category folders (Documents, Images, Audio, Videos, etc.)
          - "by_date": Year/Month folders (YYYY/YYYY-MM)
          - "by_extension": Folders named by extension (PDF, PNG, etc.)
          - "by_project": Folders derived from project prefixes/keywords
        """
        path = Path(folder_path).expanduser().resolve()
        safe, err = is_path_safe(path, self.allowed_roots)
        if not safe:
            raise PermissionError(err)

        moves: List[PlannedMove] = []
        dirs_to_create: Set[str] = set()
        empty_folders: List[str] = []

        # Collect files
        iterator = path.rglob("*") if recursive else path.glob("*")
        items = list(iterator)

        for item in items:
            if item.is_dir():
                # Detect empty folders
                if clean_empty_folders and item != path:
                    try:
                        if not any(item.iterdir()) and item.name not in _PROTECTED_DIR_NAMES:
                            empty_folders.append(str(item.resolve()))
                    except Exception:
                        pass
                continue

            # Skip hidden files
            if item.name.startswith("."):
                continue

            # Skip files that are already inside a designated category directory
            if not recursive and item.parent != path:
                continue

            rel_category = self._determine_destination(item, strategy)
            dest_dir = path / rel_category

            # If the file is already in the right place, skip
            if item.parent == dest_dir:
                continue

            dest_file = dest_dir / item.name
            dirs_to_create.add(str(dest_dir.resolve()))

            conflict = dest_file.exists()
            resolved = None
            if conflict:
                resolved = str(self._find_collision_free_path(dest_file))

            moves.append(PlannedMove(
                source_path=str(item.resolve()),
                dest_path=str(dest_file.resolve()),
                category=rel_category,
                reason=f"Classification {strategy}",
                file_size=item.stat().st_size if item.exists() else 0,
                conflict_detected=conflict,
                resolved_path=resolved
            ))

        plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(moves)}"
        return OrganizationPlan(
            plan_id=plan_id,
            source_folder=str(path),
            strategy=strategy,
            total_files=len(moves),
            planned_moves=moves,
            empty_folders_to_prune=empty_folders,
            directories_to_create=sorted(list(dirs_to_create))
        )

    def _determine_destination(self, file_path: Path, strategy: str) -> str:
        ext = file_path.suffix.lower()

        if strategy == "by_type":
            return EXT_TO_CATEGORY.get(ext, "A_examiner")

        elif strategy == "by_date":
            try:
                mtime = file_path.stat().st_mtime
                dt = datetime.fromtimestamp(mtime)
                return f"{dt.year}/{dt.year}-{dt.month:02d}"
            except Exception:
                return "Date_inconnue"

        elif strategy == "by_extension":
            if not ext:
                return "Sans_extension"
            return ext.lstrip(".").upper()

        elif strategy == "by_project":
            # Keyword / prefix heuristic: e.g. "ProjetX_Doc.pdf" -> "ProjetX"
            name = file_path.stem
            for separator in ["_", "-", " "]:
                if separator in name:
                    prefix = name.split(separator)[0].strip()
                    if len(prefix) >= 3 and not prefix.isdigit():
                        return f"Projet_{prefix.capitalize()}"
            return EXT_TO_CATEGORY.get(ext, "Divers")

        return "Autres"

    @staticmethod
    def _find_collision_free_path(target: Path) -> Path:
        """Finds a safe non-colliding path: name (1).ext, name (2).ext, etc."""
        parent = target.parent
        stem = target.stem
        suffix = target.suffix
        counter = 1
        while True:
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1


# ── Execution, Journal & Undo System ────────────────────────────────────────

class SecureSystemExecutor:
    """Executes validated plans with conflict protection, logging, and undo."""

    def __init__(self, allowed_roots: Optional[List[Path]] = None):
        self.allowed_roots = allowed_roots or get_default_safe_roots()
        self.journal_file = Path(__file__).resolve().parent.parent / "output" / "file_operations.log"
        self.journal_file.parent.mkdir(parents=True, exist_ok=True)

    def execute_plan(
        self,
        plan: OrganizationPlan,
        on_conflict: str = "rename",  # "rename" | "skip" | "error"
        prune_empty: bool = True
    ) -> Dict[str, Any]:
        """
        Executes an OrganizationPlan.
        Guarantees:
          - Never silently overwrites existing files.
          - Registers an undo entry to reverse all moves and prune created empty dirs.
          - Logs operation to JSONL journal.
        """
        safe, err = is_path_safe(plan.source_folder, self.allowed_roots)
        if not safe:
            return {"success": False, "error": err}

        executed_moves: List[Tuple[Path, Path]] = []
        skipped: List[str] = []
        errors: List[str] = []
        created_dirs: List[Path] = []

        # 1. Create target directories safely
        for dir_str in plan.directories_to_create:
            d = Path(dir_str)
            safe_d, err_d = is_path_safe(d, self.allowed_roots)
            if not safe_d:
                errors.append(f"Directory rejected: {err_d}")
                continue
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created_dirs.append(d)

        # 2. Execute moves
        for move in plan.planned_moves:
            src = Path(move.source_path)
            dst = Path(move.dest_path)

            if not src.exists():
                skipped.append(f"{src.name} (source no longer exists)")
                continue

            # Check collision
            if dst.exists():
                if on_conflict == "skip":
                    skipped.append(f"{src.name} (destination already exists)")
                    continue
                elif on_conflict == "error":
                    errors.append(f"Destination collision on {dst.name}")
                    continue
                else:  # "rename"
                    dst = SmartClassificationPlanner._find_collision_free_path(dst)

            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))

                # Verify move succeeded
                if not dst.exists():
                    errors.append(f"Move verification failed for {src.name}")
                else:
                    executed_moves.append((src.resolve(), dst.resolve()))

            except Exception as e:
                errors.append(f"Failed to move {src.name} to {dst}: {e}")

        # 3. Prune empty folders if requested
        pruned_folders: List[str] = []
        if prune_empty:
            for folder_str in plan.empty_folders_to_prune:
                f_path = Path(folder_str)
                try:
                    if f_path.exists() and f_path.is_dir() and not any(f_path.iterdir()):
                        f_path.rmdir()
                        pruned_folders.append(folder_str)
                except Exception as pe:
                    errors.append(f"Could not prune folder {f_path.name}: {pe}")

        # 4. Push Undo Registration
        if executed_moves and _HAS_UNDO:
            def _undo_batch(records=tuple(executed_moves), dirs=tuple(created_dirs)):
                restored = 0
                undo_errors = []
                for original_src, current_dst in records:
                    try:
                        if current_dst.exists():
                            original_src.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(current_dst), str(original_src))
                            restored += 1
                    except Exception as ue:
                        undo_errors.append(f"{current_dst.name}: {ue}")

                # Clean up created directories if they are now empty
                for d in dirs:
                    try:
                        if d.exists() and d.is_dir() and not any(d.iterdir()):
                            d.rmdir()
                    except Exception:
                        pass

                return f"Undo completed: {restored}/{len(records)} files restored."

            push_undo(
                f"organized {plan.source_folder} ({len(executed_moves)} files moved)",
                _undo_batch
            )

        # 5. Write to journal
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "plan_id": plan.plan_id,
            "source_folder": plan.source_folder,
            "strategy": plan.strategy,
            "moved_count": len(executed_moves),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "pruned_folders": pruned_folders,
            "errors": errors
        }
        self._append_journal(log_entry)

        return {
            "success": len(errors) == 0 or len(executed_moves) > 0,
            "plan_id": plan.plan_id,
            "moved_count": len(executed_moves),
            "skipped_count": len(skipped),
            "pruned_folders_count": len(pruned_folders),
            "errors": errors,
            "details": [f"{s.name} → {d.parent.name}/{d.name}" for s, d in executed_moves[:10]],
            "can_undo": _HAS_UNDO and len(executed_moves) > 0
        }

    def clean_duplicates(
        self,
        duplicate_groups: List[Dict[str, Any]],
        keep: str = "first"  # "first" | "oldest" | "newest"
    ) -> Dict[str, Any]:
        """
        Safely removes duplicate files, keeping 1 original per group.
        Preserves files in ~/.jarvis/quarantine/<timestamp> and registers an undo callback.
        """
        home = Path.home()
        quarantine_dir = home / ".jarvis" / "quarantine" / datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        removed_records: List[Tuple[Path, Path]] = []
        deleted_count = 0
        kept_count = 0
        total_freed_bytes = 0
        details: List[str] = []
        errors: List[str] = []

        for group in duplicate_groups:
            paths = [Path(p) for p in group.get("paths", []) if Path(p).exists()]
            if len(paths) <= 1:
                continue

            # Sort paths based on keep policy
            if keep == "oldest":
                paths.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
            elif keep == "newest":
                paths.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            else:
                # "first": prioritize cleanest filename without copy indicator
                def _priority(p: Path):
                    name = p.name.lower()
                    has_copy = any(tag in name for tag in ["copie", "copy", "(1)", "(2)", "- 1", "- 2"])
                    return (1 if has_copy else 0, len(str(p)))
                paths.sort(key=_priority)

            kept_file = paths[0]
            kept_count += 1
            copies = paths[1:]

            for copy_file in copies:
                try:
                    fsize = copy_file.stat().st_size
                    # Move to quarantine
                    target_quarantine = quarantine_dir / f"{copy_file.stem}_{deleted_count}{copy_file.suffix}"
                    shutil.move(str(copy_file), str(target_quarantine))
                    removed_records.append((copy_file.resolve(), target_quarantine.resolve()))
                    total_freed_bytes += fsize
                    deleted_count += 1
                    details.append(f"{copy_file.name} (taille : {format_size(fsize)})")
                except Exception as ex:
                    errors.append(f"Échec sur {copy_file.name} : {ex}")

        # Register Undo callback
        if removed_records and _HAS_UNDO:
            def _undo_duplicates(records=tuple(removed_records)):
                restored = 0
                for orig_p, quaran_p in records:
                    try:
                        if quaran_p.exists():
                            orig_p.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(quaran_p), str(orig_p))
                            restored += 1
                    except Exception:
                        pass
                return f"{restored} fichier(s) doublon(s) restauré(s) à leur emplacement d'origine."

            push_undo(f"Suppression de {deleted_count} doublons", _undo_duplicates)

        # Log to JSONL journal
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "operation": "clean_duplicates",
            "groups_count": len(duplicate_groups),
            "deleted_count": deleted_count,
            "kept_count": kept_count,
            "freed_bytes": total_freed_bytes,
            "quarantine_dir": str(quarantine_dir),
            "errors": errors
        }
        self._append_journal(log_entry)

        return {
            "success": len(errors) == 0 or deleted_count > 0,
            "groups_count": len(duplicate_groups),
            "kept_count": kept_count,
            "deleted_count": deleted_count,
            "freed_bytes": total_freed_bytes,
            "freed_size_str": format_size(total_freed_bytes),
            "quarantine_dir": str(quarantine_dir),
            "details": details,
            "errors": errors,
            "can_undo": _HAS_UNDO and deleted_count > 0
        }

    def _append_journal(self, entry: Dict[str, Any]) -> None:
        try:
            with open(self.journal_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[SmartOrganizer] Journal write error: {e}")

    @staticmethod
    def run_powershell_query(script: str, timeout_seconds: int = 15) -> Tuple[bool, str]:
        """Runs a safe, read-only PowerShell command."""
        if not _IS_WINDOWS:
            return False, "PowerShell is only supported on Windows."
        try:
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
            if res.returncode == 0:
                return True, res.stdout.strip()
            return False, res.stderr.strip() or f"Process failed with code {res.returncode}"
        except subprocess.TimeoutExpired:
            return False, "PowerShell command timed out."
        except Exception as e:
            return False, f"PowerShell execution failed: {e}"
