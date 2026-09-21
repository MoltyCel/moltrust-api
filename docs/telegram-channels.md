# Telegram channels

One chat carried everything until 2026-09-21. An alert about a dead agent sat
between the hourly traffic numbers and a draft tweet, and a payout notice looked
like any other line. Four chats now, and every message goes to exactly one.

| Channel | What belongs in it | Chat id variable |
|---|---|---|
| `stats` | periodic numbers nobody has to act on | `TELEGRAM_CHAT_ID_STATS` |
| `alerts` | something is broken or needs a decision now | `TELEGRAM_CHAT_ID_ALERTS` |
| `money` | payments, balances, payouts, budget | `TELEGRAM_CHAT_ID_MONEY` |
| `worklog` | what the agents did: posts, PRs, drafts, reviews, cleanups | `TELEGRAM_CHAT_ID_WORKLOG` |

Each variable falls back to the single `TELEGRAM_CHAT_ID`. An unconfigured split
behaves exactly as before rather than dropping messages, so the code ships
before the chats exist.

## Who posts where

The default is per module. A call site that carries a different kind of message
overrides it — a failing Herald run is an alert even though Herald's own output
is worklog.

| Sender | Trigger | Channel |
|---|---|---|
| `scripts/daily_stats.sh` | cron, daily | stats |
| `scripts/weekly_traffic.sh` | cron, Mon 08:00 | stats |
| `agents/traffic_monitor.py` | cron, hourly `:30` | stats |
| `scripts/funnel_diff.py` | manual / cron | stats |
| `scripts/discovery_snapshot.py` | manual / cron | stats |
| `agent/ambassador.py` milestone | service, on registration | stats |
| `agents/watchdog.py` | cron, hourly `:00` | alerts |
| `scripts/endpoint_probe.py` | cron | alerts |
| `scripts/auto_repair.py` | cron, 03:10 | alerts |
| `scripts/security_check.sh` | cron, Sun 03:00 | alerts |
| `scripts/taskmarket_legal_check.sh` | cron, Sun 06:15 | alerts |
| `scripts/anchor_audit.py` | manual / cron | alerts |
| `scripts/bazaar_index_check.sh` | cron | stats |
| `monitor/poll_payments.py` | cron, hourly `:00` | money |
| `app/budget.py` | `moltstack.service`, on threshold | money |
| `scripts/check_credits.sh` | cron | money |
| `agents/herald_v3.py`, `agents/herald.py` | cron, 12:00 | worklog (errors → alerts) |
| `agents/syndicate.py` | cron, every 30 min | worklog (failures → alerts) |
| `agents/news_scout.py` | cron, 17:00 | worklog |
| `agents/pr_monitor.py` | cron, 09:00 and 18:00 | worklog |
| `agents/proof_post.py` | cron, Sun 08:00 | worklog (failures → alerts) |
| `agents/retention_cleanup.py` | cron, 03:30 | worklog |
| `agents/ai_review.py`, `_v2` | manual | worklog |
| `scripts/concept_review.py` | manual | worklog |
| `scripts/threadwatch.py` | cron, 08:00 and 18:00 | worklog |
| `scripts/w3c_listwatch.py` | cron | worklog |
| `scripts/revoke_inactive.py` | cron | worklog |
| `scripts/telegram_hn_remind.py` | cron, 22 March | worklog |
| `workers/content_scout/telegram.py` | cron, 06:30 and 17:30 | worklog |
| `moltguard/scripts/bazaar_check_24h.sh` | `at`, one-off | stats |

`tests/test_notify_channels.py` keeps this honest: `notify.send_telegram` takes
`channel` as a required keyword, every module-local sender must declare a
default in its signature, and no sender may resolve `TELEGRAM_CHAT_ID` directly.
A new sender that skips the decision fails the build.

## Setting it up

The four chats do not exist yet; creating them and adding the bot is a manual
step. For each of stats, alerts, money, worklog:

1. Telegram → new group (or channel), name it `MolTrust <channel>`.
2. Add the existing bot as a member, and as an admin if it is a channel.
3. Post one message in it, then read the id:
   `curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" | grep -o '"chat":{"id":[-0-9]*'`
4. Append the four lines to `~/.moltrust_secrets`:

```
TELEGRAM_CHAT_ID_STATS=-100…
TELEGRAM_CHAT_ID_ALERTS=-100…
TELEGRAM_CHAT_ID_MONEY=-100…
TELEGRAM_CHAT_ID_WORKLOG=-100…
```

5. Restart the services that read `EnvironmentFile` at start, otherwise the API
   keeps posting to the old chat: `sudo systemctl restart moltstack.service`.

Cron senders need nothing — they read the file on every run. `TELEGRAM_CHAT_ID`
stays in place as the fallback; removing it would drop any message whose channel
variable is missing.

`workers/content_scout/.webdocs/` is a checkout of MoltyCel/moltrust-web that
the content-scout worker keeps to diff published pages against. It has a sender
of its own, driven by that repository's CI rather than by our cron, and it is
governed there — the scan skips nested checkouts for that reason, and the
directory is now in `.gitignore` so it stops looking like our source.
