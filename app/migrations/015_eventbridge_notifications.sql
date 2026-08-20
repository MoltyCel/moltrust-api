-- 015: EventBridge notifications alongside the SNS ones.
--
-- AWS activates a SaaS subscription with the EventBridge event "License
-- Updated", not with the SNS "subscribe-success" this table was built for
-- (saas-product-customer-setup, step 9: "Do not activate a product
-- subscription unless you receive a License Updated event"). The SNS consumer
-- stays running until EventBridge is proven to deliver, so this table has to
-- serve both: the SNS columns become nullable, the EventBridge ones are added.
--
-- sns_message_id is renamed to event_id because it now holds either an SNS
-- MessageId or an EventBridge event id. Keeping the old name would repeat the
-- mistake the AWS sample makes, where a column called customerIdentifier
-- stores a license ARN.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'aws_marketplace_notifications'
                 AND column_name = 'sns_message_id') THEN
        ALTER TABLE aws_marketplace_notifications RENAME COLUMN sns_message_id TO event_id;
    END IF;
END $$;

ALTER TABLE aws_marketplace_notifications
    ALTER COLUMN action DROP NOT NULL;

ALTER TABLE aws_marketplace_notifications
    ADD COLUMN IF NOT EXISTS channel             TEXT NOT NULL DEFAULT 'sns',
    ADD COLUMN IF NOT EXISTS detail_type         TEXT,
    ADD COLUMN IF NOT EXISTS event_source        TEXT,
    ADD COLUMN IF NOT EXISTS agreement_id        TEXT,
    ADD COLUMN IF NOT EXISTS license_arn         TEXT,
    ADD COLUMN IF NOT EXISTS acceptor_account_id TEXT;

-- Correlation index for the EventBridge path: acceptor.accountId is present on
-- every agreement and license event, license.arn only on the license ones.
CREATE INDEX IF NOT EXISTS aws_mp_notifications_acceptor_idx
    ON aws_marketplace_notifications (acceptor_account_id, product_code)
    WHERE NOT matched;

COMMIT;
