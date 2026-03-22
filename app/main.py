"""
Mediastarr v6 — main.py
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
import os, re, json, time, logging, threading, requests, random, string, zoneinfo, socket
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, render_template, jsonify, request, redirect, session, url_for
from collections import deque
import secrets
try:
    from . import db
except ImportError:
    import db

app = Flask(__name__, template_folder='../templates', static_folder='../static')
app.config["SECRET_KEY"] = os.environ.get("MEDIASTARR_SESSION_SECRET") or secrets.token_hex(32)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────
ALLOWED_TYPES       = frozenset({"sonarr","radarr"})
ALLOWED_LANGUAGES   = frozenset({"de","en"})
ALLOWED_ACTIONS     = frozenset({"start","stop","run_now"})
ALLOWED_SCHEMES     = frozenset({"http","https"})
ALLOWED_THEMES      = frozenset({"dark","light","oled"})
ALLOWED_SONARR_MODES= frozenset({"episode","season","series"})
API_KEY_RE          = re.compile(r'^[A-Za-z0-9\-_]{8,128}$')
NAME_RE             = re.compile(r'^[A-Za-z0-9 \-_äöüÄÖÜß]{1,40}$')
URL_MAX_LEN         = 256
MAX_INSTANCES       = 20
MIN_INTERVAL_SEC    = 900   # 15 minutes absolute minimum

# ─── Discord Webhook ─────────────────────────────────────────────────────────
DISCORD_COLORS = {
    "missing":  0x3de68b,
    "upgrade":  0xf5c842,
    "cooldown": 0x4d9cff,
    "limit":    0xff4d4d,
    "offline":  0x888888,
    "stats":    0xff6b2b,
    "info":     0xff6b2b,
}

# Rate-limit guard: tracks last successful send time per event_type
_dc_last_sent: dict[str, float] = {}
_dc_lock = threading.Lock()

def _dc_cooldown_ok(event_type: str, cooldown_sec: int) -> bool:
    """Return True if we're allowed to send (cooldown elapsed or never sent)."""
    with _dc_lock:
        last = _dc_last_sent.get(event_type, 0.0)
        if time.time() - last >= cooldown_sec:
            _dc_last_sent[event_type] = time.time()
            return True
        return False

def discord_send(event_type: str, title: str, description: str,
                 instance_name: str = "", fields: list | None = None,
                 force: bool = False):
    """Fire-and-forget Discord embed. Runs in a daemon thread.
    Silently drops if webhook unconfigured, disabled, or rate-limited.
    Set force=True to bypass per-type cooldown (used for test & stats)."""
    dc = CONFIG.get("discord", {})
    if not dc.get("enabled"): return
    url = safe_str(dc.get("webhook_url", ""), 512).strip()
    if not url or not url.startswith(("http://", "https://")): return

    # Per-event toggle
    toggle_map = {
        "missing":  "notify_missing",
        "upgrade":  "notify_upgrade",
        "cooldown": "notify_cooldown",
        "limit":    "notify_limit",
        "offline":  "notify_offline",
    }
    toggle_key = toggle_map.get(event_type)
    if toggle_key and not dc.get(toggle_key, True): return

    # Rate-limit cooldown (default 5 s, configurable)
    cooldown_sec = clamp_int(dc.get("rate_limit_cooldown", 5), 1, 300, 5)
    if not force and not _dc_cooldown_ok(event_type, cooldown_sec):
        logger.debug(f"Discord rate-limit: skipping {event_type}")
        return

    color = DISCORD_COLORS.get(event_type, DISCORD_COLORS["info"])
    footer_text = f"Mediastarr v6 · {instance_name}" if instance_name else "Mediastarr v6"
    embed = {
        "title":       safe_str(title, 256),
        "description": safe_str(description, 2048),
        "color":       color,
        "footer":      {"text": footer_text},
        "timestamp":   datetime.utcnow().isoformat() + "Z",
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
            r = requests.post(url, json={"embeds": [embed]},
                              timeout=CONFIG.get("request_timeout", 30))
            if r.status_code == 429:
                retry_after = r.json().get("retry_after", 5)
                logger.warning(f"Discord 429: retry_after={retry_after}s")
            elif r.status_code not in (200, 204):
                logger.warning(f"Discord webhook HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"Discord webhook failed: {e}")

    threading.Thread(target=_send, daemon=True).start()


def discord_send_stats():
    """Send a statistics summary embed to Discord."""
    dc = CONFIG.get("discord", {})
    if not dc.get("enabled") or not dc.get("notify_stats", False): return
    lang  = CONFIG.get("language", "de")
    today = db.count_today()
    limit = CONFIG.get("daily_limit", 0)
    total = db.total_count()
    cycles = STATE.get("cycle_count", 0)

    if lang == "de":
        title = "📊 Mediastarr Statistiken"
        desc  = f"Tagesbericht — {now_local().strftime('%d.%m.%Y %H:%M')}"
        f_today  = "Heute"
        f_total  = "Gesamt"
        f_cycles = "Zyklen"
        f_insts  = "Aktive Instanzen"
        f_limit  = f"{today} / {limit if limit else '∞'}"
    else:
        title = "📊 Mediastarr Statistics"
        desc  = f"Daily report — {now_local().strftime('%Y-%m-%d %H:%M')}"
        f_today  = "Today"
        f_total  = "Total"
        f_cycles = "Cycles"
        f_insts  = "Active instances"
        f_limit  = f"{today} / {limit if limit else '∞'}"

    if lang == "de":
        enabled_parts = ["Fehlend" if dc.get("notify_missing") else "", "Upgrade" if dc.get("notify_upgrade") else "", "Cooldown" if dc.get("notify_cooldown") else ""]
    else:
        enabled_parts = ["Missing" if dc.get("notify_missing") else "", "Upgrade" if dc.get("notify_upgrade") else "", "Cooldown" if dc.get("notify_cooldown") else ""]
    enabled_text = " ".join([p for p in enabled_parts if p]) or "—"
    active = len([i for i in CONFIG["instances"] if i.get("enabled")])
    fields = [
        {"name": f_today,  "value": f_limit, "inline": True},
        {"name": f_total,  "value": str(total), "inline": True},
        {"name": f_cycles, "value": str(cycles), "inline": True},
        {"name": f_insts,  "value": str(active), "inline": True},
    ]
    # Per-instance status
    for inst in CONFIG["instances"][:6]:
        st = STATE["inst_stats"].get(inst["id"], {}).get("status", "?")
        icon = "🟢" if st == "online" else "🔴" if st == "offline" else "⚫"
        fields.append({"name": inst["name"], "value": f"{icon} {st}", "inline": True})

    discord_send("stats", title, desc, "System", fields=fields, force=True)


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
        if time.time() - float(last) >= interval_min * 60:
            discord_send_stats()
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
        "app_start":        "Mediastarr v6.1.1 gestartet",
        "setup_required":   "Einrichtung erforderlich – http://localhost:7979/setup",
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
        "app_start":        "Mediastarr v6.1.1 started",
        "setup_required":   "Setup required – http://localhost:7979/setup",
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
    except: return tmpl

# ─── Paths ───────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CFG_FILE = DATA_DIR / "config.json"
DB_FILE  = DATA_DIR / "mediastarr.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
db.init(DB_FILE)

# ─── Helpers ─────────────────────────────────────────────────────────────────
def make_id() -> str:
    return "inst_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

def fresh_inst_stats() -> dict:
    return {"missing_found":0,"missing_searched":0,"upgrades_found":0,
            "upgrades_searched":0,"skipped_cooldown":0,"skipped_daily":0,
            "status":"unknown","version":"?"}

def now_local() -> datetime:
    """Current time in configured timezone."""
    tz_name = CONFIG.get("timezone", "UTC")
    try: tz = zoneinfo.ZoneInfo(tz_name)
    except Exception: tz = zoneinfo.ZoneInfo("UTC")
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
    except: return None

# ─── Default config ───────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "setup_complete": False,
    "language": "en",
    "theme": "dark",
    "timezone": "UTC",
    "instances": [],
    "hunt_missing_delay":    900,   # seconds (min 900 = 15 min)
    "hunt_upgrade_delay":   1800,
    "max_searches_per_run":   10,
    "daily_limit":            20,
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
        "stats_interval_min":  60,     # minutes between stats reports
        "stats_last_sent_at":  0.0,    # unix timestamp
        "rate_limit_cooldown": 5,      # seconds between same-type messages
    },
}

def load_config() -> dict:
    if CFG_FILE.exists():
        try:
            raw = json.loads(CFG_FILE.read_text())
            m = DEFAULT_CONFIG.copy(); m.update(raw)
            for inst in m.get("instances",[]):
                if "id" not in inst: inst["id"] = make_id()
            return m
        except Exception as e: logger.warning(f"Config load failed: {e}")
    return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    tmp = CFG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2)); tmp.replace(CFG_FILE)

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
    except: return False,"URL ungültig"
    if p.scheme not in ALLOWED_SCHEMES: return False,f"Schema '{p.scheme}' nicht erlaubt"
    if not p.hostname: return False,"Kein Hostname"
    return True,""

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
    except: return default

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
class ArrClient:
    def __init__(self, name:str, url:str, api_key:str):
        self.name = name; self.url = url.rstrip("/")
        self._h = {"X-Api-Key":api_key,"Content-Type":"application/json"}

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

    def ping(self):
        try:
            d = self.get("system/status")
            return True, str(d.get("version","?"))[:20]
        except Exception as e: return False, str(e)[:200]

# ─── Activity Log ─────────────────────────────────────────────────────────────
def log_act(service:str, action:str, item:str, status:str="info"):
    ts = fmt_time(now_local())
    STATE["activity_log"].appendleft({
        "ts": ts, "service": safe_str(service,30),
        "action": safe_str(action,50), "item": safe_str(item,200),
        "status": status if status in ("info","success","warning","error") else "info",
    })
    logger.info(f"[{service}] {action}: {item}")

# ─── Jitter ───────────────────────────────────────────────────────────────────
def jittered_delay(base_sec: int) -> tuple[int, int]:
    """Returns (actual_delay, jitter_applied). Minimum 900s enforced."""
    jmax = CONFIG.get("jitter_max", 300)
    jitter = random.randint(0, max(0, jmax)) if jmax > 0 else 0
    total = max(MIN_INTERVAL_SEC, base_sec + jitter)
    return total, jitter

# ─── Hunt helpers ─────────────────────────────────────────────────────────────
def daily_limit_reached() -> bool:
    limit = CONFIG.get("daily_limit", 0)
    return limit > 0 and db.count_today() >= limit

def should_search(iid:str, item_type:str, item_id:int):
    if daily_limit_reached(): return False, "daily_limit"
    if db.is_on_cooldown(iid, item_type, item_id, CONFIG.get("cooldown_days",7)):
        return False, "cooldown"
    return True, ""

def do_search(client:ArrClient, iid:str, item_type:str, item_id:int,
              title:str, command:dict, changed=None, year=None):
    result = "dry_run" if CONFIG["dry_run"] else "triggered"
    if not CONFIG["dry_run"]: client.post("command", command)
    db.upsert_search(iid, item_type, item_id, title, result, changed, year)

    # Discord notification
    inst = next((i for i in CONFIG["instances"] if i["id"] == iid), {})
    inst_name = inst.get("name", iid)
    is_upgrade = "upgrade" in item_type
    event = "upgrade" if is_upgrade else "missing"
    label_de = "Upgrade gesucht" if is_upgrade else "Fehlend gesucht"
    label_en = "Upgrade searched" if is_upgrade else "Missing searched"
    label = label_de if CONFIG.get("language","de") == "de" else label_en
    icon  = "⬆️" if is_upgrade else "🔍"
    if result == "dry_run":
        desc = f"**[Dry Run]** {icon} {title}"
    else:
        desc = f"{icon} {title}"
    discord_send(event, label, desc, inst_name, fields=[
        {"name": "Instanz", "value": inst_name, "inline": True},
        {"name": "Typ",     "value": item_type,  "inline": True},
    ])
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
    mode   = CONFIG.get("sonarr_search_mode", "season")
    lang   = CONFIG.get("language", "en")
    do_upgrades = CONFIG.get("search_upgrades", True)

    # Build series ID → title cache once per hunt so ep titles are always correct
    # even when Sonarr omits series.title in wanted/missing responses
    series_cache: dict[int, str] = {}
    try:
        all_series = client.get("series")
        for s in all_series:
            sid = s.get("id")
            if sid and s.get("title"):
                series_cache[int(sid)] = s["title"].strip()
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
        data  = client.get("wanted/missing", params={"pageSize":500,"sortKey":"airDateUtc","sortDir":"desc"})
        recs  = data.get("records", [])
        random.shuffle(recs)  # random selection — avoids always hitting same items
        stats["missing_found"] = int(data.get("totalRecords", len(recs)))
        searched = 0
        for ep in recs:
            if STOP_EVENT.is_set() or searched >= CONFIG["max_searches_per_run"]: break
            title = ep_title(ep)
            ok, reason = should_search(iid, "episode", ep["id"])
            if not ok:
                stats[f"skipped_{reason}"] += 1
                if reason == "daily_limit":
                    log_act(name, msg("daily_limit",today=db.count_today(),limit=CONFIG["daily_limit"]), "", "warning")
                    lang = CONFIG.get("language","en")
                    label = "Tageslimit erreicht" if lang=="de" else "Daily limit reached"
                    desc  = (f"Heute: {db.count_today()}/{CONFIG['daily_limit']} Searches"
                             if lang=="de" else
                             f"Today: {db.count_today()}/{CONFIG['daily_limit']} searches")
                    discord_send("limit", label, desc, name)
                    return
                continue
            year = _year(ep.get("series",{}).get("year") or ep.get("airDate","")[:4])
            # Build command based on search mode
            series_id = ep.get("series",{}).get("id", ep.get("seriesId"))
            if mode == "series" and series_id:
                command = {"name":"SeriesSearch","seriesId":series_id}
            elif mode == "season" and series_id:
                command = {"name":"SeasonSearch","seriesId":series_id,"seasonNumber":ep.get("seasonNumber",0)}
            else:
                command = {"name":"EpisodeSearch","episodeIds":[ep["id"]]}
            do_search(client, iid, "episode", ep["id"], title, command,
                      ep.get("series",{}).get("lastInfoSync"), year)
            stats["missing_searched"] += 1; searched += 1
            log_act(name, msg("missing"), title, "success")
            time.sleep(1.5)
    except Exception as e:
        log_act(name, msg("error"), str(e)[:200], "error")

    if not do_upgrades: return

    # ── Upgrades ──
    try:
        data  = client.get("wanted/cutoff", params={"pageSize":500})
        recs  = data.get("records", [])
        random.shuffle(recs)  # random selection
        stats["upgrades_found"] = int(data.get("totalRecords", len(recs)))
        searched = 0
        for ep in recs:
            if STOP_EVENT.is_set() or searched >= CONFIG["max_searches_per_run"]: break
            title = ep_title(ep)
            ok, reason = should_search(iid, "episode_upgrade", ep["id"])
            if not ok:
                stats[f"skipped_{reason}"] += 1
                if reason == "daily_limit": return
                continue
            year = _year(ep.get("series",{}).get("year"))
            do_search(client, iid, "episode_upgrade", ep["id"], title,
                      {"name":"EpisodeSearch","episodeIds":[ep["id"]]}, year=year)
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
    do_upgrades = CONFIG.get("search_upgrades", True)

    # ── Missing ──
    try:
        movies  = client.get("movie")
        random.shuffle(movies)  # random selection
        missing = [m for m in movies if not m.get("hasFile") and m.get("monitored")]
        stats["missing_found"] = len(missing)
        searched = 0
        for movie in missing:
            if STOP_EVENT.is_set() or searched >= CONFIG["max_searches_per_run"]: break
            title = str(movie.get("title","?"))[:100]
            year  = _year(movie.get("year"))
            if year: title = f"{title} ({year})"
            ok, reason = should_search(iid, "movie", movie["id"])
            if not ok:
                stats[f"skipped_{reason}"] += 1
                if reason == "daily_limit":
                    log_act(name, msg("daily_limit",today=db.count_today(),limit=CONFIG["daily_limit"]), "", "warning")
                    return
                continue
            do_search(client, iid, "movie", movie["id"], title,
                      {"name":"MoviesSearch","movieIds":[movie["id"]]},
                      movie.get("lastInfoSync"), _year(movie.get("year")))
            stats["missing_searched"] += 1; searched += 1
            log_act(name, msg("missing"), title, "success")
            time.sleep(1.5)
    except Exception as e:
        log_act(name, msg("error"), str(e)[:200], "error")

    if not do_upgrades: return

    # ── Upgrades ──
    try:
        data  = client.get("wanted/cutoff", params={"pageSize":500})
        recs  = data.get("records", [])
        random.shuffle(recs)  # random selection
        stats["upgrades_found"] = int(data.get("totalRecords", len(recs)))
        searched = 0
        for movie in recs:
            if STOP_EVENT.is_set() or searched >= CONFIG["max_searches_per_run"]: break
            title = str(movie.get("title","?"))[:100]
            year  = _year(movie.get("year"))
            if year: title = f"{title} ({year})"
            ok, reason = should_search(iid, "movie_upgrade", movie["id"])
            if not ok:
                stats[f"skipped_{reason}"] += 1
                if reason == "daily_limit": return
                continue
            do_search(client, iid, "movie_upgrade", movie["id"], title,
                      {"name":"MoviesSearch","movieIds":[movie["id"]]},
                      year=_year(movie.get("year")))
            stats["upgrades_searched"] += 1; searched += 1
            log_act(name, msg("upgrade"), title, "warning")
            time.sleep(1.5)
    except Exception as e:
        log_act(name, msg("error"), str(e)[:200], "error")

# ─── Ping ─────────────────────────────────────────────────────────────────────
def ping_all():
    _ensure_inst_stats()
    for inst in CONFIG["instances"]:
        stats = STATE["inst_stats"].setdefault(inst["id"], fresh_inst_stats())
        if not inst.get("enabled") or not inst.get("api_key"):
            stats["status"] = "disabled"; continue
        ok, ver = ArrClient(inst["name"], inst["url"], inst["api_key"]).ping()
        prev_status = stats.get("status","unknown")
        stats["status"]  = "online" if ok else "offline"
        stats["version"] = ver
        # Notify only on transition online→offline
        if not ok and prev_status == "online":
            lang  = CONFIG.get("language","de")
            label = "Instanz offline" if lang=="de" else "Instance offline"
            desc  = (f"**{inst['name']}** ist nicht erreichbar" if lang=="de"
                     else f"**{inst['name']}** is unreachable")
            discord_send("offline", label, desc, inst["name"])

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
        for inst in CONFIG["instances"]:
            s = STATE["inst_stats"].get(inst["id"], fresh_inst_stats())
            for k in ("missing_searched","upgrades_searched","skipped_cooldown","skipped_daily"):
                s[k] = 0
        ping_all()
        removed = db.purge_expired(CONFIG.get("cooldown_days",7))
        if removed:
            log_act("System", msg("db_pruned", n=removed), "", "info")
            # Notify Discord: items back off cooldown
            lang = CONFIG.get("language","de")
            label = "Cooldown abgelaufen" if lang=="de" else "Cooldown expired"
            desc  = (f"{removed} Item(s) wieder verfügbar" if lang=="de"
                     else f"{removed} item(s) available again")
            discord_send("cooldown", label, desc, "System")
        for inst in CONFIG["instances"]:
            if STOP_EVENT.is_set(): break
            if not inst.get("enabled") or not inst.get("api_key"): continue
            if STATE["inst_stats"].get(inst["id"],{}).get("status") != "online":
                log_act(inst["name"], msg("skipped_offline"), "", "warning"); continue
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
        # ── Hunt ──
        try: run_cycle()
        except Exception as e: log_act("System", msg("error"), str(e)[:200], "error")
    STATE["running"] = False; STATE["next_run"] = None

# ─── Flask Routes ─────────────────────────────────────────────────────────────

# ─── Auth / CSRF ──────────────────────────────────────────────────────────────
_PASSWORD = os.environ.get("MEDIASTARR_PASSWORD", "").strip()

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
    """For API routes: return 401 JSON if not authenticated."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if _PASSWORD and not session.get("authenticated"):
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login_page():
    next_path = request.args.get("next", "/")
    error = None
    if request.method == "POST":
        # CSRF for login form itself
        token = request.form.get("csrf_token", "")
        if not secrets.compare_digest(token, _csrf_token()):
            error = "Invalid request. Please try again."
        elif _PASSWORD and secrets.compare_digest(
                request.form.get("password", ""), _PASSWORD):
            session["authenticated"] = True
            session.permanent = True
            return redirect(next_path or "/")
        else:
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
    return render_template("index.html", csrf_token=_csrf_token())

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
    ok, err = validate_url(url)
    if not ok: return jsonify({"ok":False,"msg":f"URL ungültig: {err}"}),400
    key = safe_str(d.get("api_key",""), 128)
    ok, err = validate_api_key(key)
    if not ok: return jsonify({"ok":False,"msg":f"API Key: {err}"}),400
    try:
        ok, ver = ArrClient(itype, url, key).ping()
        return jsonify({"ok":ok,"version":ver})
    except: return jsonify({"ok":False,"msg":"Verbindung fehlgeschlagen"})

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
                "name":nm.strip(),"url":url,"api_key":key,"enabled":True})
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
def api_instances_get():
    safe = [{k:v for k,v in inst.items() if k!="api_key"} for inst in CONFIG["instances"]]
    return jsonify({"ok":True,"instances":safe,"stats":STATE["inst_stats"]})

@app.route("/api/instances", methods=["POST"])
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
    inst={"id":make_id(),"type":itype,"name":nm.strip(),"url":url,"api_key":key,"enabled":True}
    CONFIG["instances"].append(inst)
    STATE["inst_stats"][inst["id"]] = fresh_inst_stats()
    save_config(CONFIG); return jsonify({"ok":True,"id":inst["id"]})

@app.route("/api/instances/<inst_id>", methods=["PATCH"])
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
        inst["api_key"] = key
    if "enabled" in d: inst["enabled"] = bool(d["enabled"])
    save_config(CONFIG); return jsonify({"ok":True})

@app.route("/api/instances/<inst_id>", methods=["DELETE"])
def api_instances_delete(inst_id:str):
    before = len(CONFIG["instances"])
    CONFIG["instances"] = [i for i in CONFIG["instances"] if i["id"]!=inst_id]
    if len(CONFIG["instances"]) == before: return jsonify({"ok":False,"error":"Nicht gefunden"}),404
    STATE["inst_stats"].pop(inst_id,None); save_config(CONFIG)
    return jsonify({"ok":True})

@app.route("/api/instances/<inst_id>/ping")
def api_instances_ping(inst_id:str):
    inst = next((i for i in CONFIG["instances"] if i["id"]==inst_id), None)
    if not inst: return jsonify({"ok":False,"error":"Nicht gefunden"}),404
    if not inst.get("api_key"): return jsonify({"ok":False,"msg":"Kein API Key"})
    try:
        ok,ver = ArrClient(inst["name"],inst["url"],inst["api_key"]).ping()
        STATE["inst_stats"].setdefault(inst_id,fresh_inst_stats())["status"] = "online" if ok else "offline"
        STATE["inst_stats"][inst_id]["version"] = ver
        return jsonify({"ok":ok,"version":ver})
    except: return jsonify({"ok":False,"msg":"Verbindung fehlgeschlagen"})

# ── Main API ──────────────────────────────────────────────────────────────────
@app.route("/api/state")
@_api_auth_required
def api_state():
    today_n=db.count_today(); limit=CONFIG.get("daily_limit",0)
    instances_safe=[{k:v for k,v in i.items() if k!="api_key"} for i in CONFIG["instances"]]
    return jsonify({
        "running":STATE["running"],"last_run":STATE["last_run"],
        "next_run":STATE["next_run"],"cycle_count":STATE["cycle_count"],
        "total_searches":db.total_count(),"daily_count":today_n,
        "daily_limit":limit,"daily_remaining":max(0,limit-today_n) if limit>0 else None,
        "inst_stats":STATE["inst_stats"],"instances":instances_safe,
        "server_time": fmt_time(now_local()),
        "server_tz":   CONFIG.get("timezone","UTC"),
        "activity_log":list(STATE["activity_log"])[:60],
        "config":{
            "hunt_missing_delay":   CONFIG["hunt_missing_delay"],
            "hunt_upgrade_delay":   CONFIG["hunt_upgrade_delay"],
            "max_searches_per_run": CONFIG["max_searches_per_run"],
            "daily_limit":          CONFIG.get("daily_limit",20),
            "cooldown_days":        CONFIG.get("cooldown_days",7),
            "request_timeout":      CONFIG.get("request_timeout",30),
            "jitter_max":           CONFIG.get("jitter_max",300),
            "sonarr_search_mode":   CONFIG.get("sonarr_search_mode","season"),
            "search_upgrades":      CONFIG.get("search_upgrades",True),
            "dry_run":              CONFIG["dry_run"],
            "language":             CONFIG["language"],
            "theme":                CONFIG.get("theme","dark"),
            "timezone":             CONFIG.get("timezone","UTC"),
            "auto_start":           CONFIG["auto_start"],
            "instance_count":       len(CONFIG["instances"]),
            "discord": {
                k: v for k, v in CONFIG.get("discord", {}).items()
                if k not in ("webhook_url", "stats_last_sent_at")  # never expose
            },
            "discord_configured": bool(CONFIG.get("discord",{}).get("webhook_url","")),
            "discord_webhook_set": bool(CONFIG.get("discord",{}).get("webhook_url","")),
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
    raw_delay = clamp_int(d.get("hunt_missing_delay", CONFIG["hunt_missing_delay"]), MIN_INTERVAL_SEC, 86400, CONFIG["hunt_missing_delay"])
    CONFIG["hunt_missing_delay"]   = raw_delay
    CONFIG["hunt_upgrade_delay"]   = clamp_int(d.get("hunt_upgrade_delay",   CONFIG["hunt_upgrade_delay"]),   MIN_INTERVAL_SEC, 86400, CONFIG["hunt_upgrade_delay"])
    CONFIG["max_searches_per_run"] = clamp_int(d.get("max_searches_per_run", CONFIG["max_searches_per_run"]), 1, 500, CONFIG["max_searches_per_run"])
    CONFIG["daily_limit"]          = clamp_int(d.get("daily_limit",          CONFIG.get("daily_limit",20)),   0, 9999, CONFIG.get("daily_limit",20))
    CONFIG["cooldown_days"]        = clamp_int(d.get("cooldown_days",        CONFIG.get("cooldown_days",7)),  1, 365, CONFIG.get("cooldown_days",7))
    CONFIG["request_timeout"]      = clamp_int(d.get("request_timeout",      CONFIG.get("request_timeout",30)),5, 300, 30)
    CONFIG["jitter_max"]           = clamp_int(d.get("jitter_max",           CONFIG.get("jitter_max",300)),   0, 3600, 300)
    if "dry_run"         in d: CONFIG["dry_run"]         = bool(d["dry_run"])
    if "auto_start"      in d: CONFIG["auto_start"]      = bool(d["auto_start"])
    if "search_upgrades" in d: CONFIG["search_upgrades"] = bool(d["search_upgrades"])
    mode = safe_str(d.get("sonarr_search_mode",""), 10)
    if mode in ALLOWED_SONARR_MODES: CONFIG["sonarr_search_mode"] = mode
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
                         "notify_cooldown","notify_limit","notify_offline","notify_stats"):
            if bool_key in dc_in: dc[bool_key] = bool(dc_in[bool_key])
        if "stats_interval_min" in dc_in:
            dc["stats_interval_min"] = clamp_int(dc_in.get("stats_interval_min", 60), 1, 10080, 60)
        if "rate_limit_cooldown" in dc_in:
            dc["rate_limit_cooldown"] = clamp_int(dc_in.get("rate_limit_cooldown", 5), 1, 300, 5)
        if "webhook_url" in dc_in:
            url = safe_str(dc_in["webhook_url"], 512).strip()
            if url == "" or url.startswith(("http://","https://")):
                dc["webhook_url"] = url
    save_config(CONFIG); return jsonify({"ok":True})

# ── History API ───────────────────────────────────────────────────────────────
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
    n=db.clear_all(); log_act("System","DB geleert",f"{n} Einträge","warning")
    return jsonify({"ok":True,"removed":n})

@app.route("/api/history/clear/<inst_id>", methods=["POST"])
def api_history_clear_inst(inst_id:str):
    n=db.clear_service(inst_id); log_act("System",f"DB geleert ({inst_id})",f"{n}","warning")
    return jsonify({"ok":True,"removed":n})

# ── Timezone helper ───────────────────────────────────────────────────────────
@app.route("/api/timezones")
def api_timezones():
    """Return common timezone list for the settings dropdown."""
    common = [
        "UTC","Europe/Berlin","Europe/Vienna","Europe/Zurich","Europe/London",
        "Europe/Paris","Europe/Amsterdam","Europe/Rome","Europe/Madrid",
        "America/New_York","America/Chicago","America/Denver","America/Los_Angeles",
        "America/Sao_Paulo","Asia/Tokyo","Asia/Shanghai","Asia/Kolkata",
        "Asia/Dubai","Australia/Sydney","Pacific/Auckland",
    ]
    return jsonify({"ok":True,"timezones":common})

# ── Discord test endpoint ─────────────────────────────────────────────────────
@app.route("/api/discord/test", methods=["POST"])
@_api_auth_required
def api_discord_test():
    dc = CONFIG.get("discord", {})
    if not dc.get("webhook_url",""):
        return jsonify({"ok":False,"error":"Kein Webhook URL konfiguriert" if CONFIG.get("language","de")=="de" else "No webhook URL configured"}),400
    lang = CONFIG.get("language","de")
    if lang == "de":
        label = "🔔 Mediastarr Test"
        desc  = "Dies ist eine Test-Benachrichtigung von Mediastarr v6.\nWenn du das siehst, ist der Webhook korrekt konfiguriert."
        f_status  = "Status"
        f_ok      = "✓ Verbunden"
        f_ver     = "Version"
        f_inst    = "Instanzen"
        f_enabled = "Benachrichtigungen"
    else:
        label = "🔔 Mediastarr Test"
        desc  = "This is a test notification from Mediastarr v6.\nIf you see this, the webhook is configured correctly."
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
        {"name": f_ver,     "value": "v6.1.1", "inline": True},
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
def api_discord_stats_now():
    """Manually trigger a stats report."""
    dc = CONFIG.get("discord", {})
    if not dc.get("webhook_url",""):
        lang = CONFIG.get("language","de")
        return jsonify({"ok":False,"error":"Kein Webhook URL konfiguriert" if lang=="de" else "No webhook URL"}),400
    discord_send_stats()
    return jsonify({"ok": True})


# ─── Startup ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log_act("System", msg("app_start"), "", "info")
    if CONFIG.get("setup_complete"):
        _ensure_inst_stats(); ping_all()
        if CONFIG.get("auto_start", True):
            hunt_thread = threading.Thread(target=hunt_loop, daemon=True); hunt_thread.start()
            log_act("System", msg("auto_start"), "", "info")
    else:
        log_act("System", msg("setup_required"), "", "warning")
    app.run(host="0.0.0.0", port=7979, debug=False)
