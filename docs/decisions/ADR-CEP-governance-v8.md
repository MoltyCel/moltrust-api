# ADR — CEP (Combined Enforcement Protocol) Governance (v8 — FINALER Design-Schritt)

**Status:** **PROPOSAL** (Review-Runde 8, design-only, **FINALER Design-Schritt — schließt den Design-Loop bewusst**). **HARD GATE:** blockiert D3 **Komponente 3** (scharfes `enforce`-Umschalten). ACCEPTED erst nach 3-Reviewer-Konsens auf beiden Strängen.
**Supersedes:** `docs/decisions/ADR-CEP-governance-v7.md` (v7, PR #141). v7 bleibt als **Audit-Trail** erhalten. Kette: `…-DRAFT.md` → v1 → … → v7 → **v8**.
**Datum:** 2026-06-04 · **Autor:** Lars Kroehl
**Review-Basis:** technical-v7 `~/moltstack/reviews/20260604_143314_CEP-governance-v7_review.md` (ÜBERARBEITEN — Phase-2-ZK/HSM/DA-Cap unter-spezifiziert) + eu-compliance-v7 `~/moltstack/reviews/20260604_143528_CEP-governance-v7-eucompliance_review.md` (NACHBESSERN — **DSGVO-Fundamentalkonflikt strukturell GELÖST**, Rest = Rechts-Doku-Items).
**Bezug:** `ADR-D3-mandate-enforcement-v3.md` (ACCEPTED). CEP gated ausschließlich D3 Komponente 3.

---

## ZWECK DIESER VERSION (explizit)

**v8 bringt KEIN neues Design.** Nach 7 Runden ist die Architektur **konvergiert und von beiden Review-Linsen bestätigt**: die drei Fundamentalkonflikte sind gelöst —
1. **DSGVO-Permanenz** → keyed Commitment + **Cryptographic Erasure** (eu-v6/v7 bestätigt),
2. **AI Act Human Oversight** → **Protokoll-nicht-Deployer-Säule** (Konflikt-2, entschieden),
3. **Staking-Gate-DSGVO** → **gestaffelte Verifikation** (eu-v7: „Fundamentalkonflikt strukturell gelöst").

v8 hat **genau zwei** Aufgaben und **schließt danach den Design-Loop**:
- **(A)** die verbliebenen **technical**-Punkte in **objektive, verankerbare Kriterien** überführen (statt vager „Reife-Logik") und sie als **Implementation-Contract-Items** markieren — NICHT ausdesignen.
- **(B)** die verbliebenen **eu**-Punkte **explizit aus dem Design herausrouten** an den **Legal-Track** (Dr. Kirchinger) — als Legal-Process-Deliverables, **kein Design-Gate**.

**Was NICHT mehr passiert:** keine weitere Vertiefung von Implementierungs-Internas im ADR. ZK-Circuit-Design, HSM-Vendor-Strategie, DPIA-Berichte gehören in die **Bau-Phase + Rechtsberatung**, nicht in eine ADR-Version. Der Re-Review bewertet v8 daher gegen **objektive Kriterien + Routing**, nicht gegen Implementierungs-Tiefe.

**ENTSCHIEDEN — nicht re-litigieren:** Konflikt-2-Säule (AI Act Art. 14); alle Kernmechaniken v1–v7 (Verifikation>Produktion, signierte Transitions, 5-AND+Y-Cap, Score-Epochen, keyed Commitment HMAC-SHA-256 + Cryptographic Erasure, Retention-Lock + Hard-Cap, Treasury-Self-Replenishment + Anti-Gaming, Data-Unavailable=Invalid + DA-Challenge, gestaffelte Verifikation §A-v7, HSM-attestierte Erasure, K-Anonymität).

---

## (A) Technical-Restpunkte → OBJEKTIVE KRITERIEN + Implementation-Contract

Die technical-v7-Punkte werden NICHT ausdesignt, sondern an **objektive, verankerbare Gates** gekoppelt und als Implementation-Contract-Items (IC-#) festgeschrieben. Erfüllung = Bau-Phase, nicht ADR.

### A1 — Übergang permissioned → ZK-Verifikation (Phase 1 → Phase 2)
Statt „wenn Netz reif" gilt ein **objektives, on-chain verankerbares Gate** — ALLE Bedingungen erfüllt:
- **G1 (Performance-Bound):** Proof-Verifikationskosten der ZK-Schicht **≤ V_zkmax** (verankerter Parameter), UND **Proving-Zeit ≤ `T_dac`** (die ZK-Erzeugung muss innerhalb des DA-Challenge-Fensters abschließen — schließt den von technical-v7 benannten Liveness-Blocker „Proving-Zeit > Challenge-Window" deterministisch aus).
- **G2 (Audit-Gate):** **bestandener externer ZK-Circuit-Audit** (unabhängiger Krypto-Auditor; Audit-Attestation on-chain verankert).
- **G3 (Verankerung):** das Erfüllen von G1+G2 wird wie jeder CEP-Übergang als **signierte, verankerte Transition** festgehalten (CEP-4-Muster) → von jedem nachprüfbar, kein subjektiver Akt.
- **IC-1 (Implementation-Contract):** Die **ZK-Phase-2 selbst (Circuit-Familie SNARK/STARK, Constraints, Proving-System) ist ZIEL-Architektur, NICHT v8-Design** — sie wird in der Bau-Phase spezifiziert + auditiert. v8 legt nur das **Übergangs-Gate** fest. **Bis G1+G2 erfüllt sind, läuft Phase 1 (permissioned/DPA)** — der Enforcement-Pfad (Komponente 3) ist dadurch **nicht** ZK-blockiert.

### A2 — HSM-Topologie
- **Anforderung (objektiv):** **verteilte / Multi-Source-HSM** (keine Single-HSM- und keine Single-Vendor-Abhängigkeit) für `K_snap`-Generierung/-Wrapping/-Destruction-Attestation; Destruction-Attestations **on-chain verifizierbar** verankert.
- **IC-2:** konkrete HSM-Topologie, Vendor-Auswahl/Multi-Source-Strategie, Backup-/Replikations- + Threshold-Verfahren = **Implementation-Contract / Bau-Phase**, nicht ADR-Design. v8 fixiert nur die **Anforderung** (verteilt, Multi-Source, on-chain-attestierbar, kein SPOF/Lock-in).

### A3 — DA-Payload-Cap (Key-Wrapping)
- **Bound (objektiv):** gewrappte Key-Payload pro Snapshot **≤ P_damax** (verankerter Parameter); bei Verifier-Zahl über Cap → **Threshold-Encryption** (ein gewrappter Share-Satz statt O(V) Einzel-Wraps) als Richtung.
- **IC-3:** konkrete Cap-Höhe, Threshold-Schema (t-of-n), Paginierung = **Implementation-Contract / Bau-Phase**. v8 fixiert nur, dass das O(V)-Wachstum **gedeckelt** ist und Threshold-Encryption die Richtung ist. (Entfällt ohnehin in Phase 2: ZK braucht kein per-Verifier-Key-Wrapping.)

> **Damit sind A1–A3 als objektive Kriterien + Implementation-Contract-Items festgeschrieben — kein offenes Design.** Der Re-Review prüft die Tragfähigkeit der **Kriterien/Gates**, nicht die (Bau-Phase-)Implementierung.

---

## (B) eu-Restpunkte → LEGAL-TRACK (Legal-Process-Deliverables, KEIN Design-Gate)

Der eu-v7-Review bestätigte: der **DSGVO-Fundamentalkonflikt ist strukturell gelöst**; die verbleibenden Punkte sind **Rechts-/Compliance-Doku** — und das sind **genau die vorab an Dr. Kirchinger / Rechtsberatung ausgelagerten Rechtsfragen**. Sie werden hier **explizit aus dem Design-Scope herausgeroutet** und als **Legal-Process-Deliverables (LP-#)** geführt. **Keiner ist ein Design-Gate für ACCEPTED des CEP-Designs.**

| LP-# | Legal-Deliverable | Rechtsnorm | Eigner |
|---|---|---|---|
| **LP-1** | **Joint-Controller-Agreement(s)** MolTrust ↔ (permissioned) Verifier; Verantwortungs-/Pflichtenteilung | Art. 26 DSGVO | Dr. Kirchinger |
| **LP-2** | **Vollständige DPIA** (über die §D-K-Anonymitäts-Analyse hinaus: Key-Leak-Phase-1, Re-Identifikation, Supply-Chain) | Art. 35 + ErwG 90 | Dr. Kirchinger |
| **LP-3** | **SCCs + Transfer Impact Assessment** für Nicht-EU/EWR-Verifier (DPA allein deckt Drittlandtransfer nicht) | Art. 44 ff. / 46(2)(c) | Dr. Kirchinger |
| **LP-4** | **NIS2-Einrichtungs-Einstufung** (wichtig/wesentlich) + **Lieferketten-Sicherheitsbewertung** (Arweave/Multi-Chain ohne SLA) + Incident-Reporting-Fristen (24h/72h) | NIS2 Art. 21/23 | Dr. Kirchinger |
| **LP-5** | **Verzeichnis von Verarbeitungstätigkeiten** (beide Phasen) | Art. 30 DSGVO | Dr. Kirchinger / DPO |
| **LP-6** | **Rechtsgrundlagen-Wahl** Verifier-Key-Zugang: Art. 6(1)(b)+28 vs. **6(1)(f) berechtigtes Interesse** (eu-v7-Divergenz: 6(1)(f) flexibler) — final festlegen + LIA dokumentieren | Art. 6 DSGVO | Dr. Kirchinger |
| **LP-7** | **eIDAS-2.0 / TSP-/QEAA-Status + LoA** der ausgestellten VCs; ggf. QTSP-Kooperation; EUDI-Wallet-Interop | eIDAS 2.0 | Dr. Kirchinger |

> **Markierung für den Re-Review:** Diese Items sind **bewusst ausgeroutet** (nicht übersehen). Sie sind **Legal-Process-Deliverables**, die **parallel** zur Implementierung von der Rechtsberatung erbracht werden. Das **Design** ist DSGVO-konform strukturiert (Cryptographic Erasure, Hard-Cap, gestaffelte Verifikation mit vertraglicher Rechtsgrundlage, PII nie im Klartext on-chain). **Kein offener DESIGN-Blocker im regulatorischen Strang.**

---

## §Akzeptierte-Restrisiken (BEWUSST akzeptiert, kein Blocker)

Unverändert: Treasury-Bootstrap-Zirkularität (Branchenstandard), Weak-Subjectivity (PoS-Standard), unbeweisbarer Einzel-Key-Leak Phase 1 (1 Snapshot, DPA-Haftung; entfällt Phase 2).

---

## Implementation-Contract (Konsolidierung — vor/während D3 Komponente 3)

Bei ACCEPTED-Flip gilt diese Checkliste als verbindlicher Implementation-Contract (Bau-Phase, kein Design):
- **IC-1** ZK-Phase-2 Circuit-Spec + externer Audit (Gate G1/G2); bis dahin Phase 1.
- **IC-2** verteilte/Multi-Source-HSM + on-chain Destruction-Attestation.
- **IC-3** DA-Payload-Cap `P_damax` + Threshold-Encryption-Schema.
- **IC-4** KMS-Spec (HSM/Threshold/Audit-Logging), **IC-5** on-chain-beweisbares Key-Leak-Slashing, **IC-6** Challenger-Stake-Formel (aus v6/v7 §E).
- **LP-1…LP-7** Legal-Deliverables (Dr. Kirchinger), parallel.

---

## CEP-3 — Schwellen N / M / X / Y / T_fb / T_dac / T_ret_max / V_zkmax / P_damax / Stake-Höhen / Zeit (Lars-Geschäftsentscheidung, NICHT raten)

Platzhalter + Framework; erweitert um **V_zkmax** (ZK-Verifikationskosten-Schwelle, A1/G1) und **P_damax** (DA-Payload-Cap, A3).

---

## Konsequenz für D3 / Komponente 3

Unverändert: CEP blockiert Komponente 3 NICHT für `advisory`/`none`/`inherit` (DENY nur geloggt); scharfes `enforce` hängt an der CEP-Entscheidung; Human Oversight beim RP. **Phase 1 (permissioned) entkoppelt den Enforcement-Pfad von der ZK-Komplexität** → Komponente 3 ist nicht ZK-blockiert.

---

## Nächste Schritte (Design-Loop wird hier geschlossen)

1. **Zwei Re-Reviews auf v8** — technical (FREIGEBEN: tragen die objektiven Kriterien A1–A3; ZK/HSM/DA = Implementation-Contract, kein v8-Design) + eu (grün: kein offener DESIGN-Blocker; Rechts-Items als LP-# ausgeroutet).
2. Bei FREIGEBEN/grün auf **beiden** Strängen: **Status-Flip PROPOSAL → ACCEPTED** + Implementation-Contract (IC-1…6) + Legal-Track (LP-1…7) als Addendum → erfüllt die CEP-Bedingung des D1-HARD-GATES für D3 Komponente 3.
3. **Danach KEINE weiteren Design-ADR-Versionen** — offene Punkte sind Bau-Phase (IC) + Rechtsberatung (LP). Der Design-Loop ist geschlossen.
**HARD GATE bleibt bis ACCEPTED-Flip.**

---

## Konsequenzen / Trade-offs

- **Pro:** Design konvergiert + bewusst abgeschlossen; Restpunkte sauber getrennt in objektive Kriterien (A1–A3 / IC) + Legal-Process (LP); kein Infinite-Review-Loop mehr (ADR designt keine Implementierungs-Internas); Enforcement-Pfad nicht ZK-blockiert (Phase 1).
- **Contra / Risiko:** Erfolg hängt jetzt an **Ausführung** (Implementation-Contract korrekt bauen) + **Rechtsberatung** (LP-Deliverables), nicht mehr am Design; Phase-1-permissioned mildert „permissionless" temporär (bewusst, Ramp-up); ZK-Phase-2 bleibt anspruchsvolle Ziel-Komponente (Gate-gesteuert).
