#!/usr/bin/env python3
"""
Claude Code Hook: PostToolUse
Sends tool output to Telegram daemon via IPC socket.
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
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    session_id = os.environ.get(SESSION_ENV)
    if not session_id:
        sys.exit(0)

    tool_name = data.get("tool_name", data.get("tool", "unknown"))

    # Extract output - varies by tool
    output = data.get("tool_result", data.get("output", ""))
    if isinstance(output, dict):
        # Some tools return structured output
        output = json.dumps(output, ensure_ascii=False, indent=2)
    elif isinstance(output, list):
        parts = []
        for item in output:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        output = "\n".join(parts)
    output = str(output)

    send_ipc({
        "type":       "post_tool",
        "session_id": session_id,
        "tool":       tool_name,
        "output":     output[:2000],  # cap at 2k chars
    })

    sys.exit(0)


if __name__ == "__main__":
    main()
