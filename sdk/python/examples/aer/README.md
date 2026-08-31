# AER-Beispiel — eine Entscheidung, offline nachgerechnet

`decision.json` ist eine fertige Entscheidung: Verdikt-Record, Evidenz-Bündel, Mandat und
Transaktion. `trust.json` sagt, welchen Quellen der Prüfende glaubt. Beides ist reines JSON;
zum Prüfen braucht es weder den MolTrust-Server noch eine Verbindung.

```bash
moltrust-verify --input decision.json --trust-list trust.json
```

```
V1 PASS bundle commit and input binding hold
V2 PASS every item carries a signature from a trusted source
V3 PASS every item window covers the decision timestamp
V4 PASS recomputed PERMIT from the same inputs

PASS — recomputed verdict PERMIT
```

Der Fall: eine USDC-Überweisung über 500 Minor-Units. Das Mandat verlangt die exakte
Empfängeradresse und drei lebende Vorbedingungen — die Berechtigung ist nicht widerrufen,
die Jurisdiktion des Empfängers liegt in `{CH, DE}`, und der Betrag bleibt zum
mitgeführten Kurs von 0,92 EUR/USDC unter einem Limit von 500 EUR-Minor-Units. Drei Quellen
haben je eine Aussage mit Gültigkeitsfenster signiert; alle drei Fenster decken den
`decision_timestamp` `2026-08-31T12:00:00Z`.

Eine Änderung an `decision.json` bricht die Prüfung. Der kürzeste Weg, das zu sehen:

```bash
python3 -c "import json; d=json.load(open('decision.json')); \
  d['bundle']['decision_timestamp']='2026-08-31T12:00:01Z'; \
  json.dump(d, open('tampered.json','w'))"
moltrust-verify --input tampered.json --trust-list trust.json    # Exit-Code 1
```

Die Schlüssel der drei Quellen sind Demo-Material und entstehen deterministisch aus ihrem
Namen (`build_example.py`). Sie gehören keiner echten Quelle und dürfen in nichts anderem
auftauchen als in diesem Beispiel. `build_example.py` erzeugt beide Dateien neu und läuft
gegen das installierte Paket:

```bash
pip install moltrust-enforce && python3 build_example.py
```
