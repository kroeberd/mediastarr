# Changelog

## [6.3.4] — 2026-03-22

### Fixed
- Mobile sidebar closes immediately after opening — rewritten with explicit state variable, prevents event race condition
- Mobile sidebar now has slide-in animation and hamburger morphs to ✕ when open
- Activity log: timestamps now show HH:MM only instead of HH:MM:SS (cleaner on small screens)

## [6.3.3] — 2026-03-22

### Added
- Timezone: `TZ` environment variable now respected on startup — applied automatically when config is still at default UTC (fixes Issue #7)
- Timezone: settings dropdown now loads all 498 IANA timezones dynamically instead of 8 hardcoded options
- Timezone: searchable text input replaces static select — type to filter (e.g. `chicago`, `berlin`)
- Buy Me a Coffee link added to app sidebar and homepage (nav, hero, footer)

### Fixed
- CHANGELOG v6.3.2: removed incorrect line about ALLOWED_THEMES


## [6.3.2]

### Changed
- Version bump: all references updated to v6.3.2
- Code audit: 19/19 functional tests passed — all features verified

### Fixed (Issue #7)
- Timezone: `TZ` environment variable now respected on startup — if `TZ` is set and config is still at default UTC, the env var is applied automatically
- Timezone: settings dropdown now loads all available IANA timezones from the server dynamically (previously showed only 8 hardcoded options)
- Timezone: searchable input field — type any part of the timezone name (e.g. `chicago`, `america`) to filter the full list

### Verified
- Per-app IMDb and resolution filters (Sonarr/Radarr independent)
- Mobile sidebar with hamburger menu and overlay
- Version check against GitHub Releases API with Discord notification
- Discord stats enriched with missing/upgrade/cycle counts
- All 18+ API routes auth-protected
- DB WAL mode, CYCLE_LOCK, interval minutes conversion all intact

## [6.3.1]

### Added
- Settings → Filter: Separate IMDb minimum rating and upgrade target resolution for Sonarr and Radarr independently
- Mobile sidebar: hamburger menu button opens/closes sidebar on small screens with overlay
- Version check: Mediastarr queries GitHub Releases API hourly and notifies via Discord when an update is available
- Discord notifications enriched: stats report and test message now include missing/upgrade/cycle counts and online instance ratio
- Update badge in sidebar: green indicator when a new version is available on GitHub

### Security
- `_CURRENT_VERSION` constant — used for version comparison and Discord notifications

### Fixed
- Apostrophe syntax error in homepage JS (unescaped `'` in single-quoted strings)
- Old `_cmpT` translation map replaced with correct `_why` map matching actual HTML IDs

## [6.3.0]

### Added
- Settings → Filter: IMDb minimum rating — only search content with IMDb ≥ threshold (0 = off, applies to Sonarr series and Radarr movies)
- Settings → Filter: Target resolution for upgrades — skip upgrade search if current quality already meets or exceeds target (WEB-DL 720p … Bluray 2160p)
- `_parse_release_dt()` / `_is_released()` — unreleased episodes and movies are now skipped automatically
- `MEDIASTARR_PUBLIC_URL` / `MEDIASTARR_PUBLIC_PORT` env vars — startup log shows the actual externally reachable setup URL
- `MEDIASTARR_SESSION_SECURE` env var — enables Secure flag on session cookie for HTTPS deployments
- Default-password warning bar in dashboard — shown when `MEDIASTARR_PASSWORD=change-me` is still set
- Mobile history view — horizontal scroll for narrow screens

### Changed
- Search intervals changed from seconds to minutes in the UI (stored as seconds internally for backward compatibility)
- Default missing interval: 15 min → 30 min
- Default upgrade interval: 30 min → 60 min
- Minimum interval remains 15 minutes
- Dashboard overview now shows interval in minutes
- Dry Run toggle now saves immediately without requiring manual Save
- Homepage (mediastarr.de) fully rewritten with DE/EN language switcher

### Security
- Session cookies now set `HttpOnly`, `SameSite=Lax`, and optionally `Secure`
- Setup connection test validates that Sonarr/Radarr URLs resolve to private/internal hosts only (SSRF protection)
- `config.json` file permissions set to 0600 after every save
- All 18 API routes verified to have auth protection when `MEDIASTARR_PASSWORD` is set
- Setup log message uses dynamic URL instead of hardcoded `localhost:7979`

### Improved
- `db.py`: `_get_conn()` helper + `Optional` type annotations
- History clear log messages now respect UI language (DE/EN)
- README: added "Why not Huntarr or its forks" comparison section (DE + EN)
- Homepage: new "Why Mediastarr" section with security incident summary and feature comparison table


## [6.2.0]

### Added
- Settings → Filter: IMDb minimum rating — only search content with IMDb ≥ threshold (0 = off, applies to Sonarr series and Radarr movies)
- Settings → Filter: Target resolution for upgrades — skip upgrade search if current quality already meets or exceeds target (WEB-DL 720p … Bluray 2160p)
- `_parse_release_dt()` / `_is_released()` — unreleased episodes and movies are now skipped automatically
- `MEDIASTARR_PUBLIC_URL` / `MEDIASTARR_PUBLIC_PORT` env vars — startup log shows the actual externally reachable setup URL
- `MEDIASTARR_SESSION_SECURE` env var — enables Secure flag on session cookie for HTTPS deployments

### Changed
- Search intervals changed from seconds to minutes in the UI (stored as seconds internally for backward compatibility)
- Default missing interval: 15 min → 30 min
- Default upgrade interval: 30 min → 60 min
- Minimum interval: 15 minutes (unchanged)
- Dashboard overview now shows interval in minutes

### Security
- Session cookies now set `HttpOnly`, `SameSite=Lax`, and optionally `Secure`
- Setup connection test now validates that Sonarr/Radarr URLs resolve to private/internal hosts only (SSRF protection)
- `config.json` file permissions set to 0600 after every save
- Setup log message uses dynamic URL instead of hardcoded `localhost:7979`

### Improved
- Project homepage fully rewritten with DE/EN language switcher
- `db.py`: `_get_conn()` helper + `Optional` type annotations
- History clear log messages now respect UI language (DE/EN)
- All API routes verified to have auth protection when `MEDIASTARR_PASSWORD` is set
## [6.1.2]

### Fixed
- Settings → Instances tab: `ReferenceError: isDE4 is not defined` caused silent crash — list never rendered
- `isDE4` was defined in `renderInstanceCards()` (dashboard) but missing in `renderSettingsInstances()` (settings)
- Previous fix in v6.1.1 moved the re-render call outside the `fetchState` function body (dead code) — corrected

## [6.1.1]

### Fixed
- **Critical (Unraid):** Container started but hunt loop never ran under gunicorn — startup code was inside `if __name__ == "__main__"` block which gunicorn never executes. Moved to `@app.before_request` hook with thread-safe lock so it runs correctly on first request regardless of how the server is started
- Settings → Instances tab: list showed "Lade..." permanently — `isDE4` variable used in `renderSettingsInstances()` was not defined in that function scope (defined in `renderInstanceCards()` instead), causing a silent `ReferenceError`
- Settings → Instances tab: re-render hook was placed outside `fetchState()` function body (dead code)
- `showPage('settings')` never triggered instance list render — only `switchTab('instances')` did
- `switchTab('instances')` now retries render if `appState` not yet populated

## [6.1.0]

### Added
- Optional password protection via `MEDIASTARR_PASSWORD` environment variable
- Login page (`/login`) with session-based authentication
- CSRF protection for all write requests — browser fetch interceptor injects `X-CSRF-Token` header automatically
- gunicorn as production server (replaces `python app/main.py`) — multi-threaded, more stable under load
- `requirements.txt` with all dependencies (flask, requests, gunicorn)
- `MEDIASTARR_PASSWORD` variable added to Unraid template and docker-compose files

### Notes
- Password protection is fully optional — if `MEDIASTARR_PASSWORD` is not set, Mediastarr behaves identically to v6.0.x
- When password is set: dashboard, setup wizard, and all API endpoints require authentication
- CSRF protection activates automatically when password is set

## [6.0.3]

### Improved (from community fork review)
- `db.py`: `threading.Lock()` → `threading.RLock()` (prevents deadlock on recursive calls)
- `db.py`: `_require_init()` guard on all public functions (clear error instead of silent crash)
- `db.py`: SQL injection fix in `get_history` — cutoff value now passed as query parameter
- `main.py`: `CYCLE_LOCK` prevents two cycles running simultaneously (e.g. rapid Run Now clicks)
- `main.py`: `_bootstrap_host()` auto-detects container IP for Sonarr/Radarr fallback URLs
- `main.py`: Discord enabled_parts now language-aware (DE/EN) and uses filter() instead of string concat
- `main.py`: `clamp_int()` used consistently for Discord rate-limit and stats-interval validation
- `main.py`: Run Now when stopped now starts a single cycle instead of full hunt loop
- `index.html` + `setup.html`: `escHtml()` prevents XSS in instance name/URL fields
- `index.html` + `setup.html`: `defaultArrUrl()` uses browser hostname for URL suggestions
- `index.html` + `setup.html`: All UI messages (save, delete, ping, errors) fully translated DE/EN
- `setup.html`: Changing instance type auto-updates URL placeholder
- `setup.html`: Step counter corrected to "Step 1 of 4"
- `README.md`: Instance URL examples use `[IP]` instead of Docker hostnames

## [6.0.2]

### Fixed
- Setup wizard: validation error "#2 () Name: Name fehlt" when clicking Finish Setup on Discord step — client-side validation now runs before server call and shows errors directly in the visible pane
- New instances added via "+ Add instance" now default to name "Sonarr" or "Radarr" (type-dependent) instead of empty string, reducing chance of missing name
- Backend validation error messages now respect selected language (DE/EN)

## [6.0.1]

### Fixed
- Setup wizard: "Finish Setup" button stuck when skipping Discord step — error element was in wrong pane (Pane 2) causing button to stay disabled and page to not advance
- Error messages in setup wizard now display correctly in Pane 3 (Discord step)
- Button text reset now language-aware (was hardcoded German)
- Catch block now shows user-facing error message in both DE and EN

## [6.0.0]

### Added
- Multi-instance: any combination of Sonarr/Radarr, fully optional
- Custom instance names (Sonarr HD, Sonarr 4K, Anime, …) — editable in settings
- Instance management directly in settings (add/rename/delete/toggle/ping)
- Discord: 6 configurable events + periodic statistics report
- Discord: Rate-limit protection (configurable cooldown per event type)
- Discord: Discord 429 detection + logging
- Discord: Full DE/EN translations for all labels and messages
- Episode title format: `Series – Episode Title – S01E01` (TBA/TBD suppressed)
- Full i18n for all log messages (DE/EN)
- Configurable timezone — all timestamps in local time
- Sonarr search granularity: episode / season / full series
- Upgrade search global toggle
- Jitter with enforced minimum 15-minute interval
- Statistics dashboard: bar charts, 24h timeline, per-instance breakdowns
- Unraid Community Apps XML template
- `/api/discord/stats` endpoint for manual stats report trigger
- `_ep_title()` helper with graceful fallback for missing series data

### Fixed
- `? S2026E3648` log entries (missing series title from Sonarr API)
- Log messages always in configured language (DE/EN)
- Language switcher now updates sidebar, nav labels, and instance cards
- Server time displayed in configured timezone (not UTC)
- Settings instances tab no longer redirects to setup wizard

### Changed
- `docker-compose.yml` reduced to minimum (port + volume only)
- Version badge in sidebar is now colour-highlighted with GitHub link

## [5.0.0]

- Multi-instance architecture replacing fixed sonarr/radarr config slots
- Setup wizard supports unlimited instances of any type

## [4.0.0]

- SQLite replaces JSON for search history
- Cooldown in days (default 7), configurable 1–365
- Release year stored in DB
- 3 themes: Dark / Light / OLED Black
- Migration path for existing databases
