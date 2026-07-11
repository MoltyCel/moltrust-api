# anti-KI-Sprech.md — RETIRED (moved to moltrust-web)

This copy is retired. The canonical voice docs live in the **moltrust-web** repo root:

- `anti-KI-Sprech.md` — negative side (forbidden words / patterns)
- `my-voice-de.md` — positive side, German register
- `my-voice-en.md` — positive side, English register

The Content-Scout worker reads them from the moltrust-web shallow clone
(`workers/content_scout/config.py` → `DOC_ANTI_KI = WEB_DOCS_CLONE / "anti-KI-Sprech.md"`).

Do **not** re-add rules here — one canonical source only. This copy diverged once
(stale at changelog 2026-07-09 while moltrust-web already carried newer §3 tells);
keeping it as a pointer prevents that from recurring.
