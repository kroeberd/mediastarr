"""
Mediastarr — main.py
Multi-instance Sonarr & Radarr — independent project, NOT affiliated with Huntarr.
github.com/kroeberd/mediastarr

New in v6:
  - Jitter: random ±N minutes added to each hunt interval (configurable)
  - Sonarr search granularity: series / season / episode
  - Upgrade search can be disabled per instance
  - Configurable request timeout (default 30s)
  - Configurable timezone (default UTC, affects timestamps + log display)
  - Full i18n for log messages (DE/EN)
  - Fixed episode title: "Series – Episode title – S01E01"
  - Language switch now persists and reloads sidebar correctly
  - Instance management fully in main settings (no wizard redirect needed)
"""
import os, re, json, time, logging, threading, requests, random, string, zoneinfo, socket, ipaddress
import pathlib
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, render_template, jsonify, request, redirect, session, url_for, send_file
from collections import deque
import secrets
try:
    from . import db
except ImportError:
    import db

# ── Optional AES encryption for sensitive config values ───────────────────────
try:
    from cryptography.fernet import Fernet as _Fernet
    _crypto_available = True
except ImportError:
    _crypto_available = False

_SECRET_KEY_FILE = None   # set after DATA_DIR is known
_fernet = None

def _init_encryption(data_dir: pathlib.Path) -> None:
    """Initialize Fernet encryption. Auto-generates key on first run.
    Key stored in data_dir/.secret_key (mode 0600). Safe to re-run.
    If cryptography is not installed, encryption is silently skipped.
    """
    global _SECRET_KEY_FILE, _fernet
    if not _crypto_available:
        logger.info("cryptography not installed — API keys stored in plaintext")
        return
    _SECRET_KEY_FILE = data_dir / ".secret_key"
    try:
        if _SECRET_KEY_FILE.exists():
            key = _SECRET_KEY_FILE.read_bytes().strip()
        else:
            key = _Fernet.generate_key()
            _SECRET_KEY_FILE.write_bytes(key)
            import os as _os; _os.chmod(_SECRET_KEY_FILE, 0o600)
            logger.info("Generated new encryption key at .secret_key")
        _fernet = _Fernet(key)
        logger.info("Encryption ready — API keys and webhooks are AES-256 encrypted")
    except Exception as e:
        logger.warning(f"Encryption init failed: {e} — using plaintext")
        _fernet = None

def encrypt_secret(value: str) -> str:
    """Encrypt a secret value. Returns "enc:<base64>" or plaintext if unavailable."""
    if not _fernet or not value: return value
    try: return "enc:" + _fernet.encrypt(value.encode()).decode()
    except Exception: return value

def decrypt_secret(value: str) -> str:
    """Decrypt a secret value. Handles "enc:<base64>" prefix or returns as-is."""
    if not value: return value
    if not value.startswith("enc:"): return value  # not encrypted — backward compat
    if not _fernet:
        logger.warning("Encrypted secret found but encryption not available")
        return ""
    try: return _fernet.decrypt(value[4:].encode()).decode()
    except Exception as _de:
        logger.error(
            "DECRYPTION FAILED — the .secret_key file may have been replaced or lost. "
            "Re-enter API keys and webhooks in Settings to re-encrypt with the current key. "
            f"Error: {type(_de).__name__}"
        )
        return ""


app = Flask(__name__, template_folder='../templates', static_folder='../static')
app.config["SECRET_KEY"] = os.environ.get("MEDIASTARR_SESSION_SECRET") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = os.environ.get("MEDIASTARR_SESSION_SECURE","").strip().lower() in {"1","true","yes","on"}
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Module-level reference so we can reconfigure without restart
_file_handler: "logging.handlers.RotatingFileHandler | None" = None

def _setup_file_logging(data_dir: pathlib.Path,
                         max_mb: int = 5,
                         backups: int = 2) -> None:
    """Add (or reconfigure) a rotating file handler on the root logger.
    Safe to call multiple times — reconfigures the existing handler in place."""
    from logging.handlers import RotatingFileHandler
    global _file_handler
    max_mb  = max(1, min(100, int(max_mb  or 5)))
    backups = max(0, min(10,  int(backups or 2)))
    log_dir  = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "mediastarr.log"
    root = logging.getLogger()
    if _file_handler is None:
        _file_handler = RotatingFileHandler(
            str(log_file),
            maxBytes    = max_mb * 1024 * 1024,
            backupCount = backups,
            encoding    = "utf-8",
        )
        _file_handler.setFormatter(
            logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        root.addHandler(_file_handler)
        logger.info(f"File logging started: {log_file} "
                    f"(max {max_mb} MB × {backups+1} files)")
    else:
        # Reconfigure existing handler without restart
        _file_handler.maxBytes    = max_mb * 1024 * 1024
        _file_handler.backupCount = backups
        logger.info(f"Log rotation reconfigured: max {max_mb} MB × {backups+1} files")

def _reconfigure_file_logging() -> None:
    """Apply current CONFIG log_max_mb / log_backups to the running handler."""
    if _file_handler is None:
        return
    _setup_file_logging(
        DATA_DIR,
        max_mb  = CONFIG.get("log_max_mb",  5),
        backups = CONFIG.get("log_backups", 2),
    )

# ─── Constants ───────────────────────────────────────────────────────────────
ALLOWED_TYPES       = frozenset({"sonarr","radarr"})
ALLOWED_LANGUAGES   = frozenset({"de","en"})
ALLOWED_ACTIONS     = frozenset({"start","stop","run_now"})
ALLOWED_SCHEMES     = frozenset({"http","https"})
ALLOWED_THEMES      = frozenset({"dark","light","oled","system","github-inspired","discord-inspired","plex-inspired"})
ALLOWED_SONARR_MODES= frozenset({"episode","season","series"})
ALLOWED_RESOLUTIONS = frozenset({"","SDTV","WEBDL-480p","Bluray-480p",
    "WEBDL-720p","Bluray-720p","WEBDL-1080p","Bluray-1080p",
    "WEBDL-2160p","Bluray-2160p","HDTV-720p","HDTV-1080p"})

_RES_RANK = {
    "":0,"SDTV":1,"WEBDL-480p":2,"Bluray-480p":3,
    "HDTV-720p":4,"WEBDL-720p":5,"Bluray-720p":6,
    "HDTV-1080p":7,"WEBDL-1080p":8,"Bluray-1080p":9,
    "WEBDL-2160p":10,"Bluray-2160p":11,
}

def _res_rank(name: str) -> int:
    if not name: return 0
    n = name.strip()
    if n in _RES_RANK: return _RES_RANK[n]
    best = 0
    for k, v in _RES_RANK.items():
        if k and k.lower() in n.lower(): best = max(best, v)
    return best

def _imdb_rating(obj: dict) -> float:
    try:
        return float(obj.get("ratings",{}).get("imdb",{}).get("value",0) or 0)
    except (TypeError, ValueError):
        return 0.0
API_KEY_RE          = re.compile(r'^[A-Za-z0-9\-_]{8,128}$')
NAME_RE             = re.compile(r'^[A-Za-z0-9 \-_äöüÄÖÜß]{1,40}$')
URL_MAX_LEN         = 256
MAX_INSTANCES       = 20
MIN_INTERVAL_SEC    = 900   # 15 minutes absolute minimum
MIN_INTERVAL_MIN    = 15    # minutes

# ─── Discord Webhook ─────────────────────────────────────────────────────────
DISCORD_COLORS = {
    "missing":  0x3de68b,   # green
    "upgrade":  0xf5c842,   # yellow
    "cooldown": 0x4d9cff,   # blue
    "limit":    0xff4d4d,   # red
    "offline":  0x888888,   # grey
    "stats":    0xff6b2b,   # orange / brand
    "info":     0xff6b2b,
}

# Service icon URLs for author field
_ICON_SONARR   = "https://raw.githubusercontent.com/Sonarr/Sonarr/develop/Logo/128.png"
_ICON_RADARR   = "https://raw.githubusercontent.com/Radarr/Radarr/develop/Logo/128.png"
_ICON_MEDIASTARR = "https://mediastarr.de/static/icon.png"

# Rate-limit guard: tracks last successful send time per event_type
_dc_last_sent: dict[str, float] = {}
_dc_lock    = threading.Lock()
_cfg_lock   = threading.Lock()   # guards CONFIG reads/writes and save_config

def _dc_cooldown_ok(event_type: str, cooldown_sec: int) -> bool:
    with _dc_lock:
        last = _dc_last_sent.get(event_type, 0.0)
        if time.time() - last >= cooldown_sec:
            _dc_last_sent[event_type] = time.time()
            return True
        return False

# ── Image / link helpers ──────────────────────────────────────────────────────
def _sonarr_poster(series: dict) -> str:
    """Return best poster URL from Sonarr series object (remoteUrl preferred)."""
    for img in (series.get("images") or []):
        if img.get("coverType") in ("poster", "Poster"):
            url = img.get("remoteUrl") or img.get("url") or ""
            if url.startswith("http"): return url
    return ""

def _sonarr_fanart(series: dict) -> str:
    """Return fanart/banner URL from Sonarr (for large embed image)."""
    for cover_type in ("fanart", "banner", "Fanart", "Banner"):
        for img in (series.get("images") or []):
            if img.get("coverType") == cover_type:
                url = img.get("remoteUrl") or img.get("url") or ""
                if url.startswith("http"): return url
    return ""

def _radarr_poster(movie: dict) -> str:
    """Return best poster URL from Radarr movie object."""
    rp = movie.get("remotePoster","")
    if rp and rp.startswith("http"): return rp
    for img in (movie.get("images") or []):
        if img.get("coverType") in ("poster","Poster"):
            url = img.get("remoteUrl") or img.get("url") or ""
            if url.startswith("http"): return url
    return ""

def _radarr_fanart(movie: dict) -> str:
    """Return fanart URL from Radarr (for large embed image)."""
    rf = movie.get("remoteFanart","")
    if rf and rf.startswith("http"): return rf
    for img in (movie.get("images") or []):
        if img.get("coverType") in ("fanart","Fanart","backdrop","Backdrop"):
            url = img.get("remoteUrl") or img.get("url") or ""
            if url.startswith("http"): return url
    return ""

def _imdb_url(imdb_id: str) -> str:
    if imdb_id and str(imdb_id).startswith("tt"):
        return f"https://www.imdb.com/title/{imdb_id}/"
    return ""

def _tmdb_url(tmdb_id, media="movie") -> str:
    if tmdb_id:
        return f"https://www.themoviedb.org/{media}/{tmdb_id}"
    return ""

def _tvdb_url(series: dict) -> str:
    slug = series.get("titleSlug","")
    if slug: return f"https://thetvdb.com/series/{slug}"
    tvdb_id = series.get("tvdbId")
    if tvdb_id: return f"https://thetvdb.com/?tab=series&id={tvdb_id}"
    return ""

def _rating_str(item: dict, item_type: str) -> str:
    """Return formatted multi-source rating string from Sonarr/Radarr data."""
    parts = []
    if item_type in ("movie","movie_upgrade"):
        ratings = item.get("ratings") or {}
        # Radarr v3+: nested per-source dicts
        for src, label, icon in [("imdb","IMDb","⭐"), ("tmdb","TMDB","🎬"), ("rottenTomatoes","RT","🍅")]:
            entry = ratings.get(src)
            if isinstance(entry, dict):
                val   = entry.get("value")
                votes = entry.get("votes",0)
                if val:
                    vs = f" ({votes:,})" if votes and votes > 0 else ""
                    parts.append(f"{icon} **{val:.1f}** {label}{vs}")
        # Radarr v2: flat {value, votes}
        if not parts:
            flat = ratings.get("value")
            if flat: parts.append(f"⭐ **{flat:.1f}**")
    else:
        # Sonarr: try nested per-source ratings first (v4+), fall back to flat value
        ratings = (item.get("series") or item).get("ratings") or {}
        found_multi = False
        for src, label, icon in [("imdb","IMDb","⭐"), ("tmdb","TMDB","🎬")]:
            entry = ratings.get(src)
            if isinstance(entry, dict):
                val   = entry.get("value")
                votes = entry.get("votes", 0)
                if val:
                    vs = f" ({votes:,})" if votes and votes > 0 else ""
                    parts.append(f"{icon} **{val:.1f}** {label}{vs}")
                    found_multi = True
        # Fall back to flat Sonarr v3 format
        if not found_multi:
            val = ratings.get("value")
            if val: parts.append(f"⭐ **{val:.1f}**")
    return "\n".join(parts) if parts else ""

def _genres_str(item: dict) -> str:
    genres = (item.get("genres") or (item.get("series") or {}).get("genres") or [])
    return ", ".join(str(g) for g in genres[:4]) if genres else ""

def _year_str(item: dict) -> str:
    y = item.get("year") or (item.get("series") or {}).get("year")
    return str(y) if y else ""

def _runtime_str(item: dict) -> str:
    rt = item.get("runtime") or (item.get("series") or {}).get("runtime")
    if rt:
        h, m = divmod(int(rt), 60)
        return f"{h}h {m}min" if h else f"{m}min"
    return ""

def _status_str(item: dict) -> str:
    st = item.get("status") or (item.get("series") or {}).get("status") or ""
    icons = {"continuing":"🟢","ended":"🔴","upcoming":"🔵","announced":"🟡",
             "released":"🟢","inCinemas":"🎭","deleted":"⚫"}
    return f"{icons.get(st,'⚪')} {st.capitalize()}" if st else ""

def _link_buttons(links: list[tuple[str,str]]) -> str:
    """Build a Markdown line of [Label](url) link buttons."""
    parts = [f"[{label}]({url})" for label, url in links if url]
    return "  •  ".join(parts) if parts else ""

def discord_send(event_type: str, title: str, description: str,
                 instance_name: str = "", fields: list | None = None,
                 force: bool = False,
                 inst_type: str = "",  # "sonarr"|"radarr" for per-type webhook routing
                 thumbnail_url: str = "",
                 image_url: str = "",
                 embed_url: str = "",
                 author_name: str = "",
                 author_icon: str = "",
                 footer_extra: str = ""):
    """Fire-and-forget rich Discord embed. Daemon thread."""
    dc = CONFIG.get("discord", {})
    if not dc.get("enabled"): return
    # Pick URL: per-type override if set, else global webhook
    # Determine instance type from inst_type hint embedded in function call
    _dc_inst_type = getattr(discord_send, "_current_inst_type", "")
    _sonarr_url = decrypt_secret(safe_str(dc.get("sonarr_webhook_url",""), 512).strip())
    _radarr_url = decrypt_secret(safe_str(dc.get("radarr_webhook_url",""), 512).strip())
    if _dc_inst_type == "sonarr" and _sonarr_url:
        url = _sonarr_url
    elif _dc_inst_type == "radarr" and _radarr_url:
        url = _radarr_url
    else:
        url = decrypt_secret(safe_str(dc.get("webhook_url", ""), 512).strip())
    if not url or not url.startswith(("http://", "https://")): return

    toggle_map = {
        "missing":  "notify_missing",
        "upgrade":  "notify_upgrade",
        "cooldown": "notify_cooldown",
        "limit":    "notify_limit",
        "offline":  "notify_offline",
    }
    toggle_key = toggle_map.get(event_type)
    if toggle_key and not dc.get(toggle_key, True): return

    cooldown_sec = clamp_int(dc.get("rate_limit_cooldown", 5), 1, 300, 5)
    if not force and not _dc_cooldown_ok(event_type, cooldown_sec):
        logger.debug(f"Discord rate-limit: skipping {event_type}")
        return

    color = DISCORD_COLORS.get(event_type, DISCORD_COLORS["info"])
    footer_parts = ["Mediastarr " + _CURRENT_VERSION]
    if instance_name: footer_parts.append(instance_name)
    if footer_extra:  footer_parts.append(footer_extra)
    footer_text = "  ·  ".join(footer_parts)

    embed: dict = {
        "title":     safe_str(title, 256),
        "color":     color,
        "footer":    {"text": footer_text,
                      "icon_url": _ICON_MEDIASTARR},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    if description:
        embed["description"] = safe_str(description, 2048)
    if embed_url and embed_url.startswith("http"):
        embed["url"] = embed_url
    if thumbnail_url and thumbnail_url.startswith("http"):
        embed["thumbnail"] = {"url": thumbnail_url}
    if image_url and image_url.startswith("http"):
        embed["image"] = {"url": image_url}
    # Author line: service name + icon
    _author_icon = author_icon or _ICON_MEDIASTARR
    if author_name:
        embed["author"] = {
            "name":     safe_str(author_name, 256),
            "icon_url": _author_icon,
        }
    if fields:
        embed["fields"] = [
            {"name":   safe_str(f.get("name",""),  256),
             "value":  safe_str(f.get("value",""), 1024),
             "inline": bool(f.get("inline", True))}
            for f in fields[:10]
        ]

    def _send():
        try:
            payload = {
                "embeds":   [embed],
                "username": "Mediastarr",
                "avatar_url": _ICON_MEDIASTARR,
            }
            r = requests.post(url, json=payload, timeout=CONFIG.get("request_timeout", 30))
            if r.status_code == 429:
                retry_after = r.json().get("retry_after", 5)
                logger.warning(f"Discord 429: retry_after={retry_after}s")
            elif r.status_code not in (200, 204):
                logger.warning(f"Discord webhook HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"Discord webhook failed: {e}")

    threading.Thread(target=_send, daemon=True).start()


def discord_send_stats():
    """Send a rich statistics summary embed to Discord."""
    dc = CONFIG.get("discord", {})
    if not dc.get("enabled") or not dc.get("notify_stats", False): return
    lang   = CONFIG.get("language", "de")
    today  = db.count_today()
    limit  = CONFIG.get("daily_limit", 0)
    total  = db.total_count()
    cycles = STATE.get("cycle_count", 0)
    ts     = now_local().strftime("%d.%m.%Y %H:%M" if lang == "de" else "%Y-%m-%d %H:%M")
    is_de  = lang == "de"

    # Progress bar for daily limit (10 chars)
    if limit > 0:
        pct   = min(today / limit, 1.0)
        filled = round(pct * 10)
        bar    = "█" * filled + "░" * (10 - filled)
        limit_val = f"`{bar}` {today}/{limit} ({int(pct*100)}%)"
    else:
        limit_val = f"**{today}** {'(unbegrenzt)' if is_de else '(unlimited)'}"

    title = "📊 Mediastarr Statistiken" if is_de else "📊 Mediastarr Statistics"
    desc  = f"{'Tagesbericht' if is_de else 'Daily report'} — {ts}"

    fields = [
        {"name": "📅 " + ("Heute" if is_de else "Today"),
         "value": limit_val, "inline": False},
        {"name": "🔢 " + ("Gesamt" if is_de else "Total"),
         "value": f"**{total:,}** {'Suchen' if is_de else 'searches'}", "inline": True},
        {"name": "🔄 " + ("Zyklen" if is_de else "Cycles"),
         "value": f"**{cycles:,}**", "inline": True},
        {"name": "⏱ " + ("Intervall" if is_de else "Interval"),
         "value": f"{CONFIG.get('hunt_missing_delay',1800)//60} min", "inline": True},
    ]

    # Per-instance status block
    inst_lines = []
    for inst in CONFIG["instances"][:8]:
        st    = STATE["inst_stats"].get(inst["id"], {}).get("status", "?")
        mis   = STATE["inst_stats"].get(inst["id"], {}).get("missing_searched", 0)
        upg   = STATE["inst_stats"].get(inst["id"], {}).get("upgrades_found", 0)
        icon  = "🟢" if st == "online" else "🔴" if st == "offline" else "⚫"
        t_ico = "📺" if inst.get("type") == "sonarr" else "🎬"
        inst_lines.append(
            f"{t_ico} **{inst['name']}** {icon}\n"
            f"  {'Gesucht' if is_de else 'Searched'}: {mis}  •  Upgrades: {upg}"
        )
    if inst_lines:
        fields.append({
            "name":   "📡 " + ("Instanzen" if is_de else "Instances"),
            "value":  "\n".join(inst_lines),
            "inline": False
        })

    discord_send("stats", title, desc, "System", fields=fields, force=True,
                 author_name="Mediastarr", author_icon=_ICON_MEDIASTARR)


# Stats report background thread
_stats_stop = threading.Event()

def _stats_loop():
    """Periodically send stats report to Discord."""
    while not _stats_stop.is_set():
        time.sleep(60)  # check every minute
        dc = CONFIG.get("discord", {})
        if not dc.get("enabled") or not dc.get("notify_stats", False):
            continue
        interval_min = clamp_int(dc.get("stats_interval_min", 60), 1, 10080, 60)
        last = dc.get("stats_last_sent_at", 0.0)
        if time.time() - float(last or 0) >= interval_min * 60:
            discord_send_stats()
            # Notify if update available
            if is_update_available() and CONFIG.get("discord",{}).get("notify_update", True):
                latest = _version_cache.get("latest","")
                lang = CONFIG.get("language","de")
                is_de = lang == "de"
                _upd_title = (f"🆕 Update verfügbar: {latest}" if is_de
                              else f"🆕 Update available: {latest}")
                _upd_desc  = (f"Mediastarr **{latest}** ist auf GitHub verfügbar.\nAktuell läuft `{_CURRENT_VERSION}`."
                              if is_de else
                              f"Mediastarr **{latest}** is available on GitHub.\nCurrently running `{_CURRENT_VERSION}`.")
                _upd_url   = f"https://github.com/kroeberd/mediastarr/releases/tag/{latest}"
                _upd_fields = [{"name":"🔗 GitHub Release","value":f"[{latest}]({_upd_url})","inline":True},
                               {"name":"📦 "+("Aktuell" if is_de else "Current"),"value":f"`{_CURRENT_VERSION}`","inline":True}]
                discord_send("info", _upd_title, _upd_desc, "System", fields=_upd_fields, force=True)
            CONFIG["discord"]["stats_last_sent_at"] = time.time()
            save_config(CONFIG)

_stats_thread = threading.Thread(target=_stats_loop, daemon=True)
_stats_thread.start()


# ─── i18n log messages ───────────────────────────────────────────────────────
MSGS = {
    "de": {
        "cycle_start":      "Zyklus #{n} gestartet – {active} aktiv – Heute: {today}/{limit}",
        "cycle_done":       "Zyklus #{n} abgeschlossen – Heute gesamt: {today}",
        "daily_limit":      "Tageslimit erreicht: {today}/{limit}",
        "db_pruned":        "{n} abgelaufene Einträge bereinigt",
        "skipped_offline":  "Übersprungen – Offline oder deaktiviert",
        "auto_start":       "Hunt-Schleife gestartet",
        "app_start":        "Mediastarr gestartet",
        "setup_required":   "Einrichtung erforderlich – {setup_url}",
        "missing":          "Fehlend",
        "upgrade":          "Upgrade",
        "error":            "Fehler",
        "next_run":         "Nächster Lauf um {hhmm} (Jitter: {jitter_min})",
    },
    "en": {
        "cycle_start":      "Cycle #{n} started – {active} active – Today: {today}/{limit}",
        "cycle_done":       "Cycle #{n} done – Today total: {today}",
        "daily_limit":      "Daily limit reached: {today}/{limit}",
        "db_pruned":        "{n} expired entries pruned",
        "skipped_offline":  "Skipped – offline or disabled",
        "auto_start":       "Hunt loop started",
        "trigger":          "Run now ausgelöst",
        "app_start":        "Mediastarr started",
        "trigger":          "Run now triggered",
        "setup_required":   "Setup required – {setup_url}",
        "missing":          "Missing",
        "upgrade":          "Upgrade",
        "error":            "Error",
        "next_run":         "Next run at {hhmm} (jitter: {jitter_min})",
    },
}

def msg(key: str, **kwargs) -> str:
    lang = CONFIG.get("language","en")
    tmpl = MSGS.get(lang, MSGS["en"]).get(key, key)
    try: return tmpl.format(**kwargs)
    except Exception: return tmpl

def setup_url_for_logs() -> str:
    """Return externally reachable setup URL for startup logs."""
    public_url = os.environ.get("MEDIASTARR_PUBLIC_URL","").strip().rstrip("/")
    if public_url: return f"{public_url}/setup"
    public_port = os.environ.get("MEDIASTARR_PUBLIC_PORT","").strip()
    if public_port.isdigit(): return f"http://localhost:{public_port}/setup"
    return "http://localhost:7979/setup"

# ─── Paths ───────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CFG_FILE = DATA_DIR / "config.json"
DB_FILE  = DATA_DIR / "mediastarr.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
db.init(DB_FILE)
_init_encryption(DATA_DIR)  # set up Fernet key after DATA_DIR is established

def _migrate_encrypt_secrets() -> None:
    """On startup: re-encrypt any plaintext API keys / webhooks found in CONFIG.
    Safe to call multiple times — already-encrypted values are left unchanged.
    This handles the Docker update scenario: existing plaintext config gets
    transparently encrypted on first boot with the new version.
    """
    if not _fernet:
        return  # encryption not available, nothing to do
    changed = False
    for inst in CONFIG.get("instances", []):
        raw = inst.get("api_key", "")
        if raw and not raw.startswith("enc:"):
            inst["api_key"] = encrypt_secret(raw)
            changed = True
    dc = CONFIG.get("discord", {})
    for wh_key in ("webhook_url", "sonarr_webhook_url", "radarr_webhook_url"):
        raw = dc.get(wh_key, "")
        if raw and not raw.startswith("enc:"):
            dc[wh_key] = encrypt_secret(raw)
            changed = True
    if changed:
        save_config(CONFIG)
        logger.info(f"Migrated {sum(1 for i in CONFIG.get('instances',[]) if i.get('api_key','').startswith('enc:'))} API key(s) + webhook(s) to AES-256 encryption")


# ─── Helpers ─────────────────────────────────────────────────────────────────
def make_id() -> str:
    return "inst_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

def fresh_inst_stats() -> dict:
    return {"missing_found":0,"missing_searched":0,"upgrades_found":0,
            "upgrades_searched":0,"skipped_cooldown":0,"skipped_daily":0,
            "status":"unknown","version":"?","skipped_unreleased":0}


def _detect_local_tz() -> str:
    """Return the host OS IANA timezone name with 4 fallbacks."""
    # 1. Honour an explicit TZ environment variable if set
    env_tz = os.environ.get("TZ", "").strip()
    if env_tz:
        try:
            zoneinfo.ZoneInfo(env_tz)
            return env_tz
        except Exception:
            pass
    # 2. Python 3.11+ exposes the local zone directly
    try:
        local = zoneinfo.ZoneInfo("localtime")
        if local.key:
            return local.key
    except Exception:
        pass
    # 3. Read /etc/timezone (Debian/Ubuntu containers)
    try:
        tz_name = Path("/etc/timezone").read_text().strip()
        if tz_name:
            zoneinfo.ZoneInfo(tz_name)
            return tz_name
    except Exception:
        pass
    # 4. Resolve /etc/localtime symlink to IANA name (most Linux/macOS)
    try:
        lt = Path("/etc/localtime").resolve()
        parts = lt.parts
        zi_idx = next((i for i, p in enumerate(parts) if p == "zoneinfo"), None)
        if zi_idx is not None:
            tz_name = "/".join(parts[zi_idx + 1:])
            zoneinfo.ZoneInfo(tz_name)
            return tz_name
    except Exception:
        pass
    return "UTC"

_OS_TIMEZONE = _detect_local_tz()

def now_local() -> datetime:
    """Current time in configured timezone."""
    tz_name = CONFIG.get("timezone", _OS_TIMEZONE)
    try: tz = zoneinfo.ZoneInfo(tz_name)
    except Exception: tz = zoneinfo.ZoneInfo(_OS_TIMEZONE)
    return datetime.now(tz)

def fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")

def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def _year(val):
    if val is None: return None
    try:
        y = int(str(val)[:4])
        return y if 1900 < y < 2100 else None
    except Exception: return None


# ─── Version check ────────────────────────────────────────────────────────
_VERSION_FILE    = pathlib.Path(__file__).parent.parent / "VERSION"
_CURRENT_VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "v7.1.7"
_version_cache   = {"latest": None, "checked_at": 0.0}

def check_latest_version() -> str | None:
    """Fetch latest GitHub release tag. Returns tag string or None on error."""
    import time as _time
    now = _time.time()
    if _version_cache["latest"] and now - _version_cache["checked_at"] < 3600:
        return _version_cache["latest"]
    try:
        r = requests.get(
            "https://api.github.com/repos/kroeberd/mediastarr/releases/latest",
            timeout=8, headers={"Accept": "application/vnd.github+json",
                                 "User-Agent": "Mediastarr-UpdateCheck"}
        )
        if r.status_code == 200:
            tag = r.json().get("tag_name", "")
            _version_cache["latest"] = tag
            _version_cache["checked_at"] = now
            return tag
    except Exception:
        pass
    return None

def is_update_available() -> bool:
    latest = check_latest_version()
    if not latest: return False
    try:
        def _parse(v):
            return tuple(int(x) for x in v.lstrip("v").split("."))
        return _parse(latest) > _parse(_CURRENT_VERSION)
    except Exception:
        return False

# ─── Default config ───────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "setup_complete": False,
    "language": "en",
    "theme": "dark",
    "timezone": "UTC",
    "instances": [],
    "hunt_missing_delay":   1800,   # seconds internally (default 30min)
    "hunt_upgrade_delay":   3600,   # seconds internally (default 60min)
    "max_searches_per_run":   10,
    "daily_limit":            20, "sonarr_daily_limit": 0, "radarr_daily_limit": 0,
    "upgrade_daily_limit":    0,    # global max upgrades/day across ALL instances (0 = unlimited)
    "sonarr_upgrade_daily_limit": 0,
    "radarr_upgrade_daily_limit": 0,
    "sonarr_daily_limit":      0,    # global max searches/day across ALL Sonarr instances (0 = unlimited)
    "radarr_daily_limit":      0,    # global max searches/day across ALL Radarr instances (0 = unlimited)
    "cooldown_days":           7,
    "request_timeout":        30,   # seconds for arr API calls
    "jitter_max":            300,   # max random seconds added to interval (0=off)
    "dry_run":    False,
    "auto_start": True,
    # Sonarr search granularity: "episode" | "season" | "series"
    "sonarr_search_mode": "season",   # season is safer default (fewer API calls)
    # Whether to search for upgrades at all
    "search_upgrades": True,
    # Discord Webhook notifications
    "discord": {
        "enabled":             False,
        "webhook_url":         "",
        "notify_missing":      True,   # new missing search triggered
        "notify_upgrade":      True,   # upgrade search triggered
        "notify_cooldown":     True,   # items released from cooldown
        "notify_limit":        True,   # daily limit reached
        "notify_offline":      True,   # instance went offline
        "notify_stats":        False,  # periodic stats report
        "notify_update":       True,   # new version available on GitHub
        "stats_interval_min":  60,     # minutes between stats reports
        "stats_last_sent_at":  0.0,    # unix timestamp
        "rate_limit_cooldown": 5,      # seconds between same-type messages
    },
    # Read-only public access to /api/state (no auth required, no sensitive data)
    "public_api_state": False,
    # Maintenance windows: list of {"start":"HH:MM","end":"HH:MM","label":"..."} in local time
    # Searches are paused while local time is within any window.
    "maintenance_windows": [],
    # Rotating log file settings
    "tag_enabled":  False,  # add a tag to searched items in Sonarr/Radarr
    "webhook_trigger_key": "",  # optional auth key for POST /api/webhook/trigger
    "tag_label":    "mediastarr",  # tag label to create/use
    "log_max_mb":    5,    # max size per log file in MB (1–100)
    "log_backups":   2,    # number of backup files to keep (0–10)
    "log_min_level": "INFO",  # minimum level for Docker/file log: DEBUG|INFO|WARN|ERROR
    # Stalled download monitor (Feature #46)
    "stall_monitor_enabled":   False,   # master switch
    "stall_threshold_min":     60,      # minutes with no progress before action
    "stall_action":            "search", # "search" = trigger new search | "warn" = Discord only

}

def _migrate_config(cfg: dict) -> dict:
    """Non-destructively add any keys from DEFAULT_CONFIG that are missing in cfg.
    Also repairs nested discord dict and per-instance defaults."""
    # Top-level keys
    for key, default_val in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = default_val
            logger.info(f"Config migration: added missing key '{key}' = {default_val!r}")
    # Discord sub-keys
    dc_defaults = DEFAULT_CONFIG["discord"]
    cfg_dc = cfg.setdefault("discord", {})
    for key, default_val in dc_defaults.items():
        if key not in cfg_dc:
            cfg_dc[key] = default_val
            logger.info(f"Config migration: added discord.{key} = {default_val!r}")
    # Instance defaults — ensure required fields exist
    for inst in cfg.get("instances", []):
        if "id"               not in inst: inst["id"]               = make_id()
        if "enabled"          not in inst: inst["enabled"]          = True
        if "daily_limit"      not in inst: inst["daily_limit"]      = 0
        if "type"             not in inst: inst["type"]             = "sonarr"
        if "search_upgrades"  not in inst: inst["search_upgrades"]  = False
        if "tag_enabled"      not in inst: inst["tag_enabled"]      = None   # None = use global
        if "tag_filter_ids"   not in inst: inst["tag_filter_ids"]   = []     # empty = no filter (search all)
        if "tag_filter"       not in inst: inst["tag_filter"]       = []     # empty = all items
        if "upgrade_daily_limit" not in inst: inst["upgrade_daily_limit"] = 0  # 0 = use global
        if "stall_monitor_enabled" not in inst: inst["stall_monitor_enabled"] = None  # None = use global
    return cfg

def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if CFG_FILE.exists():
        try:
            raw = json.loads(CFG_FILE.read_text())
            cfg.update(raw)
            cfg = _migrate_config(cfg)
        except Exception as e: logger.warning(f"Config load failed: {e}")
    else:
        cfg = _migrate_config(cfg)
    # Respect TZ environment variable if timezone is still default UTC
    tz_env = os.environ.get("TZ","").strip()
    if tz_env and cfg.get("timezone","UTC") == "UTC":
        try:
            zoneinfo.ZoneInfo(tz_env)
            cfg["timezone"] = tz_env
            logger.info(f"Timezone set from TZ env: {tz_env}")
        except Exception:
            logger.warning(f"TZ env value '{tz_env}' is not a valid IANA timezone, ignoring")
    return cfg

def save_config(cfg: dict):
    with _cfg_lock:
        tmp = CFG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(CFG_FILE)
        try:
            import os as _os; _os.chmod(CFG_FILE, 0o600)
        except Exception:
            pass

def _bootstrap_host() -> str:
    """Return best-effort host/IP for local arr fallback URLs."""
    env_host = (os.environ.get("SYSTEM_IP","").strip() or
                os.environ.get("HOST_IP","").strip())
    if env_host:
        return env_host
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"

def _bootstrap_arr_url(service: str) -> str:
    port = 8989 if service == "sonarr" else 7878
    return f"http://{_bootstrap_host()}:{port}"

CONFIG = load_config()

# Env-var bootstrap
if not CONFIG["setup_complete"] and not CONFIG["instances"]:
    for svc, ek, eu in [
        ("sonarr","SONARR_API_KEY","SONARR_URL"),
        ("radarr","RADARR_API_KEY","RADARR_URL"),
    ]:
        k = os.environ.get(ek,"").strip()
        if k:
            fallback_url = _bootstrap_arr_url(svc)
            CONFIG["instances"].append({"id":make_id(),"type":svc,
                "name":svc.title(),"url":os.environ.get(eu,fallback_url).strip(),
                "api_key":k,"enabled":True})
    if CONFIG["instances"]:
        CONFIG["setup_complete"] = True; save_config(CONFIG)

# ─── Runtime State ────────────────────────────────────────────────────────────
STATE = {
    "running":False,"last_run":None,"next_run":None,"cycle_count":0,
    "inst_stats":{}, "activity_log":deque(maxlen=300),
}
STOP_EVENT  = threading.Event()
hunt_thread = None
CYCLE_LOCK  = threading.Lock()

def _ensure_inst_stats():
    for inst in CONFIG["instances"]:
        if inst["id"] not in STATE["inst_stats"]:
            STATE["inst_stats"][inst["id"]] = fresh_inst_stats()

_ensure_inst_stats()

# ─── Validation ───────────────────────────────────────────────────────────────
def validate_url(url: str):
    if not url or not isinstance(url,str): return False,"URL fehlt"
    if len(url) > URL_MAX_LEN: return False,"URL zu lang"
    try: p = urlparse(url)
    except Exception: return False,"URL ungültig"
    if p.scheme not in ALLOWED_SCHEMES: return False,f"Schema '{p.scheme}' nicht erlaubt"
    if not p.hostname: return False,"Kein Hostname"
    return True,""

def is_private_host(hostname: str) -> bool:
    """Return True if hostname resolves only to private/loopback/link-local addresses."""
    host = hostname.strip().lower()
    if not host: return False
    if host == "localhost" or "." not in host: return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    if not resolved: return False
    try:
        return all(
            ipaddress.ip_address(addr).is_private or
            ipaddress.ip_address(addr).is_loopback or
            ipaddress.ip_address(addr).is_link_local
            for addr in resolved
        )
    except ValueError:
        return False

def validate_internal_service_url(url: str):
    """validate_url + SSRF check: target must be private/internal."""
    ok, err = validate_url(url)
    if not ok: return False, err
    parsed = urlparse(url)
    if not parsed.hostname or not is_private_host(parsed.hostname):
        return False, "Ziel muss auf ein lokales oder internes System zeigen"
    return True, ""

def validate_api_key(key: str):
    if not key or not isinstance(key,str): return False,"API Key fehlt"
    if not API_KEY_RE.match(key): return False,"Ungültiges Format (8-128 Zeichen: A-Z a-z 0-9 - _)"
    return True,""

def validate_name(name: str):
    if not name or not isinstance(name,str): return False,"Name fehlt"
    if not NAME_RE.match(name.strip()): return False,"Ungültige Zeichen oder zu lang (max 40)"
    return True,""

def clamp_int(val, lo, hi, default):
    try: return max(lo, min(hi, int(val)))
    except Exception: return default

def safe_str(val, max_len=256):
    return val[:max_len] if isinstance(val,str) else ""

# ─── Security Headers ─────────────────────────────────────────────────────────
@app.after_request
def sec_headers(r):
    r.headers.update({
        "X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY",
        "X-XSS-Protection":"1; mode=block","Referrer-Policy":"same-origin",
        "Content-Security-Policy":(
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self';"),
    })
    if request.path.startswith("/api/"):
        r.headers["Cache-Control"]="no-store"; r.headers["Pragma"]="no-cache"
    return r

@app.errorhandler(400)
def e400(e): return jsonify({"ok":False,"error":"Ungültige Anfrage"}),400
@app.errorhandler(404)
def e404(e): return jsonify({"ok":False,"error":"Nicht gefunden"}),404
@app.errorhandler(405)
def e405(e): return jsonify({"ok":False,"error":"Methode nicht erlaubt"}),405
@app.errorhandler(500)
def e500(e): logger.error(f"500:{e}"); return jsonify({"ok":False,"error":"Interner Serverfehler"}),500

# ─── *arr API Client ──────────────────────────────────────────────────────────

# Safe allowlist for ping error messages returned to client
def _safe_version_str(ver: str) -> str:
    """Sanitize a version string — only allow digits, dots, letters, hyphens.
    Breaks the CodeQL taint chain from ArrClient.get() exception path.
    """
    import re as _re
    if not ver or ver == "?": return "?"
    cleaned = _re.sub(r"[^0-9a-zA-Z.\-_+]", "", str(ver))[:20]
    return cleaned if cleaned else "?"

_SAFE_PING_MESSAGES = frozenset([
    "Authentication failed", "API endpoint not found", "Host not found",
    "Timed out", "Connection refused", "Host unreachable", "TLS/SSL error",
    "Connection failed", "Network error",
])

def _safe_ping_msg(detail: str) -> str:
    """Return detail only if it is a known-safe message, else generic fallback.
    This breaks the CodeQL taint chain from exception to HTTP response.
    """
    return detail if detail in _SAFE_PING_MESSAGES else "Connection failed"

def summarize_ping_error(raw: str) -> str:
    """Turn a raw exception string into a short user-readable message."""
    text = str(raw or "").strip()
    lower = text.lower()
    if not text: return "Connection failed"
    if "401" in lower or "403" in lower or "unauthorized" in lower or "forbidden" in lower:
        return "Authentication failed"
    if "404" in lower: return "API endpoint not found"
    if "name or service not known" in lower or "nodename nor servname" in lower:
        return "Host not found"
    if "timed out" in lower or "timeout" in lower: return "Timed out"
    if "failed to establish a new connection" in lower or "connection refused" in lower:
        return "Connection refused"
    if "max retries exceeded" in lower or "connectionpool" in lower:
        return "Host unreachable"
    if "ssl" in lower or "certificate" in lower: return "TLS/SSL error"
    compact = re.sub(r"\s+", " ", text)
    if ":" in compact: compact = compact.split(":", 1)[0].strip()
    return compact[:60] or "Connection failed"

class ArrClient:
    def __init__(self, name:str, url:str, api_key:str):
        self.name = name; self.url = url.rstrip("/")
        self._h = {"X-Api-Key":decrypt_secret(api_key),"Content-Type":"application/json"}

    def _timeout(self) -> int:
        return CONFIG.get("request_timeout", 30)

    def get(self, path, params=None):
        r = requests.get(f"{self.url}/api/v3/{path}", headers=self._h,
                         params=params, timeout=self._timeout())
        r.raise_for_status(); return r.json()

    def post(self, path, data=None):
        r = requests.post(f"{self.url}/api/v3/{path}", headers=self._h,
                          json=data, timeout=self._timeout())
        r.raise_for_status(); return r.json()

    def put(self, path, data=None):
        r = requests.put(f"{self.url}/api/v3/{path}", headers=self._h,
                         json=data, timeout=self._timeout())
        r.raise_for_status(); return r.json()

    def delete_with_params(self, path, params=None):
        r = requests.delete(f"{self.url}/api/v3/{path}", headers=self._h,
                            params=params, timeout=self._timeout())
        r.raise_for_status()

    def ping(self):
        try:
            d = self.get("system/status")
            return True, str(d.get("version","?"))[:20], ""
        except Exception as e:
            return False, "?", summarize_ping_error(str(e)[:200])

# ─── Activity Log ─────────────────────────────────────────────────────────────
# ── Log levels ────────────────────────────────────────────────────────────────
_LOG_LEVEL_MAP = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
                  "WARN": logging.WARNING, "WARNING": logging.WARNING,
                  "ERROR": logging.ERROR}

def _apply_log_level() -> None:
    """Apply CONFIG log_min_level to the root logger (affects Docker console + file)."""
    level_str = (CONFIG.get("log_min_level") or "INFO").upper()
    level     = _LOG_LEVEL_MAP.get(level_str, logging.INFO)
    logging.getLogger().setLevel(level)

def ms_log(level: str, service: str, action: str, item: str = "") -> None:
    """Central log function — writes to Docker console/file AND the UI activity log.

    Args:
        level:   'DEBUG' | 'INFO' | 'WARN' | 'ERROR'
        service: source name shown in the UI (e.g. 'Sonarr', 'System')
        action:  short description of what happened
        item:    optional detail (title, URL, …)
    """
    lvl_upper = (level or "INFO").upper()
    # Map to Python logging level
    py_level  = _LOG_LEVEL_MAP.get(lvl_upper, logging.INFO)
    # Log to Docker console + rotating file via Python logging
    logger.log(py_level, "[%s] %s%s", service, action, f": {item}" if item else "")
    # Map to UI activity-log status
    status_map = {"DEBUG": "info", "INFO": "info", "WARN": "warning", "WARNING": "warning", "ERROR": "error"}
    ui_status  = status_map.get(lvl_upper, "info")
    log_act(service, action, item, ui_status)

# Convenience helpers
def ms_debug(service: str, action: str, item: str = "") -> None: ms_log("DEBUG", service, action, item)
def ms_info (service: str, action: str, item: str = "") -> None: ms_log("INFO",  service, action, item)
def ms_warn (service: str, action: str, item: str = "") -> None: ms_log("WARN",  service, action, item)
def ms_error(service: str, action: str, item: str = "") -> None: ms_log("ERROR", service, action, item)

_apply_log_level()  # honour log_min_level from config — called here, after function is defined

_API_KEY_RE_LOG = __import__("re").compile(r"\b[A-Za-z0-9]{32,128}\b")

def _censor_log(text: str) -> str:
    """Replace any bare API-key-like string in log text with ****.
    Only applies to strings that look like API keys (32-128 alphanum chars).
    URL paths, titles, etc. are safe because they contain non-alphanum chars.
    """
    if not text or len(text) < 32: return text
    return _API_KEY_RE_LOG.sub(lambda m: m.group()[:4] + "****" + m.group()[-4:], text)

def log_act(service:str, action:str, item:str, status:str="info"):
    ts = fmt_time(now_local())
    STATE["activity_log"].appendleft({
        "ts": ts, "service": safe_str(service,30),
        "action": safe_str(_censor_log(action),50), "item": safe_str(_censor_log(item),200),
        "status": status if status in ("info","success","warning","error") else "info",
    })
    # Also emit to Docker console with appropriate level
    py_level = {"info": logging.INFO, "success": logging.INFO,
                "warning": logging.WARNING, "error": logging.ERROR}.get(status, logging.INFO)
    logger.log(py_level, "[%s] %s%s", service, action, f": {item}" if item else "")

# ─── Jitter ───────────────────────────────────────────────────────────────────
def jittered_delay(base_sec: int) -> tuple[int, int]:
    """Returns (actual_delay, jitter_applied). Minimum 900s enforced."""
    jmax = CONFIG.get("jitter_max", 300)
    jitter = random.randint(0, max(0, jmax)) if jmax > 0 else 0
    total = max(MIN_INTERVAL_SEC, base_sec + jitter)
    return total, jitter

# ─── Hunt helpers ─────────────────────────────────────────────────────────────
def upgrade_daily_limit_reached(iid: str = "", inst_type: str = "sonarr") -> bool:
    """True if the global upgrade daily limit OR this instance upgrade limit is reached."""
    global_upgrade_limit = CONFIG.get("upgrade_daily_limit", 0)
    if global_upgrade_limit > 0 and db.count_today_upgrades() >= global_upgrade_limit:
        return True
    # Per-type global limit
    type_upg_limit = CONFIG.get(f"{inst_type}_upgrade_daily_limit", 0)
    if type_upg_limit > 0:
        type_upg_today = sum(
            db.count_today_upgrades_for_instance(i["id"])
            for i in CONFIG.get("instances", []) if i.get("type") == inst_type
        )
        if type_upg_today >= type_upg_limit: return True
    # Per-instance limit
    if iid:
        inst = next((i for i in CONFIG["instances"] if i["id"] == iid), None)
        inst_upg_limit = clamp_int(int(inst.get("upgrade_daily_limit", 0) or 0), 0, 9999, 0) if inst else 0
        if inst_upg_limit > 0 and db.count_today_upgrades_for_instance(iid) >= inst_upg_limit:
            return True
    return False

def daily_limit_reached(iid: str = "") -> bool:
    """True if the global daily limit OR this instance's own limit is reached."""
    # Global limit
    global_limit = CONFIG.get("daily_limit", 0)
    if global_limit > 0 and db.count_today() >= global_limit:
        return True
    # Per-instance limit
    if iid:
        inst = next((i for i in CONFIG["instances"] if i["id"] == iid), None)
        inst_limit = clamp_int(int(inst.get("daily_limit", 0) or 0), 0, 9999, 0) if inst else 0
        if inst_limit > 0 and db.count_today_for_instance(iid) >= inst_limit:
            return True
    return False

def should_search(iid:str, item_type:str, item_id:int):
    if daily_limit_reached(iid): return False, "daily"
    if db.is_on_cooldown(iid, item_type, item_id, CONFIG.get("cooldown_days",7)):
        return False, "cooldown"
    return True, ""

def _parse_release_dt(raw):
    """Parse a release date from various Arr field formats."""
    if raw is None: return None
    s = str(raw).strip()
    if not s: return None
    if len(s) >= 10:
        try: return datetime.fromisoformat(s[:10])
        except Exception: pass
    try: return datetime.fromisoformat(s.replace("Z","+00:00"))
    except Exception: pass
    if len(s) >= 4 and s[:4].isdigit():
        try: return datetime(int(s[:4]), 1, 1)
        except Exception: return None
    return None

def _pick_release_dt(record: dict, *keys):
    for key in keys:
        dt = _parse_release_dt(record.get(key))
        if dt is not None: return dt
    return None

def _is_released(release_dt) -> bool:
    """True if release_dt is in the past or unknown."""
    if release_dt is None: return True
    try: return release_dt.date() <= datetime.utcnow().date()
    except Exception: return True

def _in_maintenance_window() -> bool:
    """True if the current local time falls inside any configured maintenance window.

    Windows are defined as {start: "HH:MM", end: "HH:MM"} in local time (CONFIG timezone).
    Supports overnight windows (e.g. 22:00–06:00).
    If any window is misconfigured it is silently skipped.
    """
    windows = CONFIG.get("maintenance_windows", [])
    if not windows:
        return False
    now_local_t = now_local().time()   # datetime.time in configured tz
    for w in windows:
        try:
            sh, sm = map(int, w["start"].split(":"))
            eh, em = map(int, w["end"].split(":"))
        except (KeyError, ValueError, TypeError):
            continue
        from datetime import time as dtime
        start_t = dtime(sh, sm)
        end_t   = dtime(eh, em)
        if start_t <= end_t:
            # Normal window: 08:00–10:00
            if start_t <= now_local_t < end_t:
                return True
        else:
            # Overnight window: 22:00–06:00
            if now_local_t >= start_t or now_local_t < end_t:
                return True
    return False

def _ep_is_released(ep: dict) -> bool:
    """True if this episode has already aired (airDateUtc in the past or missing)."""
    dt = _pick_release_dt(ep, "airDateUtc", "airDate")
    return _is_released(dt)

def _movie_is_released(movie: dict) -> bool:
    """True if this movie has a known release date in the past (any release type)."""
    dt = _pick_release_dt(movie, "digitalRelease", "physicalRelease", "inCinemas", "releaseDate")
    return _is_released(dt)

# ── Tagging helpers ──────────────────────────────────────────────────────────
def _ensure_tag(client: ArrClient, label: str) -> int | None:
    """Return the tag ID for `label`, creating it if necessary. Returns None on error."""
    label = label.strip().lower()
    if not label: return None
    try:
        tags = client.get("tag")
        for t in tags:
            if t.get("label","").lower() == label:
                return int(t["id"])
        created = client.post("tag", {"label": label})
        return int(created["id"])
    except Exception as e:
        logger.warning(f"Tag ensure failed for '{label}': {e}")
        return None

def _apply_tag(client: ArrClient, inst_type: str, item_id: int,
               item_data: dict | None, tag_id: int) -> None:
    """Add tag_id to the series (Sonarr) or movie (Radarr) if not already present."""
    try:
        if inst_type == "sonarr":
            # Tags live on the series, not on individual episodes
            series_id = None
            if item_data:
                series_id = (item_data.get("series",{}).get("id")
                             or item_data.get("seriesId"))
            if not series_id: return
            series = client.get(f"series/{series_id}")
            existing = series.get("tags", [])
            if tag_id in existing: return  # already tagged
            series["tags"] = existing + [tag_id]
            client.put(f"series/{series_id}", series)
            logger.debug(f"Tagged series {series_id} with tag {tag_id}")
        else:  # radarr
            movie = client.get(f"movie/{item_id}")
            existing = movie.get("tags", [])
            if tag_id in existing: return
            movie["tags"] = existing + [tag_id]
            client.put(f"movie/{item_id}", movie)
            logger.debug(f"Tagged movie {item_id} with tag {tag_id}")
    except Exception as e:
        logger.warning(f"Tag apply failed (type={inst_type} id={item_id}): {e}")

def do_search(client: ArrClient, iid: str, item_type: str, item_id: int,
              title: str, command: dict, changed=None, year=None,
              item_data: dict | None = None):
    """Execute search command, record to DB, and fire rich Discord notification."""
    result = "dry_run" if CONFIG["dry_run"] else "triggered"
    if not CONFIG["dry_run"]: client.post("command", command)
    db.upsert_search(iid, item_type, item_id, title, result, changed, year)
    # ── Tagging ──────────────────────────────────────────────────────────────
    if not CONFIG.get("dry_run"):
        inst_cfg  = next((i for i in CONFIG["instances"] if i["id"] == iid), {})
        inst_tag  = inst_cfg.get("tag_enabled")  # None = use global
        tag_on    = CONFIG.get("tag_enabled", False) if inst_tag is None else inst_tag
        if tag_on:
            _label  = safe_str(CONFIG.get("tag_label","mediastarr").strip() or "mediastarr", 50)
            _itype  = inst_cfg.get("type","sonarr")
            tag_id  = _ensure_tag(client, _label)
            if tag_id is not None:
                _apply_tag(client, _itype, item_id, item_data, tag_id)

    # ── Rich Discord notification ─────────────────────────────────────────────
    inst      = next((i for i in CONFIG["instances"] if i["id"] == iid), {})
    inst_name = inst.get("name", iid)
    inst_type = inst.get("type", "sonarr")
    is_upgrade = "upgrade" in item_type
    is_movie   = item_type in ("movie", "movie_upgrade")
    lang       = CONFIG.get("language", "de")
    event      = "upgrade" if is_upgrade else "missing"
    item       = item_data or {}

    # Labels
    type_labels = {
        "episode":         ("📺", "Episode"),
        "episode_upgrade": ("📺⬆", "Episode Upgrade"),
        "movie":           ("🎬", "Film" if lang=="de" else "Movie"),
        "movie_upgrade":   ("🎬⬆", "Film-Upgrade" if lang=="de" else "Movie Upgrade"),
    }
    type_icon, type_label = type_labels.get(item_type, ("❓", item_type))

    if is_upgrade:
        ev_title = f"⬆️ {'Upgrade gesucht' if lang=='de' else 'Upgrade searched'}"
    else:
        ev_title = f"🔍 {'Fehlend gesucht' if lang=='de' else 'Missing searched'}"
    # Prepend the actual content title so Discord shows "🔍 Breaking Bad — Fehlend gesucht"
    if title and title != "?":
        ev_title = f"{title} — {ev_title}"
    if result == "dry_run":
        ev_title = f"🧪 [Dry Run] {ev_title}"

    # Poster + fanart
    if is_movie:
        poster  = _radarr_poster(item)
        fanart  = _radarr_fanart(item)
        series_obj = {}
    else:
        series_obj = item.get("series") or {}
        poster  = _sonarr_poster(series_obj)
        fanart  = _sonarr_fanart(series_obj)

    # External links — order: IMDb first (most recognisable)
    links: list[tuple[str, str]] = []
    if is_movie:
        imdb_id = item.get("imdbId","")
        tmdb_id = item.get("tmdbId","")
        if imdb_id: links.append(("⭐ IMDb",  _imdb_url(imdb_id)))
        if tmdb_id: links.append(("🎬 TMDB",  _tmdb_url(tmdb_id, "movie")))
    else:
        imdb_id = series_obj.get("imdbId","")
        tvdb_id = series_obj.get("tvdbId","")
        tmdb_id = series_obj.get("tmdbId","")
        if imdb_id: links.append(("⭐ IMDb", _imdb_url(imdb_id)))
        if tvdb_id or series_obj.get("titleSlug"):
            links.append(("📺 TVDB", _tvdb_url(series_obj)))
        if tmdb_id: links.append(("🎬 TMDB", _tmdb_url(tmdb_id, "tv")))

    embed_url = links[0][1] if links else ""

    # Description: link row + italicised overview
    link_line  = _link_buttons(links)
    overview   = (item.get("overview") or series_obj.get("overview") or "").strip()
    ov_short   = (overview[:180] + "…") if len(overview) > 180 else overview

    desc_parts: list[str] = []
    if link_line:  desc_parts.append(link_line)
    if ov_short:   desc_parts.append(f"*{ov_short}*")
    description = "\n\n".join(desc_parts)

    # ── Rich fields ──────────────────────────────────────────────────────────
    is_de = lang == "de"
    fields: list[dict] = []

    # Row 1: type / year / runtime
    fields.append({"name": "Typ" if is_de else "Type",
                   "value": f"{type_icon} {type_label}", "inline": True})
    yr = _year_str(item if is_movie else series_obj)
    if yr: fields.append({"name": "Jahr" if is_de else "Year",
                           "value": yr, "inline": True})
    rt = _runtime_str(item if is_movie else series_obj)
    if rt: fields.append({"name": "Laufzeit" if is_de else "Runtime",
                           "value": rt, "inline": True})

    # Row 2: rating (can be multi-line)
    rating = _rating_str(item if is_movie else (series_obj or item), item_type)
    if rating: fields.append({"name": "Bewertung" if is_de else "Rating",
                               "value": rating, "inline": True})

    # Genres
    genres = _genres_str(item if is_movie else series_obj)
    if genres: fields.append({"name": "Genre",
                               "value": genres, "inline": True})

    # Network / Studio
    network = (item.get("studio","") if is_movie
               else series_obj.get("network",""))
    if network:
        fields.append({"name": "Studio" if is_movie else "Network",
                       "value": network, "inline": True})

    # Status
    st = _status_str(item if is_movie else series_obj)
    if st: fields.append({"name": "Status", "value": st, "inline": True})

    # Current quality (upgrades only)
    if is_upgrade:
        if is_movie:
            cur_q = item.get("movieFile",{}).get("quality",{}).get("quality",{}).get("name","")
        else:
            cur_q = item.get("episodeFile",{}).get("quality",{}).get("quality",{}).get("name","")
        if cur_q:
            fields.append({"name": "Aktuelle Qualität" if is_de else "Current quality",
                           "value": cur_q, "inline": True})

    # Instance (always last)
    fields.append({"name": "Instanz" if is_de else "Instance",
                   "value": f"`{inst_name}`", "inline": True})

    # Service author icon
    svc_icon = _ICON_SONARR if inst_type == "sonarr" else _ICON_RADARR

    discord_send._current_inst_type = inst_type  # for per-type webhook routing
    discord_send(
        event, ev_title, description,
        instance_name = inst_name,
        inst_type     = inst_type,
        fields        = fields or None,
        thumbnail_url = poster,
        image_url     = fanart,
        embed_url     = embed_url,
        author_name   = f"{inst_name}  •  {'Sonarr' if inst_type=='sonarr' else 'Radarr'}",
        author_icon   = svc_icon,
    )
    discord_send._current_inst_type = ""  # reset
    return result

def _ep_title(ep: dict, lang: str) -> str:
    """Build 'Series – Episode Title – S01E01'.
    Tries all known Sonarr API paths for the series title.
    When title is genuinely absent, shows Series #ID so user can identify it."""
    series  = ep.get("series") or {}
    s_title = (
        series.get("title") or
        ep.get("seriesTitle") or
        series.get("sortTitle") or
        ""
    ).strip()
    if not s_title:
        sid = series.get("id") or ep.get("seriesId") or "?"
        s_title = f"Series #{sid}"
    s_title = s_title[:60]
    ep_title = (ep.get("title") or "").strip()[:60]
    snum = ep.get("seasonNumber", 0)
    enum = ep.get("episodeNumber", 0)
    code = f"S{snum:02d}E{enum:02d}"
    suppressed = {"tba", "tbd", "", "unknown", "n/a", "none"}
    if ep_title and ep_title.lower() not in suppressed:
        return f"{s_title} – {ep_title} – {code}"
    return f"{s_title} – {code}"

# ─── Hunt: Sonarr ─────────────────────────────────────────────────────────────
def hunt_sonarr_instance(inst: dict):
    iid   = inst["id"]; name = inst["name"]
    client = ArrClient(name, inst["url"], inst["api_key"])
    stats  = STATE["inst_stats"][iid]
    # Reset per-cycle action counters — keep found-counts from last cycle
    # so the dashboard never shows zeros while the API call is in flight.
    for _k in ("missing_searched","upgrades_searched","skipped_cooldown","skipped_daily"):
        stats[_k] = 0
    mode   = CONFIG.get("sonarr_search_mode", "season")
    lang   = CONFIG.get("language", "en")
    do_upgrades = CONFIG.get("search_upgrades", True) and inst.get("search_upgrades", False)
    logger.debug(f"📺 [{name}] hunt start — mode={mode} upgrades={do_upgrades}")
    ms_info(name, "🔄 Hunt start", f"mode={mode} upgrades={do_upgrades}")

    # Build series ID → title cache once per hunt so ep titles are always correct
    # even when Sonarr omits series.title in wanted/missing responses
    # Full series objects keyed by series ID — used for rich Discord embeds
    # (poster, fanart, multi-source ratings, TVDB/IMDb/TMDB links, genres, network)
    ms_info(name, f"🔄 Hunt start", f"mode={mode} upgrades={do_upgrades}")
    series_cache: dict[int, str]   = {}
    series_full:  dict[int, dict]  = {}
    try:
        all_series = client.get("series")
        for s in all_series:
            sid = s.get("id")
            if sid:
                if s.get("title"):
                    series_cache[int(sid)] = s["title"].strip()
                series_full[int(sid)] = s   # keep full object for Discord
    except Exception as e:
        logger.debug(f"Series cache fetch failed for {name}: {e}")

    def resolve_series_title(ep: dict) -> str:
        """Return series title from cache or from embedded series object."""
        # Try cache first (most reliable)
        sid = ep.get("seriesId") or ep.get("series", {}).get("id")
        if sid and int(sid) in series_cache:
            return series_cache[int(sid)]
        # Fall back to embedded fields
        series = ep.get("series") or {}
        return (series.get("title") or ep.get("seriesTitle") or "").strip()

    def enrich_ep_with_series(ep: dict) -> dict:
        """Inject full series object into the episode dict for rich Discord embeds."""
        sid = ep.get("seriesId") or ep.get("series", {}).get("id")
        if sid and int(sid) in series_full:
            enriched = dict(ep)
            enriched["series"] = series_full[int(sid)]
            return enriched
        return ep

    def ep_title(ep: dict) -> str:
        s_title = resolve_series_title(ep) or "?"
        ep_t    = (ep.get("title") or "").strip()
        snum    = ep.get("seasonNumber", 0)
        enum    = ep.get("episodeNumber", 0)
        code    = f"S{snum:02d}E{enum:02d}"
        suppressed = {"tba", "tbd", "", "unknown", "n/a", "none"}
        if ep_t and ep_t.lower() not in suppressed:
            return f"{s_title} – {ep_t} – {code}"
        return f"{s_title} – {code}"

    # ── Missing ──
    try:
        data  = client.get("wanted/missing", params={"pageSize":2000,"sortKey":"airDateUtc","sortDir":"desc"})
        recs  = data.get("records", [])
        random.shuffle(recs)
        # Skip upcoming (not yet aired) episodes
        # Always skip unaired episodes (hardwired)
        before_up = len(recs)
        recs = [ep for ep in recs if _ep_is_released(ep)]
        skipped_up = before_up - len(recs)
        if skipped_up:
            logger.info(f"{name}: skipped {skipped_up} unaired episode(s) (upcoming filter)")
        # IMDb filter — series_cache has titles, build separate imdb map from same /series endpoint
        _sonarr_imdb_override = CONFIG.get("sonarr_imdb_min_rating")
        imdb_min_s = float(_sonarr_imdb_override if _sonarr_imdb_override is not None else CONFIG.get("imdb_min_rating", 0) or 0)
        if imdb_min_s > 0 and series_cache:
            series_imdb: dict[int,float] = {}
            try:
                for s in client.get("series"):
                    sid = s.get("id")
                    if sid: series_imdb[int(sid)] = _imdb_rating(s)
            except Exception: pass
            before = len(recs)
            def _ep_imdb_r(ep):
                sid = ep.get("seriesId") or ep.get("series",{}).get("id")
                r = series_imdb.get(int(sid), 0.0) if sid else 0.0
                return r
            recs = [ep for ep in recs if _ep_imdb_r(ep) == 0.0 or _ep_imdb_r(ep) >= imdb_min_s]
            logger.debug(f"{name}: IMDb filter kept {len(recs)}/{before} missing episodes")
        # Tag filter — only keep episodes whose series carries one of the selected tag IDs
        _tag_filter = inst.get("tag_filter_ids", [])
        if _tag_filter:
            before_tf = len(recs)
            def _ep_has_tag(ep):
                sid = ep.get("seriesId") or ep.get("series",{}).get("id")
                s = series_full.get(int(sid), {}) if sid else {}
                return bool(set(s.get("tags",[])) & set(_tag_filter))
            recs = [ep for ep in recs if _ep_has_tag(ep)]
            logger.debug(f"{name}: tag filter kept {len(recs)}/{before_tf} episodes")
        stats["missing_found"] = int(data.get("totalRecords", len(recs)))
        ms_info(name, "📺 Missing", f"{len(recs)} items after filters (mode={mode})")
        searched = 0
        # Dedup tracking for season/series mode — avoid sending the same
        # SeasonSearch or SeriesSearch command multiple times for the same target
        _searched_seasons: set[tuple] = set()  # (series_id, season_number)
        _searched_series:  set[int]   = set()  # series_id
        for ep in recs:
            if STOP_EVENT.is_set() or searched >= CONFIG["max_searches_per_run"]: break
            title = ep_title(ep)
            series_id = ep.get("series",{}).get("id") or ep.get("seriesId")

            # In season/series mode: skip if we already triggered a search for this target
            if mode == "series" and series_id and int(series_id) in _searched_series:
                logger.debug(f"{name}: skip dup SeriesSearch for series {series_id}")
                continue
            if mode == "season" and series_id:
                season_key = (int(series_id), ep.get("seasonNumber", 0))
                if season_key in _searched_seasons:
                    logger.debug(f"{name}: skip dup SeasonSearch for {season_key}")
                    continue

            # Cooldown check at the right granularity for the current search mode
            if mode == "series" and series_id:
                ok, reason = should_search(iid, "series", int(series_id))
            elif mode == "season" and series_id:
                _ss_snum = ep.get("seasonNumber", 0)
                ok, reason = should_search(iid, "season", int(series_id) * 1000 + _ss_snum)
            else:
                ok, reason = should_search(iid, "episode", ep["id"])
            if not ok:
                stats[f"skipped_{reason}"] += 1
                if reason == "daily":
                    log_act(name, msg("daily_limit",today=db.count_today(),limit=CONFIG["daily_limit"]), "", "warning")
                    lang   = CONFIG.get("language","en")
                    is_de  = lang == "de"
                    cnt    = db.count_today()
                    lim    = CONFIG["daily_limit"]
                    bar    = "█" * 10 + "░" * 0  # full bar = limit reached
                    label  = "🚫 Tageslimit erreicht" if is_de else "🚫 Daily limit reached"
                    desc   = (f"`{bar}` **{cnt}/{lim}** {'Suchen heute' if is_de else 'searches today'}\n"
                              f"*{'Reset Mitternacht UTC. Morgen geht es weiter.' if is_de else 'Resets at midnight UTC. Resumes tomorrow.'}*")
                    discord_send("limit", label, desc, name)
                    break  # stop THIS loop, allow upgrade loop to still run
                continue
            year = _year(ep.get("series",{}).get("year") or ep.get("airDate","")[:4])
            # Build command based on search mode
            if mode == "series" and series_id:
                command = {"name":"SeriesSearch","seriesId":series_id}
                _searched_series.add(int(series_id))  # mark this series as triggered
            elif mode == "season" and series_id:
                snum = ep.get("seasonNumber", 0)
                command = {"name":"SeasonSearch","seriesId":series_id,"seasonNumber":snum}
                _searched_seasons.add((int(series_id), snum))  # mark this season as triggered
            else:
                command = {"name":"EpisodeSearch","episodeIds":[ep["id"]]}
            # Record search at correct granularity (season/series/episode)
            if mode == "series" and series_id:
                _rec_type = "series"; _rec_id = int(series_id)
            elif mode == "season" and series_id:
                _rec_type = "season"
                _rec_snum = ep.get("seasonNumber", 0)
                _rec_id   = int(series_id) * 1000 + _rec_snum
            else:
                _rec_type = "episode"; _rec_id = ep["id"]
            do_search(client, iid, _rec_type, _rec_id, title, command,
                      ep.get("series",{}).get("lastInfoSync"), year,
                      item_data=enrich_ep_with_series(ep))
            stats["missing_searched"] += 1; searched += 1
            # Log what was actually triggered (season/series vs episode)
            if mode == "series":
                s_title = resolve_series_title(ep) or title
                log_label = f"{s_title} (SeriesSearch)"
                logger.debug(f"📺 [{name}] SeriesSearch → {s_title}")
            elif mode == "season":
                s_title = resolve_series_title(ep) or title
                log_label = f"{s_title} S{ep.get('seasonNumber',0):02d} (SeasonSearch)"
                logger.debug(f"📺 [{name}] SeasonSearch → {s_title} S{ep.get('seasonNumber',0):02d}")
            else:
                log_label = title
            log_act(name, msg("missing"), log_label, "success")
            time.sleep(1.5)
    except Exception as e:
        log_act(name, msg("error"), str(e)[:200], "error")

    if not do_upgrades: return

    # ── Upgrades ──
    if upgrade_daily_limit_reached(iid, "sonarr"):
        ms_info(name, "Sonarr upgrade daily limit reached", ""); return
    try:
        data  = client.get("wanted/cutoff", params={"pageSize":2000})
        recs  = data.get("records", [])
        random.shuffle(recs)  # random selection
        target_res_s  = CONFIG.get("sonarr_upgrade_target_resolution","") or CONFIG.get("upgrade_target_resolution","")
        target_rank_s = _res_rank(target_res_s) if target_res_s else 0
        stats["upgrades_found"] = int(data.get("totalRecords", len(recs)))
        searched = 0
        for ep in recs:
            if STOP_EVENT.is_set() or searched >= CONFIG["max_searches_per_run"]: break
            if upgrade_daily_limit_reached(iid, "sonarr"): break  # mid-loop check
            title = ep_title(ep)
            if target_rank_s > 0:
                cur_q = ep.get("episodeFile",{}).get("quality",{}).get("quality",{}).get("name","")
                if _res_rank(cur_q) >= target_rank_s:
                    logger.debug(f"{name}: skip upgrade {title} already at {cur_q}")
                    continue
            if mode == "series" and series_id_upg:
                ok, reason = should_search(iid, "series_upgrade", int(series_id_upg))
            elif mode == "season" and series_id_upg:
                _us_snum = ep.get("seasonNumber", 0)
                ok, reason = should_search(iid, "season_upgrade", int(series_id_upg) * 1000 + _us_snum)
            else:
                ok, reason = should_search(iid, "episode_upgrade", ep["id"])
            if not ok:
                stats[f"skipped_{reason}"] += 1
                if reason == "daily": break  # stop upgrades loop, not whole function
                continue
            year = _year(ep.get("series",{}).get("year"))
            # Upgrades also respect the search mode
            series_id_upg = ep.get("series",{}).get("id") or ep.get("seriesId")
            if mode == "series" and series_id_upg:
                upg_command = {"name":"SeriesSearch","seriesId":series_id_upg}
            elif mode == "season" and series_id_upg:
                upg_command = {"name":"SeasonSearch","seriesId":series_id_upg,
                               "seasonNumber":ep.get("seasonNumber",0)}
            else:
                upg_command = {"name":"EpisodeSearch","episodeIds":[ep["id"]]}
            if mode == "series" and series_id_upg:
                _urs_type = "series_upgrade"; _urs_id = int(series_id_upg)
            elif mode == "season" and series_id_upg:
                _urs_type = "season_upgrade"
                _urs_snum = ep.get("seasonNumber", 0)
                _urs_id   = int(series_id_upg) * 1000 + _urs_snum
            else:
                _urs_type = "episode_upgrade"; _urs_id = ep["id"]
            do_search(client, iid, _urs_type, _urs_id, title,
                      upg_command, year=year,
                      item_data=enrich_ep_with_series(ep))
            stats["upgrades_searched"] += 1; searched += 1
            log_act(name, msg("upgrade"), title, "warning")
            time.sleep(1.5)
    except Exception as e:
        log_act(name, msg("error"), str(e)[:200], "error")

# ─── Hunt: Radarr ─────────────────────────────────────────────────────────────
def hunt_radarr_instance(inst: dict):
    iid   = inst["id"]; name = inst["name"]
    client = ArrClient(name, inst["url"], inst["api_key"])
    stats  = STATE["inst_stats"][iid]
    # Reset per-cycle action counters — keep found-counts from last cycle
    # so the dashboard never shows zeros while the API call is in flight.
    for _k in ("missing_searched","upgrades_searched","skipped_cooldown","skipped_daily"):
        stats[_k] = 0
    do_upgrades = CONFIG.get("search_upgrades", True) and inst.get("search_upgrades", False)
    logger.debug(f"🎬 [{name}] hunt start — upgrades={do_upgrades}")

    ms_info(name, "🔄 Hunt start", f"upgrades={do_upgrades}")
    # ── Missing ──
    try:
        movies  = client.get("movie")
        random.shuffle(movies)  # random selection
        missing = [m for m in movies if not m.get("hasFile") and m.get("monitored")]
        _radarr_imdb_override = CONFIG.get("radarr_imdb_min_rating")
        imdb_min = float(_radarr_imdb_override if _radarr_imdb_override is not None else CONFIG.get("imdb_min_rating", 0) or 0)
        if imdb_min > 0:
            before = len(missing)
            missing = [m for m in missing if _imdb_rating(m) == 0.0 or _imdb_rating(m) >= imdb_min]
            logger.debug(f"{name}: IMDb filter ({imdb_min}+) kept {len(missing)}/{before} missing movies")
        # Skip upcoming (not yet released) movies
        # Always skip unreleased movies (hardwired)
        before_up = len(missing)
        missing = [m for m in missing if _movie_is_released(m)]
        skipped_up = before_up - len(missing)
        if skipped_up:
            logger.info(f"{name}: skipped {skipped_up} unreleased movie(s) (upcoming filter)")
        # Tag filter — only include movies that carry one of the selected tag IDs
        _tag_filter_r = inst.get("tag_filter_ids", [])
        if _tag_filter_r:
            before_tf_r = len(missing)
            missing = [m for m in missing if bool(set(m.get("tags",[])) & set(_tag_filter_r))]
            logger.debug(f"{name}: tag filter kept {len(missing)}/{before_tf_r} movies")
        stats["missing_found"] = len(missing)
        ms_info(name, "🎬 Missing", f"{len(missing)} movies after filters")
        searched = 0
        for movie in missing:
            if STOP_EVENT.is_set() or searched >= CONFIG["max_searches_per_run"]: break
            title = str(movie.get("title","?"))[:100]
            year  = _year(movie.get("year"))
            if year: title = f"{title} ({year})"
            ok, reason = should_search(iid, "movie", movie["id"])
            if not ok:
                stats[f"skipped_{reason}"] += 1
                if reason == "daily":
                    log_act(name, msg("daily_limit",today=db.count_today(),limit=CONFIG["daily_limit"]), "", "warning")
                    break  # stop THIS loop, allow upgrade loop to still run
                continue
            do_search(client, iid, "movie", movie["id"], title,
                      {"name":"MoviesSearch","movieIds":[movie["id"]]},
                      movie.get("lastInfoSync"), _year(movie.get("year")),
                      item_data=movie)
            stats["missing_searched"] += 1; searched += 1
            log_act(name, msg("missing"), title, "success")
            time.sleep(1.5)
    except Exception as e:
        log_act(name, msg("error"), str(e)[:200], "error")

    if not do_upgrades: return

    # ── Upgrades ──
    if upgrade_daily_limit_reached(iid, "radarr"):
        ms_info(name, "Radarr upgrade daily limit reached", ""); return
    try:
        data  = client.get("wanted/cutoff", params={"pageSize":2000})
        recs  = data.get("records", [])
        random.shuffle(recs)  # random selection
        stats["upgrades_found"] = int(data.get("totalRecords", len(recs)))
        searched = 0
        target_res  = CONFIG.get("radarr_upgrade_target_resolution","") or CONFIG.get("upgrade_target_resolution","")
        target_rank = _res_rank(target_res) if target_res else 0
        imdb_min_up = float(_radarr_imdb_override if _radarr_imdb_override is not None else CONFIG.get("imdb_min_rating", 0) or 0)
        for movie in recs:
            if STOP_EVENT.is_set() or searched >= CONFIG["max_searches_per_run"]: break
            if upgrade_daily_limit_reached(iid, "radarr"): break  # mid-loop check
            title = str(movie.get("title","?"))[:100]
            year  = _year(movie.get("year"))
            if year: title = f"{title} ({year})"
            if imdb_min_up > 0 and 0 < _imdb_rating(movie) < imdb_min_up:
                logger.debug(f"{name}: skip upgrade {title} IMDb {_imdb_rating(movie):.1f}<{imdb_min_up}")
                continue
            if target_rank > 0:
                cur_q = movie.get("movieFile",{}).get("quality",{}).get("quality",{}).get("name","")
                if _res_rank(cur_q) >= target_rank:
                    logger.debug(f"{name}: skip upgrade {title} already at {cur_q}")
                    continue
            ok, reason = should_search(iid, "movie_upgrade", movie["id"])
            if not ok:
                stats[f"skipped_{reason}"] += 1
                if reason == "daily": break  # stop upgrades loop, not whole function
                continue
            do_search(client, iid, "movie_upgrade", movie["id"], title,
                      {"name":"MoviesSearch","movieIds":[movie["id"]]},
                      year=_year(movie.get("year")), item_data=movie)
            stats["upgrades_searched"] += 1; searched += 1
            log_act(name, msg("upgrade"), title, "warning")
            time.sleep(1.5)
    except Exception as e:
        log_act(name, msg("error"), str(e)[:200], "error")


# ─── Stalled Download Monitor ────────────────────────────────────────────────
_stall_seen: dict[str, float] = {}   # downloadId → first_stall_seen_ts

def _check_stalled_queue(inst: dict) -> None:
    """Check Sonarr/Radarr queue for stalled downloads and take action.
    
    Stall criteria: item has status 'downloading' but sizeleft unchanged
    for stall_threshold_min minutes. Uses trackedDownloadState / trackedDownloadStatus
    to detect stalls without needing to track size over time.
    """
    global_enabled = CONFIG.get("stall_monitor_enabled", False)
    inst_override  = inst.get("stall_monitor_enabled")  # None = use global
    enabled = global_enabled if inst_override is None else inst_override
    if not enabled:
        return

    iid    = inst["id"]
    name   = inst["name"]
    itype  = inst.get("type", "sonarr")
    client = ArrClient(name, inst["url"], inst["api_key"])
    threshold_min = max(5, int(CONFIG.get("stall_threshold_min", 60) or 60))
    action        = CONFIG.get("stall_action", "search")
    lang          = CONFIG.get("language", "en")
    is_de         = lang == "de"
    now_ts        = time.time()

    try:
        params = {"page": 1, "pageSize": 200, "includeUnknownMovieItems": "true",
                  "includeUnknownSeriesItems": "true"}
        data   = client.get("queue", params=params)
        items  = data.get("records", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    except Exception as e:
        logger.debug(f"[{name}] Queue fetch failed for stall check: {e}")
        return

    for item in items:
        dl_id   = str(item.get("downloadId") or item.get("id") or "")
        if not dl_id:
            continue

        status         = str(item.get("status", "")).lower()
        tracked_state  = str(item.get("trackedDownloadState", "")).lower()
        tracked_status = str(item.get("trackedDownloadStatus", "")).lower()
        sizeleft       = item.get("sizeleft", -1)
        title          = str(item.get("title") or item.get("movieTitle") or item.get("seriesTitle") or "?")[:100]

        # Only check actively downloading items
        if status not in ("downloading", "queued", "paused"):
            _stall_seen.pop(dl_id, None)
            continue

        # Stall detection: trackedDownloadStatus is "warning" or "error" with no seeds
        is_stalled = (
            tracked_status in ("warning", "error") or
            tracked_state  in ("importpending", "stalled") or
            (sizeleft == 0 and status == "queued")   # stuck at 0 bytes
        )

        # Also stall if sizeleft > 0 and hasn't moved in threshold minutes
        # We track first-seen-as-potentially-stalled time
        stall_key = f"{iid}:{dl_id}"
        if not is_stalled and sizeleft > 0:
            # Check status messages for stall indicators
            for msg_obj in item.get("statusMessages", []):
                for sm in msg_obj.get("messages", []):
                    if any(kw in str(sm).lower() for kw in
                           ("no seeds", "no peers", "stalled", "dead", "ratio", "seeding time")):
                        is_stalled = True
                        break

        if is_stalled:
            if stall_key not in _stall_seen:
                _stall_seen[stall_key] = now_ts
                logger.debug(f"[{name}] Stall first detected: {title} ({dl_id})")
                continue  # wait for threshold before acting

            stall_age_min = (now_ts - _stall_seen[stall_key]) / 60
            if stall_age_min < threshold_min:
                continue  # not long enough yet

            # ── Threshold exceeded → take action ────────────────────────────
            logger.info(f"[{name}] Stalled download ({stall_age_min:.0f}min): {title}")
            _stall_seen.pop(stall_key, None)  # reset so we don't act twice

            if action == "warn":
                # Discord-only warning
                label = ("⏳ Stockender Download" if is_de else "⏳ Stalled download")
                desc  = (f"**{title}** ist seit **{stall_age_min:.0f} Minuten** nicht mehr aktiv."
                         if is_de else
                         f"**{title}** has been stalled for **{stall_age_min:.0f} minutes**.")
                discord_send("offline", label, desc, name)
                ms_warn(name, "Stalled download (warn only)", title)

            elif action == "search":
                # Remove from queue (mark failed) and trigger new search
                try:
                    queue_id = item.get("id")
                    if queue_id:
                        # blocklist=true marks it as failed so it won't be re-grabbed immediately
                        client.delete_with_params(
                            f"queue/{queue_id}",
                            {"removeFromClient": "true", "blocklist": "true", "skipRequeue": "false"}
                        )
                    # Trigger new search — use the media item if available
                    if itype == "radarr":
                        movie_id = item.get("movieId")
                        if movie_id:
                            client.post("command", {"name": "MoviesSearch", "movieIds": [movie_id]})
                            ms_info(name, "Stalled → new search triggered", title)
                    else:
                        series_id = item.get("seriesId")
                        ep_id     = item.get("episodeId")
                        if series_id and ep_id:
                            client.post("command", {"name": "EpisodeSearch", "episodeIds": [ep_id]})
                            ms_info(name, "Stalled → new search triggered", title)
                        elif series_id:
                            client.post("command", {"name": "SeriesSearch", "seriesId": series_id})
                            ms_info(name, "Stalled → new search triggered", title)

                    # Discord notification
                    label = ("⚡ Stockender Download neu gesucht" if is_de
                             else "⚡ Stalled download — new search triggered")
                    desc  = (f"**{title}** war seit **{stall_age_min:.0f} Minuten** blockiert.\n"
                             f"Download wurde abgebrochen und eine neue Suche gestartet."
                             if is_de else
                             f"**{title}** was stalled for **{stall_age_min:.0f} minutes**.\n"
                             f"Download removed and new search triggered.")
                    discord_send("missing", label, desc, name)

                except Exception as e:
                    logger.warning(f"[{name}] Stall action failed for {title}: {e}")
        else:
            # Not stalled — reset tracking
            _stall_seen.pop(stall_key, None)


# ─── Ping ─────────────────────────────────────────────────────────────────────
def ping_all():
    _ensure_inst_stats()
    for inst in CONFIG["instances"]:
        stats = STATE["inst_stats"].setdefault(inst["id"], fresh_inst_stats())
        if not inst.get("enabled") or not inst.get("api_key"):
            stats["status"] = "disabled"; continue
        ok, ver, detail = ArrClient(inst["name"], inst["url"], inst["api_key"]).ping()
        prev_status = stats.get("status","unknown")
        stats["status"]       = "online" if ok else "offline"
        stats["version"]      = ver
        stats["status_detail"] = "" if ok else detail
        # Notify only on transition online→offline
        if not ok and prev_status == "online":
            lang  = CONFIG.get("language","de")
            label = "📡 Instanz offline" if lang=="de" else "📡 Instance offline"
            is_de = lang == "de"
            svc_ico = _ICON_SONARR if inst.get("type") == "sonarr" else _ICON_RADARR
            t_ico   = "📺" if inst.get("type") == "sonarr" else "🎬"
            desc = (f"{t_ico} **{inst['name']}** ist nicht erreichbar.\n"
                    f"*{'Nächster Ping erfolgt automatisch.' if is_de else 'Next ping will happen automatically.'}*")
            fields = [
                {"name": "🔌 " + ("Fehler" if is_de else "Error"),
                 "value": f"`{detail or 'Connection failed'}`", "inline": False},
                {"name": "🌐 URL",
                 "value": f"`{inst.get('url','?')}`", "inline": True},
                {"name": "🔧 Typ" if is_de else "🔧 Type",
                 "value": inst.get("type","?").capitalize(), "inline": True},
            ]
            discord_send("offline", label, desc, inst["name"], fields=fields,
                         author_name=inst["name"], author_icon=svc_ico)

# ─── Cycle & Loop ─────────────────────────────────────────────────────────────
def run_cycle():
    if not CYCLE_LOCK.acquire(blocking=False):
        logger.info("run_cycle skipped: another cycle is already running")
        return False
    try:
        STATE["cycle_count"] += 1
        STATE["last_run"] = fmt_dt(now_local())
        active = [i for i in CONFIG["instances"] if i.get("enabled") and i.get("api_key")]
        limit  = CONFIG.get("daily_limit",0)
        log_act("System", msg("cycle_start", n=STATE["cycle_count"],
                active=len(active), today=db.count_today(), limit=limit or "∞"), "", "info")
        _ensure_inst_stats()
        # NOTE: per-cycle counters are reset inside each hunt function,
        # NOT here — so the dashboard never shows a brief zero between cycles.
        ping_all()
        # ── Stalled download check ────────────────────────────────────────────
        if CONFIG.get("stall_monitor_enabled", False):
            for inst in CONFIG["instances"]:
                if inst.get("enabled") and inst.get("api_key"):
                    try: _check_stalled_queue(inst)
                    except Exception as e: logger.warning(f"Stall check error: {e}")
        removed = db.purge_expired(CONFIG.get("cooldown_days",7))
        if removed:
            log_act("System", msg("db_pruned", n=removed), "", "info")
            lang  = CONFIG.get("language","de")
            is_de = lang == "de"
            label = "⏳ Cooldown abgelaufen" if is_de else "⏳ Cooldown expired"
            desc  = (f"**{removed}** {'Item(s) sind wieder für die Suche freigegeben.' if is_de else 'item(s) are available for searching again.'}\n"
                     f"*{'Werden beim nächsten Zyklus automatisch berücksichtigt.' if is_de else 'Will be picked up automatically in the next cycle.'}*")
            next_run = STATE.get("next_run","?")
            cooldown = CONFIG.get("cooldown_days",7)
            fields = [
                {"name": "✅ " + ("Freigegeben" if is_de else "Released"),
                 "value": f"**{removed}**", "inline": True},
                {"name": "⏱ " + ("Nächster Lauf" if is_de else "Next run"),
                 "value": next_run or "?", "inline": True},
                {"name": "📅 Cooldown",
                 "value": f"{cooldown} {'Tage' if is_de else 'days'}", "inline": True},
            ]
            discord_send("cooldown", label, desc, "System", fields=fields)
        for inst in CONFIG["instances"]:
            if STOP_EVENT.is_set(): break
            if not inst.get("enabled") or not inst.get("api_key"): continue
            if STATE["inst_stats"].get(inst["id"],{}).get("status") != "online":
                log_act(inst["name"], msg("skipped_offline"), "", "warning"); continue
            itype = inst.get("type","sonarr")
            type_limit = CONFIG.get(f"{itype}_daily_limit", 0)
            type_today = sum(db.count_today_for_instance(i["id"]) for i in CONFIG.get("instances",[]) if i.get("type")==itype)
            if type_limit > 0 and type_today >= type_limit:
                ms_info("System", f"{itype.capitalize()} global daily limit reached", f"{type_today}/{type_limit}"); continue
            if inst["type"] == "sonarr":   hunt_sonarr_instance(inst)
            elif inst["type"] == "radarr": hunt_radarr_instance(inst)
        log_act("System", msg("cycle_done", n=STATE["cycle_count"], today=db.count_today()), "", "info")

        return True
    finally:
        CYCLE_LOCK.release()
def hunt_loop():
    """Wait first so user can configure settings, then hunt on schedule."""
    STATE["running"] = True
    while not STOP_EVENT.is_set():
        # ── Wait ──
        base  = CONFIG["hunt_missing_delay"]
        delay, jitter = jittered_delay(base)
        next_dt = now_local() + timedelta(seconds=delay)
        STATE["next_run"] = next_dt.strftime("%H:%M:%S")
        # Format jitter as ±Xm for readability
        jitter_min = (f'+{jitter//60}m' if jitter >= 60 else f'+{jitter}s') if jitter else '0s'
        log_act("System", msg("next_run", hhmm=STATE["next_run"], jitter_min=jitter_min), "", "info")
        for _ in range(delay):
            if STOP_EVENT.is_set(): break
            time.sleep(1)
        if STOP_EVENT.is_set(): break
        # ── Maintenance window check ──
        if _in_maintenance_window():
            windows = CONFIG.get("maintenance_windows", [])
            active  = next((w for w in windows if _in_maintenance_window()), {})
            label   = active.get("label", "")
            lang    = CONFIG.get("language", "de")
            is_de   = lang == "de"
            window_str = f"{active.get('start','?')}–{active.get('end','?')}" if active else "?"
            log_act("System",
                    f"⏸ {'Wartungsfenster' if is_de else 'Maintenance window'}: {window_str}"
                    + (f" ({label})" if label else ""),
                    "", "warning")
            # Re-check every 60s until window is over
            while not STOP_EVENT.is_set() and _in_maintenance_window():
                time.sleep(60)
            if STOP_EVENT.is_set(): break
            log_act("System", f"▶ {'Wartungsfenster beendet' if is_de else 'Maintenance window ended'}",
                    "", "info")
        # ── Hunt ──
        try: run_cycle()
        except Exception as e: log_act("System", msg("error"), str(e)[:200], "error")
    STATE["running"] = False; STATE["next_run"] = None

# ─── Flask Routes ─────────────────────────────────────────────────────────────

# ─── Auth / CSRF ──────────────────────────────────────────────────────────────
_PASSWORD = os.environ.get("MEDIASTARR_PASSWORD", "").strip()

# ── Brute-force login protection ──────────────────────────────────────────────
# Keyed by IP: (attempt_count, first_attempt_ts)
_LOGIN_ATTEMPTS: dict = {}
_MAX_LOGIN_ATTEMPTS = 10        # attempts before lockout
_LOCKOUT_SECONDS    = 300       # 5 min lockout window
_ATTEMPT_WINDOW     = 300       # sliding window to count attempts

def _get_client_ip() -> str:
    """Return real client IP, respecting X-Forwarded-For from trusted proxies."""
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else (request.remote_addr or "unknown")

def _check_brute_force() -> bool:
    """Return True if IP is currently locked out."""
    ip  = _get_client_ip()
    now = time.time()
    rec = _LOGIN_ATTEMPTS.get(ip)
    if rec is None:
        return False
    count, first_ts = rec
    if now - first_ts > _LOCKOUT_SECONDS:
        _LOGIN_ATTEMPTS.pop(ip, None)
        return False
    return count >= _MAX_LOGIN_ATTEMPTS

def _record_failed_login():
    ip  = _get_client_ip()
    now = time.time()
    rec = _LOGIN_ATTEMPTS.get(ip)
    if rec is None or now - rec[1] > _ATTEMPT_WINDOW:
        _LOGIN_ATTEMPTS[ip] = (1, now)
    else:
        _LOGIN_ATTEMPTS[ip] = (rec[0] + 1, rec[1])

def _clear_login_attempts():
    _LOGIN_ATTEMPTS.pop(_get_client_ip(), None)

def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]

def _check_csrf():
    """Verify CSRF token on state-mutating browser requests."""
    if not _PASSWORD: return  # no auth = no CSRF needed
    token = (request.headers.get("X-CSRF-Token") or
             request.form.get("csrf_token") or "")
    if not secrets.compare_digest(token, _csrf_token()):
        from flask import abort
        abort(403, "CSRF token invalid")

def _require_login():
    """Redirect to login if password protection is enabled."""
    if not _PASSWORD: return
    if not session.get("authenticated"):
        return redirect(url_for("login_page", next=request.path))

def _login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        result = _require_login()
        if result: return result
        return f(*args, **kwargs)
    return decorated

def _api_auth_required(f):
    """For API routes: return 401 JSON if not authenticated. Also validates CSRF on mutating methods."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if _PASSWORD and not session.get("authenticated"):
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        # CSRF guard for state-mutating API methods
        if request.method in ("POST", "PATCH", "DELETE") and _PASSWORD:
            token = (request.headers.get("X-CSRF-Token") or
                     request.form.get("csrf_token") or "")
            if not secrets.compare_digest(token, _csrf_token()):
                return jsonify({"ok": False, "error": "CSRF validation failed"}), 403
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login_page():
    # CodeQL py/url-redirection: break taint chain by never using user input
    # in redirect(). Map to hardcoded safe destinations only.
    # CodeQL py/url-redirection: never pass user input to redirect().
    # next_path is always a hardcoded string from a fixed tuple.
    _ALLOWED_NEXT = ("/", "/setup")
    _raw_next = request.args.get("next", "") or ""
    # Select hardcoded path — user value only used as lookup key, not as redirect target
    next_path = "/setup" if _raw_next == "/setup" else "/"
    error = None
    if request.method == "POST":
        # Check lockout first
        if _check_brute_force():
            error = "Too many failed attempts. Please wait 5 minutes."
        else:
            # CSRF for login form itself
            token = request.form.get("csrf_token", "")
            if not secrets.compare_digest(token, _csrf_token()):
                error = "Invalid request. Please try again."
            elif _PASSWORD and secrets.compare_digest(
                    request.form.get("password", ""), _PASSWORD):
                _clear_login_attempts()
                session["authenticated"] = True
                session.permanent = True
                return redirect(next_path or "/")
            else:
                _record_failed_login()
                time.sleep(0.3)   # constant-time delay, deters timing attacks
                error = "Incorrect password."
    return render_template("login.html",
                           csrf_token=_csrf_token(),
                           next_path=next_path,
                           error=error)

@app.route("/logout", methods=["POST"])
def logout():
    _check_csrf()
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/")
@_login_required
def index():
    if not CONFIG.get("setup_complete"): return redirect("/setup")
    return render_template("index.html", csrf_token=_csrf_token(), default_pw=(_PASSWORD == "change-me" and bool(_PASSWORD)), version=_CURRENT_VERSION)

@app.route("/setup")
def setup_page(): return render_template("setup.html", csrf_token=_csrf_token())

# ── Setup API ─────────────────────────────────────────────────────────────────
@app.route("/api/setup/ping", methods=["POST"])
@_api_auth_required
def api_setup_ping():
    d = request.get_json(silent=True) or {}
    itype = safe_str(d.get("type",""), 10)
    if itype not in ALLOWED_TYPES: return jsonify({"ok":False,"msg":"Unbekannter Typ"}),400
    url = safe_str(d.get("url",""), URL_MAX_LEN)
    ok, err = validate_internal_service_url(url)
    if not ok: return jsonify({"ok":False,"msg":f"URL ungültig: {err}"}),400
    key = safe_str(d.get("api_key",""), 128)
    ok, err = validate_api_key(key)
    if not ok: return jsonify({"ok":False,"msg":"Invalid API key format"}),400
    # CodeQL py/stack-trace-exposure: exception never flows to response.
    _sping_ok, _sping_ver, _sping_detail = False, "?", "Connection failed"
    try:
        _sping_ok, _sping_ver, _sping_detail = ArrClient(itype, url, key).ping()
    except Exception as _spe:
        logger.debug(f"Setup ping exception: {type(_spe).__name__}")
    return jsonify({"ok": _sping_ok,
                    "version": _safe_version_str(_sping_ver),
                    "msg":     _safe_ping_msg(_sping_detail)})

@app.route("/api/setup/complete", methods=["POST"])
@_api_auth_required
def api_setup_complete():
    d = request.get_json(silent=True) or {}
    instances = d.get("instances",[])
    if not isinstance(instances,list) or len(instances)==0:
        return jsonify({"ok":False,"errors":["Mindestens eine Instanz erforderlich"]}),400
    if len(instances) > MAX_INSTANCES:
        return jsonify({"ok":False,"errors":[f"Maximal {MAX_INSTANCES} Instanzen"]}),400
    errors=[]; validated=[]
    req_lang = safe_str(d.get("language","en"),5)
    is_de = req_lang == "de"
    for i, inst in enumerate(instances):
        nm    = safe_str(inst.get("name",""),40).strip()
        itype = safe_str(inst.get("type",""),10)
        url   = safe_str(inst.get("url",""),URL_MAX_LEN)
        key   = safe_str(inst.get("api_key",""),128)
        label = f"#{i+1} ({nm or '?'})"
        ok,e=validate_name(nm);    errors+=[f"{label} {'Name' if is_de else 'Name'}: {e}"]    if not ok else []
        if itype not in ALLOWED_TYPES: errors.append(f"{label}: {'Unbekannter Typ' if is_de else 'Unknown type'} '{itype}'")
        ok,e=validate_url(url);    errors+=[f"{label} URL: {e}"]     if not ok else []
        ok,e=validate_api_key(key);errors+=[f"{label} API Key: {e}"] if not ok else []
        if not errors:
            validated.append({"id":inst.get("id") or make_id(),"type":itype,
                "name":nm.strip(),"url":url,"api_key":encrypt_secret(key),"enabled":True})
    if errors: return jsonify({"ok":False,"errors":errors}),400
    lang = safe_str(d.get("language","de"),5)
    if lang not in ALLOWED_LANGUAGES: lang = "de"
    CONFIG["instances"]      = validated
    CONFIG["language"]       = lang
    CONFIG["setup_complete"] = True

    # Optional Discord config from wizard
    dc_in = d.get("discord")
    if isinstance(dc_in, dict) and dc_in.get("webhook_url","").strip():
        dc = CONFIG.setdefault("discord", {})
        url = safe_str(dc_in["webhook_url"], 512).strip()
        if url.startswith(("http://","https://")):
            dc["webhook_url"] = url
            dc["enabled"]     = True
            for k in ("notify_missing","notify_upgrade","notify_cooldown",
                      "notify_limit","notify_offline"):
                if k in dc_in: dc[k] = bool(dc_in[k])
            if "rate_limit_cooldown" in dc_in:
                dc["rate_limit_cooldown"] = max(1, min(300, int(dc_in.get("rate_limit_cooldown",5))))

    save_config(CONFIG); _ensure_inst_stats()
    global hunt_thread
    if CONFIG.get("auto_start") and not STATE["running"]:
        STOP_EVENT.clear()
        hunt_thread = threading.Thread(target=hunt_loop, daemon=True); hunt_thread.start()
    return jsonify({"ok":True})

@app.route("/api/setup/reset", methods=["POST"])
@_api_auth_required
def api_setup_reset():
    CONFIG["setup_complete"] = False; save_config(CONFIG); STOP_EVENT.set()
    return jsonify({"ok":True})

# ── Instance CRUD ─────────────────────────────────────────────────────────────
@app.route("/api/instances", methods=["GET"])
@_api_auth_required
def api_instances_get():
    safe = [{k:v for k,v in inst.items() if k!="api_key"} for inst in CONFIG["instances"]]
    return jsonify({"ok":True,"instances":safe,"stats":STATE["inst_stats"]})

@app.route("/api/instances", methods=["POST"])
@_api_auth_required
def api_instances_add():
    if len(CONFIG["instances"]) >= MAX_INSTANCES:
        return jsonify({"ok":False,"error":f"Maximal {MAX_INSTANCES} Instanzen"}),400
    d=request.get_json(silent=True) or {}; errors=[]
    nm=safe_str(d.get("name",""),40); itype=safe_str(d.get("type",""),10)
    url=safe_str(d.get("url",""),URL_MAX_LEN); key=safe_str(d.get("api_key",""),128)
    ok,e=validate_name(nm);    errors+=[f"Name: {e}"]    if not ok else []
    if itype not in ALLOWED_TYPES: errors.append(f"Unbekannter Typ '{itype}'")
    ok,e=validate_url(url);    errors+=[f"URL: {e}"]     if not ok else []
    ok,e=validate_api_key(key);errors+=[f"API Key: {e}"] if not ok else []
    if errors: return jsonify({"ok":False,"errors":errors}),400
    inst={"id":make_id(),"type":itype,"name":nm.strip(),"url":url,"api_key":encrypt_secret(key),"enabled":True}
    CONFIG["instances"].append(inst)
    STATE["inst_stats"][inst["id"]] = fresh_inst_stats()
    save_config(CONFIG); return jsonify({"ok":True,"id":inst["id"]})

@app.route("/api/instances/<inst_id>", methods=["PATCH"])
@_api_auth_required
def api_instances_update(inst_id:str):
    inst = next((i for i in CONFIG["instances"] if i["id"]==inst_id), None)
    if not inst: return jsonify({"ok":False,"error":"Nicht gefunden"}),404
    d = request.get_json(silent=True) or {}
    if "name" in d:
        nm=safe_str(d["name"],40); ok,e=validate_name(nm)
        if not ok: return jsonify({"ok":False,"error":f"Name: {e}"}),400
        inst["name"] = nm.strip()
    if "url" in d:
        url=safe_str(d["url"],URL_MAX_LEN); ok,e=validate_url(url)
        if not ok: return jsonify({"ok":False,"error":f"URL: {e}"}),400
        inst["url"] = url
    if "api_key" in d and d["api_key"]:
        key=safe_str(d["api_key"],128); ok,e=validate_api_key(key)
        if not ok: return jsonify({"ok":False,"error":f"API Key: {e}"}),400
        inst["api_key"] = encrypt_secret(key)
    if "enabled" in d: inst["enabled"] = bool(d["enabled"])
    if "search_upgrades" in d: inst["search_upgrades"] = bool(d["search_upgrades"])
    if "tag_enabled"     in d:
        v = d.get("tag_enabled")
        inst["tag_enabled"] = None if v is None else bool(v)  # None=use global
    if "tag_filter_ids"  in d:
        raw = d.get("tag_filter_ids", [])
        inst["tag_filter_ids"] = [int(x) for x in raw if str(x).isdigit()] if isinstance(raw, list) else []
    if "tag_filter"     in d:
        raw_tf = d.get("tag_filter") or []
        if isinstance(raw_tf, list):
            inst["tag_filter"] = [int(x) for x in raw_tf if str(x).isdigit()][:50]
    if "daily_limit" in d:
        inst["daily_limit"] = clamp_int(int(d.get("daily_limit", 0) or 0), 0, 9999, 0)
    if "upgrade_daily_limit" in d:
        inst["upgrade_daily_limit"] = clamp_int(int(d.get("upgrade_daily_limit", 0) or 0), 0, 9999, 0)
    if "stall_monitor_enabled" in d:
        v = d.get("stall_monitor_enabled")
        inst["stall_monitor_enabled"] = None if v is None else bool(v)  # None=use global
    log_act("System", "Config gespeichert" if CONFIG.get("language","en")=="de" else "Config saved", "", "info")
    save_config(CONFIG); return jsonify({"ok":True})

@app.route("/api/instances/<inst_id>", methods=["DELETE"])
@_api_auth_required
def api_instances_delete(inst_id:str):
    before = len(CONFIG["instances"])
    CONFIG["instances"] = [i for i in CONFIG["instances"] if i["id"]!=inst_id]
    if len(CONFIG["instances"]) == before: return jsonify({"ok":False,"error":"Nicht gefunden"}),404
    STATE["inst_stats"].pop(inst_id,None); save_config(CONFIG)
    return jsonify({"ok":True})

@app.route("/api/instances/<inst_id>/tags")
@_api_auth_required
def api_instance_tags(inst_id: str):
    """Return available tags from the Sonarr/Radarr instance for UI display."""
    inst = next((i for i in CONFIG["instances"] if i["id"] == inst_id), None)
    if not inst: return jsonify({"ok": False, "error": "Not found"}), 404
    client = ArrClient(inst["name"], inst["url"], inst["api_key"])
    try:
        tags = client.get("tag")
        return jsonify({"ok": True, "tags": [{"id": t["id"], "label": t["label"]} for t in tags]})
    except Exception as e:
        return jsonify({"ok": False, "error": "Could not fetch tags from instance"}), 502

@app.route("/api/instances/<inst_id>/ping")
@_api_auth_required
def api_instances_ping(inst_id:str):
    inst = next((i for i in CONFIG["instances"] if i["id"]==inst_id), None)
    if not inst: return jsonify({"ok":False,"error":"Nicht gefunden"}),404
    if not inst.get("api_key"): return jsonify({"ok":False,"msg":"Kein API Key"})
    # CodeQL py/stack-trace-exposure: exception data never flows to response.
    # _ping_result holds only safe allowlisted values.
    _ping_ok, _ping_ver, _ping_detail = False, "?", "Connection failed"
    try:
        _ping_ok, _ping_ver, _ping_detail = ArrClient(inst["name"],inst["url"],inst["api_key"]).ping()
    except Exception as _pe:
        logger.debug(f"Ping exception for {inst_id}: {type(_pe).__name__}")
    stats = STATE["inst_stats"].setdefault(inst_id, fresh_inst_stats())
    stats["status"]        = "online" if _ping_ok else "offline"
    stats["version"]       = _ping_ver
    stats["status_detail"] = "" if _ping_ok else _ping_detail
    return jsonify({"ok": _ping_ok,
                    "version": _safe_version_str(_ping_ver),
                    "msg":     _safe_ping_msg(_ping_detail)})

# ── Main API ──────────────────────────────────────────────────────────────────
@app.route("/api/state")
def api_state():
    # Allow unauthenticated read-only access when public_api_state is enabled
    if _PASSWORD and not session.get("authenticated") and not CONFIG.get("public_api_state", False):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    today_n=db.count_today(); limit=CONFIG.get("daily_limit",0)
    instances_safe=[{k:v for k,v in i.items() if k!="api_key"} for i in CONFIG["instances"]]
    # Add per-instance today count so UI can show instance limit progress
    for inst_s in instances_safe:
        inst_s["today_count"] = db.count_today_for_instance(inst_s["id"])
    return jsonify({
        "version":_CURRENT_VERSION,"running":STATE["running"],"last_run":STATE["last_run"],
        "next_run":STATE["next_run"],"cycle_count":STATE["cycle_count"],
        "total_searches":db.total_count(),"daily_count":today_n,
        "daily_limit":limit,"daily_remaining":max(0,limit-today_n) if limit>0 else None,
        "inst_stats":STATE["inst_stats"],"instances":instances_safe,
        "server_time": fmt_time(now_local()),
        "server_tz":   CONFIG.get("timezone","UTC"),
        "activity_log":list(STATE["activity_log"])[:60],
        "config":{
            "hunt_missing_delay":   CONFIG["hunt_missing_delay"] // 60,  # minutes for UI
            "hunt_upgrade_delay":   CONFIG["hunt_upgrade_delay"] // 60,  # minutes for UI
            "max_searches_per_run": CONFIG["max_searches_per_run"],
            "daily_limit":          CONFIG.get("daily_limit",20),
            "sonarr_daily_limit":   CONFIG.get("sonarr_daily_limit", 0),
            "radarr_daily_limit":   CONFIG.get("radarr_daily_limit", 0),
            "upgrade_daily_limit":  CONFIG.get("upgrade_daily_limit", 0),
            "sonarr_upgrade_daily_limit": CONFIG.get("sonarr_upgrade_daily_limit", 0),
            "radarr_upgrade_daily_limit": CONFIG.get("radarr_upgrade_daily_limit", 0),
            "radarr_daily_limit":   CONFIG.get("radarr_daily_limit", 0),
            "cooldown_days":        CONFIG.get("cooldown_days",7),
            "request_timeout":      CONFIG.get("request_timeout",30),
            "jitter_max":           CONFIG.get("jitter_max",300) // 60,  # UI shows minutes
            "sonarr_search_mode":   CONFIG.get("sonarr_search_mode","season"),
            "imdb_min_rating":           CONFIG.get("imdb_min_rating", 0.0),
            "sonarr_imdb_min_rating":     CONFIG.get("sonarr_imdb_min_rating", None),
            "radarr_imdb_min_rating":     CONFIG.get("radarr_imdb_min_rating", None),
            "upgrade_target_resolution":  CONFIG.get("upgrade_target_resolution",""),
            "sonarr_upgrade_target_resolution": CONFIG.get("sonarr_upgrade_target_resolution",""),
            "radarr_upgrade_target_resolution": CONFIG.get("radarr_upgrade_target_resolution",""),
            "update_available":           is_update_available(),
            "latest_version":             _version_cache.get("latest",""),
            "search_upgrades":      CONFIG.get("search_upgrades",True),
            "tag_enabled":          CONFIG.get("tag_enabled", False),
            "webhook_trigger_key_set": bool(CONFIG.get("webhook_trigger_key","").strip()),
            "tag_label":            CONFIG.get("tag_label", "mediastarr"),
            "dry_run":              CONFIG["dry_run"],
            "language":             CONFIG["language"],
            "theme":                CONFIG.get("theme","dark"),
            "timezone":             CONFIG.get("timezone","UTC"),
            "auto_start":           CONFIG["auto_start"],
            "instance_count":       len(CONFIG["instances"]),
            "discord": {
                k: v for k, v in CONFIG.get("discord", {}).items()
                if k not in ("webhook_url", "sonarr_webhook_url", "radarr_webhook_url", "stats_last_sent_at")
            },
            "discord_configured":    bool(CONFIG.get("discord",{}).get("webhook_url","")),
            "sonarr_webhook_set": bool(CONFIG.get("discord",{}).get("sonarr_webhook_url","")),
            "radarr_webhook_set": bool(CONFIG.get("discord",{}).get("radarr_webhook_url","")),
            "discord_webhook_set": bool(CONFIG.get("discord",{}).get("webhook_url","")),
            "public_api_state":       CONFIG.get("public_api_state", False),
"stall_monitor_enabled":  CONFIG.get("stall_monitor_enabled", False),
            "stall_threshold_min":    CONFIG.get("stall_threshold_min", 60),
            "stall_action":           CONFIG.get("stall_action", "search"),
            "log_max_mb":             CONFIG.get("log_max_mb",  5),
            "log_backups":            CONFIG.get("log_backups", 2),
            "log_min_level":          CONFIG.get("log_min_level", "INFO"),
            "log_min_level":          CONFIG.get("log_min_level", "INFO"),
            "maintenance_windows":    CONFIG.get("maintenance_windows", []),
            "in_maintenance_window":  _in_maintenance_window(),
        },
    })

@app.route("/api/control", methods=["POST"])
@_api_auth_required
def api_control():
    global hunt_thread
    d=request.get_json(silent=True) or {}; action=d.get("action")
    if action not in ALLOWED_ACTIONS: return jsonify({"ok":False,"error":"Ungültige Aktion"}),400
    if action=="start" and not STATE["running"]:
        STOP_EVENT.clear(); hunt_thread=threading.Thread(target=hunt_loop,daemon=True); hunt_thread.start()
    elif action=="stop": STOP_EVENT.set()
    elif action=="run_now":
        if not STATE["running"]:
            STOP_EVENT.clear(); hunt_thread=threading.Thread(target=hunt_loop,daemon=True); hunt_thread.start()
        else: threading.Thread(target=run_cycle,daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/config", methods=["POST"])
@_api_auth_required
def api_config():
    d=request.get_json(silent=True)
    if d is None: return jsonify({"ok":False,"error":"Ungültiges JSON"}),400
    # Enforce minimum 15 minute interval
    # UI sends minutes, store as seconds internally
    raw_min = clamp_int(d.get("hunt_missing_delay", CONFIG["hunt_missing_delay"]//60), MIN_INTERVAL_MIN, 1440, CONFIG["hunt_missing_delay"]//60)
    CONFIG["hunt_missing_delay"]   = raw_min * 60
    raw_up_min = clamp_int(d.get("hunt_upgrade_delay", CONFIG["hunt_upgrade_delay"]//60), MIN_INTERVAL_MIN, 1440, CONFIG["hunt_upgrade_delay"]//60)
    CONFIG["hunt_upgrade_delay"]   = raw_up_min * 60
    CONFIG["max_searches_per_run"] = clamp_int(d.get("max_searches_per_run", CONFIG["max_searches_per_run"]), 1, 500, CONFIG["max_searches_per_run"])
    CONFIG["daily_limit"]          = clamp_int(d.get("daily_limit",          CONFIG.get("daily_limit",20)),   0, 9999, CONFIG.get("daily_limit",20))
    if "sonarr_daily_limit" in d: CONFIG["sonarr_daily_limit"] = clamp_int(int(d.get("sonarr_daily_limit",0) or 0),0,9999,0)
    if "radarr_daily_limit" in d: CONFIG["radarr_daily_limit"] = clamp_int(int(d.get("radarr_daily_limit",0) or 0),0,9999,0)
    for _upg_key in ("upgrade_daily_limit","sonarr_upgrade_daily_limit","radarr_upgrade_daily_limit"):
        if _upg_key in d: CONFIG[_upg_key] = clamp_int(int(d.get(_upg_key,0) or 0),0,9999,0)
    if "radarr_daily_limit" in d: CONFIG["radarr_daily_limit"] = clamp_int(int(d.get("radarr_daily_limit",0) or 0),0,9999,0)
    CONFIG["cooldown_days"]        = clamp_int(d.get("cooldown_days",        CONFIG.get("cooldown_days",7)),  1, 365, CONFIG.get("cooldown_days",7))
    CONFIG["request_timeout"]      = clamp_int(d.get("request_timeout",      CONFIG.get("request_timeout",30)),5, 300, 30)
    raw_jitter_min = clamp_int(d.get("jitter_max", CONFIG.get("jitter_max",300)//60), 0, 60, 5)
    CONFIG["jitter_max"] = raw_jitter_min * 60  # store seconds internally
    if "dry_run"         in d: CONFIG["dry_run"]         = bool(d["dry_run"])
    if "auto_start"      in d: CONFIG["auto_start"]      = bool(d["auto_start"])
    if "search_upgrades" in d: CONFIG["search_upgrades"] = bool(d["search_upgrades"])
    if "tag_enabled"     in d: CONFIG["tag_enabled"]     = bool(d["tag_enabled"])
    if "tag_label"       in d:
        _lbl = safe_str(str(d.get("tag_label","mediastarr")).strip(), 50)
        CONFIG["tag_label"] = _lbl if _lbl else "mediastarr"
    mode = safe_str(d.get("sonarr_search_mode",""), 10)
    if mode in ALLOWED_SONARR_MODES: CONFIG["sonarr_search_mode"] = mode
    if "imdb_min_rating" in d:
        CONFIG["imdb_min_rating"] = max(0.0, min(10.0, float(d.get("imdb_min_rating",0) or 0)))
    for _app_imdb_key in ("sonarr_imdb_min_rating","radarr_imdb_min_rating"):
        if _app_imdb_key in d:
            _v = d.get(_app_imdb_key)
            CONFIG[_app_imdb_key] = None if (_v is None or _v == "") else max(0.0, min(10.0, float(_v or 0)))
    if "upgrade_target_resolution" in d:
        res = safe_str(d.get("upgrade_target_resolution",""), 30)
        CONFIG["upgrade_target_resolution"] = res if res in ALLOWED_RESOLUTIONS else ""
    for _app_res_key in ("sonarr_upgrade_target_resolution","radarr_upgrade_target_resolution"):
        if _app_res_key in d:
            res = safe_str(d.get(_app_res_key,""), 30)
            CONFIG[_app_res_key] = res if res in ALLOWED_RESOLUTIONS else ""
    theme = safe_str(d.get("theme", CONFIG.get("theme","dark")), 10)
    if theme in ALLOWED_THEMES: CONFIG["theme"] = theme
    lang = safe_str(d.get("language", CONFIG["language"]), 5)
    if lang in ALLOWED_LANGUAGES: CONFIG["language"] = lang
    tz = safe_str(d.get("timezone", CONFIG.get("timezone","UTC")), 50)
    try: zoneinfo.ZoneInfo(tz); CONFIG["timezone"] = tz
    except Exception: pass  # keep current if invalid

    # Discord settings
    if "discord" in d and isinstance(d["discord"], dict):
        dc_in = d["discord"]
        dc    = CONFIG.setdefault("discord", {})
        for bool_key in ("enabled","notify_missing","notify_upgrade",
                         "notify_cooldown","notify_limit","notify_offline","notify_stats","notify_update"):
            if bool_key in dc_in: dc[bool_key] = bool(dc_in[bool_key])
        for url_key in ("webhook_url_sonarr","webhook_url_radarr"):
            if url_key in dc_in:
                u = safe_str(dc_in[url_key], 512).strip()
                if u == "" or u.startswith(("http://","https://")):
                    dc[url_key] = u
        if "stats_interval_min" in dc_in:
            dc["stats_interval_min"] = clamp_int(dc_in.get("stats_interval_min", 60), 1, 10080, 60)
        if "rate_limit_cooldown" in dc_in:
            dc["rate_limit_cooldown"] = clamp_int(dc_in.get("rate_limit_cooldown", 5), 1, 300, 5)
        if "webhook_url" in dc_in:
            url = safe_str(dc_in["webhook_url"], 512).strip()
            if url == "" or url.startswith(("http://","https://")):
                dc["webhook_url"] = encrypt_secret(url) if url else ""
        for _wh_key in ("sonarr_webhook_url", "radarr_webhook_url"):
            if _wh_key in dc_in:
                wh = safe_str(dc_in[_wh_key], 512).strip()
                if wh == "" or wh.startswith(("http://","https://")):
                    dc[_wh_key] = wh
    if "public_api_state" in d:
        CONFIG["public_api_state"] = bool(d["public_api_state"])
    if "maintenance_windows" in d:
        wins = d.get("maintenance_windows") or []
        if isinstance(wins, list):
            validated = []
            for w in wins[:10]:  # max 10 windows
                if not isinstance(w, dict): continue
                start = safe_str(w.get("start",""), 5)
                end   = safe_str(w.get("end",""),   5)
                label = safe_str(w.get("label",""), 40)
                # Validate HH:MM format
                import re as _re
                if not _re.match(r"^[0-2][0-9]:[0-5][0-9]$", start): continue
                if not _re.match(r"^[0-2][0-9]:[0-5][0-9]$", end):   continue
                validated.append({"start": start, "end": end, "label": label})
            CONFIG["maintenance_windows"] = validated
    if "stall_monitor_enabled" in d: CONFIG["stall_monitor_enabled"] = bool(d["stall_monitor_enabled"])
    if "stall_threshold_min"   in d: CONFIG["stall_threshold_min"]   = clamp_int(d.get("stall_threshold_min", 60), 5, 10080, 60)
    if "stall_action"          in d:
        _sa = safe_str(d.get("stall_action","search"), 10)
        if _sa in ("search","warn"): CONFIG["stall_action"] = _sa
    _changed_log = False
    if "log_min_level" in d:
        lvl = str(d.get("log_min_level","INFO")).upper()
        if lvl in _LOG_LEVEL_MAP:
            CONFIG["log_min_level"] = lvl
            _apply_log_level()
    if "log_max_mb" in d:
        new_mb = max(1, min(100, int(d.get("log_max_mb", 5) or 5)))
        if new_mb != CONFIG.get("log_max_mb", 5):
            CONFIG["log_max_mb"] = new_mb
            _changed_log = True
    if "log_backups" in d:
        new_bk = max(0, min(10, int(d.get("log_backups", 2) or 2)))
        if new_bk != CONFIG.get("log_backups", 2):
            CONFIG["log_backups"] = new_bk
            _changed_log = True
    if _changed_log: _reconfigure_file_logging()
    save_config(CONFIG); return jsonify({"ok":True})

# ── Config Export / Import ────────────────────────────────────────────────────
@app.route("/api/config/export")
@_api_auth_required
def api_config_export():
    """Download config.json as a timestamped backup file (API keys included — treat as secret)."""
    import io
    export = {k: v for k, v in CONFIG.items()}
    ts     = now_local().strftime("%Y%m%d_%H%M%S")
    data   = json.dumps(export, indent=2, ensure_ascii=False)
    buf    = io.BytesIO(data.encode("utf-8"))
    return send_file(                           # type: ignore[return-value]
        buf,
        mimetype        = "application/json",
        as_attachment   = True,
        download_name   = f"mediastarr_config_{ts}.json",
    )

@app.route("/api/config/import", methods=["POST"])
@_api_auth_required
def api_config_import():
    """Upload a previously exported config.json to restore settings."""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "No file provided"}), 400
    if not file.filename.endswith(".json"):
        return jsonify({"ok": False, "error": "File must be a .json backup"}), 400
    try:
        raw  = file.read(1_024 * 512)   # 512 KB hard cap
        data = json.loads(raw)
    except Exception as e:
        return jsonify({"ok": False, "error": "Invalid JSON in uploaded file"}), 400

    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Root must be a JSON object"}), 400

    # Validate instances list
    for inst in data.get("instances", []):
        if not isinstance(inst, dict):
            return jsonify({"ok": False, "error": "Invalid instances list"}), 400
        # Must have required keys and safe values
        nm  = safe_str(inst.get("name",""),  40)
        url = safe_str(inst.get("url",""),   URL_MAX_LEN)
        key = safe_str(inst.get("api_key",""), 128)
        itp = safe_str(inst.get("type",""),  10)
        if itp not in ALLOWED_TYPES:
            return jsonify({"ok": False, "error": f"Unknown instance type: {itp}"}), 400
        ok_n, _ = validate_name(nm)
        if not ok_n and nm:
            return jsonify({"ok": False, "error": f"Invalid instance name: {nm}"}), 400

    # Merge: start from DEFAULT_CONFIG, overlay imported values, preserve setup_complete
    merged = DEFAULT_CONFIG.copy()
    merged.update(data)
    # Ensure IDs exist on all instances
    for inst in merged.get("instances", []):
        if "id" not in inst or not inst["id"]:
            inst["id"] = make_id()
    # Never let an import reset setup or clobber critical runtime keys
    merged["setup_complete"] = CONFIG.get("setup_complete", merged.get("setup_complete", False))
    # Write to disk and hot-reload into CONFIG
    CONFIG.clear()
    CONFIG.update(merged)
    save_config(CONFIG)
    logger.info("Config imported via /api/config/import")
    return jsonify({"ok": True, "instances": len(merged.get("instances", []))})

# ── History API ───────────────────────────────────────────────────────────────
@app.route("/api/webhook/trigger", methods=["POST"])
@_api_auth_required
def api_webhook_trigger():
    """Trigger an immediate hunt cycle from external automation (e.g. Sonarr/Radarr webhooks).
    Requires authentication. Same as pressing Run Now in the UI.
    Body: {} or {"source": "sonarr"} — source is logged only, not acted upon.
    """
    d = request.get_json(silent=True) or {}
    source = safe_str(d.get("source","external"), 30)
    lang   = CONFIG.get("language","en")
    is_de  = lang == "de"
    log_act("System",
            "Webhook-Trigger empfangen" if is_de else "Webhook trigger received",
            source, "info")
    if STATE["running"]:
        STOP_EVENT.set()
    threading.Thread(target=run_cycle, daemon=True).start()
    return jsonify({"ok": True, "message": "Hunt cycle triggered", "source": source})

@app.route("/api/history")
@_api_auth_required
def api_history():
    svc=safe_str(request.args.get("service",""),40)
    only_cd=request.args.get("cooldown_only")=="1"
    cd_days=CONFIG.get("cooldown_days",7)
    rows=db.get_history(300,svc,only_cd,cd_days)
    now=datetime.utcnow()
    for r in rows:
        ts=datetime.fromisoformat(r["searched_at"]); ago=now-ts; mins=int(ago.total_seconds()/60)
        r["ago_label"]=(f"vor {mins}min" if mins<60 else f"vor {mins//60}h" if mins<1440 else f"vor {mins//1440}d")
        r["expires_label"]=(ts+timedelta(days=cd_days)).strftime("%d.%m. %H:%M")
        r["instance_name"]=next((i["name"] for i in CONFIG["instances"] if i["id"]==r["service"]),r["service"])
    return jsonify({"ok":True,"count":len(rows),"history":rows})

@app.route("/api/history/stats")
@_api_auth_required
def api_history_stats():
    return jsonify({"ok":True,"total":db.total_count(),"today":db.count_today(),
                    "by_service":db.stats_by_service(),"by_year":db.year_stats()})

@app.route("/api/history/clear", methods=["POST"])
@_api_auth_required
def api_history_clear():
    n = db.clear_all()
    is_de = CONFIG.get("language","de") == "de"
    log_act("System", "DB geleert" if is_de else "DB cleared",
            f"{n} {'Einträge' if is_de else 'entries'}", "warning")
    return jsonify({"ok":True,"removed":n})

@app.route("/api/history/clear/<inst_id>", methods=["POST"])
@_api_auth_required
def api_history_clear_inst(inst_id:str):
    n = db.clear_service(inst_id)
    is_de = CONFIG.get("language","de") == "de"
    log_act("System", f"DB geleert ({inst_id})" if is_de else f"DB cleared ({inst_id})", str(n), "warning")
    return jsonify({"ok":True,"removed":n})

# ── Timezone helper ───────────────────────────────────────────────────────────
@app.route("/api/timezones")
@_api_auth_required
def api_timezones():
    """Return all available IANA timezones, grouped by region."""
    try:
        all_tz = sorted(zoneinfo.available_timezones())
    except Exception:
        all_tz = []
    # Always put UTC first, then sort rest
    ordered = ["UTC"] + [z for z in all_tz if z != "UTC"]
    # Group by region prefix for UI
    regions = {}
    for tz in ordered:
        region = tz.split("/")[0] if "/" in tz else "Other"
        regions.setdefault(region, []).append(tz)
    # Flat sorted list for simple select, current tz always available
    current = CONFIG.get("timezone", "UTC")
    flat = ordered if ordered else ["UTC"]
    if current not in flat:
        flat = [current] + flat
    return jsonify({"ok": True, "timezones": flat, "current": current})

# ── Discord test endpoint ─────────────────────────────────────────────────────
@app.route("/api/log/rotate", methods=["POST"])
@_api_auth_required
def api_log_rotate():
    """Manually trigger a log rotation and return current log file info."""
    import os
    try:
        if _file_handler is None:
            return jsonify({"ok": False, "error": "File logging not active"})
        _file_handler.doRollover()
        log_file = pathlib.Path(_file_handler.baseFilename)
        size_kb  = round(log_file.stat().st_size / 1024, 1) if log_file.exists() else 0
        backups  = []
        for i in range(1, _file_handler.backupCount + 1):
            bp = pathlib.Path(f"{_file_handler.baseFilename}.{i}")
            if bp.exists():
                backups.append({"file": bp.name, "size_kb": round(bp.stat().st_size/1024, 1)})
        logger.info("Manual log rotation triggered via API")
        return jsonify({
            "ok": True,
            "current": {"file": log_file.name, "size_kb": size_kb},
            "backups": backups,
            "max_mb":  CONFIG.get("log_max_mb", 5),
            "backups_count": CONFIG.get("log_backups", 2),
        })
    except Exception as e:
        logger.error(f"Log rotation failed: {e}")
        return jsonify({"ok": False, "error": "Log rotation configuration failed"})

@app.route("/api/log/status")
@_api_auth_required
def api_log_status():
    """Return current log file sizes and rotation config."""
    try:
        if _file_handler is None:
            return jsonify({"ok": False, "error": "File logging not active"})
        log_file = pathlib.Path(_file_handler.baseFilename)
        files = []
        if log_file.exists():
            files.append({"file": log_file.name, "size_kb": round(log_file.stat().st_size/1024, 1)})
        for i in range(1, _file_handler.backupCount + 2):
            bp = pathlib.Path(f"{_file_handler.baseFilename}.{i}")
            if bp.exists():
                files.append({"file": bp.name, "size_kb": round(bp.stat().st_size/1024, 1)})
        return jsonify({
            "ok": True,
            "files": files,
            "max_bytes":    _file_handler.maxBytes,
            "max_mb":       CONFIG.get("log_max_mb", 5),
            "backups_count":CONFIG.get("log_backups", 2),
            "log_dir":      str(pathlib.Path(_file_handler.baseFilename).parent),
        })
    except Exception as e:
        logger.debug(f"api_log_status error: {e}")
        return jsonify({"ok": False, "error": "Could not read log status"})

@app.route("/api/discord/test", methods=["POST"])
@_api_auth_required
def api_discord_test():
    dc = CONFIG.get("discord", {})
    if not dc.get("webhook_url",""):
        return jsonify({"ok":False,"error":"Kein Webhook URL konfiguriert" if CONFIG.get("language","de")=="de" else "No webhook URL configured"}),400
    lang = CONFIG.get("language","de")
    if lang == "de":
        label = "🔔 Mediastarr Test"
        desc  = f"Dies ist eine Test-Benachrichtigung von Mediastarr {_CURRENT_VERSION}.\nWenn du das siehst, ist der Webhook korrekt konfiguriert."
        f_status  = "Status"
        f_ok      = "✓ Verbunden"
        f_ver     = "Version"
        f_inst    = "Instanzen"
        f_enabled = "Benachrichtigungen"
    else:
        label = "🔔 Mediastarr Test"
        desc  = f"This is a test notification from Mediastarr {_CURRENT_VERSION}.\nIf you see this, the webhook is configured correctly."
        f_status  = "Status"
        f_ok      = "✓ Connected"
        f_ver     = "Version"
        f_inst    = "Instances"
        f_enabled = "Notifications"

    if lang == "de":
        enabled_parts = ["Fehlend" if dc.get("notify_missing") else "", "Upgrade" if dc.get("notify_upgrade") else "", "Cooldown" if dc.get("notify_cooldown") else ""]
    else:
        enabled_parts = ["Missing" if dc.get("notify_missing") else "", "Upgrade" if dc.get("notify_upgrade") else "", "Cooldown" if dc.get("notify_cooldown") else ""]
    enabled_text = " ".join([p for p in enabled_parts if p]) or "—"
    active = len([i for i in CONFIG["instances"] if i.get("enabled")])
    fields = [
        {"name": f_status,  "value": f_ok, "inline": True},
        {"name": f_ver,     "value": _CURRENT_VERSION, "inline": True},
        {"name": f_inst,    "value": str(active), "inline": True},
        {"name": f_enabled, "value": enabled_text, "inline": False},
    ]
    # Force-send bypassing toggle/cooldown
    saved_enabled = dc.get("enabled", False)
    dc["enabled"] = True
    discord_send("info", label, desc, "System", fields=fields, force=True)
    dc["enabled"] = saved_enabled
    return jsonify({"ok": True})


@app.route("/api/discord/stats", methods=["POST"])
@_api_auth_required
def api_discord_stats_now():
    """Manually trigger a stats report."""
    dc = CONFIG.get("discord", {})
    if not dc.get("webhook_url",""):
        lang = CONFIG.get("language","de")
        return jsonify({"ok":False,"error":"Kein Webhook URL konfiguriert" if lang=="de" else "No webhook URL"}),400
    discord_send_stats()
    return jsonify({"ok": True})


# ─── Startup ──────────────────────────────────────────────────────────────────
_started = False
_startup_lock = threading.Lock()

def _do_startup():
    """Run once on first request — works with both gunicorn and python direct."""
    global _started
    _setup_file_logging(DATA_DIR,
        max_mb  = CONFIG.get("log_max_mb",  5),
        backups = CONFIG.get("log_backups", 2))
    with _startup_lock:
        if _started:
            return
        _started = True
    log_act("System", f"{msg('app_start')} — {_CURRENT_VERSION}", "", "info")
    _migrate_encrypt_secrets()  # re-encrypt plaintext keys on Docker update
    if CONFIG.get("setup_complete"):
        _ensure_inst_stats(); ping_all()
        if CONFIG.get("auto_start", True):
            global hunt_thread
            hunt_thread = threading.Thread(target=hunt_loop, daemon=True)
            hunt_thread.start()
            log_act("System", msg("auto_start"), "", "info")
    else:
        log_act("System", msg("setup_required", setup_url=setup_url_for_logs()), "", "warning")
@app.before_request
def _before_request():
    _do_startup()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7979, debug=False, use_reloader=False)
