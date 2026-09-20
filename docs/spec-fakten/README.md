# Spec Facts — Verified Reference Repository

Each spec relevant to MolTrust gets its own `.md` file here. Memory holds only a reference pointer (see Memory #30).

Files:
- `aae.md` — AAE (draft-kroehl-agentic-trust-aae-00) ✅ VERIFIED
- `aps.md` — APS (source: `draft-pidlisnyi-aps-01` + Zenodo papers) ⚠️ STUB UNVERIFIED
- `mcp-transport-security.md` — MCP Python SDK transport advisories 2026 (CVE-2026-52869 /
  -52870 / -59950), version floor `mcp>=1.28.1` ✅ VERIFIED 2026-07-28
- `erc-8004.md` — ERC-8004 Trustless Agents ✅ VERIFIED
- `erc-8194.md` — ERC-8194 PGTR (Payment-Gated Transaction Relay) ✅ VERIFIED 2026-09-20.
  **Open PR, not a merged ERC** — see the file's Standing section before citing.

Planned (as needed):
- `x402.md`, `action-ref.md`, `w3c-vc.md`, `w3c-did.md`

## Workflow

1. Before any public comment that cites a spec (section refs, hash algorithms, normative requirements): fetch the relevant file here and verify against the current source.
2. If facts have changed: update the file first, then write the comment.
3. Each file carries: source URL, verification date, sha256 of source text, section map, key mechanics, and common attribution errors to avoid.
