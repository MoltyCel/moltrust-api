-- 016: agreement_id on subscribers — the correlation key the events all carry.
--
-- Verified against the four real EventBridge events captured on 2026-08-20:
-- detail.agreement.id is present on all four types, detail.license.arn and
-- detail.product only on the License ones. ResolveCustomer returns neither an
-- agreement id nor anything else tying a subscriber row to one, so the id is
-- stamped on from the first license event via the licence ARN, which is the
-- one field both sides share.

BEGIN;

ALTER TABLE aws_marketplace_subscribers
    ADD COLUMN IF NOT EXISTS agreement_id TEXT;

CREATE INDEX IF NOT EXISTS aws_mp_subscribers_agreement_idx
    ON aws_marketplace_subscribers (agreement_id)
    WHERE agreement_id IS NOT NULL;

COMMIT;
