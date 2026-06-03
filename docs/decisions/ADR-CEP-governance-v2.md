# ADR — CEP (Combined Enforcement Protocol) Governance (v2)

**Status:** **PROPOSAL** (Review-Runde 2, design-only). **HARD GATE:** blockiert die Implementierung von D3 **Komponente 3** (scharfes `enforce`-Umschalten). Kein Code auf Komponente 3 vor 3-Reviewer-Konsens (wie ADR-D3).
**Supersedes:** `docs/decisions/ADR-CEP-governance.md` (v1, PR #135). v1 bleibt als **Audit-Trail** erhalten — NICHT löschen. Kette: `ADR-CEP-governance-DRAFT.md` → v1 → **v2**.
**Datum:** 2026-06-04 · **Autor:** Lars Kroehl
**Review-Basis:** `~/moltstack/reviews/20260603_214959_CEP-governance-PROPOSAL_review.md` (technical: GPT-5 + Gemini 3.1 Pro Preview + Perplexity Sonar Pro → Claude-Synthese) — Verdikt **"GRUNDLEGEND ÜBERDENKEN"**. v2 foldet alle Criticals als **gelöste Design-Entscheidungen** ein.
**Bezug:** `ADR-D3-mandate-enforcement-v3.md` (ACCEPTED). CEP gated ausschließlich D3 Komponente 3 (enforce-mode-Chokepoint).

---

## Änderungen gegenüber v1 (Review-Criticals → Resolution)

| Review-Critical (technical-Review) | Resolution in v2 |
|---|---|
| **CEP-1 Oracle — Data-Availability-Lücke** (Merkle-Root beweist Integrität, nicht Verfügbarkeit; Producer kann Rohdaten withholden → "trust-us-Oracle") | **§CEP-1**: Snapshot-Rohdaten auf **dezentralem, permanentem** Storage; **`(merkle_root, data_uri)`**-Tupel verankert (nicht nur Root). Nachrechenbarkeit = **offene Eigenschaft** (jeder, keine privilegierten Indexer). Permanenz via **Arweave** (governance-kritisch). |
| **CEP-4 Split-Brain** (lokale deterministische Auswertung ohne Konsens-Layer → Partition → divergente Node-Zustände) | **§CEP-4**: explizite **signierte State-Transitions** statt impliziter Timeouts. Node verifiziert Chain-Anker + abgelaufenes Zeitschloss → schreibt signierten Übergang. |
| **Vertical-Dominanz-Lücke** (≥M Verticals erfüllt auch ein 98%-mono-vertikales Netz) | **§Ramp-up**: **5. Bedingung** "kein Vertical > Y%". Jetzt **5-AND** statt 4-AND. |
| **DoS** (erzwungene frische O(N²)-Jaccard pro Veto/Challenge) | **§Baustein b**: **signierte Score-Epochen** (asynchron, invalidation-pattern). Schließt zugleich den `sybil_penalty=0`-Cache-Befund. |
| Fehlende formale Bedingungs-Funktion | **§Formalisierung**: 5-Bedingungs-Funktion mit JSON-Schema, exakter Mathematik, Zeit-Semantik, **Algorithmen-Versionierung**. |
| Fehlendes Byzantine-Modell | **§Byzantine-Modell** (skizziert). |
| Meta-Regel self-amendment | **§Meta-Regel-Hard-Invariants** (unveränderlich, auch durch CEP selbst nicht änderbar). |
| Standards (SCITT/W3C-VC) | **§Standards-Alignment**. |
| V1 unterschätzt (IP→DID-Umbau) | **§Voraussetzungen**: V1 als eigener Auth/Routing-Umbau-Strang geschärft. |

---

## Ehrliche Ausgangslage (unverändert, ENTSCHIEDEN — siehe v1 §Ehrliche Ausgangslage)

MolTrust ist **heute zu unreif für CEP** (~67 RPs/Agents; Vertical-Verteilung schief: core 57 / skill 3 / shopping 1; zu dünn für trust-gewichtetes Quorum). Das ist **kein Defekt, sondern der Grund für die Ramp-up-Phase.** CEP **darf heute nicht greifen**; **gründer-gesetztes Enforcement ist der legitime, befristete Übergangszustand** mit vorab festgeschriebenem, objektivem Ausstiegspfad. Diese Punkte sind ENTSCHIEDEN und in v2 nicht erneut zur Debatte gestellt.

---

## Architektur-Guards (nicht verhandelbar, aus v1 + v2-Ergänzung)

- **Kein Personen-/Gründer-Schlüssel als _dauerhafter_ Anker.** Schlüssel nur im Ramp-up, mit festgeschriebenem Ablösepfad.
- **Kein Single-Chain-Lock-in.** Regel-Versionen + Übergangs-Bedingungen + `(merkle_root, data_uri)` werden über **Multi-Chain-Quorum** verankert.
- **Default = sichererer Zustand bei Unklarheit.** Ist der CEP-Auslöser nicht eindeutig/unabhängig nachrechenbar oder ein Snapshot nicht verfügbar/verifizierbar, bleibt Enforcement im **vorherigen** Zustand. No-Downgrade-Guard aus ADR-D3-v3 gilt fort.
- **Keine zirkuläre Selbstprüfung.**
- **(v2) Verifikation > Produktion.** Der Snapshot-**Produzent** muss nicht dezentral/vertrauenswürdig sein, solange der Snapshot **vollständig öffentlich nachrechenbar + vetoierbar** ist. Dezentralität liegt in der **Verifikation**, nicht in der Erzeugung. (Kernidee von "Weg 2".)

---

## Drei Bausteine (v2-Stand)

### (a) Chain-agnostisches Anchoring — `(merkle_root, data_uri)`, nicht nur Root
- **Seam vorhanden:** `app/provenance/anchor.py::anchor_batch(conn, anchor_fn)` + Pure-Python-Merkle. Die `anchor_fn`-Injektion ist die chain-agnostische Naht (derselbe Root deterministisch auf K Chains).
- **NEUBAU:** zweite reale `anchor_fn` (SKALE *oder* Solana, Auswahl = CEP-Decision) hinter dieselbe Signatur; **Quorum: ein Root gilt, wenn er auf ≥ K_q von K Chains identisch verankert ist.**
- **v2-Verschärfung:** Verankert wird das Tupel **`(merkle_root, data_uri)`** — nicht nur der Root. Ohne `data_uri` auf permanentem Storage ist der Root nicht nachrechenbar (Data-Withholding). Siehe §CEP-1.

### (b) Trust-gewichtetes Veto — signierte Score-Epochen (kein fresh-O(N²))
- **Vorhanden:** `compute_phase2_score` + `compute_sybil_penalty` (Jaccard-Cluster + Vertical-Diversity).
- **v2 (DoS-Fix + Cache-Fix):** Stimmgewicht = **signierte Score-Epoche**. Scores werden **asynchron** (z. B. stündlich/täglich) **governance-spezifisch mit aktiver `sybil_penalty`** berechnet (NICHT der Cache-Pfad mit `sybil_penalty=0`), in eine **signierte Epoche** geschrieben (Score + Algorithmen-Versions-ID + Epoch-Timestamp), und Teil des verankerten Snapshots. Veto/Challenge liest die **letzte signierte Epoche** — keine fresh-Berechnung pro Veto → kein O(N²)-DoS-Vektor. Schließt den `sybil_penalty=0`-Befund (die Epoche wird per Konstruktion mit Penalty berechnet).

### (c) Zeitschloss + öffentliches Veto — signierte Transitions (Greenfield)
- **Greenfield** (0 CEP-Tabellen). Mechanik: vorgeschlagene Regel-/Enforcement-Änderung tritt erst nach **langer Wartezeit** in Kraft und nur, **wenn kein qualifiziertes Veto** vorliegt (**Widerspruch statt aktive Zustimmung**). Veto-Gewicht = signierte Score-Epoche (Baustein b).
- **v2 (Split-Brain-Fix):** Der Übergang ist **kein impliziter lokaler Timeout**, sondern eine **explizite signierte Transition** (§CEP-4).

---

## CEP-1 — ORACLE: RESOLVED (Weg 2 — Data-Availability + öffentliche Nachrechenbarkeit, KEINE privilegierte Indexer-Rolle)

**Entscheidung (Lars):** Nicht ein Multi-Indexer-Konsens (würde eine designierte Indexer-Rolle einführen), sondern **dezentrale Verifikation**:

1. **Snapshot-Daten öffentlich + permanent.** Der Snapshot ist der vollständige RP-Registry-State zum Stichtag: **DIDs, Vertical-Zuordnungen (als W3C VC), governance-Trust-Scores (signierte Epoche), Sybil/Cluster-Status**. Diese Rohdaten liegen auf **dezentralem Storage** und sind über die `data_uri` abrufbar.
2. **`(merkle_root, data_uri)` chain-agnostisch verankert** (Multi-Chain-Quorum, Baustein a) — **nicht nur der Root.** Damit ist die Verfügbarkeit (Data Availability) gesichert: der Root allein wäre withhold-anfällig.
3. **Jeder rechnet nach.** Die 5-Bedingungs-Funktion (§Formalisierung) ist **deterministisch** über das fixierte Snapshot-Datenmodell. Jeder Beobachter lädt die Rohdaten von `data_uri`, rechnet den `merkle_root` nach (Abgleich gegen den verankerten Wert) und wertet die 5 Bedingungen aus. **Keine designierten Indexer; Nachrechenbarkeit ist eine offene Eigenschaft.**
4. **Producer ist nicht privilegiert.** Der Snapshot-**Produzent** (im Ramp-up: Gründer via M-of-N-Key) kann
   - **nicht withholden** — `data_uri` + Permanenz erzwingen Verfügbarkeit; fehlt sie, greift Default-zum-sicheren-Zustand (kein Übergang).
   - **nicht fälschen** — jeder rechnet den Root aus denselben öffentlichen Daten nach; eine manipulierte Zuordnung (Cluster/Vertical/Score) ist als Root-Mismatch oder via Challenge sichtbar und **vetoierbar**.
   Damit kollabiert der "trust-us-Oracle"-SPOF: man braucht **keine** dezentrale Snapshot-*Erzeugung*, wenn die *Verifikation* dezentral und das Veto offen ist.
5. **Residual-Liveness (kein Safety-Problem):** Ein Producer kann sich weigern zu publizieren. Folge: **kein** Übergang (Default-safe). Im Ramp-up publiziert der Gründer; stoppt er, bleibt der vorherige Zustand. Post-Transition können mehrere Parteien konkurrierende Snapshots publizieren; die deterministische Funktion + Veto + Multi-Chain-Quorum entscheiden über den gültigen Root.

### PERMANENZ-STRATEGIE (Empfehlung — Review bewertet, wird voraussichtlich nachfassen)
- **Governance-kritische Snapshots → Arweave.** Echte **Bezahl-Permanenz** (einmalige Gebühr, langfristige Speicher-Endowment), **10-Jahre-tauglich**. Nachrechenbarkeit setzt **dauerhafte** Verfügbarkeit voraus.
- **Non-critical → IPFS.**
- **Warum reines IPFS nicht reicht:** Eine IPFS-CID ist nur abrufbar, **solange jemand sie pinnt**. Ohne garantierte Permanenz verschwindet die `data_uri`-Auflösung → der verankerte Root wird **un-nachrechenbar** → exakt die Data-Withholding-Lücke kehrt zurück (nur zeitverzögert). IPFS löst Content-Adressierung, **nicht** Permanenz.
- **Strategischer Kontext:** passt zur geplanten MT-Gesamt-Migration auf IPFS; governance-kritische Permanenz wird gezielt auf Arweave gelegt.
- **Status:** Empfehlung; finale Storage-Wahl ist Review-/Geschäfts-Entscheidung.

---

## CEP-4 — BOOTSTRAP / SPLIT-BRAIN: RESOLVED (signierte State-Transitions)

**Problem (Review):** Wird der Umschaltpunkt als lokal ausgewertetes "deterministisches Ergebnis" implementiert, divergieren Nodes bei Netzwerk-Partition im Challenge-Fenster (Node A sieht Veto → bleibt `restrict`; Node B sieht keins → `enforce`).

**Resolution:** Der Übergang ist eine **explizite, signierte Transition**, kein impliziter Timeout:
1. Eine Node wertet die 5-Bedingungs-Funktion über den **verankerten** Snapshot aus, prüft, dass das **Zeitschloss abgelaufen** ist und **kein qualifiziertes Veto** (aus dem verankerten Veto-Log) vorliegt.
2. Sie verifiziert die **Chain-Anker** (Multi-Chain-Quorum) der relevanten Snapshot- und Veto-Roots.
3. Erst dann schreibt sie einen **signierten Transitions-Datensatz** (`enforce`-Aktivierung mit Referenz auf Snapshot-Root, Veto-Root, Timelock-Ablauf, Algorithmen-Version) — selbst wieder ein **verankerbares, von jedem nachprüfbares Ereignis**.
4. Der `enforce`-State folgt der signierten, verankerten Transition — **nicht** dem lokalen Uhr-Timeout einer einzelnen Instanz. Bei Partition gibt es keinen divergenten "stillen" Flip: ohne verifizierten Anker + ohne signierte Transition bleibt jede Node im **vorherigen** Zustand (Default-safe).

Der Umschaltpunkt hat damit **keinen privilegierten Auslöser** (jeder kann die Bedingungen + Anker nachprüfen), ist aber als **explizites signiertes Ereignis** partitions-fest. Solange CEP-1 (verankerte, verfügbare Snapshots) gilt, ist auch CEP-4 SPOF-frei.

---

## Ramp-up → CEP-Übergang: FÜNF Bedingungen (5-AND, war 4-AND)

Enforcement-Regeln **zunächst gründer-gesetzt**. Übergang zu CEP **nur** bei **FÜNF gleichzeitig** erfüllten Bedingungen (AND), alle **vorab festgeschrieben + chain-agnostisch verankert BEVOR der Ramp-up startet**:

1. **Mindestzeit verstrichen** (Anti-Rush).
2. **≥ N** behavioral-qualifizierte, **Sybil-geprüfte** RPs.
3. verteilt über **≥ M Verticals** (Diversität).
4. **kein Akteur/Cluster > X %** trust-gewichtetes Stimmgewicht (Anti-Konzentration).
5. **(NEU) kein einzelnes Vertical > Y %** der qualifizierten RPs / des Trust-Gewichts (**Anti-Vertical-Dominanz**) — Bedingung 3 allein lässt ein 98%-mono-vertikales Netz durch.

Zahlen **N / M / X / Y / Zeit** = Geschäftsentscheidung Lars (§CEP-3, Platzhalter + Framework, NICHT geraten).

---

## Formalisierung der 5-Bedingungs-Funktion

- **Snapshot-Datenmodell:** normatives **JSON-Schema** mit Versionsfeld; Felder je RP: `did`, `vertical_vc` (W3C VC, nicht Selbstdeklaration), `governance_trust_score` (signierte Epoche), `sybil_status`, `cluster_id`, `status=active`. Snapshot trägt **Algorithmen-Versions-IDs** (s. u.) + `snapshot_period`.
- **Exakte Mathematik:**
  - **N** = Anzahl distinct RPs mit `status=active` ∧ `governance_trust_score ≥ T_min` ∧ `sybil_status=clear`.
  - **M** = Anzahl distinct Verticals mit ≥ k qualifizierten RPs.
  - **X** = max über Cluster C_i des Anteils `trustweight(C_i) / Σ trustweight(qualifizierte)`; Cluster nach Anti-Collusion (Jaccard), overlapping Cluster konservativ (max-Zuordnung).
  - **Y** = max über Verticals V_j des Anteils qualifizierter RPs (bzw. Trust-Gewichts) in V_j.
  - **Zeit-Semantik:** Bedingungen müssen in **≥ S aufeinanderfolgenden** verankerten Snapshots erfüllt sein (max Δt Abstand) — kein Einzel-Snapshot-Glück.
- **Algorithmen-Versionierung:** governance-spezifische, **eingefrorene** Versionen (`governance_phase2_v1`, `governance_sybil_v1`, Cluster-Algo) mit Code-Hash/Versions-ID **im Snapshot eingebettet + verankert**. Änderung an diesen Algorithmen läuft durch Meta-Regel-Governance (Timelock + Veto). Verhindert, dass spätere Heuristik-Änderungen die Interpretation alter Snapshots rückwirkend verschieben.

---

## Byzantine-Modell (skizziert — im Review zu schärfen)

- **Akteure:** bis zu f byzantinische RPs/Cluster dürfen die 5 Bedingungen nicht erfüllbar/unterlaufbar machen; Bedingung 4+5 begrenzen Einzel-/Cluster-/Vertical-Macht strukturell.
- **Chains:** das Multi-Chain-Quorum (K_q von K) toleriert Ausfall/Zensur einzelner Chains, solange ≥ K_q ehrlich denselben Root verankern.
- **Storage:** Permanenz (Arweave) + öffentliche Nachrechenbarkeit machen einen einzelnen Snapshot-Producer **nicht** vertrauenswürdig-nötig (Verifikation > Produktion).
- **Honest-Verifier-Annahme:** Sicherheit hängt an **mindestens einem ehrlichen, aktiven Verifier**, der falsche Snapshots vetot — nicht an einer ehrlichen Mehrheit der Produzenten.

---

## Meta-Regel-Governance (CEP-2) — Hard-Invariants (unveränderlich)

Regeln (5 Bedingungen, Schwellen, Snapshot-Periode, Algorithmen-Versionen) bleiben über 10 Jahre änderbar — aber **nicht durch eine Instanz** und **nicht ohne Grenzen**. Änderungen unterliegen demselben **Timelock + trust-gewichteten Veto**. Darüber stehen **Hard-Invariants**, die in den initial verankerten Meta-Regeln festgeschrieben und **auch durch CEP selbst nicht änderbar** sind (Schutz gegen self-amendment-Attacke):

- **X niemals > 50 %** (kein Akteur/Cluster darf je Alleinentscheidungs-Stimmgewicht erreichen).
- **Y-Cap** (Vertical-Dominanz-Obergrenze niemals über einem festen Maximum).
- **Zeitschloss-Dauer niemals unter einem festen Mindestwert** (kein Rush-Bypass durch Timelock-Verkürzung).
- **Break-Glass-Disziplin:** ein etwaiger key-basierter Override post-Transition (falls überhaupt) nur mit denselben Invarianten + öffentlicher Verankerung — kein stiller Bypass.

---

## Standards-Alignment

- **IETF SCITT** (Supply Chain Integrity, Transparency and Trust): Snapshot-/Transparency-Log-Struktur (Merkle-Roots, Receipts, Challenge-Fenster) an SCITT-Receipts anlehnen — kryptografische Beweisführung nicht neu erfinden.
- **W3C Verifiable Credentials:** Vertical-Zuordnung eines RPs als **VC** an dessen DID gebunden (definierte Issuer), **nicht** Selbstdeklaration → Bedingung 3+5 kryptografisch beweisbar ohne Vertrauen in eine zentrale DB.
- **W3C DIDs:** RP-Identität als DID (s. V1).

---

## Voraussetzungen (vor Ramp-up-Start — aus Recon, V1 geschärft)

| # | Voraussetzung | Status | Ohne sie … |
|---|---|---|---|
| **V1** | **RP-Registry mit DID + Vertical-VC** — **eigener Vorab-Strang: Umbau Auth/Routing von IP→DID.** `known_callers` ist **IP-basiert**; der Wechsel ist kein bloßer Tabellen-Bau, sondern betrifft Authentifizierung + Routing der API (kryptografische Bindung Endpoint↔DID, DID-Lifecycle, Key-Rotation, Vertical-VC-Issuance). | Greenfield + Auth-Umbau | Bedingungen 2/3/5 nicht messbar |
| **V2** | **`enforce`-State** in `constraint_mode` (heute `{none, inherit, restrict}`, kein `enforce`) | additive Migration (ADR-D3-v3-Ziel) | Komponente 3 hat keinen scharfen Ziel-Zustand |
| **V3** | **Multi-Chain-Anchoring** (2. `anchor_fn`) + Permanenz-Storage (Arweave) | Stub + Greenfield | `(merkle_root, data_uri)` nur single-chain/non-permanent → Lock-in + Withholding |

V1–V3 sind **Implementierungs-Vorbedingungen**, kein Teil dieses Design-Sign-offs — aber ohne sie ist der Übergang weder messbar (V1) noch scharf schaltbar (V2) noch lock-in-/withholding-frei verankerbar (V3).

---

## CEP-3 — SCHWELLEN N / M / X / Y / Zeit (Lars-Geschäftsentscheidung, NICHT raten)

Platzhalter + Begründungs-Framework:
- **N** (Mindest-RPs): groß genug gegen finanzierbaren Sybil-Schwarm, klein genug erreichbar. Anker: heute ~67 RPs.
- **M** (Verticals): > 1 zwingend; sinnvoll ≥ Hälfte der real betriebenen Vertical-Typen.
- **X** (max. Akteur/Cluster-Konzentration): **Hard-Invariant < 50 %**, real eher Sperrminoritäts-Logik.
- **Y** (max. Vertical-Anteil): so, dass kein Vertical das Netz dominiert; Anker: heutige Schieflage (core dominiert) → Y muss diese explizit ausschließen.
- **Zeit:** lang gegen Rush, gekoppelt an Zeitschloss-Mindestdauer (Hard-Invariant).

**Status:** Geschäftsentscheidung — vor Ramp-up-Start festschreiben + multi-chain verankern.

---

## Konsequenz für D3 / Komponente 3

- CEP blockiert Komponente 3 **NICHT** für `advisory`/`none`/`inherit`: Evaluator läuft scharf, DENY wird **nur geloggt**.
- Das **scharfe `enforce`-Umschalten** hängt an der CEP-Entscheidung.
- **Verhältnis zu ADR-D3-v3:** Die M-of-N-Signatur-Autorisierung (Governance-Key + 2. Key, Telegram nur Notification, No-Downgrade-Guard) bleibt **Ramp-up-Mechanik** + Snapshot-Publisher-Rolle. CEP **ersetzt** diese schlüsselgebundene Autorität nach erfülltem Übergang durch objektive, öffentlich nachrechenbare Bedingungen. No-Downgrade + Default-zum-sicheren-Zustand gelten in beiden Phasen.

---

## Offene Punkte für die Re-Review (Runde 2)

1. **Permanenz-Strategie** (Arweave governance-kritisch / IPFS non-critical) — tragfähig für 10 Jahre? Kosten/Endowment-Modell? (Review wird voraussichtlich nachfassen.)
2. **Byzantine-Modell** formalisieren (f, K_q, Honest-Verifier-Annahme exakt).
3. **Liveness im Ramp-up:** Gründer als alleiniger Publisher ist Safety-frei (nicht fälschbar/withhold-bar), aber Liveness-abhängig — Übergang zu Mehr-Publisher-Post-Transition sauber spezifizieren.
4. **SCITT-Konkretisierung:** Snapshot-Format als SCITT-Receipt.

---

## Nächste Schritte

1. **Re-Review** (Multi-Model). **Modus-Frage** (`technical` Mechanik vs. `eu-compliance` regulatorischer Governance-Winkel) — Lars klärt; eu-compliance erst nach Tooling-Verfügbarkeit (#134) + technical ≥ ÜBERARBEITEN.
2. Adversarisch v. a. **CEP-1 Permanenz/Liveness** und **CEP-4 signierte Transitions** prüfen.
3. **CEP-3-Schwellen** als Geschäftsentscheidung, dann festschreiben + multi-chain verankern.
4. Status-Flip **PROPOSAL → ACCEPTED** erst nach Konsens. **HARD GATE bleibt:** kein Code auf D3 Komponente 3 vor Sign-off.

---

## Konsequenzen / Trade-offs

- **Pro:** personen-/technik-/instanz-unabhängig; Verifikation dezentral statt Producer-Trust; Data-Availability + Permanenz schließen Withholding; signierte Transitions partitions-fest; 5-AND deckt Vertical-Dominanz; DoS via Score-Epochen entschärft.
- **Contra / Risiko:** hohe Komplexität (3 Bausteine + 3 Vorbedingungen, viel Greenfield, Auth-Umbau V1); Permanenz-Kosten (Arweave) real; Liveness-Abhängigkeit vom Publisher im Ramp-up (Safety-frei, aber Fortschritt blockierbar); lange Zeitschlösser kosten Reaktionsfähigkeit (bewusst).
