"""
Claude Code hook → EIDOLON review window.

Installed in ~/.claude/settings.json from EIDOLON (HUD → term → ⋯ → « Relier
Claude Code »). Claude Code runs it with the hook event as JSON on stdin.

    PreToolUse on Edit / Write / MultiEdit, and on risky Bash commands
        the proposed change (or command) is shown in EIDOLON and this script
        waits for your answer: accept → it goes through, refuse → Claude is told why.
    every other event
        reported to EIDOLON so it can show what the agent is doing; answered at once.

EIDOLON not running, or any error: the script prints nothing and exits 0, which
leaves Claude Code exactly as it is without the hook. Standard library only.
"""
import json
import os
import sys

PORT = int(os.environ.get("EIDOLON_REVIEW_PORT") or os.environ.get("JARVIS_REVIEW_PORT") or "47321")
URL = f"http://127.0.0.1:{PORT}/hook"
RUN_MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", f"ide_bridge.{PORT}.running")
# Calls that may wait for your answer (EIDOLON answers at once for safe commands).
BLOCKING_TOOLS = {"Edit", "Write", "MultiEdit", "Bash"}


def main() -> None:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except Exception:
        return
    # EIDOLON drops this marker while it runs. Without it there is nothing to
    # ask — return at once instead of waiting on a refused connection (~1-2 s
    # on Windows), so every tool call stays fast while EIDOLON is closed.
    if not os.path.exists(RUN_MARKER):
        return
    import socket
    import urllib.request
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.25).close()
    except OSError:
        return
    payload["_source"] = "Claude Code"
    blocking = payload.get("hook_event_name") == "PreToolUse" and payload.get("tool_name") in BLOCKING_TOOLS

    req = urllib.request.Request(URL, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=595 if blocking else 2) as resp:
            answer = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception:
        return  # EIDOLON absent — stay out of the way

    decision = answer.get("decision")
    if blocking and decision in ("allow", "deny", "ask"):
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": answer.get("reason", ""),
            }
        }))


if __name__ == "__main__":
    main()
