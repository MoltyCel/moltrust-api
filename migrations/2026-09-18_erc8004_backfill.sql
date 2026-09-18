-- Backfill agents.erc8004_agent_id for registrations that exist on-chain.
--
-- Four agent IDs are held by 0x3802…38F5 in the Base IdentityRegistry
-- (0x8004A169FB4a3325136EB29fA0ceB6D2e539a432). The table knew about one.
--
--   21023  did:web:api.moltrust.ch        platform identity, not an agents row
--   21351  did:moltrust:455d06aa3d9d4fac  present, link missing  <- this file
--   21352  did:moltrust:5709efa5e68b4da6  already linked
--   33553  did:web:api.moltrust.ch        platform identity, not an agents row
--
-- Every id below was verified with ownerOf() against Base mainnet before being
-- written here; this records a fact that is already true on-chain rather than
-- asserting a new one.
--
-- The consequence of the gap was visible from outside: the registration file at
--   https://api.moltrust.ch/agents/did:moltrust:455d06aa3d9d4fac/erc8004
-- served "registrations": [] while the token pointing at that very URL was
-- 21351 — the document denied its own registration.
--
-- Idempotent: the WHERE clause makes a second run a no-op, and it will not
-- overwrite a different id that someone set in the meantime.

UPDATE agents
   SET erc8004_agent_id = 21351
 WHERE did = 'did:moltrust:455d06aa3d9d4fac'
   AND erc8004_agent_id IS NULL;
