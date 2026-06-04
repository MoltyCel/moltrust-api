# ADR — CEP (Combined Enforcement Protocol) Governance (v5)

**Status:** **PROPOSAL** (Review-Runde 5, design-only). **HARD GATE:** blockiert die Implementierung von D3 **Komponente 3** (scharfes `enforce`-Umschalten). Kein Code vor 3-Reviewer-Konsens.
**Supersedes:** `docs/decisions/ADR-CEP-governance-v4.md` (v4, PR #138). v4 bleibt als **Audit-Trail** erhalten — NICHT löschen. Kette: `…-DRAFT.md` → v1 → v2 → v3 → v4 → **v5**.
**Datum:** 2026-06-04 · **Autor:** Lars Kroehl
**Review-Basis:** `~/moltstack/reviews/20260604_103512_CEP-governance-v4_review.md` (technical: GPT-5 + Gemini 3.1 Pro + Perplexity Sonar Pro → Claude-Synthese) — Verdikt **"GRUNDLEGEND ÜBERDENKEN"**: Architektur gelobt, aber der PII-off-chain-Krypto-Teil aus v4 unter-spezifiziert (Salt über ~67 DIDs brute-forcebar). v5 foldet die 4 Krypto-Findings + Standards-Korrekturen.
**Bezug:** `ADR-D3-mandate-enforcement-v3.md` (ACCEPTED). CEP gated ausschließlich D3 Komponente 3.

---

## Änderungen gegenüber v4 (technical-Review-v4-Findings → Resolution)

| technical-Review-v4 (Krypto/DA) | Resolution in v5 |
|---|---|
| **Salt über kleinen DID-Raum (~67) wertlos** — Brute-Force-De-Anonymisierung | **§1 Keyed-Commitment**: HMAC-secret-key / Pedersen-Commitment + **Cryptographic Erasure**. |
| **Race: Löschung während Challenge-Fenster** bricht Nachrechenbarkeit | **§2 Retention-Lock**: `min_retention_time ≥ challenge_window` als Hard-Invariante. |
| **CEP-5 Treasury = verstecktes SPOF** (läuft leer → Verifier's Dilemma) | **§3 Treasury**: zirkuläres Self-Replenishment + Bootstrap M-of-N→CEP ohne Zirkularität. |
| **Data-Withholding DSGVO-getarnt** ("für Art.17 gelöscht" als Withholding-Tarnung) | **§4 DA-Integrity**: „Data-Unavailable = Invalid-Data" (slash/reject) + DA-Challenge + Kopplung an §2. |
| Standards-Fehler (COSE 8152, „W3C SCITT", fehlende Patterns) | **§Standards-Korrekturen**: RFC 9052/9053, IETF-SCITT, Bitstring Status Lists, Sidetree/ION. |

**ENTSCHIEDEN — nicht re-litigieren:** KONFLIKT-2-Säule (AI Act Art. 14 / Human Oversight) — MolTrust = Verifikations-/Enforcement-**Protokollschicht, nicht Deployer**; Aufsicht liegt beim Relying Party; Protokoll aufsichts-ermöglichend, kein Break-Glass (würde CEP-4-Personen-Anker reintroduzieren). Rechtliche Bestätigung = Dr. Kirchinger (§Rechtsfragen, aus v4). Unverändert.
Technische Kernmechaniken aus v1–v3 (Verifikation>Produktion, signierte State-Transitions, 5-AND+Y-Cap, signierte Score-Epochen, Permissionless-Fallback-Publisher, DA-Fallback-Array) + Konflikt-1-Grundsatz (PII off-chain, nur Commitments on-chain) **unverändert** — hier nur die Krypto-/DA-Präzisierungen.

---

## §1 — Keyed-Commitment + Cryptographic Erasure (Salt-Fix)

v4-Review: ein einfacher (gesalzener) Hash über einen **enumerierbaren ~67-DID-Raum** ist wertlos — 67 Hashes sind in Millisekunden brute-forcebar; ein öffentlicher Salt schützt nicht, ein „geheimer" Salt im off-chain-Payload leakt zwangsläufig.

**Resolution — keyed Konstruktion statt simplem Salt:**
1. **Commitment = keyed.** Pro Snapshot-Eintrag ein **HMAC mit geheimem Key** (`HMAC-SHA-256(K_snap, eintrag)`) **oder** ein **Pedersen-Commitment** (`C = g^m · h^r`, geheimer Blinding-Factor `r`). Der `merkle_root` baut über diese keyed Commitments. Ohne den geheimen Key/Blinding-Factor ist der Pre-Image-Raum **nicht** brute-forcebar — die ~67-DID-Enumeration läuft ins Leere.
2. **Key/Blinding-Factor NIE on-chain.** `K_snap` / `r` existieren **ausschließlich zusammen mit den off-chain-Rohdaten** (in einem kontrollierten KMS bzw. via Secret-Sharing). On-chain / Arweave liegt **nur** der `(merkle_root, data_uri)`-Commitment. Der Verifier erhält Key + Rohdaten **off-chain im Challenge-Fenster** (s. §2), rechnet den Root nach — die öffentliche **Nachrechenbarkeit bleibt**, aber **erst nach Erhalt des Off-Chain-Pakets**, nicht aus dem reinen On-Chain-Wert.
3. **Cryptographic Erasure.** Eine Löschung (Art. 17) löscht **Rohdaten + Key/Blinding-Factor** gemeinsam. Danach ist das verbleibende On-Chain-Commitment ein **kryptografisch bedeutungsloser Wert ohne Rückführbarkeit** — kein personenbezogenes Datum mehr. Krypto-Löschung (Schlüsselvernichtung) ist eine **anerkannte DSGVO-Löschmethode** (Art. 17 erfüllt, auch bei permanentem Commitment).
4. **Stärkt Konflikt-1 + Arweave-Permanenz.** Das Commitment darf jetzt **permanent** (Arweave) bleiben — es ist ohne Key bedeutungslos. Damit löst v5 den v3→v4-Trade-off sauberer: **Integritäts-/Append-Only-Beweis permanent** verankert, **PII via Key-Vernichtung löschbar**. (v4 gab Rohdaten-Permanenz auf; v5 braucht das für das *Commitment* nicht mehr.)

> **Abwägung:** Der geheime Key verschiebt einen Teil der „jeder rechnet sofort nach"-Eigenschaft auf „jeder berechtigte Verifier, der Key+Rohdaten im Fenster erhält, rechnet nach". Das ist der **bewusste** Preis für Brute-Force-Resistenz bei kleinem DID-Raum; das Honest-Verifier-/Veto-Modell bleibt intakt (Verifier bekommen das Off-Chain-Paket, §2/§4 garantieren dessen Verfügbarkeit im Fenster).

---

## §2 — Retention-Lock (Race Challenge-Fenster vs. Löschung)

v4-Review: Löschung der Rohdaten **während** eines aktiven Challenge-Fensters bricht die Nachrechenbarkeit.

**Resolution:**
1. **Hard-Invariante `min_retention_time ≥ challenge_window`.** Rohdaten + Key eines Snapshots sind **bis zum Ende seines Challenge-Fensters unlöschbar** (Retention-Lock). Löschbegehren während des Fensters werden **vorgemerkt und erst nach Fenster-Ende** vollzogen (Cryptographic Erasure, §1). Das ist DSGVO-konform: Art. 17(3) erlaubt Aufschub, solange die Verarbeitung zur Wahrung von Rechten/Pflichten bzw. eines berechtigten Interesses (hier: Integrität/Anfechtbarkeit eines laufenden Governance-Übergangs) erforderlich ist; das Retention-Fenster ist **eng begrenzt** (= Challenge-Dauer) und vorab festgeschrieben.
2. **Klare Semantik:** *legitime Löschung* = **nach** Fenster-Ende, via Krypto-Erasure; *böswilliges Withholding* = Nicht-Bereitstellung **während** des Fensters → behandelt als Invalid-Data (§4). Die beiden sind zeitlich sauber getrennt und nicht verwechselbar.
3. **`min_retention_time` ist Hard-Invariant-gekoppelt** (kein Unterschreiten durch Parameter-Verbiegung, analog Zeitschloss-Mindestdauer).

> **OFFEN (markiert): Weak-Subjectivity.** Sind Rohdaten alter Snapshots nach Fenster-Ende krypto-gelöscht, kann ein **neu hinzukommender Node die Historie nicht von Genesis voll nachrechnen** — nur die permanenten Commitments + Consistency-Proofs (Integrität, nicht Inhalt) prüfen. Das ist ein bewusstes Weak-Subjectivity-Modell (wie bei modernen PoS-Chains: neue Nodes vertrauen einem jüngeren, verankerten Checkpoint). **Als zu klärende Frage geführt** — ob Commitment-only-Verifikation alter Snapshots für das Sicherheitsmodell ausreicht, im Re-Review zu schärfen.

---

## §3 — CEP-5 Treasury: zirkuläres Self-Replenishment (SPOF-Fix)

v4-Review: eine **statische** Treasury, die nur Veto-Gas erstattet, läuft leer → das Verifier's Dilemma kehrt zurück; zudem ungeklärtes Key-Holding/Governance-Capture.

**Resolution-Richtung (Treasury-GOVERNANCE bleibt CEP-5, aber konkreter):**
1. **Zirkuläres Gebührenmodell (Self-Replenishment).** Die Treasury speist sich aus **laufenden Protokoll-Gebühren** (z. B. ein kleiner Anteil der x402/AAE-Submit-/Evaluate-Gebühren bzw. RP-Onboarding-Beiträge) statt aus einem einmaligen Topf. Auszahlung **nur** für deterministisch-valide Vetos/DA-Challenges; **invalide Challenges tragen ihre Kosten selbst** (kein Spam, kein Leerlaufen durch Fake-Vetos).
2. **Slashing speist zurück.** Ein wegen Withholding (§4) geslashter Producer-Deposit fließt in die Treasury → die ehrliche Verifikation finanziert sich teilweise aus dem bestraften Fehlverhalten.
3. **Bootstrap ohne Zirkularität.** Im Ramp-up ist die Treasury **gründer-finanziert + M-of-N-key-verwaltet** (derselbe befristete, öffentlich verankerte Übergangszustand wie das Enforcement selbst). Nach dem Übergang: Auszahlungsregeln + Parameter (Gebührenanteil, Erstattungshöhe) unterliegen **Timelock + trust-gewichtetem Veto + Hard-Invariants**; die Auszahlung selbst ist **deterministisch** (valides Veto ⇒ Erstattung), sodass **kein Einzelakteur eine berechtigte Erstattung verweigern** oder die Treasury abziehen kann.
4. **Governance-Capture-Schutz:** Treasury-Parameteränderung fällt unter dieselben Meta-Regel-Hard-Invariants (CEP-2); ein Abzug/eine Zweckentfremdung ist nicht regelkonform abbildbar.

> **Status CEP-5:** von „offener Anker" auf **konkrete Lösungsrichtung mit benanntem Restrisiko** gehoben — die **vollständige Entkopplung der Bootstrap-Treasury vom Gründer** bleibt die im Re-Review zu härtende Frage (zirkuläres Bootstrap-Problem: die CEP-verwaltete Treasury braucht das reife Netz, das sie mit absichern soll).

---

## §4 — DA-Integrity: „Data-Unavailable = Invalid-Data" + DA-Challenge (Anti-Gaming)

v4-Review: das off-chain-Auslagern reintroduziert Data-Withholding — jetzt **DSGVO-getarnt** („für Art. 17 gelöscht" als Vorwand für Withholding während eines Fensters).

**Resolution:**
1. **Invariante „Data-Unavailable = Invalid-Data".** Sind im Challenge-Fenster die Rohdaten+Key über **keinen** `data_uri`-Pfad (Fallback-Array, v3) auflösbar, gilt der Snapshot **als ungültig** (reject) — derselbe Effekt wie ein Root-Mismatch. Ein Producer kann durch Withholding **keinen** Übergang erzwingen (Default-zum-sicheren-Zustand).
2. **DA-Challenge mit Frist.** Auf signierten Withholding-Vorwurf eines Verifiers muss der Producer die Rohdaten+Key binnen **T_dac** (z. B. 1 h) über die DA-Schicht bereitstellen (on-chain DA-Challenge-Record). Versäumnis → Snapshot invalid + **Producer-Slash** (speist §3-Treasury). Erfolgreiche Bereitstellung → Veto-Fenster läuft weiter (ggf. auto-verlängert, v3 §DA-Fix).
3. **Kopplung an §2 macht es wasserdicht:** Withholding **während** des Fensters ist durch den **Retention-Lock** (§2) regelwidrig (Löschung ist erst danach erlaubt) → eine „DSGVO-Löschung" während des Fensters ist **per Invariante kein gültiger Grund**, sondern fällt unter Invalid-Data/Slash. Legitime Löschung erst nach Fenster-Ende. Die DSGVO-Tarnung ist damit ausgeschlossen.
4. **Pattern-Anlehnung:** DA-Behandlung folgt **DIF Sidetree/ION** („Missing Data = Invalid Data") — etabliertes Muster für genau dieses off-chain-DA-Problem.

---

## §Standards-Korrekturen (Fakten, direkt eingearbeitet)

- **COSE:** **RFC 9052 / 9053** (CBOR Object Signing and Encryption) — **RFC 8152 ist superseded**, nicht mehr referenzieren.
- **SCITT** ist ein **IETF**-Vorhaben (`draft-ietf-scitt-architecture`), **nicht W3C** — Bezeichnung im gesamten ADR entsprechend (Transparency-Service, Receipts, Inclusion-/Consistency-Proofs aus dem IETF-SCITT-Modell).
- **W3C Bitstring Status List** für **anonyme Revocation** von Vertical-/RP-Credentials — privatsphäre-freundlicher als ein Merkle-Baum über DIDs; für die VC-Revocation in V1 vorzusehen.
- **DIF Sidetree / ION** als Referenz-Pattern für die off-chain-DA-Schicht (§4) und für DID-Operationen-Anchoring.

---

## OFFENE KERN-ENTSCHEIDUNGEN (für Re-Review)

- **CEP-5 Treasury-Bootstrap-Entkopplung** (§3) — Lösungsrichtung konkret, vollständige Gründer-Entkopplung im Bootstrap noch zu härten.
- **Weak-Subjectivity** (§2) — reicht Commitment-only-Verifikation alter (krypto-gelöschter) Snapshots; neuer-Node-Checkpoint-Modell.
- **Key-Management für `K_snap`/Blinding** (§1) — KMS- vs Secret-Sharing-Ausgestaltung, Key-Verfügbarkeit für berechtigte Verifier vs Nicht-Leak.
- **CEP-2** Meta-Regel-Governance (unverändert).
- **§Rechtsfragen** (aus v4, an Dr. Kirchinger): eIDAS-2.0/TSP/QEAA, Art. 26 Joint-Controllership, NIS2-Einstufung, AI-Act-Rollenabgrenzung. Unverändert.

---

## CEP-3 — Schwellen N / M / X / Y / T_fb / T_dac / Zeit (Lars-Geschäftsentscheidung, NICHT raten)

Platzhalter + Begründungs-Framework unverändert aus v3/v4, erweitert um **T_dac** (DA-Challenge-Frist) und den Treasury-Gebührenanteil (§3) als festzuschreibende Parameter.

---

## Konsequenz für D3 / Komponente 3

Unverändert: CEP blockiert Komponente 3 **NICHT** für `advisory`/`none`/`inherit` (DENY nur geloggt); das scharfe `enforce`-Umschalten hängt an der CEP-Entscheidung; Human Oversight des Hochrisiko-Einsatzes liegt beim RP (Konflikt-2-Säule). No-Downgrade + Default-zum-sicheren-Zustand fortbestehend.

---

## Konsequenzen / Trade-offs

- **Pro:** DSGVO-Fix jetzt kryptografisch tragfähig (keyed Commitment + Cryptographic Erasure — Brute-Force-resistent bei kleinem DID-Raum, Art. 17 via Schlüsselvernichtung, Commitment darf permanent bleiben); Retention-Lock + „Data-Unavailable=Invalid" schließen die Withholding-/DSGVO-Tarnung wasserdicht; Treasury self-replenishing statt leerlaufend; Standards korrekt (RFC 9052/9053, IETF-SCITT, Bitstring Status List, Sidetree).
- **Contra / Risiko:** geheimer Key verschiebt „sofort nachrechenbar" auf „berechtigter Verifier mit Off-Chain-Paket" (bewusst); Key-Management = neue kritische Komponente; Weak-Subjectivity bei alten Snapshots; Treasury-Bootstrap-Entkopplung + §Rechtsfragen weiter offen.
