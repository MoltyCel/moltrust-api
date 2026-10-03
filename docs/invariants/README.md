# Invarianten

Aussagen, die immer wahr sein müssen, je als Abfrage mit erwartetem Ergebnis.

Wir haben bisher Lebenszeichen geprüft, nicht Plausibilität. Der
Track-Record-Defekt lief fünf Tage, weil der Watchdog fragt *antwortet der
Dienst* und niemand fragt *stimmt die Verteilung*. Drei Agents holten 75
Credentials, jedes verankert und bezahlt, und alles war grün.

## Format

Eine YAML-Datei je Invariante in diesem Verzeichnis.

```yaml
id: a-track-record-burst          # eindeutig, Kategorie als Präfix
titel: Kein DID mit mehr als zwei Track Records in sieben Tagen
kategorie: A                       # A..G, siehe unten
tempo: hourly                      # hourly | daily
abfrage:
  art: sql                         # sql | shell | http
  wert: |
    SELECT count(*) FROM ...
erwartung:
  operator: eq                     # eq | lte | gte | empty | contains
  wert: 0
schweregrad: fail                  # warn | fail
autofix: none                      # none | <Name eines GRÜN-Fixes>
deckel: 3                          # Autofix-Läufe je 24 h
herkunft: >
  Welcher Vorfall diese Prüfung erzeugt hat, mit Datum. Steht hier, damit in
  sechs Monaten niemand eine Prüfung entfernt, deren Grund er nicht kennt.
```

`erwartung.operator`:

| | |
|---|---|
| `eq` | das Ergebnis ist genau dieser Wert |
| `lte` / `gte` | höchstens / mindestens |
| `empty` | die Abfrage liefert keine Zeile |
| `contains` | die Ausgabe enthält diesen Text |

Eine Abfrage liefert **einen** Wert: die erste Spalte der ersten Zeile, oder
bei `empty` die Zeilenzahl. Was mehr braucht, ist zwei Invarianten.

## Kategorien

| | |
|---|---|
| **A** | Verteilung — Zahlen, die plausibel sein müssen, nicht nur vorhanden |
| **B** | Quellenabgleich — zwei Quellen, die dasselbe sagen müssen |
| **C** | Deklaration gegen Wirkung — was konfiguriert ist, läuft auch |
| **D** | Vollständigkeit — eine Leseoperation weist nach, dass sie alles sah |
| **E** | Öffentliche Artefakte — was draußen steht, stimmt und hat einen Schreiber |
| **F** | Geld — Töpfe, Buchungen, Escrows |
| **G** | Agenten — unsere eigenen, ihr Verhalten gegen ihren Sollwert |

## Autofix, drei Klassen

**GRÜN** läuft selbständig, wird protokolliert und im Wochenreport aufgeführt.
Nur idempotente Wiederherstellung: Export neu fahren, Dienst neu starten,
ausgefallenen Cron nachholen, Cache invalidieren, Watchdog-Basislinie
nachziehen, fehlende Verankerung nachfahren, eine untracked Dateikopie
entfernen **nachdem** sie als byte-identisch bewiesen ist.

**GELB** legt einen PR an und mergt ihn nicht: jede Codeänderung,
Konfiguration, Ruleset, Crontab.

**ROT** wird nur gemeldet: alles mit Geld, jede externe Kommunikation, jede
Löschung oder Widerrufung, alles was eine fremde Partei betrifft.

Jeder GRÜN-Autofix trägt einen Deckel, Vorgabe drei Läufe je 24 h je
Invariante. Beim Überschreiten setzt der Autofix aus und meldet FAIL — eine
Reparatur, die sich dreimal am Tag wiederholt, repariert nichts.

## Die Meta-Invariante

Der Runner protokolliert je Lauf, **welche** Invarianten tatsächlich
ausgeführt wurden, und schlägt Alarm, wenn eine zwei Läufe in Folge
übersprungen wurde oder der Lauf vor dem Ende abbricht.

Ein Selbsttest, der still nichts prüft, ist genau der Fehler, den wir in den
Harness-Tests schon dreimal hatten. Die Meta-Invariante ist deshalb nicht
abschaltbar und steht nicht als Datei hier, sondern im Runner.

## Lernregel

Jeder gefundene Defekt bekommt eine Invariante, die ihn künftig fängt, und
zwar **bevor** der Fix gemergt wird. Kein Fix ohne Wächter.
