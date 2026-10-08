-- Bring a database up to the live schema of 2026-10-08, for the tables in use.
--
-- Generated from `pg_dump --schema-only` of the live database (structure only,
-- no rows) and from pg_attribute for seventeen missing columns. Every statement
-- is idempotent, so the file is a no-op on the live database.
--
-- Covers 34 tables: the ones moltstack_sandbox lacked on 2026-10-08 and
-- that are in use, plus the twenty live tables no code in any repository
-- creates and that are read or written by running code.
--
-- Left out on purpose, listed for a decision rather than carried:
--   dead (no rows or no write in 30 days, no writer):
--     agent_environment, caep_events_legacy_20260415, conversion_funnel,
--     probe_activity, probe_agents, slot_shadow_log, verified_badges
--   unclear: outreach_sent, swarm_graph, webhook_events
--
-- Not covered: columns present in both databases with a different type or
-- default (api_keys.email; several in interaction_proof_records).

BEGIN;

-- 1. Tables
CREATE TABLE IF NOT EXISTS public.agent_delegations (
    id integer NOT NULL,
    parent_did character varying(40) NOT NULL,
    child_did character varying(40) NOT NULL,
    aae_id character varying(255),
    credential_type character varying(100),
    hop_depth integer DEFAULT 1 NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    revoked_at timestamp without time zone
);

CREATE SEQUENCE IF NOT EXISTS public.agent_delegations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_delegations_id_seq OWNED BY public.agent_delegations.id;

CREATE TABLE IF NOT EXISTS public.agent_messages (
    id integer NOT NULL,
    to_did character varying(40) NOT NULL,
    message text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS public.agent_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_messages_id_seq OWNED BY public.agent_messages.id;

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

CREATE TABLE IF NOT EXISTS public.api_key_labels (
    api_key_prefix character varying(16) NOT NULL,
    label text NOT NULL,
    color character varying(20) DEFAULT 'gray'::character varying,
    updated_at timestamp without time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.caller_labels (
    ip character varying(45) NOT NULL,
    label text,
    color character varying(20) DEFAULT 'gray'::character varying,
    updated_at timestamp without time zone DEFAULT now()
);

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

CREATE TABLE IF NOT EXISTS public.did_bridges (
    id integer NOT NULL,
    external_did character varying(256) NOT NULL,
    moltrust_did character varying(40) NOT NULL,
    chain character varying(20) NOT NULL,
    wallet_address character varying(64) NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS public.did_bridges_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.did_bridges_id_seq OWNED BY public.did_bridges.id;

CREATE TABLE IF NOT EXISTS public.erc8004_outreach (
    agent_id integer NOT NULL,
    wallet_address character varying(64),
    owner_address character varying(64),
    token_uri text,
    moltrust_registered boolean DEFAULT false,
    outreach_sent boolean DEFAULT false,
    first_seen timestamp without time zone DEFAULT now(),
    source character varying(32) DEFAULT 'erc8004'::character varying NOT NULL,
    chain character varying(32) DEFAULT 'base'::character varying NOT NULL
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

CREATE TABLE IF NOT EXISTS public.graph_edges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    from_did text NOT NULL,
    to_did text NOT NULL,
    ipr_id text,
    context text DEFAULT 'general'::text,
    outcome_score double precision,
    interaction_at timestamp with time zone DEFAULT now() NOT NULL,
    on_chain_anchor text,
    created_at timestamp with time zone DEFAULT now(),
    source text,
    CONSTRAINT graph_edges_outcome_score_check CHECK (((outcome_score >= (0.0)::double precision) AND (outcome_score <= (1.0)::double precision)))
);

COMMENT ON COLUMN public.graph_edges.outcome_score IS 'CONFIRMED=1.0, PARTIAL=0.6, INCORRECT=0.0, INCONCLUSIVE=NULL (edge not created)';

CREATE TABLE IF NOT EXISTS public.hackathon_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    api_key character varying(64) NOT NULL,
    email character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '72:00:00'::interval) NOT NULL,
    call_count integer DEFAULT 0 NOT NULL,
    last_used_at timestamp with time zone,
    active boolean DEFAULT true NOT NULL
);

CREATE TABLE IF NOT EXISTS public.known_callers (
    ip character varying(45) NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    label character varying(128),
    category character varying(32) DEFAULT 'unknown'::character varying
);

CREATE TABLE IF NOT EXISTS public.linkedin_oauth (
    id smallint DEFAULT 1 NOT NULL,
    state text,
    state_at timestamp with time zone,
    token_enc bytea,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT linkedin_oauth_id_check CHECK ((id = 1))
);

COMMENT ON TABLE public.linkedin_oauth IS 'LinkedIn OAuth: single row. token_enc is Fernet ciphertext, key derived from LINKEDIN_CLIENT_SECRET — the database never holds the clear token. See app/linkedin_oauth.py.';

CREATE TABLE IF NOT EXISTS public.payment_events (
    id integer NOT NULL,
    tx_hash character varying(66),
    from_address character varying(64),
    to_address character varying(64),
    amount_usdc numeric(18,6),
    token character varying(20),
    did character varying(100),
    received_at timestamp without time zone DEFAULT now(),
    path text
);

CREATE SEQUENCE IF NOT EXISTS public.payment_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.payment_events_id_seq OWNED BY public.payment_events.id;

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

CREATE TABLE IF NOT EXISTS public.request_log (
    id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    endpoint character varying(200) NOT NULL,
    method character varying(10) NOT NULL,
    status_code integer NOT NULL,
    ip character varying(50),
    user_agent character varying(500),
    response_ms integer,
    source character varying(20) DEFAULT 'fastapi'::character varying,
    agent_did character varying(100),
    ip_org character varying(200),
    ip_country character varying(100),
    ip_spoof_detected boolean DEFAULT false,
    caller_framework text
);

CREATE SEQUENCE IF NOT EXISTS public.request_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.request_log_id_seq OWNED BY public.request_log.id;

CREATE TABLE IF NOT EXISTS public.sas_events (
    id integer NOT NULL,
    did character varying(200),
    session_id character varying(100),
    verdict character varying(10) NOT NULL,
    residual numeric(6,3) NOT NULL,
    proposed_type character varying(50),
    proposed_resource text,
    conflict_type character varying(50),
    conflict_resource text,
    reason text,
    created_at timestamp with time zone DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS public.sas_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.sas_events_id_seq OWNED BY public.sas_events.id;

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

CREATE TABLE IF NOT EXISTS public.skill_credentials (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    skill_hash text NOT NULL,
    agent_did text NOT NULL,
    skill_name text NOT NULL,
    skill_version text NOT NULL,
    github_url text NOT NULL,
    audit_score integer NOT NULL,
    audit_findings jsonb DEFAULT '[]'::jsonb NOT NULL,
    credential jsonb NOT NULL,
    anchor_tx text,
    anchor_block text,
    issued_at timestamp with time zone DEFAULT now(),
    authorization_envelope jsonb
);

CREATE TABLE IF NOT EXISTS public.spiffe_bindings (
    id integer NOT NULL,
    spiffe_uri character varying(512) NOT NULL,
    did character varying(40) NOT NULL,
    bound_by character varying(40),
    created_at timestamp without time zone DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS public.spiffe_bindings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.spiffe_bindings_id_seq OWNED BY public.spiffe_bindings.id;

CREATE TABLE IF NOT EXISTS public.swarm_seeds (
    did text NOT NULL,
    label text,
    base_score real DEFAULT 80.0,
    registered_at timestamp with time zone DEFAULT now()
);

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

CREATE TABLE IF NOT EXISTS public.vc_challenges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    nonce character varying(64) NOT NULL,
    did character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '00:05:00'::interval) NOT NULL,
    used boolean DEFAULT false NOT NULL
);

CREATE TABLE IF NOT EXISTS public.wallet_attestations (
    did character varying(64) NOT NULL,
    wallet character varying(42) NOT NULL,
    total_usdc numeric(12,2) DEFAULT 0 NOT NULL,
    wallet_score integer DEFAULT 0 NOT NULL,
    attested_at timestamp with time zone DEFAULT now() NOT NULL
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

CREATE TABLE IF NOT EXISTS public.x402_verify_calls (
    id integer NOT NULL,
    queried_did character varying(100) NOT NULL,
    caller_ip character varying(45),
    result_payment_ready boolean,
    result_trust_score double precision,
    called_at timestamp without time zone DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS public.x402_verify_calls_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.x402_verify_calls_id_seq OWNED BY public.x402_verify_calls.id;

ALTER TABLE ONLY public.agent_delegations ALTER COLUMN id SET DEFAULT nextval('public.agent_delegations_id_seq'::regclass);

ALTER TABLE ONLY public.agent_messages ALTER COLUMN id SET DEFAULT nextval('public.agent_messages_id_seq'::regclass);

ALTER TABLE ONLY public.did_bridges ALTER COLUMN id SET DEFAULT nextval('public.did_bridges_id_seq'::regclass);

ALTER TABLE ONLY public.gate_decisions ALTER COLUMN id SET DEFAULT nextval('public.gate_decisions_id_seq'::regclass);

ALTER TABLE ONLY public.gate_measurement ALTER COLUMN id SET DEFAULT nextval('public.gate_measurement_id_seq'::regclass);

ALTER TABLE ONLY public.payment_events ALTER COLUMN id SET DEFAULT nextval('public.payment_events_id_seq'::regclass);

ALTER TABLE ONLY public.pool_spend ALTER COLUMN id SET DEFAULT nextval('public.pool_spend_id_seq'::regclass);

ALTER TABLE ONLY public.request_log ALTER COLUMN id SET DEFAULT nextval('public.request_log_id_seq'::regclass);

ALTER TABLE ONLY public.sas_events ALTER COLUMN id SET DEFAULT nextval('public.sas_events_id_seq'::regclass);

ALTER TABLE ONLY public.spiffe_bindings ALTER COLUMN id SET DEFAULT nextval('public.spiffe_bindings_id_seq'::regclass);

ALTER TABLE ONLY public.x402_verify_calls ALTER COLUMN id SET DEFAULT nextval('public.x402_verify_calls_id_seq'::regclass);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_delegations_parent_did_child_did_aae_id_key') THEN
    EXECUTE 'ALTER TABLE ONLY public.agent_delegations
    ADD CONSTRAINT agent_delegations_parent_did_child_did_aae_id_key UNIQUE (parent_did, child_did, aae_id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_delegations_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.agent_delegations
    ADD CONSTRAINT agent_delegations_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_messages_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_pkey PRIMARY KEY (id)';
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
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'api_key_labels_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.api_key_labels
    ADD CONSTRAINT api_key_labels_pkey PRIMARY KEY (api_key_prefix)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'caller_labels_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.caller_labels
    ADD CONSTRAINT caller_labels_pkey PRIMARY KEY (ip)';
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
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'did_bridges_external_did_key') THEN
    EXECUTE 'ALTER TABLE ONLY public.did_bridges
    ADD CONSTRAINT did_bridges_external_did_key UNIQUE (external_did)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'did_bridges_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.did_bridges
    ADD CONSTRAINT did_bridges_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'erc8004_outreach_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.erc8004_outreach
    ADD CONSTRAINT erc8004_outreach_pkey PRIMARY KEY (agent_id)';
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
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'graph_edges_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.graph_edges
    ADD CONSTRAINT graph_edges_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hackathon_keys_api_key_key') THEN
    EXECUTE 'ALTER TABLE ONLY public.hackathon_keys
    ADD CONSTRAINT hackathon_keys_api_key_key UNIQUE (api_key)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hackathon_keys_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.hackathon_keys
    ADD CONSTRAINT hackathon_keys_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'known_callers_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.known_callers
    ADD CONSTRAINT known_callers_pkey PRIMARY KEY (ip)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'linkedin_oauth_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.linkedin_oauth
    ADD CONSTRAINT linkedin_oauth_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'payment_events_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.payment_events
    ADD CONSTRAINT payment_events_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'payment_events_tx_hash_key') THEN
    EXECUTE 'ALTER TABLE ONLY public.payment_events
    ADD CONSTRAINT payment_events_tx_hash_key UNIQUE (tx_hash)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pool_spend_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.pool_spend
    ADD CONSTRAINT pool_spend_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'request_log_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.request_log
    ADD CONSTRAINT request_log_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sas_events_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.sas_events
    ADD CONSTRAINT sas_events_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'skill_audits_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.skill_audits
    ADD CONSTRAINT skill_audits_pkey PRIMARY KEY (skill_hash)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'skill_credentials_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.skill_credentials
    ADD CONSTRAINT skill_credentials_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'skill_credentials_skill_hash_key') THEN
    EXECUTE 'ALTER TABLE ONLY public.skill_credentials
    ADD CONSTRAINT skill_credentials_skill_hash_key UNIQUE (skill_hash)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'spiffe_bindings_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.spiffe_bindings
    ADD CONSTRAINT spiffe_bindings_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'spiffe_bindings_spiffe_uri_key') THEN
    EXECUTE 'ALTER TABLE ONLY public.spiffe_bindings
    ADD CONSTRAINT spiffe_bindings_spiffe_uri_key UNIQUE (spiffe_uri)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'swarm_seeds_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.swarm_seeds
    ADD CONSTRAINT swarm_seeds_pkey PRIMARY KEY (did)';
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
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'vc_challenges_nonce_key') THEN
    EXECUTE 'ALTER TABLE ONLY public.vc_challenges
    ADD CONSTRAINT vc_challenges_nonce_key UNIQUE (nonce)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'vc_challenges_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.vc_challenges
    ADD CONSTRAINT vc_challenges_pkey PRIMARY KEY (id)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'wallet_attestations_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.wallet_attestations
    ADD CONSTRAINT wallet_attestations_pkey PRIMARY KEY (did)';
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

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'x402_verify_calls_pkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.x402_verify_calls
    ADD CONSTRAINT x402_verify_calls_pkey PRIMARY KEY (id)';
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS agent_source_source_idx ON public.agent_source USING btree (source, recorded_at);

CREATE INDEX IF NOT EXISTS gate_measurement_measured_at_idx ON public.gate_measurement USING btree (measured_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_messages_to_did ON public.agent_messages USING btree (to_did);

CREATE INDEX IF NOT EXISTS idx_agent_profile_cluster ON public.agent_profile USING btree (country, ua_framework);

CREATE INDEX IF NOT EXISTS idx_agent_profile_enriched ON public.agent_profile USING btree (enriched_at NULLS FIRST);

CREATE INDEX IF NOT EXISTS idx_bridges_external ON public.did_bridges USING btree (external_did);

CREATE INDEX IF NOT EXISTS idx_bridges_moltrust ON public.did_bridges USING btree (moltrust_did);

CREATE INDEX IF NOT EXISTS idx_credential_anchors_tx ON public.credential_anchors USING btree (tx_hash);

CREATE INDEX IF NOT EXISTS idx_delegations_active ON public.agent_delegations USING btree (parent_did) WHERE (revoked_at IS NULL);

CREATE INDEX IF NOT EXISTS idx_delegations_child ON public.agent_delegations USING btree (child_did);

CREATE INDEX IF NOT EXISTS idx_delegations_parent ON public.agent_delegations USING btree (parent_did);

CREATE INDEX IF NOT EXISTS idx_erc8004_outreach_first_seen ON public.erc8004_outreach USING btree (first_seen DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_erc8004_outreach_source_chain ON public.erc8004_outreach USING btree (source, chain);

CREATE INDEX IF NOT EXISTS idx_gate_decisions_did ON public.gate_decisions USING btree (did) WHERE (did IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_gate_decisions_ts ON public.gate_decisions USING btree (ts DESC);

CREATE INDEX IF NOT EXISTS idx_graph_edges_context ON public.graph_edges USING btree (context);

CREATE INDEX IF NOT EXISTS idx_graph_edges_from ON public.graph_edges USING btree (from_did);

CREATE INDEX IF NOT EXISTS idx_graph_edges_interaction ON public.graph_edges USING btree (interaction_at);

CREATE INDEX IF NOT EXISTS idx_graph_edges_to ON public.graph_edges USING btree (to_did);

CREATE INDEX IF NOT EXISTS idx_hackathon_keys_expires ON public.hackathon_keys USING btree (expires_at);

CREATE INDEX IF NOT EXISTS idx_hackathon_keys_key ON public.hackathon_keys USING btree (api_key);

CREATE INDEX IF NOT EXISTS idx_payment_events_path ON public.payment_events USING btree (path);

CREATE INDEX IF NOT EXISTS idx_payment_events_time ON public.payment_events USING btree (received_at);

CREATE INDEX IF NOT EXISTS idx_pool_spend_pool_time ON public.pool_spend USING btree (pool, spent_at);

CREATE INDEX IF NOT EXISTS idx_pool_spend_state ON public.pool_spend USING btree (state);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pool_spend_tx ON public.pool_spend USING btree (lower(tx_hash)) WHERE (tx_hash IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_request_log_caller_framework ON public.request_log USING btree (caller_framework, ts DESC) WHERE (caller_framework IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_request_log_endpoint ON public.request_log USING btree (endpoint);

CREATE INDEX IF NOT EXISTS idx_request_log_ip_ts ON public.request_log USING btree (ip, ts DESC);

CREATE INDEX IF NOT EXISTS idx_request_log_source ON public.request_log USING btree (source);

CREATE INDEX IF NOT EXISTS idx_request_log_ts ON public.request_log USING btree (ts DESC);

CREATE INDEX IF NOT EXISTS idx_sas_events_did ON public.sas_events USING btree (did);

CREATE INDEX IF NOT EXISTS idx_sas_events_session ON public.sas_events USING btree (session_id);

CREATE INDEX IF NOT EXISTS idx_skill_audits_audited_at ON public.skill_audits USING btree (audited_at DESC);

CREATE INDEX IF NOT EXISTS idx_skill_cred_did ON public.skill_credentials USING btree (agent_did);

CREATE INDEX IF NOT EXISTS idx_skill_cred_hash ON public.skill_credentials USING btree (skill_hash);

CREATE INDEX IF NOT EXISTS idx_spiffe_did ON public.spiffe_bindings USING btree (did);

CREATE INDEX IF NOT EXISTS idx_spiffe_uri ON public.spiffe_bindings USING btree (spiffe_uri);

CREATE INDEX IF NOT EXISTS idx_usage_daily_class ON public.usage_daily USING btree (traffic_class, day DESC);

CREATE INDEX IF NOT EXISTS idx_usage_daily_day ON public.usage_daily USING btree (day DESC);

CREATE INDEX IF NOT EXISTS idx_usage_daily_endpoint ON public.usage_daily USING btree (endpoint_key, day DESC);

CREATE INDEX IF NOT EXISTS idx_usage_daily_keys_day ON public.usage_daily_keys USING btree (day DESC);

CREATE INDEX IF NOT EXISTS idx_usage_meter_month ON public.usage_meter USING btree (month_key);

CREATE INDEX IF NOT EXISTS idx_vc_challenges_expires ON public.vc_challenges USING btree (expires_at);

CREATE INDEX IF NOT EXISTS idx_wa_wallet ON public.wallet_attestations USING btree (wallet);

CREATE INDEX IF NOT EXISTS idx_x402_calls_did ON public.x402_verify_calls USING btree (queried_did);

CREATE INDEX IF NOT EXISTS idx_x402_calls_time ON public.x402_verify_calls USING btree (called_at);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_messages_to_did_fkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_to_did_fkey FOREIGN KEY (to_did) REFERENCES public.agents(did)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'credit_balances_did_fkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.credit_balances
    ADD CONSTRAINT credit_balances_did_fkey FOREIGN KEY (did) REFERENCES public.agents(did)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'did_bridges_moltrust_did_fkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.did_bridges
    ADD CONSTRAINT did_bridges_moltrust_did_fkey FOREIGN KEY (moltrust_did) REFERENCES public.agents(did)';
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'spiffe_bindings_did_fkey') THEN
    EXECUTE 'ALTER TABLE ONLY public.spiffe_bindings
    ADD CONSTRAINT spiffe_bindings_did_fkey FOREIGN KEY (did) REFERENCES public.agents(did)';
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
