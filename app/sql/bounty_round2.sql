-- Bounty round 2: who gets paid, and in what order.
--
-- Round 1 scored 202 free-text submissions against eight criteria and paid six.
-- The sighting cost more than the bounty. Here the task text names conditions
-- this database can answer, so the winner list is a query rather than a
-- judgement, and a worker can predict the outcome before submitting.
--
--   psql -h localhost -U moltstack -d moltstack \
--        -v include_a2a=true -f app/sql/bounty_round2.sql
--
-- Three knobs, all deliberate, none of them a default anyone should change
-- without saying so in the payout note:
--
--   include_a2a    Thirteen agents did every step and set platform='a2a'
--                  instead of 'taskmarket'. The task text asked for
--                  'taskmarket', so excluding them is defensible and paying
--                  them is defensible; what is not defensible is deciding it
--                  after seeing who they are. Same pattern as round 1, where
--                  CLAUDE.md's counting rule picks such agents up by behaviour.
--   wallet_dedupe  'earliest' — when one wallet carries several DIDs, the one
--                  that qualified first is the one that counts. Rewarding the
--                  first arrival rather than the last is the only ordering that
--                  cannot be gamed by re-submitting.
-- The window is the WALLET BINDING, not the registration. The task text asks
-- for a DID, a bound key and a bound wallet; it never says the DID has to be
-- new. Filtering on created_at excluded the round-1 agents who came back and
-- did the work, which is the one group round 1 was supposed to produce and
-- did not. Binding is the act the round is buying, so binding is the clock.
--
--   operator_cap   At most this many paid DIDs per operator, an operator being
--                  one bound wallet. It cuts nothing today and is kept anyway.
--                  POST /identity/bind already refuses a wallet that belongs to
--                  another DID, so one wallet carries one identity by
--                  construction; the cap only starts working when somebody
--                  binds a second wallet. What it cannot do is recognise one
--                  operator behind two wallets — that needs the funding source,
--                  which is still not measurable.
--
--                  NOT one /24. 104.30.180.0 carries 32 of these DIDs and is
--                  Cloudflare WARP, a shared exit; capping on it would refuse
--                  thirty submissions because of a VPN.

\set ON_ERROR_STOP on
\if :{?include_a2a} \else \set include_a2a false \endif
\if :{?operator_cap} \else \set operator_cap 2 \endif
\set round_start '2026-09-23 15:14'
\set winners 100

-- Stage 1: a DID that registered in the window, bound an API key by signature,
-- and bound a wallet on Base by signature. wallet_bound_at is the load-bearing
-- column — wallet_address alone is set on paths that never proved control.
WITH eligible AS (
    SELECT a.did,
           lower(a.wallet_address) AS wallet,
           a.registration_ip,
           a.platform,
           a.wallet_bound_at AS qualified_at
      FROM agents a
      JOIN api_keys k
        ON k.owner_did = a.did
       AND k.signup_method = 'did_signature'
     WHERE a.wallet_bound_at >= :'round_start'::timestamptz
       AND a.revoked_at IS NULL
       AND lower(a.wallet_chain) = 'base'
       AND (a.platform = 'taskmarket'
            OR (:include_a2a AND a.platform = 'a2a'))
),
-- wallet_dedupe = earliest, and the operator cap on top of it.
ranked AS (
    SELECT *,
           row_number() OVER (PARTITION BY wallet ORDER BY qualified_at) AS n_wallet
      FROM eligible
),
paid AS (
    SELECT * FROM ranked WHERE n_wallet <= :operator_cap
)
SELECT row_number() OVER (ORDER BY qualified_at) AS rank,
       did, wallet, platform, qualified_at
  FROM paid
 ORDER BY qualified_at
 LIMIT :winners;

-- The same run as one line, for the payout note.
WITH eligible AS (
    SELECT a.did, lower(a.wallet_address) AS wallet, a.platform,
           a.wallet_bound_at AS qualified_at
      FROM agents a
      JOIN api_keys k ON k.owner_did = a.did AND k.signup_method = 'did_signature'
     WHERE a.wallet_bound_at >= :'round_start'::timestamptz
       AND a.revoked_at IS NULL
       AND lower(a.wallet_chain) = 'base'
       AND (a.platform = 'taskmarket' OR (:include_a2a AND a.platform = 'a2a'))
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY wallet ORDER BY qualified_at) AS n_wallet
      FROM eligible
)
SELECT :'include_a2a'          AS include_a2a,
       :operator_cap           AS operator_cap,
       count(*)                                         AS eligible_dids,
       count(DISTINCT wallet)                           AS distinct_wallets,
       count(*) FILTER (WHERE n_wallet <= :operator_cap) AS would_be_paid,
       count(*) FILTER (WHERE n_wallet >  :operator_cap) AS cut_by_cap,
       count(*) FILTER (WHERE platform = 'a2a')          AS from_a2a
  FROM ranked;

-- Completeness, the same check the round-1 file carries: a "nobody qualified"
-- figure is worthless if the window starts after the data does.
SELECT (SELECT min(created_at) FROM agents WHERE created_at >= :'round_start'::timestamptz) AS first_registration,
       (SELECT count(*) FROM agents WHERE created_at >= :'round_start'::timestamptz)        AS registrations_in_window,
       (SELECT count(*) FROM wallet_first_tx)                                               AS wallet_first_tx_cached;
