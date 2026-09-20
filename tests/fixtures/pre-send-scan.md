# pre-send-scan.md — Pre-Send-Scan für @moltrust

Zweck: die maschinell prüfbare Hälfte des Voice-Gates. `anti-KI-Sprech.md` sagt,
was nicht vorkommen darf, `my-voice-en.md` sagt, wie LKK baut — beide sind Prosa
und gehen dem Drafter als Systemprompt mit. Diese Datei ist der Teil, der vor dem
Senden **mechanisch** läuft und entweder durchlässt oder blockt.

Diese Datei ist die Quelle, nicht die Kopie. `agents/voice_gate.py` in
`MoltyCel/moltrust-api` liest die ` ```yaml `-Blöcke unten zur Laufzeit aus dem
Shallow-Clone von moltrust-web und baut daraus seine Prüfungen. Eine Regel, die
hier steht, wirkt beim nächsten Lauf; eine Regel, die nur im Python steht,
existiert nicht. Wortlisten für Gate 2 (a) kommen aus `anti-KI-Sprech.md` §1/§2
und werden ebenfalls zur Laufzeit geparst, damit beide Dateien nicht auseinanderlaufen.

Blockiert heißt: der Entwurf geht per Telegram raus statt auf X. Nichts wird
stillschweigend abgeschwächt.

---

## Aufbau

**Gate 1** prüft **jeden Satz**, in allen drei Positionen — Opener, Mitte, Coda.
Die sechs Muster sind die, die durch einen reinen Wortfilter rutschen, weil jedes
einzelne Wort darin unverdächtig ist.

**Gate 2** prüft den Beitrag als Ganzes: Wortverbote, Struktur-Tells, Dichte,
Aufmacher, Links, Substanz, Zahlen.

Ein Entwurf geht raus, wenn **beide** Gates durchlaufen.

### Positionen

Pro Tweet ist der erste Satz der **Opener**, der letzte die **Coda**, alles
dazwischen **Mitte**. Ein Tweet aus einem Satz ist Opener und Coda zugleich.
Segmente, die nur aus einer URL bestehen, zählen nicht als Satz.

---

## Gate 1 — Satzebene

### (a) Kontrapunkt

Antithese als Bauprinzip: „not X, but Y", „nicht X, sondern Y", „less A, more B".
Die Aussage wird direkt gesetzt, ohne Gegenpol-Rahmen. Gilt in jeder Position,
auch im Nebensatz.

```yaml
id: g1a
label: Kontrapunkt („not X, but Y" / „nicht X, sondern Y")
gate: 1
scope: sentence
positions: [opener, middle, coda]
patterns:
  - '\bnot\s+[^.!?,;]{1,50},?\s+but\s+'
  - '\bnicht\s+[^.!?,;]{1,50},?\s+sondern\b'
  - "\\b(?:it|that|this)'?s\\s+not\\b[^.!?]{0,60}[—–-]\\s*(?:it|that|this)'?s\\b"
  - "\\b(?:it|that|this)\\s+is\\s+not\\b[^.!?]{0,60}[—–-]\\s*(?:it|that|this)\\s+is\\b"
  - '\bdas\s+ist\s+nicht\b[^.!?]{0,60}[—–-]\s*das\s+ist\b'
  - "\\bisn'?t\\b[^.!?]{1,50}\\bit'?s\\b"
  - '\bless\s+\w+,\s*more\s+\w+'
  - '\bweniger\s+\w+,\s*mehr\s+\w+'
```

### (b) Validierungs-Opener

Zustimmung als Einstieg. Ein Satz, der mit einem Lob oder einer Bestätigung des
Gegenübers beginnt, sagt nichts und kostet den Platz, an dem die Sache stehen
müsste. Fängt jeden Satz ab, der so anfängt, nicht nur den ersten.

```yaml
id: g1b
label: Validierungs-Opener (Zustimmung als Einstieg)
gate: 1
scope: sentence
positions: [opener, middle, coda]
anchor: sentence_start
lexicon: validation_openers
```

```yaml
lexicon: validation_openers
terms_en:
  - great question
  - good question
  - great point
  - good point
  - excellent point
  - fair point
  - fair enough
  - exactly
  - absolutely
  - totally
  - agreed
  - i agree
  - i couldn't agree more
  - couldn't agree more
  - you're right
  - you are right
  - that's right
  - that's true
  - so true
  - well said
  - love this
  - this is great
  - nice one
  - spot on
  - good catch
  - great thread
  - thanks for sharing
  - happy to
terms_de:
  - genau
  - stimmt
  - richtig
  - absolut
  - guter punkt
  - sehr guter punkt
  - gute frage
  - da hast du recht
  - du hast recht
  - sehe ich auch so
  - volle zustimmung
  - genau so
  - gut gesagt
  - starker thread
  - danke fürs teilen
```

### (c) Parallel-Negation

„no X, no Y, no Z" — drei gleich gebaute Verneinungen als Rhythmus. Ab drei
greift die Regel; zwei sind noch Aussage. Läuft über Satzgrenzen, weil das Muster
gern als drei kurze Sätze erscheint.

```yaml
id: g1c
label: Parallel-Negation („no X, no Y, no Z")
gate: 1
scope: part
positions: [opener, middle, coda]
patterns:
  - '\bno\s+\w+\s*[,;.]\s*no\s+\w+\s*[,;.]\s*no\s+\w+'
  - '\bnot\s+\w+\s*[,;.]\s*not\s+\w+\s*[,;.]\s*not\s+\w+'
  - '\bwithout\s+\w+\s*[,;.]\s*without\s+\w+\s*[,;.]\s*without\b'
  - '\bkein\w*\s+\w+\s*[,;.]\s*kein\w*\s+\w+\s*[,;.]\s*kein\w*'
  - '\bnicht\s+\w+\s*[,;.]\s*nicht\s+\w+\s*[,;.]\s*nicht\s+\w+'
  - '\bohne\s+\w+\s*[,;.]\s*ohne\s+\w+\s*[,;.]\s*ohne\b'
```

### (d) Emphatische Identitäts-Coda

„That's who we are", „Das ist MolTrust". Der Schluss zeigt auf die eigene Identität
statt die Aussage zu machen. Nur in der Coda, weil das Muster dort sitzt.

```yaml
id: g1d
label: Emphatische Identitäts-Coda („That's who we are")
gate: 1
scope: sentence
positions: [coda]
patterns:
  - "\\bthat'?s\\s+(?:who|what)\\s+we\\s+(?:are|do|build|stand\\s+for)\\b"
  - '\bthat\s+is\s+(?:who|what)\s+we\s+(?:are|do)\b'
  - '\bthis\s+is\s+(?:who|what)\s+we\s+(?:are|do)\b'
  - "\\bthat'?s\\s+(?:MolTrust|MoltGuard|MoltProof)\\b"
  - "\\bthat'?s\\s+the\\s+\\w+\\s+way\\b"
  - "\\bthat'?s\\s+(?:the\\s+)?(?:point|mission|promise|idea)\\s+of\\s+MolTrust\\b"
  - '\bwe\s+are\s+MolTrust\b'
  - '\bdas\s+ist\s+(?:MolTrust|MoltGuard|wer\s+wir\s+sind|was\s+wir\s+tun)\b'
  - '\bdafür\s+steh(?:en\s+wir|t\s+MolTrust)\b'
```

### (e) Wertende Prädikat-Kopula

SUBJEKT + ist + WERTUNG, bezogen auf das Gegenüber oder eine Entscheidung:
„Your approach is smart", „die Entscheidung ist richtig". Greift auch im
Nebensatz, weil das Urteil dort genauso steht.

Zwei Schärfen: `eval_core` sind Wörter, die fast nur als Urteil über Menschen und
Entscheidungen vorkommen — die blocken allein hinter jeder Kopula. `eval_context`
sind Wörter, die auch sachlich gemeint sein können („the market is strong") — die
blocken nur, wenn davor ein Subjekt aus `judged_subjects` steht.

```yaml
id: g1e
label: Wertende Prädikat-Kopula (SUBJEKT + ist + WERTUNG)
gate: 1
scope: sentence
positions: [opener, middle, coda]
rule: copula_evaluation
```

```yaml
lexicon: copula
terms_en: [is, are, was, were]
terms_de: [ist, sind, war, waren]
```

```yaml
lexicon: eval_core
terms_en:
  - smart
  - clever
  - wise
  - brilliant
  - excellent
  - impressive
  - admirable
  - commendable
  - outstanding
  - genius
  - thoughtful
  - sensible
  - insightful
  - astute
  - spot-on
terms_de:
  - klug
  - brillant
  - exzellent
  - beeindruckend
  - hervorragend
  - durchdacht
  - vernünftig
  - mutig
  - scharfsinnig
  - bewundernswert
```

```yaml
lexicon: eval_context
terms_en: [right, correct, good, great, solid, strong, sound, bold, sharp, elegant, perfect]
terms_de: [richtig, gut, stark, sinnvoll, elegant, perfekt, treffend]
```

```yaml
lexicon: judged_subjects
terms_en:
  - your
  - yours
  - you
  - their
  - his
  - her
  - the decision
  - this decision
  - that decision
  - the approach
  - the choice
  - the move
  - the call
  - the idea
  - the point
  - the question
  - the answer
  - the argument
  - the take
  - the proposal
  - the spec
  - the paper
  - the team
  - the project
  - the launch
terms_de:
  - dein
  - deine
  - deinem
  - deinen
  - ihr
  - ihre
  - sein
  - seine
  - die entscheidung
  - der ansatz
  - die wahl
  - der zug
  - der punkt
  - die idee
  - die frage
  - die antwort
  - das argument
  - der vorschlag
  - das team
  - das projekt
```

### (f) Wertender Füller als Urteil

„interesting", „important", „powerful" — ein Urteil, das als Beobachtung auftritt.
Der Satz darf das Wort tragen, wenn im selben Satz ein Beleg steht: eine Zahl, ein
Zitat oder eine Quelle. Ohne Beleg blockt es.

```yaml
id: g1f
label: Wertender Füller ohne Beleg
gate: 1
scope: sentence
positions: [opener, middle, coda]
rule: unsupported_judgement
lexicon: judgement_fillers
```

```yaml
lexicon: judgement_fillers
terms_en:
  - interesting
  - important
  - powerful
  - significant
  - notable
  - remarkable
  - compelling
  - fascinating
  - crucial
  - essential
  - valuable
  - meaningful
  - striking
  - profound
  - groundbreaking
  - transformative
terms_de:
  - wichtig
  - bedeutend
  - mächtig
  - bemerkenswert
  - wesentlich
  - entscheidend
  - zentral
  - erheblich
  - spannend
  - interessant
  - aufschlussreich
  - tiefgreifend
  - wegweisend
```

---

## Gate 1 — Zusätzliche Muster

Dieselbe Satzebene, gleiche Blockwirkung.

### Superlativketten

Zwei oder mehr Superlative in einem Satz.

```yaml
id: g1x_superlative
label: Superlativkette (≥2 Superlative im Satz)
gate: 1
scope: sentence
positions: [opener, middle, coda]
rule: count_threshold
threshold: 2
patterns:
  - '\bmost\s+\w+'
  - '\b(?:the\s+)?(?:best|worst|fastest|largest|biggest|smallest|highest|lowest|strongest|greatest|cheapest|safest|hardest|easiest|first|only)\b'
  - '\b(?:größte|beste|schnellste|höchste|niedrigste|stärkste|billigste|sicherste|einzige)\w*\b'
```

### Leere Antithesen

„It's not just a product — it's a movement."

```yaml
id: g1x_empty_antithesis
label: Leere Antithese („not just X — it's Y")
gate: 1
scope: sentence
positions: [opener, middle, coda]
patterns:
  - "\\bnot\\s+just\\s+[^.!?]{0,50}[—–-]\\s*(?:it'?s|its|it\\s+is|a\\b|an\\b)"
  - '\bmore\s+than\s+(?:just\s+)?an?\b[^.!?]{0,40}[—–-]'
  - '\bnicht\s+nur\s+[^.!?]{0,50}[—–-]'
```

### Rhetorische Frage als Opener

Der erste Satz endet auf ein Fragezeichen.

```yaml
id: g1x_rhetorical_opener
label: Rhetorische Frage als Aufmacher
gate: 1
scope: sentence
positions: [opener]
rule: ends_with_question
```

### Triade mündend in rhetorische Frage

Drei kurze Parallelsätze, dann eine Frage.

```yaml
id: g1x_triad_question
label: Triaden-Parallelismus → rhetorische Frage
gate: 1
scope: part
rule: triad_then_question
max_sentence_chars: 90
min_run: 3
```

### Fragment-Coda

Vollständige Aussage, dann ein kurzes nachgestelltes Fragment als Effekt.
Mechanisch: die Coda ist kurz und trägt kein finites Verb.

```yaml
id: g1x_fragment_coda
label: Fragment-Coda (Nachschlag ohne Verb)
gate: 1
scope: sentence
positions: [coda]
rule: verbless_short_coda
max_chars: 50
lexicon: verb_hints
```

```yaml
lexicon: verb_hints
terms_en:
  - is
  - are
  - was
  - were
  - be
  - been
  - has
  - have
  - had
  - do
  - does
  - did
  - can
  - could
  - will
  - would
  - should
  - may
  - might
  - must
  - says
  - said
  - shows
  - showed
  - moved
  - move
  - moves
  - took
  - take
  - takes
  - get
  - gets
  - make
  - makes
  - run
  - runs
  - hold
  - holds
  - sit
  - sits
  - cost
  - costs
  - went
  - go
  - goes
  - came
  - come
  - comes
  - keep
  - keeps
  - need
  - needs
  - work
  - works
  - fail
  - fails
  - check
  - checks
terms_de:
  - ist
  - sind
  - war
  - waren
  - hat
  - haben
  - hatte
  - kann
  - können
  - wird
  - werden
  - muss
  - müssen
  - zeigt
  - zeigen
  - liegt
  - liegen
  - kostet
  - steht
  - stehen
  - geht
  - gehen
  - bleibt
  - bleiben
```

---

## Gate 2 — Beitragsebene

### (a) Wortverbote

Die Listen kommen aus `anti-KI-Sprech.md` §1 (DE) und §2 (EN) und werden zur
Laufzeit geparst. Hier steht keine Kopie — eine Kopie würde auseinanderlaufen.

```yaml
id: g2a
label: Wortverbote (anti-KI-Sprech §1/§2)
gate: 2
scope: part
rule: banned_words_from_anti_ki
```

### (b) Struktur-Tells

Die Satzmuster aus `anti-KI-Sprech.md` §3/§5, die Gate 1 nicht schon abdeckt.

```yaml
id: g2b
label: Struktur-Tells (anti-KI-Sprech §3/§5)
gate: 2
scope: part
patterns:
  - '\bwhat\s+\w+\s+(?:does|provides|matters|means)\s+is\b'
  - '\b(?:two|three|four)\s+(?:things|properties|reasons|points)\b[^.?!]*[:.]'
  - "\\bthat(?:'s| is)\\s+the\\s+(?:claim|point|gap|question|problem)\\b"
  - '\bthe\s+(?:through-line|common\s+thread)\b'
  - '\bthe\s+interesting\s+question\s+is\b'
  - '\bworth\s+pulling\s+apart\b'
  - '\bthe\s+right\s+altitude\b'
  - '\bas\s+we\s+will\s+see\b'
  - '\bdieser\s+Abschnitt\s+behandelt\b'
```

### (c) Gegensatz-Dichte

Höchstens ein Gegensatzpaar pro Tweet (anti-KI-Sprech §3, Eintrag 2026-09-18).

```yaml
id: g2c
label: Gegensatzpaar-Dichte (max. 1 pro Tweet)
gate: 2
scope: part
rule: count_threshold
threshold: 2
patterns:
  - '\bnot\b[^.?!]{0,40}\bbut\b'
  - '\brather\s+than\b'
  - '\binstead\s+of\b'
  - '\bwhereas\b'
  - '\bversus\b'
  - '\bvs\.?\b'
```

### (d) Aufmacher

Der Hook gehört der Sache. Kein „we", „our", kein Produktname als erstes Wort
(anti-KI-Sprech §3, Selbstbezug im Aufmacher).

```yaml
id: g2d
label: Aufmacher ohne Selbstbezug
gate: 2
scope: thread
rule: opener_self_reference
patterns:
  - '^\s*we\b'
  - '^\s*our\b'
  - '^\s*wir\b'
  - '^\s*unser'
  - '^\s*moltrust\b'
  - '^\s*moltguard\b'
  - '^\s*introducing\b'
  - '^\s*announcing\b'
  - "^\\s*i'?m\\s+(?:excited|proud|happy)\\b"
```

### (e) Link-Disziplin

Kein Link im Hook, genau ein Link, und der steht im letzten Tweet. Ein
Einzelpost ist Hook und letzter Tweet zugleich — dort gehört der Link hin.
Für Replies (Reply-Radar) gilt stattdessen null Links.

```yaml
id: g2e
label: Link-Disziplin
gate: 2
scope: thread
rule: link_discipline
expected_links: 1
```

### (f) Substanz-Boden

Mindestens eine konkrete Zahl im Beitrag, jeder Tweet höchstens 280 Zeichen,
kein leerer Entwurf.

```yaml
id: g2f
label: Substanz-Boden
gate: 2
scope: thread
rule: substance_floor
max_chars: 280
```

### (g) Zahlenprüfung gegen die Quelle

Jede Zahl ab drei Stellen im Entwurf muss von einer Zahl im Quelltext getragen
sein. Drei Stellen ist die Untergrenze, weil zweistellige Angaben mit allem
kollidieren. Ohne Quelltext entfällt die Prüfung — der Digest liefert keinen.

Zwei Ergänzungen, beide aus dem ersten Testlauf:

- **Skalierte Angaben zählen unabhängig von der Stellenzahl.** „$9,9M" zeigt zwei
  Ziffern und behauptet 9.900.000. Suffixe `k`, `M`, `B`, `bn`, `Mio`, `Mrd`,
  `Tsd`, `thousand`, `million`, `billion` werden ausgerechnet und mitgeprüft.
- **Abgleich über den Wert, nicht über die Ziffernfolge.** Die Quelle schreibt
  `6188051.748`, der Entwurf `$6.2M` — richtig gerundet und trotzdem keine
  Zeichenübereinstimmung. Geprüft wird deshalb auf eine Quellzahl innerhalb von
  `tolerance`; exakt übereinstimmende Ziffernfolgen (Jahreszahlen, IDs) gehen
  weiterhin direkt durch.

```yaml
id: g2g
label: Zahlen gegen die Quelle
gate: 2
scope: thread
rule: numbers_grounded
min_digits: 3
tolerance: 0.02
```

---

## Änderungslog

- 2026-09-21: Datei angelegt. Gate 1 (a)–(f) auf Satzebene plus fünf
  Zusatzmuster; Gate 2 übernimmt die vorherigen sechs Prüfungen und bekommt (g)
  Zahlenprüfung als eigene Regel. Wortlisten für Gate 2 (a) aus
  `anti-KI-Sprech.md` §1/§2 statt als Kopie. Auslöser: die erste Fassung des
  Scans prüfte nur den Beitrag als Ganzes und ließ die satzweisen Muster
  (Kontrapunkt, Validierungs-Opener, Parallel-Negation, Identitäts-Coda,
  wertende Kopula, Urteils-Füller) durch.
