# Changelog — moltrust-enforce

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung nach
[SemVer](https://semver.org/lang/de/). Vor 1.0.0 darf eine Minor-Version brechen; wo sie es
tut, steht der Bruch unter **BREAKING** mit der Migrationszeile daneben.

## [0.3.0] — unveröffentlicht

### BREAKING

- **`httpx` ist keine Basis-Abhängigkeit mehr.** Der HTTP-Client steht im Extra `client`.
  Wer `EnforceClient` benutzt, migriert mit:

  ```bash
  pip install 'moltrust-enforce[client]'
  ```

  Betroffen sind alle vier Namen aus `client.py` — `EnforceClient`, `Ratification`,
  `Verdict` und `VerifyResult`. Gemessen im Subprozess mit blockiertem httpx-Import; die
  Namen aus Kern, Evidenz-Schicht und Verifizierer sind nicht betroffen
  (`tests/test_aer_verify.py::test_every_client_name_needs_httpx_and_no_other_name_does`).

  Der Zugriff auf einen der vier ohne das Extra wirft keinen nackten
  `ModuleNotFoundError`, sondern nennt das Extra und den ganzen betroffenen Satz. Der
  Grund für den Schnitt: wer ein fremdes Urteil nachrechnet, ist nicht derselbe wie der,
  der Verdikte anfragt. Eine Prüf-Installation kommt damit auf 5 Pakete statt 12.

  0.1.0 und 0.2.0 liegen auf PyPI, der Bruch trifft also bestehende Installationen.

### Hinzugefügt

- **AER — Attested-Evidence Replay**, Baustufe 1 und 4 der Feature-Spec. Der Kern
  entscheidet zusätzlich über lebende Vorbedingungen — Widerrufsstand, Sanktions- und
  Jurisdiktionsstatus, Umrechnungskurs —, die als signierte Aussagen mit
  Gültigkeitsfenster im Bündel liegen und mit der Entscheidung aufbewahrt werden.
  - `evidence.py` — Evidenz-Item als DSSE-Envelope; signiert wird die PAE über
    `(payloadType, payload)`. Bündel aufsteigend nach `item_digest`, `bundle_commit` über
    alles außer sich selbst, `mandate_ref`/`transaction_ref` unter denselben Domain-Tags
    wie der statische Kern. Zeiten als RFC-3339-UTC auf ganze Sekunden.
  - `_ext_core.py` — `f_ext(mandate, transaction, bundle)`, rein, ohne Netz und ohne Uhr.
    Vier Evidenz-Constraints: `evidence_bool`, `evidence_enum`, `evidence_range` und
    `evidence_scaled_range` (Betrag mal Kurs gegen ein Fiat-Limit, ganzzahlig ohne
    Rundung). Jeder prüft zusätzlich das Fenster seines Items gegen den
    `decision_timestamp`.
  - `verify.py` — V1 Integrität, V2 Ed25519 gegen eine mitgebrachte Trust-List,
    V3 Frische über alle Items, V4 Nachrechnung. Alle vier laufen auch dann, wenn eine
    schon fällt.
  - `cli.py` — `moltrust-verify` als Konsolenskript, netzfrei, Exit 0/1/2.
  - `examples/aer/` — eine fertige Entscheidung als JSON plus Trust-List und der
    deterministische Generator dazu.
- Extra `verify` — leer, damit `pip install 'moltrust-enforce[verify]'` läuft und die
  Antwort auf die Frage nach den Abhängigkeiten des Offline-Pfads im Paket selbst steht.

### Geändert

- `EnforceClient`, `Ratification`, `Verdict` und `VerifyResult` werden über ein
  Modul-`__getattr__` (PEP 562) erst beim Zugriff geladen. `import moltrust_enforce.cli`
  zieht damit weder `httpx` noch `socket` oder `ssl` in den Prozess; geladen sind `jcs`
  und `cryptography`. Die öffentliche Oberfläche bleibt unverändert.
- `evidence_payload_bytes` heißt so und nicht `statement_bytes` — den Namen belegt der
  Ratifikations-Kern bereits mit anderer Bedeutung.
- CI-Job `sdk-tests` installiert `[test]`, weil die Client-Tests httpx brauchen, das jetzt
  im Extra liegt.

### Behoben

- Die README-Zeile „Nicht auf PyPI" war falsch; 0.1.0 und 0.2.0 liegen dort.

## [0.2.0] — PR #309

- Ratifikations-Kern (`ratify`, `mandate_authorities`, `ratification_statement`) und die
  lokale Prüfung der Ratifikations-Signatur über Ed25519.

## [0.1.0] — PR #307

- Referenz-Client für `POST /enforce/check` mit lokalem Nachrechen-Kern (`enforce_check`,
  `action_digest`, `core_digest`, `recompute`).
