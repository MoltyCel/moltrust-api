# PR #70 — Sprint 1.2.2: Seed-Konsolidierung + Score-Härtung

**Repo:** moltrust-api · **Voraussetzung:** PR #66 deployed
**Reviewer:** §2.3 Cross-Review (GPT-4o + Gemini + Perplexity)

## Scope

1. **Daten-Bereinigung** (`01_migration.sql`): Seeds + Stale-Agents
2. **Code-Härtung** (`02_code_patches.md`): Sybil-Whitelist + SeedRequest-Validator + stats-Stabilität
3. **Diagnose** (`02_code_patches.md` §Patch 4): propagation_depth=0

## Was wird verändert

| Tabelle | Änderung | DIDs |
|---|---|---|
| `agents` | display_name fix | `d34ed796...` ("moltguard_v1" → "TrustScout") |
| `agents` | revoked_at gesetzt | `28a0984ab85d4c40`, `te5tharne550001`, `vcone` |
| `swarm_seeds` | DELETE | `vcone`, `662a7181e0154998` |

| Code-Stelle | Patch |
|---|---|
| `compute_phase2_score` | SEED_DID_WHITELIST → Sybil-Penalty=0 für Seeds |
| `SeedRequest` Pydantic | Strict 16-hex Validator → Vanity-DIDs blockiert |
| `GET /swarm/stats` | Definierte Aggregation → `avg_trust_score` stabil |

## Blast-Radius

**Hoch:** `compute_phase2_score`-Änderung wirkt auf alle Score-Endpoints. Ambassador-Score steigt sichtbar (80 → ~90).
**Mittel:** `swarm/stats top_trusted`-Liste schrumpft. Memory-Eintrag #5 (agent count, Seed-Anzahl) updaten.
**Niedrig:** SeedRequest-Validator wirkt nur auf NEUE POSTs. Revozierte Stubs hatten 0 Endorsements.

## Apply-Reihenfolge

```bash
# 1. DB-Backup (PFLICHT)
pg_dump -h localhost -U moltstack -d moltstack \
        -t agents -t swarm_seeds --column-inserts \
        > /home/moltstack/backups/sprint-1.2.2-pre.sql

# 2. Baseline
bash migrations/sprint-1.2.2/03_verify_post_deploy.sh > before.log

# 3. SQL-Migration (ON_ERROR_STOP=on Pflicht — DO-Blocks RAISE EXCEPTION)
psql -h localhost -U moltstack -d moltstack \
     --set ON_ERROR_STOP=on \
     -f migrations/sprint-1.2.2/01_migration.sql

# 4. Code-Patches anwenden (siehe 02_code_patches.md)

# 5. Service-Restart
sudo systemctl restart moltstack

# 6. Re-Propagation
curl -X POST https://api.moltrust.ch/swarm/propagate/did:moltrust:d34ed796a4dc4698
curl -X POST https://api.moltrust.ch/swarm/propagate/did:moltrust:ambassador0001

# 7. Verify + Diff
bash migrations/sprint-1.2.2/03_verify_post_deploy.sh > after.log
diff before.log after.log
```

## Rollback

```bash
psql -h localhost -U moltstack -d moltstack \
     --set ON_ERROR_STOP=on \
     -f migrations/sprint-1.2.2/99_rollback.sql
sudo systemctl restart moltstack
curl -X POST https://api.moltrust.ch/swarm/propagate/did:moltrust:662a7181e0154998
bash migrations/sprint-1.2.2/03_verify_post_deploy.sh
```

vcone und display_name 'TrustScout' werden BEWUSST nicht rolled back (siehe Header-Kommentare in `99_rollback.sql`).

## Offen nach Sprint 1.2.2

- `propagation_depth: 0` Root-Cause (3 Hypothesen in `02_code_patches.md` §Patch 4)
- Seeds endorsieren niemanden (Operations-Aufgabe, kein Code-Fix)
- DID-Validierungs-Konsistenz global (Audit aller DID-Eingangspunkte)
