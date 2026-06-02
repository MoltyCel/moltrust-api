# ADR — CEP (Combined Enforcement Protocol) Governance
**Status:** **KONZEPT** (DRAFT — noch NICHT Proposal, design-only). Eigenes ADR nötig: Recon → Proposal → Review-Runden (wie ADR-D3).
**Datum:** 2026-06-02 · **Autor:** Lars Kroehl
**Bezug:** D3 MANDATE-Enforcement Komponente 3 (enforce-mode-Chokepoint). CEP entscheidet, WER das scharfe enforce-Umschalten autorisiert.

## Problem
Die Autorität, den enforce-mode scharf zu schalten (DENY blockiert eine Aktion), darf NICHT hängen an:
- **Personen** (Gründer) — überlebt keinen 10-Jahres-Horizont, ist ein Single Point of Trust.
- **einer einzelnen Chain** — Base/eine L2 kann verschwinden, zensieren, forken.
- **einer einzelnen Instanz** — eine MolTrust-Instanz/Node ist abschaltbar/kompromittierbar.
Ziel: **technik- und personenunabhängige** Enforcement-Autorität, 10-Jahres-Horizont.

## Gewählte Richtung: objektive Bedingungen
Bewusst NICHT:
- **ZK-Proofs** — falsches Werkzeug (löst Privacy, nicht Governance-Autorität).
- **MPC / Node-gebunden** — bindet Autorität an einen Betreiber-Satz.
- **Single-Chain-Governance** — bindet an eine Chain (genau das Problem).
Stattdessen: **objektiv prüfbare Bedingungen** auf MolTrust-eigenen Primitiven.

## 3 kombinierte Bausteine (alle auf bestehenden MolTrust-Primitiven)
**(a) Regel-Versionen chain-agnostisch verankert.** Enforcement-Regel-Versionen via TechSpec-§6-Anchoring, **Quorum über mehrere Chains** — überlebt das Verschwinden einzelner Chains. → löst "nicht an Base binden".
**(b) Stimmgewicht/Veto an behavioral trust score gebunden.** Wer abstimmt/vetot, gewichtet nach **behavioral trust score** (Sybil-resistent via Jaccard-Cluster + Vertical-Diversity, bestehende Swarm-Primitiven). → löst "wer stimmt ab" OHNE Plutokratie (kein Token-Kauf-Gewicht).
**(c) Zeitschloss + öffentliches Veto.** Lange Wartezeit; **Widerspruch statt aktive Zustimmung** (Aktionen treten in Kraft, wenn niemand begründet vetot). Überbrückt kleine Frühphasen-Basis, technologieunabhängig.

## Ramp-up: Gründer → CEP (automatischer Übergang)
Enforcement-Regeln werden **zunächst durch den Gründer** gesetzt. **Automatischer** Übergang zu CEP, sobald **VIER Bedingungen GLEICHZEITIG** erfüllt sind — **AND, nicht OR** (ein einzelner Schwellwert ist manipulierbar/willkürlich):
1. **Mindestzeit verstrichen** (Anti-Rush).
2. **>= N** behavioral-qualifizierte, **Sybil-geprüfte** RPs.
3. verteilt über **>= M Verticals** (Diversität).
4. **kein Akteur/Cluster > X%** trust-gewichtetes Stimmgewicht (Anti-Konzentration).

**Zahlen N / M / X / Zeit werden VORAB festgeschrieben + chain-agnostisch verankert, BEVOR der Ramp-up startet** — kein opportunistisches Nachjustieren, auch nicht durch den Gründer.

## OFFENE KERNFRAGE (Blocker für Proposal)
**Wer MISST, ob die 4 Bedingungen erfüllt sind?** Interne Messung = neuer Single Point of Failure (der Auslöser wäre dann doch wieder personen-/instanzgebunden). Der Auslöser **MUSS unabhängig verifizierbar** sein:
- RP-Zählung, Cluster-Prüfung, Vertical-Verteilung, Stimmgewichts-Konzentration **aus öffentlich verankerten Daten von JEDEM nachrechenbar** — nicht interne Behauptung.
- Schließt den Kreis zum **Transparenz-/Anchoring-Prinzip**: dieselben Primitiven, die Enforcement auditierbar machen, machen auch den CEP-Auslöser auditierbar.

## Konsequenz für D3 / Komponente 3
- CEP blockiert Komponente 3 (enforce-mode-Chokepoint) **NICHT** für `advisory`/`none`-Mode (DENY wird nur geloggt — der Evaluator läuft scharf, blockiert aber nicht).
- Das **scharfe `enforce`-Umschalten** (DENY blockiert) hängt an der CEP-Entscheidung.

## Nächste Schritte (eigenes ADR, wie ADR-D3)
1. **Recon:** welche MolTrust-Primitiven liefern N/M/X/Zeit verifizierbar (Swarm-Trust, Anti-Collusion, TechSpec-§6-Anchoring, multi-chain-Quorum)?
2. **Proposal:** Zahlen + Verankerungs-Mechanismus + unabhängiger Auslöser-Verifikationspfad.
3. **Review-Runden** (Multi-Model security, wie ADR-D3) — insb. die offene Kernfrage (unabhängige Messung) adversarisch prüfen.
