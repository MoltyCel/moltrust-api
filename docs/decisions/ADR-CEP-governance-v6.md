# ADR — CEP (Combined Enforcement Protocol) Governance (v6)

**Status:** **PROPOSAL** (Review-Runde 6, design-only, **Ziel: FREIGEBEN**). **HARD GATE:** blockiert die Implementierung von D3 **Komponente 3** (scharfes `enforce`-Umschalten). ACCEPTED erst nach 3-Reviewer-Konsens FREIGEBEN.
**Supersedes:** `docs/decisions/ADR-CEP-governance-v5.md` (v5, PR #139). v5 bleibt als **Audit-Trail** erhalten — NICHT löschen. Kette: `…-DRAFT.md` → v1 → v2 → v3 → v4 → v5 → **v6**.
**Datum:** 2026-06-04 · **Autor:** Lars Kroehl
**Review-Basis:** `~/moltstack/reviews/20260604_125154_CEP-governance-v5_review.md` (technical) — Verdikt **"ÜBERARBEITEN"**: alle 4 v5-Krypto-Fixes bestätigt korrekt, keiner der 3 offenen Punkte ein Design-Blocker; verbleibend = **Implementierungs-Detail-Specs**. v6 foldet diese ein + dokumentiert die akzeptierten Restrisiken explizit.
**Bezug:** `ADR-D3-mandate-enforcement-v3.md` (ACCEPTED). CEP gated ausschließlich D3 Komponente 3.

---

## Änderungen gegenüber v5 (Review-v5-Implementierungs-Specs → Resolution)

| technical-Review-v5 | Resolution in v6 |
|---|---|
| **§1 Kern-Spannung:** permissionless Verifikation vs. Key-Secrecy (permissionless → jeder kriegt Key → §1-Brute-Force-Schutz kollabiert) | **§A Staking-Gate** — wer verifiziert/vetot, staked; Stake = Key-Zugang. Kein privilegierter Rollen-Anker, aber Key nicht frei öffentlich. |
| Key-Distribution unspezifiziert | **§B Key-Wrapping** an gestakte Verifier (Verfügbarkeit ohne freien Leak). |
| DA-Challenge Spam/Griefing | **§C DA-Challenger-Stake** (Anti-Frivol). |
| Retention-Lock-Randfälle | **§D** überlappende Fenster + Challenge-kurz-vor-Ende präzisiert. |
| Treasury-Anti-Gaming | **§E Rate-Limits** gegen Erstattungs-Farming. |
| Standards-Präzision | **§Standards** DID-Core + VC-2.0 + HMAC-SHA-256-Default. |
| 3 offene Punkte = kein Blocker, aber Treasury-Bootstrap + Weak-Subjectivity branchenüblich | **§Akzeptierte-Restrisiken** explizit dokumentiert (bewusst akzeptiert, nicht übersehen). |

**ENTSCHIEDEN — nicht re-litigieren:** KONFLIKT-2-Säule (AI Act Art. 14 / Human Oversight = Protokollschicht, nicht Deployer; Aufsicht beim RP; kein Break-Glass; rechtliche Bestätigung = Dr. Kirchinger). Alle technischen Kernmechaniken v1–v5 (Verifikation>Produktion, signierte State-Transitions, 5-AND+Y-Cap, Score-Epochen, keyed Commitment+Cryptographic-Erasure, Retention-Lock, Treasury-Self-Replenishment, "Data-Unavailable=Invalid", DA-Challenge) **unverändert** — hier nur die Implementierungs-Präzisierungen.

---

## §A — Staking-Gate: Auflösung der §1-Kern-Spannung (permissionless vs. Key-Secrecy)

Das v5-Review benannte die zentrale Spannung: das keyed Commitment (§1 v5) schützt nur gegen Brute-Force, **solange der Key nicht frei öffentlich ist** — aber „jeder rechnet nach + vetot" (permissionless Verifikation, ein CEP-Kernwert) scheint genau das zu verlangen. **Auflösung: Staking-Gate.**

1. **Stake statt Rolle.** Wer verifizieren/vetoen will, hinterlegt einen **Stake (Deposit)**. Der Stake ist **permissionless zugänglich** — **jeder** kann staken; es gibt **keinen** designierten/privilegierten Verifier-Satz, keine Whitelist, keinen Gründer-/Instanz-Anker. Damit bleibt der CEP-Guard „keine privilegierte Rolle" gewahrt.
2. **Stake ⇒ Key-Zugang.** Gegen den hinterlegten Stake erhält der Verifier (via Key-Wrapping, §B) den Snapshot-Key `K_snap` und rechnet den `merkle_root` nach. **Skin-in-the-Game** ersetzt freien Key-Zugang: der Key liegt nicht öffentlich (kein Brute-Force über den ~67-DID-Raum), aber **jeder Bereitwillige mit Stake** kommt heran. „Permissionless" wird damit präzisiert zu **„permissionless-mit-Skin-in-the-Game"**, nicht „anonym-kostenlos".
3. **Key-Leak ist teuer/bestrafbar.** Ein Verifier, der `K_snap` öffentlich leakt (und damit §1 unterminiert), riskiert seinen Stake (Slashing, soweit beweisbar) — und hätte selbst keinen Vorteil, da er den Key bereits legitim hat. Der Leak-Anreiz ist gering (Skin-in-the-Game), das Restrisiko eines unbeweisbaren Leaks ist **benannt und akzeptiert** (s. §Akzeptierte-Restrisiken: ein einzelner krimineller Leak deanonymisiert *einen* Snapshot, nicht das System; der nächste Snapshot hat einen neuen Key).
4. **Veto-Gewicht unverändert** trust-gewichtet (signierte Score-Epoche); der Stake ist ein **Zugangs-/Anti-Spam-Gate**, kein Stimmgewicht (keine Plutokratie — Geld kauft Key-Zugang + Anti-Spam, nicht Veto-Macht).

> **Damit ist die §1-Spannung aufgelöst:** Brute-Force-Schutz (keyed Commitment) und permissionless Verifikation koexistieren über das Staking-Gate. Das ist die zentrale FREIGEBEN-relevante Resolution dieser Runde.

---

## §B — Key-Distribution: Key-Wrapping an gestakte Verifier

- **Asymmetrisches Key-Wrapping.** Jeder gestakte Verifier registriert einen Public Key. Der Snapshot-Producer (bzw. der KMS/Secret-Sharing-Layer, v5 §1) **wrapped** `K_snap` mit dem Public Key jedes berechtigten (= gestakten) Verifiers (Envelope-Encryption). `K_snap` wird **nie im Klartext on-chain** und nie an Nicht-Gestakte ausgeliefert.
- **Verfügbarkeit ohne freien Leak.** Der gewrappte Key ist über die DA-Schicht (Fallback-Array, v3) abrufbar; nur der jeweilige Verifier kann ihn mit seinem Private Key auspacken. So ist `K_snap` für **alle** gestakten Verifier verfügbar (öffentliche Nachrechenbarkeit im Verifier-Set) **ohne** einen einzelnen Klartext-Verteilpunkt.
- **Rotation pro Snapshot:** `K_snap` ist snapshot-spezifisch (v5 §1) → ein kompromittierter Key betrifft nur einen Snapshot; neue Verifier, die später staken, erhalten Wrapping für laufende/zukünftige Snapshots.

---

## §C — DA-Challenger-Stake (Anti-Frivol für DA-Challenge)

v5 §4 führte die DA-Challenge ein (Producer muss Rohdaten+Key binnen `T_dac` posten). v5-Review: ohne Anti-Spam kann ein böswilliger Challenger den Producer mit Upload-Kosten belasten (Griefing).

- **Challenger-Stake.** Wer eine DA-Challenge auslöst (Withholding-Vorwurf), hinterlegt einen **Stake**. Stellt der Producer die Daten+Key fristgerecht (`T_dac`) bereit → war die Challenge **frivol**, der Challenger-Stake wird (teilweise) an den Producer als Aufwandsentschädigung ausgekehrt. Stellt der Producer **nicht** bereit → Withholding bestätigt, **Producer-Slash** (speist Treasury, v5 §3), Challenger-Stake zurück + Belohnung.
- **Symmetrie:** beide Seiten haben Skin-in-the-Game → Frivol-Challenges und Withholding sind beide bestraft. Kein einseitiger Griefing-Vektor.

---

## §D — Retention-Lock-Randfälle (Präzisierung §2)

Die Hard-Invariante `min_retention_time ≥ challenge_window` (v5 §2) wird für Randfälle präzisiert:

1. **Überlappende Challenge-Fenster / mehrfach referenzierte Snapshots.** Ein Snapshot, dessen Daten in **mehreren** laufenden Fenstern referenziert sind (z. B. von einer Folge-Transition), bleibt bis zum **spätesten** relevanten Fenster-Ende retention-gelockt (`max` über alle referenzierenden Fenster). Die Löschung wird erst nach Ablauf **aller** Locks vollzogen. Ein Löschbegehren setzt einen **Pending-Erasure-Marker**, der beim letzten Lock-Ablauf automatisch ausgeführt wird (Cryptographic Erasure, v5 §1).
2. **Challenge kurz vor Fenster-Ende.** Wird eine (gestakte, §C) DA-/Inhalts-Challenge **kurz vor** Fenster-Ende eingereicht, **verlängert** sich das Retention-Lock (und das Veto-Fenster, v3 §DA-Fix) um die zur Bearbeitung der Challenge nötige Mindestzeit (`T_dac` + Auswertungspuffer) — Löschung erst danach. So kann eine Last-Minute-Challenge nicht durch ein auslaufendes Fenster „verhungern".
3. **Determinismus:** Retention-Status eines Snapshots = reine Funktion über (verankerte Fenster-Fristen, offene Challenges, Pending-Erasure-Marker) → von jedem Node identisch ausgewertet (kein Split-Brain über Löschzeitpunkte).

---

## §E — Treasury-Anti-Gaming (Rate-Limits gegen Erstattungs-Farming)

v5 §3 Treasury erstattet valides Veto-/DA-Challenge-Gas. v5-Review: ohne Anti-Gaming droht Farming der Erstattung.

- **Rate-Limits / Epoch-Caps:** Erstattungen pro Verifier-DID und pro Epoche gedeckelt; Gesamt-Auszahlung pro Epoche gedeckelt (kein Treasury-Drain in einer Runde).
- **Nur deterministisch-valide Vetos/Challenges** sind erstattungsfähig (v5 §3); **frivole** Challenges verlieren Stake (§C) → kein Profit aus Fake-Vetos.
- **Anti-Sybil:** Erstattungs-Berechtigung gekoppelt an die Sybil-geprüfte, trust-gewichtete Verifier-Identität (5-AND-Datenmodell) → kein Schwarm aus Wegwerf-DIDs farmt die Treasury.
- **Slashing-Caps:** Producer-Slash (§C/v5 §4) hat eine Obergrenze pro Vorfall → kein Anreiz für koordinierte Challenge-Schwärme gegen einen ehrlichen Producer.

---

## §Standards (Präzisierung — Fakten)

- **Commitment-Default = HMAC-SHA-256** (symmetrisch, operativ robust, geringes Implementierungsrisiko). Pedersen-Commitment bleibt **optionale** Alternative für spätere ZK-Erweiterungen, ist aber bei der Gruppen-/Kurvenwahl fehleranfälliger → **nicht** Default.
- **W3C DID Core** (DIDs) + **W3C Verifiable Credentials Data Model 2.0** (VCs, Vertical-Zuordnung) explizit referenziert.
- **W3C Bitstring Status List** (anonyme Revocation), **COSE RFC 9052/9053**, **IETF SCITT** (`draft-ietf-scitt-architecture`) — aus v5. **Draft-Status gekennzeichnet:** SCITT + Sidetree/ION sind **Implementierungsreferenzen/laufende Standardisierung**, keine finalen Normen → Implementierung folgt finalen RFCs, Breaking Changes eingeplant.
- **DIF Sidetree/ION** = Referenz-**Pattern** für die DA-Schicht (nicht harter Normstandard).

---

## §Akzeptierte-Restrisiken (BEWUSST akzeptiert, kein offener Blocker)

Der v5-Review stufte diese als **kein Design-Blocker** ein; v6 dokumentiert sie explizit als **bewusst akzeptierte** Restrisiken (damit der Re-Review sieht: akzeptiert, nicht übersehen):

1. **Treasury-Bootstrap-Zirkularität** — im Ramp-up gründer-finanziert + M-of-N-verwaltet, Übergang in protokollgesteuerte Treasury bei Erreichen der CEP-Metriken. **Branchenstandard** (Bitcoin/Ethereum/Cosmos starten alle bootstrap-zentralisiert → protokollgesteuert). Befristet, öffentlich verankert, mit objektivem Ausstiegspfad — derselbe akzeptierte Übergangszustand wie das gründer-gesetzte Enforcement.
2. **Weak-Subjectivity** — neue Nodes können krypto-gelöschte alte Snapshots nur via permanente Commitments + Consistency-Proofs (Integrität, nicht Inhalt) prüfen und vertrauen einem jüngeren verankerten Checkpoint. **PoS-Standard** (Ethereum Casper FFG, Polkadot). Notwendiger und akzeptierter Trade-off für DSGVO-Datenminimierung (Cryptographic Erasure).
3. **Unbeweisbarer Key-Leak durch einen gestakten Verifier** (§A) — deanonymisiert höchstens **einen** Snapshot (eigener Key pro Snapshot), nicht das System; Leak-Anreiz gering (Skin-in-the-Game). Akzeptiert.

---

## OFFENE PUNKTE (für Re-Review — keine Design-Blocker mehr erwartet)

- **CEP-2** Meta-Regel-Governance (unverändert).
- **§Rechtsfragen** (aus v4, an Dr. Kirchinger): eIDAS-2.0/TSP/QEAA, Art. 26 Joint-Controllership, NIS2-Einstufung, AI-Act-Rollenabgrenzung. **Rechts-, nicht Design-Fragen.**
- **CEP-3-Schwellen** (Lars-Geschäftsentscheidung, s. u.).

---

## CEP-3 — Schwellen N / M / X / Y / T_fb / T_dac / Stake-Höhen / Zeit (Lars-Geschäftsentscheidung, NICHT raten)

Platzhalter + Begründungs-Framework unverändert; erweitert um **Stake-Höhen** (Verifier-Stake §A, Challenger-Stake §C) und **Treasury-Rate-Limits/Epoch-Caps** (§E) als festzuschreibende Parameter. Stakes: hoch genug gegen Spam/Leak-Anreiz, niedrig genug für permissionless Zugänglichkeit.

---

## Konsequenz für D3 / Komponente 3

Unverändert: CEP blockiert Komponente 3 **NICHT** für `advisory`/`none`/`inherit` (DENY nur geloggt); das scharfe `enforce`-Umschalten hängt an der CEP-Entscheidung; Human Oversight des Hochrisiko-Einsatzes liegt beim RP (Konflikt-2-Säule). No-Downgrade + Default-zum-sicheren-Zustand fortbestehend.

---

## Nächste Schritte

1. **Re-Review (Ziel FREIGEBEN)** — prüfen, ob die §A-Staking-Gate-Auflösung trägt und keine Design-Blocker verbleiben; akzeptierte Restrisiken als bewusst akzeptiert anerkennen.
2. Bei FREIGEBEN-Konsens (3 Reviewer): Status-Flip **PROPOSAL → ACCEPTED** → erfüllt die CEP-Bedingung des D1-HARD-GATES für D3 Komponente 3.
3. **Rechtsberatung** (§Rechtsfragen) parallel — eIDAS/TSP, Art. 26, NIS2, AI-Act-Rollenabgrenzung.
4. **CEP-3-Schwellen + Stake-Höhen** als Geschäftsentscheidung festschreiben + verankern.
**HARD GATE bleibt bis ACCEPTED-Flip.**

---

## Konsequenzen / Trade-offs

- **Pro:** §1-Kern-Spannung aufgelöst (Staking-Gate: Brute-Force-Schutz + permissionless ohne privilegierte Rolle); Key-Distribution (Wrapping), DA-Anti-Spam (Challenger-Stake), Retention-Randfälle, Treasury-Anti-Gaming alle spezifiziert; Standards präzise (HMAC-Default, DID-Core/VC-2.0, Draft-Status markiert); Restrisiken bewusst dokumentiert. **Design im Kern vollständig.**
- **Contra / Risiko:** „permissionless" ist jetzt „permissionless-mit-Stake" (bewusst); Key-Management bleibt eine kritische Implementierungs-Komponente (KMS/Wrapping korrekt zu bauen); §Rechtsfragen weiter offen (Rechtsberatung); akzeptierte Restrisiken (Bootstrap-Zirkularität, Weak-Subjectivity) sind real, aber branchenüblich + benannt.
