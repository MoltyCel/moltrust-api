# @moltrust/openclaw-plugin v2

> W3C DID trust verification + lifecycle gating for [OpenClaw](https://openclaw.ai)

v2 adds the four lifecycle hooks the OpenClaw core already exposes (per
`openclaw/openclaw#49971` close-comment) — `before_install`,
`before_tool_call`, `inbound_claim`, `gateway_start` — on top of the v1
agent tools / slash commands / gateway RPC / CLI surface.

## Install

```bash
openclaw plugins install @moltrust/openclaw-plugin
```

Restart your gateway.

## What's new in v2

| Hook | What it gates |
|---|---|
| `before_install` | Plugin / skill installs against `installAllowlist` / `installBlocklist` |
| `before_tool_call` | Sensitive tool calls (default: `pay_*`, `transfer_*`, `x402_*`, `agent_call_*`) — blocks if own agent or any DID in params is below `minTrustScore` |
| `inbound_claim` | Inbound messages — replies with a warning when sender DID is below `minTrustScore` |
| `gateway_start` | Connectivity probe + optional self-verify |

All hooks are **opt-in by default** (`minTrustScore: 0` = no-op). Set a
non-zero threshold (e.g. `50`) to activate gating.

New tool: `moltrust_endorse` — issue a SkillEndorsementCredential (W3C VC,
90-day) for another agent, posting to `POST /skill/endorse`.

## Configuration

```json
{
  "plugins": {
    "entries": {
      "moltrust": {
        "enabled": true,
        "config": {
          "apiKey": "mt_live_...",
          "minTrustScore": 50,
          "agentDid": "did:moltrust:your-agent",
          "verifyOnStart": true,
          "sensitivePrefixes": ["pay_", "transfer_", "x402_", "agent_call_"],
          "gateAllTools": false,
          "installAllowlist": [],
          "installBlocklist": [],
          "cacheTtlMs": 300000
        }
      }
    }
  }
}
```

Get an API key at [api.moltrust.ch/auth/signup](https://api.moltrust.ch/auth/signup).

## Architecture

```
src/
├── openclaw-types.ts     vendored OpenClaw plugin SDK types (subset)
├── client.ts             MolTrustClient + LRU cache (5 min TTL)
├── utils.ts              extractDids / isLikelyDid
├── hooks/
│   ├── before-install.ts     makeBeforeInstallHandler({cfg, logger})
│   ├── before-tool-call.ts   makeBeforeToolCallHandler({cfg, client, logger})
│   ├── inbound-claim.ts      makeInboundClaimHandler({cfg, client, logger})
│   └── gateway-start.ts      makeGatewayStartHandler({cfg, client, logger})
└── index.ts              wires hooks + v1 tools/commands/RPC/CLI
```

Each hook handler uses a factory pattern (`makeXxxHandler(deps)`) so it's
unit-testable without an OpenClaw host.

## Tests

```bash
npm install
npm test       # ≥15 vitest tests across hooks + client
npm run build  # produces dist/*.js + *.d.ts
```

## Fail-open on lookup errors

`before_tool_call` and `inbound_claim` log a warning and **do not block** when
a MolTrust API lookup fails (network down, rate limit, etc.). This is a
deliberate design choice: a transient trust-API outage shouldn't take an
agent fleet offline. Operators should monitor the warn-log for sustained
failures.

## License

MIT © CryptoKRI GmbH (MolTrust), Zurich
