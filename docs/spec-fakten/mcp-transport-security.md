# MCP Python SDK — Transport-Security (Advisories 2026)

**Paket:** `mcp` (Model Context Protocol Python SDK, PyPI).
**Quellen:** GitHub Security Advisories `GHSA-jpw9-pfvf-9f58`, `GHSA-hvrp-rf83-w775`,
`GHSA-vj7q-gjh5-988w`; Abgleich der Fix-Versionen gegen `pip-audit` auf der Prod-venv.
**Erhoben:** 2026-07-28. **Bei Bedarf gegen OSV/GHSA neu prüfen** — CVSS-Werte und
Fix-Bereiche werden nachträglich korrigiert.

**Version-Pin-Rationale:** `1.28.1` ist die **niedrigste** Version, die alle drei
Advisories abdeckt. Zwei sind in `1.27.2` behoben, das dritte erst in `1.28.1`.
Ein Floor auf `1.27.2` liesse also eines offen. Deklariert in
`requirements.txt` (`mcp>=1.28.1`) und im Paket `moltrust-mcp-server` ab `1.2.2`.

## Die drei Advisories

| Advisory | CVE | Schwere | Fix ab | Trifft uns |
|---|---|---|---|---|
| `GHSA-jpw9-pfvf-9f58` | CVE-2026-52869 | HIGH — `CVSS:3.1/AV:N/AC:H/PR:L/UI:N/S:U/C:H/I:H/A:L` | 1.27.2 | **ja** |
| `GHSA-hvrp-rf83-w775` | CVE-2026-52870 | HIGH — `CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:L/A:L` | 1.27.2 | nein |
| `GHSA-vj7q-gjh5-988w` | CVE-2026-59950 | HIGH — `CVSS:4.0/AV:N/AC:L/AT:P/PR:N/UI:P/VC:H/VI:H/VA:N` | 1.28.1 | nein |

### CVE-2026-52869 — Session-Routing ohne Aufrufer-Prüfung

Die SSE- und Streamable-HTTP-Transporte ordnen einen eingehenden Request allein
anhand der Session-ID einer bestehenden Session zu, ohne zu prüfen, ob der
Request als derselbe Aufrufer authentifiziert ist. Wer eine fremde Session-ID
kennt, spricht in deren Kontext.

**Warum das uns trifft:** `services/mcp_http.py` fährt genau diesen Transport —
`mcp.run(transport="streamable-http")`, `streamable_http_path = "/mcp"`, öffentlich
erreichbar unter `https://api.moltrust.ch/mcp` hinter nginx. Der nginx-Block davor
macht nur Rate-Limiting, keine Authentifizierung.

### CVE-2026-52870 — experimentelle Task-Handler

Die Default-Handler des experimentellen Task-Features prüfen nicht, welche Session
einen Task angelegt hat. Erreichbar nur nach `server.experimental.enable_tasks()`.
Wird bei uns nirgends aufgerufen.

### CVE-2026-59950 — WebSocket ohne Host/Origin-Validierung

Der deprecatete WebSocket-Server-Transport nimmt den Handshake ohne `Host`- oder
`Origin`-Prüfung an, `TransportSecuritySettings` greift dort nicht. Wir importieren
den WebSocket-Transport nicht.

## Verhältnis zum request-scoped api_key

Der Kern von CVE-2026-52869 ist, dass ein Request die Authentifizierung einer
fremden Session erbt. Bei uns greift das nur eingeschränkt, weil
`moltrust_mcp_server/server.py` den API-Key **pro Request** aus einer contextvar
auflöst (Query-Parameter `api_key`, `X-API-Key` oder `Bearer`) statt ihn an die
Session zu binden — siehe `_session_api_key()`. Der Kommentar dort nennt genau das
Race als Grund. Wer eine Session-ID erbeutet, trägt damit keine geliehene
Autorisierung; Session-State und Tool-Ergebnisse bleiben das Restrisiko.

Diese Eigenschaft ist Mitigation, **nicht** Ersatz für den Versions-Floor. Sie ist
ausserdem eine Eigenschaft unseres Codes, nicht des SDK — ein SDK-Update, das die
Auth-Verdrahtung ändert, könnte sie aushebeln. Verifiziert wurde deshalb beim
Bump auf `1.28.1`:

- `TestSessionKeyIsolation` (3 Tests aus `moltrust-mcp-server#12`) grün auf `1.26.0`
  und auf `1.28.1`, Vollsuite je 90 passed
- End-to-End gegen einen laufenden streamable-http-Server: 12 Runden mit je zwei
  überlappenden Sessions und verschiedenen Keys, 0 Abweichungen
- Negativ-Kontrolle (Key-Auflösung auf eine Konstante gezwungen) macht denselben
  Lauf 12/12 rot — der grüne Befund ist also nicht vakuum-grün

Die drei Unit-Tests allein reichen dafür nicht: sie bauen den Context per
`MagicMock` und fassen den Transport nie an.

## In 1.28.1 nachgesehen

Die Verdrahtung, auf der `_session_api_key()` aufsetzt, ist unverändert:
`request_ctx` ist weiterhin ein `contextvars.ContextVar`, weiterhin pro
eingehender Nachricht in `mcp/server/lowlevel/server.py` gesetzt, und
`RequestContext.request` trägt weiterhin den per-Request-Starlette-Request.

## Häufige Fehlzuordnung

Alle drei Advisories werden gern als „das MCP-Session-Bug" zusammengefasst. Sie
haben verschiedene Voraussetzungen und verschiedene Fix-Versionen. Wer nur
`1.27.2` zitiert, lässt CVE-2026-59950 offen; wer den WebSocket-Fund auf den
HTTP-Transport überträgt, beschreibt eine Lücke, die es dort nicht gibt.
