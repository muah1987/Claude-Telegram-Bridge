#!/usr/bin/env python3
"""
Claude Code Hook: PreToolUse
Sends tool-use notifications to the Telegram daemon via IPC socket.

Place in ~/.claude/settings.json under hooks.PreToolUse
"""
import json
import os
import socket
import sys
from pathlib import Path

SOCKET_PATH = Path.home() / ".claude-telegram" / "daemon.sock"
SESSION_ENV  = "CLAUDE_TG_SESSION_ID"


def send_ipc(payload: dict) -> None:
    if not SOCKET_PATH.exists():
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(SOCKET_PATH))
            s.sendall(json.dumps(payload).encode())
    except Exception:
        pass  # Never block Claude Code execution


def main():
    # Claude Code passes hook data via stdin as JSON
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    session_id = os.environ.get(SESSION_ENV)
    if not session_id:
        sys.exit(0)

    tool_name  = data.get("tool_name", data.get("tool", "unknown"))
    tool_input = data.get("tool_input", data.get("input", {}))

    send_ipc({
        "type":       "pre_tool",
        "session_id": session_id,
        "tool":       tool_name,
        "input":      tool_input,
    })

    # Return empty = allow tool use to proceed
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    main()
