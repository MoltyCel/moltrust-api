# ADR — CEP (Combined Enforcement Protocol) Governance (v4)

**Status:** **PROPOSAL** (Review-Runde 4, design-only). **HARD GATE:** blockiert die Implementierung von D3 **Komponente 3** (scharfes `enforce`-Umschalten). Kein Code vor 3-Reviewer-Konsens.
**Supersedes:** `docs/decisions/ADR-CEP-governance-v3.md` (v3, PR #137). v3 bleibt als **Audit-Trail** erhalten — NICHT löschen. Kette: `…-DRAFT.md` → v1 → v2 → v3 → **v4**.
**Datum:** 2026-06-04 · **Autor:** Lars Kroehl
**Review-Basis:** `~/moltstack/reviews/20260604_100424_CEP-governance-v3-eucompliance_review.md` (eu-compliance: GPT-5 + Gemini 3.1 Pro + Perplexity Sonar Pro + **Mistral Large** → Claude-Synthese) — Verdikt **"GRUNDLEGEND ÜBERARBEITEN"**, zwei fundamentale EU-Rechts-Konflikte (DSGVO-Permanenz, AI-Act-Human-Oversight). v4 löst Konflikt 1 als Fix und schreibt Konflikt 2 als bewusste Scope-Grundsatzentscheidung positiv fest.
**Bezug:** `ADR-D3-mandate-enforcement-v3.md` (ACCEPTED). CEP gated ausschließlich D3 Komponente 3.

---

## Änderungen gegenüber v3 (eu-compliance-Findings → Resolution)

| eu-compliance-Finding | Resolution in v4 |
|---|---|
| **DSGVO Art. 17 vs Arweave-Permanenz** (4/4 kritisch — PII unlöschbar) | **§Konflikt-1 RESOLVED**: PII nie im Klartext on-chain; nur **salted Commitments / Merkle-Roots**, Rohdaten **off-chain (löschbar)**. |
| **AI Act Art. 14 — Human Oversight** (4/4 kritisch — autonomer Umschalt ohne Mensch) | **§Konflikt-2 SCOPE-GRUNDSATZENTSCHEIDUNG**: Human Oversight liegt beim **Relying Party** (Protokoll- vs Deployer-Rolle); MolTrust ist aufsichts-**ermöglichend**, nicht -ersetzend. Bewusst kein Break-Glass. |
| DSGVO Art. 22 (automatisierte DENY ohne Appeal) | **§Konflikt-1**: MolTrust liefert Verdict + reason + Audit-Trail; Appeal/Widerspruch liegt beim RP (Scope). |
| NIS2 Art. 21/23 | **§NIS2**: Multi-Chain als Lieferketten-Risiko + Incident-Reporting-Fähigkeit notiert; Einrichtungs-Einstufung = Rechtsfrage. |
| eIDAS 2.0 / TSP / VC-als-QEAA | **§Rechtsfragen**: an Rechtsberatung (Dr. Kirchinger) verwiesen — nicht im ADR gelöst. |
| Joint Controllership Art. 26 (Mistral) | **§Rechtsfragen**: durch Scope-Erklärung adressiert, als zu prüfender Rechtspunkt notiert. |
| Gelobt: SCITT-COSE-Sign1, 5-AND, Verification>Production | **behalten** (§Gelobt). |

Technische Kernmechaniken (CEP-1 Verifikation>Produktion, CEP-4 signierte Transitions, 5-AND+Y-Cap, Score-Epochen, Hard-Invariants, DA-Fallback-Array, Permissionless-Fallback-Publisher, SCITT-Profil) **unverändert aus v3** — hier nur die Compliance-Deltas.

---

## §Konflikt-1 — DSGVO/Arweave: RESOLVED (PII off-chain, nur Commitments on-chain)

v3-Review: permanente, unlöschbare Speicherung von DIDs / Vertical-Zuordnungen / Trust-Scores auf Arweave verletzt Art. 17 (Löschrecht) + Art. 5(1)(c) (Datenminimierung); pseudonyme DID ≠ anonym (ErwG 26, EuGH C-398/15).

**Resolution:**
1. **Keine PII im Klartext on-chain / auf Arweave.** RP-DIDs, Vertical-Zuordnungen und Trust-Score-Attributionen werden **nie** als Rohwert verankert. On-chain / Arweave liegt ausschließlich der **`(merkle_root, data_uri)`-Commitment** — der Root ist ein **salted Hash** über das Snapshot-Set, kein lesbares PII.
2. **Rohdaten off-chain, löschbar.** Die auflösbaren Snapshot-Rohdaten (das, was `data_uri` liefert) liegen in einem **off-chain, löschbaren** Speicher (kontrolliert, DSGVO-konform). Salt pro RP/Snapshot, sodass aus dem Commitment ohne die off-chain-Rohdaten **nichts** rekonstruierbar ist (kein Rainbow-/Brute-Force-Pfad bei kleinem DID-Raum).
3. **Art. 17 gewahrt.** Wird ein RP/Betroffener gelöscht, werden die **off-chain-Rohdaten gelöscht**; das verbleibende On-Chain-Commitment ist dann ein **bedeutungsloser Hash ohne Rückführbarkeit** — kein personenbezogenes Datum mehr. Konsistent mit dem bestehenden Architektur-Guard **„kein DSGVO-Volllog — Hash/Attestation-Anchoring statt Inhalt"** (ADR-D3-v3).
4. **„Verifikation > Produktion" bleibt intakt.** Ein Verifier rechnet weiterhin nach: er bezieht die **off-chain bereitgestellten Rohdaten** (Honest-Verifier-Modell, DA-Fallback-Array unverändert), rechnet den `merkle_root` nach und prüft die 5-AND-Bedingungen. Die Nachrechenbarkeit gilt **gegen das Commitment + die (off-chain, aber während des Challenge-Fensters verfügbare) Rohdaten** — nicht gegen permanent-öffentliche PII. Das schließt den DSGVO-Konflikt, ohne die dezentrale Verifikation aufzugeben.
   - **Trade-off ehrlich:** Die *Permanenz* der Rohdaten (10-Jahre-Nachrechenbarkeit beliebig alter Snapshots) wird zugunsten der Löschbarkeit aufgegeben. Verankert/permanent bleibt das **Commitment** (Integritäts-/Append-Only-Beweis via SCITT-Consistency-Proof); die **Rohdaten** sind nur im relevanten Challenge-/Aufbewahrungsfenster garantiert verfügbar. Das ist die bewusste, DSGVO-konforme Abwägung (Integritätsbeweis permanent, PII-Rohdaten löschbar).
5. **Art. 22 (automatisierte DENY).** MolTrust liefert das **Verdict + reason + vollständigen Audit-Trail** (signiert, SCITT). Das **Widerspruchs-/Appeal-Verfahren** über die Wirkung eines DENY liegt beim **Relying Party** (siehe §Konflikt-2 Scope) — der RP entscheidet über seinen Agenten und ist die Stelle mit der Betroffenen-Beziehung. Das Protokoll ermöglicht den Appeal (nachvollziehbares, anfechtbares Verdict), führt ihn aber nicht.

---

## §Konflikt-2 — AI Act Art. 14 Human Oversight: SCOPE-GRUNDSATZENTSCHEIDUNG (ENTSCHIEDEN, nicht re-litigieren)

v3-Review: der deterministische CEP-Umschalt ohne menschlichen Trigger verletze Art. 14 (menschliche Aufsicht). **Bewusste Grundsatzentscheidung (Lars): Human Oversight wird im Protokoll explizit NICHT implementiert** — positiv begründet:

### Scope-Erklärung — Protokollschicht, nicht Deployer
MolTrust/CEP ist eine **Verifikations- und Enforcement-PROTOKOLLSCHICHT**, **nicht** der Deployer eines Hochrisiko-KI-Systems. Analogie **TLS / PKI / TCP**: es gibt keine menschliche Freigabe pro Handshake oder pro Paket; eine **CA stellt Credentials aus + ein Verifikations-/Widerrufs-Regime bereit**, die inhaltliche Aufsicht über den *Einsatz* liegt beim nutzenden Dienst. CEP verhält sich zur Agenten-Autorisierung wie PKI zur Transportsicherheit.

### Wo die Art.-14-Pflicht liegt
Die **Human-Oversight-Pflicht trägt der Relying Party**, der das Protokoll einsetzt, um **seine** Agenten zu gaten. Der RP trifft die Entscheidung über seinen Agenten, hält die Betroffenen-/Einsatz-Beziehung und hat damit die Aufsichtspflicht über den Hochrisiko-Einsatz. CEP autorisiert keinen Einsatz — es **verifiziert Autorisierungs-Envelopes und schaltet eine Enforcement-Regelschicht**.

### Was MolTrust LIEFERT (aufsichts-ERMÖGLICHEND, nicht -ersetzend)
Damit der RP **seine** Aufsicht ausüben kann, stellt das Protokoll bereit:
- **verifizierbare, signierte Verdicts** (jeder Verdict nachrechen-/anfechtbar),
- **Default-DENY** (im Zweifel sicherer Zustand — keine stille Freigabe),
- **vollständiger, signierter Audit-Trail** (SCITT-COSE-Sign1),
- **Veto-Recht** (öffentlich, trust-gewichtet),
- **SCITT-Transparency** (Inclusion-/Consistency-Proofs).
Das ist ein **stärkeres** Compliance-Argument, als selbst eine (nicht-skalierende, personengebundene) Aufsicht zu führen: MolTrust maximiert die *Aufsichtsfähigkeit* der nutzenden Stelle statt sie zu usurpieren.

### Warum kein Break-Glass / Review-Window
Ein menschliches Break-Glass- oder Review-Window im Umschalt-Moment würde **genau den Personen-/Instanz-Anker reintroduzieren, den CEP-4 eliminiert** (10-Jahre-personenunabhängig). Das widerspräche dem Kern-Guard. Wer in seinem konkreten Kontext Human Oversight benötigt, **liefert sie auf RP-Ebene** — dort, wo die Hochrisiko-Verantwortung tatsächlich sitzt.

**Diese Scope-Abgrenzung ist eine ENTSCHIEDENE Säule, kein offener Punkt — im Re-Review als gegeben markieren, nicht re-litigieren.** (Offen bleibt nur die *rechtliche Bestätigung* der Rollenabgrenzung, §Rechtsfragen.)

---

## §NIS2 (Art. 21/23) — Protokoll-Ebene notiert

- **Multi-Chain-Abhängigkeit = Lieferketten-Risiko** (Art. 21(2)(d)): die ≥ 2 Anchor-Chains + DA-Storage + RPC-Endpoints sind als Lieferkette zu führen (Redundanz-Arrays bereits in v3 §Nebenpunkte).
- **Incident-Reporting-Fähigkeit:** DA-Challenges, Veto-Ereignisse, fehlgeschlagene/abweichende State-Transitions sind bereits signierte, verankerte Ereignisse → maschinenlesbarer Incident-Feed möglich (Art. 23-Meldefähigkeit als Designeigenschaft notiert).
- **Einstufung als wichtige/wesentliche Einrichtung = zu prüfen** (Geschäfts-/Rechtsfrage, nicht Design — §Rechtsfragen).

---

## §Rechtsfragen (nicht im ADR gelöst — an Rechtsberatung)

Folgende eu-compliance-Findings sind **Rechts-/Geschäftsfragen**, nicht Design — an **Dr. Kirchinger / Rechtsberatung**:
- **eIDAS 2.0 / TSP-Status:** Löst MolTrusts **VC-Ausgabe** (Vertical-Zuordnung, AAE-Credentials) den Status als (qualifizierter) **Vertrauensdiensteanbieter** aus? Sind die VCs **elektronische Attributbescheinigungen (EAA/QEAA)** im Sinne eIDAS 2.0 (EUDI-Wallet)? → Haftung/Zertifizierung.
- **DSGVO Art. 26 Joint Controllership:** Verhältnis MolTrust ↔ RP (und Permissionless-Fallback-Publisher). Durch die Scope-Erklärung (Protokoll vs Deployer) adressiert, aber **rechtlich zu bestätigen** (ggf. Joint-Controller-Agreement).
- **NIS2-Einrichtungs-Einstufung** (s. o.).
- **AI-Act-Rollenabgrenzung** (Protokollschicht vs Deployer/Sicherheitskomponente) — rechtliche Bestätigung der §Konflikt-2-Position.
- **Räumliche Anwendbarkeit** (CryptoKRI GmbH, Zürich; Marktortprinzip Art. 3 DSGVO) — bestätigt anwendbar, Ausgestaltung mit Beratung.

---

## §Gelobt — behalten (eu-compliance-Stärken, 4/4 bzw. 3/4)

- **SCITT-COSE-Sign1** = strategischer Anker für **AI Act Art. 12/13** (Record-Keeping/Transparenz) — standard-konforme Audit-Trails als Marktdifferenziator.
- **5-AND-Bedingungs-Framework** = diskriminierungsfreie, nachvollziehbare Alternative zu US-„Trust & Safety"-Black-Boxes.
- **„Verification > Production"** deckt sich mit dem EU-Ansatz nachrechenbarer Rechenschaftspflicht.

---

## OFFENE KERN-ENTSCHEIDUNGEN (für Re-Review)

### CEP-5 — TREASURY-GOVERNANCE (unverändert offen aus v3)
Treasury-**Existenz** löst das Verifier's Dilemma; **Verwaltung/Nachfinanzierung** ist ein potenzieller neuer Personen-/Instanz-Anker. Lösungsrichtung: Treasury nach Übergang **durch CEP-Regeln verwaltet** (Auszahlung nur für deterministisch-valide Vetos; Parameter via Timelock+Veto+Hard-Invariants). **Status: zu klären, KRITISCH.**

### CEP-2 — Meta-Regel-Governance
Unverändert (Timelock + Veto + Hard-Invariants; self-amendment-Schutz).

> **NICHT offen (ENTSCHIEDEN):** Human-Oversight-Scope (§Konflikt-2) — Protokoll- vs Deployer-Rolle ist eine festgeschriebene Säule, nur die rechtliche Bestätigung steht aus (§Rechtsfragen).

---

## CEP-3 — Schwellen N / M / X / Y / T_fb / Zeit (Lars-Geschäftsentscheidung, NICHT raten)

Platzhalter + Begründungs-Framework unverändert aus v3.

---

## Konsequenz für D3 / Komponente 3

CEP blockiert Komponente 3 **NICHT** für `advisory`/`none`/`inherit` (DENY nur geloggt). Das scharfe `enforce`-Umschalten hängt an der CEP-Entscheidung. M-of-N-Signatur (ADR-D3-v3) bleibt Ramp-up-Mechanik + Publisher-Rolle; CEP ersetzt sie nach Übergang. No-Downgrade + Default-zum-sicheren-Zustand in beiden Phasen. **Human Oversight des Hochrisiko-Einsatzes liegt beim RP, nicht im Protokoll (§Konflikt-2).**

---

## Nächste Schritte

1. **Re-Review** — adversarisch v. a. **§Konflikt-1** (Salt/Commitment gegen kleinen DID-Raum, Rohdaten-Verfügbarkeit im Challenge-Fenster vs Löschbarkeit) + **CEP-5 (Treasury-Governance)**. §Konflikt-2-Scope ist gegeben.
2. **Rechtsberatung** (§Rechtsfragen) parallel — eIDAS/TSP, Art. 26, NIS2-Einstufung, AI-Act-Rollenabgrenzung.
3. **CEP-3-Schwellen** als Geschäftsentscheidung festschreiben + verankern.
4. Status-Flip **PROPOSAL → ACCEPTED** erst nach Konsens. **HARD GATE bleibt.**

---

## Konsequenzen / Trade-offs

- **Pro:** DSGVO-Konflikt strukturell gelöst (PII off-chain/löschbar, nur Commitments permanent — konsistent mit bestehendem Hash-Anchoring-Guard); AI-Act-Position klar + positiv begründet (aufsichts-ermöglichend > -ersetzend, kein Personen-Anker); regulatorische Stärken (SCITT/5-AND/Verification>Production) bleiben.
- **Contra / Risiko:** §Konflikt-2 ist eine **rechtlich zu bestätigende** Scope-Position (Restrisiko, falls Aufsicht die Protokoll-/Deployer-Abgrenzung anders sieht); Permanenz der Rohdaten zugunsten Löschbarkeit aufgegeben (alte Snapshots nur via Commitment integritäts-, nicht voll-nachrechenbar); CEP-5 weiter offen; eIDAS/TSP-Haftung ungeklärt bis Rechtsberatung.
