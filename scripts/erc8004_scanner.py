#!/usr/bin/env python3
"""
ERC-8004 Registry Scanner — via Goldsky Subgraph (fast, no RPC limits).
Fetches all agents from the 8004tokens subgraph on Base.
"""
import asyncio, asyncpg, json, logging, sys
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("erc8004_scanner")

SUBGRAPH = "https://api.goldsky.com/api/public/project_cml7i9a7gb1wi01y1gn2keqqm/subgraphs/agent8-base/v1.0.6/gn"
BATCH = 1000
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 5000


def query_subgraph(skip=0, first=1000):
    q = f'''{{ agents(first: {first}, skip: {skip}, orderBy: agentId, orderDirection: asc) {{
        agentId owner agentWallet agentURI name description active createdAt
    }} }}'''
    data = json.dumps({"query": q}).encode()
    req = Request(SUBGRAPH, data=data, headers={"Content-Type": "application/json", "User-Agent": "MolTrust/1.0"})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("data", {}).get("agents", [])


async def main():
    conn = await asyncpg.connect(user="moltstack", database="moltstack")

    log.info("Fetching ERC-8004 agents from Goldsky subgraph (limit %d)...", LIMIT)

    known_ids = {r["agent_id"] for r in await conn.fetch("SELECT agent_id FROM erc8004_outreach")}
    mt_wallets = {r["w"] for r in await conn.fetch(
        "SELECT LOWER(wallet_address) as w FROM agents WHERE wallet_address IS NOT NULL"
    )}

    all_agents = []
    skip = 0
    while len(all_agents) < LIMIT:
        batch = query_subgraph(skip=skip, first=BATCH)
        if not batch:
            break
        all_agents.extend(batch)
        skip += BATCH
        log.info("  Fetched %d agents so far...", len(all_agents))

    log.info("Total fetched: %d agents", len(all_agents))

    new_count = registered = outreach_cand = 0

    for a in all_agents:
        agent_id = int(a["agentId"])
        if agent_id in known_ids:
            continue

        wallet = a.get("agentWallet") or a.get("owner", "")
        owner = a.get("owner", "")
        uri = a.get("agentURI") or ""
        name = a.get("name") or ""

        is_reg = wallet.lower() in mt_wallets if wallet else False

        await conn.execute("""
            INSERT INTO erc8004_outreach (agent_id, wallet_address, owner_address, token_uri, moltrust_registered)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (agent_id) DO UPDATE SET
                wallet_address = $2, owner_address = $3, token_uri = $4, moltrust_registered = $5
        """, agent_id, wallet[:64] if wallet else None, owner[:64] if owner else None,
            (name + " | " + uri)[:500] if uri else name[:500] if name else None, is_reg)

        new_count += 1
        if is_reg:
            registered += 1
        else:
            outreach_cand += 1

    total = await conn.fetchval("SELECT COUNT(*) FROM erc8004_outreach")
    total_cand = await conn.fetchval(
        "SELECT COUNT(*) FROM erc8004_outreach WHERE moltrust_registered = FALSE AND outreach_sent = FALSE"
    )
    await conn.close()

    log.info("\n=== ERC-8004 Scanner Report ===")
    log.info("Agents on Base:        %d", len(all_agents))
    log.info("New to DB:             %d", new_count)
    log.info("Already at MolTrust:   %d", registered)
    log.info("Outreach candidates:   %d", outreach_cand)
    log.info("Total in DB:           %d", total)
    log.info("Total outreach pool:   %d", total_cand)


if __name__ == "__main__":
    asyncio.run(main())
