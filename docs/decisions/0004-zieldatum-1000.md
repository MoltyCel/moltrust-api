# 0004 — Zieldatum 1.000: intern der 31.10., öffentlich kein Datum

**Datum:** 2026-09-26
**Status:** Accepted
**Entscheider:** Lars

## Entscheidung

**Intern gilt der 31.10.2026. Nach außen wird kein Datum genannt, bis Runde 3
gemessen ist.** Der Meilenstein-Trigger läuft ab sofort täglich um 06:00 und
veröffentlicht nichts.

## Grundlage

Stand 26.09.2026, 17:30 UTC, gelesen über `app/sql/public_count.sql`:

| Größe | Wert |
|---|---:|
| öffentliche Zahl (extern, verankert, ohne interne und Partner-Testagents) | **309** |
| davon aktiviert nach der strengen Regel | 31 |
| unmessbar (vor dem 30-Tage-Fenster von `request_log` registriert) | 56 |
| abgezogen (eigen 21, Partner-Test 9) | 30 |
| alle nicht widerrufenen Zeilen | 346 |

Es fehlen 691. Der 10.10. verlangt 49 Köpfe am Tag; der höchste je gemessene
Tageswert ist 83, und das war der Eröffnungstag der ersten Runde. Der 31.10.
verlangt 19,7 am Tag, was dem laufenden Takt entspricht, solange durchgehend
eine Task offen ist. Am 22.09., dem einzigen Tag zwischen zwei Runden, kam
eine einzige Registrierung.

Warum nach außen trotzdem kein Datum: die Ausbeute je Runde ist von 125 auf 109
gefallen, und zwei Messpunkte tragen keine Zusage. Bei 5 % Abschlag je Runde
reichen 8 Runden und 43 USDC; bei 13 % sind es 20 Runden und 108 USDC, also
mehr als das Budget; bei 25 % endet die Reihe bei +327 und der Kanal trägt das
Ziel nie. Welcher Fall zutrifft, sagt Runde 3.

## Trigger

`scripts/milestone_trigger.py`, täglich 06:00 im Cron. Der Auslöser hängt an
der Projektion `(1000 − Stand) / Rate7`, nicht am Zählstand — eine einbrechende
Rate schiebt ihn von selbst nach hinten.

| Stufe | Auslöser | Kanal | was passiert |
|---|---|---|---|
| T-1 | Projektion ≤ 7 Tage | STATS | Entwürfe schreiben, Voice-Gate |
| T-2 | Projektion ≤ 3 Tage | ALERTS | Texte final, Termin gesetzt |
| T-3 | Stand ≥ 1.000 an zwei Tagen in Folge | ALERTS | Freigabe möglich |

Die zwei Tage bei T-3 fangen einen Rückfall durch Widerrufe ab. Vor der
Freigabe müssen vier Punkte stehen; die ersten beiden prüft das Skript selbst,
die anderen beiden stehen als Häkchen in der Meldung:

1. Zählregel veröffentlicht.
2. `registry-proof.html` antwortet.
3. Die Trennung nach Bounty, Partner und organisch steht im Text der ersten
   Nachricht.
4. Die strenge Zahl steht in derselben Nachricht, nicht erst auf Nachfrage.

Das Skript sendet Meldungen. Es postet nichts und bekommt dafür auch keine
Zugangsdaten.

## Was dabei aufgefallen ist

**`SCRIPTED_ENDPOINTS` war seit Runde 1 nicht nachgezogen.** Runde 2 verlangt
das Binden einer Wallet, `/identity/bind` stand aber nicht in der Liste. Damit
galten 122 Agents als eigenständig, die nichts anderes getan haben als den
Schritt aus dem Aufgabentext. Die Liste trägt jetzt die vier Pfade der zweiten
Runde, und `public_count.sql` führt dieselben sieben.

**Ein `\set` mit dreifachen Anführungszeichen hat zwei der sieben Pfade
stillschweigend unbrauchbar gemacht.** `'''a,b,c'''` liefert nach der
`:'name'`-Ersetzung ein Array, dessen erstes und letztes Element ein
Anführungszeichen trägt; `/identity/verify/` und `/credentials/track-record`
haben dann nie gematcht, und die Spalte zeigte 37 statt 21 Bounty-Agents. Die
Abfrage prüft ihre eigene Parameterliste jetzt mit (`scripted_intact`,
`scripted_unquoted`) und verweigert die Zahl, wenn eine der Proben fällt.

## Was das kostet

50 USDC gesamt, davon 5,41 bereits als Escrow für TSK-J3R0MDGA gebunden. Die
750-bps-Gebühr steckt in den 5,41 und kommt nicht obendrauf: 100 × 0,05 netto
ergibt 5,00, geteilt durch 0,925 sind 5,405. Verbleiben 44,59.

Kopfpreis Runde 1: 13,40 USDC für 125 neue DIDs, also 0,107. Runde 2, Task 1:
5,41 für 109, also 0,050.

## Wann das neu zu bewerten wäre

Nach Runde 3. Liegt der Abschlag bei 5 %, kann der 31.10. auch öffentlich
genannt werden. Liegt er über 13 %, trägt taskmarket allein das Ziel nicht und
die Frage ist ein zweiter Kanal, kein weiteres Budget auf demselben.

Volle Erhebung: `~/Downloads/basisrechnung-ziel-1000.md` (2026-09-26).
