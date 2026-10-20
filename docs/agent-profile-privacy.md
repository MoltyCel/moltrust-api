# agent_profile — was gespeichert wird und warum

Stand 2026-09-20. Gilt für die Tabelle `agent_profile` und den Tageslauf
`scripts/enrich_agent_profiles.py`.

## Keine IP-Spalte

Die Tabelle enthält keine IP-Adresse, weder im Klartext noch als Hash.

Die Anforderung lautete „IP nur gehasht". Das wäre hier eine Verschlechterung
gewesen, aus zwei Gründen.

Erstens gibt es keine vollständige IP, die zu schützen wäre. Beide Quellen
kürzen bereits vor dem Schreiben: `_anonymize_ip()` nullt das letzte Oktett
(IPv4) beziehungsweise die letzten 64 Bit (IPv6), und `request_log.ip` wird
genauso geschrieben. Live nachgesehen sind die häufigsten Werte dort
`130.49.215.0`, `46.225.175.0`, `187.188.11.0` — ein /24, kein Endgerät.

Zweitens ist ein Hash über eine IPv4 keine Anonymisierung. Der Adressraum hat
2³² Elemente und lässt sich auf handelsüblicher Hardware in Minuten
durchrechnen; wer den Hash hat, hat die Adresse. Ein Hash würde also nur so
aussehen, als wäre etwas geschützt, und die gekürzte Form durch eine
scheinbar sicherere ersetzen, die es nicht ist.

Für die Auswertung wird die IP ohnehin nicht gebraucht. Der Schlüssel ist die
DID; die Registrierungs-IP dient einmalig als Ersatzschlüssel beim Rückblick auf
`request_log` und wird dabei nicht in `agent_profile` übernommen.

## ASN und Land im Klartext

`asn`, `country` und `cloud_provider` stehen unverschlüsselt in der Tabelle. Sie
tragen die gesamte Auswertung — Herkunftscluster, Sybil-Verdacht,
Anbieterverteilung — und keines der drei Felder identifiziert eine Person. Ein
Autonomous-System umfasst typischerweise Tausende bis Millionen Adressen.

## Deklariert und beobachtet sind getrennt

Die Spalten `declared_capabilities`, `declared_description`, `agent_card_url` und
`declared_framework` enthalten, was ein Agent über sich selbst behauptet. Sie
sind ungeprüft und werden nie so verwendet, als wären sie gemessen. Alles andere
stammt aus `request_log` oder aus der Kette.

Die Trennung ist der Zweck der Tabelle: die interessante Frage ist, wo Anspruch
und Beobachtung auseinanderfallen. Eine Spalte, die beides mischt, macht aus
einer Behauptung eine Messung.

## Herkunft der Beobachtung wird mitgeschrieben

`observed_from` hält fest, worüber ein Profil zustande kam:

| Wert | Bedeutung |
|---|---|
| `did` | Die Anfragen trugen die DID des Agenten. Eindeutig. |
| `registration_ip` | Die Anfragen kamen aus dem /24, aus dem der Agent registriert wurde. Schwächer — hinter einem NAT oder einem Cloud-Ausgangsbereich liegen mehrere Agenten. Sagt „gleiche Herkunft", nie „derselbe Agent". |
| `none` | Kein Datensatz vorhanden. |

`observed_rows` nennt die Zahl der zugrunde liegenden Zeilen. Ein Profil aus drei
Zeilen und eines aus dreitausend sehen sonst gleich aus.

## Aufbewahrung

`request_log` hält 30 Tage (siehe `scripts/retention_cleanup.py`). Damit ist ein
Profil ein gleitendes Fenster und keine Historie: ein Agent, der im Juli aktiv
war und seither schweigt, erscheint hier als still. Das ist richtig so und muss
beim Lesen der Zeitreihe mitgedacht werden.

Wird ein Agent widerrufen, bleibt die Profilzeile bestehen; die Auswertungen im
Panel filtern über `agents.revoked_at IS NULL`. Wer die Zeile mitlöschen will,
braucht dafür einen eigenen Vorgang — das ist bewusst nicht automatisch, weil
ein Widerruf wegen Missbrauchs genau der Fall ist, in dem die Herkunft noch
gebraucht wird.

## Ausgehende Anfragen

Der Tageslauf macht im Normalbetrieb keine ausgehenden Anfragen. Nur
`--skills` holt die ERC-8004-`tokenURI` und die Agent-Card von der URL, die der
Agent selbst angegeben hat. Weil das bedeutet, dass der Server auf Zuruf fremde
Adressen abruft, ist die Option abschaltbar und standardmäßig aus; `agent_card_url`
wird zusätzlich beim Eintragen auf `https://` eingeschränkt.
