# ADR — Identität des MolTrust Ambassador (did:web vs. did:moltrust)

- **Status:** offen, Entscheidung steht aus
- **Datum:** 2026-07-22
- **Entscheidet:** Lars (Identität, Schlüsselwahl) · Review: Harald
- **Betrifft:** `agents/workspace/ambassador/IDENTITY.md`, `agents/ambassador.py`,
  `agent/ambassador.py`, `agents/moltbook_poster.py`, `app/main.py` (`DID_PATTERN`)

## Befund

Der Ambassador führt zwei DIDs. Live geprüft am 2026-07-22:

| Identität | `/identity/verify` | Auflösung nach W3C did:web | Fundstellen |
|---|---|---|---|
| `did:moltrust:ambassador0001` | 200, `verified:true` | entfällt | `agents/ambassador.py:34`, `agent/ambassador.py:16`, `agents/moltbook_poster.py:16`, `tests/test_did_lookup.py:28` |
| `did:web:api.moltrust.ch:agents:ambassador` | 400, „Invalid DID format. Expected: did:moltrust:…" | 404 auf `https://api.moltrust.ch/agents/ambassador/did.json` | nur `IDENTITY.md` |

Im Registry stehen 87 Agenten, davon 0 mit einer `did:web`-DID. Die längste
eingetragene DID hat 33 Zeichen.

`MoltyCel/moltrust-api` ist öffentlich (`visibility: public`, authentifiziert
über die GitHub-API geprüft). `IDENTITY.md` ist damit über GitHub lesbar, und
die dort genannte DID läuft ins Leere: Wer sie nach W3C-Verfahren auflöst,
bekommt 404; wer sie gegen unser eigenes Registry prüft, bekommt 400.

Der Org-DID `did:web:api.moltrust.ch` löst dagegen sauber auf
(`/.well-known/did.json`, 200). Das Verfahren funktioniert also — nur für den
Agenten fehlt das Dokument.

Zwei Hürden liegen zwischen dem heutigen Stand und einer echten did:web-Identität.
Die mechanische ist seit PR #283 weg: die DID-Felder standen auf `max_length=40`,
der String hat 41 Zeichen, jetzt sind es 128. Die inhaltliche steht weiter —
`DID_PATTERN` in `app/main.py:846` akzeptiert ausschließlich
`did:moltrust:<16 hex>`, und `/identity/verify` prüft dagegen.

## Optionen

**A — Die verifizierbare Identität wird die dokumentierte.**
`IDENTITY.md` nennt `did:moltrust:ambassador0001`. Der did:web-String wird als
reserviert und noch nicht aufgelöst gekennzeichnet oder gestrichen.
Aufwand: eine Datei. Kein Deploy, kein Schlüssel, jederzeit rücknehmbar.

**B — Die dokumentierte Identität wird verifizierbar.**
Dafür ist zu bauen:

1. DID-Dokument unter `https://api.moltrust.ch/agents/ambassador/did.json`,
   Format wie `/.well-known/did.json`.
2. Schlüsselwahl: eigenes Schlüsselpaar für den Agenten oder Ableitung aus dem
   Registry-Schlüssel. Das ist eine Schlüsselentscheidung und gehört zu Lars.
3. `alsoKnownAs`-Verknüpfung beider DIDs in beide Richtungen, damit die
   Moltbook-Historie unter `did:moltrust:ambassador0001` nicht abreißt.
4. `DID_PATTERN` und der Acceptance-Gate-Pfad müssen `did:web` im Verify
   akzeptieren, heute tun sie es nicht.
5. Registry-Eintrag für die did:web-Identität, samt Entscheidung, ob beide IDs
   nebeneinander stehen oder eine die andere ablöst.

## Empfehlung

A jetzt, B als eigener Strang mit eigenem Review.

Begründung: Solange B nicht vollständig gebaut ist, behauptet eine öffentliche
Datei eine Identität, die niemand prüfen kann. Für ein Registry ist das der
teuerste der beiden Fehler — ein Anbieter von Identitätsnachweisen, dessen
eigener Agent keinen erbringt. A beseitigt das mit einer Zeile und nimmt B
nichts vorweg: der Alias bleibt als reserviert dokumentiert.

Dieser PR baut A.

## Entscheidung (2026-07-22)

Lars hat die Richtung bestätigt: **Option A ist umgesetzt** — die dokumentierte
und die verifizierbare Identität sind jetzt dieselbe (`did:moltrust:ambassador0001`,
`/identity/verify` → 200, `verified:true`). Der did:web-Alias bleibt in
IDENTITY.md als *reserviert, löst noch nicht auf* markiert. Der Root-DID
`did:web:api.moltrust.ch` bleibt unberührt und löst weiter sauber auf.

**Option B (echtes per-agent did:web) ist VERTAGT.** Begründung:

- B verlangt ein DID-Dokument je Agent unter
  `https://api.moltrust.ch/agents/<id>/did.json`, eigenes Schlüsselmaterial und
  eine Rotationsstrategie (Schritte 1–5 oben).
- Kein externer Konsument verlangt derzeit eine did:web-Auflösung für einen
  MolTrust-Agenten.
- `did:moltrust:ambassador0001` löst auf (200) und besteht `/identity/verify` —
  die Identität ist heute nachweisbar, nur nicht als did:web.
- Der Root `did:web:api.moltrust.ch` bleibt unberührt.

## Trigger zum Wiederaufgreifen (Option B)

Ein externer Partner verlangt die **did:web-Auflösung für einen MolTrust-Agenten**
(nicht bloß für den Root-DID). Spiegelfall: genau das, was MolTrust selbst in
`microsoft/autogen#7525` von MoltBridge verlangt — die Gegenseite muss ihre
Agenten-DID nach W3C did:web auflösbar machen. Sobald dieselbe Anforderung an
uns gestellt wird, wird B aufgegriffen.

## Offen / nicht geprüft

- Außerhalb des Repos: `~/moltstack/logs` und `~/moltstack/data` wurden auf den
  did:web-String geprüft — **keine Fundstelle**, der Bot hat ihn nicht in Posts
  rausgetragen. Moltbook-Profil/Fremdzitate sind von hier nicht prüfbar.
- Historische Erwähnungen in `docs/audits/agent_audit_20260424.md` bleiben als
  Befund-Protokoll stehen (sie dokumentieren genau diese jetzt geschlossene
  Lücke). Der Dev-Guide
  `content_scout/.webdocs/blog/skill-verification-developer-guide.html` nutzt
  `did:web:api.moltrust.ch:agents:<id>` als Platzhalter-Format für fremde Agenten
  — separater Scope (external-agent did:web), hier nicht mitbehandelt.
- Ob `agent/ambassador.py` und `agents/ambassador.py` beide noch laufen. Der
  Mac-Zweig ist seit 2026-06-20 abgeschaltet; die Doppelablage im Repo bleibt
  davon unberührt und ist hier nicht mitbehandelt.
