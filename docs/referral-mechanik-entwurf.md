# Referral — Mechanik-Entwurf

**Entwurf, nicht scharf.** Kein Code im Request-Pfad, keine Auszahlung, kein
Eintrag in `pool_spend`. Scharfschalten frühestens mit Runde 3 und nur nach
eigener Freigabe.

## Was bezahlt wird

**0,05 USDC je geworbenem Neuzugang**, der ein verankertes Credential trägt.
Nicht je Registrierung: eine Registrierung ohne Anker ist eine Zeile, kein
Agent, und wäre in Runde 2 für 0,05 zu haben gewesen.

## Nachweis

`POST /identity/register-pop` bekommt ein Feld `referrer` neben dem schon
gebauten `source`. Beide sind selbstberichtet; der Unterschied ist, dass
`referrer` Geld auslöst und deshalb eine Bedingung mehr trägt:

- Der Werber muss selbst registriert, nicht widerrufen und verankert sein.
- Selbstwerbung fällt raus (`referrer <> did`).
- Eine Wallet je Werber, und der Werber muss eine gebunden haben — sonst gibt
  es keine Adresse, an die gezahlt werden könnte.
- Eine Zuschreibung je Geworbenem, beim Anlegen gesetzt und nie geändert.

## Deckel

Aus dem Rundenbudget, **nicht** aus dem Defekt-Topf. Zwei Grenzen:

- **20 je Werber**, damit ein einzelner Betreiber nicht die ganze Runde
  abräumt.
- **200 gesamt je Runde** = 10 USDC. Erreicht heißt: Mechanik aus, nicht
  stillschweigend weiterlaufen.

## Was der Deckel nicht kann

Er erkennt keinen Betreiber mit zwanzig Werber-DIDs. Dagegen hilft nur die
Finanzierungsseite, und die ist weiter nicht messbar — 66 von 78 Wallets aus
Runde 2 stehen auf nonce 0, weil taskmarket gaslos relayt. Wer die Mechanik
scharf schaltet, kauft dieses Risiko mit ein; 10 USDC je Runde ist der Preis,
zu dem das vertretbar erscheint.

## SQL, zum Nachrechnen vor jeder Auszahlung

```sql
-- Zahlbare Werbungen. Nichts hier zahlt; die Liste geht wie die
-- Bounty-Gewinnerliste zur Vorlage und wird von Hand angenommen.
WITH werbung AS (
  SELECT r.referrer, r.did AS geworben, a.created_at
    FROM agent_referral r
    JOIN agents a ON a.did = r.did AND a.revoked_at IS NULL
   WHERE r.referrer <> r.did
     AND EXISTS (SELECT 1 FROM credentials c
                   JOIN credential_anchors k ON k.credential_id = c.id
                  WHERE c.subject_did = a.did AND NOT c.revoked)
), werber_ok AS (
  SELECT w.*, ag.wallet_address
    FROM werbung w
    JOIN agents ag ON ag.did = w.referrer
   WHERE ag.revoked_at IS NULL
     AND ag.wallet_address IS NOT NULL
     AND EXISTS (SELECT 1 FROM credentials c
                   JOIN credential_anchors k ON k.credential_id = c.id
                  WHERE c.subject_did = ag.did AND NOT c.revoked)
), gedeckelt AS (
  SELECT *, row_number() OVER (PARTITION BY referrer ORDER BY created_at) AS lfd
    FROM werber_ok
)
SELECT referrer, wallet_address, count(*) AS zahlbar,
       round(count(*) * 0.05, 2) AS usdc
  FROM gedeckelt
 WHERE lfd <= 20                      -- Deckel je Werber
 GROUP BY 1, 2
 ORDER BY 3 DESC;
```

Die Tabelle dazu, noch nicht angelegt:

```sql
CREATE TABLE IF NOT EXISTS agent_referral (
    did         varchar PRIMARY KEY,   -- der Geworbene, eine Zuschreibung je DID
    referrer    varchar NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_referral_referrer_idx ON agent_referral (referrer);
```

## Offen vor dem Scharfschalten

- Wer zahlt: `0xd8f5` unter dem bestehenden Deckel, oder ein eigener Topf.
- Ob der Werber die Zahlung annehmen muss oder sie gutgeschrieben bekommt.
- Ob ein Werber, der später widerrufen wird, bereits gezahlte Werbungen behält.
