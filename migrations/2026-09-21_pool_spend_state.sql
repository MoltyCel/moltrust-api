-- 2026-09-21_pool_spend_state.sql
-- Ein Escrow ist keine Ausgabe.
--
-- pool_spend kannte nur "gebucht". Zwei Zeilen à 5 USDC lasen sich als
-- Bounty-Auszahlung und waren Escrow-Einzahlungen für Tasks, die noch offen
-- waren; der Abgleich zählte sie als ausgegeben, was für den Deckel stimmt und
-- für die Frage "was ist weg" nicht. Zwischen beidem lagen zehn USDC.
--
-- Von Hand anwenden, vor dem Deploy des Codes, der es liest:
--   psql -h localhost -U moltstack -d moltstack \
--        -f migrations/2026-09-21_pool_spend_state.sql

BEGIN;

ALTER TABLE pool_spend
    ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'spent';

-- spent    das Geld ist weg und kommt nicht zurück
-- escrowed hinterlegt, gebunden, rückholbar solange nichts zugeteilt ist
-- refunded war escrowed und ist zurückgeflossen
ALTER TABLE pool_spend
    DROP CONSTRAINT IF EXISTS pool_spend_state_check;
ALTER TABLE pool_spend
    ADD CONSTRAINT pool_spend_state_check
    CHECK (state IN ('spent', 'escrowed', 'refunded'));

-- Der Abgleich fragt "was ist noch gebunden" und "was zählt gegen den Deckel".
CREATE INDEX IF NOT EXISTS idx_pool_spend_state ON pool_spend (state);

COMMIT;
