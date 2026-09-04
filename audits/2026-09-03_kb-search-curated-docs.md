# KB-Suche: kuratierte Dokumente auffindbar machen

**Datum:** 2026-09-03 · **Typ:** Server-Skript-Änderung (nicht repo-verwaltet) · **Scope:** `~/moltrust-knowledge/search.sh`, `~/moltrust-knowledge/weekly-summary.md`
**Grund:** Audit-Eintrag statt Commit — `moltrust-knowledge/` ist keine repo-verwaltete Fläche (liegt untracked im Home-Repo `/home/moltstack`, das 190 untracked Einträge und keine Commits hat).

## §1 Befund

`search.sh` durchsuchte ausschließlich `chats/`:

```bash
grep -r -l -i "$QUERY" "$KB_DIR/chats/" 2>/dev/null | ...
```

`chats/` ist reine Ausgabe von `import.sh`, also konvertierte claude.ai-Conversations.
Von Hand geschriebene Dokumente liegen in der KB-Wurzel neben `weekly-summary.md` und
waren damit über das Suchwerkzeug **nicht erreichbar** — weder über den Index (der bildet
nur Chat-Dateinamen ab) noch über den Volltext-Fallback.

Aufgefallen beim Anlegen von `security-review-2026-08-31.md` (kuratierte Zusammenfassung
des Harald-Reviews vom 31.08.): eine Suche nach „Versionsobergrenzen" kam leer zurück,
obwohl der Begriff in der Datei steht.

## §2 Verworfener Zwischenschritt

Zuerst war ein Verweis auf das neue Dokument in `weekly-summary.md` eingetragen worden.
Das trägt aus zwei Gründen nicht:

1. `weekly_summary.py` macht ein `SUMMARY_FILE.write_text(...)`, also einen
   Vollüberschreib, und läuft per Cron `0 6 * * 0` — der Verweis wäre am 07.09.
   verschwunden.
2. Auch mit Verweis bliebe die Datei für `search.sh` unsichtbar; der Verweis hätte
   nur ein menschliches Auge erreicht, das ohnehin schon in der KB-Wurzel liest.

`weekly-summary.md` wurde deshalb aus dem Backup `weekly-summary.md.bak-20260903-153230`
wiederhergestellt und ist byte-identisch mit dem Stand vor der Änderung.

## §3 Änderung

`search.sh` bekommt einen dritten Abschnitt hinter dem bestehenden Volltext-Grep:

```bash
# 3. Kuratierte Dokumente in der KB-Wurzel.
# chats/ ist reine Import-Ausgabe; von Hand geschriebene Notizen liegen daneben
# und wurden vom Grep oben nicht erfasst. Kein -r: nur die Wurzel, damit weder
# raw/ (Export-JSONs, dutzende MB) noch chats/ ein zweites Mal durchlaufen wird.
CURATED=$(grep -l -i "$QUERY" "$KB_DIR"/*.md 2>/dev/null)
if [ -n "$CURATED" ]; then
    echo ""
    echo "📄 Kuratierte Dokumente:"
    echo "$CURATED" | while read -r file; do
        echo "  → $(basename "$file")"
        grep -i -m 2 "$QUERY" "$file" | sed 's/^/     /'
    done
fi
```

Bewusst **ohne** `-r`: der Glob `"$KB_DIR"/*.md` trifft nur die Wurzel. Ein rekursiver
Lauf würde `raw/` mitnehmen (dort liegen Export-JSONs von 22 bis 68 MB) und `chats/` ein
zweites Mal durchsuchen.

Die bestehenden Abschnitte 1 (Index) und 2 (Volltext über `chats/`) sind unverändert.

Backup: `search.sh.bak-20260903-155616`, nach dem Muster der vorhandenen
`import.sh.bak-*`.

## §4 Verifikation

```
$ ./search.sh "Versionsobergrenzen"
=== Knowledge Base Suche: 'Versionsobergrenzen' ===

🔍 Volltext:

📄 Kuratierte Dokumente:
  → security-review-2026-08-31.md
     - **G-2** Lockfiles + Versionsobergrenzen über alle 7 Python-Repos.
```

Gegenprobe auf Regression, Begriff der nur in `chats/` vorkommt:

```
$ ./search.sh "Ambassador"
📋 Gefunden in:
  → 2026-02-20: moltrust continuation
     ./chats/2026-02-20-moltrust-continuation-5076acb8.md
  → 2026-02-24: moltrust favicon and og image deployment
  …
```

Index-Treffer und Chat-Volltext unverändert. `bash -n search.sh` sauber.

## §5 Nicht angefasst

- `chats/` — unverändert bei 174 Dateien, neueste vom 11.08.
- `index.json` — unverändert bei 229 Einträgen, Zeitstempel 11.08. 15:07.
- `import.sh` — nicht gelaufen. Das Skript filtert **nicht** nach Thema; ein Lauf gegen
  einen vollständigen Export würde jede Conversation importieren, auch nicht-MolTrust.
- Der claude.ai-Export vom 02.09. (Manifest in `~/Downloads`) — kein Download. Die
  Export-URLs sind laut Manifest einmalig verwendbar, und drei der vier Kategorien
  (`projects`, `memories`, `light_metadata`) fallen unter die Privat-Ausschlussliste.
- Kein `git add`, kein Commit in `/home/moltstack`.

## §6 Offen

Dieser Audit-Eintrag liegt untracked in `audits/`. Bestehende Einträge sind im Repo
committet (zuletzt `107b8e2`, `b5ffcae`, `60facd5`); ob dieser mit soll, ist noch offen.
