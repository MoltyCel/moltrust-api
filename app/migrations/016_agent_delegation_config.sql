-- 016: agent_delegation_config als repo-verwaltete Migration nachziehen.
--
-- Die Tabelle existiert seit April 2026 produktiv, aber nur von Hand angelegt — im Repo
-- gab es sie nie. Sie traegt inzwischen die Auswahl, welche Enforcement-Maschine fuer einen
-- Agenten greift (`constraint_mode`): none/inherit/restrict laufen ueber den AAE-Evaluator,
-- enforce ueber den reinen Laufzeit-Kern hinter POST /enforce/check (#306). Eine Tabelle mit
-- dieser Rolle darf nicht ausschliesslich im Kopf existieren; ohne sie laesst sich die
-- Umgebung nicht reproduzieren.
--
-- Diese Migration LEGT NICHTS NEUES AN. Sie schreibt exakt die Struktur auf, die live steht:
-- fuenf Spalten, PK auf did, FK auf agents(did). Keine Spalte fuer die spaetere
-- restrict+enforce-Kombination — die braucht getrennte Felder und einen eigenen Entwurf,
-- und Vorratshaltung im Schema waere genau die Art Altlast, die hier gerade behoben wird.
--
-- Gegen die bestehende Tabelle ist sie ein No-op (IF NOT EXISTS), gegen eine frische
-- Datenbank legt sie sie identisch an. Verifiziert per Struktur-Diff gegen die Live-DB.
--
-- NICHT enthalten: ein CHECK auf constraint_mode. Live gibt es keinen; das Enum wird in
-- app/main.py (/delegation/configure) geprueft, nicht in der Datenbank. Einen CHECK hier zu
-- ergaenzen waere eine Aenderung am Ist-Zustand, keine Nachdokumentation — falls gewuenscht,
-- gehoert das in eine eigene Migration mit eigener Entscheidung.

BEGIN;

CREATE TABLE IF NOT EXISTS agent_delegation_config (
    did                  varchar(40)  NOT NULL PRIMARY KEY REFERENCES agents(did),
    delegation_permitted boolean      DEFAULT false,
    max_depth            integer      DEFAULT 0,
    constraint_mode      varchar(20)  DEFAULT 'none',
    updated_at           timestamp    DEFAULT now()
);

-- Wie 008/009: die Migration laeuft als `postgres`, die App als `moltstack`.
-- Live entspricht das dem vorhandenen ACL-Eintrag (moltstack=arwdDxt/postgres). Idempotent.
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_delegation_config TO moltstack;

COMMIT;

-- DOWN (manuell, bewusst nicht automatisch):
--   DROP TABLE IF EXISTS agent_delegation_config;
-- Loescht die Modus-Zuordnung aller Agenten. Nach einem DROP faellt jeder Agent auf "none"
-- zurueck — ein enforce-Agent liefe danach wieder ueber den AAE-Evaluator. Nicht beilaeufig
-- ausfuehren.
