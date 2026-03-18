-- MT Salesguard: Brand product provenance for the A2A economy
-- Migration 006 — idempotent

CREATE TABLE IF NOT EXISTS brands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    did TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    domain TEXT,
    api_key TEXT NOT NULL,
    contact_email TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id),
    product_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    credential_hash TEXT,
    base_anchor TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS resellers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id),
    reseller_did TEXT NOT NULL,
    reseller_name TEXT NOT NULL,
    authorized_skus TEXT[] DEFAULT '{}',
    credential_hash TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brands_did ON brands (did);
CREATE INDEX IF NOT EXISTS idx_brands_api_key ON brands (api_key);
CREATE INDEX IF NOT EXISTS idx_products_product_id ON products (product_id);
CREATE INDEX IF NOT EXISTS idx_products_brand_id ON products (brand_id);
CREATE INDEX IF NOT EXISTS idx_resellers_brand_id ON resellers (brand_id);
CREATE INDEX IF NOT EXISTS idx_resellers_did ON resellers (reseller_did);
