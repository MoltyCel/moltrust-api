"""MoltGuard MCP Tools — extends the MolTrust MCP server with integrity tools."""

import httpx
from typing import Any

MOLTGUARD_URL = "http://127.0.0.1:3003"


async def _guard_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{MOLTGUARD_URL}{path}")
        r.raise_for_status()
        return r.json()


async def _guard_post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{MOLTGUARD_URL}{path}", json=body)
        r.raise_for_status()
        return r.json()


def register_moltguard_tools(mcp):
    """Register all MoltGuard tools on the given MCP server instance."""

    @mcp.tool()
    async def moltguard_score(address: str) -> str:
        """Get an agent trust score for a Base wallet address.

        Analyzes on-chain activity, ERC-8004 registration, USDC balance,
        counterparty diversity, and MolTrust credentials to produce a 0-100 score.

        Args:
            address: Base (EVM) wallet address (0x...)
        """
        data = await _guard_get(f"/api/agent/score/{address}")
        lines = [
            f"Agent Trust Score: {data['score']}/100",
            f"Wallet: {data['wallet']}",
            "",
            "Breakdown:",
        ]
        for k, v in data.get("breakdown", {}).items():
            lines.append(f"  {k}: {v}")
        lines.append(f"\nData source: {data.get('_meta', {}).get('dataSource', 'unknown')}")
        return "\n".join(lines)

    @mcp.tool()
    async def moltguard_detail(address: str) -> str:
        """Get a detailed agent trust report for a Base wallet address.

        Returns full scoring breakdown, wallet history from Blockscout,
        ERC-8004 registration, MolTrust DID cross-reference, and Sybil indicators.

        Args:
            address: Base (EVM) wallet address (0x...)
        """
        import json
        data = await _guard_get(f"/api/agent/detail/{address}")
        return json.dumps(data, indent=2)

    @mcp.tool()
    async def moltguard_sybil(address: str) -> str:
        """Scan a Base wallet for Sybil indicators.

        Analyzes wallet age, transaction patterns, counterparty diversity,
        and funding source to detect potential Sybil wallets.
        Also traces funding clusters — if the funder sent ETH to many wallets,
        it indicates a Sybil ring.

        Args:
            address: Base (EVM) wallet address (0x...)
        """
        data = await _guard_get(f"/api/sybil/scan/{address}")
        lines = [
            f"Sybil Score: {data['sybilScore']} ({data['confidence']} confidence)",
            f"Wallet: {data['wallet']}",
            f"Recommendation: {data['recommendation']}",
            "",
            "Indicators:",
            f"  Wallet age: {data['indicators']['walletAgeDays']} days",
            f"  TX count: {data['indicators']['txCount']}",
            f"  Unique counterparties: {data['indicators']['uniqueCounterparties']}",
            f"  Has USDC: {data['indicators']['hasUsdcBalance']}",
            f"  Patterns: {', '.join(data['indicators']['patternMatch']) or 'none'}",
        ]
        cluster = data.get("cluster", {})
        if cluster.get("detected"):
            lines.append(f"\nCluster DETECTED: ~{cluster['estimatedSize']} sibling wallets")
        if cluster.get("fundingSource"):
            lines.append(f"  Funding source: {cluster['fundingSource']}")
            lines.append(f"  Funding amount: {cluster.get('fundingAmountEth', '?')} ETH")
            lines.append(f"  Sibling wallets: {cluster.get('siblingWallets', '?')}")
        return "\n".join(lines)

    @mcp.tool()
    async def moltguard_market(market_id: str) -> str:
        """Check a Polymarket prediction market for integrity anomalies.

        Analyzes volume spikes, price-volume divergence, liquidity ratios,
        and outcome price spreads to detect potential manipulation.

        Args:
            market_id: Polymarket market/condition ID
        """
        data = await _guard_get(f"/api/market/check/{market_id}")
        lines = [
            f"Anomaly Score: {data['anomalyScore']}/100",
            f"Market: {data.get('marketQuestion') or data['marketId']}",
            f"Assessment: {data['assessment']}",
            "",
            "Signals:",
            f"  Volume spike: {data['signals']['volumeSpike']}",
            f"  24h volume: ${data['signals'].get('volumeChange24h') or 0:,.0f}",
            f"  Price-volume divergence: {data['signals']['priceVolumeDiv']}",
        ]
        return "\n".join(lines)

    @mcp.tool()
    async def moltguard_feed() -> str:
        """Get the top anomaly feed — markets with highest integrity concerns.

        Scans the top 20 active Polymarket markets by 24h volume and returns
        those with anomaly indicators, sorted by anomaly score.
        """
        data = await _guard_get("/api/market/feed")
        lines = [f"Scanned: {data['totalScanned']} markets", ""]
        for m in data.get("markets", []):
            lines.append(f"  [{m['anomalyScore']}] {m.get('marketQuestion', m['marketId'])[:60]}")
        if not data.get("markets"):
            lines.append("  No anomalies detected in top markets.")
        return "\n".join(lines)

    @mcp.tool()
    async def moltguard_credential_issue(address: str) -> str:
        """Issue a W3C Verifiable Credential (AgentTrustCredential) for a wallet.

        The credential contains the agent's trust score, Sybil score,
        ERC-8004 registration status, and MolTrust verification status.
        It is cryptographically signed with Ed25519 (JWS).

        Args:
            address: Base (EVM) wallet address (0x...)
        """
        import json
        data = await _guard_post("/api/credential/issue", {"address": address})
        return json.dumps(data, indent=2)

    @mcp.tool()
    async def moltguard_credential_verify(jws: str) -> str:
        """Verify a MoltGuard Verifiable Credential JWS signature.

        Checks the Ed25519 signature and returns the credential payload if valid.

        Args:
            jws: JWS compact serialization string from a MoltGuard credential
        """
        import json
        data = await _guard_post("/api/credential/verify", {"jws": jws})
        if data.get("valid"):
            return f"VALID credential\n\n{json.dumps(data['payload'], indent=2)}"
        return "INVALID — signature verification failed."

    # --- MT Shopping Tools ---

    @mcp.tool()
    async def mt_shopping_info() -> str:
        """Get MT Shopping API information.

        Returns the MT Shopping service info including version, supported
        endpoints, BuyerAgentCredential schema, and verification details.
        """
        import json
        data = await _guard_get("/shopping/info")
        return json.dumps(data, indent=2)

    @mcp.tool()
    async def mt_shopping_verify(
        credential_jws: str,
        transaction_amount: float,
        transaction_currency: str,
        merchant_id: str,
        item_description: str,
    ) -> str:
        """Verify a shopping transaction against a BuyerAgentCredential.

        Checks the credential signature, spend limits, trust score, and
        returns a verification receipt with approval status.

        Args:
            credential_jws: JWS compact serialization of the BuyerAgentCredential
            transaction_amount: Transaction amount (e.g. 189.99)
            transaction_currency: Currency code (e.g. "USDC")
            merchant_id: Merchant identifier string
            item_description: Description of the item being purchased
        """
        import json
        data = await _guard_post("/shopping/verify", {
            "credentialJws": credential_jws,
            "transaction": {
                "amount": transaction_amount,
                "currency": transaction_currency,
                "merchantId": merchant_id,
                "itemDescription": item_description,
            }
        })
        lines = [
            f"Result: {data.get('result', 'unknown')}",
            f"Receipt ID: {data.get('receiptId', 'N/A')}",
            f"Guard Score: {data.get('guardScore', 'N/A')}/100",
        ]
        if data.get("receiptId"):
            lines.append(f"Receipt URL: https://api.moltrust.ch/guard/shopping/receipt/{data['receiptId']}")
        return "\n".join(lines)

    @mcp.tool()
    async def mt_shopping_issue_vc(
        agent_did: str,
        human_did: str,
        spend_limit: float,
        currency: str,
        categories: str,
        validity_days: int = 30,
    ) -> str:
        """Issue a BuyerAgentCredential (W3C Verifiable Credential) for a shopping agent.

        Creates a cryptographically signed credential that authorizes an AI agent
        to make purchases on behalf of a human, with enforced spend limits.

        Args:
            agent_did: DID of the shopping agent (e.g. "did:moltrust:agent123")
            human_did: DID of the authorizing human (e.g. "did:moltrust:human456")
            spend_limit: Maximum spend amount per transaction
            currency: Currency code (e.g. "USDC", "USD")
            categories: Comma-separated allowed categories (e.g. "electronics,books")
            validity_days: Number of days the credential is valid (default 30)
        """
        import json
        data = await _guard_post("/vc/buyer-agent/issue", {
            "agentDid": agent_did,
            "humanDid": human_did,
            "spendLimit": spend_limit,
            "currency": currency,
            "categories": [c.strip() for c in categories.split(",")],
            "validityDays": validity_days,
        })
        lines = [
            f"Credential issued successfully.",
            f"Agent: {agent_did}",
            f"Human: {human_did}",
            f"Spend limit: {spend_limit} {currency}",
            f"Categories: {categories}",
            f"Valid for: {validity_days} days",
        ]
        if data.get("jws"):
            lines.append(f"\nJWS (first 80 chars): {data['jws'][:80]}...")
        return "\n".join(lines)

    @mcp.tool()
    async def mt_travel_info() -> str:
        """Get MT Travel service information and available endpoints.

        Returns service description, supported segments, and API endpoints
        for the MT Travel booking trust protocol.
        """
        data = await _guard_get("/travel/info")
        return json.dumps(data, indent=2)

    @mcp.tool()
    async def mt_travel_verify(
        agent_did: str,
        vc_json: str,
        merchant: str,
        segment: str,
        amount: float,
        currency: str,
    ) -> str:
        """Verify a travel booking against a TravelAgentCredential.

        Runs a 10-step verification pipeline: VC signature, expiry, agent DID match,
        segment authorization, spend limit, currency, daily cap, trust score,
        delegation chain, and traveler binding.

        Args:
            agent_did: DID of the booking agent (e.g. "did:base:0x...")
            vc_json: The TravelAgentCredential as a JSON string
            merchant: Merchant domain (e.g. "hilton.com")
            segment: Booking segment: hotel, flight, car_rental, or rail
            amount: Booking amount
            currency: Currency code (e.g. "USDC")
        """
        import json as _json
        try:
            vc = _json.loads(vc_json)
        except Exception:
            vc = {}
        data = await _guard_post("/travel/verify", {
            "agentDID": agent_did,
            "vc": vc,
            "merchant": merchant,
            "segment": segment,
            "amount": amount,
            "currency": currency,
        })
        lines = [
            f"Result: {data.get('result', 'unknown')}",
            f"Merchant: {merchant}",
            f"Segment: {segment}",
            f"Amount: {amount} {currency}",
            f"Guard Score: {data.get('guardScore', 'N/A')}/100",
        ]
        if data.get("receiptId"):
            lines.append(f"Receipt: https://api.moltrust.ch/guard/travel/receipt/{data['receiptId']}")
        if data.get("tripId"):
            lines.append(f"Trip ID: {data['tripId']}")
        if data.get("reason"):
            lines.append(f"Reason: {data['reason']}")
        return "\n".join(lines)

    @mcp.tool()
    async def mt_travel_issue_vc(
        agent_did: str,
        principal_did: str,
        segments: str,
        spend_limit: float,
        currency: str,
        traveler_name: str = "",
        validity_days: int = 30,
    ) -> str:
        """Issue a TravelAgentCredential (W3C Verifiable Credential) for a booking agent.

        Creates a cryptographically signed credential that authorizes an AI agent
        to book travel on behalf of a principal (company/human), with enforced
        segment permissions and spend limits.

        Args:
            agent_did: DID of the travel agent (e.g. "did:base:0x...")
            principal_did: DID of the authorizing entity (e.g. "did:base:acme-corp")
            segments: Comma-separated allowed segments (e.g. "hotel,flight,car_rental")
            spend_limit: Maximum spend amount per booking
            currency: Currency code (e.g. "USDC")
            traveler_name: Name of the authorized traveler (optional)
            validity_days: Number of days the credential is valid (default 30)
        """
        import json as _json
        body = {
            "agentDID": agent_did,
            "principalDID": principal_did,
            "segments": [s.strip() for s in segments.split(",")],
            "spendLimit": spend_limit,
            "currency": currency,
            "validDays": validity_days,
        }
        if traveler_name:
            body["traveler"] = {"name": traveler_name}
        data = await _guard_post("/vc/travel-agent/issue", body)
        lines = [
            f"TravelAgentCredential issued.",
            f"Agent: {agent_did}",
            f"Principal: {principal_did}",
            f"Segments: {segments}",
            f"Spend limit: {spend_limit} {currency}",
            f"Valid for: {validity_days} days",
        ]
        if traveler_name:
            lines.append(f"Traveler: {traveler_name}")
        if data.get("jws"):
            lines.append(f"\nJWS (first 80 chars): {data['jws'][:80]}...")
        return "\n".join(lines)


    # --- MT Skill Verification Tools ---

    @mcp.tool()
    async def mt_skill_audit(github_url: str) -> str:
        """Audit an AI agent skill (SKILL.md) for security risks.

        Fetches the SKILL.md from a URL, computes its canonical SHA-256 hash,
        and runs an 8-point security audit checking for prompt injection,
        data exfiltration, tool scope violations, and metadata completeness.
        Score starts at 100 with deductions per finding. Passing score: >= 70.

        Args:
            github_url: URL to the skill (GitHub repo or direct HTTPS link to SKILL.md)
        """
        import urllib.parse
        data = await _guard_get(f"/skill/audit?url={urllib.parse.quote(github_url, safe='')}")
        if "error" in data:
            return f"Audit failed: {data.get('message', data['error'])}"
        lines = [
            f"Skill: {data.get('skillName', 'unknown')} v{data.get('skillVersion', '?')}",
            f"Score: {data['audit']['score']}/100 ({'PASS' if data.get('passed') else 'FAIL'})",
            f"Hash: {data.get('skillHash', 'N/A')}",
            f"Repository: {data.get('repositoryUrl', github_url)}",
            "",
        ]
        findings = data.get("audit", {}).get("findings", [])
        if findings:
            lines.append("Findings:")
            for f in findings:
                lines.append(f"  [{f['severity'].upper()}] {f['category']}: {f['description']} (-{f['deduction']})")
        else:
            lines.append("No security findings.")
        return "\n".join(lines)

    @mcp.tool()
    async def mt_skill_verify(skill_hash: str) -> str:
        """Verify an AI agent skill by its canonical SHA-256 hash.

        Checks if a VerifiedSkillCredential has been issued for this skill hash.
        Returns credential details if verified.

        Args:
            skill_hash: Canonical skill hash (e.g. "sha256:a1b2c3...")
        """
        data = await _guard_get(f"/skill/verify/{skill_hash}")
        if data.get("verified"):
            vc = data["credential"]
            sub = vc["credentialSubject"]
            lines = [
                f"VERIFIED: {sub['skillName']} v{sub['skillVersion']}",
                f"Author: {sub['id']}",
                f"Audit score: {sub['audit']['score']}/100",
                f"Issued: {vc['issuanceDate']}",
                f"Expires: {vc['expirationDate']}",
                f"Anchor TX: {sub.get('anchorTx', 'N/A')}",
            ]
            return "\n".join(lines)
        return f"NOT VERIFIED: {data.get('message', 'No credential found')}"

    @mcp.tool()
    async def mt_skill_issue_vc(author_did: str, repository_url: str) -> str:
        """Issue a VerifiedSkillCredential for an AI agent skill.

        Fetches SKILL.md, runs security audit, and if score >= 70, issues a
        W3C Verifiable Credential signed with Ed25519 (JWS compact serialization).
        Requires x402 payment ($5 USDC) when paywall is active.

        Args:
            author_did: DID of the skill author (e.g. "did:base:0x...")
            repository_url: URL to the skill repository or SKILL.md
        """
        data = await _guard_post("/vc/skill/issue", {
            "authorDID": author_did,
            "repositoryUrl": repository_url,
        })
        if "error" in data:
            return f"Issuance FAILED: {data.get('message', data['error'])}"
        sub = data["credentialSubject"]
        lines = [
            "VerifiedSkillCredential issued.",
            f"Skill: {sub['skillName']} v{sub['skillVersion']}",
            f"Author: {sub['id']}",
            f"Hash: {sub['skillHash']}",
            f"Audit score: {sub['audit']['score']}/100",
            f"Expires: {data['expirationDate']}",
            f"Anchor TX: {sub.get('anchorTx', 'N/A')}",
        ]
        return "\n".join(lines)

    print(f"[MoltGuard MCP] Registered 16 tools (7 guard + 3 shopping + 3 travel + 3 skill)")
