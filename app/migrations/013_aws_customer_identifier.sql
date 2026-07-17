-- 013_aws_customer_identifier.sql  (2026-07-17)
-- D: persist BOTH ResolveCustomer values on the fulfillment record.
-- Adds customer_identifier (AWS CustomerIdentifier) alongside the existing
-- customer_aws_account_id (AWS CustomerAWSAccountId). Additive & reversible;
-- no rename, no drop of the existing PK column.
--
-- ===================== UP =====================
ALTER TABLE aws_marketplace_subscribers ADD COLUMN IF NOT EXISTS customer_identifier TEXT;

-- ===================== DOWN ===================
-- ALTER TABLE aws_marketplace_subscribers DROP COLUMN IF EXISTS customer_identifier;
