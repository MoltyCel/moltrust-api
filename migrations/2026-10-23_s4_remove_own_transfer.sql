-- Ops S4 (2026-10-23). The 5 USDC paid for the first Skill VC anchored with
-- the in-process signer (moltguard #63) is money from our own side: Lars paid
-- it from his wallet 0x825c…56a6 to the MolTrust wallet 0xD63f…5800 (tx
-- 0xe70a6b4a…, block 52981724) to produce the on-chain proof S4 asks for.
-- moltguard booked it in payment_events on 2026-10-23 10:58 UTC as a payment
-- received, so it counted in every sum over the table.
--
-- Removed for the reasons given in 2026-10-13_c4_remove_own_transfer.sql.
-- The row is reconstructable from the chain by its hash. No usdc_deposits row
-- and no credits exist for it (the sender is bound to no DID).
--
-- x402_receipts keeps its row for this hash on purpose: it is the replay guard
-- that stops the same transaction from paying for a second request.
--
-- Guarded on sender, recipient, amount and the absent DID, so it removes this
-- row and nothing else. Idempotent.

DELETE FROM payment_events
 WHERE tx_hash = '0xe70a6b4a137d6cfe6e2ac922910171c09b6d010fee4451341b56f5aed79ed33e'
   AND LOWER(from_address) = '0x825c87f39ec42cfd717a1ed47764ad2a2c7d56a6'
   AND LOWER(to_address) = '0xd63f988201a0bd184deab7d4e97b1717b25a5800'
   AND amount_usdc = 5
   AND did IS NULL
   AND NOT EXISTS (SELECT 1 FROM usdc_deposits
                    WHERE tx_hash = '0xe70a6b4a137d6cfe6e2ac922910171c09b6d010fee4451341b56f5aed79ed33e');
