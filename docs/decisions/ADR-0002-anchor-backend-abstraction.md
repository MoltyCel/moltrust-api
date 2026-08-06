# ADR-0002 — Anchor-Backend-Abstraktion: Anker im Record, nicht im Identifier

**Status:** **ACCEPTED** (Richtungsentscheidung). **Implementierung: FUTURE** — kein Code in diesem ADR, kein Bau-Auftrag. Trigger siehe §Scope.
**Datum:** 2026-08-04 · **Autor:** Lars Kroehl
**Bezug:** Verlängert `docs/decisions/ADR-CEP-governance.md` (dort verworfen: Single-Chain-Governance; Architektur-Guard „Kein Single-Chain-Lock-in") von der Governance- auf die **Anker-Ebene**. Anlass: `kroftrust.md` (Paola / AIKR) kritisiert die *resolution dependency* chain-basierter Trust-Layer.

**Recon-Basis (verifiziert 2026-08-04, `origin/main` = `af656ac`):**
- (a) `moltproof/src/engine/recompute.ts:8-11` → Quelltext-Kommentar: *„Fully offline: no DID/mandate/context fetching happens here (F4)."* `recomputeVerdict` (`recompute.ts:56`) rechnet aus bereits dekodierten öffentlichen Eingaben.
- (a) `verify-package/src/chain.ts:4` → `DEFAULT_RPC = 'https://mainnet.base.org'`; `chain.ts:16-21` baut den Client unbedingt; RPC-Call-Stellen `verifier.ts:80,85,102`.
- (a) `app/main.py:4324` und `app/provenance/anchor.py:227` → `chainId: 8453` als Literal im TX-Dict. RPC-Literal `main.py:4304`, env-überschreibbar nur in `anchor.py:207`.
- (a) `app/provenance/anchor.py:120` → `async def anchor_batch(conn, anchor_fn)`. Naht auf Batch-Ebene vorhanden.
- (a) `app/skale_anchor.py` → 18 Zeilen, nur `CHAIN_CONFIG = {…}`, keine `def`/`class`. Deckt sich mit `ADR-CEP-governance.md:97` („Stub, config-only", Multi-Chain als V3 geführt).
- (a) `moltproof/src/config/chains.ts:9-24` → 7 Chains deklariert, `kind ∈ {evm, solana, hyperliquid}` (`types.ts:135`). Reader-Implementierungen nur zwei: `replay.ts:23 StaticReader`, `replay.ts:44 EvmSwapReader`. Solana `status: "beta"`, `note: "decode path in progress"` (`chains.ts:16`).
- (b) `kroftrust.md` liegt **nicht** im Repo und wurde für dieses ADR nicht gelesen. Die Kritik ist hier als Anlass referenziert, nicht zitiert. Vor einer öffentlichen Erwiderung im Wortlaut prüfen.

---

## Problem: resolution dependency

Eine Verifikation, die zur Prüfzeit eine bestimmte Chain erreichen muss, erbt drei Eigenschaften dieser Chain: ihre **Sterblichkeit** (sie kann anhalten, forken, reorganisieren), ihre **Governance** (sie kann zensieren) und ihre **Latenz/Verfügbarkeit** (RPC nicht erreichbar = Prüfung nicht durchführbar). Das ist die *resolution dependency*, die `kroftrust.md` an chain-basierten Trust-Layern kritisiert.

Der arXiv-Preprint 2605.06738v2 §4.1 hat dieselbe Bindung für die CEP-Zertifizierungsinstanz bereits verworfen:

> A single chain. Anchoring the judgement to one ledger inherits that ledger's mortality and politics: it can halt, censor, reorganise, or fork. A verification protocol meant to be neutral keeps its most consequential signal free of any single chain's governance.

Dort betrifft es die Frage, wer Evidenz zertifiziert. Dieses ADR zieht dieselbe Linie eine Ebene tiefer: auf den Anker selbst.

**Der global tragende Grund ist jurisdiktioneller, nicht technischer.** Es gibt Jurisdiktionen, in denen Agenten, Reseller oder Entwickler keinen legalen Zugang zu einer öffentlichen Blockchain haben — der China-Fall (Reseller und Developer) ist der konkrete Anlass. Ein Verifikationspfad, der einen Chain-Zugriff voraussetzt, schließt diese Teilnehmer aus. Eine Chain-Abhängigkeit ist damit kein Rest-Risiko, das man in Kauf nimmt, sondern eine Marktgrenze.

---

## Entscheidung

Der Anker wird zu einer **Backend-Abstraktion**. Drei Backend-Klassen sind vorgesehen:

| Klasse | Beispiel | Eigenschaft |
|---|---|---|
| EVM-Chain | Base (heute kanonisch), Ethereum, Arbitrum | öffentlich, kostenpflichtig, jurisdiktionsabhängig |
| Solana | — | eigener Decode-Pfad, heute `beta` |
| chain-frei | RFC 3161 Trusted Timestamp, Transparency-Log-artig | ohne Krypto-Zugang nutzbar |

Zwei Festlegungen tragen die Entscheidung:

**1. Das Backend steht im Anchor-Record, nicht im DID-Identifier.** Der Identifier bleibt backend-stabil. Ein Agent kann das Anker-Backend wechseln, ohne dass seine DID bricht, und ohne dass historische Anker ungültig werden. Ein Identifier, der die Chain kodiert, würde die Bindung genau dort festschreiben, wo sie am teuersten zu lösen ist.

**2. Der Verifier liest das Backend aus dem Record.** Er entscheidet daraus, welchen Prüfpfad er nimmt. Ein Verifier ohne Zugang zum genannten Backend meldet den Ankerbefund als *nicht prüfbar* — er meldet ihn nicht als ungültig, und er fällt nicht still auf ein anderes Backend zurück.

---

## Begründung

Der Anker ist ein **datierter Zeitstempel über einen Hash**, keine Vertrauensquelle. Das Urteil selbst wird nachgerechnet, nicht nachgeschlagen: `recompute.ts:8-11` hält den MoltProof-Verify-Pfad ausdrücklich offline, `recomputeVerdict` bekommt die Actions als öffentliche Eingaben und leitet das Verdict daraus ab.

Wenn die Vertrauensfrage über Recompute beantwortet wird, trägt der Anker nur noch die Aussage „dieser Hash existierte zu diesem Zeitpunkt". Diese Aussage kann ein RFC-3161-Zeitstempel genauso liefern wie eine Chain. Damit ist eine Chain **eine** Backend-Klasse unter mehreren, und ein chain-freies Backend ist ihr für den Anker-Zweck gleichwertig.

Der Rest der Chain-Eigenschaften — Zensurresistenz, Permanenz, öffentliche Nachprüfbarkeit ohne benannten Betreiber — bleibt ein realer Vorteil. Er ist der Grund, Base als Default zu behalten, kein Grund, ihn zur Voraussetzung zu machen.

---

## Current State vs. Target (ehrlicher Deployment-Stand)

Der heutige Stand entspricht der Zielarchitektur **nicht**. Alle Zeilen (a)-verifiziert am 2026-08-04.

| Komponente | Heute | Ziel |
|---|---|---|
| MoltProof `/verify` | offline, kein Chain-Zugriff im Verify-Pfad (`recompute.ts:8-11`) | unverändert — bereits backend-unabhängig |
| `verify-package` | liest **pro Verifikation** on-chain gegen Base; bis zu 3 RPC-Calls (`verifier.ts:80,85,102`), Default-RPC `chain.ts:4` | offline-fähig, Backend aus Record |
| Anchor-Schreibpfad | Base hart verdrahtet, `chainId: 8453` Literal (`main.py:4324`, `anchor.py:227`) | Backend-Dispatch |
| Batch-Ebene | Naht vorhanden: `anchor_batch(conn, anchor_fn)` (`anchor.py:120`) | Einstiegspunkt für den Dispatch |
| Zweites Anker-Backend | `skale_anchor.py` = 18 Zeilen Config, keine Funktion | mindestens ein chain-freies Backend gebaut |
| Lese-Registry (MoltProof) | 7 Chains deklariert (`chains.ts:9-24`), Decoder nur EVM, Solana `beta` | Decoder je deklarierter Chain oder Deklaration zurücknehmen |

**Zur Entstehungsgeschichte, korrigiert.** Die Annahme, die Architektur habe Chain-Unabhängigkeit „seit dem Concept Paper 03/2026" vorgesehen, hält der Repo-Historie nicht stand. Verifiziert:

- `app/skale_anchor.py` erstmals committet **2026-05-09** (`649782c`) — das früheste Artefakt mit Chain-Agnostik-Absicht im Repo.
- `docs/specs/d3-cep-concept-paper.md` datiert **2026-06-02** (`25f3f15`), verwirft dort Single-Chain-Governance (Zeile 55).
- Im März 2026 gibt es genau einen `docs/`-Commit (`66f769a`, 2026-03-18, Swarm/ERC-8004) ohne Bezug zur Anker-Abstraktion.

Die belastbare Aussage lautet also: die Absicht ist seit **Mai 2026** dokumentiert, sie wurde nie implementiert, und sie war nach außen nicht sichtbar. Der Deployment-Default ist Base. Wer den Code liest, sieht eine chain-gebundene Verankerung — die Kritik in `kroftrust.md` trifft den Ist-Stand korrekt, auch wenn sie die Absicht verfehlt.

---

## Scope: Implementierung = FUTURE

Dieses ADR baut nichts. Es legt die Richtung fest, damit künftiger Code nicht gegen sie anwächst.

**Bau-Trigger — einer von beiden genügt:**
1. Erster Agent, Reseller oder Entwickler aus einer Jurisdiktion ohne legalen Krypto-Zugang.
2. Konkreter Bedarf an einem zweiten Anker-Backend aus anderem Grund (Kosten, Permanenz, Ausfallsicherheit).

Bis dahin bleibt Base der Default und der einzige gebaute Schreibpfad. Vorbauen auf Verdacht kostet Aufwand ohne Nutzer.

**Guard bis zum Trigger:** neuer Code, der einen Anker schreibt oder liest, darf `chainId`/RPC nicht zusätzlich hart verdrahten. Bestehende Verdrahtung bleibt unangetastet.

---

## Offene Punkte

- **RFC-3161-Backend-Design.** Welcher TSA, Vertrauensmodell, Umgang mit TSA-Zertifikatsablauf über den 10-Jahres-Horizont. Ein TSA ist ein benannter Betreiber — das Instanz-Argument aus 2605.06738 §4.1 gilt auch hier und muss beantwortet werden, statt umgangen.
- **Record-Schema `chain` → `backend` generalisieren.** Betrifft bestehende Anchor-Records; Migrations- und Rückwärtskompatibilitätsfrage offen.
- **`verify-package` offline-fähig machen.** Heute chain-gebunden. Ohne diesen Schritt bleibt die extern sichtbare Verifikation an Base gebunden, unabhängig davon, was der Anker sagt.
- **DID-Method-Doku klarstellen,** dass der Identifier backend-stabil ist. Solange das nicht dokumentiert ist, bleibt die Bindung für Außenstehende unterstellbar.
- **`chains.ts`-Deklaration und Decoder-Realität angleichen.** 7 deklarierte Chains bei einem EVM-Decoder ist eine Aussage, die der Code nicht einlöst.
- **`kroftrust.md` im Wortlaut prüfen** vor jeder öffentlichen Erwiderung. Für dieses ADR nicht gelesen (siehe Recon-Basis (b)).

---

## Consequences

**Positiv:**
- Die Anker-Ebene ist damit derselben Linie unterstellt wie die Governance-Ebene in `ADR-CEP-governance.md`. Eine spätere Chain-Migration bricht keine DID.
- Der jurisdiktionelle Ausschluss ist als Architektur-Frage benannt und nicht mehr implizit.

**Negativ / Kosten:**
- Ein chain-freies Backend führt einen benannten Betreiber ein (TSA). Das ist eine andere Vertrauensbindung, keine geringere — offen unter §Offene Punkte.
- Backend-Vielfalt erhöht die Verifier-Komplexität: jeder Verifier braucht Prüfpfade für jedes Backend, das er akzeptieren will.

**Neutral:**
- Bis zum Trigger ändert sich am Betrieb nichts. Base bleibt Default und einziger Schreibpfad.
