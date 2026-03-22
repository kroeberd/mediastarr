# Changelog

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
