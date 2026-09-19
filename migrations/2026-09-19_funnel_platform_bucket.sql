-- Funnel platform buckets.
--
-- One mapping from the free-form agents.platform string to a display bucket,
-- used by both /admin/funnel and the nightly Telegram digest so the two cannot
-- report different numbers for the same day.
--
-- Generated from app/funnel.py::build_bucket_function_sql(). Do not edit by
-- hand: tests/test_funnel.py asserts this file matches the generator.

CREATE OR REPLACE FUNCTION funnel_platform_bucket(p text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE lower(trim(coalesce(p, '')))
        WHEN 'clawhub' THEN 'clawhub'
        WHEN 'taskmarket' THEN 'taskmarket'
        WHEN 'smithery' THEN 'smithery'
        WHEN 'a2a' THEN 'a2a'
        WHEN 'erc8004' THEN 'erc8004'
        WHEN 'erc-8004' THEN 'erc8004'
        WHEN 'sdk' THEN 'sdk'
        ELSE 'other'
    END
$$;
