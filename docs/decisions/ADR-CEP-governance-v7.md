# ADR — CEP (Combined Enforcement Protocol) Governance (v7)

**Status:** **PROPOSAL** (Review-Runde 7, design-only, **Ziel: beide Stränge grün — technical FREIGEBEN halten + eu-compliance grün**). **HARD GATE:** blockiert D3 **Komponente 3** (scharfes `enforce`-Umschalten). ACCEPTED erst nach 3-Reviewer-Konsens auf BEIDEN Strängen.
**Supersedes:** `docs/decisions/ADR-CEP-governance-v6.md` (v6, PR #140). v6 bleibt als **Audit-Trail** erhalten — NICHT löschen. Kette: `…-DRAFT.md` → v1 → … → v6 → **v7**.
**Datum:** 2026-06-04 · **Autor:** Lars Kroehl
**Review-Basis:**
- technical v6: `~/moltstack/reviews/20260604_125154_CEP-governance-v5_review.md` (→ v6) + v6-Lauf **FREIGEBEN mit Implementierungs-Verträgen**.
- eu-compliance v6: `~/moltstack/reviews/20260604_142048_CEP-governance-v6-eucompliance_review.md` — Verdikt **NACHBESSERN**: v4-Art.-17-Blocker geschlossen, ABER **neuer DSGVO-Konflikt durch das v6-Staking-Gate** (anonymer Key-Zugang ohne Rechtsgrundlage) + 3 Bedingungen.
**Bezug:** `ADR-D3-mandate-enforcement-v3.md` (ACCEPTED). CEP gated ausschließlich D3 Komponente 3.

---

## Änderungen gegenüber v6 (eu-compliance-v6-Findings → Resolution)

| eu-compliance-v6-Finding | Resolution in v7 |
|---|---|
| **🔴 Staking-Gate-Key-Zugang ohne DSGVO-Rechtsgrundlage** (anonyme Staker erhalten K_snap-Zugang zu pseudonymen PII; "finanzieller Stake ≠ Art.-6-Rechtsgrundlage", kein Art.-26-Joint-Controller) | **§A Gestaffelte Verifikation**: Ramp-up = **permissioned/DPA-gebundene** Verifier (vertragliche Rechtsgrundlage Art. 6(1)(b)/28); Ziel = **ZK-Verifikation ohne Key-Herausgabe**. |
| HSM-beweisbare Erasure (Bedingung) | **§B**: Cryptographic Erasure **HSM-attestiert** (nachweisbar, nicht behauptet). |
| Hard Cap Retention (Bedingung) | **§C**: `challenge_window ≤ min_retention_time ≤ T_ret_max` (obere Grenze als Hard-Invariante). |
| K-Anonymität Commitments (Bedingung) | **§D**: formale K-Anonymitäts-Analyse des keyed Commitment über den ~67-DID-Raum. |
| **technical-v6 approve-with-nits (4 Impl-Verträge)** | **§E**: KMS-Spec, DA-Payload-Cap, on-chain-beweisbares Key-Leak-Slashing, Challenger-Stake-Formel. |

**ENTSCHIEDEN — nicht re-litigieren:** KONFLIKT-2-Säule (AI Act Art. 14 / Human Oversight = Protokollschicht, nicht Deployer; Aufsicht beim RP; rechtliche Bestätigung = Dr. Kirchinger). Alle technischen Kernmechaniken v1–v6 (Verifikation>Produktion, signierte State-Transitions, 5-AND+Y-Cap, Score-Epochen, keyed Commitment HMAC-SHA-256 + Cryptographic Erasure, Retention-Lock, Treasury-Self-Replenishment + Anti-Gaming, "Data-Unavailable=Invalid" + DA-Challenge, Standards) **unverändert** — hier nur die DSGVO-Rechtsgrundlage des Key-Zugangs + die Bedingungen + Impl-Verträge.

---

## §A — Gestaffelte Verifikation: Key-Zugang DSGVO-sauber (KERN-FIX)

Das eu-v6-Review: das v6-Staking-Gate gibt **anonymen** Stakern Key-Zugang zu pseudonymen PII — finanzieller Stake ist **keine** gültige Art.-6-Rechtsgrundlage. v7 löst das über **dasselbe Ramp-up-Muster** wie der CEP selbst (Gründer→trust-gewichtet, did:moltrust→did:web, advisory→enforce):

### Phase 1 (Ramp-up) — permissioned, vertraglich gebundene Verifier
- Key-Zugang (`K_snap` via Key-Wrapping, v6 §B) **nur** für Verifier, die einen **Auftragsverarbeitungs-/Data-Processing-Vertrag (DPA)** mit MolTrust geschlossen haben. Rechtsgrundlage = **vertraglich** (Art. 6(1)(b) + Art. 28 Auftragsverarbeitung; Pflichten-/Verantwortungsteilung als Art.-26-Joint-Controller-Agreement, wo zutreffend) — **nicht** der finanzielle Stake.
- Der Stake (v6 §A) bleibt als **Anti-Spam-/Skin-in-the-Game-Gate** bestehen, ist aber **nicht** die Rechtsgrundlage: erst DPA **dann** Stake. Identifizierte, gebundene Verifier → Art. 13/14-Empfänger sind benannt, Art. 6 erfüllt.
- **Sofort DSGVO-sauber** und **kein ZK-Engineering-Blocker** für D3 Komponente 3: der enforce-Chokepoint kann scharf gehen, sobald die CEP-Bedingungen erfüllt sind, ohne dass die (anspruchsvolle) ZK-Schicht fertig sein muss.

### Phase 2 (Ziel-Architektur) — ZK-Verifikation ohne Key-Herausgabe
- Verifier **beweist** die korrekte 5-AND-Nachrechnung über den verankerten Snapshot **per Zero-Knowledge-Proof**, **ohne** `K_snap` oder die pseudonymen PII (DIDs/Verticals/Scores) je zu erhalten. Damit: **permissionless** (jeder kann verifizieren) **+ Brute-Force-Schutz** (kein Key-Zugang nötig) **+ DSGVO** (keine PII-Offenlegung an Verifier) — gleichzeitig. Der eu-v6-Konflikt entfällt strukturell, weil **gar keine PII mehr an Verifier herausgegeben** wird.
- **WICHTIG (Abgrenzung):** Das ist **ZK für die VERIFIKATIONS-Ebene** (privatsphäre-wahrender Korrektheitsbeweis), **NICHT** das in v1 bewusst verworfene **"ZK für Governance-Autorität"** (das löste fälschlich Privacy statt Governance-Autorität). Hier bleibt die Governance-Autorität bei den objektiven Bedingungen (5-AND) + Multi-Chain-Anchoring + trust-gewichtetem Veto; ZK ersetzt **nur** den Key-/PII-Austausch in der Nachrechnung. **Kein Widerspruch zur CEP-Grundsatzentscheidung.**

### Übergang Phase 1 → Phase 2
An **dieselbe Reife-Logik** gekoppelt wie der CEP-Ramp-up selbst (objektive, verankerte Bedingungen; vgl. 5-AND). Sobald die ZK-Verifikationsschicht produktionsreif + auditiert ist und das Netz die Reife-Schwelle erreicht, löst Phase 2 die permissioned Phase 1 ab. Phase 1 ist der **befristete, DSGVO-konforme Übergangszustand** mit objektivem Ausstiegspfad — exakt das etablierte CEP-Muster.

> **Ergebnis:** eu-🔴 ist in **beiden** Phasen geschlossen — Phase 1 via vertraglicher Rechtsgrundlage (sofort), Phase 2 via PII-Nicht-Offenlegung (strukturell). Phase 1 entkoppelt den ACCEPTED-/Enforcement-Pfad von der ZK-Komplexität.

---

## §B — HSM-attestierte Cryptographic Erasure (eu-Bedingung)

Cryptographic Erasure (v5 §1) wird **HSM-gestützt nachweisbar**:
- `K_snap` (und Verifier-seitige gewrappte Kopien, Phase 1) werden in **Hardware-Sicherheitsmodulen** generiert/gehalten; Löschung = **HSM-attestierte Schlüsselvernichtung** (signiertes Destruction-Attestation-Log), nicht bloß behauptetes „delete".
- Erfüllt den ErwG-26-/Behörden-Maßstab („nachweisbare Unwiederbringlichkeit"): nach Attestierung sind **alle** vernünftigerweise nutzbaren Mittel zur Rückführung ausgeschlossen → Commitment ist **anonym** (kein personenbezogenes Datum mehr), Art. 17 erfüllt auch bei permanentem Commitment.
- Phase-1-DPA verpflichtet permissioned Verifier zur **HSM-Haltung + Co-Löschung** gewrappter Keys; Phase-2-ZK gibt **gar keine** Keys heraus → Erasure-Garantie ist dort trivial (kein Dritt-Key existiert).

---

## §C — Hard Cap für Retention-Fenster (eu-Bedingung)

Die Retention-Invariante (v5 §2 / v6 §D) bekommt eine **obere** Grenze:
`challenge_window ≤ min_retention_time ≤ T_ret_max` — als **Hard-Invariante**. `T_ret_max` deckelt die maximale Aufbewahrungsdauer (auch bei überlappenden/kaskadierenden Fenstern, v6 §D `max()`): die Summe/Verlängerung darf `T_ret_max` **nicht** überschreiten. Das stützt den Art.-17(3)(e)-Aufschub **eng begründet** (berechtigtes Interesse = Integrität eines laufenden Governance-Übergangs, **knapp + vorab festgeschrieben befristet**) und schließt Gemini's „kaskadierende Fenster = Umgehung"-Risiko. `T_ret_max` ist Lars-Parameter (CEP-3), Hard-Invariant-gekoppelt (kein Überschreiten via Parameter-Verbiegung).

---

## §D — Formale K-Anonymitäts-Analyse der Commitments (eu-Bedingung)

Explizite Analyse für den **kleinen (~67-DID-)Inputraum**:
- **Solange der Key existiert** ist das keyed Commitment (HMAC-SHA-256, v5/v6) **pseudonym** (DSGVO gilt) — und über den Key (nur off-chain, Phase-1-permissioned bzw. Phase-2-nie-herausgegeben) auf einen RP rückführbar.
- **Ohne den Key** (Nicht-Berechtigte / nach Cryptographic Erasure) ist der ~67-DID-Raum **nicht** brute-forcebar (keyed → kein Pre-Image-Enumerieren), d. h. das Commitment erfüllt **K-Anonymität gegenüber Nicht-Schlüssel-Inhabern**: kein Mittel, einen Commitment-Wert einem der 67 DIDs zuzuordnen. → On-Chain-Wert ist gegenüber der Öffentlichkeit **anonym** i. S. v. ErwG 26.
- **Nachweis-Anforderung (Implementierungs-Vertrag):** formaler K-Anonymitäts-/Re-Identifikations-Report (inkl. Hintergrundwissen-/Linkage-Analyse über die öffentlichen Anchor-Daten) als Teil der DPIA (Art. 35). Die Konstruktion ist tragfähig; der **formale Report** ist auszuarbeiten.

---

## §E — Technical-Implementierungs-Verträge (approve-with-nits aus v6)

Die 4 v6-Nits als Implementation-Contract (vor/während Komponente-3-Code):
1. **KMS-Sicherheitsspec:** HSM / Threshold-Cryptography / Audit-Logging für `K_snap`-Generierung, -Wrapping (§B), -Destruction-Attestation.
2. **DA-Payload-Cap fürs Key-Wrapping:** Obergrenze Verifier/Snapshot bzw. Paginierung gegen O(V)-Payload-Wachstum auf der DA-Schicht (entfällt in Phase 2, da ZK kein per-Verifier-Wrapping braucht).
3. **Key-Leak-Slashing nur on-chain-beweisbar:** symmetrischer `K_snap` → Traitor-Tracing schwer → Slashing **nur** für on-chain-beweisbare Vergehen; sonst Abschreckung + Skin-in-the-Game + per-Snapshot-Blast-Radius (1 Snapshot). DPA (Phase 1) ergänzt **vertragliche** Leak-Haftung.
4. **Challenger-Stake-Berechnungsformel** (§C/v6 §C): konkret zu spezifizieren (Bezug Producer-Aufwand/DA-Kosten).

---

## §Akzeptierte-Restrisiken (BEWUSST akzeptiert, kein offener Blocker)

Unverändert aus v6 (im technical-v6-Review als kein-Blocker bestätigt):
1. **Treasury-Bootstrap-Zirkularität** — Branchenstandard (BTC/ETH/Cosmos bootstrap→protokollgesteuert), befristet + objektiver Ausstiegspfad.
2. **Weak-Subjectivity** — PoS-Standard (Casper FFG/Polkadot), notwendiger DSGVO-Datenminimierungs-Trade-off.
3. **Unbeweisbarer Einzel-Key-Leak** (Phase 1) — 1 Snapshot betroffen (Key pro Snapshot), DPA-Haftung + Skin-in-the-Game; entfällt in Phase 2 (kein Key-Austausch).

---

## OFFENE PUNKTE (für Re-Review)

- **§Rechtsfragen** (an Dr. Kirchinger, aus v4/v6): eIDAS-2.0/TSP/QEAA, Art. 26 Joint-Controllership (Phase-1-DPA adressiert teils), NIS2-Einrichtungs-Einstufung + Lieferketten-Audit (Arweave/Multi-Chain ohne SLA), AI-Act-Rollenabgrenzung. **Rechts-, nicht Design-Fragen.**
- **CEP-2** Meta-Regel-Governance (unverändert).
- **ZK-Verifikationsschicht (Phase 2)** = anspruchsvolle, zu auditierende Ziel-Komponente — **kein** Sofort-Blocker (Phase 1 trägt den Enforcement-Pfad), aber technisch nicht trivial (ZK-Membership/Recompute-Proof über das Snapshot-Set).
- **CEP-3-Schwellen** (Lars-Geschäftsentscheidung).

---

## CEP-3 — Schwellen N / M / X / Y / T_fb / T_dac / T_ret_max / Stake-Höhen / Zeit (Lars-Geschäftsentscheidung, NICHT raten)

Platzhalter + Framework; erweitert um **T_ret_max** (§C) und die Phase-1→Phase-2-Reife-Schwelle (§A).

---

## Konsequenz für D3 / Komponente 3

Unverändert: CEP blockiert Komponente 3 **NICHT** für `advisory`/`none`/`inherit` (DENY nur geloggt); das scharfe `enforce`-Umschalten hängt an der CEP-Entscheidung; Human Oversight beim RP (Konflikt-2). **Wichtig:** Phase 1 (permissioned Verifikation) entkoppelt den Enforcement-Pfad von der ZK-Komplexität → Komponente 3 ist nicht ZK-blockiert.

---

## Nächste Schritte

1. **Zwei Re-Reviews auf v7** — technical (FREIGEBEN halten; permissioned-Ramp-up + ZK-Ziel mechanisch tragfähig, ZK als Ziel-nicht-Sofort = kein Blocker) + eu-compliance (grün; permissioned/DPA-Rechtsgrundlage schließt den Stake-als-Rechtsgrundlage-Konflikt; HSM-Erasure/Hard-Cap/K-Anonymität bestätigt).
2. Bei FREIGEBEN/grün auf **beiden** Strängen: Status-Flip **PROPOSAL → ACCEPTED** + Implementation-Contract (§E + §B/§C/§D-Nachweise) → erfüllt die CEP-Bedingung des D1-HARD-GATES.
3. **Rechtsberatung** (§Rechtsfragen) parallel.
4. **CEP-3-Schwellen** festschreiben + verankern.
**HARD GATE bleibt bis ACCEPTED-Flip.**

---

## Konsequenzen / Trade-offs

- **Pro:** eu-🔴 (Staking-Key-Zugang) in beiden Phasen geschlossen — Phase 1 vertragliche Rechtsgrundlage (sofort, kein ZK-Blocker), Phase 2 PII-Nicht-Offenlegung (strukturell); HSM-Erasure beweisbar; Retention Hard-Cap; K-Anonymität analysiert; Impl-Verträge benannt. **Beide Stränge adressiert.**
- **Contra / Risiko:** Phase 1 hat identifizierte/gebundene Verifier (mildert „permissionless" temporär — bewusst, Ramp-up); Phase-2-ZK ist anspruchsvoll + zu auditieren (Ziel, nicht sofort); §Rechtsfragen (eIDAS/NIS2/Art.26) bleiben Rechtsberatung; akzeptierte Restrisiken real, aber branchenüblich.
