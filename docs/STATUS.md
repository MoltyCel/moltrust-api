# STATUS.md — MolTrust System Status

**Version:** V1 (first manual version — WORKFLOW §1.2 bootstrap item; auto-refresh via `scripts/generate_status.py` still OPEN)
**Last updated:** 2026-06-25 (manual, console)
**⚠️ STALE (Vermerk 2026-08-11):** Seit dem 2026-06-25 nicht nachgezogen. Die
Repo-HEADs und die „last landed"-Spalte unten sind **überholt** — in
`moltrust-api` allein sind seither **98 PRs** gelandet (#189 bis #295, davon 83
im Juli), die Tabelle nennt noch #186.
Absichtlich nicht von Hand aktualisiert: ein handgepflegter Stand driftet binnen
Wochen erneut, und `scripts/generate_status.py` (WORKFLOW §1.2) ist weiterhin
**OPEN**. Bis dahin gilt: **diese Datei ist kein Beleg.** Repo-Stände live über
`git log` / `gh pr list` prüfen, Server-Stände über eine Live-Probe — beides
ohnehin die Regel aus WORKFLOW §14 (Verify-before-Recommend).
**Scope:** all MolTrust repos (moltrust-api, moltrust-web, aae-conformance-vectors; moltguard, moltrust-protocol)
**Method:** verified against `git log` + `gh pr list` (repo evidence only). Server-state sections (systemd services / cron / DB schema drift) are **NOT** captured here — that needs `generate_status.py` or a live server probe; nothing is inferred.

## Repo HEADs (verified 2026-06-25)

| Repo | `main` HEAD | Last landed |
|---|---|---|
| moltrust-api | `134ed94` | #186 (2026-06-23) |
| moltrust-web | `25032562` | #84 (2026-06-25) |
| aae-conformance-vectors | `5bb946d3` | #4 v1.1.0 (2026-06-25) |

## Sprint landed (2026-06-18 → 06-25, PR-verified)

**moltrust-api**
- #183 `60facd5` — disarm autonomous X milestone-post → notify-only (§0.1 now code-hard)
- #184 `b5ffcae` — ThreadWatch pinned-roster + `/pin` (always-shown tracked threads) — *moltrust-api, not web*
- #185 `1bc81c3` — re-pin spec-fakten aae PIN in CLAUDE.md (Zitierquelle `b619d163` / Derivat `f99da5f2`; old `2847f4da` declared removed)
- #186 `134ed94` — BACKLOG proposal: scoped NOPASSWD sudoers for web-root deploy
- #181 security floors (5 pkgs, pip-audit 06-21) · #182 traffic ledgerless-IP freeze
- #179/#180 ambassador Stage-1 open-question hook + `funnel_diff.py` · #176/#177 ai-review hardening · #178 moltbook verify-solver
- spec-fakten repo seeded: `aae.md` `620b9f6`, `aps.md` stub `84f6b78` (UNVERIFIED)

**moltrust-web**
- #82 Estonia blog · #84 cross-org blog (agent identity across organizations) · #83 `deploy_page.sh --prebuilt` (reproducible web-root deploy)

**aae-conformance-vectors**
- #4 `5bb946d3` — v1.1.0 `verification_mode` (runtime|structural, required); issue #2 CLOSED

## Live surfaces (verified)
- Blogs live (HTTP 200) + in `sitemap.xml`: `/blog/estonia-ai-agent-authorization-layer`, `/blog/agent-identity-cross-org`
- GSC sitemap re-submit: reported done (clipperati2015@gmail.com) — **not console-verifiable**
- LinkedIn (Estonia + Cross-Org): **PLANNED**, not yet posted

## Open PRs (snapshot)
- **moltrust-api (10):** #98/#97 (backlog docs), #71 standards-anchors SPEC, #70 sprint-1.2.2, #62 CI pin actions to SHAs, #59 db authoritative schema.sql, #10 pydantic response models, #7 JCS + PQ dual-sig, #4 moltbook auth DIDs, #3 nonce tests — several long-lived → triage candidate
- **moltrust-web (1):** #9 webroot reconcile-plan Phase 2 (STOP-gated)
- **aae-conformance-vectors:** none

## Known drift / pending
- **spec-fakten/aae.md body still cites the removed `2847f4da`** + dead `.txt` URL, although #185 re-pinned the PIN (CLAUDE.md) to `b619d163` (Zitierquelle) / `f99da5f2` (Derivat) and declared `2847f4da` removed → aae.md body not yet reconciled (BACKLOG item).
- `MOLTSTACK_SUDO_PW` remains in `~/.moltrust_secrets` until the scoped NOPASSWD rule (#186) is set via `visudo`.
- STATUS.md auto-refresh (`generate_status.py`) + server-state capture (services/cron/DB): OPEN bootstrap — server-side state is not tracked in this file yet.

## Backlog
See `docs/BACKLOG.md` → **Sprint-Review 2026-06-25** for open items with status + next action.
