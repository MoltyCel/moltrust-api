-- Internal traffic predicate.
--
-- Which registrations are ours, by either of two signals: the operator host's
-- /24, or a platform string reserved for internal use. Used by /admin/funnel,
-- the agent-cluster panel and the nightly digest, so all three take internal
-- out of organic the same way.
--
-- Generated from app/funnel.py::build_internal_function_sql(). Do not edit by
-- hand: tests/test_funnel.py asserts this file matches the generator.

CREATE OR REPLACE FUNCTION funnel_is_internal(p text, reg_ip text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT lower(trim(coalesce(p, ''))) IN ('moltrust-internal', 'system', 'test')
           OR coalesce(reg_ip, '') LIKE '57.129.23.%'
$$;
