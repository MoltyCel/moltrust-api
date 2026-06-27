# Infra notes (server-side, not repo-managed)

Server infrastructure (nginx / systemd / cron) is **not** managed in any repo.
This file records applied server changes so they are not silent
`live ≠ repo` drift. Each entry: what, why, where, when.

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
