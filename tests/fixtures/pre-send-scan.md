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

### Modi und geladene Regeln (ab 07.10.2026)

Jeder Modus nennt die Regeln, die er lädt, als Positivliste. Eine Regel, die in
der Liste des Modus fehlt, läuft in diesem Modus nicht und erscheint im Report
als „nicht geladen". Ein Modus ohne Liste ist ein Fehler, und das Gate läuft
dann gar nicht. Grund (07.10.2026): „nicht anwendbar" stand bisher als Ergebnis
im Report und las sich wie ein Befund, obwohl die Regel nie hätte laufen sollen.

`article` ist der Modus für Blogposts und andere lange Texte. Er lädt g2e
(genau ein Link) nicht, weil ein Artikel seine Belege als Tabelle trägt und
keine Links braucht. g2h (Quellenregel) bleibt beim Modus `reply`.

```yaml
mode_rules:
  thread: [g1a, g1b, g1c, g1c2, g1d, g1e, g1f, g1g, g1h, g1q, g1x_superlative,
           g1x_empty_antithesis, g1x_rhetorical_opener, g1x_triad_question,
           g1x_fragment_coda, g1x_pseudo_cleft_reverse, g1x_em_dash_density,
           g1x_em_dash_appositive, g1x_parallel_definition,
           g1x_overstatement_coda, g1x_scaffold_opener, g1x_thesis_recall_coda,
           g2a, g2b, g2c, g2d, g2e, g2f, g2g]
  post:   [g1a, g1b, g1c, g1c2, g1d, g1e, g1f, g1g, g1h, g1q, g1x_superlative,
           g1x_empty_antithesis, g1x_rhetorical_opener, g1x_triad_question,
           g1x_fragment_coda, g1x_pseudo_cleft_reverse, g1x_em_dash_density,
           g1x_em_dash_appositive, g1x_parallel_definition,
           g1x_overstatement_coda, g1x_scaffold_opener, g1x_thesis_recall_coda,
           g2a, g2b, g2c, g2d, g2e, g2f, g2g]
  reply:  [g1a, g1b, g1c, g1c2, g1d, g1e, g1f, g1g, g1h, g1q, g1x_superlative,
           g1x_empty_antithesis, g1x_rhetorical_opener, g1x_triad_question,
           g1x_fragment_coda, g1x_pseudo_cleft_reverse, g1x_em_dash_density,
           g1x_em_dash_appositive, g1x_parallel_definition,
           g1x_overstatement_coda, g1x_scaffold_opener, g1x_thesis_recall_coda,
           g2a, g2b, g2c, g2d, g2e, g2f, g2g, g2h]
  article: [g1a, g1b, g1c, g1c2, g1d, g1e, g1f, g1g, g1h, g1q, g1x_superlative,
           g1x_empty_antithesis, g1x_rhetorical_opener, g1x_triad_question,
           g1x_fragment_coda, g1x_pseudo_cleft_reverse, g1x_em_dash_density,
           g1x_em_dash_appositive, g1x_parallel_definition,
           g1x_overstatement_coda, g1x_scaffold_opener, g1x_thesis_recall_coda,
           g2a, g2b, g2c, g2d, g2f, g2g]
```

Im Modus `article` bekommt das Gate den ganzen Markdown-Text als einen Teil.
HTML-Kommentare zählen nicht als Text, weil sie vor der Auslieferung entfernt
werden. Überschriften und Tabellenzeilen sind keine Sätze und laufen nicht durch
die Satzregeln. Wortverbote (g2a) und die Zahlenprüfung (g2g) lesen sie mit.
Jeder Absatz zählt als ein Teil, für die Positionen wie für die Gegensatz-Dichte.
Ohne Quelltext läuft der Modus `article` nicht, weil g2g dort geladen ist.

### Produktnamen (präzisiert 07.10.2026)

„Kein Produktname" betrifft die eigenen Produkte: MolTrust, MoltProof, MoltGuard,
AAE. Namen zitierter Dritter (Netze, Standards, Unternehmen, Gerichtsverfahren)
sind zulässig und, wo sie einen Beleg tragen, Pflicht. Grund (07.10.2026): ein
Beleg ohne den Namen seines Urhebers lässt sich nicht prüfen.

### Positionen

Pro Tweet ist der erste Satz der **Opener**, der letzte die **Coda**, alles
dazwischen **Mitte**. Ein Tweet aus einem Satz ist Opener und Coda zugleich.
Segmente, die nur aus einer URL bestehen, zählen nicht als Satz.

### Normalisierung vor dem Abgleich

Die Muster unten sind mit geradem Apostroph und geraden Anführungszeichen
geschrieben. Modelle liefern die typografischen Formen. Vor jedem Abgleich
werden deshalb `’ ‘ ‛ ´ \`` auf `'`, `“ ” „ ‟` auf `"` und geschützte
Leerzeichen auf normale gefaltet. Der Entwurf selbst behält seine Zeichen.

Grund: die echte Herald-Zeile „That’s not conviction—that’s coordination"
rutschte am 21.09. an Regel (a) vorbei, weil das Muster auf `that's` stand.

---

## Gate 1 — Satzebene

**Die Regeln (a) bis (f) prüfen nur Text, der nicht zitiert ist** (ab
07.10.2026). Als Zitat gilt eine Blockquote-Zeile: im Markdown eine Zeile, die
mit `>` beginnt, im HTML der Inhalt von `<blockquote>`. Ein wörtliches Zitat
gibt fremden Satzbau wieder; wer es umbaut, damit es die Regel besteht,
verfälscht das Zitat. Was ein Zitat darf, begrenzt die Regel (q).

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
  - thank you for the comment
  - thanks for the comment
  - thank you for raising
  - thanks for raising
  - thank you for the thoughtful
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

### (c2) Vergleichende Verneinung (ab 08.10.2026)

Erweiterung von (c). Eine Eigenschaft von A wird behauptet, indem sie B
abgesprochen wird: „raises a question Y does not", „unlike A", „what the other
cannot". B steht nur im Satz, um etwas nicht zu haben. Die Eigenschaft wird
direkt gesetzt, B bleibt unerwähnt. Grund (08.10.2026): der erste scharfe
Syndikations-Thread trug diese Form, und keine Regel fing sie.

Prüffrage: **Nennt der Satz ein zweites Element nur, um ihm etwas
abzusprechen?** Ja heißt umschreiben.

```yaml
id: g1c2
label: Vergleichende Verneinung („unlike A", „what the other cannot", „… Y does not.")
gate: 1
scope: sentence
positions: [opener, middle, coda]
quote_exempt: true
patterns:
  - '\bunlike\s+(?:the\s+|a\s+|an\s+)?\w+'
  - '\b(?:in\s+contrast\s+to|as\s+opposed\s+to)\b'
  - "\\b(?:what|which|that)\\s+(?:the\\s+)?(?:other|others|rest)\\s+(?:cannot|can't|can\\s+not|does\\s+not|doesn't|do\\s+not|don't|is\\s+not|isn't|are\\s+not|aren't|will\\s+not|won't)\\b"
  - "\\b(?:a|an|the)\\s+(?:\\w+\\s+){0,3}(?:question|point|property|claim|thing|answer)\\s+(?:that\\s+|which\\s+)?[\\w-]+(?:\\s+[\\w-]+){0,3}\\s+(?:does|do|did|can|could|will|would|is|are|has|have)\\s*(?:not|n't)\\s*[.!?]?\\s*$"
```

### (g) Platzhalternomen mit Auflösung im selben Satz (ab 08.10.2026)

Ein abstraktes Nomen (question, point, issue, thing, aspect) steht für einen
Inhalt, den derselbe Satz nach einem Doppelpunkt, Gedankenstrich, „namely"
oder „that is" nachliefert: „raises a second question …: whether the agent
…". Der Inhalt wird ein eigener Satz, das Platzhalternomen fällt weg.

Prüffrage: **Enthält der Satz ein Platzhalternomen, das später im selben Satz
aufgelöst wird?** Ja heißt umschreiben.

```yaml
id: g1g
label: 'Platzhalternomen mit Auflösung („a question …: whether …“)'
gate: 1
scope: sentence
positions: [opener, middle, coda]
quote_exempt: true
patterns:
  # Only as a noun after a determiner or number: "a question", "one thing",
  # "the same thing", "three questions". The verb ("it issues") and a bare
  # line label ("Questions or feedback:") do not count.
  - "\\b(?:a|an|the|one|this|that|two|three|four|five|several|same|second|real|other|only)\\s+(?:\\w+\\s+){0,2}(?:question|point|issue|thing|aspect)s?\\b[^.!?]{0,90}?(?::\\s|\\s[—–]\\s|,?\\s+namely\\b|,?\\s+that\\s+is\\b)"
```

### (h1) Zwei gegensätzliche Bewertungen desselben Gegenstands (ab 08.10.2026)

Ergänzt (a) um die Wertungsform: derselbe Gegenstand bekommt im selben Satz
zwei gegenläufige Urteile, verbunden durch „but" oder „yet" („real but early",
„is true, but useless"). Das ist die „X but Y"-Hedge-Kadenz aus anti-KI-Sprech
§3, hier als Satzregel.

Prüffrage: **Stellt der Satz zwei gegensätzliche Bewertungen desselben
Gegenstands gegenüber?** Ja heißt umschreiben.

```yaml
id: g1h
label: Zwei gegenläufige Urteile über denselben Gegenstand („real but early")
gate: 1
scope: sentence
positions: [opener, middle, coda]
quote_exempt: true
patterns:
  - '\b(?:is|are|was|were|looks|sounds|seems|feels|remains)\s+(?:\w+ly\s+)?(?:real|true|right|good|useful|promising|solid|strong|valid|correct|important|necessary|interesting|impressive|clever|elegant|simple|fast|cheap|safe|new)\s*,?\s+(?:but|yet)\s+(?:still\s+|also\s+)?(?:early|unproven|useless|wrong|incomplete|insufficient|limited|fragile|slow|costly|expensive|risky|premature|thin|weak|narrow|unclear)\b'
  - '\b(?:real|true|promising|useful|solid|right|valid)\s+but\s+(?:early|unproven|incomplete|insufficient|limited|thin|narrow|premature)\b'
```

Die drei Prüffragen sind mechanisch: jede ist ein Satzmuster, keine
Ermessensfrage. Einmal Ja heißt umschreiben.

### (q) Zitatbudget (ab 07.10.2026)

Ein Zitat ist von (a) bis (f) ausgenommen. Damit die Ausnahme kein Versteck
wird, gilt für Zitate eine harte Obergrenze. Verstoß heißt BLOCKED, ohne
Ausnahme. Grund (07.10.2026): ohne Grenze ließe sich jeder gesperrte Satzbau als
Blockquote durch das Gate tragen.

- höchstens **zwei** Blockquote-Blöcke je Text;
- jeder Block hat **in den zwei Zeilen danach** eine Quellenangabe: ein
  Dokumentname plus ein Datum (`2026-04-18`, `18 April 2026`, `18.04.2026`)
  oder eine ID (`ID# 0031176`, `§4.1.24.10`, `/22/`). HTML-Kommentare zählen
  nicht, weil sie nicht ausgeliefert werden;
- zitierte Wörter machen höchstens **5 %** aller Wörter aus.

Ein Block ist eine zusammenhängende Folge von `>`-Zeilen. Leerzeilen trennen
Blöcke.

```yaml
id: g1q
label: Zitatbudget (≤2 Blockquotes, Quelle in 2 Zeilen, ≤5 % Wörter)
gate: 1
scope: text
rule: quote_budget
max_blocks: 2
source_window_lines: 2
max_quoted_share: 0.05
source_patterns:
  - '\b(?:19|20)\d{2}-\d{2}-\d{2}\b'
  - '\b\d{1,2}\.\d{1,2}\.(?:19|20)\d{2}\b'
  - '\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+(?:19|20)\d{2}\b'
  - '\bID#\s*\d+'
  - '§\s*\d+(?:\.\d+)*'
  - '/\d+/'
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

Ob ein Satz ein finites Verb trägt, entscheidet seit dem 01.10.2026 die Form,
nicht mehr eine Wortliste allein. Die Liste blieb drei Mal hinter der Sprache
zurück: `made` am 21.09., ein Imperativ am 23.09., `ends` und `answers` am
01.10. Jedes Mal blockierte die Regel einen korrekten Satz, und jedes Mal war
die Antwort, die Liste zu verlängern.

**Ein Verb-Kandidat ist**, in dieser Reihenfolge:

1. das **erste** Wort, wenn es in `imperative_verbs` steht — „Sign the voucher
   per call." trägt ein finites Verb, es steht nur vorn und in der Grundform;
2. ein Wort auf `-ed`, `-en`, `-s` oder `-es`, das **weder das erste noch das
   letzte** Wort ist und **kein Komma** direkt hinter sich hat;
3. ein Wort aus `verb_hints` — der Rückfall für das, was die Form nicht sieht.

Punkt 2 trägt die Arbeit. Die Stellung ist das Signal: in „The proof ends at
the rack you operate." steht `ends` mitten im Satz und etwas folgt darauf; in
„Just numbers." steht `numbers` am Ende und es folgt nichts. Deshalb bleibt die
Nominalphrase blockiert, obwohl sie auf `-s` endet.

Die Komma-Bedingung trennt die Aufzählung vom Satz: „Two registrations, no
calls." hat `registrations` in der Mitte, aber ein Komma dahinter — eine Liste,
kein Prädikat.

**`verb_hints` bleibt**, weil die Form unregelmäßige Verben nicht erkennt. „The
agent held none." hat kein Wort auf `-ed`, `-en` oder `-s`; ohne `held` in der
Liste fiele der Satz durch. Die Liste ist jetzt der Rückfall für Grenzfälle,
nicht die Regel.

```yaml
id: g1x_fragment_coda
label: Fragment-Coda (Nachschlag ohne Verb)
gate: 1
scope: sentence
positions: [coda]
rule: verbless_short_coda
max_chars: 50
lexicon: verb_hints
imperative_lexicon: imperative_verbs
# Form before list: an -ed/-en/-s/-es word in the middle of the sentence, with
# something after it and no comma directly behind, is a finite verb.
structural: true
verb_suffixes: [ed, en, es, s]
```

```yaml
lexicon: imperative_verbs
terms_en:
  - sign
  - check
  - verify
  - run
  - read
  - compare
  - recompute
  - replay
  - reproduce
  - try
  - open
  - post
  - file
  - report
  - measure
  - count
  - test
  - start
  - stop
  - see
  - look
  - watch
  - track
  - ask
  - call
  - send
  - fetch
  - pull
  - push
  - publish
  - ship
  - deploy
  - merge
  - review
  - audit
  - revoke
  - rotate
  - record
  - log
  - name
  - list
  - show
  - prove
  - cite
  - quote
  - add
  - drop
  - keep
  - use
  - take
  - write
  - note
  - pick
  - set
  - put
  - make
  - hold
  - wait
  - skip
terms_de:
  - signiere
  - prüfe
  - pruefe
  - prüft
  - vergleiche
  - vergleich
  - lies
  - starte
  - stoppe
  - melde
  - miss
  - zähle
  - zaehle
  - rechne
  - nenn
  - nenne
  - zeig
  - zeige
  - beleg
  - belege
  - nimm
  - schau
  - sieh
  - frag
  - frage
  - schick
  - hol
  - hole
  - veröffentliche
  - schreib
  - schreibe
  - halt
  - warte
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
  - ends
  - answers
  - carries
  - proves
  - reads
  - writes
  - means
  - leaves
  - stays
  - applies
  - covers
  - settles
  - records
  - verifies
  - signs
  - sets
  - lets
  - puts
  - gives
  - knows
  - sees
  - looks
  - turns
  - starts
  - stops
  - counts
  - names
  - points
  - rests
  - belongs
  - depends
  - matters
  - happens
  - arrives
  - returns
  - claims
  - states
  - asks
  - tells
  - adds
  - drops
  - breaks
  - stands
  # Irregular past forms. The heuristic also accepts a word ending in -ed or
  # -en, which covers the regular ones; these have neither ending and are
  # exactly what a short factual coda uses. "The other 14 registrations made
  # none." was blocked as verbless on 2026-09-21 because `made` was absent.
  - made
  - said
  - took
  - went
  - came
  - got
  - gave
  - saw
  - found
  - left
  - ran
  - became
  - brought
  - held
  - sent
  - built
  - lost
  - paid
  - kept
  - meant
  - drew
  - wrote
  - put
  - set
  - cut
  - let
  - hit
  - won
  - led
  - met
  - told
  - sold
  - stood
  - began
  - chose
  - fell
  - felt
  - knew
  - grew
  - spent
  - thought
  - caught
  - bought
  - fought
  - rose
  - broke
  - spoke
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
  - machte
  - ging
  - kam
  - gab
  - sah
  - fand
  - blieb
  - nahm
  - hielt
  - schrieb
  - trug
  - zog
  - wurde
  - war
```

### Pseudo-Cleft, rückwärtige Form

„recompute-determinism is what lets a relying party …". Gate 2 (b) fängt die
Vorwärtsform („What X does is Y"); diese macht das Prädikat zum Subjekt und
rutschte daran vorbei.

Die nackte Definitions-Kopula aus derselben Familie („The seam is revocation.")
steht nur in der Prosa: sie ist von einem normalen Aussagesatz mechanisch nicht
zu trennen.

```yaml
id: g1x_pseudo_cleft_reverse
label: Pseudo-Cleft rückwärts („X is what lets Y")
gate: 1
scope: sentence
positions: [opener, middle, coda]
patterns:
  - '\b[\w-]+\s+is\s+what\s+(?:lets|makes|allows|gives|tells|keeps|turns|drives|earns|matters|counts|distinguishes|separates)\b'
  - '\b[\w-]+\s+ist\s+das,\s+was\b'
```

### Em-Dash-Einschub

Zwei Fälle, beide auf Satzebene.

Mehr als ein Einschub im Satz: ein Einschub klammert mit zwei Strichen, also
blockt der dritte.

Ein Einschub, der selbst aus mehreren Gliedern besteht — „the same verdict —
the same outcome, under the same reason code — without a network call" hängt
drei Umschreibungen derselben Sache aneinander, und die Klammer enthält dafür
ein Komma. Genau das ist das Muster aus den AUDIT-Threads, und es hat **zwei**
Striche, läuft der Zählregel also davon.

Ein Einschub mit Komma kann sachlich gemeint sein. Der Scan blockt nach
Telegram statt still umzuschreiben, ein Fehlalarm kostet also eine Durchsicht
und keinen Beitrag.

```yaml
id: g1x_em_dash_density
label: Mehr als ein Em-Dash-Einschub im Satz
gate: 1
scope: sentence
positions: [opener, middle, coda]
rule: count_threshold
threshold: 3
patterns:
  - '[—–]'
```

```yaml
id: g1x_em_dash_appositive
label: Em-Dash-Einschub mit mehrgliedrigem Inhalt
gate: 1
scope: sentence
positions: [opener, middle, coda]
patterns:
  - '[—–]\s*[^—–.!?]{3,90},\s*[^—–.!?]{3,90}[—–]'
```

### Symmetrische Parallel-Definition

„Attestation tells you X. Recomputation tells you Y." Zwei gleich gebaute Sätze
hintereinander, beide wahr, zusammen ein Muster.

```yaml
id: g1x_parallel_definition
label: Symmetrische Parallel-Definition (zwei gleich gebaute Sätze)
gate: 1
scope: part
patterns:
  - '\b\w+\s+tells\s+you\s+[^.!?]{1,70}[.!?]\s+[\w-]+\s+tells\s+you\b'
  - '\b\w+\s+gives\s+you\s+[^.!?]{1,70}[.!?]\s+[\w-]+\s+gives\s+you\b'
  - '\b\w+\s+shows\s+you\s+[^.!?]{1,70}[.!?]\s+[\w-]+\s+shows\s+you\b'
  - '\b\w+\s+answers\s+[^.!?]{1,70}[.!?]\s+[\w-]+\s+answers\b'
  - '\b\w+\s+sagt\s+dir\s+[^.!?]{1,70}[.!?]\s+[\w-]+\s+sagt\s+dir\b'
```

### Übertreibungs-Coda

„it is not evidence of anything". Die Maximalform ist fast immer unwahr; die
präzise Folge gehört dorthin („settles nothing about which verdict was
correct"). Eine Verneinung, die ihren Gegenstand benennt („proves nothing about
X"), läuft durch.

```yaml
id: g1x_overstatement_coda
label: Übertreibungs-Coda („not evidence of anything")
gate: 1
scope: sentence
positions: [coda]
patterns:
  - '\bnot\s+evidence\s+of\s+anything\b'
  - '\b(?:proves|means|shows|says|settles)\s+nothing\s*(?:at\s+all\s*)?[.!?]?$'
  - '\bdoes\s+not\s+mean\s+anything\b'
  - '\bbeweist\s+(?:gar\s+)?nichts\s*[.!?]?$'
  - '\bsagt\s+(?:gar\s+)?nichts\s+aus\s*[.!?]?$'
```

### Scaffold-Opener

Ein Satz, der ankündigt, was gleich kommt, statt es zu sagen. Getrennt von (b)
gehalten: (b) fängt Zustimmung, das hier fängt Ankündigung.

```yaml
id: g1x_scaffold_opener
label: Scaffold-Opener (Ankündigung statt Aussage)
gate: 1
scope: sentence
positions: [opener, middle, coda]
anchor: sentence_start
lexicon: scaffold_openers
```

```yaml
lexicon: scaffold_openers
terms_en:
  - short answer first
  - short answer
  - the short version
  - the thing i'd watch
  - the thing to watch
  - one distinction to keep
  - one distinction worth keeping
  - one thing to note
  - a few thoughts
  - here's the thing
  - here is the thing
  - let me start with
  - first, some context
  - to set the scene
  - the key insight here
  - the important part
terms_de:
  - kurz vorweg
  - eins vorweg
  - zunächst einmal
  - vorab
  - die kurze antwort
  - worauf ich achten würde
  - eine unterscheidung
  - der entscheidende punkt
```

### Thesis-Rückruf-Coda

Der Schlusssatz sagt die Eröffnung noch einmal mit anderen Wörtern. Mechanisch:
die Coda teilt genug Inhaltswörter mit dem Opener und bringt selbst nichts
Neues — keine Zahl, keinen Namen, keinen Link, keine Aufforderung. Eine Coda,
die den nächsten Schritt nennt, läuft durch, weil genau das die Abhilfe ist.

```yaml
id: g1x_thesis_recall_coda
label: Thesis-Rückruf-Coda (Schluss wiederholt die Eröffnung)
gate: 1
scope: part
rule: thesis_recall_coda
min_shared_words: 3
min_word_len: 5
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

**Modusregel, nicht global** (ab 07.10.2026). g2e läuft in den Modi `thread`,
`post` und `reply`, im Modus `article` nicht. Grund (07.10.2026): die Regel
beschreibt X, und ein Artikel ohne Links stand im Report jedes Mal mit einem
Befund da, der keiner war. Das Feld `modes` unten und die Liste
`mode_rules` oben nennen dieselben Modi.

```yaml
id: g2e
label: Link-Disziplin
gate: 2
scope: thread
rule: link_discipline
expected_links: 1
modes: [thread, post, reply]
```

#### Ausnahme: taskmarket-Aufgabentexte (Entscheid Lars, 05.10.2026)

**Ein Aufgabentext auf taskmarket darf mehr als einen Link tragen, und g2e
bleibt dort offen.** Das ist beabsichtigt und keine offene Frage mehr.

Die Regel kommt von X: dort drosselt die Plattform einen Post mit Auslink, und
die URL kostet 42 der 280 Zeichen im Hook. Beides gilt auf taskmarket nicht.
Ein Aufgabentext hat kein Zeichenlimit von 280, keine Reichweitendrosselung,
und seine Links sind **Anleitung, nicht Werbung** — die drei Endpunkte in den
Stufe-1-Texten (`/identity/register-challenge`, `/auth/signup-did`,
`developers.html`) sind die Arbeitsanweisung selbst. Wer sie streicht, um eine
X-Regel zu erfüllen, macht die Aufgabe unlösbar.

Gemessen am 03. und 05.10.2026: die Texte der Runden 3 und 4 bestehen **Gate 1
mit 18 von 18** und Gate 2 mit 7 von 8. Der eine offene Punkt ist jedes Mal
g2e mit „3 links, expected exactly 1".

**Was weiter gilt:** `voice_gate.scan` kennt keinen Modus für einen
Aufgabentext (`thread`, `post`, `reply`), also wird mit `mode="post"` und
erhöhtem `max_chars` gescannt, und g2e wird im Protokoll als **nicht anwendbar**
notiert, nicht als bestanden. Jeder andere Befund bleibt ein Befund — die
Ausnahme deckt genau diese eine Regel und genau diese eine Textsorte.

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
# Ein Artikelabsatz ist kein Tweet. Im Modus article gilt die Grenze je Absatz.
max_chars_by_mode:
  article: 1500
```

### (g) Zahlenprüfung gegen die Quelle

Jede Zahl ab drei Stellen im Entwurf muss von einer Zahl im Quelltext getragen
sein. Drei Stellen ist die Untergrenze, weil zweistellige Angaben mit allem
kollidieren.

**Ohne Quelltext** (ab 07.10.2026): In den Modi `thread`, `post` und `reply`
gilt bis zur Frist eine Übergangsregel. Enthält der Entwurf keine Ziffer, wird
g2g nicht geladen, und der Report nennt den Grund. Enthält er eine Ziffer, ist
g2g geladen und schlägt fehl, weil eine Zahl ohne Quelle nicht geprüft werden
kann. Im Modus `reply` gelten die abgerufenen Quellen der Regel (h) als
Quelltext. Der Modus `article` läuft ohne Quelltext nicht. Grund (07.10.2026):
„entfällt" stand bisher als Ergebnis im Report und zählte als bestanden, obwohl
nichts geprüft war.

**Frist: Herald übergibt seinen Quelltext bis zum 14.10.2026.** Ab dem
15.10.2026 ist ein fehlender Quelltext in allen Modi ein harter Fehlschlag, ob
der Entwurf eine Ziffer trägt oder nicht. Das Datum steht unten als
`source_required_from` und wird vom Gate gelesen.

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
source_required_from: "2026-10-15"
```

### (h) Quellenregel für Replies

Gilt nur im Modus `reply`. Eine Reply hat kein Quelldokument, aus dem sie
entsteht — sie antwortet auf einen fremden Post. Damit greift (g) ins Leere:
eine erfundene Zahl sieht in einer Reply genauso aus wie eine erinnerte.

Deshalb muss der Entwurf seine Quellen selbst mitbringen. Jede Behauptung, die
prüfbar aussieht — eine Zahl ab drei Stellen, eine skalierte Angabe, ein
Aktenzeichen, eine Norm-, RFC-, CVE- oder ERC-Nummer, ein Fallname — muss im
Text mindestens eines Dokuments vorkommen, das im selben Lauf über eine vom
Entwurf genannte URL geholt wurde.

Kein Beleg, kein Post. Der frühere Hinweisblock „Vor Freigabe prüfen" entfällt:
ein Hinweis, den ein Mensch prüfen soll, ist keine Prüfung, und sobald
irgendwann automatisch gepostet wird, ist er gar nichts.

**Der Post, auf den geantwortet wird, zählt als Quelle** (ab 02.10.2026). Eine
Reply darf sich auf das stützen, was der Post selbst sagt — das ist kein
Gedächtnis, das steht auf dem Schirm.

Auslöser war ein Fehlalarm: die Regel blockierte eine Reply mit „114.09 ETH" als
unbelegt, während der Post darüber lautete

> 🚨SlowMist TI Alert🚨
> 💸 @aave v3 Loop Safe Module Loss: ~114.09 ETH

Die Zahl war sichtbar, nur nicht in dem Korpus, den (h) durchsuchte — der hielt
unsere eigenen Seiten und das, was der Entwurf genannt hatte, nie aber das
Beantwortete.

**Nur dessen Text, nicht dessen Links.** Verlinkte Seiten werden getrennt geholt
und nur, wenn der Entwurf sie nennt. Ein Post, der irgendwohin verlinkt, macht
nicht die ganze Zielseite zitierfähig — sonst wäre jede Behauptung belegbar, die
irgendwo hinter einem Link im fremden Post steht.

Auslöser der Regel selbst: der erste Trockenlauf des Reply-Radars am 21.09.
erzeugte den Satz „Moffatt v. Air Canada, 2024 BCCRT 149: CAD 812.02 awarded" —
vollständig plausibel, von nichts in der Kette geprüft.

```yaml
id: g2h
label: Quellenregel (jede Behauptung belegt)
gate: 2
scope: thread
modes: [reply]
rule: sources_grounded
min_digits: 3
tolerance: 0.02
# The reply radar puts the post being answered into `sources` under its own
# URL. Its links are not included; those are fetched only when the draft
# names them.
target_post_is_source: true
claim_patterns:
  - '(?:RFC|CVE|ERC|EIP|BIP|CWE|ISO|NIST|SLSA|GDPR|BCCRT|SOC)[-\s]?v?\d+[\w./-]*'
  - '\b\d{4}\s+[A-Z]{2,6}\s+\d+\b'
  - '\b[A-Z][a-z]+\s+v\.?\s+[A-Z][\w.]+'
  - '\b(?:USD|EUR|CAD|GBP|CHF)\s?[\d,.]+\b'
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

- 2026-09-22: Sieben Muster aus den AUDIT-/IRIS-Threads. Gate 1 bekommt
  Pseudo-Cleft in der rückwärtigen Form, Em-Dash-Dichte (ab dem dritten Strich),
  symmetrische Parallel-Definition, Übertreibungs-Coda, Scaffold-Opener und
  Thesis-Rückruf-Coda; die Lexikonliste zu (b) wächst um die dankenden
  Aufmacher. Die Metapher-Verben (lands on, sits at, draws the line) stehen in
  `anti-KI-Sprech.md` §2 und wirken über Gate 2 (a) ohne eigene Regel. Die
  nackte Definitions-Kopula („The seam is revocation.") bleibt Prosa: sie ist
  von einem gewöhnlichen Aussagesatz mechanisch nicht zu trennen.

- 2026-10-07: Modus `article` und Positivliste der Regeln je Modus
  (`mode_rules`). Ein Ergebnis „nicht anwendbar" gibt es nicht mehr: eine Regel
  ist im Modus geladen oder nicht. Gate 1 (a)–(f) prüfen nur nicht-zitierten
  Text, die neue Regel (q) begrenzt Zitate auf zwei Blöcke, je eine
  Quellenangabe in den zwei Zeilen danach und 5 % der Wörter. g2e ist eine
  Modusregel (thread, post, reply). „Kein Produktname" gilt für die eigenen
  Produkte, Namen zitierter Dritter sind zulässig. Auslöser: der Blogpost
  „Who proves what the agent was allowed to buy" mit zwei wörtlichen Zitaten
  und einer Belegtabelle ohne Links, den das Gate im Modus `post` mit zwei
  Befunden blockte, die keine waren.

- 2026-10-07 (2): g2g ohne Quelltext. Bis zum 14.10.2026: ein Entwurf ohne
  Ziffer lädt g2g nicht, ein Entwurf mit Ziffer schlägt fehl; im Modus `reply`
  zählen die Quellen der Regel (h) als Quelltext. Ab dem 15.10.2026
  (`source_required_from`) ist fehlender Quelltext in allen Modi ein harter
  Fehlschlag. Auslöser: der Herald-Digest lief mit „skipped (no source)", und
  der Report zählte das als bestanden.

- 2026-10-08: Drei Satzregeln aus dem ersten scharfen Syndikations-Thread:
  (c2) vergleichende Verneinung, (g) Platzhalternomen mit Auflösung im selben
  Satz, (h1) zwei gegenläufige Urteile über denselben Gegenstand. In allen
  Modi geladen, wie (a)–(f) von Zitaten ausgenommen (`quote_exempt`).
