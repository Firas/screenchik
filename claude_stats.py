"""Claude Code usage stats: exact % from the official OAuth usage endpoint
(same source as the /usage command), with a local JSONL-based fallback.

Adapted from https://github.com/samperez10/claude_meter (MIT-ish, small
personal project) — credentials/endpoint approach is theirs, trimmed down
here to just the numbers we render.
"""
import json
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude" / "projects"
_CREDS_FILE = Path.home() / ".claude" / ".credentials.json"
_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_KEYCHAIN_SERVICE = "Claude Code-credentials"
_API_TTL = 120       # seconds between real API calls
_API_BACKOFF = 3600  # seconds to back off after a 429

WINDOW_HOURS = 5
WEEKLY_HOURS = 168
_DEFAULT_WINDOW_LIMIT = 500_000

_api_cache = {"data": None, "ts": 0.0, "backoff_until": 0.0}

PRICES = {
    "sonnet": (3.0, 15.0),
    "opus": (15.0, 75.0),
    "haiku": (0.80, 4.0),
}


def _get_token():
    # 1) plaintext credentials file (some platforms/older installs)
    try:
        creds = json.loads(_CREDS_FILE.read_text())
        return creds["claudeAiOauth"]["accessToken"]
    except Exception:
        pass
    # 2) macOS Keychain (this Mac's Claude Code stores the OAuth blob here).
    # Never printed/logged — only used in-memory for the HTTPS Authorization
    # header of the same official usage endpoint the /usage command uses.
    try:
        out = subprocess.run(
            ["/usr/bin/security", "find-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            blob = json.loads(out.stdout.strip())
            return blob["claudeAiOauth"]["accessToken"]
    except Exception:
        pass
    return None


def fetch_usage_api():
    now = time.time()
    if now < _api_cache["backoff_until"]:
        return _api_cache["data"]
    if _api_cache["data"] and (now - _api_cache["ts"]) < _API_TTL:
        return _api_cache["data"]
    token = _get_token()
    if not token:
        return None
    try:
        req = urllib.request.Request(
            _USAGE_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        _api_cache["data"] = data
        _api_cache["ts"] = now
        return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _api_cache["backoff_until"] = now + _API_BACKOFF
        return _api_cache["data"]
    except Exception:
        return _api_cache["data"]


def _fmt_reset(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        rem = (dt - datetime.now(timezone.utc)).total_seconds()
        if rem <= 0:
            return "now"
        d, h, m = int(rem // 86400), int((rem % 86400) // 3600), int((rem % 3600) // 60)
        return f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")
    except Exception:
        return "?"


def parse_stats():
    now = time.time()
    window_start = now - WINDOW_HOURS * 3600

    entries = []
    seen_uuids = set()
    total_out = msg_count = 0
    cost = 0.0
    last_model = "Sonnet"
    last_model_ts = None
    last_role = None
    last_role_ts = None

    if CLAUDE_DIR.exists():
        for fpath in CLAUDE_DIR.rglob("*.jsonl"):
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                        except Exception:
                            continue

                        ts = None
                        ts_raw = e.get("timestamp")
                        if ts_raw:
                            try:
                                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
                            except Exception:
                                pass

                        role = e.get("message", {}).get("role") if "message" in e else None
                        if role and ts and (last_role_ts is None or ts > last_role_ts):
                            last_role, last_role_ts = role, ts

                        m = e.get("model") or e.get("message", {}).get("model", "")
                        if m and ts and (last_model_ts is None or ts > last_model_ts):
                            last_model, last_model_ts = m, ts

                        uid = e.get("uuid", "")
                        if uid:
                            if uid in seen_uuids:
                                continue
                            seen_uuids.add(uid)

                        u = e.get("message", {}).get("usage", {})
                        out = u.get("output_tokens", 0) or 0
                        inp = u.get("input_tokens", 0) or 0
                        cc = u.get("cache_creation_input_tokens", 0) or 0

                        if out > 0 and ts:
                            msg_count += 1
                            total_out += out
                            tier = next((t for t in PRICES if t in last_model.lower()), "sonnet")
                            p_in, p_out = PRICES[tier]
                            cost += (inp + cc) * p_in / 1e6 + out * p_out / 1e6
                            entries.append((ts, out))
            except Exception:
                continue

    burn_tok = sum(out for ts, out in entries if ts >= now - 300)
    burn_rate = burn_tok / 5.0  # tokens/min over last 5 min

    api = fetch_usage_api()
    if api:
        window_pct = float(api.get("five_hour", {}).get("utilization", 0) or 0)
        weekly_pct = float(api.get("seven_day", {}).get("utilization", 0) or 0)
        window_reset = _fmt_reset(api.get("five_hour", {}).get("resets_at"))
        weekly_reset = _fmt_reset(api.get("seven_day", {}).get("resets_at"))
        api_ok = True
    else:
        week_start = now - WEEKLY_HOURS * 3600
        window_tok = sum(out for ts, out in entries if ts >= window_start)
        weekly_tok = sum(out for ts, out in entries if ts >= week_start)
        window_pct = min(window_tok / _DEFAULT_WINDOW_LIMIT * 100, 100)
        weekly_pct = min(weekly_tok / (_DEFAULT_WINDOW_LIMIT * 168 / 5) * 100, 100)
        window_reset = weekly_reset = None
        api_ok = False

    is_active = (last_role == "user" and last_role_ts is not None
                 and (now - last_role_ts) < 300)

    short = last_model
    for pat, rep in [
        ("sonnet-4-6", "Sonnet 4.6"), ("sonnet-4-5", "Sonnet 4.5"),
        ("sonnet-5", "Sonnet 5"), ("opus-5", "Opus 5"),
        ("opus-4-7", "Opus 4.7"), ("opus-4-6", "Opus 4.6"),
        ("haiku-4-5", "Haiku 4.5"), ("fable-5", "Fable 5"),
    ]:
        if pat in short.lower():
            short = rep
            break
    if len(short) > 20:
        short = short.rsplit("-", 1)[0]

    return {
        "model": short,
        "messages": msg_count,
        "window_pct": window_pct,
        "window_reset": window_reset,
        "weekly_pct": weekly_pct,
        "weekly_reset": weekly_reset,
        "cost": cost,
        "is_active": is_active,
        "api_ok": api_ok,
        "burn_rate": burn_rate,
    }
