# Vorschlag: DIDs ohne Aktivität nach 90 Tagen revoken

**Status:** Vorschlag, nicht umgesetzt. Braucht eine Entscheidung, weil die
Regel eine Zahl verändert, die nach außen zitiert wird.

## Das Problem

`registered` zählt jede nicht revokte DID. Die Zählung ist seit dem 18.09.
sauber definiert und trotzdem weich: ein Agent, der sich registriert und nie
wieder auftaucht, steht dauerhaft in derselben Zahl wie einer, der täglich ruft.

Mit bezahlter Akquise wird das spürbar. Am 20.09. haben zehn USDC in einer
Stunde dreizehn Registrierungen erzeugt; fünf davon hatten nach einer Stunde
einen authentifizierten Call. Bleiben die anderen acht dauerhaft in
`registered`, misst die Zahl irgendwann, wie oft wir Bounties ausgelobt haben.

## Was vorgeschlagen wird

Eine DID, die **90 Tage nach ihrer Registrierung** keinen einzigen
authentifizierten Aufruf hat, wird revoked mit
`revocation_reason = 'inactive_90d'`.

Bewusst eng:

- **Nur wer nie aktiv war.** Ein Agent mit einem Call am Tag 2 und Stille danach
  bleibt. Die Regel trennt „nie angekommen" von „nicht mehr aktiv", und nur das
  erste ist eine Zählfrage.
- **Revoked, nicht gelöscht.** `agents` hat keinen Löschpfad, und die DID bleibt
  auflösbar. Wer eine ausgestellte Credential hält, kann sie weiter prüfen.
- **Umkehrbar.** Ein Aufruf nach der Revocation reaktiviert; die Regel darf
  niemanden aussperren, der zurückkommt.

## Was das kostet

Heute beträfe es 0 Agenten — die älteste betroffene Kohorte wird erst ab dem
19.12.2026 90 Tage alt. Die Regel ist also jetzt zu entscheiden und erst später
wirksam, was der angenehmste Zeitpunkt für so etwas ist.

## Was dagegen spricht

`registered` fällt dadurch irgendwann. Eine Zahl, die man extern genannt hat,
nachträglich kleiner zu machen, muss man erklären können — deshalb steht hier
ausdrücklich, dass der Rückgang eine Definitionsänderung ist und keine
Abwanderung.

Und die 90 Tage sind gesetzt, nicht gemessen. Sobald genug Kohorten da sind,
sollte die Frist aus der Verteilung „Zeit bis erstem Call" kommen und nicht aus
einer runden Zahl.

## Umsetzung, wenn entschieden

`scripts/revoke_inactive.py --dry-run` listet die Kandidaten samt Alter und
Registrierungsplattform; ohne Flag revoked es sie und meldet die Anzahl per
Telegram. Ein Lauf pro Woche reicht. Die Zählregeln in `agent-counting.md`
brauchen dann einen Absatz, der `inactive_90d` von einer echten Revocation
unterscheidet.
