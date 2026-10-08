-- Bring a database up to the live schema of 2026-10-08: the tables and columns
-- that exist live but not in moltstack_sandbox.
--
-- Generated from `pg_dump --schema-only` of the live database (structure only,
-- no rows) for the eighteen tables the sandbox lacks, and from pg_attribute for
-- the seventeen columns missing in four tables both have. Every statement is
-- idempotent, so the file is a no-op on the live database.
--
-- Written for the sandbox, where tests/conftest.py points the suite: without
-- credit_balances the credit_test_agent fixture fails before any test runs.
--
-- Not covered: columns present in both databases with a different type or
-- default (api_keys.email; interaction_proof_records.confidence, produced_at,
-- anchor_status, agent_did, anchor_tx). They are listed, not changed.

BEGIN;

-- 1. Tables that exist live and not in the sandbox
CREATE TABLE IF NOT EXISTS public.agent_environment (
    did text NOT NULL,
    environment text DEFAULT 'production'::text NOT NULL,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_environment_environment_check CHECK ((environment = ANY (ARRAY['production'::text, 'sandbox'::text, 'test'::text])))
);

CREATE TABLE IF NOT EXISTS public.agent_profile (
    did text NOT NULL,
    declared_capabilities text[],
    declared_description text,
    agent_card_url text,
    declared_framework text,
    asn text,
    country text,
    cloud_provider text,
    ua_framework text,
    first_endpoints_24h text[],
    wallet_age_days integer,
    erc8004_skills text[],
    a2a_skills text[],
    taskmarket_tasks integer,
    observed_from text,
    observed_rows integer,
    enriched_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.agent_source (
    did character varying NOT NULL,
    source character varying NOT NULL,
    surface character varying,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.agent_source IS 'Channel a registration is attributed to. Role-owned side table; agents is postgres-owned.';

CREATE TABLE IF NOT EXISTS public.credential_anchors (
    credential_id integer NOT NULL,
    tx_hash text NOT NULL,
    block bigint,
    merkle_root text NOT NULL,
    merkle_proof jsonb NOT NULL,
    anchored_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.credit_balances (
    did text NOT NULL,
    balance bigint DEFAULT 0 NOT NULL,
    currency text DEFAULT 'CREDITS'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT credit_balances_balance_check CHECK ((balance >= 0))
);

CREATE TABLE IF NOT EXISTS public.gate_decisions (
    id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    did text,
    path text NOT NULL,
    amount integer,
    reason text NOT NULL,
    via text,
    detail text
);

COMMENT ON COLUMN public.gate_decisions.detail IS 'Verifier message behind `reason`, capped at 200 chars by the writer. NULL on an allow and on rows written before 2026-10-06.';

CREATE SEQUENCE IF NOT EXISTS public.gate_decisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.gate_decisions_id_seq OWNED BY public.gate_decisions.id;

CREATE TABLE IF NOT EXISTS public.gate_measurement (
    id bigint NOT NULL,
    measured_at timestamp with time zone DEFAULT now() NOT NULL,
    priced_raw bigint NOT NULL,
    discounted_raw bigint NOT NULL,
    priced_delta bigint NOT NULL,
    discounted_delta bigint NOT NULL,
    denied_by_reason jsonb DEFAULT '{}'::jsonb NOT NULL,
    restarted boolean DEFAULT false NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS public.gate_measurement_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.gate_measurement_id_seq OWNED BY public.gate_measurement.id;

CREATE TABLE IF NOT EXISTS public.linkedin_oauth (
    id smallint DEFAULT 1 NOT NULL,
    state text,
    state_at timestamp with time zone,
    token_enc bytea,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT linkedin_oauth_id_check CHECK ((id = 1))
);

COMMENT ON TABLE public.linkedin_oauth IS 'LinkedIn OAuth: single row. token_enc is Fernet ciphertext, key derived from LINKEDIN_CLIENT_SECRET — the database never holds the clear token. See app/linkedin_oauth.py.';

CREATE TABLE IF NOT EXISTS public.pool_spend (
    id bigint NOT NULL,
    pool text NOT NULL,
    usdc numeric(18,6) NOT NULL,
    tx_hash text,
    purpose text NOT NULL,
    tranche integer,
    spent_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    state text DEFAULT 'spent'::text NOT NULL,
    CONSTRAINT pool_spend_state_check CHECK ((state = ANY (ARRAY['spent'::text, 'escrowed'::text, 'refunded'::text]))),
    CONSTRAINT pool_spend_usdc_check CHECK ((usdc > (0)::numeric))
);

CREATE SEQUENCE IF NOT EXISTS public.pool_spend_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.pool_spend_id_seq OWNED BY public.pool_spend.id;

CREATE TABLE IF NOT EXISTS public.skill_audits (
    skill_hash text NOT NULL,
    skill_name text,
    skill_version text,
    github_url text,
    profile text NOT NULL,
    score integer NOT NULL,
    passed boolean NOT NULL,
    findings jsonb DEFAULT '[]'::jsonb NOT NULL,
    auditor_version text NOT NULL,
    audited_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.slot_shadow_log (
    id bigint NOT NULL,
    day date NOT NULL,
    operator_did text NOT NULL,
    tier text,
    slots_counted integer DEFAULT 0 NOT NULL,
    tier_limit integer,
    would_be_overage integer DEFAULT 0 NOT NULL,
    would_be_blocked boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS public.slot_shadow_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.slot_shadow_log_id_seq OWNED BY public.slot_shadow_log.id;

CREATE TABLE IF NOT EXISTS public.usage_daily (
    day date NOT NULL,
    endpoint_key text NOT NULL,
    status_code integer NOT NULL,
    source text NOT NULL,
    traffic_class text NOT NULL,
    requests bigint NOT NULL,
    distinct_ips integer NOT NULL,
    distinct_dids integer NOT NULL,
    rolled_up_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.usage_daily_keys (
    day date NOT NULL,
    key_fp text NOT NULL,
    did text,
    calls bigint DEFAULT 0 NOT NULL
);

CREATE TABLE IF NOT EXISTS public.usage_daily_payments (
    day date NOT NULL,
    challenges_402 bigint NOT NULL,
    settled_payments bigint NOT NULL,
    distinct_wallets integer NOT NULL,
    usdc_total numeric(18,6) NOT NULL,
    rolled_up_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.usage_meter (
    did text NOT NULL,
    month_key text NOT NULL,
    endpoint_key text NOT NULL,
    count bigint DEFAULT 0 NOT NULL
);

CREATE TABLE IF NOT EXISTS public.wallet_first_tx (
    wallet text NOT NULL,
    chain text DEFAULT 'base'::text NOT NULL,
    first_block bigint,
    first_ts timestamp with time zone,
    source text NOT NULL,
    measured_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.x402_authorizations (
    nonce text NOT NULL,
    payer text,
    path text NOT NULL,
    amount_usdc numeric NOT NULL,
    tx_hash text,
    seen_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.x402_receipts (
    tx_hash text NOT NULL,
    path text NOT NULL,
    amount_usdc numeric NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.gate_decisions ALTER COLUMN id SET DEFAULT nextval('public.gate_decisions_id_seq'::regclass);

ALTER TABLE ONLY public.gate_measurement ALTER COLUMN id SET DEFAULT nextval('public.gate_measurement_id_seq'::regclass);

ALTER TABLE ONLY public.pool_spend ALTER COLUMN id SET DEFAULT nextval('public.pool_spend_id_seq'::regclass);

ALTER TABLE ONLY public.slot_shadow_log ALTER COLUMN id SET DEFAULT nextval('public.slot_shadow_log_id_seq'::regclass);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_environment_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.agent_environment
    ADD CONSTRAINT agent_environment_pkey PRIMARY KEY (did)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_profile_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.agent_profile
    ADD CONSTRAINT agent_profile_pkey PRIMARY KEY (did)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_source_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.agent_source
    ADD CONSTRAINT agent_source_pkey PRIMARY KEY (did)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'credential_anchors_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.credential_anchors
    ADD CONSTRAINT credential_anchors_pkey PRIMARY KEY (credential_id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'credit_balances_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.credit_balances
    ADD CONSTRAINT credit_balances_pkey PRIMARY KEY (did)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'gate_decisions_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.gate_decisions
    ADD CONSTRAINT gate_decisions_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'gate_measurement_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.gate_measurement
    ADD CONSTRAINT gate_measurement_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'linkedin_oauth_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.linkedin_oauth
    ADD CONSTRAINT linkedin_oauth_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pool_spend_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.pool_spend
    ADD CONSTRAINT pool_spend_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'skill_audits_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.skill_audits
    ADD CONSTRAINT skill_audits_pkey PRIMARY KEY (skill_hash)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'slot_shadow_log_day_operator_did_key') THEN
    EXECUTE 'ALTER TABLE ONLY public.slot_shadow_log
    ADD CONSTRAINT slot_shadow_log_day_operator_did_key UNIQUE (day, operator_did)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'slot_shadow_log_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.slot_shadow_log
    ADD CONSTRAINT slot_shadow_log_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usage_daily_keys_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.usage_daily_keys
    ADD CONSTRAINT usage_daily_keys_pkey PRIMARY KEY (day, key_fp)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usage_daily_payments_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.usage_daily_payments
    ADD CONSTRAINT usage_daily_payments_pkey PRIMARY KEY (day)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usage_daily_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.usage_daily
    ADD CONSTRAINT usage_daily_pkey PRIMARY KEY (day, endpoint_key, status_code, source, traffic_class)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usage_meter_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.usage_meter
    ADD CONSTRAINT usage_meter_pkey PRIMARY KEY (did, month_key, endpoint_key)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'wallet_first_tx_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.wallet_first_tx
    ADD CONSTRAINT wallet_first_tx_pkey PRIMARY KEY (wallet)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'x402_authorizations_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.x402_authorizations
    ADD CONSTRAINT x402_authorizations_pkey PRIMARY KEY (nonce)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'x402_receipts_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.x402_receipts
    ADD CONSTRAINT x402_receipts_pkey PRIMARY KEY (tx_hash)';
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS agent_source_source_idx ON public.agent_source USING btree (source, recorded_at);

CREATE INDEX IF NOT EXISTS gate_measurement_measured_at_idx ON public.gate_measurement USING btree (measured_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_profile_cluster ON public.agent_profile USING btree (country, ua_framework);

CREATE INDEX IF NOT EXISTS idx_agent_profile_enriched ON public.agent_profile USING btree (enriched_at NULLS FIRST);

CREATE INDEX IF NOT EXISTS idx_credential_anchors_tx ON public.credential_anchors USING btree (tx_hash);

CREATE INDEX IF NOT EXISTS idx_gate_decisions_did ON public.gate_decisions USING btree (did) WHERE (did IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_gate_decisions_ts ON public.gate_decisions USING btree (ts DESC);

CREATE INDEX IF NOT EXISTS idx_pool_spend_pool_time ON public.pool_spend USING btree (pool, spent_at);

CREATE INDEX IF NOT EXISTS idx_pool_spend_state ON public.pool_spend USING btree (state);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pool_spend_tx ON public.pool_spend USING btree (lower(tx_hash)) WHERE (tx_hash IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_skill_audits_audited_at ON public.skill_audits USING btree (audited_at DESC);

CREATE INDEX IF NOT EXISTS idx_slot_shadow_day ON public.slot_shadow_log USING btree (day DESC);

CREATE INDEX IF NOT EXISTS idx_usage_daily_class ON public.usage_daily USING btree (traffic_class, day DESC);

CREATE INDEX IF NOT EXISTS idx_usage_daily_day ON public.usage_daily USING btree (day DESC);

CREATE INDEX IF NOT EXISTS idx_usage_daily_endpoint ON public.usage_daily USING btree (endpoint_key, day DESC);

CREATE INDEX IF NOT EXISTS idx_usage_daily_keys_day ON public.usage_daily_keys USING btree (day DESC);

CREATE INDEX IF NOT EXISTS idx_usage_meter_month ON public.usage_meter USING btree (month_key);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'credit_balances_did_fkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.credit_balances
    ADD CONSTRAINT credit_balances_did_fkey FOREIGN KEY (did) REFERENCES public.agents(did)';
  END IF;
END $$;

-- 2. Columns that exist live and not in the sandbox
ALTER TABLE public.api_keys ADD COLUMN IF NOT EXISTS signup_method text;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS schema_version character varying(10) DEFAULT '1.0'::character varying NOT NULL;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS output_type character varying(50) DEFAULT 'generic'::character varying NOT NULL;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS source_hashes jsonb DEFAULT '[]'::jsonb NOT NULL;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS source_refs jsonb DEFAULT '[]'::jsonb NOT NULL;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS confidence_basis character varying(50) DEFAULT 'declared'::character varying NOT NULL;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS aae_ref character varying(100);
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS agent_signature character varying(200); -- live: NOT NULL without default; added nullable so existing rows do not refuse it
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS created_at timestamp with time zone DEFAULT now() NOT NULL;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS anchor_block bigint;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS merkle_proof jsonb;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS anchor_retries integer DEFAULT 0 NOT NULL;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS outcome_hash character varying(100);
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS outcome_at timestamp with time zone;
ALTER TABLE public.interaction_proof_records ADD COLUMN IF NOT EXISTS chain character varying(20) DEFAULT 'base'::character varying NOT NULL;
ALTER TABLE public.payment_events ADD COLUMN IF NOT EXISTS path text;
ALTER TABLE public.request_log ADD COLUMN IF NOT EXISTS caller_framework text;

COMMIT;
