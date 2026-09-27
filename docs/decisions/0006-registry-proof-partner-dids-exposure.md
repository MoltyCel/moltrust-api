# 0006 — registry-proof.json listete 50 Partner-DIDs; ein Fremdabruf

**Datum:** 2026-09-27
**Status:** Accepted, geschlossen
**Entscheider:** Lars

## Was passiert ist

`registry-proof.json` lag vom **27.09. 09:52 bis 17:13 UTC** im Web-Root und
führte **50 Partner-DIDs einzeln auf**, statt sie zu einer Aggregatzeile
zusammenzufassen. Die Vorgabe dazu war am 26.09. um 19:14 erteilt worden.

**Ein Fremdabruf innerhalb des Fensters:**

```
104.28.203.246  27/Sep/2026:13:16:11 +0000  GET /registry-proof.html  200   43 570 B  "curl/8.5.0"
104.28.235.246  27/Sep/2026:13:16:21 +0000  GET /registry-proof.json  200  234 029 B  "curl/8.5.0"
```

Zehn Sekunden auseinander, kein Referrer, je genau ein Treffer dieser IPs im
gesamten Log. `104.28.0.0/16` gehört Cloudflare. Die 234 029 Byte sind die
exponierte Fassung; der Abruf hat sie vollständig erhalten. Die übrigen 16
Treffer auf `registry-proof.*` stammen von `9.246.15.224`, unserem eigenen
Anschluss.

## Ursache

**Zwei Erzeugungspfade für dasselbe öffentliche Artefakt.** Die Vorlage in
`~/Downloads/zaehlregel-v1` hatte Positivliste und Partner-Aggregation. Dieses
Repo hatte eine Ausschlussliste und listete Partner zeilenweise. Die
Nachweisseite wurde aus dem zweiten gebaut.

Der Fehler war nicht die Ausschlussliste, sondern dass eine Vorgabe mit Wirkung
auf ein öffentliches Artefakt nur in dem Pfad umgesetzt wurde, in dem sie
erteilt worden war.

**Auffindbarkeit.** Die Seite trug `noindex,nofollow`, stand in keiner Sitemap
und war von keiner Seite verlinkt. Das hieß nie „nicht auffindbar":
`moltrust-api` ist ein öffentliches Repository, und
`PROOF_URL = "https://moltrust.ch/registry-proof.html"` steht seit dem 26.09.
19:26 in `scripts/milestone_trigger.py` sowie in drei PR-Beschreibungen. Die URL
haben wir selbst veröffentlicht.

## Der Befund, der den Vorgang schließt

**Die Partner-Zuordnung war bereits öffentlich, unauthentifiziert, für alle
51 DIDs.** Gemessen am 27.09. gegen `api.moltrust.ch`, ohne Schlüssel:

| Pfad | gibt `platform` zurück |
|---|---:|
| `GET /identity/resolve/{did}` | **51 von 51** |
| `GET /a2a/agent-card/{did}` | **51 von 51** |
| `display_name` enthält den Partnernamen | 44 von 51 |

`/identity/resolve/` liefert die Zuordnung in `metadata.platform` im Klartext,
die Agent-Card als Feld `platform`. Weitere öffentliche Pfade —
`/agents/{did}/erc8004`, `/identity/badge/{did}`, `/identity/agent-type/{did}`,
`/identity/revocation-status/{did}` — geben sie über den Anzeigenamen mit
heraus. Ohne Zuordnung antwortet keiner.

Die exponierte Datei hat also **keine Zuordnung offengelegt, die nicht schon
vorher jeder abrufen konnte.**

## Was sie trotzdem hinzugefügt hat

Die Aufzählung. Die öffentlichen Endpunkte antworten je DID; wer sie nutzen
will, muss die DID vorher kennen. Die Datei nannte 50 Partner-DIDs in einem
Zug als Menge. Der einzige öffentliche Endpunkt, der überhaupt aufzählt, ist
`GET /agents/recent`, und der gibt zehn Einträge zurück.

Unterschied also: nicht *ob* eine DID einem Partner zuzuordnen ist, sondern wie
schnell jemand die Liste zusammen hat. Das ist eine reale, aber kleinere
Differenz als der erste Bericht nahelegte.

## Entscheidung

**Keine Meldung an Partner.** Nach dem Befund oben hat der Vorfall keine
Zuordnung offengelegt, die nicht über zwei öffentliche Endpunkte ohnehin
abrufbar ist. Ein Zweizeiler an aeoess war vorbereitet und wird nicht gesendet;
Ownify liefe über Harald, klaw analog.

## Behoben

`#500` (api) und `#253` (web), beide gemergt und deployt.

- Ein Erzeugungspfad: `app/sql/registry_export.sql` über
  `app/registry_export.py`. `app/sql/public_count.sql` gelöscht.
- Positivliste statt Ausschluss. Eine Plattform, die in keiner der vier
  Eimer-Listen steht, erzeugt keine Zeile, keine Zahl, keinen Platzhalter.
- Partner als eine Aggregatzeile ohne DID. Ihre Anker hängen daran, nach Leaf
  sortiert, ohne `credential_id` und `issued_at` — sonst ließe sich die Zeile
  gegen die Einzelzeilen ausrichten.
- Exporter und Prüfskript verweigern beide, wenn eine Aggregatzeile eine DID
  trägt.
- Ein Schnitt für Registrierungen, Anker und Aktivierungsfenster.

Probe gegen den ausgelieferten Stand: `aeoess` 0 Treffer in JSON, HTML, py und
SQL; 0 Einzelzeilen mit `bucket=partner`; Aggregat 42 DIDs und 42 Anker ohne
`did`-Feld; 372 von 372 Ankern replayen, 56 von 56 Wurzeln stehen auf Base.

Die Regel dagegen steht in `CLAUDE.md` unter *Vorgaben mit Wirkung auf
öffentliche Artefakte*.

## Offen, nicht Teil dieses Vorgangs

Dass `GET /identity/resolve/{did}` und die Agent-Card die Plattform
unauthentifiziert herausgeben, ist eine Produktentscheidung, keine Panne — die
Agent-Card soll auffindbar sein. Ob die Plattform eines Partner-Agents dort
stehen muss, ist eine eigene Frage. Sie wird hier festgehalten und nicht
beantwortet.
