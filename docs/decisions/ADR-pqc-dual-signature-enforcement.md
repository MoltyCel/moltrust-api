# ADR: PQC dual-signature — capability present, enforcement off by default

**Status:** Accepted (Lars, 2026-07-05)
**Context:** PR #209 added post-quantum dual signatures (Ed25519 + ML-DSA-65) to
the Verifiable-Credential path, initially hard-enforcing that a PQC-capable
issuer must dual-sign.

## Decision

The credential format supports dual signatures (classic Ed25519 + PQC
ML-DSA-65). Enforcement is **not** turned on today. It is gated by a single
central switch, `PQC_ENFORCE` (environment variable), **default off**.

- **Default (off) — advisory:** verification still evaluates the policy and
  surfaces the outcome in the response (`pqc_policy: "satisfied" | "would_reject"`)
  plus a log line, but an Ed25519-only credential from a PQC-capable issuer is
  **accepted**. No existing issuer breaks for lacking a second signature.
- **`PQC_ENFORCE` on — reject:** a PQC-capable issuer's single-signed JCS
  credential is rejected.
- **Legacy** credentials (`sort_keys`, no `canonicalizationAlgorithm`) are always
  exempt — they predate the format and only ever had one leg.

The cryptographic guarantees are unchanged either way: a *dual-signed*
credential still cannot be stripped to Ed25519-only (skeleton binding), both
legs are still verified, `liboqs-python` stays hard-pinned, and the proofValue
length cap stays.

> **Nachtrag 2026-07-28 — der Pin ist gestrichen.** Der Satz oben („`liboqs-python`
> stays hard-pinned") gilt seit diesem Datum nicht mehr. PyPI hat `0.15.0`
> zurückgezogen; das Projekt servierte nur noch `0.16.0`, die gepinnte Version
> antwortete mit 404. Damit scheiterte `pip install -r requirements.txt` und
> **jeder** PR im Repo lief rot, auch reine Docs-PRs. Der Pin wurde gestrichen
> statt angehoben: die Prod-venv hatte das Paket nie installiert,
> `dilithium.is_available()` liest dort `False`, `DILITHIUM_*` ist nicht gesetzt,
> und `app/crypto/dilithium.py` importiert `oqs` lazy innerhalb der Funktionen.
> Ein Anheben hätte bei jedem frischen Install eine PQC-Bibliothek hereingezogen,
> die kein Codepfad aufruft.
>
> Die Begründung des ursprünglichen Hard-Pins — Supply-Chain-Drift bei einem
> pre-1.0, nicht FIPS-validierten C-Binding, 3-Modell-Review-Konsens — **gilt
> weiter**. Sie ist nur nicht mit `==` gegen ein Projekt lösbar, das seine
> Releases zurückzieht: `0.10.2` wurde vom Review zitiert und existierte nie,
> dann waren `0.14.1`/`0.15.0` die realen, und `0.15.0` ist jetzt ebenfalls weg.
> Der ausführliche Kommentar an der Fundstelle in `requirements.txt` trägt die
> Bitte, den Pin nicht blind wieder einzusetzen.
>
> **Wieder deklarieren, wenn PQC scharf geht** — gepinnt, gegen das dann
> existierende Release, und mit im Deploy verifizierter Installation statt
> angenommener. Am Rest dieses ADR ändert der Nachtrag nichts: Status bleibt
> **Accepted**, `PQC_ENFORCE` bleibt default off, die Skeleton-Bindung und der
> proofValue-Cap sind unberührt. Betroffen ist ausschließlich die Aussage über
> den Pin. Siehe auch `ADR-dependency-pinning.md`.

## Rationale

No coercion until there is real need. The capability is prepared and tested;
turning enforcement on is a one-line env flip once the ecosystem (issuers,
wallets) is ready. **No deprecation end-date is set — deliberately open.**

## How to flip

Set `PQC_ENFORCE=true` in the service environment. The switch is read at verify
time.
