# CEP-3 — Threshold-Spezifikation (Implementation-Contract, Gate-C3-1)

**Typ:** Implementation-Contract-Spec — **KEIN Design-ADR.** Das CEP-Design ist **ACCEPTED** (`docs/decisions/ADR-CEP-governance-v8.md`, PR #143). Diese Spec speist **Gate-C3-1** (Schwellen festgeschrieben + chain-agnostisch verankert) aus dem ADR-ADDENDUM und macht die Übergangs-Parameter **eindeutig anwendbar** (schließt die 2 Definitionslücken + 2 Schutzregeln aus dem Kalibrierungs-Review).
**Datum:** 2026-06-04 · **Autor:** Lars Kroehl
**Basis:** Kalibrierungs-Review `~/moltstack/reviews/20260604_190237_CEP-threshold-calibration_review.md` (technical, KEIN ADR-Review). Bezug: `ADR-CEP-governance-v8` §Ramp-up (5-AND), §A gestaffelte Verifikation, anti-collusion-Modell.
**Status der Werte:** **Schwellen-WERTE final = Lars (Geschäftsentscheidung).** Diese Spec legt **Definitionen, Mess-Semantik, Invarianten, Algorithmus und Schutzregeln** fest — nicht die finale Zahl. Zahlen unten = **PLATZHALTER** bis Lars bestätigt.

---

## 1. Übergangs-Schwellen (5-AND, aus ADR §Ramp-up)

| Param | Bedeutung | Wert | Status |
|---|---|---|---|
| **N** | Mindestzahl Sybil-qualifizierter RPs | **101** | **GESETZT** (Lars) |
| **T** | Timelock / öffentliches Veto-Fenster | **31 Tage** | **GESETZT** (Lars) |
| **K** | Mindestzahl unabhängiger Jaccard-Cluster | **4** *(Korridor 4–7)* | PLATZHALTER — Lars wählt |
| **Y** | Max. Anteil pro Cluster (Trust-Gewicht) | **33 %** *(Korridor 25–33 %)* | PLATZHALTER — Lars wählt |
| **X** | Max. Stimmgewicht pro Einzel-Akteur | **10 %** *(Korridor 5–10 %)* | PLATZHALTER — Lars wählt |

Empfehlung für das **junge, reifende Netz:** K=4 / Y=33 % / X=10 % (erreichbarkeitsfreundlich, siehe §6 Power-Law-Deadlock). Maximal-dezentrale Alternative: K=5–6 / Y=25 % / X=5 %. **Final = Lars.**

Der Übergang zu CEP erfolgt nur, wenn **alle fünf** Bedingungen GLEICHZEITIG erfüllt sind (AND): N ∧ T-Fenster-ohne-qualifiziertes-Veto ∧ K ∧ Y ∧ X. Alle Größen werden über die verankerten, DA-gesicherten Snapshots (ADR §A) berechnet und sind **von jedem Verifier identisch nachrechenbar**.

---

## 2. Definitionslücke 1 — X/Y-Mess-Semantik (eindeutig festgelegt)

CEP nutzt **trust-gewichtetes** Voting (kein Token-/Kopf-Stimmrecht, keine Plutokratie). Mess-Basis ist daher durchgängig das **aggregierte behavioral-Trust-Gewicht**, NICHT die RP-Anzahl.

**Definitionen:**
- Sei `Q` = Menge der **qualifizierten** RPs zum Snapshot-Stichtag (`status=active` ∧ `governance_trust_score ≥ T_min` ∧ `sybil_status=clear`; identisch zur N-Zählung).
- Sei `w(rp)` = das **governance-Trust-Gewicht** eines RP = sein frisch berechneter, signierter Phase-2-Score **mit aktiver `sybil_penalty`** (governance-Score-Epoche, ADR — NICHT der Cache-Pfad mit `sybil_penalty=0`).
- Sei `W = Σ_{rp ∈ Q} w(rp)` = Gesamt-Trust-Gewicht.

- **X (pro EINZEL-AKTEUR, pro DID):**
  `X-Bedingung erfüllt ⟺ max_{rp ∈ Q} ( w(rp) / W ) ≤ X`
  → kein einzelner DID hält mehr als X des Gesamt-Trust-Gewichts.
- **Y (pro CLUSTER):**
  `Y-Bedingung erfüllt ⟺ max_{c ∈ Clusters} ( Σ_{rp ∈ c} w(rp) / W ) ≤ Y`
  → kein einzelner Cluster (s. §3) hält mehr als Y des Gesamt-Trust-Gewichts.
- **K (Cluster-Streuung):**
  `K-Bedingung erfüllt ⟺ |Clusters(Q)| ≥ K` (Anzahl **unabhängiger** Cluster, s. §3).

**Mess-Einheit normativ:** X und Y messen **Anteil am aggregierten Trust-Gewicht** (`w`), nicht RP-Kopfzahl. (Begründung: das tatsächliche Stimm-/Veto-Gewicht ist trust-gewichtet; ein Konzentrations-Cap auf Kopfzahl ließe einen High-Trust-Akteur unterhalb der Kopf-Schranke dennoch das Voting dominieren.)

**Konsistenz-Invarianten (Hard-Invariants, MUSS gelten):**
- **`Y ≥ 1/K`** — Schubfachprinzip: K Cluster können 100 % nur dann unter dem Cap Y verteilen, wenn `Y ≥ 1/K`; andernfalls ist die Bedingung **strukturell unerfüllbar** (Liveness-Deadlock). Beispiel K=4 → Y ≥ 25 %.
- **`X ≤ Y`** — ein Einzel-Akteur ist Teilmenge seines Clusters; ein Akteur-Cap oberhalb des Cluster-Caps wäre wirkungslos/widersprüchlich.
- **`X < 50 %` und `Y < 50 %`** — Hard-Invariant aus dem ADR (keine Sperrminorität/Dominanz), unveränderlich auch durch CEP selbst.
- Bei finaler Wertewahl ist die Kette `1/K ≤ Y < 50 %` und `X ≤ Y` zu prüfen (CI-/Anchoring-seitig erzwingen).

---

## 3. Definitionslücke 2 — Cluster-Algorithmus (deterministisch)

**Festlegung: Connected-Components im Jaccard-gefilterten Endorsement-Graphen.** (NICHT Louvain/Community-Detection.)

**Algorithmus (deterministisch, reproduzierbar):**
1. Knoten = qualifizierte RPs `Q`.
2. Kante zwischen RP `a` und RP `b` ⟺ ihr **reziproker** Jaccard-Koeffizient über die Endorsement-Nachbarschaften `> 0.8` (dieselbe `jaccard = |N(a) ∩ N(b)| / |N(a) ∪ N(b)|`-Logik wie `app/swarm/anti_collusion.py`, Cutoff 0.8 bereits etabliert) **und** die zählenden Endorsements sind reziprok (s. §5 Schutzregel 2).
3. **Cluster = Zusammenhangskomponenten (Connected Components)** dieses gefilterten Graphen. Ein RP ohne qualifizierende Kante ist ein **eigener** (Single-Node-)Cluster.
4. `K-Ist = Anzahl der Zusammenhangskomponenten`.

**Warum Determinismus zwingend ist:** K (und Y, das über Cluster aggregiert) muss von **jedem Verifier identisch nachrechenbar** sein — das ist die Grundlage des Honest-Verifier-Modells (ADR §A / „Verifikation > Produktion"): ein Übergang darf nur ausgelöst werden, wenn jeder Beobachter aus den **öffentlich verankerten** Snapshot-Daten denselben Cluster-Graphen und damit dasselbe K/Y rekonstruiert. **Connected Components sind deterministisch** (eindeutiges Ergebnis bei gegebenem Graphen, keine Zufalls-Initialisierung). **Louvain/Modularity-Maximierung ist nicht-deterministisch** (Reihenfolge-/Seed-abhängig, lokale Optima) → unterschiedliche Verifier bekämen unterschiedliches K → Split-Brain beim Übergang. Daher ausgeschlossen.

**Wiederverwendung:** nutzt die bestehende `anti_collusion.py`-Jaccard-Berechnung (Cutoff 0.8) als Kanten-Prädikat; neu ist nur die Connected-Components-Aggregation + die Reziprozitäts-Filterung (§5).

---

## 4. Schutzregel 1 — High-Trust-Konsortium-Ausnahme (False-Positive)

**Problem:** Ein legitimes dichtes Cluster (z. B. Banken-Konsortium mit vollständigen gegenseitigen Endorsements, Jaccard = 1.0) ist algorithmisch nicht von einem Sybil-Ring unterscheidbar und würde Sybil-bestraft.

**Regel:**
- Cluster-Mitglieder können **verifizierte juristische Eigenständigkeit** nachweisen (z. B. distinkte Rechtsträger via verifiable credential / Registereintrag — Ausgestaltung = LP-Track / Dr. Kirchinger, vgl. ADR LP-1/LP-7).
- Ist die Eigenständigkeit verifiziert, wird die **`sybil_penalty` für dieses Cluster aufgehoben** (die Mitglieder behalten ihr Trust-Gewicht `w` voll).
- **ABER:** das Cluster **zählt weiterhin als EIN Cluster für K** und unterliegt voll dem **Y-Cap** (Trust-Gewicht des gesamten Konsortiums ≤ Y). Die Ausnahme hebt nur die *Sybil-Strafe* auf, **nicht** die Konzentrations-/Diversitäts-Grenzen. So wird ein legitimes Konsortium nicht fälschlich entwertet, kann aber auch nicht über die Ausnahme die Dominanz-Caps umgehen.

**Abgrenzung (kein Schlupfloch):** ohne verifizierte Eigenständigkeit bleibt ein dichtes Cluster Sybil-bestraft (Default-DENY-Logik). Die Verifikation ist ein **positiver** Nachweis durch das Cluster, keine Annahme.

---

## 5. Schutzregel 2 — Jaccard-Griefing (Cluster-Poisoning)

**Problem:** Ein Angreifer kann durch gezielte **unidirektionale** Endorsements legitimer Knoten den Nenner (`|N(a) ∪ N(b)|`) künstlich vergrößern und so den Jaccard-Wert legitimer Cluster **unter 0.8 drücken** → Cluster künstlich aufspalten / K manipulieren / legitime Akteure isolieren.

**Regel (zwei kombinierte Maßnahmen):**
1. **Nur reziproke Endorsements zählen für die Cluster-Kanten-Jaccard-Berechnung.** Ein Endorsement `a → b` fließt nur in `N(·)` der Cluster-Bildung ein, wenn auch `b → a` existiert (bidirektional). Einseitige Endorsements eines Außenstehenden können den Nenner damit **nicht** aufblähen → Griefing über unidirektionale Kanten ist wirkungslos.
2. **Mindest-Trust des Endorsers:** nur Endorsements von Endorsern mit `governance_trust_score ≥ T_endorse_min` zählen für die Cluster-Kanten. (Verhindert, dass ein Schwarm wertloser Wegwerf-DIDs den Graphen verzerrt; konsistent mit der Sybil-Qualifikation.) `T_endorse_min` = Lars-Parameter (Korridor mit T_min abstimmen).

**Hinweis:** Diese Reziprozitäts-Regel gilt **spezifisch für die Cluster-Kanten-Bildung (K/Y)**. Die bestehende `anti_collusion.py`-Sybil-Penalty-Logik bleibt unberührt; §5 ergänzt nur das Kanten-Prädikat aus §3.

---

## 6. Power-Law-Deadlock-Hinweis (Liveness vs. Anti-Capture)

Natürliche Trust-Netze folgen einer **Power-Law-/Pareto-Verteilung** (der heutige Zustand 67 Agents, core 57 / skill 3 / shopping 1 illustriert das). Ein **zu striktes Y** bei einem organisch dominanten, **ehrlichen** Core-Cluster führt zu einem **Liveness-Failure**: die Y-Bedingung wird nie erfüllt, der Übergang bleibt dauerhaft blockiert — obwohl keine Kaperung vorliegt.

**Konsequenz für die Wertewahl:**
- Im **jungen, reifenden** Netz spricht das für **Y am oberen Korridor-Rand (≈ 33 %)** — gerade so streng, dass ≥ 4 Cluster erzwungen werden, aber nicht so streng, dass ein ehrlicher Core den Übergang blockiert.
- Y kann mit zunehmender Netz-Reife **nachgeschärft** werden (engerer Cap), sobald die Verteilung breiter ist — Anpassung dann über das Meta-Regel-Regime (Timelock + Veto + Hard-Invariants, ADR CEP-2), **nie** unter die `Y ≥ 1/K`-Invariante.
- Symmetrisch: **K am unteren Rand (4)** im jungen Netz, später erhöhbar.

Dieser Hinweis ist **Kalibrierungs-Leitlinie für Lars**, kein harter Mechanismus — er begründet die Empfehlung K=4 / Y=33 % / X=10 % für den Erstübergang.

---

## 7. Verankerung & CI (Gate-C3-1-Erfüllung)

- Die **finalen** Werte (N, T, K, Y, X, T_min, T_endorse_min) werden **vorab festgeschrieben + chain-agnostisch verankert**, BEVOR der Ramp-up startet (ADR §A / CEP-3) — kein opportunistisches Nachjustieren.
- **CI-/Anchoring-seitige Erzwingung der Invarianten** (§2): `1/K ≤ Y`, `X ≤ Y`, `X < 0.5`, `Y < 0.5`. Eine Wertekombination, die eine Invariante verletzt, ist **ungültig** (kein Übergang).
- Die deterministische Cluster-Funktion (§3) + X/Y-Mess-Funktion (§2) sind Teil des **Implementation-Contract IC** (Bau-Phase); sie laufen über die verankerten Snapshots, sodass jeder Verifier K/X/Y reproduziert.

---

## 8. Offene Werte-Entscheidungen (Lars)

- **K, Y, X** final aus Korridor (Empfehlung K=4 / Y=33 % / X=10 %).
- **T_min** (governance-Trust-Schwelle für RP-Qualifikation / N-Zählung).
- **T_endorse_min** (Mindest-Trust eines Endorsers, §5).
- Reife-Nachschärfungs-Politik für Y/K (§6) — wann/wie eng, über CEP-2.

**Diese Spec ist Implementation-Contract, kein Design.** Sie macht K/X/Y eindeutig anwendbar; die Zahlen setzt Lars, die Invarianten + Algorithmen + Schutzregeln sind hier normativ fixiert.
