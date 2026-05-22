# Sprint 1.2.2 — Code-Patches

Änderungen an `~/moltstack/app/swarm/trust_score.py` (Patch 1) und `~/moltstack/app/main.py` (Patch 2 + 3).

## Patch 1: Sybil-Penalty Whitelist für Seed-DIDs

Stelle in `compute_phase2_score()`, vor Score-Aggregation:

```python
SEED_DID_WHITELIST: frozenset[str] = frozenset()  # zur Laufzeit geladen

def _load_seed_whitelist(conn) -> frozenset[str]:
    rows = conn.execute("SELECT did FROM swarm_seeds").fetchall()
    return frozenset(r[0] for r in rows)

# In compute_phase2_score(), vor Score-Aggregation:
if did in SEED_DID_WHITELIST:
    sybil_penalty = 0.0
```

Wirkung: Ambassador `breakdown.sybil_penalty` → 0.0 (war 10.0), `trust_score` steigt auf ~90. Flags (`low_confidence`, `ghost_agent`) bleiben sichtbar.

## Patch 2: SeedRequest DID-Validator

Pydantic-Modell für `POST /swarm/seed`:

```python
from pydantic import field_validator
import re

SEED_DID_STRICT = re.compile(r'^did:moltrust:[a-f0-9]{16}$')

class SeedRequest(BaseModel):
    did: str
    label: str
    base_score: float

    @field_validator('did')
    @classmethod
    def validate_did_format(cls, v: str) -> str:
        if not SEED_DID_STRICT.match(v):
            raise ValueError(
                f"Seed-DID muss strikt 16-hex sein. Vanity-Identifier "
                f"wie '{v}' werden nicht mehr als neue Seeds akzeptiert."
            )
        return v
```

Wirkung: Blockiert NUR neue Seed-POSTs mit Vanity-DIDs. Bestehende Lookups auf `ambassador0001` bleiben unverändert 200.

## Patch 3: avg_trust_score-Aggregation in /swarm/stats

Handler von `GET /swarm/stats` in `app/main.py`:

```python
avg = conn.execute("""
    SELECT AVG(score)
      FROM trust_score_cache
     WHERE score IS NOT NULL
       AND did NOT IN (SELECT did FROM agents WHERE revoked_at IS NOT NULL)
""").fetchone()[0] or 0.0
```

Wirkung: 5× consecutive Calls → identischer `avg_trust_score` (war 85.0 → 77.5 drift).

## Patch 4: propagation_depth-Diagnose (kein Code-Fix, Auftrag)

`/swarm/stats` liefert `propagation_depth: 0` trotz 5 Seeds + 61 Endorsements. Vor Code-Fix klären:

1. Reporting-Bug: `grep -n "propagation_depth" ~/moltstack/app/*.py`
2. Config-Default 0: Env `MOLTGRAPH_MAX_DEPTH`
3. Graph trivial: `SELECT COUNT(*) FROM endorsements WHERE endorser_did IN (SELECT did FROM swarm_seeds)`

Bei Hypothese 3: Seeds endorsieren niemanden → strukturelles Cold-Start-Problem, Operations-Aufgabe.
