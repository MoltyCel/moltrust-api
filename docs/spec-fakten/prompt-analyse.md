# Prompt-Historie — aggregierte Analyse

**Stand:** 2026-07-14 · **Zeitraum:** 2026-06-20 → 2026-07-14 (25 Tage) · **Korpus:** n=463 eigene Prompts
**Quellen:** `~/.claude/projects/**/*.jsonl` (autoritativ für Prompt-Text) + `~/.claude/history.jsonl` (nur Cross-Reference)
**Guardrail:** nur `role==user`-Turns, Secret-Redact-Pass vorgeschaltet, keine Rohprompts/-daten in dieser Datei.

---

## 0. Umfang, Quellen, Methodik

- **130 `.jsonl`-Files** in `~/.claude/projects/` = **22 primäre User-Sessions** + **98 Subagent-Sidechains** + **10 Snapshot/Agent/Meta**. Die „echte" Session-Zahl (eigene Konversationen) ist **22**; die 98 Sidechains sind Subagent-Transkripte (deckt sich mit 99 `Agent`-Calls + Workflow-Fanout).
- **1 Claude-Projektverzeichnis** (`-Users-kerstenkroehl`); **42 reale Arbeitsverzeichnisse** (`cwd`).
- `history.jsonl` (521 Zeilen) deckt nur **22 Sessions** ab und kürzt `display` auf ~500 Zeichen → für Längen/Struktur **ungeeignet**; Prompt-Text-Metriken daher aus den Session-Files.
- **Ausgeschlossen vor Auswertung:** `isSidechain` (Subagent-Prompts), `isMeta`, `tool_result`/Image-Turns, `<task-notification>`- und Slash-Command-Wrapper, sowie injizierte `<system-reminder>`-Blöcke (aus der Längenmessung entfernt).

## 1. Redact-Pass

- Prompts mit Secret-/`.env`-Treffer **ausgeschlossen: 19** (Muster: `ghp_` / `github_pat_` / `sk_live_` / `whsec_` / `sk-ant-` / `BASE_WALLET_KEY` / `xoxb-` / `AKIA` / env-`KEY=VALUE` / `.env`) — Fundstellen nie ausgegeben.
- **Verbleibender Prompt-Korpus: 463** eigene, getippte Prompts.

## 2. Prompt-Länge & Struktur (n=463)

| Metrik | Median | P90 | Max |
|---|---|---|---|
| Zeichen | **642** | **3.778** | 21.608 |
| Wörter | **82** | **477** | — |

- **Task-Block-Struktur** (`GROUND TRUTH` \| `GUARDRAILS` \| `PRE-PUBLISH-GATE`): **5 / 463 = 1,1 %**.

## 3. Tool-Call-Verteilung (assistant-Turns, alle Sessions)

- assistant-Turns: 14.533 · **tool_use-Calls gesamt: 5.965**
- **bash (`Bash`) 52,9 %** (3.158) · **str_replace (`Edit`) 10,1 %** (603) · **view (`Read`) 9,2 %** (549) → zusammen **~72 %**
- Übrige: `WebFetch` 623 · `WebSearch` 348 · `Write` 250 · `ToolSearch` 103 · `TaskUpdate` 103 · `Agent` 99 · `TaskCreate` 69 · `AskUserQuestion` 30 · `Skill` 15 · `SendMessage` 5 · `TaskOutput` 4 · `Monitor` 2 · `TaskStop` 2 · `TaskList` 1 · MCP-Remote 1
- Web-Recherche (`WebFetch`+`WebSearch` = 971) ist der viertgrößte Block.

## 4. Multi-Turn-Iteration (Proxy)

- Sessions mit **>5 eigenen Prompts: 15 / 22 = 68,2 %**
- Prompts pro primärer Session: **Median 15 · Ø 21,0 · Max 88**
- Proxy für längeres Debugging/Iteration; „gleiches File/Thema" nicht exakt verifiziert.

## 5. Traffic nach Arbeitsverzeichnis (Top 10, Record-Volumen)

| cwd | Records | Sessions |
|---|---|---|
| `~` | 9.082 | 79 |
| `~/moltrust-web` | 4.901 | 15 |
| `~/moltrust-api-compliance-wt` | 2.402 | 11 |
| `~/moltrust-api-eidas-wt` | 1.280 | 24 |
| `~/moltrust-api` | 1.021 | 10 |
| `~/moltrust-web-proofgap-wt` | 809 | 1 |
| `~/Downloads` | 718 | 5 |
| `~/Downloads/layer3-run` | 439 | 2 |
| `~/.claude/…/memory` | 349 | 3 |
| `~/aae-conformance-vectors` | 244 | 2 |

**Schwerpunkt:** `moltrust-web` + die `moltrust-api`-Worktrees (`compliance`/`eidas`/Basis) tragen zusammen ~11,3k Records — Website- und API-Compliance-Track dominiert; `~` (Home) ist Sammelbecken für Ad-hoc-/SSH-/Server-Sessions.

---

## 6. Abgeleitete Regeln (→ `~/.claude/CLAUDE.md`, §Betriebsregeln 2026-07-14)

- **GUARDRAIL-PFLICHT PROD/COMPLIANCE** — Task-Blöcke Pflicht für alle Compliance-/Regulatorik-Worktrees (moltrust-web, *-compliance-wt, *-eidas-wt, analoge), nicht nur Deploy. *(Basis: nur 1,1 % nutzten die Struktur trotz Prod-Kritikalität.)*
- **SUBAGENT-FANOUT BEI ITERATION** — Ketten >5 Turns zum gleichen Thema in Subagent/Task auslagern. *(Basis: 68 % der Sessions >5 Prompts, Median 15, Max 88.)*
- **MODELL-ROUTING** — Opus 4.8 default für Prod/Compliance + Governance/Peer-Review-Konsolidierung; Sonnet 5 für Ad-hoc/Home/Template. *(Basis: 72 % bash/edit/read, Schwerpunkt web + compliance/eidas-wt.)*
- **PEER-REVIEW-FILE-SPLIT** — Quelldateien fürs externe Peer-Review pro Reviewer/Modell splitten, kein konkateniertes .txt.
- **OPS-ISOLATION (Backlog)** — dediziertes `ops/`-Arbeitsverzeichnis mit eigenem CLAUDE.md für Ad-hoc-/SSH-Arbeit aus `~`. *(Basis: `~` größter Bucket, 9.082 Records / 79 Sessions, ohne eigenes CLAUDE.md.)*
