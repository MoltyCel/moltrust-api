"""Backfill AgentTrustCredentials for all agents that don't have one."""
import sys, os, json, asyncio
sys.path.insert(0, os.path.expanduser('~/moltstack'))

import asyncpg
from app.credentials import issue_credential

async def main():
    conn = await asyncpg.connect(host='localhost', database='moltstack',
                                  user='moltstack', password=os.getenv('MOLTSTACK_DB_PW', ''))

    rows = await conn.fetch("""
        SELECT a.did, a.display_name
        FROM agents a
        LEFT JOIN credentials c ON a.did = c.subject_did AND c.credential_type = 'AgentTrustCredential'
        WHERE c.id IS NULL
    """)

    print(f'Found {len(rows)} agents without AgentTrustCredential')

    issued = 0
    errors = 0
    for row in rows:
        did = row['did']
        try:
            # Get current reputation
            rep_row = await conn.fetchrow(
                'SELECT COALESCE(AVG(score),0) as avg, COUNT(*) as total FROM ratings WHERE to_did=$1', did
            )
            reputation = {'score': round(float(rep_row['avg']), 2), 'total_ratings': int(rep_row['total'])}

            vc = issue_credential(did, 'AgentTrustCredential', {
                'trustProvider': 'MolTrust',
                'reputation': reputation,
                'verified': True
            })

            await conn.execute(
                """INSERT INTO credentials (subject_did, credential_type, issuer, issued_at, expires_at, proof_value, raw_vc)
                VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                did, 'AgentTrustCredential', vc['issuer'],
                __import__('datetime').datetime.fromisoformat(vc['issuanceDate'].replace('Z','')),
                __import__('datetime').datetime.fromisoformat(vc['expirationDate'].replace('Z','')),
                vc['proof']['proofValue'],
                json.dumps(vc)
            )
            issued += 1
        except Exception as e:
            errors += 1
            print(f'Error for {did}: {e}')

    await conn.close()
    print(f'Done. Issued: {issued}, Errors: {errors}')

asyncio.run(main())
