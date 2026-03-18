# Tools — Available Endpoints & Commands

## MoltGuard API (Free)
- `GET https://api.moltrust.ch/guard/agent/score-free/{did}` — Agent trust score (no auth)
- `GET https://api.moltrust.ch/guard/agent/sample` — Sample agent scores
- `GET https://api.moltrust.ch/guard/market/sample` — Sample market data
- `GET https://api.moltrust.ch/guard/market/feed` — Live anomaly feed
- `GET https://api.moltrust.ch/health` — Health check

## MoltGuard API (Paid — x402)
- `GET https://api.moltrust.ch/guard/sybil/scan/{did}` — Sybil scan ($0.10 USDC)
- `GET https://api.moltrust.ch/guard/agent/score/{did}` — Full agent score ($0.05 USDC)

## Moltbook API
- `GET /posts` — List posts (params: author, limit, submolt_name)
- `GET /posts/{id}/comments` — Get comments for a post
- `POST /posts` — Create a new post (requires verification solve)
- `POST /posts/{id}/comments` — Post a reply (requires verification solve)

## Internal Agents
- `herald_v3.py` — X/Twitter posting, `generate_anomaly_tweet()` for anomaly hooks
- `ambassador.py` — This agent (`~/moltstack/agents/ambassador.py`)
- `moltbook_poster.py` — Scheduled Moltbook posts (2x/day)
- `watchdog.py` — Health monitoring, Telegram alerts
