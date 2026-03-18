#!/usr/bin/env python3
"""
Claude Telegram Bridge - Main Daemon
Runs as a background service, manages Telegram bot and session routing.
"""

import asyncio
import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# ── Logging ──────────────────────────────────────────────────────────────────
CLAUDE_TG_DIR = Path.home() / ".claude-telegram"
CLAUDE_TG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(CLAUDE_TG_DIR / "daemon.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ctg-daemon")

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH     = CLAUDE_TG_DIR / "sessions.db"
SOCKET_PATH = CLAUDE_TG_DIR / "daemon.sock"
CONFIG_PATH = CLAUDE_TG_DIR / "config.json"
PID_PATH    = CLAUDE_TG_DIR / "daemon.pid"


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION STORE
# ═══════════════════════════════════════════════════════════════════════════════
class SessionStore:
    def __init__(self):
        self.db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id              TEXT PRIMARY KEY,
                alias           TEXT,
                project_dir     TEXT NOT NULL,
                chat_id         INTEGER NOT NULL,
                claude_sess_id  TEXT,
                status          TEXT DEFAULT 'active',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                last_tool       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_chat   ON sessions(chat_id);
            CREATE INDEX IF NOT EXISTS idx_status ON sessions(status);

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                ts         TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.db.commit()

    def create(self, project_dir: str, chat_id: int, alias: str = None) -> str:
        sid = str(uuid.uuid4())[:8]
        self.db.execute(
            "INSERT INTO sessions (id,alias,project_dir,chat_id) VALUES (?,?,?,?)",
            (sid, alias, project_dir, chat_id),
        )
        self.db.commit()
        return sid

    def get(self, sid: str) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def active_for_chat(self, chat_id: int) -> List[dict]:
        rows = self.db.execute(
            "SELECT * FROM sessions WHERE chat_id=? AND status='active' ORDER BY updated_at DESC",
            (chat_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update(self, sid: str, **kw):
        kw["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE sessions SET {sets} WHERE id=?", [*kw.values(), sid])
        self.db.commit()

    def close(self, sid: str):
        self.update(sid, status="closed")

    def all_active(self) -> List[dict]:
        rows = self.db.execute("SELECT * FROM sessions WHERE status='active'").fetchall()
        return [dict(r) for r in rows]

    def log_msg(self, sid: str, role: str, content: str):
        self.db.execute(
            "INSERT INTO messages (session_id,role,content) VALUES (?,?,?)",
            (sid, role, content),
        )
        self.db.commit()

    def get_session_by_alias(self, chat_id: int, alias: str) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM sessions WHERE chat_id=? AND alias=? AND status='active'",
            (chat_id, alias),
        ).fetchone()
        return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════════
# CLAUDE RUNNER  (sub-agent: non-interactive, own context window)
# ═══════════════════════════════════════════════════════════════════════════════
class ClaudeRunner:
    """
    Spawns `claude --print` as a non-interactive sub-agent.
    Maintains session continuity via --resume <claude_sess_id>.
    Output is streamed back to the caller.
    """

    async def run(
        self,
        message: str,
        project_dir: str,
        claude_sess_id: Optional[str] = None,
        timeout: int = 300,
    ) -> tuple[str, Optional[str]]:
        """
        Returns (output_text, new_claude_session_id).
        """
        cmd = ["claude", "--print", "--output-format", "json"]
        if claude_sess_id:
            cmd += ["--resume", claude_sess_id]

        env = {**os.environ, "CLAUDE_TG_ACTIVE": "1"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_dir,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=message.encode()), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return "⏱ Timeout: Claude took too long to respond.", claude_sess_id
        except FileNotFoundError:
            return "❌ `claude` CLI not found. Install Claude Code CLI first.", None

        out_text = stdout.decode(errors="replace").strip()
        err_text = stderr.decode(errors="replace").strip()

        # Try to parse JSON output for session id + text
        new_sess_id = claude_sess_id
        display_text = out_text

        try:
            data = json.loads(out_text)
            # Claude Code JSON output format
            new_sess_id = data.get("session_id", claude_sess_id)
            # Collect all text result blocks
            parts = []
            for block in data.get("result", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            if parts:
                display_text = "\n".join(parts)
            else:
                display_text = data.get("result", out_text) if isinstance(data.get("result"), str) else out_text
        except (json.JSONDecodeError, AttributeError):
            # Plain text output fallback
            display_text = out_text or err_text or "(no output)"

        return display_text, new_sess_id


# ═══════════════════════════════════════════════════════════════════════════════
# UNIX SOCKET IPC  (hooks & wrapper talk to daemon here)
# ═══════════════════════════════════════════════════════════════════════════════
class IPCServer:
    """
    Hook scripts and the claude-tg wrapper send events over this socket.
    Protocol: newline-delimited JSON.
    """

    def __init__(self, store: SessionStore, bot_send_fn):
        self.store = store
        self.bot_send = bot_send_fn  # async fn(chat_id, text)

    async def start(self):
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        server = await asyncio.start_unix_server(self._handle, path=str(SOCKET_PATH))
        SOCKET_PATH.chmod(0o600)
        log.info(f"IPC socket: {SOCKET_PATH}")
        async with server:
            await server.serve_forever()

    async def _handle(self, reader, writer):
        try:
            data = await reader.read(65536)
            msg = json.loads(data.decode())
            await self._dispatch(msg, writer)
        except Exception as e:
            log.error(f"IPC error: {e}")
        finally:
            writer.close()

    async def _dispatch(self, msg: dict, writer):
        kind = msg.get("type")
        sid  = msg.get("session_id")
        sess = self.store.get(sid) if sid else None

        # ── Session lifecycle ──────────────────────────────────────────────
        if kind == "session_start":
            project_dir = msg["project_dir"]
            chat_id     = msg["chat_id"]
            alias       = msg.get("alias")
            new_sid     = self.store.create(project_dir, chat_id, alias)
            writer.write(json.dumps({"session_id": new_sid}).encode())
            label = f"`{alias}`" if alias else f"`{new_sid}`"
            await self.bot_send(
                chat_id,
                f"🟢 *Nieuwe Claude Code sessie gestart*\n"
                f"📁 `{project_dir}`\n"
                f"🔑 Sessie ID: `{new_sid}`\n"
                f"💬 Stuur berichten vrij of prefix met `@{new_sid}` bij meerdere sessies.",
            )
            log.info(f"Session started: {new_sid} in {project_dir}")

        elif kind == "session_resume":
            if sess:
                self.store.update(sid, status="active")
                await self.bot_send(
                    sess["chat_id"],
                    f"🔄 *Sessie hervat*: `{sid}`\n📁 `{sess['project_dir']}`",
                )

        elif kind == "session_stop":
            if sess:
                self.store.close(sid)
                await self.bot_send(
                    sess["chat_id"],
                    f"🔴 *Sessie beëindigd*: `{sid}`\n📁 `{sess['project_dir']}`",
                )

        # ── Hook events ────────────────────────────────────────────────────
        elif kind == "pre_tool":
            if sess:
                tool  = msg.get("tool", "?")
                input_= msg.get("input", {})
                text  = f"🔧 *[{sid}] Tool wordt uitgevoerd:* `{tool}`"
                if input_:
                    snippet = json.dumps(input_, ensure_ascii=False)[:400]
                    text += f"\n```\n{snippet}\n```"
                await self.bot_send(sess["chat_id"], text)
                self.store.update(sid, last_tool=tool)

        elif kind == "post_tool":
            if sess:
                tool   = msg.get("tool", "?")
                output = msg.get("output", "")
                # Truncate long output
                if len(output) > 800:
                    output = output[:800] + "\n…(ingekort)"
                await self.bot_send(
                    sess["chat_id"],
                    f"✅ *[{sid}] `{tool}` klaar*\n```\n{output}\n```",
                )

        elif kind == "notification":
            if sess:
                await self.bot_send(
                    sess["chat_id"],
                    f"📢 *[{sid}]* {msg.get('message', '')}",
                )

        elif kind == "agent_response":
            # Full assistant response forwarded from hook
            if sess:
                content = msg.get("content", "")
                await self.bot_send(sess["chat_id"], f"🤖 *[{sid}]*\n{content}")

        writer.write(b"ok")
        await writer.drain()


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM BOT
# ═══════════════════════════════════════════════════════════════════════════════
class TelegramBridge:
    def __init__(self, token: str, allowed_user_id: int, store: SessionStore):
        from telegram import Bot, Update
        from telegram.ext import (
            Application, CommandHandler, MessageHandler,
            filters, ContextTypes,
        )
        self.token   = token
        self.uid     = allowed_user_id
        self.store   = store
        self.runner  = ClaudeRunner()
        self.app     = Application.builder().token(token).build()
        self._running_tasks: Dict[str, asyncio.Task] = {}

        # Register handlers
        self.app.add_handler(CommandHandler("start",    self._cmd_start))
        self.app.add_handler(CommandHandler("sessions", self._cmd_sessions))
        self.app.add_handler(CommandHandler("close",    self._cmd_close))
        self.app.add_handler(CommandHandler("help",     self._cmd_help))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )

    # ── Public send function (used by IPC) ────────────────────────────────
    async def send(self, chat_id: int, text: str):
        try:
            # Telegram has 4096 char limit per message
            for chunk in self._split(text):
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="Markdown",
                )
        except Exception as e:
            log.warning(f"Telegram send failed: {e}")
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=text[:4000])
            except Exception:
                pass

    @staticmethod
    def _split(text: str, limit: int = 3800) -> List[str]:
        if len(text) <= limit:
            return [text]
        chunks = []
        while text:
            chunks.append(text[:limit])
            text = text[limit:]
        return chunks

    def _auth(self, update) -> bool:
        return update.effective_user.id == self.uid

    # ── Commands ──────────────────────────────────────────────────────────
    async def _cmd_start(self, update, ctx):
        if not self._auth(update):
            return
        await update.message.reply_text(
            "👋 *Claude Telegram Bridge*\n\n"
            "Stuur een bericht om met de actieve sessie te chatten.\n"
            "Bij meerdere sessies: prefix met `@sessie-id bericht`\n\n"
            "/sessions — toon actieve sessies\n"
            "/close <id> — sluit een sessie\n"
            "/help — uitleg",
            parse_mode="Markdown",
        )

    async def _cmd_sessions(self, update, ctx):
        if not self._auth(update):
            return
        chat_id  = update.effective_chat.id
        sessions = self.store.active_for_chat(chat_id)
        if not sessions:
            await update.message.reply_text("Geen actieve sessies.")
            return
        lines = ["*Actieve sessies:*\n"]
        for s in sessions:
            alias = f" ({s['alias']})" if s.get("alias") else ""
            lines.append(
                f"🔑 `{s['id']}`{alias}\n"
                f"   📁 `{s['project_dir']}`\n"
                f"   🕒 {s['updated_at'][:16]}\n"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_close(self, update, ctx):
        if not self._auth(update):
            return
        if not ctx.args:
            await update.message.reply_text("Gebruik: /close <session-id>")
            return
        sid = ctx.args[0]
        sess = self.store.get(sid)
        if not sess:
            await update.message.reply_text(f"Sessie `{sid}` niet gevonden.", parse_mode="Markdown")
            return
        self.store.close(sid)
        await update.message.reply_text(f"🔴 Sessie `{sid}` gesloten.", parse_mode="Markdown")

    async def _cmd_help(self, update, ctx):
        if not self._auth(update):
            return
        await update.message.reply_text(
            "*Claude Telegram Bridge — Help*\n\n"
            "*Berichten sturen:*\n"
            "• Enkel sessie: stuur gewoon een bericht\n"
            "• Meerdere sessies: `@abc12345 jouw vraag`\n"
            "• Alias gebruiken: `@mijnproject jouw vraag`\n\n"
            "*Commando's:*\n"
            "/sessions — lijst actieve sessies\n"
            "/close <id> — sluit sessie\n\n"
            "*Sessie starten (terminal):*\n"
            "`claude-tg` — start sessie + koppel Telegram\n"
            "`claude-tg --resume <id>` — hervatten\n\n"
            "*Memory:* wordt gesynchroniseerd via CLAUDE.md",
            parse_mode="Markdown",
        )

    # ── Incoming message ──────────────────────────────────────────────────
    async def _on_message(self, update, ctx):
        if not self._auth(update):
            return
        chat_id  = update.effective_chat.id
        raw_text = update.message.text.strip()

        # Determine target session
        sid, text = self._parse_session_prefix(raw_text, chat_id)
        if sid is None:
            await update.message.reply_text(
                "⚠️ Geen actieve sessie gevonden.\n"
                "Start een sessie met `claude-tg` in de terminal.",
                parse_mode="Markdown",
            )
            return

        sess = self.store.get(sid)
        if not sess:
            await update.message.reply_text(f"❌ Sessie `{sid}` niet gevonden.", parse_mode="Markdown")
            return

        # Cancel previous running task for this session if any
        if sid in self._running_tasks and not self._running_tasks[sid].done():
            self._running_tasks[sid].cancel()

        # Log & run
        self.store.log_msg(sid, "user", text)
        await update.message.reply_text(f"⏳ *[{sid}]* Bezig…", parse_mode="Markdown")

        task = asyncio.create_task(
            self._run_and_reply(sid, sess, text, update)
        )
        self._running_tasks[sid] = task

    async def _run_and_reply(self, sid: str, sess: dict, text: str, update):
        output, new_claude_sid = await self.runner.run(
            message=text,
            project_dir=sess["project_dir"],
            claude_sess_id=sess.get("claude_sess_id"),
        )
        # Persist updated claude session id
        if new_claude_sid and new_claude_sid != sess.get("claude_sess_id"):
            self.store.update(sid, claude_sess_id=new_claude_sid)

        self.store.log_msg(sid, "assistant", output)
        await self.send(update.effective_chat.id, f"🤖 *[{sid}]*\n{output}")

    def _parse_session_prefix(self, text: str, chat_id: int) -> tuple[Optional[str], str]:
        """
        Supports:
          @abc12345 message   → target by session id
          @mijnproject msg    → target by alias
          plain message       → pick most-recent active session
        """
        match = re.match(r"^@(\S+)\s+(.*)", text, re.DOTALL)
        if match:
            ref, msg = match.group(1), match.group(2)
            # Try direct id
            sess = self.store.get(ref)
            if not sess:
                sess = self.store.get_session_by_alias(chat_id, ref)
            if sess:
                return sess["id"], msg

        # Fall back to most recent active session
        sessions = self.store.active_for_chat(chat_id)
        if sessions:
            return sessions[0]["id"], text
        return None, text

    # ── Run ───────────────────────────────────────────────────────────────
    async def run(self):
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram bot polling started.")
        try:
            await asyncio.Event().wait()  # run forever
        finally:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
async def main():
    # Load config
    if not CONFIG_PATH.exists():
        print(f"Config not found: {CONFIG_PATH}")
        print("Run: claude-tg --setup")
        sys.exit(1)

    cfg = json.loads(CONFIG_PATH.read_text())
    token   = cfg["telegram_token"]
    user_id = cfg["telegram_user_id"]

    store  = SessionStore()
    bridge = TelegramBridge(token, user_id, store)
    ipc    = IPCServer(store, bridge.send)

    # Write PID
    PID_PATH.write_text(str(os.getpid()))

    # Handle shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: loop.stop())

    log.info("Claude Telegram Bridge daemon starting…")
    await asyncio.gather(
        bridge.run(),
        ipc.start(),
    )


if __name__ == "__main__":
    asyncio.run(main())
