# ADR — CEP (Combined Enforcement Protocol) Governance (v3)

**Status:** **PROPOSAL** (Review-Runde 3, design-only). **HARD GATE:** blockiert die Implementierung von D3 **Komponente 3** (scharfes `enforce`-Umschalten). Kein Code auf Komponente 3 vor 3-Reviewer-Konsens.
**Supersedes:** `docs/decisions/ADR-CEP-governance-v2.md` (v2, PR #136). v2 bleibt als **Audit-Trail** erhalten — NICHT löschen. Kette: `ADR-CEP-governance-DRAFT.md` → v1 → v2 → **v3**.
**Datum:** 2026-06-04 · **Autor:** Lars Kroehl
**Review-Basis:** `~/moltstack/reviews/20260604_094710_CEP-governance-v2_review.md` (technical: GPT-5 + Gemini 3.1 Pro Preview + Perplexity Sonar Pro → Claude-Synthese) — Verdikt **"ÜBERARBEITEN"** (Kernarchitektur einstimmig gelobt: CEP-1 = Optimistic-Rollup-Sicherheitsmodell, CEP-4 Split-Brain vollständig eliminiert, Score-Epochen-DoS sauber, 5-AND/Hard-Invariants wasserdicht). Keine fundamentale Lücke mehr — 4 **operative** Fixes. v3 foldet diese als gelöste Design-Entscheidungen ein.
**Bezug:** `ADR-D3-mandate-enforcement-v3.md` (ACCEPTED). CEP gated ausschließlich D3 Komponente 3.

---

## Änderungen gegenüber v2 (Review-v2-Fixes → Resolution)

| technical-Review-v2 (operativ) | Resolution in v3 |
|---|---|
| **Arweave DA-Liveness-Loch** im Challenge-Fenster (Gateway-503/Zensur, Finality ~2 min vs Timelock-Start) | **§DA-Fix**: `data_uri` als **Fallback-Array** (`arweave://` + `ipfs://` + https-Gateway); **automatische Zeitschloss-Verlängerung** bei > X_av % Nichtverfügbarkeit; Timelock-Start erst nach Arweave-Bestätigungstiefe. |
| **Verifier's Dilemma** (Veto kostet Gas auf K Chains, kein Reward → niemand vetot) | **§Verifier-Ökonomie**: Veto-**Erstattung aus vorfinanzierter Treasury**; Online-/Anreiz-Annahmen explizit; Veto-TX-Zensur via Multi-Chain/Multi-Relay. **Treasury-Governance bleibt OFFEN** (§CEP-5). |
| **Ramp-up Liveness-SPOF** (Gründer-Sole-Publisher kann Übergang per Nicht-Publizieren ewig blockieren — Griefing) | **§Permissionless-Fallback**: nach T_fb Tagen ohne Gründer-Snapshot darf **jede Node** bei erfüllten 5-AND publizieren. Safety bleibt (jeder rechnet nach). + **kanonische Auswahlregel** bei konkurrierenden Transitions. |
| **SCITT nur als Label** | **§SCITT-Profil**: konkret **COSE-Sign1**-Envelope, **Transparency-Service-Rolle**, **Inclusion- + Consistency-Proofs** — echtes Profil, keine "Anlehnung". |
| Nebenpunkte (V1-Latenz, RPC-Redundanz, VC-Profil) | **§Nebenpunkte**. |

**Status der v2-Kernmechaniken (vom Review bestätigt, in v3 unverändert übernommen):** CEP-1 Verifikation > Produktion · CEP-4 signierte State-Transitions · 5-AND + Y-Cap · signierte Score-Epochen · Meta-Regel-Hard-Invariants. Details siehe v2; hier nur die Deltas.

---

## Ehrliche Ausgangslage & Architektur-Guards (ENTSCHIEDEN — siehe v1/v2)

MolTrust ist heute zu unreif für CEP (~67 RPs; Verticals schief core 57/skill 3/shopping 1) — **kein Defekt, sondern der Grund für die Ramp-up-Phase**; gründer-gesetztes Enforcement = legitimer, befristeter Übergangszustand. Guards unverändert: kein Personen-Schlüssel als Daueranker, kein Single-Chain-Lock-in, Default = sichererer Zustand bei Unklarheit, keine zirkuläre Selbstprüfung, **Verifikation > Produktion**.

---

## §DA-Fix — Data-Availability-Liveness (CEP-1, withholding-fest auch im Challenge-Fenster)

v2 sicherte **Permanenz** (Arweave); v2-Review zeigte das verbleibende **Liveness-Loch im Challenge-Fenster**. v3:

1. **`data_uri` = Fallback-Array, nicht single-uri.** Im verankerten Tupel ist `data_uri` eine **geordnete Liste** redundanter Auflösungspfade, z. B.
   `["arweave://<txid>", "ipfs://<cid>", "https://da-gateway.moltrust.ch/<root>"]`.
   Alle Pfade liefern **dieselben** Rohdaten (derselbe `merkle_root` aus jedem nachrechenbar); ein Verifier nutzt den ersten erreichbaren. Gateway-503/Zensur eines einzelnen Pfads bricht die Nachrechenbarkeit nicht mehr.
2. **Automatische Zeitschloss-Verlängerung (Data-Availability-Challenge).** Sind die Rohdaten für mehr als **X_av %** des laufenden Challenge-Fensters über **keinen** Pfad abrufbar (durch signierte DA-Challenge eines Verifiers belegt), verlängert sich das Zeitschloss automatisch. Das Veto-Fenster kann nicht verstreichen, während Daten temporär unerreichbar sind.
3. **Arweave-Finality vs. Timelock-Start.** Arweave hat probabilistische Finalität (~2 min Blockzeit). Der Timelock startet **erst**, nachdem die Arweave-`txid` eine festgelegte **Bestätigungstiefe** erreicht hat (Hard-Invariant-gekoppelt), damit ein Arweave-Reorg die `data_uri` nicht invalidiert, während der Timelock auf den Anchor-Chains bereits läuft.

**Safety war bereits gewahrt** (Producer nicht-fälschbar); §DA-Fix schließt das **Liveness**-Loch.

---

## §Verifier-Ökonomie — Verifier's Dilemma (CEP-1/Veto tragfähig machen)

v2-Review: die Honest-Verifier-Annahme ist kryptografisch korrekt, aber ökonomisch leer — ein Veto muss auf K_q Chains verankert werden (Gas-Kosten), ohne Reward vetot niemand.

- **Veto-Erstattung aus vorfinanzierter Treasury.** Ein **valides** Veto (deterministisch durch Root-Mismatch / DA-Challenge beweisbar) bekommt die Anchoring-Gas-Kosten aus einer **vorab finanzierten Treasury/Endowment** erstattet. Nur valide Vetos sind erstattungsfähig (kein Spam-Anreiz; invalide Vetos tragen ihre Kosten selbst).
- **Online-/Anreiz-Annahmen explizit.** Sicherheit hängt an **mindestens einem ehrlichen, online-aktiven** Verifier mit Sicht auf die DA-Daten im Challenge-Fenster. v3 macht diese Annahme normativ (Verifier-Liveness, Daten-Sichtbarkeit, ökonomische Deckung der Veto-Kosten) statt sie implizit zu lassen.
- **Veto-TX-Zensur.** Gegen Zensur der Veto-Transaktion auf einer Ziel-Chain: Veto ist gültig, wenn es auf **mindestens K_q von K** Chains verankert ist (Multi-Chain), plus optionale Multi-Relay-Einreichung — eine einzelne zensierende Chain/Validator-Menge kann das Veto nicht unterdrücken.

> **OFFEN — siehe §CEP-5 (Treasury-Governance):** Die **Existenz** der Treasury ist hier als Fix gesetzt; **wer sie verwaltet/nachfinanziert** ist ein potenzieller neuer Personen-/Instanz-Anker und damit eine ungelöste Kern-Frage — NICHT als gelöst behauptet.

---

## §Permissionless-Fallback — Ramp-up-Liveness-SPOF (CEP-4 Griefing-Schutz)

v2-Review: der Gründer als Sole-Publisher kann den Übergang durch bloßes Nicht-mehr-Publizieren **unendlich blockieren** (Safety gewahrt, aber Liveness-SPOF).

- **Permissionless Fallback Publisher.** Ist die Ramp-up-Phase aktiv und seit **T_fb** Tagen **kein** vom Gründer-Key signierter Snapshot verankert worden, darf **jede Node** — die die 5-AND-Bedingungen über den letzten verfügbaren Zustand lokal als erfüllt ansieht — selbst einen Snapshot erzeugen, auf DA-Storage legen und `(merkle_root, data_uri)` multi-chain verankern. Safety bleibt vollständig gewahrt: der Fallback-Snapshot ist exakt so **öffentlich nachrechenbar + vetoierbar** wie ein Gründer-Snapshot (Verifikation > Produktion). Damit ist der Liveness-SPOF eliminiert, ohne eine privilegierte Rolle einzuführen.
- **Kanonische Auswahlregel (konkurrierende Transitions).** Bei mehreren konkurrierenden gültigen Snapshots/Transitions im selben Fenster gilt eine **deterministische, vorab festgeschriebene Auswahl** (z. B. niedrigster `merkle_root` lexikografisch bei sonst gleichwertigen, regelkonformen, multi-chain-verankerten Kandidaten; identische 5-AND-Auswertung erzwungen). Jede Node wählt denselben kanonischen Kandidaten → kein Split-Brain durch Publisher-Konkurrenz.

---

## §SCITT-Profil — echtes Profil statt "Anlehnung" (CEP-1 Transparenz)

v2-Review: "SCITT-Anlehnung" ist ein Label; ein Merkle-Root auf einer Chain ist kein SCITT-Receipt. v3 spezifiziert ein konkretes Profil:

- **COSE-Sign1-Envelope.** Der Snapshot wird als **COSE Sign1** signiert: Payload = das normative Snapshot-JSON-Schema (RP-Set mit DIDs, Vertical-VCs, Score-Epochen, Sybil/Cluster-Status, Algorithmen-Versions-IDs); Signatur = Publisher-Key (Gründer-M-of-N im Ramp-up bzw. Fallback-Publisher).
- **Transparency-Service-Rolle.** Die Anchoring-Smart-Contracts/Programme auf den ≥ 2 Chains (Baustein a) übernehmen die SCITT-Transparency-Service-Rolle: sie führen das **Append-Only-Log** der `(merkle_root, data_uri)`-Receipts.
- **Inclusion-Proofs.** Für jeden Snapshot ein Inclusion-Proof, dass sein Root im Multi-Chain-Quorum-Log enthalten ist (von jedem prüfbar).
- **Consistency-Proofs.** **Hash-Chain zwischen aufeinanderfolgenden Snapshots** (jeder Snapshot referenziert den Vorgänger-Root) → Append-Only / Nicht-Umschreibbarkeit der Snapshot-Historie kryptografisch belegbar (schließt nachträgliche History-Manipulation aus).
- **Receipt-Format** an SCITT-Receipts ausgerichtet, damit externe Auditierung (CEP-1) gegen einen offenen Standard erfolgt, nicht gegen MolTrust-Internas.

---

## Ramp-up → CEP-Übergang: FÜNF Bedingungen (5-AND, unverändert aus v2)

1. Mindestzeit (Anti-Rush). 2. ≥ N sybil-geprüfte RPs. 3. ≥ M Verticals. 4. kein Akteur/Cluster > X % trust-gewichtetes Stimmgewicht. 5. kein Vertical > Y % der RPs/Trust-Gewichts (Anti-Vertical-Dominanz).
Übergang = **explizite signierte Transition** (CEP-4, v2) über **verankerten, DA-gesicherten** Snapshot (CEP-1, §DA-Fix), nach Zeitschloss-Ablauf ohne qualifiziertes (erstattetes) Veto.

---

## Formalisierung, Byzantine-Modell, Meta-Regel-Hard-Invariants (aus v2, v3-Ergänzung)

- **5-Bedingungs-Funktion** (JSON-Schema, exakte Mathematik N/M/X/Y, S aufeinanderfolgende Snapshots, Algorithmen-Versionierung im Snapshot eingebettet) — unverändert aus v2.
- **Byzantine-Modell:** Honest-**Verifier**-Annahme (≥ 1 ehrlicher, online, ökonomisch gedeckter Verifier) statt Honest-Producer-Mehrheit; Multi-Chain-Quorum K_q-von-K toleriert Chain-Ausfall/Zensur. v3 macht die **ökonomische Deckung** des Verifiers (Treasury) zum expliziten Teil der Annahme.
- **Hard-Invariants (unveränderlich, auch durch CEP selbst nicht änderbar):** X < 50 %, Y-Cap, Zeitschloss-Mindestdauer; **v3-Ergänzung:** Arweave-Mindest-Bestätigungstiefe vor Timelock-Start, DA-Challenge-Schwelle X_av, Fallback-Frist T_fb haben jeweils einen festgeschriebenen Korridor (kein Rush-Bypass durch Parameter-Verbiegung). Break-Glass-Disziplin unverändert.

---

## Voraussetzungen (V1 geschärft, aus v2)

| # | Voraussetzung | Status |
|---|---|---|
| **V1** | RP-Registry mit DID + Vertical-VC — **eigener Vorab-Strang: IP→DID Auth/Routing-Umbau** (`known_callers` ist IP-basiert) | Greenfield + Auth-Umbau |
| **V2** | `enforce`-State in `constraint_mode` (heute `{none,inherit,restrict}`) | additive Migration |
| **V3** | Multi-Chain-Anchoring (2. `anchor_fn`) + Permanenz-Storage (Arweave) + DA-Fallback-Gateway + Treasury-Mechanik | Stub + Greenfield |

---

## CEP-3 — Schwellen N / M / X / Y / T_fb / Zeit (Lars-Geschäftsentscheidung, NICHT raten)

Platzhalter + Begründungs-Framework (aus v2, erweitert um T_fb-Fallback-Frist und X_av-DA-Schwelle): N gegen finanzierbaren Sybil-Schwarm; M > 1, sinnvoll ≥ Hälfte der Vertical-Typen; X Hard-Invariant < 50 %; Y schließt heutige core-Dominanz aus; Zeit lang gegen Rush; **T_fb** lang genug, dass kein legitimer Gründer-Publish-Zyklus fälschlich als Ausfall gilt, kurz genug gegen Dauer-Griefing. **Status: Geschäftsentscheidung — vor Ramp-up-Start festschreiben + multi-chain verankern.**

---

## OFFENE KERN-ENTSCHEIDUNGEN (für Re-Review)

### CEP-5 (NEU) — TREASURY-GOVERNANCE (ungelöst, NICHT als gelöst behauptet)
Die **Existenz** einer vorfinanzierten Treasury löst das Verifier's Dilemma (§Verifier-Ökonomie). Aber: **Wer verwaltet und finanziert die Treasury nach dem Übergang nach?** Eine vom Gründer/einer Instanz kontrollierte Treasury ist ein **neuer Personen-/Instanz-Anker** und widerspricht direkt dem 10-Jahres-/personenunabhängig-Guard.
- **Lösungsrichtung (zu schärfen, NICHT final):** Die Treasury wird nach dem Übergang **durch dieselben CEP-Regeln** verwaltet (Auszahlung nur für deterministisch-valide Vetos; Nachfinanzierung/Parameter via Timelock + trust-gewichtetes Veto + Hard-Invariants), sodass kein Einzelakteur Erstattung verweigern oder die Treasury abziehen kann.
- **Henne-Ei:** Im Ramp-up ist die Treasury gründer-finanziert (wie das Enforcement selbst) — derselbe befristete, öffentlich verankerte Übergangszustand mit Ausstiegspfad.
- **Status: KRITISCHE OFFENE FRAGE** — Blocker für ACCEPTED, im Re-Review adversarisch zu prüfen (insb. ob die CEP-verwaltete Treasury nicht selbst ein zirkuläres Bootstrap-Problem erzeugt).

### CEP-2 — Meta-Regel-Governance
Unverändert aus v2 (Timelock + Veto + Hard-Invariants; self-amendment-Schutz). Treasury-Governance (CEP-5) fällt unter dasselbe Meta-Regel-Regime.

---

## §Nebenpunkte (aus v2-Review, adressiert)

- **V1 IP→DID-Latenz:** DID-Resolution + VC-Validierung pro API-Call sind mit einem **hochperformanten Cache** (resolved DID-Docs + Vertical-VC-Status, TTL-gebunden, Invalidierung bei Key-Rotation) machbar — DID-basiertes Routing ist etablierte Praxis. Performance-Budget im V1-Strang mitführen.
- **Cross-Chain-RPC-Redundanz:** Multi-Chain-Verifikation (CEP-4) nutzt **redundante RPC-Endpoint-Arrays** je Chain; eine Node bleibt bei Einzel-RPC-Ausfall verifikationsfähig.
- **W3C-VC-Profil/Revocation:** Vertical-Zuordnung als VC braucht ein **konkretes Profil** (Subject = RP-DID, definierte Issuer, Revocation via Status-List o. ä.) statt "VC" generisch — als Teil von V1 zu spezifizieren.

---

## Konsequenz für D3 / Komponente 3

CEP blockiert Komponente 3 **NICHT** für `advisory`/`none`/`inherit` (DENY nur geloggt). Das scharfe `enforce`-Umschalten hängt an der CEP-Entscheidung. M-of-N-Signatur (ADR-D3-v3) bleibt Ramp-up-Mechanik + (Gründer-)Publisher-Rolle; CEP ersetzt sie nach Übergang durch objektive, öffentlich nachrechenbare Bedingungen. No-Downgrade + Default-zum-sicheren-Zustand in beiden Phasen.

---

## Nächste Schritte

1. **Re-Review** — adversarisch v. a. **CEP-5 (Treasury-Governance)** + DA-Challenge-Mechanik + Permissionless-Fallback-Auswahlregel.
2. **eu-compliance-Review** (regulatorischer Governance-Winkel: EU AI Act / DSGVO / eIDAS 2.0 / NIS2) — separat.
3. **CEP-3-Schwellen** als Geschäftsentscheidung festschreiben + verankern.
4. Status-Flip **PROPOSAL → ACCEPTED** erst nach Konsens. **HARD GATE bleibt.**

---

## Konsequenzen / Trade-offs

- **Pro:** DA-Liveness geschlossen (Fallback-Array + auto-extend); Verifier ökonomisch tragfähig; Ramp-up-Liveness-SPOF eliminiert (Permissionless Fallback); SCITT als echtes Profil → standard-auditierbar. Kernarchitektur (Verifikation > Produktion, signierte Transitions) vom Review bestätigt.
- **Contra / Risiko:** **CEP-5 (Treasury-Governance) ungelöst** — neuer potenzieller Anker, nur Lösungsrichtung; steigende Komplexität (DA-Fallback, Treasury, Fallback-Publisher, SCITT-Profil, V1-Auth-Umbau); Permanenz-/Treasury-Kosten real; Liveness im Ramp-up nur bis T_fb gründer-abhängig.
