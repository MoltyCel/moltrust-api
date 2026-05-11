# Nginx Real-IP Policy

**Last updated:** 2026-05-11
**Status:** documented; not yet applied to /etc/nginx because no upstream proxy is currently in front of nginx

## Why this exists

The Python layer relies on `X-Real-IP` (set by nginx) for two security-critical decisions:

- `app.identity._client_ip` reads `X-Real-IP` first, then falls back to the last hop of `X-Forwarded-For` (commit `88956b7`). This IP feeds the probe-spawn rate guard and spawn-attribution.
- `app.main._ratelimit_key` uses the same precedence for `slowapi`-backed `@limiter.limit` decorators (commit `88956b7`'s sibling refactor in `main.py`).

Both treat the resolved IP as trustworthy. That trust holds only if `X-Real-IP` is set by an authenticated upstream (nginx itself) and the client can't forge it.

In the current production layout there is no proxy between the client and nginx — Hetzner serves `api.moltrust.ch` direct, no Cloudflare or AWS CloudFront in path. nginx's `proxy_set_header X-Real-IP $remote_addr` sets the header to the TCP connection's source, which IS the real client IP. The Python policy is safe.

## When the policy must change

The moment a proxy is added in front of nginx — Cloudflare, Fastly, AWS CloudFront, or even a different edge nginx — `$remote_addr` becomes the proxy's IP, not the client's. `X-Forwarded-For` from the proxy contains the chain, but unless nginx is told which upstreams are trustworthy, an attacker can spoof XFF from below.

The fix in that scenario is `ngx_http_realip_module`:

```nginx
# /etc/nginx/conf.d/cloudflare-realip.conf (or similar)

# Cloudflare IP ranges, as published at https://www.cloudflare.com/ips/
# Refresh quarterly or wire to a cron that pulls https://www.cloudflare.com/ips-v4
set_real_ip_from 103.21.244.0/22;
set_real_ip_from 103.22.200.0/22;
set_real_ip_from 103.31.4.0/22;
set_real_ip_from 104.16.0.0/13;
set_real_ip_from 104.24.0.0/14;
set_real_ip_from 108.162.192.0/18;
set_real_ip_from 131.0.72.0/22;
set_real_ip_from 141.101.64.0/18;
set_real_ip_from 162.158.0.0/15;
set_real_ip_from 172.64.0.0/13;
set_real_ip_from 173.245.48.0/20;
set_real_ip_from 188.114.96.0/20;
set_real_ip_from 190.93.240.0/20;
set_real_ip_from 197.234.240.0/22;
set_real_ip_from 198.41.128.0/17;
# IPv6
set_real_ip_from 2400:cb00::/32;
set_real_ip_from 2606:4700::/32;
set_real_ip_from 2803:f800::/32;
set_real_ip_from 2405:b500::/32;
set_real_ip_from 2405:8100::/32;
set_real_ip_from 2a06:98c0::/29;
set_real_ip_from 2c0f:f248::/32;

# CF-Connecting-IP carries the real client IP from Cloudflare; if you prefer
# X-Forwarded-For for compatibility with non-CF tooling, pick that. Either
# way, real_ip_recursive walks the chain back to the first untrusted hop.
real_ip_header CF-Connecting-IP;
real_ip_recursive on;
```

After applying:
- nginx rewrites `$remote_addr` and `$proxy_add_x_forwarded_for` to the real client IP
- existing `proxy_set_header X-Real-IP $remote_addr` lines in the site config keep working unchanged
- the Python layer continues reading `X-Real-IP` and gets the right value automatically

## Rollout checklist (do not skip)

When the day comes:

- [ ] Confirm the upstream proxy's published IP ranges, including IPv6
- [ ] Add the snippet to `/etc/nginx/conf.d/`, NOT inside an existing site config (so it's reusable)
- [ ] `sudo nginx -t` before reloading
- [ ] `sudo systemctl reload nginx` (not restart — reload keeps existing connections)
- [ ] Run a curl through the proxy with a forged `X-Forwarded-For` header and verify the resolved IP is the actual source, not the forged value. Example:
  ```bash
  curl -sv -H "X-Forwarded-For: 1.1.1.1" https://api.moltrust.ch/health 2>&1 | grep -i 'remote\|forwarded'
  ```
- [ ] Tail `/var/log/nginx/access.log` for a minute — the source-IP column should show real client IPs, not the proxy's range
- [ ] Verify `app.identity._client_ip` resolves correctly by checking `probe_agents.first_seen_ip` for new probes minted post-reload

## Related code

- `app/identity.py::_client_ip` — defensive [-1] XFF parsing, prefers X-Real-IP
- `app/main.py::_ratelimit_key` — same policy applied to slowapi key extraction, plus IPv6 /64 bucketing per H8
- `app/identity.py::bucket_subnet` — IPv4 /24 and IPv6 /64 bucketing helper used by the probe-spawn rate guard

## Related findings

- H6 (XFF first-element trust) — closed at the Python layer by `88956b7`
- H8 (IPv6 rate-limit bypass) — closed by `e89dcbe`
- The third item from the Perplexity backlog ("nginx real_ip_header for XFF handling") is what this document captures. Deferring the nginx-side change until an upstream proxy is actually deployed avoids a no-op edit to production config and keeps the trust chain explicit when it does change.
