# Infra notes (server-side, not repo-managed)

Server infrastructure (nginx / systemd / cron) is **not** managed in any repo.
This file records applied server changes so they are not silent
`live ≠ repo` drift. Each entry: what, why, where, when.

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
