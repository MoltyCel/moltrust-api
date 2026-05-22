-- MolTrust API — Authoritative database schema (GENERATED — do not hand-edit).
--
-- Source of truth: the live `moltstack` PostgreSQL database.
-- Regenerate with:
--   pg_dump -h localhost -U moltstack -d moltstack \
--     --schema-only --no-owner --no-privileges \
--     --exclude-table=caep_events_legacy_20260415 -f schema.sql
--
-- Fresh instance:  createdb moltstack && psql -d moltstack -v ON_ERROR_STOP=1 -f schema.sql
-- Generated 2026-05-22 — 53 tables, 2 views, 91 indexes, 17 FK constraints.

--
-- PostgreSQL database dump
--


-- Dumped from database version 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: prevent_ledger_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.prevent_ledger_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'credit_transactions is append-only';
    RETURN NULL;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_delegation_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_delegation_config (
    did character varying(40) NOT NULL,
    delegation_permitted boolean DEFAULT false,
    max_depth integer DEFAULT 0,
    constraint_mode character varying(20) DEFAULT 'none'::character varying,
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: agent_delegations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_delegations (
    id integer NOT NULL,
    parent_did character varying(40) NOT NULL,
    child_did character varying(40) NOT NULL,
    aae_id character varying(255),
    credential_type character varying(100),
    hop_depth integer DEFAULT 1 NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    revoked_at timestamp without time zone
);


--
-- Name: agent_delegations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_delegations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_delegations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_delegations_id_seq OWNED BY public.agent_delegations.id;


--
-- Name: agent_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_messages (
    id integer NOT NULL,
    to_did character varying(40) NOT NULL,
    message text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_messages_id_seq OWNED BY public.agent_messages.id;


--
-- Name: agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agents (
    did character varying(40) NOT NULL,
    display_name character varying(64) NOT NULL,
    platform character varying(32) DEFAULT 'moltbook'::character varying,
    created_at timestamp without time zone DEFAULT now(),
    base_tx_hash text,
    last_seen timestamp without time zone DEFAULT now(),
    agent_type character varying(16) DEFAULT 'external'::character varying,
    erc8004_agent_id integer,
    wallet_address character varying(64),
    public_key_hex character varying(128),
    key_anchor_tx character varying(100),
    key_anchor_block bigint,
    wallet_chain character varying(20) DEFAULT 'base'::character varying,
    wallet_bound_at timestamp without time zone,
    wallet_signature text,
    last_active_at timestamp with time zone,
    registration_ip character varying(50),
    agent_class character varying(20) DEFAULT 'autonomous'::character varying,
    agent_framework character varying(100),
    agent_version character varying(50),
    publisher character varying(255),
    agent_class_updated_at timestamp without time zone,
    revoked_at timestamp without time zone,
    revocation_reason character varying(100),
    parent_probe_did text,
    CONSTRAINT agents_agent_class_check CHECK (((agent_class)::text = ANY ((ARRAY['orchestrator'::character varying, 'autonomous'::character varying, 'human_initiated'::character varying, 'copilot'::character varying])::text[])))
);


--
-- Name: api_key_labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_key_labels (
    api_key_prefix character varying(16) NOT NULL,
    label text NOT NULL,
    color character varying(20) DEFAULT 'gray'::character varying,
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    key text NOT NULL,
    email text NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    active boolean DEFAULT true,
    rate_limit integer DEFAULT 100,
    owner_did text,
    tier text DEFAULT 'standard'::text,
    label text,
    notes text
);


--
-- Name: billing_payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.billing_payments (
    stripe_invoice_id text NOT NULL,
    stripe_customer_id text NOT NULL,
    amount_chf numeric(10,2) DEFAULT 0 NOT NULL,
    success boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: billing_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.billing_subscriptions (
    stripe_subscription_id text NOT NULL,
    stripe_customer_id text NOT NULL,
    tier text NOT NULL,
    agent_did text,
    active boolean DEFAULT true NOT NULL,
    current_period_end timestamp with time zone,
    cancel_at_period_end boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    referral_source text
);


--
-- Name: brands; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.brands (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    did text NOT NULL,
    name text NOT NULL,
    domain text,
    api_key text NOT NULL,
    contact_email text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: caep_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.caep_events (
    id bigint NOT NULL,
    event_id text DEFAULT ('evt_'::text || encode(public.gen_random_bytes(8), 'hex'::text)) NOT NULL,
    did text NOT NULL,
    event_type text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    acknowledged_at timestamp with time zone,
    CONSTRAINT caep_events_event_type_check CHECK ((event_type = ANY (ARRAY['trust_score_change'::text, 'flag_added'::text, 'flag_removed'::text, 'did_revoked'::text])))
);


--
-- Name: TABLE caep_events; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.caep_events IS 'MolTrust CAEP Profile v1 — Continuous Trust Update events';


--
-- Name: COLUMN caep_events.event_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.caep_events.event_id IS 'Public-facing UUID-style identifier (evt_<16hex>)';


--
-- Name: COLUMN caep_events.payload; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.caep_events.payload IS 'JSONB, shape depends on event_type';


--
-- Name: COLUMN caep_events.acknowledged_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.caep_events.acknowledged_at IS 'NULL = pending; soft-ack, 90d retention before hard-delete';


--
-- Name: caep_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.caep_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: caep_events_id_seq1; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.caep_events_id_seq1
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: caep_events_id_seq1; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.caep_events_id_seq1 OWNED BY public.caep_events.id;


--
-- Name: caller_labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.caller_labels (
    ip character varying(45) NOT NULL,
    label text,
    color character varying(20) DEFAULT 'gray'::character varying,
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: conversion_funnel; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversion_funnel (
    probe_did text NOT NULL,
    source text,
    first_tool text,
    tool_count integer DEFAULT 0 NOT NULL,
    unique_tools integer DEFAULT 0 NOT NULL,
    verticals_touched integer DEFAULT 0 NOT NULL,
    claim_state text DEFAULT 'unclaimed'::text NOT NULL,
    claimed_at timestamp with time zone,
    CONSTRAINT funnel_claim_state_valid CHECK ((claim_state = ANY (ARRAY['unclaimed'::text, 'claimed'::text, 'anonymous-claimed'::text, 'expired'::text])))
);


--
-- Name: credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credentials (
    id integer NOT NULL,
    subject_did character varying(40) NOT NULL,
    credential_type character varying(64) DEFAULT 'AgentTrustCredential'::character varying NOT NULL,
    issuer character varying(100) DEFAULT 'did:web:api.moltrust.ch'::character varying NOT NULL,
    issued_at timestamp without time zone DEFAULT now() NOT NULL,
    expires_at timestamp without time zone DEFAULT (now() + '1 year'::interval) NOT NULL,
    proof_value text NOT NULL,
    revoked boolean DEFAULT false NOT NULL,
    revoked_at timestamp without time zone,
    raw_vc jsonb NOT NULL,
    authorization_envelope jsonb,
    ipfs_cid character varying(64)
);


--
-- Name: credentials_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.credentials_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: credentials_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.credentials_id_seq OWNED BY public.credentials.id;


--
-- Name: credit_balances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_balances (
    did text NOT NULL,
    balance bigint DEFAULT 0 NOT NULL,
    currency text DEFAULT 'CREDITS'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT credit_balances_balance_check CHECK ((balance >= 0))
);


--
-- Name: credit_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_transactions (
    id bigint NOT NULL,
    from_did text,
    to_did text,
    amount bigint NOT NULL,
    tx_type text NOT NULL,
    reference text,
    description text,
    balance_after bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT credit_transactions_amount_check CHECK ((amount > 0))
);


--
-- Name: credit_transactions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.credit_transactions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: credit_transactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.credit_transactions_id_seq OWNED BY public.credit_transactions.id;


--
-- Name: did_bridges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.did_bridges (
    id integer NOT NULL,
    external_did character varying(256) NOT NULL,
    moltrust_did character varying(40) NOT NULL,
    chain character varying(20) NOT NULL,
    wallet_address character varying(64) NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: did_bridges_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.did_bridges_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: did_bridges_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.did_bridges_id_seq OWNED BY public.did_bridges.id;


--
-- Name: discovery_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.discovery_snapshots (
    id bigint NOT NULL,
    snapshot_at date NOT NULL,
    generated_at timestamp with time zone DEFAULT now() NOT NULL,
    payload jsonb NOT NULL,
    source_run_status text DEFAULT 'ok'::text NOT NULL,
    CONSTRAINT discovery_snapshots_source_run_status_check CHECK ((source_run_status = ANY (ARRAY['ok'::text, 'partial'::text, 'failed'::text])))
);


--
-- Name: TABLE discovery_snapshots; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.discovery_snapshots IS 'Daily Discovery-Tracking snapshots. One row per day. payload-JSONB shape per SPEC §3.5.';


--
-- Name: COLUMN discovery_snapshots.source_run_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discovery_snapshots.source_run_status IS 'ok = all 5 sources captured · partial = some sources failed (see payload.errors) · failed = none captured';


--
-- Name: discovery_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.discovery_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: discovery_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.discovery_snapshots_id_seq OWNED BY public.discovery_snapshots.id;


--
-- Name: endorsements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.endorsements (
    id integer NOT NULL,
    endorser_did text NOT NULL,
    endorsed_did text NOT NULL,
    skill text NOT NULL,
    evidence_hash text NOT NULL,
    evidence_timestamp timestamp with time zone NOT NULL,
    base_tx_hash text,
    vertical text NOT NULL,
    weight real DEFAULT 1.0 NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    vc_jwt text,
    CONSTRAINT endorsements_vertical_check CHECK ((vertical = ANY (ARRAY['skill'::text, 'shopping'::text, 'travel'::text, 'prediction'::text, 'salesguard'::text, 'sports'::text, 'core'::text])))
);


--
-- Name: endorsements_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.endorsements_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: endorsements_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.endorsements_id_seq OWNED BY public.endorsements.id;


--
-- Name: erc8004_outreach; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.erc8004_outreach (
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


--
-- Name: fantasy_lineups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fantasy_lineups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_did text NOT NULL,
    contest_id text NOT NULL,
    platform text NOT NULL,
    sport text NOT NULL,
    contest_type text,
    contest_start timestamp with time zone NOT NULL,
    entry_fee_usd double precision,
    lineup jsonb NOT NULL,
    lineup_hash text NOT NULL,
    projected_score double precision,
    confidence double precision,
    commitment_hash text NOT NULL,
    tx_hash text,
    committed_at timestamp with time zone DEFAULT now(),
    actual_score double precision,
    rank integer,
    total_entries integer,
    prize_usd double precision,
    percentile double precision,
    settled_at timestamp with time zone,
    credential jsonb
);


--
-- Name: flag_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.flag_records (
    flag_id character varying(100) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    market_id character varying(200) NOT NULL,
    market_question text,
    market_url text,
    polymarket_slug character varying(200),
    anomaly_type character varying(50),
    anomaly_score integer,
    price_at_flag numeric(10,4),
    volume_24h_usd bigint,
    volume_vs_baseline numeric(10,2),
    news_catalyst boolean DEFAULT false,
    signals jsonb,
    settlement_expected_at timestamp with time zone,
    status character varying(20) DEFAULT 'pending'::character varying,
    created_tweet_id character varying(100)
);


--
-- Name: graph_edges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.graph_edges (
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


--
-- Name: COLUMN graph_edges.outcome_score; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.graph_edges.outcome_score IS 'CONFIRMED=1.0, PARTIAL=0.6, INCORRECT=0.0, INCONCLUSIVE=NULL (edge not created)';


--
-- Name: hackathon_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hackathon_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    api_key character varying(64) NOT NULL,
    email character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '72:00:00'::interval) NOT NULL,
    call_count integer DEFAULT 0 NOT NULL,
    last_used_at timestamp with time zone,
    active boolean DEFAULT true NOT NULL
);


--
-- Name: interaction_proof_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interaction_proof_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    schema_version character varying(10) DEFAULT '1.0'::character varying NOT NULL,
    agent_did character varying(255) NOT NULL,
    output_hash character varying(100) NOT NULL,
    output_type character varying(50) DEFAULT 'generic'::character varying NOT NULL,
    source_hashes jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    confidence double precision NOT NULL,
    confidence_basis character varying(50) DEFAULT 'declared'::character varying NOT NULL,
    aae_ref character varying(100),
    agent_signature character varying(200) NOT NULL,
    produced_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    anchor_tx character varying(100),
    anchor_block bigint,
    merkle_proof jsonb,
    anchor_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    anchor_retries integer DEFAULT 0 NOT NULL,
    outcome_hash character varying(100),
    outcome_correct boolean,
    outcome_at timestamp with time zone,
    chain character varying(20) DEFAULT 'base'::character varying NOT NULL,
    CONSTRAINT interaction_proof_records_aae_ref_check CHECK (((aae_ref IS NULL) OR ((aae_ref)::text ~ '^sha256:[a-f0-9]{64}$'::text))),
    CONSTRAINT interaction_proof_records_anchor_retries_check CHECK ((anchor_retries <= 3)),
    CONSTRAINT interaction_proof_records_anchor_status_check CHECK (((anchor_status)::text = ANY ((ARRAY['pending'::character varying, 'anchored'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT interaction_proof_records_confidence_check CHECK (((confidence >= (0.0)::double precision) AND (confidence <= (1.0)::double precision))),
    CONSTRAINT interaction_proof_records_output_hash_check CHECK (((output_hash)::text ~ '^sha256:[a-f0-9]{64}$'::text))
);


--
-- Name: known_callers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.known_callers (
    ip character varying(45) NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    label character varying(128),
    category character varying(32) DEFAULT 'unknown'::character varying
);


--
-- Name: music_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.music_credentials (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    agent_did text NOT NULL,
    human_name text,
    tool text NOT NULL,
    human_oversight text NOT NULL,
    session text,
    genre text,
    rights text NOT NULL,
    isrc text,
    track_title text NOT NULL,
    track_description text,
    track_hash text NOT NULL,
    credential jsonb NOT NULL,
    issued_at timestamp with time zone DEFAULT now(),
    anchor_tx text,
    anchor_block text,
    revoked boolean DEFAULT false,
    revocation_reason text,
    CONSTRAINT music_credentials_human_oversight_check CHECK ((human_oversight = ANY (ARRAY['true'::text, 'false'::text, 'partial'::text])))
);


--
-- Name: outcome_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outcome_records (
    flag_id character varying(100) NOT NULL,
    settled_at timestamp with time zone,
    settlement_outcome character varying(10),
    price_at_settlement numeric(10,4),
    price_movement_pct numeric(10,2),
    volume_post_flag_24h bigint,
    verdict character varying(20),
    flag_score_contribution numeric(5,2),
    on_chain_anchor character varying(200),
    outcome_tweet_id character varying(100),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: outreach_sent; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outreach_sent (
    wallet_address character varying(64) NOT NULL,
    channel character varying(20) DEFAULT 'xmtp'::character varying,
    sent_at timestamp without time zone DEFAULT now(),
    xmtp_capable boolean,
    message_id character varying(100)
);


--
-- Name: payment_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payment_events (
    id integer NOT NULL,
    tx_hash character varying(66),
    from_address character varying(64),
    to_address character varying(64),
    amount_usdc numeric(18,6),
    token character varying(20),
    did character varying(100),
    received_at timestamp without time zone DEFAULT now()
);


--
-- Name: payment_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.payment_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: payment_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.payment_events_id_seq OWNED BY public.payment_events.id;


--
-- Name: prediction_market_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prediction_market_events (
    id integer NOT NULL,
    wallet_address text NOT NULL,
    market_id text NOT NULL,
    market_question text,
    platform text DEFAULT 'polymarket'::text NOT NULL,
    outcome text,
    amount_in numeric(18,6),
    amount_out numeric(18,6),
    "position" text,
    event_timestamp timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: prediction_market_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.prediction_market_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: prediction_market_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.prediction_market_events_id_seq OWNED BY public.prediction_market_events.id;


--
-- Name: prediction_wallets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prediction_wallets (
    id integer NOT NULL,
    address text NOT NULL,
    platform text DEFAULT 'polymarket'::text NOT NULL,
    linked_did text,
    linked_at timestamp with time zone,
    total_bets integer DEFAULT 0 NOT NULL,
    wins integer DEFAULT 0 NOT NULL,
    losses integer DEFAULT 0 NOT NULL,
    total_volume numeric(18,6) DEFAULT 0 NOT NULL,
    net_pnl numeric(18,6) DEFAULT 0 NOT NULL,
    prediction_score integer DEFAULT 0 NOT NULL,
    score_breakdown jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_synced timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: prediction_wallets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.prediction_wallets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: prediction_wallets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.prediction_wallets_id_seq OWNED BY public.prediction_wallets.id;


--
-- Name: probe_activity; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.probe_activity (
    id bigint NOT NULL,
    probe_did text NOT NULL,
    tool_name text NOT NULL,
    args_redacted jsonb,
    result_summary text,
    at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: probe_activity_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.probe_activity_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: probe_activity_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.probe_activity_id_seq OWNED BY public.probe_activity.id;


--
-- Name: probe_agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.probe_agents (
    did text NOT NULL,
    probe_key_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    call_count integer DEFAULT 0 NOT NULL,
    call_cap integer DEFAULT 50 NOT NULL,
    ttl_extensions integer DEFAULT 0 NOT NULL,
    first_seen_ip inet,
    first_seen_ua text,
    smithery_session_hash text,
    claimed_at timestamp with time zone,
    claimed_did text,
    claimed_email_hash text,
    CONSTRAINT probe_call_cap_positive CHECK ((call_cap > 0)),
    CONSTRAINT probe_did_format CHECK ((did ~ '^did:moltrust:probe:[0-9a-f]{8}$'::text)),
    CONSTRAINT probe_ttl_extensions_bounded CHECK (((ttl_extensions >= 0) AND (ttl_extensions <= 2)))
);


--
-- Name: products; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.products (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    brand_id uuid NOT NULL,
    product_id text NOT NULL,
    name text NOT NULL,
    credential_hash text,
    base_anchor text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: ratings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ratings (
    id integer NOT NULL,
    from_did character varying(40) NOT NULL,
    to_did character varying(40) NOT NULL,
    score smallint,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT ratings_score_check CHECK (((score >= 1) AND (score <= 5)))
);


--
-- Name: ratings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ratings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ratings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ratings_id_seq OWNED BY public.ratings.id;


--
-- Name: request_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_log (
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
    ip_spoof_detected boolean DEFAULT false
);


--
-- Name: request_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.request_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: request_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.request_log_id_seq OWNED BY public.request_log.id;


--
-- Name: resellers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resellers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    brand_id uuid NOT NULL,
    reseller_did text NOT NULL,
    reseller_name text NOT NULL,
    authorized_skus text[] DEFAULT '{}'::text[],
    credential_hash text,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: sas_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sas_events (
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


--
-- Name: sas_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sas_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sas_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sas_events_id_seq OWNED BY public.sas_events.id;


--
-- Name: signal_providers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_providers (
    id integer NOT NULL,
    provider_id character varying(11) NOT NULL,
    agent_did character varying(40) NOT NULL,
    provider_name character varying(128) NOT NULL,
    provider_url character varying(512),
    sport_focus jsonb DEFAULT '[]'::jsonb NOT NULL,
    description text,
    credential_hash character varying(64),
    credential_tx_hash text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: signal_providers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.signal_providers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: signal_providers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.signal_providers_id_seq OWNED BY public.signal_providers.id;


--
-- Name: skill_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.skill_credentials (
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


--
-- Name: skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.skills (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    author_did character varying(40),
    description text,
    security_score smallint DEFAULT 0,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT skills_security_score_check CHECK (((security_score >= 0) AND (security_score <= 100)))
);


--
-- Name: skills_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.skills_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: skills_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.skills_id_seq OWNED BY public.skills.id;


--
-- Name: spiffe_bindings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.spiffe_bindings (
    id integer NOT NULL,
    spiffe_uri character varying(512) NOT NULL,
    did character varying(40) NOT NULL,
    bound_by character varying(40),
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: spiffe_bindings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.spiffe_bindings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: spiffe_bindings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.spiffe_bindings_id_seq OWNED BY public.spiffe_bindings.id;


--
-- Name: sports_predictions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sports_predictions (
    id integer NOT NULL,
    agent_did character varying(40) NOT NULL,
    event_id character varying(256) NOT NULL,
    prediction jsonb NOT NULL,
    event_start timestamp with time zone NOT NULL,
    commitment_hash character varying(64) NOT NULL,
    base_tx_hash text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    outcome jsonb,
    correct boolean,
    settled_at timestamp with time zone
);


--
-- Name: sports_predictions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sports_predictions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sports_predictions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sports_predictions_id_seq OWNED BY public.sports_predictions.id;


--
-- Name: swarm_graph; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.swarm_graph (
    id integer NOT NULL,
    from_did text NOT NULL,
    to_did text NOT NULL,
    edge_weight real DEFAULT 1.0,
    propagation_depth integer DEFAULT 1,
    computed_at timestamp with time zone DEFAULT now()
);


--
-- Name: swarm_graph_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.swarm_graph_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: swarm_graph_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.swarm_graph_id_seq OWNED BY public.swarm_graph.id;


--
-- Name: swarm_seeds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.swarm_seeds (
    did text NOT NULL,
    label text,
    base_score real DEFAULT 80.0,
    registered_at timestamp with time zone DEFAULT now()
);


--
-- Name: trust_score_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_score_cache (
    did text NOT NULL,
    score real NOT NULL,
    endorser_count integer NOT NULL,
    sybil_penalty real DEFAULT 0.0 NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    cache_valid_until timestamp with time zone NOT NULL,
    propagated_score real,
    cross_vertical_bonus real DEFAULT 0,
    computation_method text DEFAULT 'phase1'::text
);


--
-- Name: usdc_deposits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usdc_deposits (
    id bigint NOT NULL,
    tx_hash text NOT NULL,
    from_address text NOT NULL,
    to_did text NOT NULL,
    usdc_amount numeric(20,6) NOT NULL,
    credits_granted bigint NOT NULL,
    block_number bigint,
    claimed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: usdc_deposits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.usdc_deposits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: usdc_deposits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.usdc_deposits_id_seq OWNED BY public.usdc_deposits.id;


--
-- Name: v_explorer_agents; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_explorer_agents AS
 SELECT o.agent_id AS external_agent_id,
    o.wallet_address,
    o.chain,
    o.source,
    o.first_seen AS external_registered_at,
    o.token_uri AS metadata_uri,
    a.did AS moltrust_did,
    a.created_at AS moltrust_registered_at,
        CASE
            WHEN (a.did IS NOT NULL) THEN 'moltrust_verified'::text
            WHEN (o.outreach_sent = true) THEN 'contacted_not_verified'::text
            ELSE 'indexed_only'::text
        END AS verification_status,
    tc.score AS moltrust_trust_score
   FROM ((public.erc8004_outreach o
     LEFT JOIN public.agents a ON ((a.erc8004_agent_id = o.agent_id)))
     LEFT JOIN public.trust_score_cache tc ON ((tc.did = (a.did)::text)));


--
-- Name: v_explorer_stats; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_explorer_stats AS
 SELECT source,
    chain,
    count(*) AS total_indexed,
    count(*) FILTER (WHERE (verification_status = 'moltrust_verified'::text)) AS moltrust_verified,
    count(*) FILTER (WHERE (verification_status = 'contacted_not_verified'::text)) AS contacted,
    count(*) FILTER (WHERE (verification_status = 'indexed_only'::text)) AS indexed_only
   FROM public.v_explorer_agents
  GROUP BY source, chain;


--
-- Name: vc_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vc_challenges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    nonce character varying(64) NOT NULL,
    did character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '00:05:00'::interval) NOT NULL,
    used boolean DEFAULT false NOT NULL
);


--
-- Name: verified_badges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verified_badges (
    id integer NOT NULL,
    did text NOT NULL,
    tier text DEFAULT 'verified'::text NOT NULL,
    trust_score_at_issuance real NOT NULL,
    issued_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone,
    revoked_at timestamp without time zone,
    revocation_reason text,
    payment_tx text,
    vc_hash text,
    active boolean DEFAULT true
);


--
-- Name: verified_badges_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.verified_badges_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: verified_badges_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.verified_badges_id_seq OWNED BY public.verified_badges.id;


--
-- Name: violation_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.violation_records (
    id text NOT NULL,
    agent_did text NOT NULL,
    principal_did text NOT NULL,
    violation_type text NOT NULL,
    interaction_proof_id text,
    description text,
    adjudicator_type text DEFAULT 'external'::text,
    adjudicator_reference text,
    confirmed_at text NOT NULL,
    reversed boolean DEFAULT false,
    reversal_date text,
    reversal_reference text,
    created_at text DEFAULT (now())::text
);


--
-- Name: wallet_attestations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wallet_attestations (
    did character varying(64) NOT NULL,
    wallet character varying(42) NOT NULL,
    total_usdc numeric(12,2) DEFAULT 0 NOT NULL,
    wallet_score integer DEFAULT 0 NOT NULL,
    attested_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: webhook_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.webhook_events (
    id integer NOT NULL,
    event character varying(50) NOT NULL,
    agent_id character varying(200) NOT NULL,
    payload jsonb,
    received_at timestamp with time zone DEFAULT now()
);


--
-- Name: webhook_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.webhook_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: webhook_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.webhook_events_id_seq OWNED BY public.webhook_events.id;


--
-- Name: x402_verify_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.x402_verify_calls (
    id integer NOT NULL,
    queried_did character varying(100) NOT NULL,
    caller_ip character varying(45),
    result_payment_ready boolean,
    result_trust_score double precision,
    called_at timestamp without time zone DEFAULT now()
);


--
-- Name: x402_verify_calls_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.x402_verify_calls_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: x402_verify_calls_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.x402_verify_calls_id_seq OWNED BY public.x402_verify_calls.id;


--
-- Name: agent_delegations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_delegations ALTER COLUMN id SET DEFAULT nextval('public.agent_delegations_id_seq'::regclass);


--
-- Name: agent_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_messages ALTER COLUMN id SET DEFAULT nextval('public.agent_messages_id_seq'::regclass);


--
-- Name: caep_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caep_events ALTER COLUMN id SET DEFAULT nextval('public.caep_events_id_seq1'::regclass);


--
-- Name: credentials id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credentials ALTER COLUMN id SET DEFAULT nextval('public.credentials_id_seq'::regclass);


--
-- Name: credit_transactions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_transactions ALTER COLUMN id SET DEFAULT nextval('public.credit_transactions_id_seq'::regclass);


--
-- Name: did_bridges id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.did_bridges ALTER COLUMN id SET DEFAULT nextval('public.did_bridges_id_seq'::regclass);


--
-- Name: discovery_snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.discovery_snapshots ALTER COLUMN id SET DEFAULT nextval('public.discovery_snapshots_id_seq'::regclass);


--
-- Name: endorsements id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.endorsements ALTER COLUMN id SET DEFAULT nextval('public.endorsements_id_seq'::regclass);


--
-- Name: payment_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_events ALTER COLUMN id SET DEFAULT nextval('public.payment_events_id_seq'::regclass);


--
-- Name: prediction_market_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prediction_market_events ALTER COLUMN id SET DEFAULT nextval('public.prediction_market_events_id_seq'::regclass);


--
-- Name: prediction_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prediction_wallets ALTER COLUMN id SET DEFAULT nextval('public.prediction_wallets_id_seq'::regclass);


--
-- Name: probe_activity id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.probe_activity ALTER COLUMN id SET DEFAULT nextval('public.probe_activity_id_seq'::regclass);


--
-- Name: ratings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ratings ALTER COLUMN id SET DEFAULT nextval('public.ratings_id_seq'::regclass);


--
-- Name: request_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_log ALTER COLUMN id SET DEFAULT nextval('public.request_log_id_seq'::regclass);


--
-- Name: sas_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sas_events ALTER COLUMN id SET DEFAULT nextval('public.sas_events_id_seq'::regclass);


--
-- Name: signal_providers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_providers ALTER COLUMN id SET DEFAULT nextval('public.signal_providers_id_seq'::regclass);


--
-- Name: skills id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills ALTER COLUMN id SET DEFAULT nextval('public.skills_id_seq'::regclass);


--
-- Name: spiffe_bindings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.spiffe_bindings ALTER COLUMN id SET DEFAULT nextval('public.spiffe_bindings_id_seq'::regclass);


--
-- Name: sports_predictions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sports_predictions ALTER COLUMN id SET DEFAULT nextval('public.sports_predictions_id_seq'::regclass);


--
-- Name: swarm_graph id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.swarm_graph ALTER COLUMN id SET DEFAULT nextval('public.swarm_graph_id_seq'::regclass);


--
-- Name: usdc_deposits id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usdc_deposits ALTER COLUMN id SET DEFAULT nextval('public.usdc_deposits_id_seq'::regclass);


--
-- Name: verified_badges id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verified_badges ALTER COLUMN id SET DEFAULT nextval('public.verified_badges_id_seq'::regclass);


--
-- Name: webhook_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_events ALTER COLUMN id SET DEFAULT nextval('public.webhook_events_id_seq'::regclass);


--
-- Name: x402_verify_calls id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.x402_verify_calls ALTER COLUMN id SET DEFAULT nextval('public.x402_verify_calls_id_seq'::regclass);


--
-- Name: agent_delegation_config agent_delegation_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_delegation_config
    ADD CONSTRAINT agent_delegation_config_pkey PRIMARY KEY (did);


--
-- Name: agent_delegations agent_delegations_parent_did_child_did_aae_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_delegations
    ADD CONSTRAINT agent_delegations_parent_did_child_did_aae_id_key UNIQUE (parent_did, child_did, aae_id);


--
-- Name: agent_delegations agent_delegations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_delegations
    ADD CONSTRAINT agent_delegations_pkey PRIMARY KEY (id);


--
-- Name: agent_messages agent_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_pkey PRIMARY KEY (id);


--
-- Name: agents agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (did);


--
-- Name: api_key_labels api_key_labels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key_labels
    ADD CONSTRAINT api_key_labels_pkey PRIMARY KEY (api_key_prefix);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (key);


--
-- Name: billing_payments billing_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_payments
    ADD CONSTRAINT billing_payments_pkey PRIMARY KEY (stripe_invoice_id);


--
-- Name: billing_subscriptions billing_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_subscriptions
    ADD CONSTRAINT billing_subscriptions_pkey PRIMARY KEY (stripe_subscription_id);


--
-- Name: brands brands_did_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brands
    ADD CONSTRAINT brands_did_key UNIQUE (did);


--
-- Name: brands brands_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brands
    ADD CONSTRAINT brands_pkey PRIMARY KEY (id);


--
-- Name: caep_events caep_events_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caep_events
    ADD CONSTRAINT caep_events_event_id_key UNIQUE (event_id);


--
-- Name: caep_events caep_events_pkey1; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caep_events
    ADD CONSTRAINT caep_events_pkey1 PRIMARY KEY (id);


--
-- Name: caller_labels caller_labels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caller_labels
    ADD CONSTRAINT caller_labels_pkey PRIMARY KEY (ip);


--
-- Name: conversion_funnel conversion_funnel_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversion_funnel
    ADD CONSTRAINT conversion_funnel_pkey PRIMARY KEY (probe_did);


--
-- Name: credentials credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credentials
    ADD CONSTRAINT credentials_pkey PRIMARY KEY (id);


--
-- Name: credit_balances credit_balances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_balances
    ADD CONSTRAINT credit_balances_pkey PRIMARY KEY (did);


--
-- Name: credit_transactions credit_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_transactions
    ADD CONSTRAINT credit_transactions_pkey PRIMARY KEY (id);


--
-- Name: did_bridges did_bridges_external_did_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.did_bridges
    ADD CONSTRAINT did_bridges_external_did_key UNIQUE (external_did);


--
-- Name: did_bridges did_bridges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.did_bridges
    ADD CONSTRAINT did_bridges_pkey PRIMARY KEY (id);


--
-- Name: discovery_snapshots discovery_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.discovery_snapshots
    ADD CONSTRAINT discovery_snapshots_pkey PRIMARY KEY (id);


--
-- Name: discovery_snapshots discovery_snapshots_snapshot_at_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.discovery_snapshots
    ADD CONSTRAINT discovery_snapshots_snapshot_at_key UNIQUE (snapshot_at);


--
-- Name: endorsements endorsements_endorser_did_endorsed_did_evidence_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.endorsements
    ADD CONSTRAINT endorsements_endorser_did_endorsed_did_evidence_hash_key UNIQUE (endorser_did, endorsed_did, evidence_hash);


--
-- Name: endorsements endorsements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.endorsements
    ADD CONSTRAINT endorsements_pkey PRIMARY KEY (id);


--
-- Name: erc8004_outreach erc8004_outreach_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.erc8004_outreach
    ADD CONSTRAINT erc8004_outreach_pkey PRIMARY KEY (agent_id);


--
-- Name: fantasy_lineups fantasy_lineups_agent_did_contest_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fantasy_lineups
    ADD CONSTRAINT fantasy_lineups_agent_did_contest_id_key UNIQUE (agent_did, contest_id);


--
-- Name: fantasy_lineups fantasy_lineups_commitment_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fantasy_lineups
    ADD CONSTRAINT fantasy_lineups_commitment_hash_key UNIQUE (commitment_hash);


--
-- Name: fantasy_lineups fantasy_lineups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fantasy_lineups
    ADD CONSTRAINT fantasy_lineups_pkey PRIMARY KEY (id);


--
-- Name: flag_records flag_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flag_records
    ADD CONSTRAINT flag_records_pkey PRIMARY KEY (flag_id);


--
-- Name: graph_edges graph_edges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_edges
    ADD CONSTRAINT graph_edges_pkey PRIMARY KEY (id);


--
-- Name: hackathon_keys hackathon_keys_api_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hackathon_keys
    ADD CONSTRAINT hackathon_keys_api_key_key UNIQUE (api_key);


--
-- Name: hackathon_keys hackathon_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hackathon_keys
    ADD CONSTRAINT hackathon_keys_pkey PRIMARY KEY (id);


--
-- Name: interaction_proof_records interaction_proof_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interaction_proof_records
    ADD CONSTRAINT interaction_proof_records_pkey PRIMARY KEY (id);


--
-- Name: known_callers known_callers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.known_callers
    ADD CONSTRAINT known_callers_pkey PRIMARY KEY (ip);


--
-- Name: music_credentials music_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.music_credentials
    ADD CONSTRAINT music_credentials_pkey PRIMARY KEY (id);


--
-- Name: outcome_records outcome_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcome_records
    ADD CONSTRAINT outcome_records_pkey PRIMARY KEY (flag_id);


--
-- Name: outreach_sent outreach_sent_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outreach_sent
    ADD CONSTRAINT outreach_sent_pkey PRIMARY KEY (wallet_address);


--
-- Name: payment_events payment_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_events
    ADD CONSTRAINT payment_events_pkey PRIMARY KEY (id);


--
-- Name: payment_events payment_events_tx_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_events
    ADD CONSTRAINT payment_events_tx_hash_key UNIQUE (tx_hash);


--
-- Name: prediction_market_events prediction_market_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prediction_market_events
    ADD CONSTRAINT prediction_market_events_pkey PRIMARY KEY (id);


--
-- Name: prediction_wallets prediction_wallets_address_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prediction_wallets
    ADD CONSTRAINT prediction_wallets_address_key UNIQUE (address);


--
-- Name: prediction_wallets prediction_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prediction_wallets
    ADD CONSTRAINT prediction_wallets_pkey PRIMARY KEY (id);


--
-- Name: probe_activity probe_activity_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.probe_activity
    ADD CONSTRAINT probe_activity_pkey PRIMARY KEY (id);


--
-- Name: probe_agents probe_agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.probe_agents
    ADD CONSTRAINT probe_agents_pkey PRIMARY KEY (did);


--
-- Name: probe_agents probe_agents_probe_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.probe_agents
    ADD CONSTRAINT probe_agents_probe_key_hash_key UNIQUE (probe_key_hash);


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (id);


--
-- Name: products products_product_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_product_id_key UNIQUE (product_id);


--
-- Name: ratings ratings_from_did_to_did_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ratings
    ADD CONSTRAINT ratings_from_did_to_did_key UNIQUE (from_did, to_did);


--
-- Name: ratings ratings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ratings
    ADD CONSTRAINT ratings_pkey PRIMARY KEY (id);


--
-- Name: request_log request_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_log
    ADD CONSTRAINT request_log_pkey PRIMARY KEY (id);


--
-- Name: resellers resellers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resellers
    ADD CONSTRAINT resellers_pkey PRIMARY KEY (id);


--
-- Name: sas_events sas_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sas_events
    ADD CONSTRAINT sas_events_pkey PRIMARY KEY (id);


--
-- Name: signal_providers signal_providers_agent_did_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_providers
    ADD CONSTRAINT signal_providers_agent_did_key UNIQUE (agent_did);


--
-- Name: signal_providers signal_providers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_providers
    ADD CONSTRAINT signal_providers_pkey PRIMARY KEY (id);


--
-- Name: signal_providers signal_providers_provider_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_providers
    ADD CONSTRAINT signal_providers_provider_id_key UNIQUE (provider_id);


--
-- Name: skill_credentials skill_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_credentials
    ADD CONSTRAINT skill_credentials_pkey PRIMARY KEY (id);


--
-- Name: skill_credentials skill_credentials_skill_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_credentials
    ADD CONSTRAINT skill_credentials_skill_hash_key UNIQUE (skill_hash);


--
-- Name: skills skills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_pkey PRIMARY KEY (id);


--
-- Name: spiffe_bindings spiffe_bindings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.spiffe_bindings
    ADD CONSTRAINT spiffe_bindings_pkey PRIMARY KEY (id);


--
-- Name: spiffe_bindings spiffe_bindings_spiffe_uri_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.spiffe_bindings
    ADD CONSTRAINT spiffe_bindings_spiffe_uri_key UNIQUE (spiffe_uri);


--
-- Name: sports_predictions sports_predictions_agent_did_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sports_predictions
    ADD CONSTRAINT sports_predictions_agent_did_event_id_key UNIQUE (agent_did, event_id);


--
-- Name: sports_predictions sports_predictions_commitment_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sports_predictions
    ADD CONSTRAINT sports_predictions_commitment_hash_key UNIQUE (commitment_hash);


--
-- Name: sports_predictions sports_predictions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sports_predictions
    ADD CONSTRAINT sports_predictions_pkey PRIMARY KEY (id);


--
-- Name: swarm_graph swarm_graph_from_did_to_did_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.swarm_graph
    ADD CONSTRAINT swarm_graph_from_did_to_did_key UNIQUE (from_did, to_did);


--
-- Name: swarm_graph swarm_graph_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.swarm_graph
    ADD CONSTRAINT swarm_graph_pkey PRIMARY KEY (id);


--
-- Name: swarm_seeds swarm_seeds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.swarm_seeds
    ADD CONSTRAINT swarm_seeds_pkey PRIMARY KEY (did);


--
-- Name: trust_score_cache trust_score_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_score_cache
    ADD CONSTRAINT trust_score_cache_pkey PRIMARY KEY (did);


--
-- Name: usdc_deposits usdc_deposits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usdc_deposits
    ADD CONSTRAINT usdc_deposits_pkey PRIMARY KEY (id);


--
-- Name: usdc_deposits usdc_deposits_tx_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usdc_deposits
    ADD CONSTRAINT usdc_deposits_tx_hash_key UNIQUE (tx_hash);


--
-- Name: vc_challenges vc_challenges_nonce_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vc_challenges
    ADD CONSTRAINT vc_challenges_nonce_key UNIQUE (nonce);


--
-- Name: vc_challenges vc_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vc_challenges
    ADD CONSTRAINT vc_challenges_pkey PRIMARY KEY (id);


--
-- Name: verified_badges verified_badges_did_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verified_badges
    ADD CONSTRAINT verified_badges_did_key UNIQUE (did);


--
-- Name: verified_badges verified_badges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verified_badges
    ADD CONSTRAINT verified_badges_pkey PRIMARY KEY (id);


--
-- Name: violation_records violation_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.violation_records
    ADD CONSTRAINT violation_records_pkey PRIMARY KEY (id);


--
-- Name: wallet_attestations wallet_attestations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_attestations
    ADD CONSTRAINT wallet_attestations_pkey PRIMARY KEY (did);


--
-- Name: webhook_events webhook_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_events
    ADD CONSTRAINT webhook_events_pkey PRIMARY KEY (id);


--
-- Name: x402_verify_calls x402_verify_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.x402_verify_calls
    ADD CONSTRAINT x402_verify_calls_pkey PRIMARY KEY (id);


--
-- Name: idx_agent_messages_to_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_messages_to_did ON public.agent_messages USING btree (to_did);


--
-- Name: idx_agents_last_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agents_last_active ON public.agents USING btree (last_active_at) WHERE (last_active_at IS NOT NULL);


--
-- Name: idx_agents_last_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agents_last_seen ON public.agents USING btree (last_seen);


--
-- Name: idx_agents_parent_probe; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agents_parent_probe ON public.agents USING btree (parent_probe_did) WHERE (parent_probe_did IS NOT NULL);


--
-- Name: idx_agents_reg_ip; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agents_reg_ip ON public.agents USING btree (registration_ip) WHERE (registration_ip IS NOT NULL);


--
-- Name: idx_agents_wallet; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_agents_wallet ON public.agents USING btree (wallet_address) WHERE (wallet_address IS NOT NULL);


--
-- Name: idx_api_keys_owner_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_owner_did ON public.api_keys USING btree (owner_did);


--
-- Name: idx_billing_pay_customer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_billing_pay_customer ON public.billing_payments USING btree (stripe_customer_id);


--
-- Name: idx_billing_sub_customer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_billing_sub_customer ON public.billing_subscriptions USING btree (stripe_customer_id);


--
-- Name: idx_billing_sub_referral; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_billing_sub_referral ON public.billing_subscriptions USING btree (referral_source) WHERE (referral_source IS NOT NULL);


--
-- Name: idx_brands_api_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_brands_api_key ON public.brands USING btree (api_key);


--
-- Name: idx_brands_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_brands_did ON public.brands USING btree (did);


--
-- Name: idx_bridges_external; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bridges_external ON public.did_bridges USING btree (external_did);


--
-- Name: idx_bridges_moltrust; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bridges_moltrust ON public.did_bridges USING btree (moltrust_did);


--
-- Name: idx_caep_events_cleanup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_caep_events_cleanup ON public.caep_events USING btree (acknowledged_at) WHERE (acknowledged_at IS NOT NULL);


--
-- Name: idx_caep_events_did_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_caep_events_did_pending ON public.caep_events USING btree (did, created_at) WHERE (acknowledged_at IS NULL);


--
-- Name: idx_caep_events_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_caep_events_event_id ON public.caep_events USING btree (event_id);


--
-- Name: idx_credentials_revoked; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credentials_revoked ON public.credentials USING btree (revoked) WHERE (revoked = true);


--
-- Name: idx_credentials_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credentials_subject ON public.credentials USING btree (subject_did);


--
-- Name: idx_credentials_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credentials_type ON public.credentials USING btree (credential_type);


--
-- Name: idx_credit_tx_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_tx_from ON public.credit_transactions USING btree (from_did, created_at DESC);


--
-- Name: idx_credit_tx_to; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_tx_to ON public.credit_transactions USING btree (to_did, created_at DESC);


--
-- Name: idx_credit_tx_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_tx_type ON public.credit_transactions USING btree (tx_type);


--
-- Name: idx_delegations_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_delegations_active ON public.agent_delegations USING btree (parent_did) WHERE (revoked_at IS NULL);


--
-- Name: idx_delegations_child; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_delegations_child ON public.agent_delegations USING btree (child_did);


--
-- Name: idx_delegations_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_delegations_parent ON public.agent_delegations USING btree (parent_did);


--
-- Name: idx_discovery_snapshots_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_discovery_snapshots_at ON public.discovery_snapshots USING btree (snapshot_at DESC);


--
-- Name: idx_endorsements_endorsed_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_endorsements_endorsed_did ON public.endorsements USING btree (endorsed_did);


--
-- Name: idx_endorsements_endorser_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_endorsements_endorser_did ON public.endorsements USING btree (endorser_did);


--
-- Name: idx_endorsements_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_endorsements_expires_at ON public.endorsements USING btree (expires_at);


--
-- Name: idx_erc8004_outreach_first_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_erc8004_outreach_first_seen ON public.erc8004_outreach USING btree (first_seen DESC NULLS LAST);


--
-- Name: idx_erc8004_outreach_source_chain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_erc8004_outreach_source_chain ON public.erc8004_outreach USING btree (source, chain);


--
-- Name: idx_fl_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fl_agent ON public.fantasy_lineups USING btree (agent_did);


--
-- Name: idx_fl_contest; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fl_contest ON public.fantasy_lineups USING btree (contest_id);


--
-- Name: idx_fl_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fl_hash ON public.fantasy_lineups USING btree (commitment_hash);


--
-- Name: idx_flag_records_market; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_flag_records_market ON public.flag_records USING btree (market_id);


--
-- Name: idx_flag_records_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_flag_records_status ON public.flag_records USING btree (status);


--
-- Name: idx_funnel_claimed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_funnel_claimed_at ON public.conversion_funnel USING btree (claimed_at) WHERE (claimed_at IS NOT NULL);


--
-- Name: idx_funnel_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_funnel_source ON public.conversion_funnel USING btree (source);


--
-- Name: idx_funnel_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_funnel_state ON public.conversion_funnel USING btree (claim_state);


--
-- Name: idx_graph_edges_context; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_graph_edges_context ON public.graph_edges USING btree (context);


--
-- Name: idx_graph_edges_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_graph_edges_from ON public.graph_edges USING btree (from_did);


--
-- Name: idx_graph_edges_interaction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_graph_edges_interaction ON public.graph_edges USING btree (interaction_at);


--
-- Name: idx_graph_edges_to; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_graph_edges_to ON public.graph_edges USING btree (to_did);


--
-- Name: idx_hackathon_keys_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hackathon_keys_expires ON public.hackathon_keys USING btree (expires_at);


--
-- Name: idx_hackathon_keys_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hackathon_keys_key ON public.hackathon_keys USING btree (api_key);


--
-- Name: idx_ipr_agent_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ipr_agent_did ON public.interaction_proof_records USING btree (agent_did);


--
-- Name: idx_ipr_agent_output; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_ipr_agent_output ON public.interaction_proof_records USING btree (agent_did, output_hash);


--
-- Name: idx_ipr_failed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ipr_failed ON public.interaction_proof_records USING btree (anchor_status) WHERE ((anchor_status)::text = 'failed'::text);


--
-- Name: idx_ipr_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ipr_pending ON public.interaction_proof_records USING btree (anchor_status, created_at) WHERE ((anchor_status)::text = 'pending'::text);


--
-- Name: idx_music_cred_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_music_cred_did ON public.music_credentials USING btree (agent_did);


--
-- Name: idx_music_cred_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_music_cred_hash ON public.music_credentials USING btree (track_hash);


--
-- Name: idx_payment_events_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_events_time ON public.payment_events USING btree (received_at);


--
-- Name: idx_pme_market; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pme_market ON public.prediction_market_events USING btree (market_id);


--
-- Name: idx_pme_wallet; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pme_wallet ON public.prediction_market_events USING btree (wallet_address);


--
-- Name: idx_probe_act_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_probe_act_did ON public.probe_activity USING btree (probe_did, at DESC);


--
-- Name: idx_probe_act_tool; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_probe_act_tool ON public.probe_activity USING btree (tool_name, at DESC);


--
-- Name: idx_probe_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_probe_active ON public.probe_agents USING btree (expires_at) WHERE (claimed_at IS NULL);


--
-- Name: idx_probe_ip_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_probe_ip_recent ON public.probe_agents USING btree (first_seen_ip, created_at);


--
-- Name: idx_probe_smithery_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_probe_smithery_session ON public.probe_agents USING btree (smithery_session_hash) WHERE (smithery_session_hash IS NOT NULL);


--
-- Name: idx_products_brand_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_brand_id ON public.products USING btree (brand_id);


--
-- Name: idx_products_product_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_product_id ON public.products USING btree (product_id);


--
-- Name: idx_pw_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pw_did ON public.prediction_wallets USING btree (linked_did) WHERE (linked_did IS NOT NULL);


--
-- Name: idx_pw_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pw_score ON public.prediction_wallets USING btree (prediction_score DESC);


--
-- Name: idx_ratings_to_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ratings_to_did ON public.ratings USING btree (to_did);


--
-- Name: idx_request_log_endpoint; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_log_endpoint ON public.request_log USING btree (endpoint);


--
-- Name: idx_request_log_ip_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_log_ip_ts ON public.request_log USING btree (ip, ts DESC);


--
-- Name: idx_request_log_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_log_source ON public.request_log USING btree (source);


--
-- Name: idx_request_log_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_log_ts ON public.request_log USING btree (ts DESC);


--
-- Name: idx_resellers_brand_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resellers_brand_id ON public.resellers USING btree (brand_id);


--
-- Name: idx_resellers_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resellers_did ON public.resellers USING btree (reseller_did);


--
-- Name: idx_sas_events_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sas_events_did ON public.sas_events USING btree (did);


--
-- Name: idx_sas_events_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sas_events_session ON public.sas_events USING btree (session_id);


--
-- Name: idx_sigprov_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sigprov_did ON public.signal_providers USING btree (agent_did);


--
-- Name: idx_skill_cred_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_skill_cred_did ON public.skill_credentials USING btree (agent_did);


--
-- Name: idx_skill_cred_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_skill_cred_hash ON public.skill_credentials USING btree (skill_hash);


--
-- Name: idx_skills_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_skills_score ON public.skills USING btree (security_score DESC);


--
-- Name: idx_sp_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sp_agent ON public.sports_predictions USING btree (agent_did);


--
-- Name: idx_sp_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sp_event ON public.sports_predictions USING btree (event_id);


--
-- Name: idx_sp_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sp_hash ON public.sports_predictions USING btree (commitment_hash);


--
-- Name: idx_sp_unsettled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sp_unsettled ON public.sports_predictions USING btree (event_start) WHERE (settled_at IS NULL);


--
-- Name: idx_spiffe_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_spiffe_did ON public.spiffe_bindings USING btree (did);


--
-- Name: idx_spiffe_uri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_spiffe_uri ON public.spiffe_bindings USING btree (spiffe_uri);


--
-- Name: idx_trust_score_cache_valid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trust_score_cache_valid ON public.trust_score_cache USING btree (cache_valid_until);


--
-- Name: idx_usdc_deposits_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usdc_deposits_did ON public.usdc_deposits USING btree (to_did);


--
-- Name: idx_usdc_deposits_tx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usdc_deposits_tx ON public.usdc_deposits USING btree (tx_hash);


--
-- Name: idx_vc_challenges_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vc_challenges_expires ON public.vc_challenges USING btree (expires_at);


--
-- Name: idx_wa_wallet; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wa_wallet ON public.wallet_attestations USING btree (wallet);


--
-- Name: idx_webhook_events_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_webhook_events_agent ON public.webhook_events USING btree (agent_id);


--
-- Name: idx_x402_calls_did; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_x402_calls_did ON public.x402_verify_calls USING btree (queried_did);


--
-- Name: idx_x402_calls_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_x402_calls_time ON public.x402_verify_calls USING btree (called_at);


--
-- Name: credit_transactions trg_no_update_credit_tx; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_no_update_credit_tx BEFORE DELETE OR UPDATE ON public.credit_transactions FOR EACH ROW EXECUTE FUNCTION public.prevent_ledger_mutation();


--
-- Name: agent_delegation_config agent_delegation_config_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_delegation_config
    ADD CONSTRAINT agent_delegation_config_did_fkey FOREIGN KEY (did) REFERENCES public.agents(did);


--
-- Name: agent_messages agent_messages_to_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_to_did_fkey FOREIGN KEY (to_did) REFERENCES public.agents(did);


--
-- Name: agents agents_parent_probe_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_parent_probe_did_fkey FOREIGN KEY (parent_probe_did) REFERENCES public.probe_agents(did) ON DELETE SET NULL;


--
-- Name: api_keys api_keys_owner_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_owner_did_fkey FOREIGN KEY (owner_did) REFERENCES public.agents(did);


--
-- Name: conversion_funnel conversion_funnel_probe_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversion_funnel
    ADD CONSTRAINT conversion_funnel_probe_did_fkey FOREIGN KEY (probe_did) REFERENCES public.probe_agents(did) ON DELETE CASCADE;


--
-- Name: credentials credentials_subject_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credentials
    ADD CONSTRAINT credentials_subject_did_fkey FOREIGN KEY (subject_did) REFERENCES public.agents(did);


--
-- Name: credit_balances credit_balances_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_balances
    ADD CONSTRAINT credit_balances_did_fkey FOREIGN KEY (did) REFERENCES public.agents(did);


--
-- Name: did_bridges did_bridges_moltrust_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.did_bridges
    ADD CONSTRAINT did_bridges_moltrust_did_fkey FOREIGN KEY (moltrust_did) REFERENCES public.agents(did);


--
-- Name: outcome_records outcome_records_flag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcome_records
    ADD CONSTRAINT outcome_records_flag_id_fkey FOREIGN KEY (flag_id) REFERENCES public.flag_records(flag_id);


--
-- Name: prediction_market_events prediction_market_events_wallet_address_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prediction_market_events
    ADD CONSTRAINT prediction_market_events_wallet_address_fkey FOREIGN KEY (wallet_address) REFERENCES public.prediction_wallets(address);


--
-- Name: probe_activity probe_activity_probe_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.probe_activity
    ADD CONSTRAINT probe_activity_probe_did_fkey FOREIGN KEY (probe_did) REFERENCES public.probe_agents(did) ON DELETE CASCADE;


--
-- Name: products products_brand_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_brand_id_fkey FOREIGN KEY (brand_id) REFERENCES public.brands(id);


--
-- Name: resellers resellers_brand_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resellers
    ADD CONSTRAINT resellers_brand_id_fkey FOREIGN KEY (brand_id) REFERENCES public.brands(id);


--
-- Name: signal_providers signal_providers_agent_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_providers
    ADD CONSTRAINT signal_providers_agent_did_fkey FOREIGN KEY (agent_did) REFERENCES public.agents(did);


--
-- Name: spiffe_bindings spiffe_bindings_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.spiffe_bindings
    ADD CONSTRAINT spiffe_bindings_did_fkey FOREIGN KEY (did) REFERENCES public.agents(did);


--
-- Name: sports_predictions sports_predictions_agent_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sports_predictions
    ADD CONSTRAINT sports_predictions_agent_did_fkey FOREIGN KEY (agent_did) REFERENCES public.agents(did);


--
-- Name: usdc_deposits usdc_deposits_to_did_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usdc_deposits
    ADD CONSTRAINT usdc_deposits_to_did_fkey FOREIGN KEY (to_did) REFERENCES public.agents(did);


--
-- PostgreSQL database dump complete
--


