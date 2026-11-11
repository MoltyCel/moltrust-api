-- Ops V6 criterion 13 (2026-10-18). Our own test DID did:moltrust:a2f9415d27094890
-- had no registered key. Probes must go through the same door as outside
-- callers, so it gets one and signs its requests (RFC 9421) instead of using
-- an API key. The identifier is assigned-opaque (minted before spec v0.2), so
-- binding a key does not contradict its derivation rule.
--
-- The private key lives on the box in ~/.moltrust_testdids/a2f9415d27094890.ed25519
-- (mode 600), generated there 2026-10-18; it never left the box.
--
-- Guarded: only while the DID has no key and is not revoked. Idempotent.

UPDATE agents
   SET public_key_hex = '7cdcf74bb78690c892df6a0ca429477d45884c53cdc080191634191210b073cd'
 WHERE did = 'did:moltrust:a2f9415d27094890'
   AND public_key_hex IS NULL
   AND revoked_at IS NULL;
