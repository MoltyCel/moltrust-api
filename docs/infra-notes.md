# Infra notes (server-side, not repo-managed)

Server infrastructure (nginx / systemd / cron) is **not** managed in any repo.
This file records applied server changes so they are not silent
`live ≠ repo` drift. Each entry: what, why, where, when.

## 2026-08-08 — status.moltrust.ch: `GH_PAT` erneuert, Auto-Update-Workflows deaktiviert

**Why.** Die Upptime-Instanz meldete ab 2026-08-07 11:09 durchgehend `Uptime CI`-
Fehlschläge, vier Annotations pro Lauf. Keine davon war ein Endpoint: dreimal
`fatal: could not read Username for 'https://github.com': terminal prompts disabled`
plus `The process '/usr/bin/git' failed with exit code 128`, alle aus dem Schritt
**Checkout**. Der Job starb zwei Schritte vor `Check endpoint status`, die drei
Retries erklären die 35–40 s Laufzeit. Alle sechs überwachten Endpoints waren zu
dem Zeitpunkt live 200; der zuletzt aufgezeichnete Stand lag zwischen 99,32 % und
99,89 % Uptime. Also **kein Ausfall, sondern ein blinder Melder**.

Ursache: `uptime.yml` reicht `${{ secrets.GH_PAT || github.token }}` an Checkout
*und* Monitor. Ist `GH_PAT` gesetzt, aber ungültig, greift der `||`-Fallback nicht —
ein nicht-leerer String gewinnt. Das Secret war zuletzt am 2026-05-09 gesetzt; plus
90 Tage ergibt den 2026-08-07, den Tag des Kipppunkts. Alle sechs Upptime-Workflows
im Repo teilen dieses eine Secret und fielen im selben Fenster aus.

**What (applied).**

- Neuer fine-grained PAT, nur auf `status.moltrust.ch`, Permissions **Contents:
  Read/write** und **Issues: Read/write**. Issues ist nicht optional — Upptimes
  Störungsmeldungen *sind* GitHub-Issues (40 im Repo, z. B. „🛑 Agent Score (Free)
  is down"). Mit Contents allein committet Upptime weiter Messwerte und meldet
  keine Ausfälle mehr.
- Kein Workflow-Schreibrecht vergeben. Folge: `update-template.yml` (täglich 00:00)
  und `updates.yml` (täglich 03:00) regenerieren den Repo-Inhalt inklusive
  `.github/workflows/*.yml` und liefen damit in ein 403. Beide daher über die
  Actions-API auf `disabled_manually` gesetzt (`gh workflow disable`) — Dateien
  bleiben liegen, `gh workflow enable` macht es rückgängig. Upptime steht damit
  fest auf **v1.43.13**.

**Verify.** Ein grüner Lauf allein beweist nichts: `Uptime CI` protokollierte
sechsmal `Skipping commit, status is up` — das `update`-Kommando schreibt nur bei
einem Statuswechsel. Der Schreibpfad wurde deshalb separat über `Response Time CI`
geprüft, das bei jedem Lauf committet: `master` wanderte `731f6ab7 → 18874c8d`,
sechs neue Commits in `history/`, Dateiinhalt auf `master` gegengelesen
(`lastUpdated: 2026-08-08T11:57:06.070Z`). Ungeprüft blieb das Issues-Recht — das
löst nur ein echter Ausfall aus.

## 2026-07-28 — nginx: Discovery-Aliases im `moltrust.ch`-Block

**Why.** Wiederkehrende 404 von Discovery-Crawlern (AgenstryBot, Terminus-
Observatory, GuzzleHttp, AgentRadar) auf Pfaden, deren Daten längst vorlagen.
`api.moltrust.ch` beantwortete vier der fünf bereits per App-Route (PR #207/#212),
der statische Web-Host `moltrust.ch` nicht — die nginx-Blöcke sind getrennt, unter
`moltrust.ch` greift nur `try_files` gegen `/var/www/html`. Volumen über ~14 Tage:
70 vergebliche Abrufe auf `agent.json`, 5 auf `x402`.

**What (applied).** Zwei `location`-Blöcke im `moltrust.ch`-Server-Block,
eingefügt nach dem bestehenden `a2a`-Redirect:

```nginx
location = /.well-known/agent.json {
    alias /var/www/html/.well-known/agent-card.json;
    default_type application/json;
    add_header Access-Control-Allow-Origin "*" always;
    add_header Cache-Control "public, max-age=3600";
}
location = /.well-known/x402 {
    return 301 /.well-known/x402.json;
}
```

`agent.json` ist der Vor-Rename-Name aus A2A und inhaltlich identisch zur
`agent-card.json` — per `alias` dieselbe Datei, kein zweites File, keine Drift.
Exakt-Match (`location =`) statt Prefix wie bei den Nachbarblöcken, damit nicht
versehentlich `agent.jsonX` mitgefangen wird.

**Verify.** `agent.json` → 200, Body per `cmp` byte-identisch zu `agent-card.json`
(md5 beidseitig `6f36c6b4…3bdfdc`); `x402` → 301 mit Ziel `/.well-known/x402.json`,
gefolgt → 200 `application/json`. api-Block unberührt, Nachbarpfade (`jwks.json`,
`a2a`, `did.json`, `llms.txt`, `sitemap.xml`, `blog/`) unverändert. Backup unter
`~/nginx-backups/default.bak-2026-07-28-0817`, Audit-Zeile in
`~/moltguard-infra-audit.log`.

**Offen.** `/.well-known/mcp` und `mcp.json` fehlen auf dem Web-Host weiterhin
(braucht die Datei, nicht nur eine nginx-Zeile). `mcp/server-card.json` und
`agent-directory.json` bleiben ohne belegten Nutzen — SEP-2127 ist Draft und nennt
einen anderen Pfad (`.well-known/ai-catalog.json`), und MolTrust ist bei Agenstry
bereits über `agent-card.json` gelistet. Nebenbefund: `x402.json` liegt im Web-Root,
ohne von einem Repo gedeckt zu sein.

## 2026-06-28 — trouvart DB-backup cron: daily → weekly (+ one-time prune)

**Why.** `~/trouvart/scripts/backup.sh` (cron `0 4 * * *`) copied the full ~2.7G
trouvart SQLite DB **daily**; with its own `-mtime +14` retention that plateaued at
~37G — ~60% of the 75G root disk (which had reached **86% used**). trouvart's live
data is only ~3.4G, so 14 daily 2.7G copies was disproportionate.

**What (applied).**

- **Cron** (user `moltstack` crontab): backup frequency `0 4 * * *` → `0 4 * * 0`
  (weekly, Sunday 04:00). The daily trouvart `run_scan.sh` (`0 2 * * *`) is unchanged.
- **One-time prune**: deleted `trouvart_*.db` backups older than 7 days (7 files,
  ~16 GiB); kept the 8 most recent + the live DB (`~/trouvart/data/trouvart.db`).
- Also cleared verified-stale items (May-12 pre-deploy snapshot, old KB raw exports
  `export-2026-06-01/-12.json`, April html backups in `~/backups/`). **Kept** the
  active `~/backups/moltstack_*.sql` daily DB dumps (separate `backup_db.sh`, which
  already self-prunes at `-mtime +7`).

**Where/when.** `crontab` (user `moltstack`), applied 2026-06-28; crontab backed up
to `~/crontab.bak-2026-06-28-*`. Disk **86% → 61%** (29G free).

**Note.** `backup.sh` itself unchanged — weekly run + `-mtime +14` retention now
yields ~2 retained restore points; bump retention if more weekly history is wanted.

## 2026-06-27 — nginx: mask `api_key=` in access-log query strings

**Why.** Default `combined` log_format logs the full request line incl. the query
string. External MCP-discovery crawlers (`agent-tools.cloud-crawler`, some
`python-httpx` clients) append `?api_key=…` to `POST /mcp`, landing those tokens in
plaintext in `/var/log/nginx/access.log*` (~14-day retention). Analysis: the leaked
values are **not** MolTrust keys (no `mt_` prefix, 0 matches in the `api_keys` table) —
they are the crawlers' own credentials — but logging third-party secrets is a
liability, and a real `mt_` key could land the same way. The API itself authenticates
only via the `X-API-Key` **header**, so a query-string `api_key` is functionally
ignored (the `200/202` status ≠ "key accepted" — verified against the DB, not assumed).

**What (applied rule).** In `http {}` (Logging Settings), mask only the `api_key`
value while preserving every other param, so `daily_stats.sh` `profile=` extraction
and awk field positions keep working:

```nginx
map $request $request_masked {
    "~^(?<pre>.*[?&]api_key=)[^& ]*(?<post>.*)$"  "${pre}REDACTED${post}";
    default                                        $request;
}
log_format masked '$remote_addr - $remote_user [$time_local] '
                  '"$request_masked" $status $body_bytes_sent '
                  '"$http_referer" "$http_user_agent"';
access_log /var/log/nginx/access.log masked;
```

Replaces the prior `access_log /var/log/nginx/access.log;` (implicit `combined`).
No per-server `access_log` override exists, so this covers all vhosts. Named captures
(`${pre}`/`${post}`) chosen over positional `${1}` for unambiguous runtime resolution.

**Where/when.** `/etc/nginx/nginx.conf`, applied 2026-06-27 via `systemctl reload
nginx` (graceful — master PID unchanged, workers reloaded; backup
`/etc/nginx/nginx.conf.bak-2026-06-27-*`).

**Verify (live, 2026-06-27 11:48 UTC).**
`curl -s "https://api.moltrust.ch/health?profile=keepme&api_key=MASKPROBE<epoch>"` →
log line shows `…?profile=keepme&api_key=REDACTED…`; the raw marker is absent from the
log (grep count 0). `profile=` preserved.

**Backfill.** Pre-existing plaintext entries age out via the 14-day logrotate; no
MolTrust-secret rotation needed (leaked values were external, per the analysis above).

## 2026-06-18 — nginx Cache-Control for the static web root (moltrust.ch)

**Why.** Served `*.html` had **no `Cache-Control` header**, so browsers applied
heuristic caching and returning visitors reused stale HTML (e.g. EU visitors
saw an old `/pricing.html` that pre-dated the EUR auto-detect, appearing as
"EU stuck on USD" even though the deployed JS was correct). Root cause was the
cache layer, not the page code (confirmed by headless render of a fresh
context: `de-DE → EUR`, `en-US → USD`).

**What (applied rule).** In the `server { listen 443 ssl; server_name
moltrust.ch; root /var/www/html; }` block:

- Server-level, alongside the existing security headers:
  ```nginx
  add_header Cache-Control "no-cache, must-revalidate" always;
  ```
  This is inherited by `location /` (no own `add_header`), so **all HTML and
  the homepage `/`** revalidate on every request (304 when unchanged) instead
  of being served stale from browser cache.

- Versioned static assets are exempted with a long-lived immutable policy via a
  regex location (which re-adds the security headers, since `add_header` in a
  location does not inherit server-level headers):
  ```nginx
  location ~* \.(?:js|mjs|css|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|eot|map)$ {
      add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
      add_header X-Content-Type-Options "nosniff" always;
      add_header X-Frame-Options "DENY" always;
      add_header Cache-Control "public, max-age=31536000, immutable" always;
  }
  ```
  Assets are cache-busted by query string (`nav.js?v=5`, `pricing.js?v=1`, …);
  bump `?v=` when an asset changes.

  `txt` / `json` / `xml` (e.g. `llms.txt`, `sitemap.xml`, `x402.json`) are
  intentionally **not** in the immutable set — they revalidate like HTML
  (some already set their own `max-age=3600` in dedicated locations).

**Where.** `/etc/nginx/sites-enabled/default` — the moltrust.ch `:443`
server block. Note: `sites-enabled/default` is a **regular file** and **differs
from** `sites-available/default`; nginx loads the `sites-enabled` copy, so edit
that one. Backup taken at `/etc/nginx/default.cachefix-bak-20260618`.
Backups must **never** live under `sites-enabled/` (the `*` glob would load a
`.bak` as a second server config → duplicate-directive `nginx -t` failure).

**Verify.**
```
curl -sI https://moltrust.ch/pricing.html   | grep -i cache-control
#   cache-control: no-cache, must-revalidate
curl -sI https://moltrust.ch/assets/js/nav.js | grep -i cache-control
#   cache-control: public, max-age=31536000, immutable
```
Applied with `nginx -t` (pass) + `systemctl reload nginx`; security headers
(HSTS / nosniff / X-Frame-Options) confirmed still present on HTML responses.
