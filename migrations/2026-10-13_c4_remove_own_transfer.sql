-- Ops C4 (2026-10-13). The 0.39 USDC the wallet rotation moved from OLD to NEW
-- on 2026-10-12 (tx 0xb112209f…, block 52520458) is money moving between our
-- own wallets. The poller booked it in payment_events on 2026-10-13 07:23 UTC as
-- a payment received, so it counted in every sum over the table
-- (app/main.py:9512, app/usage.py:179, the funnel).
--
-- Removed rather than marked: payment_events belongs to postgres and the app
-- role cannot add a column, and the readers sum every row, so a mark in a side
-- table would leave the figure wrong until each reader learned to join it. The
-- row is reconstructable from the chain by its hash. No usdc_deposits row and
-- no credits exist for it (the sender is bound to no DID).
--
-- Guarded on sender, amount and the absent DID, so it removes this row and
-- nothing else. Idempotent.

DELETE FROM payment_events
 WHERE tx_hash = '0xb112209f607eb01dbb223900566d3f76f617c5e5cf5c5307ae28a90812be53fd'
   AND LOWER(from_address) = '0x380238347e58435f40b4da1f1a045a271d5838f5'
   AND amount_usdc = 0.39
   AND did IS NULL
   AND NOT EXISTS (SELECT 1 FROM usdc_deposits
                    WHERE tx_hash = '0xb112209f607eb01dbb223900566d3f76f617c5e5cf5c5307ae28a90812be53fd');
