"""
EIDOLON — entry point of the packaged app (EIDOLON.exe).

Running from source you still start `python main.py`; this file only matters
once PyInstaller has frozen the app, where three things differ:

  * there is no console: stdout / stderr go to logs/eidolon.log next to the
    app, so a problem on another PC can be read back;
  * EIDOLON.exe is also `sys.executable`. Reminders and scheduled game updates
    re-launch it with a .py script (as they would launch python.exe): that
    script is run here instead of starting a second assistant;
  * `-m pip` and friends cannot work in a frozen app: say so and exit, so the
    caller reports a clean failure instead of opening another HUD.
"""
import os
import runpy
import sys
from pathlib import Path


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _redirect_output() -> None:
    logs = _app_dir() / "logs"
    try:
        logs.mkdir(exist_ok=True)
        log = logs / "eidolon.log"
        if log.exists() and log.stat().st_size > 2_000_000:   # keep one previous run
            log.replace(logs / "eidolon.previous.log")
        stream = open(log, "a", encoding="utf-8", buffering=1, errors="replace")
        sys.stdout = sys.stderr = stream
    except OSError:
        pass


def main() -> None:
    args = sys.argv[1:]
    frozen = getattr(sys, "frozen", False)

    if frozen and args and args[0] == "-m":
        print(f"EIDOLON: « python -m {' '.join(args[1:2])} » n'est pas disponible dans "
              f"l'application installée.", file=sys.stderr)
        sys.exit(1)

    if frozen and args and args[0].lower().endswith(".py") and Path(args[0]).is_file():
        script = args[0]
        sys.argv = [script] + args[1:]
        sys.path.insert(0, str(Path(script).resolve().parent))
        runpy.run_path(script, run_name="__main__")
        return

    if frozen and (sys.stdout is None or not sys.stdout.isatty()):
        _redirect_output()
    os.chdir(_app_dir())      # relative paths in older modules resolve against the app

    import main as eidolon
    eidolon.main()


if __name__ == "__main__":
    main()
