# Audit 2026-09-01 — W3C ListWatch, mailing-list monitor

**Console action.** New standalone script (`scripts/w3c_listwatch.py`) plus one
crontab entry. ThreadWatch is untouched.

## Problem

ThreadWatch reaches only `api.github.com` — issues, comments, discussions,
notifications. W3C mailing lists are outside it entirely, and not by
configuration: there is no source class for them in the script.

That gap has already cost something twice. ADR-0002 records a W3C
`public-agent-identity` thread as its trigger, so a list has driven an
architectural decision here before. And the Agent Conformance and Benchmarking
CG launched on 2026-08-25 with a substantive thread running from 2026-08-26;
it was noticed on 2026-09-01, a week late, and only because someone went
looking by hand.

## Change

Separate script rather than a second source class inside `threadwatch.py`. The
archive is HTML with different failure modes, a different identity model (no
logins, display names only) and no issue numbers to hang an ack/roster model
on. Mixing the two would put two parsers and two failure vocabularies in one
781-line file.

- Watches `public-agent-conformance` and `public-agentprotocol`.
- Fetches the month index at `Archives/Public/<list>/<YYYYMon>/`, parses
  `(number, subject, author)`, diffs against `state/w3c_listwatch.json`.
- **Current month and the previous one every run**, so a rollover cannot hide
  the tail of the old month.
- **HTTP 404 on a month index is normal**: the directory is created by the
  first message of that month. Treating it as an error would fire a false
  alarm at the start of every month.
- Reports every new message; four trigger rules decorate the entries that are
  worth reading first — unknown sender, structure vocabulary (repo, work item,
  charter), MolTrust/AAE/EMILIA/action_ref mentions, and the convergence
  markers (8 Sept, evidence-record group). A trigger never suppresses.
- Sends through `app.notify.send_telegram`, so the shared `MOLTRUST_NOTIFY`
  gate governs it exactly as it governs every other sender. No Telegram code
  is duplicated.
- Quoted lines are stripped before triggers run. Otherwise every reply that
  quotes a triggering message re-fires the same trigger.

**On send failure the state is deliberately not advanced.** A message counted
as seen but never delivered is a silently lost alert; the cost of the other
choice is one repeated entry.

## Verification

Trigger rules unit-tested against synthetic inputs — each fires when it should
and stays silent when it should not. Three defects found in the first dry run
and fixed before install:

- the archive serves UTF-8 without always declaring it, so `requests` guessed
  ISO-8859-1 and mangled every em-dash; encoding is now forced
- `repo` matched inside `report` and `reporting`, firing on nearly every
  message; terms are now matched on word boundaries
- `NEW SENDER` fired on every `public-agentprotocol` message because no roster
  is defined for that list; the rule now applies only where one is

State seeded from the 26 existing messages at install, so the first live run
reports genuinely new mail rather than replaying the archive.

## Rate-limit / secrets

Two index fetches plus one fetch per new message, twice a day, with a 1s pause
between message fetches. No API key, no authentication, no rate limit to
respect beyond politeness. Secrets are read from `~/.moltrust_secrets` into the
process environment; none are logged.

## Deploy

Cron `0 11,23`, offset from ThreadWatch (`0 8,18`). Those two hours carry no
other Telegram-sending job — checked against every cron entry, not the first
screenful. Crontab backed up to `~/crontab.bak.20260901-064656` before the
edit.

Per §11 the crontab itself is server infra and not repo-managed; this entry is
the record of that change.
