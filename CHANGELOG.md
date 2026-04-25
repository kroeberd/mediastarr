# Changelog

## [v7.1.10] — 2026-04-25

### Fixed
- **Lidarr tab renders empty** — two bugs: (1) the tab pane had `style="display:none"` hardcoded inline, overriding the CSS `.tab-pane.active` mechanism used by all other tabs; (2) `switchTab()` was missing the `renderSettingsInstances('lidarr')` call so the instance list was never populated. Both fixed.
- **README missing Lidarr** — Lidarr was not mentioned in the feature tables or roadmap. Added to both EN and DE feature tables and roadmap.

## [v7.1.9] — 2026-04-25

### Added — Lidarr integration EXPERIMENTAL
- **Lidarr instance type** — add Lidarr instances alongside Sonarr and Radarr in Settings -> new Lidarr tab. The tab shows a prominent yellow EXPERIMENTAL warning; the tab button carries an EXPERIMENTAL badge.
- **`hunt_lidarr_instance()`** — full hunt function: fetches missing albums via `GET /wanted/missing`, shuffles randomly, applies cooldown and daily limits, fires `AlbumSearch` commands. Upgrade search via `GET /wanted/cutoff` (enabled per-instance).
- **Deep-link to Lidarr artist** — history rows for album searches link to `<lidarr_url>/artist/<id>`.
- **`lidarr_daily_limit`** — new global config key (default 0 = unlimited), settable in the Lidarr settings tab.
- **`ALLOWED_TYPES`** — expanded to include `lidarr`. Invalid types still rejected by API.
- **History: album types** — status icon for `album` and `album_upgrade` item types; type labels translated DE/EN.
- **i18n** — `lbl_experimental`, `lbl_lidarr_daily`, `hint_lidarr_daily` added. Coverage: 104/104 keys.

### Functional tests (v7.1.9)
| Test | Status |
|---|---|
| lidarr in ALLOWED_TYPES | OK |
| hunt_lidarr_instance callable | OK |
| Lidarr dispatch in run_cycle | OK |
| lidarr_daily_limit in DEFAULT_CONFIG | OK |
| Invalid type rejected by API | OK |
| api_key not in /api/state | OK |
| JS syntax | OK |
| Python syntax | OK |
| i18n 104/104 keys | OK |

## [v7.1.8] — 2026-04-17

### Added
- **History log: expandable rows** — clicking any row expands a detail panel showing: full timestamp, search duration, instance name, total search count, result status. All rows collapse automatically when another is opened.
- **History log: Deep-link to Sonarr/Radarr** — each history entry now stores the native arr item ID and constructs a direct link (`/movie/<id>` or `/series/<id>`) to the corresponding Sonarr/Radarr item. "Open in Radarr" / "Open in Sonarr" button appears in the expanded row when the instance URL is known.
- **History log: search duration** — `do_search()` now measures the time of the API command call in milliseconds and stores it in the DB. Displayed as `1.8s` or `342ms` in the detail panel.
- **History log: status icons** — each row now shows a type icon: 📺 episode, 🎬 movie, ⬆️ upgrades. Service chip shows instance name (truncated) instead of raw instance ID.
- **Jitter: extended to 24 hours** — jitter max increased from 60 min to 1440 min (24h) in the UI input and backend clamp. Useful for very low-frequency background automation scenarios.

### Changed
- **DB schema** — three new columns added via non-destructive `ALTER TABLE` migration (backward-compatible with existing databases):
  - `arr_id INTEGER` — native Sonarr/Radarr item ID for deep linking
  - `arr_url TEXT` — pre-built deep link URL (`<instance_url>/movie/<id>` or `/series/<id>`)
  - `duration_ms INTEGER` — command call duration in milliseconds

## [v7.1.7] — 2026-04-14

### Added
- **Kubernetes deployment** — new `k8s/` directory with production-ready manifests:
  - `namespace.yaml` — dedicated `mediastarr` namespace
  - `pvc.yaml` — 1 Gi PersistentVolumeClaim for `/data` (StorageClass configurable)
  - `deployment.yaml` — single-replica Deployment (`strategy: Recreate` for SQLite safety) with liveness, readiness and startup probes on `/api/state`; resource requests/limits; `terminationGracePeriodSeconds: 30`; Secret-based password injection
  - `service.yaml` — ClusterIP Service on port 7979
  - `ingress.yaml` — optional Ingress with nginx/Traefik annotations and cert-manager TLS support
  - `secret.yaml` — Secret template for `MEDIASTARR_PASSWORD`
  - `kustomization.yaml` — deploy everything with a single `kubectl apply -k k8s/`
- **Important:** `replicas: 1` is mandatory — SQLite does not support concurrent writers. The deployment strategy is `Recreate` (not `RollingUpdate`) to prevent two pods running simultaneously.

## [v7.1.6] — 2026-04-14

### Fixed
- **Bug #52 — Dashboard mixed language** — two elements were hardcoded DE with no translation mechanism: `limit-bar-label` ("Tageslimit") had no `data-i18n` attribute; the "Verlauf" history button used a static text node. Both now use `data-i18n` with keys `lbl_daily_limit` (DE: "Tageslimit" / EN: "Daily limit") and the existing `history` key. "Reset DB" button also wrapped in `data-i18n`.

### Changed
- **Font: body text** — changed from `DM Mono` (monospace) to `Inter` for body text. DM Mono was incorrectly set as the `font-family` on `<body>`, making all body copy render in a monospace font — hard to read, especially in dark mode. DM Mono is now used only for code, console output, and mono-specific UI elements (unchanged). Inter is loaded from Google Fonts alongside the existing Bricolage Grotesque and DM Mono.

## [v7.1.5] — 2026-04-12

### Added
- **FAQ page** — new sidebar entry "💬 FAQ" with accordion-style Q&A, 11 questions per language (DE + EN). Language switches with the app language or independently via EN/DE toggle in the page header. Questions include:
  - Is Huntarr the predecessor of Mediastarr?
  - Is the developer of Huntarr also the developer of Mediastarr?
  - How secure is Mediastarr? (full security feature list)
  - What technology is Mediastarr built on?
  - How does the search work exactly? (step-by-step)
  - What does "upgrade" mean? (with 3 worked examples)
  - What is the cooldown for?
  - Why does "FEHLEND" show a large number like 175,000?
  - What does "Dry Run" do?
  - Can I run multiple Sonarr/Radarr instances?
  - Is Mediastarr free and open source?

### Changed
- **FAQ visual redesign** — questions grouped into three named categories (📖 Background, 🔒 Security, ⚙ How it works) with icon badges; security features displayed as 2-column grid; tech stack as structured rows; search steps as numbered CSS counters; upgrade examples with orange left-border accent cards; animated `+`→`×` accordion icon with orange glow on open; subtitle under page title switches with language

### Security & Functional Test (v7.1.5)
- FAQ page tested: accordion open/close ✓, DE/EN language switch via button ✓, setLang() sync ✓, no JS errors ✓, nav item visible ✓, dashboard/settings still work after FAQ navigation ✓

## [v7.1.4] — 2026-04-08

### Fixed
- **Bug #51 — English translation broken** — `data-de`/`data-en` inline spans (used in Settings section titles like "Verhalten", "Globale Filter") were never applied by `applyLang()`. Only `[data-i18n]` was handled. Added a second pass in `applyLang()` to translate all `[data-de][data-en]` elements when the language switches.
- **Dashboard stats reset to 0 during searches** — per-cycle counters (`missing_searched`, `upgrades_searched`, `skipped_cooldown`, `skipped_daily`, `missing_found`, `upgrades_found`) were zeroed in `run_cycle()` before `ping_all()`, so the dashboard showed all-zero instance cards for several seconds at the start of every cycle. Counters are now reset at the start of each individual hunt function (`hunt_sonarr_instance`, `hunt_radarr_instance`) instead, so the previous cycle's values are visible until the new hunt actually begins.
- **CodeQL #12 — Stack trace exposure in `/api/setup/ping`** — exception-derived `detail` variable could taint the JSON response. Fixed by isolating the exception handler: ping result stored in clean local variables; exception only logs `type(e).__name__`, never flows to response.
- **CodeQL #13 — Stack trace exposure in `/api/instances/<id>/ping`** — same pattern. Fixed identically.

### Added
- **Tooltips throughout Settings UI** — `title` attributes added to all number inputs, selects, and action buttons in General, Sonarr, Radarr, and Discord settings. Hovering shows a concise description in the browser's native tooltip. CSS highlight on hover added.
- **i18n completeness audit** — Found 16 `data-i18n` keys used in HTML that had no entry in `T.de` or `T.en`: `btn_rotate_log`, `btn_add_maint`, `btn_setup`, `lbl_public`, `hint_public`, `lbl_theme`, `lbl_language`, `opt_dark/light/oled/system`, `console_title/waiting/live/autoscroll`, `stat_tab_all`. All 16 added to both language objects. Settings now fully translates in EN mode: "Log rotieren"→"Rotate log", "+ Fenster hinzufügen"→"+ Add window", "Öffentlicher API-State"→"Public API state", "Setup zurücksetzen"→"Reset setup", etc.

### Security Audit (v7.1.4)
| Check | Status |
|---|---|
| CSRF protection on all mutating API routes | ✅ |
| API keys never in `/api/state` response | ✅ |
| Stack trace not in ping responses (CodeQL #12+#13) | ✅ |
| SSRF protection on all URL inputs | ✅ |
| Input validation (API key regex, name, URL length) | ✅ |
| Brute-force lockout (10 attempts → 5 min block) | ✅ |
| Security headers (X-Frame-Options, CSP, COEP) | ✅ |
| Open redirect prevention (allowlist only) | ✅ |
| AES-256 encryption for secrets at rest | ✅ |
| config.json chmod 0600 | ✅ |

### Functional Tests (v7.1.4)
| Test | Status |
|---|---|
| Server starts and `/api/state` returns 200 | ✅ |
| `api_key` NOT present in `/api/state` response | ✅ |
| `version` field correct (`v7.1.4`) in state | ✅ |
| `POST /api/config` updates and persists config | ✅ |
| `GET /api/config/export` returns valid JSON | ✅ |
| `GET /api/history` and `/api/history/stats` respond | ✅ |
| `GET /api/timezones` returns timezone list | ✅ |
| `GET /api/log/status` responds | ✅ |
| `X-Frame-Options: DENY` in all responses | ✅ |
| `Cache-Control: no-store` on API routes | ✅ |
| Non-existent instance ping returns 404 | ✅ |

## [v7.1.3] — 2026-04-05

### Fixed
- **Bug #50 — Webhook URL vanishes instantly** — root cause: `oninput="_autosaveDc()"` on the `dc-url` field triggered `saveDc()` on every keystroke, which called `fetchState()`, which called `updateUI()`. After save, `urlEl.value = ''` also explicitly wiped the field. Fixed by: removing `oninput` from `dc-url` (URL only saves on explicit Save button click), removing the `urlEl.value = ''` clear after save, and replacing `fetchState()` with `setTimeout(fetchState, 1000)` in `saveDc()` so the field is not wiped while the user is still interacting with the Discord tab
- **Bug #49 — Save button in wrong position in Discord update card** — the update notification card used `class="dc-ev-header"` instead of `class="dc-ev-top"` like all other event cards; the `dc-ev-header` class is not defined in CSS, causing the toggle and button to render outside the card flex layout. Fixed by changing to `dc-ev-top`.

## [v7.1.2 — patch] — 2026-04-05

### Fixed
- **README language separation** — German API-Endpunkte block (`## 🔌 API-Endpunkte`, `### Webhook-Trigger Beispiel`, DE curl example) was inside the English section; moved to the German section where it belongs. EN section now contains only `## 🔌 API Reference` with English content. Structure verified: no German text in EN section, no English API block in DE section.

## [v7.1.2] — 2026-04-05

### Changed
- **UI redesign** — Settings tabs restructured into named sections (⏱ Intervals, 📊 Limits, ⚙ Behavior, 🌐 Global Filters, 🖥 System, 🏷 Tagging, ⚡ Stalled Monitor, 📝 Log, 🕐 Maintenance, 💾 Backup); each section has icon badge + title + subtitle for immediate orientation without reading field labels
- **Form system rebuilt** — labels now readable weight (not all-caps), inputs taller with focus ring, hints 11px with line-height; grid uses `auto-fill` columns adapting to content
- **Light theme fixed** — sidebar and topbar now render with correct white background; logo text readable in both themes
- **Screenshots updated** — all 9 Playwright screenshots regenerated with redesigned UI

### Fixed
- **Export config** — `exportConfig()` was never defined in the frontend; added as proper `async function` with Blob URL download trigger
- **Import config** — `importConfig()` used `await` but lacked `async` keyword, silently failing in strict JS contexts; fixed. Also added missing `X-CSRF-Token` header so import works when password protection is active
- **Import into fresh instance** — tested and verified: export from running instance → import into brand-new instance with no config restores all instances and `setup_complete` flag correctly
- **Dashboard cards blank** — `updateUI()` referenced undefined JS variable `_CURRENT_VERSION` (Python-only), causing a `ReferenceError` that aborted rendering before `renderInstanceCards()` was called; removed invalid reference
- **Discord update toggle not persisting** — `dcKeyMap` in `updateUI()` was missing `update: 'notify_update'` entry, so the toggle was never synced from server state and always reset to default on load; added
- **Discord update preview showing wrong version** — preview card used hardcoded `v7.1.2` / `v7.2.0` dummy values; now dynamically populated from `appState.version` (current) and `appState.config.latest_version` (new); opacity dimmed when no update is available
- **Update preview current version always "—"** — `version-display` ID did not exist in HTML; `_curVer` fell back to undefined `_CURRENT_VERSION` JS variable; fixed: `/api/state` now returns `version: _CURRENT_VERSION`, preview reads from `appState.version`
- **Demo link in sidebar** — `🎮 demo.mediastarr.de` link was inadvertently added to the app sidebar during design work; removed

### Added
- **Dashboard update badge** — orange pill next to status indicator shows available version when GitHub has a newer release; click opens GitHub Releases page
- **Sidebar update badge** — small `🆕 vX.Y.Z` badge below the logo in the sidebar, visible only when an update is available; links to GitHub Releases
- **Discord: update notification toggle** — new "🆕 Update available" card in Discord settings; sends rich embed (current vs. new version, GitHub release link) when a new version is detected; configurable on/off via `notify_update`; gated so no notification fires if toggle is disabled
- **`/api/state` exposes `version`** — the app's own running version (`_CURRENT_VERSION`) is now included in every `/api/state` response, enabling the frontend to display it without DOM scraping

## [v7.1.1] — 2026-04-03

### Fixed
- **Bug #47 — Discord test shows "v6"** — `api_discord_test()` had hardcoded version string `"Mediastarr v6"` in both DE and EN notification text. Now uses `{_CURRENT_VERSION}` dynamically. Fallback version string also updated from `"v6.4.5"` to `"v7.1.1"`

### Added
- **Feature #41 — AES-256 encryption for API keys and webhooks** — `cryptography` (Fernet/AES-128-CBC+HMAC) added to `requirements.txt`; auto-generates `/data/.secret_key` (mode 0600) on first run; `encrypt_secret()` / `decrypt_secret()` helpers with `"enc:"` prefix; backward compatible (unencrypted values still work); `ArrClient` decrypts on use; encryption applied to: API keys on `PATCH /api/instances/<id>`, `POST /api/instances`, setup wizard; Discord webhook URLs on save. Gracefully degrades if `cryptography` is not installed
- **Feature #48 — Tooltips / hints for all settings fields** — added `form-hint` elements and `title` attributes to previously un-annotated fields: API timeout, log level dropdown, Sonarr search mode, upgrade toggle, timezone; all hints in both DE and EN via i18n keys
- **SECURITY.md updated** — version table changed to `v7.1.x (latest) ✅` and `< v7.1.0 ❌`; security features list updated to include AES encryption, censoring, and CodeQL-verified fixes

### Changed
- **`requirements.txt`** — `cryptography` added as optional dependency for API key encryption

### Security fixes (included in v7.1.0)
- **CodeQL #8 — Information exposure in setup ping** (`py/stack-trace-exposure`) — `detail[:100]` from `ArrClient.ping()` still flowed from exception via `summarize_ping_error(str(e))`. Fix: introduced `_safe_ping_msg()` with a hardcoded `_SAFE_PING_MESSAGES` allowlist. Only strings from the allowlist are returned; anything else becomes `"Connection failed"`. Breaks the taint chain at the source.
- **CodeQL #9 — Information exposure in instance ping** (`py/stack-trace-exposure`) — same root cause as #8 in the `/api/instances/<id>/ping` route. Same fix applied.
- **CodeQL #7 — URL redirect from remote source** (`py/url-redirection`) — previous fix validated `startswith("/")` and `"//"` absence, but CodeQL still flags `request.args.get()` as tainted. Hardened with `urllib.parse.urlparse()`: rejects any URL with a scheme, netloc, or `//` prefix. Now fully breaks the taint flow.

## [v7.1.0] — 2026-04-01

### Added

**Feature #46 — Stalled Download Monitor**
- Monitors the Sonarr/Radarr queue API (`GET /api/v3/queue`) on every hunt cycle
- Detects stalled downloads via `trackedDownloadStatus` ("warning"/"error"), `trackedDownloadState` ("stalled"), and keyword scan in `statusMessages` ("no seeds", "no peers", "dead", etc.)
- Two-stage detection: first sighting starts a timer; action only fires after `stall_threshold_min` minutes (default 60, min 5)
- Two actions selectable:
  - **New search** — removes download from client (with blocklist flag so it won't re-grab), then triggers `MoviesSearch` / `EpisodeSearch` / `SeriesSearch`
  - **Warn only** — Discord notification without removing the download
- **Master switch** in Settings → General → Stalled Download Monitor (global on/off)
- **Per-instance override** via `PATCH /api/instances/<id>` (`stall_monitor_enabled`: `null` = use global, `true` / `false` = override)
- Configurable threshold (minutes) and action via UI and `POST /api/config`
- Stall state tracked in-memory (`_stall_seen` dict keyed by `instance_id:downloadId`)
- `ArrClient.delete_with_params()` method added for queue item removal with `blocklist` + `removeFromClient` params

**Feature #41 — API Key Censoring**
- `_censor_log(text)` function automatically masks any 32–128 character alphanumeric string in log text
- Applied to all `log_act()` calls — both the action and item fields
- Pattern: first 4 + `****` + last 4 chars (e.g. `abc1****3d4e`)
- API keys are already excluded from `/api/state` (`k != "api_key"` filter) and webhook URLs excluded from Discord config in state
- Webhook URLs (Sonarr, Radarr, main) never returned in `/api/state`

### Changed
- **README** — roadmap updated: all completed features marked `[x]` with version tags through v7.1.0; new roadmap items added (Gotify/Apprise, per-indexer stall, import lists); language order confirmed EN → DE; features table updated with new v7.1.0 entries

## [v7.0.6 — security patch] — 2026-03-31

### Fixed (Security — CodeQL alerts)
- **#1 Information exposure — API key validation** (`py/stack-trace-exposure`) — setup ping endpoint returned raw validation error detail. Now returns generic `"Invalid API key format"` message
- **#2 Information exposure — tag fetch** (`py/stack-trace-exposure`) — `/api/instances/<id>/tags` returned `str(e)[:200]` from exception. Now returns `"Could not fetch tags from instance"`
- **#3 Information exposure — instance ping detail** (`py/stack-trace-exposure`) — ping response `msg` field forwarded raw exception detail. Now truncated to 100 chars max
- **#4 Information exposure — JSON import** (`py/stack-trace-exposure`) — config import endpoint returned `f"Invalid JSON: {e}"` leaking parser internals. Now returns `"Invalid JSON in uploaded file"`
- **#5 Information exposure — log rotation** (`py/stack-trace-exposure`) — log reconfiguration endpoint returned `str(e)` from OS-level exception. Now returns generic message; original error still logged via `logger.error()`
- **#6 Information exposure — log status** (`py/stack-trace-exposure`) — `/api/log/status` returned `str(e)` from file system exception. Now logs debug-level and returns `"Could not read log status"`
- **#7 URL redirect from remote source** (`py/url-redirection`) — `?next=` parameter on login was passed directly to `redirect()` without validation. Now validated: must start with `/` and must not contain `//` (prevents protocol-relative redirect `//evil.com`)

### Changed
- **GitHub Actions workflow upgraded** — replaced simple `docker-publish.yml` with smart workflow:
  - **Weekly base image check** (`schedule: 0 3 * * 1`) — rebuilds automatically on Monday if `python:3.12-slim` digest changed
  - **Digest caching** — skips scheduled build if base image unchanged (saves CI minutes)
  - **`weekly` tag** — scheduled builds get a `weekly` tag alongside `latest`
  - **`no-cache` on schedule** — full rebuild on base image update
  - All other behavior identical to previous workflow

## [v7.0.6] — 2026-03-30

### Fixed
- **Tag multi-select redesigned** — replaced the native `<select multiple>` (requires Ctrl+click, no deselect) with an interactive chip UI: clickable tag chips that toggle on/off visually, active count badge, "Clear all" button, deselect support, chips auto-load when instance card opens

### Added
- **Feature #45 — Separate upgrade daily limit** — upgrades can now be limited independently from the missing-search daily limit:
  - **Global** — Settings → General: "Upgrade daily limit (0 = ∞)" — applies across ALL instances
  - **Per type** — Settings → Sonarr/Radarr tab: separate limit for Sonarr upgrades and Radarr upgrades
  - **Per instance** — instance card: "Upgrade limit/day" field per instance
  - **Logic**: global limit → per-type limit → per-instance limit (all checked independently)
  - Both missing-search DB function and new `count_today_upgrades()` / `count_today_upgrades_for_instance()` functions in `db.py`
  - Hunt loops check upgrade limit before entering the upgrades section and mid-loop to stop promptly
- **tini as PID 1 handler** — added to Dockerfile (`apt-get install tini`); gunicorn runs under tini via `ENTRYPOINT ["/usr/bin/tini", "--"]`. S6 overlay evaluated and rejected: Mediastarr is a single-process app, S6 would add ~20MB image size and startup complexity with zero benefit. tini is the minimal correct solution
- **Screenshots updated** — 9 new Playwright-generated screenshots (dashboard, settings, Sonarr tab, Discord tab, history, stats, log, setup wizard, mobile view); stored in `static/screenshots/`

### Changed
- **`_syncInstToggles()` updated** — now syncs upgrade_daily_limit field in instance cards without re-rendering

## [v7.0.5] — 2026-03-30

### Hotfix (v7.0.5 patch)
- **Duplicate Discord webhook fields** — two competing implementations coexisted in the Discord tab HTML (ids `dc-sonarr-url` and `dc-url-sonarr`); the old implementation with stray unclosed div caused layout breakage. Removed the old duplicate, kept the clean implementation
- **Duplicate tag filter widget** — the instance card had both a `<select multiple>` implementation and a chip-based implementation; both rendered simultaneously causing a split layout. Removed the chip implementation, kept and fixed the `<select multiple>` approach
- **Tag filter now auto-loads** — tags are fetched automatically when the instance card is rendered, no manual ↺ click required; selections are pre-highlighted based on saved `tag_filter_ids`
- **Tag filter uses correct field key** — both frontend `saveInstTagFilter()` and backend PATCH handler now consistently use `tag_filter_ids`

### Fixed
- **Tag toggle reverts after 5 seconds** — two root causes:
  1. `fetchState()` called `renderSettingsInstances()` on every 4-second poll, completely rebuilding the instance card HTML and wiping all loaded tag chips
  2. The tag settings sync (`c_always` block) ran outside the `_configDirty` guard, overwriting `_tagGlobalEnabled` and `cfg-tag-label` on every poll regardless of dirty state
  Fix: removed `renderSettingsInstances` calls from the poll cycle (replaced with lightweight `_syncInstToggles()` that only updates toggle DOM elements); tag sync now guarded by `!_configDirty`
- **Tag chips disappear within 5 seconds** — same root cause as above (card re-render wiped chip list). Fixed by the same change: cards only re-render on `switchTab()` and explicit save/add/delete actions
- **Bug #39 — Log rotation reconfigured spam** — `saveConfig()` included `log_max_mb`/`log_backups` on every call; `_reconfigure_file_logging()` fired unconditionally. Fix: only reconfigure when values actually changed
- **Bug #40 — Missing default name in add form** — Sonarr/Radarr name field had no default value. Fix: `value="Sonarr"` / `value="Radarr"` pre-populated
- **Bug #42 — Tagging toggle not persisting** — `saveConfig()` read `tog-tag-global.classList.contains('on')` which was always `false` when `_configDirty` prevented sync. Fix: `_tagGlobalEnabled` JS variable tracks state; `toggleTagGlobal()` / `updateUI()` maintain it; `saveConfig()` reads it
- **Sonarr activity log showed episode titles in season/series mode** — hunt correctly sent `SeasonSearch`/`SeriesSearch` but logged episode titles. Fix: log now shows `"Breaking Bad S01 (SeasonSearch)"` / `"Breaking Bad (SeriesSearch)"`
- **Language flag emojis not rendering in Chrome on Windows** — replaced with CSS-styled text badges (`.flag-de` black/yellow, `.flag-en` navy/white)
- **Sonarr/Radarr webhook URLs exposed in `/api/state`** — exclusion list had wrong key names (`webhook_url_sonarr` instead of `sonarr_webhook_url`). Fixed

### Added
- **Feature #37 — Tag-based filtering per instance** — `↺ Load tags` fetches all available tags from the instance; select one or more tags → only items carrying at least one selected tag are included in the cycle; empty = search all items (default); new endpoint `GET /api/instances/<id>/tags`; saved instantly via `PATCH /api/instances/<id>` with `tag_filter: [id, ...]`
- **Feature #38 — Separate Discord webhooks for Sonarr and Radarr** — two optional URL fields in Settings → Discord tab; per-type URL overrides main webhook; URLs never exposed in `/api/state`; DE + EN translations in Discord label system
- **Webhook trigger endpoint** — `POST /api/webhook/trigger` triggers an immediate hunt cycle from external automation; protected by `@_api_auth_required`; optional `{"source":"..."}` body field
- **Discord tab emoji** — tab now shows `💬 Discord`
- **`_syncInstToggles()` function** — lightweight instance toggle state sync that updates only toggle/label DOM elements without rebuilding card HTML — preserves tag chip selections across poll cycles
- **Debug logging** — hunt start logged at DEBUG with mode and upgrade flag; item counts after filters logged; season/series command type logged per-trigger

### Changed
- **README — API reference added** — full endpoint table (17 endpoints) in both EN and DE; webhook trigger curl example; feature table updated
- **Help page updated** — changelog and milestones sections include v7.0.1 through v7.0.5 (EN + DE)

## [v7.0.4] — 2026-03-27

### Added
- **Feature #36 — Tagging** — after each successful search, Mediastarr optionally adds a tag to the item in Sonarr/Radarr so you can see which entries it has already processed:
  - **Global toggle + label** in Settings → General → Tagging: enable/disable for all instances; configure the tag name (default: `mediastarr`); tag is created in each Sonarr/Radarr instance automatically if it does not exist
  - **Per-instance override** in each instance card: three states — `Global` (inherit global setting), `On` (always tag), `Off` (never tag); click to cycle through states
  - For Sonarr: the tag is applied to the **series** (tags live on series level in Sonarr, not on episodes)
  - For Radarr: the tag is applied to the **movie**
  - Tag is never applied in Dry Run mode
  - `_ensure_tag(client, label)` creates the tag if missing, returns existing ID if already present — idempotent
  - `_apply_tag(client, inst_type, item_id, item_data, tag_id)` adds tag only if not already present — no duplicate tags
  - `ArrClient.put()` method added for series/movie update calls
  - Default: **off** (global and per-instance)

### Changed
- **API page size increased 500 → 2000** — `wanted/missing` and `wanted/cutoff` now request up to 2000 records per call so large libraries are fully covered

## [v7.0.3] — 2026-03-26

### Fixed
- **Bug #33 — Upgrade search not working even when globally enabled** — `hunt_sonarr_instance()` and `hunt_radarr_instance()` read only `inst.get("search_upgrades", False)` — the per-instance toggle. The global `CONFIG["search_upgrades"]` toggle was saved but never consulted by the hunt functions. Per-instance default is `False`, so upgrades never ran even with the global switch enabled. Fix: `do_upgrades = CONFIG.get("search_upgrades", True) and inst.get("search_upgrades", False)` — global is master switch, per-instance is fine-grained control
- **Bug #34 — Per-instance Search Upgrades toggle not saved** — `PATCH /api/instances/<id>` had no `search_upgrades` branch. `toggleInstUpgrades()` sent the PATCH correctly but the backend silently ignored the field. Fix: added `if "search_upgrades" in d: inst["search_upgrades"] = bool(d["search_upgrades"])` to the PATCH handler
- **Bug #35 — Discord notifications toggle reverting on save** — clicking Save in any settings tab called `saveConfig()` → `fetchState()` → `updateUI()` re-synced `dcStates` from the server, overwriting local discord toggle changes that hadn't been saved yet via the Discord tab. Fix: `saveConfig()` now always includes current `dcStates` in the request body so discord state is persisted atomically with any settings save
- **Settings reverting on 4-second poll** — `fetchState()` runs every 4 seconds and `updateUI()` overwrote all config input fields on every poll tick. Any field the user edited but hadn't yet saved was overwritten seconds later. Fix: added `_configDirty` flag — set `true` on any `input`/`change` event inside `#page-settings`, cleared to `false` after a successful `saveConfig()`. While dirty, `updateUI()` skips the config sync block entirely
- **`dcStates.stats` was undefined** — `dcStates` was initialized without the `stats` key. Every `saveConfig()` sent `notify_stats: undefined` → backend received `null` → `bool(null) = False` → Discord stats notifications silently disabled on every general save. Fix: `dcStates` now initialized with `stats: false`
- **10 settings fields not synced back to DOM** — `updateUI()` never wrote these fields back after `fetchState()`: `jitter_max`, `request_timeout`, `imdb_min_rating`, `upgrade_target_resolution`, `sonarr_imdb_min_rating`, `sonarr_search_mode`, `sonarr_upgrade_target_resolution`, `radarr_imdb_min_rating`, `radarr_upgrade_target_resolution`, `timezone`. All now synced via additional `syncField()` calls in `updateUI()`
- **Log settings excluded from general save** — `log_min_level`, `log_max_mb`, `log_backups` were only saved via their own dedicated buttons; now included in every `saveConfig()` call so all settings save atomically from any tab
- **Sonarr/Radarr IMDb override `null` not cleared** — when IMDb override was unset (`null` = use global), the input field was left showing the last value instead of clearing to empty. Fix: always sync, `null` → `''`
- **Season/Series mode duplicate searches** — in `season` or `series` mode, `wanted/missing` returns individual episodes. Previously each episode triggered its own `SeasonSearch` or `SeriesSearch` — identical commands fired multiple times for the same target. Fix: dedup sets `_searched_seasons` and `_searched_series` track already-triggered targets per cycle and skip redundant commands
- **Upgrades ignored search mode** — the upgrade loop always used `EpisodeSearch` regardless of the configured Sonarr search mode. Fix: upgrade loop now applies the same `season`/`series`/`episode` logic as the missing search loop

### Changed
- **API page size increased from 500 → 2000** — `wanted/missing` and `wanted/cutoff` now request up to 2000 records per call so large libraries are fully covered

## [v7.0.2] — 2026-03-26

### Fixed
- **Skip items don't consume search slots** — Root cause found and fixed: `should_search()` returned reason `"daily_limit"` but the stats dict key is `"skipped_daily"`. This caused a `KeyError` on every daily-limit hit, which was silently swallowed by the `except` block — making the `return` (stop-instance) unreachable and stats tracking broken.
  - `should_search` now returns reason `"daily"` to match the `skipped_daily` stats key
  - `stats[f"skipped_{reason}"] += 1` now correctly increments `skipped_cooldown` or `skipped_daily`
  - Cooldown skips have always used `continue` (correct — next item tried) — no change
  - Daily-limit hits correctly stop the instance loop via `return` (limit is genuinely reached)

## [v7.0.1] — 2026-03-26

### Fixed
- **Missing EN translations** — Sonarr/Radarr behavior section headers, instance headings, "Same as global" option, all previously untranslated
- **Log level dropdown** — all 4 options now translated DE/EN via i18n keys
- **Jitter** — changed from seconds to minutes in UI (min 0, max 60); stored as seconds internally; backend converts on save; hint text updated

### Added
- **Per-type global daily limit** — Sonarr and Radarr each get their own global searches/day cap (0 = unlimited); configured in Settings → Sonarr/Radarr tabs; applies across ALL instances of that type; per-instance limits apply additionally
- **Upgrade toggle hint** — instance card now shows "(global limit ignored)" note next to the per-instance upgrade toggle to clarify that when enabled, only the instance-level daily limit applies (not the global daily limit)

## [v7.0.0] — 2026-03-26

### Added
- **Structured Logging System** — replaces ad-hoc `logger.*` calls with a unified `ms_log()` architecture:
  - Central `ms_log(level, service, action, item)` function — writes to Docker console, rotating log file, AND the UI activity log simultaneously
  - Convenience helpers: `ms_debug()`, `ms_info()`, `ms_warn()`, `ms_error()`
  - Four levels: `DEBUG` | `INFO` | `WARN` | `ERROR` (replaces the previous implicit INFO-only behaviour)
  - `log_min_level` config key — persisted to `config.json`, applied via `_apply_log_level()` at startup and on every config save
  - Docker/Unraid console output format: `2026-03-26 14:32:10,123 [INFO] [Sonarr] Missing searched: Breaking Bad S01E03`
  - Settings → General → Log Level dropdown — four options, saves to backend and syncs frontend immediately
- **MSLog v2 (frontend)** — complete rewrite of the browser console logger:
  - Timestamp format: `[2026-03-26 14:32:10] [INFO] message`
  - `MSLog.log(level, ...args)` central function
  - Helpers: `MSLog.debug()`, `MSLog.info()`, `MSLog.warn()`, `MSLog.error()`
  - `MSLog.setLevel('WARN')` — suppress below threshold; persisted via `sessionStorage`
  - `MSLog.getLevel()` — returns current level string
  - `trace()` kept as backwards-compatible alias for `debug()`
  - Level synced from server config on every `fetchState()` poll


## [v6.4.6] — 2026-03-26

### Fixed
- **Bug #29** — Startup log showed `Mediastarr v6.3.8 started` instead of current version; now uses `_CURRENT_VERSION` dynamically in both DE and EN messages, and in the Discord stats embed version field
- **Bug #21 / #28** — Console showed no entries (both standalone page and Settings-tab version): refactored `_appendConsoleLogs` to write to both `#console-output` and `#console-output-s` simultaneously via `_makeConsoleLine()` helper; `filterConsole()` and `clearConsole()` updated to cover both outputs
- **Bug #27** — Help page German translation broken: `setHelpLang()` now correctly toggles `.help-en`/`.help-de` visibility; `&amp;` entity in `help_title` i18n key replaced with literal `&`
- **Bug #23** — Homepage mobile view: language selector (`🇬🇧 / 🇩🇪`) not visible on small screens; added persistent `#mobile-lang-toggle` (fixed bottom-right, visible only on `max-width:768px`); `setLang()` now syncs both desktop and mobile buttons

### Added
- **Feature #32 — Upgrade toggle per instance**: each Sonarr/Radarr instance now has an independent `search_upgrades` toggle (default: **off**); toggle rendered in instance card in Settings → Sonarr/Radarr tabs; saved via `PATCH /api/instances/<id>`
- **Feature #26 — Improved logging**: `run_now` control action logs "Run now triggered" to activity log; `POST /api/config` saves log "Config saved" to activity log
- **Milestones updated**: v6.4.5 and v6.4.6 added to completed milestones in both DE and EN

### Changed
- `search_upgrades` is now a **per-instance** setting (default `false`) instead of a global config toggle
