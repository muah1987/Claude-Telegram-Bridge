# Claude Telegram Bridge 🤖📱

Volledige integratie van **Claude Code CLI** met **Telegram** — elk bericht, elke tool-aanroep en elke sessie wordt gesynchroniseerd.

---

## Architectuur

```
┌──────────────────────────────────────────────────────────┐
│                     Jouw Terminal                        │
│  claude-tg  ──────►  claude (sub-agent, non-interactive) │
│      │                     │                             │
│      │              hooks (pre/post/stop)                │
└──────┼─────────────────────┼─────────────────────────────┘
       │                     │
       ▼                     ▼
┌──────────────────────────────────────────────────────────┐
│              Claude Telegram Daemon                      │
│                                                          │
│  SessionStore (SQLite)  ◄──►  IPC Socket                │
│  TelegramBridge (bot)   ◄──►  ClaudeRunner (--print)    │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
                    📱 Telegram Bot
                    (jij, op je telefoon)
```

### Sub-agent model
- Elke sessie spawnt een **niet-interactief** `claude --print` sub-process
- Eigen context window per sessie
- Sessie-continuïteit via `--resume <claude_session_id>`
- Hooks zorgen dat de hoofd-sessie (terminal) en Telegram altijd in sync zijn

---

## Installatie

### Vereisten
- Python 3.10+
- [Claude Code CLI](https://claude.ai/code) geïnstalleerd
- Telegram account

### Stap 1 — Telegram Bot aanmaken
1. Open Telegram → zoek **@BotFather**
2. Stuur `/newbot` → volg de stappen
3. Kopieer je **Bot Token** (bijv. `123456:ABCdef...`)
4. Zoek **@userinfobot** → stuur `/start` → kopieer je **User ID**

### Stap 2 — Installeren
```bash
git clone <repo>
cd claude-telegram
chmod +x install.sh
./install.sh
```

### Stap 3 — Configureren
```bash
claude-tg --setup
# Bot Token: 123456:ABCdef...
# Jouw Telegram User ID: 987654321
```

### Stap 4 — Daemon starten
```bash
claude-tg --daemon
```

### Stap 5 — Eerste sessie
```bash
cd ~/mijn-project
claude-tg
```

📱 Je krijgt een Telegram-notificatie met de sessie-ID.

---

## Gebruik

### Basis commando's

| Commando | Beschrijving |
|----------|-------------|
| `claude-tg` | Nieuwe sessie in huidige map |
| `claude-tg --resume abc12345` | Sessie hervatten |
| `claude-tg --alias mijnproject` | Sessie met alias |
| `claude-tg --daemon` | Daemon starten/herstarten |
| `claude-tg --status` | Daemon + sessie status |
| `claude-tg --stop-daemon` | Daemon stoppen |
| `claude-tg -- --dangerously-skip-permissions` | Extra claude args |

### In Telegram

| Wat je stuurt | Resultaat |
|--------------|-----------|
| `Hallo, maak een README` | Reageert op meest recente sessie |
| `@abc12345 refactor dit` | Reageert op specifieke sessie |
| `@mijnproject fix de bug` | Reageert via alias |
| `/sessions` | Lijst actieve sessies |
| `/close abc12345` | Sessie sluiten |
| `/help` | Uitleg |

### Meerdere sessies tegelijk
```
Terminal 1 (project-a):  claude-tg --alias frontend
Terminal 2 (project-b):  claude-tg --alias backend

In Telegram:
  @frontend Voeg een login form toe
  @backend  Maak een auth endpoint
```

---

## Telegram notificaties

Wat je te zien krijgt in Telegram:

```
🟢 Nieuwe Claude Code sessie gestart
📁 /Users/jij/mijn-project
🔑 Sessie ID: a1b2c3d4
💬 Stuur berichten vrij of prefix met @a1b2c3d4 bij meerdere sessies.

🔧 [a1b2c3d4] Tool wordt uitgevoerd: Read
   {"file_path": "src/main.py"}

✅ [a1b2c3d4] `Read` klaar
   def hello_world():
       print("Hello!")

📢 [a1b2c3d4] Claude wil bestand schrijven: src/utils.py

🤖 [a1b2c3d4]
   Ik heb src/utils.py aangemaakt met 3 hulpfuncties...

🔴 Sessie a1b2c3d4 beëindigd
📁 /Users/jij/mijn-project
```

---

## Memory synchronisatie

Claude Code CLI gebruikt `CLAUDE.md` voor persistent memory. Dit werkt automatisch omdat:
- Terminal-sessies schrijven naar `~/.claude/CLAUDE.md` (globaal) of `./CLAUDE.md` (project)
- Telegram sub-agent sessies starten in dezelfde `project_dir` en lezen dezelfde `CLAUDE.md`
- Memory is dus identiek tussen terminal en Telegram

**Tip:** Gebruik project-specifieke CLAUDE.md voor per-project context:
```bash
echo "# Project X\nGebruik altijd TypeScript strict mode" > ./CLAUDE.md
```

---

## Sessie resume

Wanneer je een terminal-sessie stopzet en later wil hervatten:

```bash
# Bekijk sessie-ID's
claude-tg --status

# Hervatten
claude-tg --resume a1b2c3d4
```

Of vanuit Telegram:
- Stuur berichten naar `@a1b2c3d4` — de daemon reageert ook als de terminal-sessie gesloten is

---

## Automatisch starten

### macOS
LaunchAgent wordt automatisch geïnstalleerd door `install.sh`.

### Linux (systemd)
```bash
systemctl --user start claude-telegram
systemctl --user status claude-telegram
journalctl --user -u claude-telegram -f
```

### Shell alias (optioneel)
```bash
# ~/.bashrc of ~/.zshrc
alias claude="claude-tg"
```

---

## Troubleshooting

**Daemon start niet:**
```bash
cat ~/.claude-telegram/daemon.log
```

**Telegram bot reageert niet:**
```bash
claude-tg --status
claude-tg --daemon  # herstart
```

**Hooks werken niet:**
```bash
cat ~/.claude/settings.json  # check hooks aanwezig zijn
claude-tg --setup            # herinstalleer hooks
```

**Sessie niet gevonden:**
```bash
claude-tg --list             # alle sessies
/sessions                    # in Telegram
```

---

## Bestandsstructuur

```
~/.claude-telegram/
├── config.json          # Bot token + user ID
├── sessions.db          # SQLite: alle sessies + berichten
├── daemon.py            # Hoofd daemon
├── daemon.log           # Logs
├── daemon.pid           # PID van actieve daemon
├── daemon.sock          # Unix socket (IPC)
└── hooks/
    ├── pre_tool.py      # Notificatie vóór tool
    ├── post_tool.py     # Notificatie ná tool
    ├── stop.py          # Sessie-einde
    └── notification.py  # Claude notificaties

~/.claude/
└── settings.json        # Hooks geregistreerd hier
```
