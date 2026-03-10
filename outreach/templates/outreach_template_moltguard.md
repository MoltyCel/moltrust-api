# Template: MoltGuard for Prediction Markets

**Use for:** Prediction market projects, betting integrity tools, market manipulation detection

---

## Issue Title
Market Integrity Monitoring with MoltGuard

## Body

Hi,

I'm building [MoltGuard](https://github.com/moltrust/moltguard), an open-source market integrity monitor for agent prediction markets — essentially the Sportradar equivalent for the agent economy.

**What MoltGuard does:**
- **Sybil Shield** — Detects when multiple "independent" agents are controlled by one operator using statistical clustering and behavioral analysis
- **Integrity Monitor** — Z-score anomaly detection on market volume and price data (volume spikes, suspicious price moves, low-liquidity manipulation)
- **Compliance Layer** — Integrity reports issued as W3C Verifiable Credentials, anchored on Base blockchain for tamper-proof audit trails

**Current capabilities:**
- Monitors 500+ active Polymarket markets
- Detects anomalies with Z > 3 volume spikes, >15% price moves, and low-liquidity flags
- Reports issued via MolTrust trust infrastructure (DIDs + VCs)

**Why this matters for your project:**
As prediction markets grow and more AI agents participate, market integrity becomes critical. Unverified agents can manipulate outcomes through coordinated betting, front-running, or Sybil attacks. MoltGuard provides independent monitoring that market operators can integrate.

**Integration options:**
- API-based integrity checks before accepting agent bets
- Webhook alerts for anomaly detection on specific markets
- Verifiable integrity reports for regulatory compliance

I'd like to discuss how MoltGuard could complement your market infrastructure. Happy to share our anomaly detection methodology or run a pilot on your market data.

Links: [MolTrust](https://moltrust.ch) | [MolTrust API](https://api.moltrust.ch/docs)
