# ADR-AER — Attested-Evidence Replay: Evidenz-Schicht über AAE

**Status:** **ACCEPTED** (Baustufe 1 und 4 implementiert). Baustufen 2, 3, 5, 6 offen — siehe §Nicht gebaut.
**Datum:** 2026-08-31 · **Autor:** Lars Kroehl
**Bezug:** Feature-Spec `MolTrust_AER_Feature-Spec_Build-Handoff.md` (Übergabe-Dokument, nicht im Repo). Erweitert `ADR-D3-mandate-enforcement-v3.md` (Enforce-Kern) um die Vorbedingungs-Ebene. Die frühere Quarantäne des Musters ist aufgehoben: das Stufe-2-Neuheits-Gate ist negativ, die Technik ist publiziert (OCSP RFC 6960 für die Fenster-Primitive, DSSE für den Envelope, „Governing Actions, Not Agents" arXiv 2606.26298 für die Gesamtkomposition, OPA/Styra/Permit.io für Autorisierungs-Replay). Freedom-to-Operate: bauen und veröffentlichen ist frei, ein Schutzrecht wird nicht beansprucht.

**Recon-Basis (verifiziert 2026-08-31, `origin/main` = `a533390`):**
- (a) `sdk/python/src/moltrust_enforce/_core.py` — 335 Zeilen, rein, JCS/RFC 8785, Domain-Tags je Digest-Rolle, deny-by-default, `recompute()` für die Dritt-Nachrechnung.
- (a) `sdk/python/tests/test_core_parity.py:37` — `_core.py` muss zu `app/enforcement/enforce_check.py` bis auf die Import-Zeile zeilengleich sein. `_core.py` ist damit nicht änderbar; AER importiert daraus.
- (a) `app/enforcement/evaluator.py` — der DB-gebundene AAE-Evaluator, getrennte Maschine, teilt keinen Code mit dem Kern.
- (a) `moltstack/verify-package/package.json` — npm `@moltrust/verify` 1.0.0 ist der VC-/On-Chain-Verifizierer und hat mit dem AER-CLI weder Code noch Format gemeinsam. Namensnähe ist bewusst in Kauf genommen, weil die Feature-Spec §5 den Namen `moltrust-verify` setzt und die Namensräume (PyPI-Konsolenskript ↔ npm-Paket) getrennt sind.

---

## Problem

`enforce_check(mandate, transaction)` entscheidet über das, was im Request steht. Ein Teil der Compliance-Entscheidung steht dort aber nicht drin und kann dort auch nicht stehen: ob die Berechtigung inzwischen widerrufen wurde, ob der Empfänger auf einer Sanktionsliste gelandet ist, welcher Umrechnungskurs für ein Fiat-Limit gilt. Diese Fakten leben außerhalb und ändern sich.

Wer sie zur Entscheidungszeit abruft und das Ergebnis nur behauptet, gibt einem Dritten nichts zu prüfen. Wer sie zur Prüfzeit neu abruft, bekommt einen anderen Weltzustand und damit ein anderes Urteil — die Nachrechnung ist dann keine Nachrechnung mehr.

## Entscheidung

Jeder Faktenwert wird als signierte Aussage mit Gültigkeitsfenster in die Entscheidung eingebunden und mit ihr aufbewahrt. Der Prüfende rechnet aus dem mitgeführten Material nach, ohne eine Quelle erneut zu fragen.

Fünf Festlegungen, die die Feature-Spec §8 offen gelassen hatte:

**Das Fenster kommt von der Quelle.** Ein Item trägt `valid_from` und `valid_until`, die die Quelle mitsigniert. Es gibt keine Ableitung aus einer Max-Age-Policy des Verifizierers: zwei Verifizierer mit verschiedenen Policies kämen sonst zu verschiedenen Urteilen über denselben Record, und das Fenster wäre keine Aussage der Quelle mehr. Kann eine Quelle das nicht liefern, vouched später ein Attesting-Adapter mit eigenem Schlüssel — ein sichtbarer Trust-Downgrade, der im `source_id` steht.

**Der Entscheidungszeitpunkt wird von den Fenstern eingeklammert, nicht extern beglaubigt.** `decision_timestamp` behauptet der Entscheider. V3 verlangt, dass jedes Item-Fenster ihn deckt; die Schnittmenge aller Fenster ist damit die belegte Ober- und Untergrenze. Ein Base-L2-Anker auf `bundle_commit` als notarisierte Obergrenze bleibt möglich und ist hier nicht gebaut — er kostet eine Chain-Abhängigkeit zur Prüfzeit, die `ADR-0002` gerade abbaut.

**Schlüssel bringt der Prüfende mit.** Der Verifizierer nimmt eine Trust-List entgegen: `source_id` → Ed25519-Public-Keys. Keine DID-Auflösung, kein Netz. Welchen Quellen er glaubt, ist seine Entscheidung und nicht die des Entscheiders; ein Verifizierer, der Schlüssel erst online holen müsste, wäre außerdem kein Offline-Verifizierer.

**Zeiten sind RFC-3339-UTC auf ganze Sekunden.** `YYYY-MM-DDTHH:MM:SSZ`, keine Bruchteile, kein Offset außer `Z`, keine Schaltsekunde, Schranke 1970 bis 2100. Verglichen wird danach auf Ganzzahlen. Alles Mehrdeutige fällt durch, statt geraten zu werden.

**Score bleibt draußen.** Reputations- oder Score-Werte als Evidenz sind nicht vorgesehen. Widerruf, Sanktion und Kurs sind die Leitfälle; ein Score als Entscheidungseingabe holt eine Angriffsfläche herein, die für den Compliance-Fall nichts trägt.

### Format

Ein Evidenz-Item ist ein DSSE-Envelope, kein Eigenformat. Signiert wird die DSSE-PAE über `(payloadType, payload)`, nicht der bloße Hash des Statements — die Feature-Spec §4.2 skizzierte das anders; PAE bindet den Typ mit ein, ein Statement lässt sich damit nicht als anderer Nachrichtentyp weiterverwenden. Das Statement trägt genau sieben Felder (`aer_version`, `source_id`, `query`, `value`, `valid_from`, `valid_until`, `nonce`), weitere sind verboten. Die dekodierten Payload-Bytes müssen exakt JCS(statement) sein, sonst ließe sich einer gültigen Signatur ein anders serialisiertes Statement unterschieben.

Ein Bündel hält die Items aufsteigend nach `item_digest`. Die Ordnung hängt am Inhalt und nicht daran, in welcher Reihenfolge der Entscheider gefragt hat; zwei Sammlungen derselben Items ergeben denselben `bundle_commit`. Doppelte Items und zwei Antworten auf dieselbe Abfrage sind verboten. `mandate_ref` und `transaction_ref` benutzen dieselben Domain-Tags wie `_core`, der Wert im Bündel ist also derselbe String wie `core["mandate_digest"]`.

### Kern

`f_ext(mandate, transaction, bundle)` liegt neben `enforce_check` und nicht darin. `_core.py` bleibt zeilengleich zum Server-Kern (Parity-Test oben), also importiert `_ext_core` die Prädikate von dort. `exact`, `enum` und `range` verhalten sich unverändert; ein Mandat ohne Evidenz-Constraints bekommt von beiden Maschinen dasselbe Verdikt, dieselbe `reason` und denselben `grant_index`. Die `core_digest`-Werte unterscheiden sich, weil der AER-Core `bundle_commit` und `decision_timestamp` mitträgt und einen eigenen Domain-Tag benutzt — ein AER-Core soll nicht als statischer Core durchgehen.

Vier Constraint-Typen zeigen auf das Bündel: `evidence_bool` (Widerruf, Sanktion), `evidence_enum` (Jurisdiktion), `evidence_range` (Plausibilitätsgrenze) und `evidence_scaled_range` (Betrag mal Kurs gegen ein Fiat-Limit). Der Kurs steht als Ganzzahl in Einheiten von `10**rate_scale`, verglichen wird `betrag * kurs` gegen `grenze * 10**rate_scale` — ohne Division, ohne Rundung, weil ein Fließkomma-Zwischenschritt je nach Plattform ein anderes Urteil ergibt.

Jeder Evidenz-Constraint prüft zusätzlich das Fenster seines Items gegen `decision_timestamp`. Der Kern entscheidet damit nie auf Evidenz, die zum Entscheidungszeitpunkt nicht galt. Signaturen prüft er nicht — das braucht Schlüssel, die im Kern nichts zu suchen haben, und steht als V2 im Verifizierer.

Ein strukturell unbrauchbares Bündel ist DENY, auch wenn das Mandat gar keine Evidenz braucht. Wer AER benutzt, bekommt kein Urteil auf einem Bündel, das der Verifizierer nachher verwirft.

### Verifizierer

`verify_record(record, bundle, mandate, transaction, trust_list)` und das CLI `moltrust-verify` prüfen vier Dinge, und alle vier laufen auch dann, wenn eine schon fällt:

| | Prüfung | Wehrt ab |
|---|---|---|
| V1 | `bundle_commit` passt zum Inhalt; Record nennt denselben Commit; Refs binden die gelieferten Eingaben | untergeschobenes Bündel, ausgetauschtes Mandat, recycelte Evidenz aus einer anderen Entscheidung |
| V2 | je Item eine Ed25519-Signatur über die PAE gegen einen Schlüssel der Trust-List | nachträglich gedrehter Wert, Fremdsignatur, unbekannte Quelle |
| V3 | je Item `valid_from ≤ T ≤ valid_until`, über alle Items | abgelaufene Evidenz als frisch ausgegeben |
| V4 | `f_ext` neu gerechnet, `core_digest` und Verdikt verglichen; zusätzlich muss der Digest den Core im Record selbst decken | Verdikt inkonsistent zu den Eingaben; Record, dessen Core-Objekt und Digest auseinanderfallen |

V3 deckt auch Items ab, die kein Constraint anspricht. Ein Bündel mit abgelaufener Beilage ist als Ganzes nicht mehr das, was vorgelegt wurde.

Die V4-Selbstkonsistenz kam beim Bau dazu: ohne sie überlebt ein Record alle vier Prüfungen, dessen `core` PERMIT zeigt, während `core_digest` über einen anderen Core rechnet. Wer den Core liest statt ihn nachzurechnen, liest dann eine Lüge.

## Was das belegt und was nicht

Belegt ist: der Betreiber hat genau diese Evidenz benutzt, sie war zum Entscheidungszeitpunkt gültig, und aus ihr folgt genau dieses Verdikt. Offen bleibt, ob eine benannte Quelle die Wahrheit gesagt hat — wer den Schlüssel einer gelisteten Quelle besitzt, kann im Fenster einen falschen Wert signieren. Ebenso offen ist die Änderung eines Faktums innerhalb eines gültigen Fensters; dagegen hilft ein kurzes Fenster oder ein erneuter Abruf unmittelbar vor der Ausführung. „Governing Actions" §5 nennt dieselbe Grenze. Schlüssel-Kompromittierung trifft AER wie jede PKI.

Das Vertrauen ist damit verschoben und benannt: von „glaube dem Betreiber" zu „glaube diesen Quellen je Domäne und rechne die Arithmetik selbst nach".

## Nicht gebaut

Baustufe 2, 3, 5 und 6 der Feature-Spec sind offen. Es gibt keinen Evidence Source Adapter — das SDK prüft Evidenz und stellt keine aus; die Testquellen sind Fixtures. Es gibt keinen `/enforce/check-with-evidence`-Endpunkt und keine server-seitige Evidenz-Beschaffung; damit ist auch die Frage Courier-Muster gegen Server-Sammlung noch nicht entschieden. Die Zwei-Maschinen-Demo existiert nur als Test (`test_the_record_verifies_from_plain_json_without_the_deciding_objects`), nicht als vorführbarer Ablauf über zwei Rechner und mehrere Tage.

Ein Endpunkt zieht die Discovery-Checkliste aus `CLAUDE.md` nach sich (Agent-Card, OpenAPI, `llms.txt`). Solange AER nur im SDK liegt, greift sie nicht.

## Positionierung

Die AAE-Bindung dieses Musters lässt sich Richtung DIF/SCITT einbringen. Beansprucht wird nichts davon als Erfindung: signierter Status mit Gültigkeitsfenster ist OCSP (RFC 2560/6960), Multi-Quellen-Attestierung mit deterministischer Policy und Dritt-Nachprüfung ist „Governing Actions, Not Agents" (arXiv 2606.26298) und der zitierte 2026-Cluster, Autorisierungs-Replay aus signierten Decision-Logs ist OPA/Styra/Permit.io, freshness-geprüfte signierte Manifeste mit Merkle-Transparency sind arXiv 2601.23132. AER ist die AAE-gebundene Implementierung eines offenen Musters.
