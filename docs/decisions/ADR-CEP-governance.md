# ADR — CEP (Combined Enforcement Protocol) Governance

**Status:** **PROPOSAL** (Review-Runde, design-only). **HARD GATE:** blockiert die Implementierung von D3 **Komponente 3** (scharfes `enforce`-Umschalten). Kein Code auf Komponente 3 vor Sign-off (3-Reviewer-Konsens wie ADR-D3).
**Supersedes:** `docs/decisions/ADR-CEP-governance-DRAFT.md` (KONZEPT/DRAFT). DRAFT bleibt als **Audit-Trail** erhalten — NICHT löschen (Kette: DRAFT → PROPOSAL, analog v1→v2→v3 bei ADR-D3).
**Datum:** 2026-06-03 · **Autor:** Lars Kroehl
**Bezug:** D3 MANDATE-Enforcement Komponente 3 (enforce-mode-Chokepoint, siehe `ADR-D3-mandate-enforcement-v3.md`). CEP entscheidet, **WER** das scharfe `enforce`-Umschalten autorisiert — personen-, technik- und instanz-unabhängig, 10-Jahres-Horizont.
**Recon-Basis (verifiziert 2026-06-03, Live-Checkout `7e5a471`):**
- (a) `app/provenance/anchor.py:120` → `async def anchor_batch(conn, anchor_fn)` + Pure-Python-Merkle (`merkle_root`/`merkle_proof`). Seam vorhanden.
- (a) `app/skale_anchor.py:3` → nur `CHAIN_CONFIG = {…}`, **keine** `def`/`class`. Multi-Chain = Stub.
- (b) `app/swarm/trust_score.py:149` `compute_phase2_score`, `app/swarm/anti_collusion.py:48` `compute_sybil_penalty`. Abfragbar — ABER Cache-/Seed-Pfad gibt `sybil_penalty: 0.0` (`trust_score.py:189,224`).
- (c) **Kein** `timelock`/`quorum`/`veto`/`ramp_up` in `app/`/`migrations/` (nur unverwandtes statisches `GOVERNANCE_RULES`-Dict für Agent-Klassen). Greenfield.
- enforce-State: Live-`constraint_mode` ∈ `{none, inherit, restrict}` (`app/main.py:5675`). **Kein `enforce`**. ADR-D3-v3 fordert `{none, inherit, enforce}` → **Drift** + fehlender Ziel-State.
- RP-Quelle: `KNOWN_CALLERS` ist ein IP-Präfix-Dict (`app/main.py:6910`) — **kein** Vertical/DID. Keine First-Class-RP-Registry.

---

## Problem (Kernproblem)

Die Autorität, den `enforce`-mode scharf zu schalten (DENY blockiert real eine Aktion, nicht nur Logging), darf **NICHT** hängen an:
- **Personen** (Gründer) — überlebt keinen 10-Jahres-Horizont, ist ein Single Point of Trust.
- **einer einzelnen Chain** — Base/eine L2 kann verschwinden, zensieren, forken.
- **einer einzelnen Instanz/Node** — abschaltbar, kompromittierbar.

Ziel: **technik- und personenunabhängige** Enforcement-Autorität über 10 Jahre. Die in ADR-D3-v3 für Komponente 3 spezifizierte M-of-N-Signatur-Lösung (Governance-Key + zweiter unabhängiger Key) ist ein korrekter **kurzfristiger** Übergangsmechanismus, bleibt aber **schlüssel-/personengebunden** — CEP ist die langfristige Ablösung dieser Autoritätsbindung durch **objektive Bedingungen**.

---

## Ehrliche Ausgangslage (Recon-Realität, explizit benannt)

MolTrust ist **HEUTE zu unreif für CEP**:
- ~**67 RPs/Agents** insgesamt; Vertical-Verteilung stark **schief** (z. B. core 57 / skill 3 / shopping 1).
- Das Netz ist **zu dünn für ein trust-gewichtetes Quorum**: zu wenige unabhängige, sybil-geprüfte Akteure, zu wenig Vertical-Diversität, einzelne Cluster dominierten jedes Stimmgewicht.

Das ist **kein Defekt, sondern der Grund für die Ramp-up-Phase.** CEP **darf heute nicht greifen**, weil die Bedingungen real nicht erfüllt sind. **Gründer-gesetztes Enforcement ist der legitime Übergangszustand** — explizit, befristet, und mit vorab festgeschriebenem, objektivem Ausstiegspfad. Die Recon-Zahlen oben sind ein Snapshot; sie sind selbst die Eingangsgrößen der Übergangsbedingungen (2)+(3) und MÜSSEN zum Übergangszeitpunkt über die (noch zu bauende) RP-Registry verifizierbar gemessen werden, nicht aus diesem Dokument zitiert.

---

## Gewählte Richtung: objektive Bedingungen

Bewusst **NICHT**:
- **ZK-Proofs** — falsches Werkzeug (löst Privacy, nicht Governance-Autorität).
- **MPC / Node-gebunden** — bindet Autorität an einen Betreiber-Satz.
- **Single-Chain-Governance** — bindet an eine Chain (genau das Problem).

Stattdessen: **objektiv prüfbare, vorab festgeschriebene, öffentlich-verifizierbare Bedingungen** auf MolTrust-eigenen Primitiven.

---

## Architektur-Guards (nicht verhandelbar)

- **Kein Personen-/Gründer-Schlüssel als _dauerhafter_ Anker.** Schlüssel sind nur im Ramp-up zulässig, mit festgeschriebenem Ablösepfad.
- **Kein Single-Chain-Lock-in.** Regel-Versionen + Übergangs-Bedingungen werden über ein **Multi-Chain-Quorum** verankert.
- **Default = sichererer Zustand bei Unklarheit.** Ist der CEP-Auslöser nicht eindeutig/unabhängig nachrechenbar, bleibt Enforcement im **vorherigen** Zustand (kein automatisches Hochschalten auf bloßen Verdacht; kein automatisches Abschalten bestehenden Schutzes). No-Downgrade-Guard aus ADR-D3-v3 gilt fort.
- **Keine zirkuläre Selbstprüfung** — der CEP-Auslöser darf nicht von genau der Instanz behauptet werden, deren Autorität er ablöst.

---

## Drei Bausteine (mit Recon-Realität)

### (a) Chain-agnostisches Anchoring der Regel-Versionen — Seam vorhanden, Multi-Chain = NEUBAU
- **Vorhanden:** `anchor_batch(conn, anchor_fn)` + Merkle in `app/provenance/anchor.py`. Die `anchor_fn`-Injektion **ist** die chain-agnostische Naht: derselbe Merkle-Root kann an mehrere Chains gegeben werden.
- **Lücke:** `app/skale_anchor.py` ist reine `CHAIN_CONFIG` ohne Funktionen. Es existiert **keine** zweite reale Anchoring-Funktion.
- **ADR-Entscheidung:** Eine **zweite Chain** (SKALE *oder* Solana — Auswahl offen, siehe CEP-Decisions) als **Neubau** hinter dieselbe `anchor_fn`-Signatur. Regel-Versionen + Übergangs-Bedingungen werden mit **Quorum über mehrere Chains** verankert (≥ 2 von K Chains bestätigen denselben Root) → überlebt das Verschwinden/Zensieren einzelner Chains. Löst "nicht an Base binden".

### (b) Trust-gewichtetes Veto/Vote — Engine abfragbar, Caveats ZWINGEND explizit
- **Vorhanden:** `compute_phase2_score` + `compute_sybil_penalty`; Sybil-Resistenz via **Jaccard-Cluster + Vertical-Diversity** (bestehende Swarm-Primitiven). Wer abstimmt/vetot, gewichtet nach **behavioral trust score**, nicht nach Token-Besitz → keine Plutokratie.
- **Caveat 1 (kritisch):** Der **Cache-/Seed-Pfad gibt `sybil_penalty = 0.0`** (`trust_score.py:189,224`). Ein naiv aus dem Cache gelesenes Stimmgewicht **ignoriert die Sybil-Strafe**. Für CEP-Stimmgewicht ist eine **frische, nicht gecachte** Berechnung mit aktivem `compute_sybil_penalty` ZWINGEND — sonst ist das Veto sybil-manipulierbar.
- **Caveat 2:** Das Netz ist heute zu unreif (s. o.); Stimmgewicht ist erst nach Erfüllung der Übergangsbedingungen aussagekräftig.
- **ADR-Entscheidung:** Bootstrap-Annahmen + Schwellen **explizit** (siehe Ramp-up). CEP-Stimmgewicht = frisch berechneter Phase-2-Score **mit** Sybil-Penalty, niemals Cache-Pfad.

### (c) Zeitschloss + öffentliches Veto — KOMPLETT GREENFIELD (Hauptteil des Designs)
- **Recon:** 0 Tabellen für `timelock`/`veto`/`quorum`/`govern` (CEP-Sinn). Vollständiger Neubau.
- **Mechanik:** Eine vorgeschlagene Enforcement-Regel-Änderung (oder das Scharfschalten selbst) tritt erst nach **langer Wartezeit** in Kraft, und nur, **wenn niemand begründet vetot** — **Widerspruch statt aktive Zustimmung**. Das überbrückt eine dünne Frühphasen-Basis (man braucht keine aktive Mehrheit, nur das Ausbleiben qualifizierten Widerspruchs), ist technologieunabhängig und gibt jedem Beobachter Zeit, das öffentlich verankerte Vorhaben nachzurechnen und ggf. zu vetoieren.
- Veto-Gewicht = trust-gewichtet (Baustein b, frische Berechnung). Zeitschloss-Parameter + Veto-Schwelle = vorab festgeschrieben + verankert.

---

## Ramp-up → CEP-Übergang: VIER Bedingungen (AND, nicht OR)

Enforcement-Regeln werden **zunächst durch den Gründer** gesetzt. Übergang zu CEP **nur**, wenn **VIER** Bedingungen **GLEICHZEITIG** erfüllt sind (AND — ein einzelner Schwellwert ist manipulierbar/willkürlich), alle **vorab festgeschrieben + chain-agnostisch verankert BEVOR der Ramp-up startet** (kein opportunistisches Nachjustieren, auch nicht durch den Gründer):

1. **Mindestzeit verstrichen** (Anti-Rush).
2. **≥ N** behavioral-qualifizierte, **Sybil-geprüfte** RPs.
3. verteilt über **≥ M Verticals** (Diversität — gegen ein scheinbar großes, aber mono-vertikales Netz).
4. **kein Akteur/Cluster > X %** trust-gewichtetes Stimmgewicht (Anti-Konzentration).

**Zahlen N / M / X / Zeit = Geschäftsentscheidung Lars** (siehe CEP-3 — im ADR als PLATZHALTER + Begründungs-Framework, NICHT geraten).

---

## Voraussetzungen (vor Ramp-up-Start nötig — aus Recon)

| # | Voraussetzung | Recon-Status | Ohne sie … |
|---|---|---|---|
| V1 | **RP-Registry mit Vertical + DID** (First-Class) | **Greenfield** — `KNOWN_CALLERS` ist IP-Präfix-Dict, kein Vertical/DID | Bedingungen (2)+(3) **nicht messbar** |
| V2 | **`enforce`-State** in `constraint_mode` | Live = `{none, inherit, restrict}`, **kein `enforce`**; ADR-D3-v3 fordert `enforce` | Komponente 3 hat keinen scharfen Ziel-Zustand |
| V3 | **Multi-Chain-Anchoring** (Baustein a, 2. Chain) | Stub (`skale_anchor.py` config-only) | Bedingungen + Regel-Versionen nur single-chain verankerbar (Lock-in) |

V1–V3 sind **Implementierungs-Vorbedingungen**, kein Teil dieses Design-Sign-offs — aber ohne sie ist der Übergang weder messbar (V1) noch scharf schaltbar (V2) noch lock-in-frei verankerbar (V3).

---

## OFFENE KERN-ENTSCHEIDUNGEN (Kern des Reviews)

### CEP-1 — ORACLE-PROBLEM (der schwierigste; primärer adversarischer Prüfpunkt)
**WER MISST, ob die 4 Bedingungen erfüllt sind?** Interne Messung = neuer SPOF — der Auslöser wäre dann doch wieder personen-/instanzgebunden, und genau die Instanz, deren Autorität CEP ablöst, behauptete ihren eigenen Übergang.
**Anforderung:** RP-Zählung, Cluster-/Sybil-Prüfung, Vertical-Verteilung und Stimmgewichts-Konzentration MÜSSEN **aus öffentlich verankerten Daten von JEDEM nachrechenbar** sein — nicht interne Behauptung.
**Doppelte Lücke (Recon):** Die Bedingungs-Daten existieren heute **nicht einmal sauber intern** (V1 fehlt) → erst intern-messbar machen, dann öffentlich-verifizierbar verankern.
**Lösungsrichtung (zu schärfen im Review, NICHT final):**
- **Verankerte RP-Registry-Snapshots:** periodische, Merkle-gewurzelte, multi-chain-verankerte Snapshots des RP-Registry-Zustands (DID, Vertical, Sybil-Status, frischer Trust-Score). Der 4-Bedingungs-Check ist dann eine **deterministische, öffentlich nachrechenbare Funktion** über die verankerten Snapshots — kein Vertrauen in eine Live-Abfrage.
- **Attestation/Challenge-Fenster:** während des Zeitschlosses (Baustein c) kann jeder den behaupteten Übergang anhand der verankerten Snapshots nachrechnen und bei Abweichung vetoieren.
- **Status:** **KRITISCHE OFFENE FRAGE** — Blocker für ACCEPTED. Default-Guard greift: solange CEP-1 nicht gelöst ist, kein automatischer Übergang.

### CEP-2 — META-REGEL (wer ändert die Regeln nach dem Übergang?)
Regeln müssen flexibel bleiben (10 Jahre, sich ändernde Bedrohungslage/Regulierung), aber **Regeländerung darf nicht bei einer Instanz liegen.** Vorschlag: Regeländerungen unterliegen **demselben** trust-gewichteten Veto + Zeitschloss wie Enforcement-Entscheidungen (Baustein b+c). **Henne-Ei (Bootstrap):** vor dem Übergang gibt es kein quorumfähiges Gremium für Meta-Regeln → im Ramp-up bleiben Meta-Regeln gründer-gesetzt **und** chain-verankert (öffentlich auditierbar), mit demselben objektiven Ablösepfad. **Status: offen — im Review zu schärfen** (insb. Schutz gegen Meta-Regel-Selbstermächtigung).

### CEP-3 — SCHWELLEN N / M / X / Zeit (Lars-Geschäftsentscheidung)
NICHT raten. Im ADR als **PLATZHALTER** + **Begründungs-Framework**:
- **N (Mindest-RPs):** groß genug, dass kein realistisch finanzierbarer Sybil-Schwarm Bedingung (4) unterläuft; klein genug, in absehbarer Zeit erreichbar. Anker: heutige ~67 RPs.
- **M (Verticals):** > 1 zwingend; sinnvoll ≥ Hälfte der real betriebenen Vertical-Typen, damit kein mono-vertikales Netz qualifiziert. Anker: heutige Schieflage (core dominiert).
- **X (max. Konzentration):** so niedrig, dass ein einzelner Akteur/Cluster auch im Veto kein Alleinentscheidungsrecht hat (Richtwert-Diskussion: deutlich < 50 %, eher im Bereich einer Sperrminoritäts-Logik). Bezug: Jaccard-Cluster aus Anti-Collusion.
- **Zeit:** lang genug gegen Rush/kurzfristige Manipulation, gekoppelt an die Zeitschloss-Dauer aus Baustein c.
**Status: Geschäftsentscheidung — vor Ramp-up-Start festschreiben + verankern.**

### CEP-4 — NETZ-REIFE-BOOTSTRAP (Übergang ohne SPOF im Umschalt-Moment)
Ein trust-gewichtetes Quorum braucht ein reifes Netz, das es heute nicht gibt. **Wie kommt man von gründer-gesetzt zu quorumfähig, ohne dass der Umschalt-Moment selbst ein SPOF ist?**
Vorschlag: Der Umschaltpunkt ist **kein Akt** (keine Person/Instanz "drückt den Knopf"), sondern das **deterministische Ergebnis** der öffentlich verankerten 4-Bedingungs-Funktion (CEP-1) + Ablauf des Zeitschlosses ohne qualifiziertes Veto (Baustein c). Der "Moment" ist damit von jedem nachrechenbar und hat keinen privilegierten Auslöser. **Status: offen** — hängt direkt an CEP-1 (ohne unabhängiges Orakel ist auch CEP-4 nicht SPOF-frei).

---

## Konsequenz für D3 / Komponente 3

- CEP blockiert Komponente 3 **NICHT** für `advisory`/`none`/`inherit`: der Evaluator läuft scharf, DENY wird **nur geloggt** (kein realer Block).
- Das **scharfe `enforce`-Umschalten** (DENY blockiert real) hängt an der CEP-Entscheidung.
- **Verhältnis zu ADR-D3-v3:** Die dort spezifizierte M-of-N-Signatur-Autorisierung (Governance-Key + zweiter Key, Telegram nur Notification, No-Downgrade-Guard) bleibt der **Ramp-up-Mechanismus**. CEP **ersetzt** diese schlüsselgebundene Autorität nach erfülltem Übergang durch objektive Bedingungen. No-Downgrade-Guard und Default-zum-sichereren-Zustand gelten in beiden Phasen.

---

## Status der drei Bausteine (Bau-Realität)

| Baustein | Seam heute | Arbeit |
|---|---|---|
| (a) Multi-Chain-Anchoring | `anchor_batch(conn, anchor_fn)` + Merkle vorhanden; 2. Chain = Stub | 2. reale `anchor_fn` (SKALE/Solana) + Quorum-Logik = **Neubau** |
| (b) Trust-gewichtetes Veto | `compute_phase2_score`/`compute_sybil_penalty` vorhanden | Veto-/Vote-Schicht; **frische** Score-Berechnung erzwingen (kein Cache-Pfad mit `sybil_penalty=0`) |
| (c) Zeitschloss + öffentliches Veto | nichts | **Greenfield** — Hauptteil |
| V1 RP-Registry (Vertical+DID) | `KNOWN_CALLERS` IP-basiert | **Greenfield** |
| V2 `enforce`-State | `{none,inherit,restrict}` | `enforce` ergänzen (ADR-D3-v3-Ziel) |

---

## Nächste Schritte

1. **Review-Runde** (Multi-Model, wie ADR-D3). **Modus-Frage offen:** `technical` (für die Mechanik) vs. `eu-compliance` (für den regulatorischen Governance-Winkel) — **Lars klärt vor dem Review.**
2. Adversarisch insbesondere **CEP-1 (Oracle)** und **CEP-4 (Bootstrap-SPOF)** prüfen.
3. **CEP-3-Schwellen** als Geschäftsentscheidung festlegen, dann vorab festschreiben + multi-chain verankern.
4. Status-Flip **PROPOSAL → ACCEPTED** erst nach Konsens. **HARD GATE bleibt:** kein Code auf D3 Komponente 3 (`enforce`-Scharfschalten) vor Sign-off.

---

## Konsequenzen / Trade-offs

- **Pro:** personen-/technik-/instanz-unabhängig; objektiv + öffentlich nachrechenbar; übersteht Chain-Ausfall; legitimer, befristeter Gründer-Übergang mit hartem Ausstiegspfad.
- **Contra / Risiko:** Komplexität (3 Bausteine + 3 Vorbedingungen, viel Greenfield); CEP-1 ungelöst = der ganze Übergang ungelöst; lange Zeitschlösser kosten Reaktionsfähigkeit (bewusst, zugunsten Anti-Rush); Bootstrap-Henne-Ei (CEP-2/CEP-4) real, nur über den Default-zum-sicheren-Zustand abgesichert, nicht eliminiert.
