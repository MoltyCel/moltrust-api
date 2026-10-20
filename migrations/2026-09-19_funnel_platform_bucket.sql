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
        WHEN 'hermes' THEN 'hermes'
        WHEN 'smithery' THEN 'smithery'
        WHEN 'glama' THEN 'glama'
        WHEN 'a2a' THEN 'a2a'
        WHEN 'erc8004' THEN 'erc8004'
        WHEN 'erc-8004' THEN 'erc8004'
        WHEN 'rnwy' THEN 'rnwy'
        WHEN 'virtuals-acp' THEN 'virtuals-acp'
        WHEN 'virtuals' THEN 'virtuals-acp'
        WHEN 'virtuals_acp' THEN 'virtuals-acp'
        WHEN 'olas' THEN 'olas'
        WHEN 'taskmarket' THEN 'taskmarket'
        WHEN 'x402-bazaar' THEN 'x402-bazaar'
        WHEN 'x402bazaar' THEN 'x402-bazaar'
        WHEN 'bazaar' THEN 'x402-bazaar'
        WHEN 'langchain' THEN 'langchain'
        WHEN 'moltrust-langchain' THEN 'langchain'
        WHEN 'crewai' THEN 'crewai'
        WHEN 'moltrust-crewai' THEN 'crewai'
        WHEN 'openai-agents' THEN 'openai-agents'
        WHEN 'openai_agents' THEN 'openai-agents'
        WHEN 'vercel-ai' THEN 'vercel-ai'
        WHEN 'vercel' THEN 'vercel-ai'
        WHEN 'ai-sdk' THEN 'vercel-ai'
        WHEN 'sdk' THEN 'sdk'
        ELSE 'other'
    END
$$;
