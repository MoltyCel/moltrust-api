# ADR: Dependency-Pinning — Werkzeuge exakt, Laufzeit auf den Fix, nie `==` gegen flüchtige Releases

**Status:** Accepted (2026-08-11)
**Kontext:** Am 2026-07-28 fielen an einem Tag zwei CI-Ausfälle an, beide durch
Pinning-Entscheidungen, beide in entgegengesetzte Richtungen falsch.

## Was passiert ist

`moltrust-mcp-server#14` änderte eine einzige Zeile in `pyproject.toml` und lief
rot — mit einem `I001` in `src/moltrust_mcp_server/server.py`, einer Datei, die der
Branch nie angefasst hatte (`git diff main -- src/` war leer). Die CI machte
`pip install ruff` ohne Pin; zwischen dem letzten grünen Lauf auf `main` und diesem
Push war `ruff 0.16.0` erschienen und brachte eine neue Regel mit. Jeder danach
geöffnete PR wäre in dieselbe Wand gelaufen.

Am selben Tag brach `moltrust-api` vollständig: `liboqs-python==0.15.0` war von
PyPI verschwunden, nur noch `0.16.0` existierte, die gepinnte Version antwortete
mit 404. `pip install -r requirements.txt` scheiterte, und damit **jeder** PR im
Repo, auch reine Docs-PRs. Der Pin stammte aus einem 3-Modell-Review-Konsens gegen
Supply-Chain-Drift bei einem pre-1.0-C-Binding — ein sachlich richtiges Motiv, das
sich nur nicht mit `==` durchsetzen liess: `0.10.2` wurde vom Review zitiert und
existierte nie, dann waren `0.14.1`/`0.15.0` die realen, dann war `0.15.0` weg.

## Entscheidung

**Lint- und Typecheck-Werkzeuge exakt pinnen.** `ruff` und `pyright` gaten auf Stil
und Inferenz und liefern mit jeder Version neue Befunde. Ein Floor lässt CI und
lokale Prüfung auseinanderlaufen, und die Differenz zeigt sich erst nach dem Push.
Gepinnt wird auf die Version, die der letzte grüne Lauf benutzt hat, in `ci.yml` und
in der Dev-Dependency-Group gleichlautend. Angehoben wird bewusst, in einem eigenen
PR, zusammen mit dem, was die neue Version verlangt.

**Test-Runner behalten ihren Floor.** `pytest` erfindet keine neuen Fehlschläge wie
ein Linter; ein Pin kostet dort mehr, als er bringt.

**Laufzeit-Abhängigkeiten auf ihren Security-Fix flooren, nicht pinnen.** `>=` auf
die Version, die den Fund behebt, mit dem Advisory als Kommentar an der Zeile.
Beispiele: `mcp>=1.28.1` (CVE-2026-52869, siehe
`spec-fakten/mcp-transport-security.md`), `aiosmtplib>=5.1.1` (GHSA-v3q9-hj7j-63hq).
Das hält die Untergrenze fest, ohne bei jedem Patch-Release nachziehen zu müssen.

**Kein `==` gegen ein Projekt, das Releases zurückzieht.** Wo Supply-Chain-Sorge
besteht und der Anbieter unzuverlässig veröffentlicht, ist ein Hash-Pin
(`--require-hashes`) oder ein Vendoring die belastbare Antwort. Ein `==` auf eine
Version, die verschwinden kann, tauscht ein hypothetisches Risiko gegen einen
sicheren Totalausfall.

**Was importiert wird, wird deklariert.** `aiosmtplib`, `mcp` und
`moltrust-mcp-server` liefen produktiv, ohne in `requirements.txt` zu stehen — von
Hand in die venv installiert. Ein `pip install -r requirements.txt` auf einer
frischen Maschine hätte den MCP-Server nicht mitgebracht und die API wäre beim
Modul-Load gescheitert.

**Ein Pin auf ein optionales Paket beschreibt die Realität oder verschwindet.**
`liboqs-python` stand vier Monate in `requirements.txt`, ohne je in der Prod-venv
installiert zu sein. `pip-audit` sah es deshalb nicht und konnte auch keine
Entwarnung geben. Ein Pin, den die laufende Umgebung nicht kennt, ist kein Schutz,
sondern eine Behauptung.

## Konsequenzen

Die Toolchain bewegt sich nicht mehr von selbst unter dem Repo weg; dafür muss sie
von Hand angehoben werden, sonst veraltet sie still. Der Floor-Ansatz bei
Laufzeit-Deps lässt Minor-Updates ungeprüft durch — das ist der bewusste Preis
dafür, dass Security-Fixes nicht an einem vergessenen Pin hängenbleiben.

Nicht geregelt und offen: ein Hash-Pinning-Verfahren (`--require-hashes`) und ein
Renovate- oder Dependabot-Lauf, der Werkzeug-Pins vorschlägt statt sie ungefragt
zu verschieben.

## Referenzen

- `moltrust-mcp-server#14` (mcp-Floor), `#15` (ruff/pyright gepinnt)
- `moltrust-api#287` (liboqs-Pin gestrichen), `#289` (drei Deps deklariert)
- `ADR-pqc-dual-signature-enforcement.md` — Nachtrag 2026-07-28 zum liboqs-Pin
- `spec-fakten/mcp-transport-security.md` — warum der Floor bei `1.28.1` liegt
