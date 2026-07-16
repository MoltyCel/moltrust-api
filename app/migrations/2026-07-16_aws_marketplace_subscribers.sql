-- AWS Marketplace SaaS fulfillment subscribers (created live by
-- ensure_aws_marketplace_tables at app startup; this file is the repo record).
CREATE TABLE IF NOT EXISTS aws_marketplace_subscribers (
    customer_aws_account_id TEXT NOT NULL,
    license_arn             TEXT,
    product_code            TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    moltrust_account_id     TEXT,
    PRIMARY KEY (customer_aws_account_id, product_code)
);
