"""Trockenlauf: hält die Autorisierung, und wer sind wir für LinkedIn.

Posting is not implemented and this does not post. It reports three things the
decision about automatic posting depends on:

  does the stored token still work   — by calling userinfo with it
  which scopes it actually carries   — the granted set, not the requested one
  when it expires, and whether it can be renewed at all

The third is the one to read carefully. LinkedIn issues programmatic refresh
tokens only to approved Marketing Developer Platform partners, and this app is
Share on LinkedIn plus Sign In with OpenID Connect. If `refreshable` is false
the seven-day renewal has nothing to renew and re-authorisation is a manual
step every sixty days — which is a fact about the app tier, not a defect.

    python3 scripts/linkedin_auth_check.py           # report
    python3 scripts/linkedin_auth_check.py --renew   # renew if it is due
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import linkedin_oauth as li
from app import notify

# memberCreatorPostAnalytics is not available to this app. The LinkedIn figures
# are a manual series in scripts/linkedin_metrics.py and there is no endpoint
# to find. Written here as well as in app/linkedin_oauth.py because this is the
# file somebody opens when they wonder where the numbers come from.
ANALYTICS_NOTE = ("memberCreatorPostAnalytics steht dieser App nicht zur "
                  "Verfügung — die Kennzahlen bleiben manuelle Erfassung "
                  "(scripts/linkedin_metrics.py).")


def check() -> dict:
    st = li.status()
    out = {"stored": st}
    if st["state"] in ("none", "unreadable"):
        return out
    tokens = li.load_tokens_safe() or {}
    token = tokens.get("access_token")
    if not token:
        out["live"] = {"ok": False, "detail": "kein Access-Token im Speicher"}
        return out
    try:
        member = li.fetch_member(token)
        out["live"] = {"ok": True, **member}
    except RuntimeError as e:
        # The token is the only thing that can prove itself. A stored expiry in
        # the future and a 401 from userinfo is the case worth seeing.
        out["live"] = {"ok": False, "detail": str(e)}
    return out


def report(k: dict) -> str:
    st = k["stored"]
    L = ["🔗 <b>LinkedIn-Autorisierung</b>", ""]
    if st["state"] == "none":
        L += ["Nicht autorisiert. Ablauf:",
              "1. <code>GET https://api.moltrust.ch/oauth/linkedin/start</code>",
              "2. die zurückgegebene URL im Browser öffnen, als @moltrust bestätigen",
              "3. dieser Trockenlauf bestätigt dann die Mitglieds-Kennung", "",
              ANALYTICS_NOTE]
        return "\n".join(L)
    if st["state"] == "unreadable":
        L += [f"<b>Token-Speicher nicht lesbar:</b> {st['detail']}",
              "Das ist ein Befund, kein leerer Speicher — die Datei liegt da "
              "und öffnet nicht. Nicht überschreiben, ansehen."]
        return "\n".join(L)

    live = k.get("live") or {}
    L += [f"Zustand: <b>{st['state']}</b>",
          f"Scopes laut Token: <code>{st.get('scope') or '—'}</code>",
          f"Access-Token läuft ab: {st.get('access_expires_at') or '—'}"
          + (f" (in {st['days_left']} Tagen)" if st.get("days_left") is not None
             else ""),
          f"Mitglieds-Kennung: <code>{st.get('member_sub') or '—'}</code>",
          f"Autor-URN: <code>{st.get('author_urn') or '—'}</code>", ""]
    if st.get("refreshable"):
        L += [f"Erneuerbar: <b>ja</b>, Refresh läuft "
              f"{st.get('refresh_expires_at') or '—'} ab. Automatische "
              f"Erneuerung sieben Tage vor Ablauf."]
    else:
        L += ["Erneuerbar: <b>nein</b> — LinkedIn gibt programmatische "
              "Refresh-Token nur an zugelassene Marketing-Developer-Platform-"
              "Partner. Diese App ist Share on LinkedIn plus Sign In with "
              "OpenID Connect. Neuautorisierung also von Hand, alle 60 Tage; "
              "der Sammelbericht meldet es sieben Tage vorher."]
    L += ["", f"Live geprüft: {'ja, userinfo antwortet' if live.get('ok') else 'NEIN — ' + str(live.get('detail'))[:120]}",
          "", "Posten ist nicht aktiviert.", ANALYTICS_NOTE]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--renew", action="store_true",
                    help="erneuern, wenn fällig und möglich")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.renew:
        st = li.status()
        if st["state"] not in ("renew_due", "expired"):
            print(f"nicht fällig: {st['state']}")
            return 0
        if not st.get("refreshable"):
            msg = ("LinkedIn-Token läuft ab und kann nicht erneuert werden — "
                   "kein Refresh-Token (App ist kein MDP-Partner). "
                   "Neuautorisierung von Hand nötig.")
            print(msg)
            notify.send_telegram(f"🔗 {msg}", channel=notify.STATS)
            return 1
        try:
            out = li.refresh()
            print(f"erneuert, neu bis {out['access_expires_at']}")
            return 0
        except RuntimeError as e:
            # Into the collected report, as instructed — not an interruption.
            notify.send_telegram(
                f"🔗 <b>LinkedIn-Erneuerung fehlgeschlagen</b>\n{e}",
                channel=notify.STATS, parse_mode="HTML")
            print(f"fehlgeschlagen: {e}")
            return 1

    k = check()
    text = report(k)
    print(json.dumps(k, indent=1, ensure_ascii=False) if a.json else text)
    if a.send:
        notify.send_telegram(text, channel=notify.STATS, parse_mode="HTML")
    return 0 if (k["stored"]["state"] == "ok"
                 and (k.get("live") or {}).get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
