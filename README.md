<div align="center">

<img src="https://raw.githubusercontent.com/kroeberd/mediastarr/refs/heads/main/logos/logo.png" width="128" alt="Mediastarr Logo"/>

# Mediastarr

**EN** · [DE](#de)

[![GitHub](https://img.shields.io/badge/GitHub-kroeberd%2Fmediastarr-orange?logo=github)](https://github.com/kroeberd/mediastarr)
[![Docker Hub](https://img.shields.io/docker/pulls/kroeberd/mediastarr?label=Docker%20Pulls&logo=docker)](https://hub.docker.com/r/kroeberd/mediastarr)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/Version-v6.0-ff6b2b)](https://github.com/kroeberd/mediastarr/releases)
[![Discord](https://img.shields.io/badge/Discord-Join-5865f2?logo=discord&logoColor=white)](https://discord.gg/8Vb9cj4ksv)

</div>

---

<!-- ENGLISH -->
<a name="en"></a>

**Automated media search for Sonarr & Radarr** — finds missing content and quality upgrades on a configurable schedule. Web dashboard, first-run wizard, SQLite history, multi-instance support, Discord notifications and 3 themes.

> **Note:** Independent project, built from scratch. Not affiliated with Huntarr.

## ✨ Features

| Feature | Details |
|---|---|
| 📺 Multiple Sonarr instances | Missing + Upgrades · Mode: Episode / Season / Series |
| 🎬 Multiple Radarr instances | Missing + Upgrades |
| 🏷️ Custom names | Each instance gets its own name (Sonarr 4K, Anime, …) |
| 🧙 First-Run Wizard | Browser-based setup — no config editing required |
| 🗄️ SQLite history | Title, year, result, search count, timestamps |
| ⏳ Cooldown | 1–365 days, configurable |
| 📊 Daily limit | Max searches per day (0 = unlimited) |
| 🎲 Random selection | Items picked randomly each cycle for even coverage |
| 🎲 Jitter | Random offset ±N sec (min. 15 min interval enforced) |
| 🔔 Discord | 6 events + periodic stats report + rate-limit protection |
| 🌐 Multilingual | German & English (UI + logs + Discord messages) |
| 🎨 3 themes | Dark / Light / OLED Black |
| 🕐 Timezone | Configurable — all timestamps in local time |
| 🔒 Secure | Whitelists, input validation, API keys never in state |

## 🚀 Quick Start

```bash
git clone https://github.com/kroeberd/mediastarr.git
cd mediastarr && mkdir data
docker compose up -d
open http://localhost:7979
```

## 🐳 Docker Compose

```yaml
services:
  mediastarr:
    image: kroeberd/mediastarr:latest
    container_name: mediastarr
    restart: unless-stopped
    ports:
      - "7979:7979"
    volumes:
      - /mnt/user/appdata/mediastarr:/data
```

## 📦 Unraid

Community Apps template: [`mediastarr.xml`](mediastarr.xml)

Manual: Repository `kroeberd/mediastarr:latest`, Port `7979:7979`, Volume `/mnt/user/appdata/mediastarr` → `/data`.

## 🔔 Discord Notifications

Settings → Discord:

| Event | Description |
|---|---|
| 🔍 Missing searched | Movie/series requested — title, instance, year |
| ⬆ Upgrade searched | Quality upgrade triggered |
| ⏳ Cooldown expired | Items available for search again |
| 🚫 Daily limit | Daily search limit reached |
| 📡 Instance offline | Instance not reachable |
| 📊 Statistics report | Periodic report (interval configurable) |

**Rate-limit protection:** Configurable minimum gap between same-type events (default 5 sec) — prevents Discord 429 errors.

**Join the community:** [discord.gg/8Vb9cj4ksv](https://discord.gg/8Vb9cj4ksv)

## ⚙️ Settings

| Setting | Default | Range |
|---|---|---|
| Missing interval | 900s | min. 900s (15 min) |
| Max searches/run | 10 | 1–500 |
| Daily limit | 20 | 0 = unlimited |
| Cooldown | 7 days | 1–365 days |
| Jitter max | 300s | 0 = off, max 3600s |
| API timeout | 30s | 5–300s |
| Sonarr search mode | Season | Episode / Season / Series |
| Search upgrades | On | On / Off |
| Timezone | UTC | any IANA timezone |
| Discord rate-limit | 5s | 1–300s |
| Discord stats interval | 60 min | 1–10080 min |

## 📡 API

```bash
GET  /api/state                    # Status, stats, config, log
POST /api/control                  # {"action":"start|stop|run_now"}
POST /api/config                   # Update configuration
GET  /api/instances                # List instances (no API keys)
POST /api/instances                # Add instance
PATCH /api/instances/{id}          # Update name/url/key/type/enabled
DELETE /api/instances/{id}         # Delete instance
GET  /api/instances/{id}/ping      # Test connection
GET  /api/history                  # Search history
POST /api/discord/test             # Send test message
POST /api/discord/stats            # Send stats report now
GET  /api/timezones                # Available timezones
```

---

<!-- DEUTSCH -->
<a name="de"></a>

<div align="center">

**DE** · [EN](#en)

</div>

**Automatisierte Mediensuche für Sonarr & Radarr** — sucht fehlende Inhalte und Qualitäts-Upgrades nach konfigurierbarem Zeitplan. Mit Web-Dashboard, First-Run-Wizard, SQLite-Verlauf, Multi-Instanz-Support, Discord-Benachrichtigungen und 3 Themes.

> **Hinweis:** Eigenständiges Projekt, von Grund auf neu entwickelt. Keine Verbindung zu Huntarr.

## ✨ Features

| Feature | Details |
|---|---|
| 📺 Mehrere Sonarr-Instanzen | Fehlend + Upgrades · Modus: Episode / Staffel / Serie |
| 🎬 Mehrere Radarr-Instanzen | Fehlend + Upgrades |
| 🏷️ Freie Benennung | Jede Instanz erhält einen eigenen Namen (Sonarr 4K, Anime, …) |
| 🧙 First-Run Wizard | Browser-Setup beim ersten Start — keine Config-Datei nötig |
| 🗄️ SQLite-Verlauf | Titel, Jahr, Ergebnis, Suchzähler, Timestamps |
| ⏳ Cooldown | 1–365 Tage, konfigurierbar |
| 📊 Tageslimit | Max. Searches pro Tag (0 = unbegrenzt) |
| 🎲 Zufällige Auswahl | Items werden pro Zyklus zufällig gewählt für gleichmäßige Abdeckung |
| 🎲 Jitter | Zufälliger Versatz ±N Sek. (min. 15 Min. Intervall erzwungen) |
| 🔔 Discord | 6 Events + periodischer Statistik-Bericht + Rate-Limit-Schutz |
| 🌐 Mehrsprachig | Deutsch & Englisch (UI + Logs + Discord-Nachrichten) |
| 🎨 3 Themes | Dark / Light / OLED Black |
| 🕐 Zeitzone | Konfigurierbar — alle Timestamps lokal |
| 🔒 Sicher | Whitelists, Input-Validierung, API-Keys nie im State |

## 🚀 Schnellstart

```bash
git clone https://github.com/kroeberd/mediastarr.git
cd mediastarr && mkdir data
docker compose up -d
# → http://localhost:7979
```

Der Setup-Wizard öffnet sich automatisch.

## 🐳 Docker Compose

```yaml
services:
  mediastarr:
    image: kroeberd/mediastarr:latest
    container_name: mediastarr
    restart: unless-stopped
    ports:
      - "7979:7979"
    volumes:
      - /mnt/user/appdata/mediastarr:/data
```

Für den Zugriff auf Sonarr/Radarr im gleichen Docker-Netz:

```yaml
    networks:
      - arr

networks:
  arr:
    external: true
```

## 📦 Unraid

Das Community Apps Template liegt in [`mediastarr.xml`](mediastarr.xml).

**Manuell hinzufügen:**
1. Unraid → Docker → Add Container → Advanced View
2. Repository: `kroeberd/mediastarr:latest`
3. Port: `7979:7979`
4. Volume: `/mnt/user/appdata/mediastarr` → `/data`
5. Apply → http://UNRAID-IP:7979

## 🔔 Discord-Benachrichtigungen

Einstellungen → Discord:

| Event | Beschreibung |
|---|---|
| 🔍 Fehlend gesucht | Film/Serie angefragt — Titel, Instanz, Jahr |
| ⬆ Upgrade gesucht | Qualitäts-Upgrade ausgelöst |
| ⏳ Cooldown abgelaufen | Items wieder für Suche freigegeben |
| 🚫 Tageslimit | Tageslimit ausgeschöpft |
| 📡 Instanz offline | Instanz nicht erreichbar |
| 📊 Statistik-Bericht | Periodischer Bericht (Intervall konfigurierbar) |

**Rate-Limit-Schutz:** Konfigurierbarer Mindestabstand zwischen gleichen Events (Standard 5 Sek.) — verhindert Discord 429-Fehler.

**Community:** [discord.gg/8Vb9cj4ksv](https://discord.gg/8Vb9cj4ksv)

## 🔢 Mehrere Instanzen

Beliebige Kombinationen direkt in den Einstellungen konfigurierbar:

- `Sonarr HD` → `http://sonarr:8989`
- `Sonarr 4K` → `http://sonarr4k:8989`
- `Sonarr Anime` → `http://sonarr-anime:8989`
- `Radarr HD` → `http://radarr:7878`

## ⚙️ Einstellungen

| Einstellung | Standard | Bereich |
|---|---|---|
| Missing-Intervall | 900s | min. 900s (15 Min.) |
| Max. Searches/Lauf | 10 | 1–500 |
| Tageslimit | 20 | 0 = unbegrenzt |
| Cooldown | 7 Tage | 1–365 Tage |
| Jitter Max | 300s | 0 = aus, max 3600s |
| API-Timeout | 30s | 5–300s |
| Sonarr Suchmodus | Staffel | Episode / Staffel / Serie |
| Upgrades suchen | An | An / Aus |
| Zeitzone | UTC | IANA-Zeitzonen |
| Discord Rate-Limit | 5s | 1–300s |
| Discord Stats-Intervall | 60 Min. | 1–10080 Min. |

## 📡 API-Referenz

```bash
GET  /api/state                    # Status, Stats, Konfig, Log
POST /api/control                  # {"action":"start|stop|run_now"}
POST /api/config                   # Konfiguration ändern
GET  /api/instances                # Instanzen (ohne API-Keys)
POST /api/instances                # Hinzufügen
PATCH /api/instances/{id}          # Name / URL / Key / Typ / enabled
DELETE /api/instances/{id}         # Löschen
GET  /api/instances/{id}/ping      # Verbindungstest
GET  /api/history                  # Suchverlauf (filterbar)
POST /api/history/clear            # Alles löschen
POST /api/history/clear/{id}       # Instanz löschen
GET  /api/history/stats            # DB-Statistiken + Jahres-Breakdown
POST /api/discord/test             # Test-Nachricht senden
POST /api/discord/stats            # Statistik-Bericht sofort senden
GET  /api/timezones                # Verfügbare Zeitzonen
```

## 📜 Changelog

### v6.0
- Multi-Instanz: Sonarr + Radarr beliebig kombinierbar, frei benennbar
- Instanzen direkt in Einstellungen hinzufügen/umbenennen/löschen
- Zufällige Item-Auswahl pro Zyklus für gleichmäßige Abdeckung
- Discord: 6 Events, Statistik-Bericht, Rate-Limit-Schutz, DE/EN
- Episodentitel: `Serie – Episodenname – S01E01` (TBA/TBD unterdrückt)
- Vollständiges i18n inkl. Log-Meldungen
- Zeitzone konfigurierbar
- Jitter mit Minimum 15 Min.
- Sonarr Suchmodus: Episode / Staffel / Serie
- Upgrades global an/abschaltbar
- Statistik-Dashboard (Balkendiagramme, 24h-Timeline)
- Unraid Community Apps Template

### v5.0
- Multi-Instanz-Architektur

### v4.0
- SQLite statt JSON für Suchverlauf
- Cooldown in Tagen
- 3 Themes (Dark / Light / OLED)

---

<div align="center">
MIT License · <a href="https://github.com/kroeberd/mediastarr">github.com/kroeberd/mediastarr</a> · <a href="https://discord.gg/8Vb9cj4ksv">Discord</a>
</div>
