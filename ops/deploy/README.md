# ops/deploy

`deploy.sh` is the single deploy path for moltrust-api, moltrust-web and
moltguard; it runs on the server as `/home/moltstack/bin/deploy.sh`, the forced
command of the deploy key. `moltstack-webinstall` is the root-owned install
wrapper at `/usr/local/sbin/moltstack-webinstall` that `deploy.sh` calls for
each served file.

moltguard came last, on 2026-10-16 (ops D3): it is built on the server (`npm
ci` when the lockfile moved, then `npm run build`) and restarted as the unit
`moltguard`. A commit range that touches none of the paths the service runs
moves its checkout and does nothing else. The service writes two paths of its
own checkout — `data/trust-proofs/` and `jwks.json` — and those are left out of
the deploy gate, of the read-only protection and of `c-checkout-clean`;
everything else there belongs to the deploy, as in the other two.

Both were committed here on 2026-10-08 byte-for-byte as they ran on the server:

| file | sha256 |
|---|---|
| `deploy.sh` | `f3f90e29e72e8a5d05b144f6adf976e275e632c36a6a293d6330b7328c14bc0a` |
| `moltstack-webinstall` | `c30d1d34958aff70b7e2347eae808ffa0027b44e896665a5a69ee8078e0d8840` |

Until 2026-10-08 neither file was in any repository; `deploy.sh` was replaced
by hand on the server that morning (07:19:24 UTC); the console session
kerstenkroehl-0d later identified itself as the author (record_deploy and
--dienst, now part of this file). From here on the repository is the source.
`moltstack-webinstall` is source only: it is owned by root and installed by
Lars, never by a deploy.
