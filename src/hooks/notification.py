#!/usr/bin/env python3
"""
Claude Code Hook: Notification
Forwards Claude Code notifications (e.g. permission requests) to Telegram.
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
        pass


def main():
    session_id = os.environ.get(SESSION_ENV)
    if not session_id:
        sys.exit(0)

    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    message = data.get("message", data.get("notification", str(data)))

    send_ipc({
        "type":       "notification",
        "session_id": session_id,
        "message":    message,
    })

    sys.exit(0)


if __name__ == "__main__":
    main()
