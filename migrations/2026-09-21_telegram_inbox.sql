-- Inbound Telegram updates, delivered by webhook instead of polled.
--
-- getUpdates is exclusive per bot token: two pollers on one token steal each
-- other's updates, which is why ThreadWatch owned the token alone and the
-- reply radar could not have an approval loop. A webhook has no such limit —
-- every consumer reads this table and claims what it wants.
--
-- Created by the moltstack role, so no ALTER against a postgres-owned table.

CREATE TABLE IF NOT EXISTS telegram_inbox (
    update_id    BIGINT      PRIMARY KEY,
    payload      JSONB       NOT NULL,
    consumed_by  TEXT,
    consumed_at  TIMESTAMPTZ,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The consumers ask the same question every time: what is unclaimed, oldest
-- first.
CREATE INDEX IF NOT EXISTS idx_telegram_inbox_unconsumed
    ON telegram_inbox (ts) WHERE consumed_by IS NULL;
