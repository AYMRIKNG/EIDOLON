# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build of EIDOLON — run packaging/build.ps1 rather than this file.

Output: dist/EIDOLON/
    EIDOLON.exe          the assistant (no console)
    eidolon-hook.exe     tiny console helper Claude Code calls (IDE bridge)
    actions/ plugins/ …  data the app reads from disk at runtime

`contents_directory="."` keeps every bundled file next to the exe instead of an
_internal/ folder, so the paths the code computes from __file__ and from
sys.executable point to the same place — the install folder. The installer puts
that folder in the user's profile, where the app may write its config.

Never bundled: config/api_keys.json (your key), config/certs, memory/*.json,
output/, logs/ — they are created on the user's PC at first launch.
"""
import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))

# Heavy optional extras the source version may have installed: the app runs
# without them (each one is imported inside a try), and together they weigh
# more than a gigabyte.
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "transformers", "kokoro", "misaki", "spacy",
    "tensorflow", "keras", "jax", "mediapipe", "matplotlib", "IPython", "jupyter",
    "notebook", "pytest", "sphinx", "PyInstaller", "numpy.tests",
    # optional too, and large: spreadsheet analysis and the source-only speech stacks
    "pandas", "pyarrow", "narwhals", "speechbrain", "faster_whisper", "ctranslate2",
    "sympy", "numba", "llvmlite", "fsspec", "gi",
]

# Folders whose .py files the app loads from disk (discover_actions /
# discover_plugins): they ship as files, and what they import is collected here
# because PyInstaller never sees those imports otherwise.
SOURCE_DIRS = ["actions", "plugins", "automation", "core", "memory", "dashboard",
               "config", "integrations"]
LOCAL_TOP = {"actions", "plugins", "automation", "core", "memory", "dashboard", "config",
             "integrations", "main", "ui", "eidolon_launcher", "agents", "workflows"}


def third_party_imports():
    found = set()
    for d in SOURCE_DIRS:
        for f in (ROOT / d).rglob("*.py"):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module]
                else:
                    continue
                for n in names:
                    top = n.split(".")[0]
                    if top in LOCAL_TOP or top in EXCLUDES or top in sys.stdlib_module_names:
                        continue
                    try:
                        if importlib.util.find_spec(top) is None:
                            continue
                        found.add(top)
                        if "." in n and importlib.util.find_spec(n) is not None:
                            found.add(n)
                    except Exception:
                        pass
    return sorted(found)


def tree(folder, pattern="*", dest=None):
    """(src, dest) pairs for every file under ROOT/folder matching pattern."""
    out = []
    for f in (ROOT / folder).rglob(pattern):
        if f.is_file() and "__pycache__" not in f.parts:
            out.append((str(f), str(Path(dest or folder) / f.relative_to(ROOT / folder).parent)))
    return out


datas = (
    tree("actions", "*.py")
    + tree("plugins", "*.py")
    + tree("workflows", "*.json")
    + tree("dashboard/static")
    + tree("integrations", "*.py")
    + [
        (str(ROOT / "core" / "prompt.txt"), "core"),
        (str(ROOT / "core" / "face_model.obj"), "core"),
        (str(ROOT / "config" / "jarvis.ico"), "config"),
        (str(ROOT / "agents" / "registry.json"), "agents"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "README.md"), "."),
    ]
)

hiddenimports = third_party_imports() + [
    "core", "memory", "config", "dashboard", "automation", "plugins",
    "PyQt6.QtSvg", "PyQt6.sip",
]

# ── EIDOLON.exe ──────────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "eidolon_launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="EIDOLON",
    icon=str(ROOT / "config" / "jarvis.ico"),
    console=False,
    contents_directory=".",
    version=str(ROOT / "packaging" / "version_info.txt"),
)

# ── eidolon-hook.exe (stdlib only, starts in a fraction of a second) ────────
h = Analysis(
    [str(ROOT / "integrations" / "claude_code" / "eidolon_hook.py")],
    pathex=[str(ROOT)],
    excludes=EXCLUDES + ["PyQt6", "numpy", "google", "cv2", "PIL"],
)
hook_pyz = PYZ(h.pure)
hook_exe = EXE(
    hook_pyz, h.scripts, [],
    exclude_binaries=True,
    name="eidolon-hook",
    console=True,
    contents_directory=".",
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    hook_exe, h.binaries, h.datas,
    name="EIDOLON",
)
