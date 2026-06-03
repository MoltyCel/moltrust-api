# Architektur-Brief — D-1 Acceptance-Gate (AAE Signature & Schema Verification)
**Status:** DESIGN-BRIEF (kein Code). Sign-off → Security-Review (ai_review SECURITY) → DANN Code.
**Scope:** AAE draft-04 **§5 Step 1 (Signatur-Verifikation + signing-authority) + Step 2 (payload/schema/`cty:"aae+json"`)** — submit-time acceptance. Step 4 (subject-binding challenge) und Step 9 (delegation chain) sind **explizite Follow-ons**, NICHT hier.
**Datum:** 2026-06-02 · **Autor:** Lars Kroehl
**Referenzen:** ADR-D3-mandate-enforcement-v3 (ACCEPTED, D-1 = Acceptance-Gate), AAE draft-04 §5, `app/enforcement/envelope_store.py` (Komponente 1), `app/main.py:6000` (`aae_submit`).

## Zweck
Heute speichert Komponente 1 `issuer_did` + `envelope_signature`, **verifiziert sie aber NIE** — die Signatur ist storage-only. D-1 schließt diese Lücke: bei AAE-Registrierung wird die Issuer-Signatur kryptographisch geprüft (fail-closed), bevor der Envelope persistiert wird. Reine Krypto-Verifikation, **NICHT CEP-gated** (unabhängig von enforce-mode/Komponente 3 baubar).

## Getroffene Entscheidungen

### #1 Submit-Contract: JWS-wrapped VC (Contract-Wechsel Komponente 1)
- Der Client submitted eine **compact JWS** (RFC 7515). Der Server **extrahiert** `mandate`/`constraints`/`validity` aus dem **verifizierten** payload (`credentialSubject.aae`) — nicht mehr aus separaten Body-Feldern.
- Die Signatur deckt damit **exakt, was der Issuer signierte**: `b64url(protected) . b64url(payload)`. Kein server-zusammengebautes `raw_canonical` mehr als Signatur-Grundlage.
- **Impact Komponente 1 (explizit):**
  - **API-Bruch `/vc/aae/submit`:** Body wechselt von `{aae_id, issuer_did, signature, mandate, constraints, validity}` zu **`{aae_jws: "<compact JWS>"}`** (alles Übrige wird aus dem verifizierten VC abgeleitet). Versioniert ankündigen.
  - **`raw_canonical` Neudefinition:** bislang JCS von `{mandate,constraints,validity}` (server-gebaut). Künftig = **der JWS-payload-Bytes** (das, was signiert wurde) — damit `aae_ref = sha256(raw_canonical)` an die signierte Form bindet. **Sign-off-Frage:** raw_canonical = exakt `b64url(payload)`-Bytes ODER die dekodierten payload-JSON-Bytes? (Re-Verify muss reproduzierbar sein.)
  - **`issuer_did`** wird aus dem VC `issuer` abgeleitet (nicht mehr client-behauptet); **`envelope_signature`** = die compact JWS verbatim.
  - Migration: additive Spalten möglich (siehe trust-tier), aber der Store bleibt strukturell; der Bruch ist im **API-/Persist-Pfad**, nicht im Tabellen-Schema (außer trust-tier-Spalte).

### #2 DID-Methoden (Launch)
- **`did:web`** — via `_resolve_did_web_external` (`main.py:2173`) + registrierte `jwks_url`, **erweitert um Verification-Method-Dereferencing** (DID-Doc → `assertionMethod` → JWK).
- **`did:moltrust`** — via Registry (eigene DIDs/Keys).
- **`did:key`** = Follow-on (self-contained key, kein Netz-Resolve — später).

### #3 Trust-Modell: resolve-and-verify mit Trust-Tiering (KEINE hard-allowlist)
- **Signatur-Echtheit ist Pflicht/fail-closed:** ungültige/unauflösbare Signatur → reject. Immer.
- **Issuer-Trust-Level als mitgeführtes Attribut** (analog zur Evaluator-`value_source`): `trusted` vs `unverified_issuer`. Self-souverän — jeder valide Issuer wird akzeptiert (Signatur muss stimmen), aber das **Tier wird persistiert + mitgeführt**, sodass nachgelagerte Schichten (Evaluator/enforce-mode) bei `unverified_issuer` strenger sein können. Schließt das self-issued-unknown-Loch durch Tiering statt durch Ausschluss.
- **Tier-Bestimmung (Skizze):** `trusted` = Issuer-DID in einer kuratierten/registrierten Menge ODER did:moltrust-Registry; `unverified_issuer` = valide Signatur, aber unbekannter Issuer. Genaue Tier-Kriterien = Sign-off.

### #4 Scope-Grenze
**NUR §5 Step 1 + Step 2.** Step 3/5/6/7 (temporal/single_use/action/constraints) macht der Evaluator (Komponente 2, live). Step 4 (subject-binding challenge-response) + Step 8 (revocation, deferred) + Step 9 (delegation chain) = separate Follow-ons.

## Step 1 — Signatur-Verifikation + signing-authority (Detail aus §5)
1. **Compact JWS parsen** mit **PyJWT 2.12.1** (`jwt.api_jws.PyJWS` low-level — kein neues Dependency; verifiziert arbiträren payload + liest protected-header; EdDSA via `cryptography`).
2. Protected-header lesen → **`kid`** = DID-URL → **signing-DID = DID-Teil von kid**.
3. **Signing-DID resolven** → Verification-Method dereferenzieren; require ALLE:
   - (a) VM **present** im DID-Doc;
   - (b) für **`assertionMethod`** proof-purpose autorisiert;
   - (c) JWK **`kty:"OKP"` / `crv:"Ed25519"`**;
   - (d) **JWS-Signatur valide** unter diesem Key.
4. **`alg` MUSS `"EdDSA"`** sein.
5. **Signing-authority (non-delegated):** signing-DID **MUSS == VC `issuer`** (delegated → Follow-on Step 9).
6. **Reject** bei: DID unauflösbar, VM absent / nicht-assertionMethod, Key nicht Ed25519, alg≠EdDSA, Signatur invalid, signing-DID≠issuer.

## Step 2 — Payload & Schema
- JWS-payload = UTF-8-JSON = **voller W3C Verifiable Credential**.
- Protected-header **`cty` MUSS `"aae+json"`** sein.
- VC MUSS enthalten: `id`, `issuer`, `credentialSubject.id`, `credentialSubject.aae`; `aae` MUSS `mandate` + `constraints` + `validity` enthalten.
- Falsche Typen / fehlende Member → reject.

## Hook-Point
`aae_submit` (`main.py:6000`), **fail-closed**: D-1 läuft VOR `persist_envelope`. Bad/unverifiable JWS → **reject** (Acceptance-Gate). Nur ein verifizierter VC wird persistiert; `mandate/constraints/validity` kommen aus dem verifizierten payload.

## Canonicalization-Klarheit (drei Schemata im System — D-1 nutzt JOSE)
1. **sorted-json** (`json.dumps(sort_keys=True)`) — Alt-`verify_credential` (MolTrust-self, nacl/hex). NICHT D-1.
2. **JCS / RFC 8785** — Store (`raw_canonical`, `aae_ref`) + Evaluator-`verdict_sign`. NICHT die Issuer-Signatur-Grundlage.
3. **JOSE / JWS** (`b64url(protected).b64url(payload)`) — **die AAE-Issuer-Signatur. D-1 verifiziert DIESE Bytes.**
> D-1 prüft die JOSE-JWS-Bytes (was der Issuer signierte) — **nicht** das JCS-`raw_canonical`. Das ist der subtile Korrektheitspunkt: "was verifiziert wird" == "was signiert wurde", byte-genau.

## Architektur-Guards
- **Fail-closed** (Default-DENY-Linie): jede Unsicherheit (DID nicht auflösbar, VM-Ambiguität, Parse-Fehler) → reject, nie still akzeptieren.
- **Unabhängige Verifikation:** der Issuer-Key kommt aus dem aufgelösten DID-Doc, nicht aus client-behaupteten Feldern.
- **Kein DSGVO-Volllog:** nur Verifikations-Ergebnis/Tier + Hash, kein VC-Inhaltslog über das Nötige hinaus.

## Sign-off RESOLVED (2026-06-02)

**1. DID-Resolution SSRF/DoS — DERSELBE Egress-Proxy wie revocation_check, KEINE neue Mitigation.**
did:web-Resolution (outbound HTTPS auf DID-Doc/jwks_url) läuft über **denselben dedizierten Egress-Proxy** wie der Evaluator-`revocation_check` (ADR-v3 C2): RFC1918 + IPv6 (`::1/128`,`fc00::/7`,`fe80::/10`,`::ffff:0:0/96`) + CGNAT (`100.64.0.0/10`) + Broadcast-Blocklist, **https-only**-Scheme-Allowlist, DNS-Rebinding **resolve+pin** vor Connect, Timeout + Circuit-Breaker. Nichts Neues zu bauen — D-1 erbt die Mitigation.
- **Entkopplung (wichtig):** did:web-Resolution ist **geblockt bis der Egress-Proxy (Harald) steht**. **D-1 STARTET mit `did:moltrust`-only** (Registry-Lookup, **kein** outbound) → **D-1 ist für den did:moltrust-Pfad NICHT auf den Egress-Proxy-Termin gated**. did:web wird aktiviert, sobald der Proxy live ist. So liefert D-1 sofortigen Wert ohne Infra-Abhängigkeit.

**2. `raw_canonical` = JWS-payload (was signiert wurde).**
Der `raw_canonical`-Inhalt wechselt auf die **JWS-payload-Bytes**; der **hash-binding-Trigger bleibt strukturell unverändert** (hasht weiterhin `raw_canonical`, nur anderer Inhalt → `aae_ref = sha256(JWS-payload)` bindet an die signierte Form). **BREAKING change am submit-Contract** — aber nur **Smoke-Test-Rows** betroffen (kein Produktiv-Traffic auf `/vc/aae/submit`). Wird dokumentiert; kein Daten-Migrationsschritt nötig (alte Test-Rows bleiben als Artefakt, neue folgen dem JWS-Contract).

**3. Trust-Tier-Persistierung — neue Spalte `issuer_trust_tier`.**
Additive Migration: `issuer_trust_tier text` in `aae_envelopes` (`trusted` / `unverified_issuer`), CHECK-eingeschränkt, **analog zur Evaluator-`value_source`**. Gesetzt von D-1 beim accept; vom Evaluator/enforce-mode konsumierbar (strenger bei `unverified_issuer`). Additiv → kein Bruch am Bestand.

**4. did:web-VM-Dereferencing — NEU bauen.**
`_resolve_did_web_external` liefert nur das **rohe DID-Doc** — **nicht genug**. D-1 baut eine **neue Schicht**: DID-Doc → Verification-Method (per `kid`) → `assertionMethod`-Proof-Purpose-Autorisierung prüfen → **OKP/Ed25519-JWK extrahieren**. Kein vorhandenes Wiring wiederverwendbar; der Resolver liefert nur den Rohinput.

### Konsequenz: D-1-Launch-Reihenfolge
1. **Phase A (sofort, kein Infra-Blocker):** did:moltrust-only Acceptance-Gate (Registry-Key-Lookup, JWS-verify, signing-authority, payload/schema, trust-tier). Liefert Step 1+2 für den internen/Registry-Issuer-Pfad.
2. **Phase B (wenn Egress-Proxy live):** did:web aktivieren (VM-Dereferencing-Schicht + Egress-Proxy-Resolution).

## Review-Härtung (Security-Review `20260603_052110`, RESOLVED)
Verdikt = ÜBERARBEITEN bei solidem Grunddesign; 4 Criticals + 2 Mediums (klassische externe-JWS-Bug-Klassen) als verbindliche Implementierungs-Regeln eingearbeitet:

**1. alg-confusion (Critical) — normativ.** JWS-Verifikation MUSS mit **explizitem `algorithms=["EdDSA"]`-Allowlist** erfolgen (PyJWS low-level). Der `alg`-Wert aus dem Header wird **NIE** als Vertrauensbasis genommen; `alg=none`, HS*, RS* → hart reject (nicht in Allowlist). Header-`alg`≠"EdDSA" → reject vor jeder Key-Operation.

**2. kid-parsing (Critical).** Das `kid` (DID-URL) wird **strikt nach W3C DID Core validiert, BEVOR** daraus die signing-DID extrahiert oder ein Resolve angestoßen wird: Regex auf erlaubte DID-Methoden/Zeichen, **Path-Traversal-Schutz** (kein `..`/`/`-Missbrauch in der method-specific-id), **Look-alike-DID-Schutz** (kein Unicode-/Homoglyph-Trick, ASCII-Normalisierung). Erst die validierte signing-DID geht in den `signing-DID == issuer`-Check.

**3. canonicalization (Critical) — kein re-serialize.** `raw_canonical` = die **exakten base64url-DEKODIERTEN payload-Bytes** des JWS (das, was der Issuer signiert hat). Es wird **NIE** re-serialisiert/re-canonicalisiert (kein `json.dumps` des geparsten payload als Signatur-Grundlage) — Re-Serialisierung = Signatur-Bypass. Verifiziert werden die exakt signierten Bytes; das geparste JSON dient nur der Schema-Prüfung, nicht der Signatur.

**4. submit-replay / storage-DoS (Critical).** `/vc/aae/submit` bekommt **Rate-Limiting** (der CIDR-gated IP-keying-Fix gilt) + **optional per-issuer-Quota**. Exakte Replays sind bereits durch `aae_ref = sha256(raw_canonical)` als **PK** geblockt (identischer payload → identischer aae_ref → Duplicate-Insert-reject). Distinct re-signed Envelopes wachsen Storage → Rate-Limit/Quota deckelt das.

**5. (Medium) did:moltrust-Registry = SPOF.** Eine kompromittierte Registry bricht die ganze Vertrauenskette des did:moltrust-Pfads. Mitigation zu notieren: **Key-Rotation** (kid-basiert, wie verdict_kid) + Registry-Härtung; Tier-Logik so, dass ein Registry-Bruch nicht still `trusted` erschleicht.

**6. (Medium) JSON-duplicate-keys.** Der payload wird mit **`object_pairs_hook`** geparst, das **doppelte Keys ablehnt** (kein last-wins/first-wins-Confusion zwischen Verifizierer und nachgelagerten Konsumenten).

> Diese 6 Punkte sind Implementierungs-Vertrag, keine Architektur-Änderung. Das Grunddesign (fail-closed, assertionMethod-proof-purpose, signing-DID==issuer, JWS-statt-custom-canon) wurde im Review bestätigt.

## Nächster Schritt
Brief → Sign-off (inkl. der offenen Punkte) → **ai_review.py SECURITY-Modus** (externe Signatur-Verifikation = klassischer Bug-Ort, höchste Kritikalität; kein Single-LLM) → bei Freigabe: Code komponentenweise (JWS-verify + DID-resolution/VM-deref + signing-authority + payload/schema + aae_submit-Wiring + trust-tier), je eigener PR, Pre-Commit-Diff-Verify.
