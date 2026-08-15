# Экранчик

Claude Code usage + date/USD-RUB/weather on a small USB LCD (AX206, VID
1908:0102), driven directly from macOS over libusb — no Windows, no VM, no
AIDA64.

Two screens, alternating every 10s:

1. **Usage** — clock (edge-to-edge), then 5-hour and weekly Claude token
   budget remaining (`h` / `w`), with reset countdowns. Exact numbers come
   from the same OAuth usage endpoint the `/usage` command uses (token read
   from macOS Keychain, `Claude Code-credentials`); falls back to a rough
   estimate from local `~/.claude/projects/*.jsonl` logs if the API/keychain
   is unavailable.
2. **Info** — date, USD/RUB rate (cbr-xml-daily.ru), tomorrow's weather for
   Moscow (Open-Meteo).

## Files

- `ax206.py` — USB Mass-Storage-BOT driver for the display (vendored from
  [sunzhengya/ax206-usb-display-macos](https://github.com/sunzhengya/ax206-usb-display-macos),
  GPL-3.0). Only the `BLIT` command works on this firmware — anything else
  (INQUIRY, GETLCD, SETPROPERTY) wedges the USB endpoint and needs a
  physical replug.
- `claude_stats.py` — usage % (API + JSONL fallback), adapted from
  [samperez10/claude_meter](https://github.com/samperez10/claude_meter).
- `extra_stats.py` — date / currency / weather.
- `dashboard.py` — rendering + main loop + screen switching.

## Setup

```bash
brew install libusb
python3 -m venv .venv
.venv/bin/pip install pyusb pillow numpy psutil
```

## Run

```bash
.venv/bin/python dashboard.py
```

Runs permanently via a LaunchAgent
(`~/Library/LaunchAgents/com.firas.ax206dashboard.plist`) — starts on
login, auto-restarts on crash/USB glitch.
