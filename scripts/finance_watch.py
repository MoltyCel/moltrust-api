#!/usr/bin/env python3
"""finance-watch — the monthly report on the triggers for MolTrust Finance (ops FIN-WATCH).

Once a month this reads, from free sources without a key:

- capital held by AI agents on Base and Solana, per chain and together
  (DefiLlama, category "AI Agents" plus the extra list in config/finance_watch.yaml)
- the largest single agent on Base or Solana
- x402 payment volume of the last 30 complete days, per chain (DefiLlama)
- the state of rules on agents at the CFTC (Federal Register, press feed) and
  the NY Gaming Commission (proposed-rules page)

and writes ~/Downloads/finance-watch-<YYYY-MM>.md with every value, its source
and the time it was read. One short line goes to STATS. A value over its
threshold, or a regulator text that names agents and has not been read yet,
goes to ALERTS as a message for Lars with a recommendation. Sending goes
through app.notify.

A source that cannot be read gives no value, never a zero: the report says
which source failed and the heartbeat status is `partial`.

  python scripts/finance_watch.py            the monthly run
  python scripts/finance_watch.py --dry-run  read and print, no file, no state, no message
  python scripts/finance_watch.py --pages-only   the pages the Mac delivered (ops FIN-NY)
  python scripts/finance_watch.py --check-inbox  read-only, for the invariant

The NY Gaming Commission refuses the server's address (HTTP 403 since
24.10.2026). The server does not request it and does not work around the
block: the Mac reads the page once a month from its own address and leaves it
in ~/finance-watch-inbox/ (INBOX).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from defusedxml import ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIG = ROOT / "config" / "finance_watch.yaml"
STATE = ROOT / "data" / "finance_watch_state.json"
HEARTBEAT = ROOT / "data" / "finance_watch_heartbeat.json"
DOWNLOADS = Path(os.path.expanduser("~")) / "Downloads"
# Pages the server may not read itself land here, written by the Mac
# (moltrust-security ops/fin-ny-abruf.sh, ops FIN-NY-MAC). One JSON file per
# page: url, fetched_at, http_status, html.
INBOX = Path(os.path.expanduser("~")) / "finance-watch-inbox"
# The Mac reads once a month and retries daily; a copy older than this means
# a month was missed.
INBOX_MAX_DAYS = 40

PROTOCOLS_URL = "https://api.llama.fi/protocols"
X402_URL = "https://api.llama.fi/summary/dexs/x402?dataType=dailyVolume"
FR_URL = "https://www.federalregister.gov/api/v1/documents.json"
FR_TYPES = {"Rule": "RULE", "Proposed Rule": "PRORULE"}
UA = "moltrust-finance-watch/1 (+https://moltrust.ch)"
DAY = 86400
# Protocols below this stay in the sum but get no row of their own.
SHOW_FROM_USD = 10_000

# Wording that names agents as actors. The Federal Register's own term search
# matches the words apart ("AI" anywhere, "agent" anywhere), so its hits are
# read again against this before anything counts.
AGENT_WORDING = re.compile(
    r"\b(?:AI[- ]agents?|autonomous agents?|trading agents?|agentic)\b", re.I)

MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


# ------------------------------------------------------------------ reading

def _get(url: str, params: dict | None = None, timeout: int = 60) -> bytes:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - fixed https URLs
        return r.read()


def fetch_json(url: str, params: dict | None = None):
    return json.loads(_get(url, params))


def fetch_text(url: str) -> str:
    return _get(url).decode("utf-8", "replace")


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or CONFIG).read_text(encoding="utf-8"))


def load_state(path: Path | None = None) -> dict:
    try:
        return json.loads((path or STATE).read_text())
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


# ---------------------------------------------------------------- the values

def _chain_tvl(p: dict, chain: str) -> float:
    v = (p.get("chainTvls") or {}).get(chain)
    return float(v) if isinstance(v, (int, float)) else 0.0


def agent_tvl(protocols: list, cfg: dict) -> dict:
    """Agent TVL per chain and together, the members, and the largest agent."""
    chains = cfg["chains"]
    by_slug = {p.get("slug"): p for p in protocols}
    members = {p["slug"]: "Kategorie" for p in protocols
               if p.get("category") == cfg["category"] and p.get("slug")}
    missing = []
    for e in cfg.get("extra_agents") or []:
        if e["slug"] in by_slug:
            members.setdefault(e["slug"], "Zusatzliste")
        else:
            missing.append(e["slug"])
    rows = []
    for slug, origin in members.items():
        p = by_slug[slug]
        per = {c: _chain_tvl(p, c) for c in chains}
        rows.append({"slug": slug, "name": p.get("name", slug), "origin": origin,
                     "per_chain": per, "total": sum(per.values())})
    rows.sort(key=lambda r: (-r["total"], r["slug"]))
    per_chain = {c: sum(r["per_chain"][c] for r in rows) for c in chains}
    return {"per_chain": per_chain, "total": sum(per_chain.values()),
            "members": rows, "missing": missing,
            "largest": rows[0] if rows and rows[0]["total"] > 0 else None}


def autonomous_vaults(protocols: list, cfg: dict) -> list:
    by_slug = {p.get("slug"): p for p in protocols}
    out = []
    for e in cfg.get("autonomous_vaults") or []:
        p = by_slug.get(e["slug"])
        if p is None:
            out.append({"slug": e["slug"], "name": e["slug"], "per_chain": None})
            continue
        out.append({"slug": e["slug"], "name": p.get("name", e["slug"]),
                    "per_chain": {c: _chain_tvl(p, c) for c in cfg["chains"]}})
    return out


def x402_volume(summary: dict, now: dt.datetime, chains: list) -> dict:
    """Sum of the last 30 complete UTC days, per chain and over all chains."""
    today = int(now.timestamp()) // DAY * DAY
    start = today - 30 * DAY
    days = [e for e in summary.get("totalDataChartBreakdown") or []
            if start <= int(e[0]) < today]
    if not days:
        raise ValueError("no daily x402 volume in the last 30 days")

    def vol(entry, chain):
        return float((entry.get(chain) or {}).get("x402", 0) or 0)

    return {"per_chain": {c: sum(vol(e[1], c) for e in days) for c in chains},
            "all_chains": sum(vol(e[1], c) for e in days for c in e[1]),
            "days": len(days),
            "from": dt.datetime.fromtimestamp(start, dt.timezone.utc).date().isoformat(),
            "until": dt.datetime.fromtimestamp(today - DAY, dt.timezone.utc).date().isoformat(),
            "reported_total30d": summary.get("total30d")}


# ---------------------------------------------------------------- regulators

def page_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def federal_register(cfg: dict, since: str, fetch_json_=None, fetch_text_=None) -> dict:
    """CFTC rules and proposed rules since `since` that are not read yet."""
    fetch_json_ = fetch_json_ or fetch_json
    fetch_text_ = fetch_text_ or fetch_text
    fr = cfg["regulation"]["federal_register"]
    reviewed = {d["number"] for d in cfg["regulation"].get("reviewed_documents") or []}
    seen: dict = {}
    for term in fr["terms"]:
        got = fetch_json_(FR_URL, {
            "conditions[agencies][]": fr["agency"],
            # Unquoted: the API finds quoted phrases unreliably ("AI agent" in
            # quotes gave 0 where the words gave 2, 24.10.2026). AGENT_WORDING
            # narrows the wide match down afterwards.
            "conditions[term]": term,
            "conditions[type][]": [FR_TYPES[t] for t in fr["types"]],
            "conditions[publication_date][gte]": since,
            "per_page": 100,
            "fields[]": ["document_number", "title", "type", "publication_date",
                         "html_url", "raw_text_url"],
        })
        for d in got.get("results") or []:
            seen.setdefault(d["document_number"], d)
    new = []
    for number, d in sorted(seen.items()):
        if number in reviewed:
            continue
        hits = len(AGENT_WORDING.findall(fetch_text_(d["raw_text_url"])))
        new.append({"number": number, "title": d["title"], "type": d["type"],
                    "date": d["publication_date"], "url": d["html_url"], "hits": hits})
    return {"since": since, "matched": len(seen), "new": new}


NEGATED = re.compile(r"\b(?:no|not|without|never|non)[- ]+\w+", re.I)


def read_inbox(page: dict, now: dt.datetime, inbox: Path | None = None) -> dict:
    """The Mac's copy of a page, or ValueError saying why it cannot be used."""
    path = (inbox or INBOX) / f"{page['inbox']}.json"
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"keine Ablage vom Mac ({path})") from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"Ablage vom Mac nicht lesbar ({_err(exc)})") from None
    if got.get("url") != page["url"]:
        raise ValueError(f"Ablage vom Mac nennt eine andere Adresse ({got.get('url')})")
    if got.get("http_status") != 200:
        raise ValueError(f"Abruf vom Mac ergab HTTP {got.get('http_status')}")
    fetched = dt.datetime.fromisoformat(str(got.get("fetched_at")).replace("Z", "+00:00"))
    age = (now - fetched).days
    if age > INBOX_MAX_DAYS:
        raise ValueError(f"Ablage vom Mac ist {age} Tage alt (gelesen {fetched:%d.%m.%Y})")
    return {**got, "fetched": fetched}


def page_documents(raw: str, pattern: str) -> list:
    return sorted({html.unescape(t).strip() for t in re.findall(pattern, raw)})


def _watch_inbox_page(page: dict, prev: dict | None, now: dt.datetime,
                      inbox: Path | None) -> dict:
    try:
        got = read_inbox(page, now, inbox)
    except ValueError as exc:
        return {**page, "error": str(exc)}
    docs = page_documents(got["html"], page["document_title_regex"])
    if not docs:
        # Read but understood nothing: the pattern no longer fits the page.
        return {**page, "error": "Ablage vom Mac gelesen, aber kein Dokument erkannt"}
    text = page_text(got["html"])
    first = prev is None or "documents" not in prev
    new = [] if first else [d for d in docs if d not in prev["documents"]]
    terms = re.compile(r"\b(?:" + "|".join(page.get("alert_terms") or []) + r")\b", re.I)
    flagged = [d for d in new if page.get("alert_terms") and terms.search(NEGATED.sub(" ", d))]
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {**page, "via": "mac", "fetched_at": got["fetched"], "hash": digest,
            "hits": len(AGENT_WORDING.findall(text)), "documents": docs,
            "new_documents": new, "flagged_documents": flagged, "first": first,
            "changed": bool(new),
            "since": prev["since"] if prev and not new and not first
            else now.isoformat(timespec="seconds")}


def watch_pages(cfg: dict, state: dict, now: dt.datetime, fetch_text_=None,
                inbox: Path | None = None) -> list:
    fetch_text_ = fetch_text_ or fetch_text
    out = []
    for page in cfg["regulation"].get("pages") or []:
        prev = (state.get("pages") or {}).get(page["url"])
        if page.get("inbox"):
            # Never requested from here: the server's address is refused, and
            # the refusal is not worked around. The Mac reads it from its own.
            out.append(_watch_inbox_page(page, prev, now, inbox))
            continue
        try:
            text = page_text(fetch_text_(page["url"]))
        except urllib.error.HTTPError as exc:
            # A refusal the config already names is a known state, not a run
            # fault. It is reported every month and never worked around.
            if exc.code == 403 and page.get("blocked_from_server"):
                out.append({**page, "blocked": True})
            else:
                out.append({**page, "error": _err(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - a failed read is reported, not raised
            out.append({**page, "error": _err(exc)})
            continue
        digest = hashlib.sha256(text.encode()).hexdigest()
        out.append({**page, "hash": digest, "hits": len(AGENT_WORDING.findall(text)),
                    "first": prev is None,
                    "changed": prev is not None and prev["hash"] != digest,
                    "since": prev["since"] if prev and prev["hash"] == digest
                    else now.isoformat(timespec="seconds")})
    return out


def watch_feeds(cfg: dict, fetch_text_=None) -> list:
    fetch_text_ = fetch_text_ or fetch_text
    out = []
    for feed in cfg["regulation"].get("feeds") or []:
        try:
            root = ET.fromstring(fetch_text_(feed["url"]))
        except Exception as exc:  # noqa: BLE001
            out.append({**feed, "error": _err(exc)})
            continue
        items = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            text = title + " " + page_text(it.findtext("description") or "")
            if AGENT_WORDING.search(text):
                items.append({"title": title, "link": (it.findtext("link") or "").strip(),
                              "date": (it.findtext("pubDate") or "").strip()})
        out.append({**feed, "items": items})
    return out


# ------------------------------------------------------------------ triggers

def triggers(cfg: dict, tvl: dict | None, fr: dict | None, pages: list) -> list:
    """Every trigger as {kind, text}: the text is the paragraph for Lars."""
    th = cfg["thresholds"]
    out = []
    if tvl is not None and tvl["total"] >= th["agent_tvl_usd"]:
        out.append({"kind": "tvl", "text": (
            f"KI-Agenten verwalten auf Base und Solana zusammen {mio(tvl['total'])} "
            f"(Base {mio(tvl['per_chain'].get('Base', 0))}, Solana "
            f"{mio(tvl['per_chain'].get('Solana', 0))}). Die Schwelle ist "
            f"{mio(th['agent_tvl_usd'])}.\n"
            "Empfehlung: die Vault-Demo auf Solana Devnet als Job freigeben (der Plan "
            "steht in der README der Demo) und den Abschnitt MolTrust Finance im "
            "Business Case nachrechnen lassen.")})
    big = tvl and tvl["largest"]
    if big and big["total"] >= th["agent_aum_usd"]:
        out.append({"kind": "aum", "text": (
            f"Ein einzelner Agent, {big['name']}, verwaltet auf Base und Solana "
            f"{mio(big['total'])}. Die Schwelle ist {mio(th['agent_aum_usd'])}.\n"
            f"Empfehlung: prüfen lassen, ob {big['name']} Mandate mit prüfbaren Limits "
            "braucht. Eine Ansprache geht vorher als Entwurf an dich.")})
    reg = cfg["regulation"]
    if reg.get("agent_limits_rule"):
        out.append({"kind": "rule", "text": (
            f"Eine Wettaufsicht verlangt prüfbare Limits für Agenten (Stand "
            f"{reg['reviewed']}): {reg['summary'].strip()}\n"
            "Empfehlung: die MoltGuard-Mandate gegen den Regeltext abgleichen lassen.")})
    for d in (fr or {}).get("new") or []:
        if d["hits"]:
            out.append({"kind": "rule-candidate", "text": (
                f"Die CFTC hat am {d['date']} „{d['title']}“ veröffentlicht "
                f"({d['type']}), der Text nennt Agenten {d['hits']}-mal. Ob er Limits für "
                f"Agenten verlangt, ist noch nicht gelesen. {d['url']}\n"
                "Empfehlung: den Text lesen lassen. Verlangt er prüfbare Limits, den Stand "
                "in config/finance_watch.yaml auf ja setzen; sonst als gelesen eintragen.")})
    for p in pages:
        if p.get("via") == "mac":
            for d in p.get("flagged_documents") or []:
                out.append({"kind": "rule-candidate", "text": (
                    f"Die Seite „{p['name']}“ führt einen neuen Entwurf: „{d}“. "
                    f"Vom Mac gelesen am {p['fetched_at']:%d.%m.%Y}. Ob er prüfbare "
                    f"Limits für Agenten verlangt, ist noch nicht gelesen. {p['url']}\n"
                    "Empfehlung: den Entwurf lesen lassen. Verlangt er prüfbare Limits, "
                    "den Stand in config/finance_watch.yaml auf ja setzen.")})
            continue
        if p.get("changed") and p.get("hits"):
            out.append({"kind": "rule-candidate", "text": (
                f"Die Seite „{p['name']}“ hat sich geändert und nennt Agenten "
                f"{p['hits']}-mal. {p['url']}\n"
                "Empfehlung: die Änderung lesen lassen, ob sie Limits für Agenten verlangt.")})
    return out


# ------------------------------------------------------------------- wording

def mio(usd: float) -> str:
    return f"{usd / 1e6:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".") + " Mio. USD"


def stamp(t: dt.datetime) -> str:
    return t.strftime("%d.%m.%Y %H:%M UTC")


def month_name(now: dt.datetime) -> str:
    return f"{MONTHS[now.month - 1]} {now.year}"


def report(now, cfg, tvl, vaults, x402, fr, pages, feeds, found, errors) -> str:
    th, reg = cfg["thresholds"], cfg["regulation"]
    read = stamp(now)
    L = [f"# Finance-Auslöser, Monatsbericht {month_name(now)}", "",
         f"Abgerufen {read}. Alle Werte stammen aus freien Quellen ohne Schlüssel; "
         "jede Zeile nennt ihre Quelle.", ""]
    if errors:
        L += ["Nicht abrufbar in diesem Lauf (kein Wert, keine Null):", ""]
        L += [f"- {e}" for e in errors] + [""]

    L += ["## Auslöser", "",
          "| Auslöser | Stand | Schwelle | erreicht |", "|---|---|---|---|"]
    if tvl is not None:
        L.append(f"| Kapital von KI-Agenten auf Base und Solana | {mio(tvl['total'])} | "
                 f"{mio(th['agent_tvl_usd'])} | {'ja' if tvl['total'] >= th['agent_tvl_usd'] else 'nein'} |")
        big = tvl["largest"]
        L.append("| Größtes Kapital eines einzelnen Agenten, Base oder Solana | "
                 + (f"{mio(big['total'])} ({big['name']})" if big else "kein Agent mit Kapital")
                 + f" | {mio(th['agent_aum_usd'])} | "
                 + ("ja" if big and big["total"] >= th["agent_aum_usd"] else "nein") + " |")
    else:
        L.append("| Kapital von KI-Agenten auf Base und Solana | nicht abrufbar | "
                 f"{mio(th['agent_tvl_usd'])} | unbekannt |")
        L.append("| Größtes Kapital eines einzelnen Agenten | nicht abrufbar | "
                 f"{mio(th['agent_aum_usd'])} | unbekannt |")
    cand = [t for t in found if t["kind"] == "rule-candidate"]
    rule_state = ("ja" if reg.get("agent_limits_rule") else
                  "zu lesen" if cand else "nein")
    L += [f"| Regel einer Wettaufsicht zu prüfbaren Agenten-Limits | Stand geprüft "
          f"{reg['reviewed']} | eine solche Regel | {rule_state} |", ""]
    L += [f"{len(found)} Auslöser gemeldet, die Meldung ging an Lars." if found else
          "Kein Auslöser erreicht, keine Meldung.", ""]

    L += ["## Kapital von KI-Agenten (TVL)", "",
          "TVL heißt das Kapital, das Nutzer in einem Protokoll hinterlegt haben. Gezählt "
          f"werden die Protokolle der DefiLlama-Kategorie „{cfg['category']}“ und die "
          "Zusatzliste in `config/finance_watch.yaml`.", ""]
    if tvl is not None:
        src = f"DefiLlama {PROTOCOLS_URL}"
        L += ["| Chain | Wert | Quelle | abgerufen |", "|---|---|---|---|"]
        for c in cfg["chains"]:
            L.append(f"| {c} | {mio(tvl['per_chain'][c])} | {src} | {read} |")
        L += [f"| zusammen | {mio(tvl['total'])} | Summe der Zeilen darüber | {read} |", ""]
        shown = [r for r in tvl["members"] if r["total"] >= SHOW_FROM_USD]
        L += [f"{len(tvl['members'])} Protokolle gezählt, {len(shown)} davon mit mindestens "
              f"{mio(SHOW_FROM_USD)} auf Base oder Solana:", "",
              "| Protokoll | " + " | ".join(cfg["chains"]) + " | Herkunft |",
              "|---|" + "---|" * len(cfg["chains"]) + "---|"]
        for r in shown:
            L.append(f"| {r['name']} | " + " | ".join(mio(r["per_chain"][c]) for c in cfg["chains"])
                     + f" | {r['origin']} |")
        L.append("")
        for slug in tvl["missing"]:
            L += [f"Der Eintrag `{slug}` der Zusatzliste fehlt bei DefiLlama und ist nicht "
                  "gezählt.", ""]
    if vaults:
        L += ["Autonome Vaults ohne KI, nicht mitgezählt:", ""]
        for v in vaults:
            if v["per_chain"] is None:
                L.append(f"- {v['slug']}: bei DefiLlama nicht gefunden")
            else:
                L.append(f"- {v['name']}: " + ", ".join(
                    f"{c} {mio(v['per_chain'][c])}" for c in cfg["chains"])
                    + f" (DefiLlama, abgerufen {read})")
        L.append("")
    L += ["Nicht erhoben:", ""]
    L += [f"- {n['name']}: nicht erhoben, {n['why']}" for n in cfg.get("not_collected") or []]
    L.append("")

    L += ["## Größtes Kapital eines einzelnen Agenten", ""]
    if tvl is not None and tvl["largest"]:
        b = tvl["largest"]
        L += [f"{b['name']} verwaltet auf Base und Solana {mio(b['total'])} ("
              + ", ".join(f"{c} {mio(b['per_chain'][c])}" for c in cfg["chains"])
              + f"). Quelle DefiLlama {PROTOCOLS_URL}, abgerufen {read}.", ""]
    else:
        L += ["Kein Wert in diesem Lauf.", ""]

    L += ["## x402-Zahlungen der letzten 30 Tage", "",
          "x402 ist ein Zahlungsweg, über den Agenten kleine USDC-Beträge für einzelne "
          "Abrufe zahlen.", ""]
    if x402 is not None:
        src = f"DefiLlama {X402_URL.split('?')[0]}"
        L += [f"Zeitraum {x402['from']} bis {x402['until']}, {x402['days']} volle Tage.", "",
              "| Chain | Volumen | Quelle | abgerufen |", "|---|---|---|---|"]
        for c in cfg["chains"]:
            L.append(f"| {c} | {mio(x402['per_chain'][c])} | {src} | {read} |")
        L += [f"| alle Chains | {mio(x402['all_chains'])} | {src} | {read} |", ""]
        for pc in cfg.get("press_comparisons") or []:
            if pc["metric"] == "x402_volume_30d":
                L += [f"Zum Vergleich nennt {pc['outlet']} am {pc['published']} "
                      f"{pc['figure'].strip()} ({pc['url']}). Der eigene Wert oben stammt "
                      "von DefiLlama und liegt weit darunter; woher der Unterschied kommt, "
                      "ist nicht geklärt.", ""]
    else:
        L += ["Kein Wert in diesem Lauf.", ""]

    L += ["## Regeln der Wettaufsichten", "",
          f"Stand geprüft am {reg['reviewed']}: {reg['summary'].strip()}", ""]
    if reg.get("reviewed_documents"):
        L += ["Bereits gelesen:", ""]
        L += [f"- {d['number']} „{d['title']}“: {d['verdict'].strip()}"
              for d in reg["reviewed_documents"]]
        L.append("")
    if fr is not None:
        fresh = [d for d in fr["new"] if d["hits"]]
        loose = [d for d in fr["new"] if not d["hits"]]
        L.append(f"Federal Register, CFTC, Regeln und Entwürfe seit {fr['since']}: "
                 f"{fr['matched']} Treffer der Suche, {len(fresh)} neu mit Agenten im Text.")
        L += [f"- {d['date']} {d['type']} „{d['title']}“, {d['hits']} Stellen, {d['url']}"
              for d in fresh]
        if loose:
            L.append(f"{len(loose)} weitere neue Treffer nennen Agenten nicht wörtlich: "
                     + "; ".join(f"{d['number']} „{d['title']}“" for d in loose))
        L.append("")
    L += page_lines(pages, reg)
    for f in feeds:
        if f.get("error"):
            L.append(f"- {f['name']}: nicht abrufbar ({f['error']})")
        elif f["items"]:
            L.append(f"- {f['name']}: {len(f['items'])} Meldungen nennen Agenten:")
            L += [f"  - {i['date']}: {i['title']} {i['link']}" for i in f["items"]]
        else:
            L.append(f"- {f['name']}: keine aktuelle Meldung nennt Agenten, {f['url']}")
    L.append("")
    return "\n".join(L)


def page_lines(pages: list, reg: dict) -> list:
    L = []
    for p in pages:
        if p.get("error"):
            L.append(f"- {p['name']}: nicht abrufbar ({p['error']})")
        elif p.get("via") == "mac":
            head = (f"- {p['name']}: vom Mac gelesen am {p['fetched_at']:%d.%m.%Y}, "
                    f"{len(p['documents'])} Dokumente")
            if p["first"]:
                L.append(f"{head}, Stand aufgenommen, {p['url']}")
            elif p["new_documents"]:
                L.append(f"{head}, neu seit dem letzten Lauf: "
                         + "; ".join(f"„{d}“" for d in p["new_documents"]) + f", {p['url']}")
                if p["flagged_documents"]:
                    L.append("  Zur Durchsicht gemeldet: "
                             + "; ".join(f"„{d}“" for d in p["flagged_documents"]))
            else:
                L.append(f"{head}, keins neu seit {p['since'][:10]}, {p['url']}")
        elif p.get("blocked"):
            L.append(f"- {p['name']}: vom Server aus nicht lesbar, "
                     f"{p['blocked_from_server'].strip()} Zuletzt von Hand gelesen am "
                     f"{reg['reviewed']}; die Durchsicht bleibt Handarbeit: {p['url']}")
        elif p["first"]:
            L.append(f"- {p['name']}: Stand aufgenommen ({p['hits']} Stellen mit Agenten), "
                     f"{p['url']}")
        elif p["changed"]:
            L.append(f"- {p['name']}: geändert seit dem letzten Lauf, {p['hits']} Stellen mit "
                     f"Agenten, {p['url']}")
        else:
            L.append(f"- {p['name']}: unverändert seit {p['since'][:10]}, {p['url']}")
    return L


def stats_line(now, cfg, tvl, found, path: Path) -> str:
    th = cfg["thresholds"]
    head = f"Finance-Auslöser {month_name(now)}: "
    head += "Auslöser erreicht, Meldung auf ALERTS. " if found else "nichts erreicht. "
    if tvl is None:
        return head + f"Agenten-Kapital nicht abrufbar. Bericht {path}"
    big = tvl["largest"]
    return (head + f"Agenten-Kapital auf Base und Solana {mio(tvl['total'])}, Schwelle "
            f"{mio(th['agent_tvl_usd'])}. Größter Agent {mio(big['total'] if big else 0)}, "
            f"Schwelle {mio(th['agent_aum_usd'])}. Bericht {path}")


def alert_text(now, found, path: Path) -> str:
    return (f"Finance-Auslöser erreicht (Monatsbericht {month_name(now)})\n\n"
            + "\n\n".join(t["text"] for t in found)
            + f"\n\nBericht: {path} auf dem Server.")


# ---------------------------------------------------------------------- run

def heartbeat(status: str, detail: str, now: dt.datetime) -> None:
    write_json(HEARTBEAT, {"timestamp": now.isoformat(timespec="seconds"),
                           "status": status, "detail": detail})
    print(f"finance watch: {status}: {detail}")


def page_state(pages: list, state: dict) -> dict:
    out = dict(state.get("pages") or {})
    for p in pages:
        if not p.get("error") and not p.get("blocked"):
            out[p["url"]] = {"hash": p["hash"], "since": p["since"]}
            if p.get("via") == "mac":
                out[p["url"]]["documents"] = p["documents"]
    return out


def pages_only(cfg: dict, state: dict, now: dt.datetime, dry_run: bool) -> int:
    """Only the pages the Mac delivers: state, a note in this month's report, ALERTS.

    Called by the Mac right after it has written the inbox (ops FIN-NY-MAC). The
    monthly run reads the same inbox, so whichever comes first records the state
    and the other finds nothing new: no message is sent twice.
    """
    cfg = {**cfg, "regulation": {**cfg["regulation"], "pages": [
        p for p in cfg["regulation"].get("pages") or [] if p.get("inbox")]}}
    pages = watch_pages(cfg, state, now)
    found = [t for t in triggers(cfg, None, None, pages) if t["kind"] == "rule-candidate"]
    path = DOWNLOADS / f"finance-watch-{now:%Y-%m}.md"
    note = [f"## Nachtrag {stamp(now)}: Seiten, die der Mac liest", ""]
    note += page_lines(pages, cfg["regulation"]) + [""]
    note += [f"{len(found)} Entwurf/Entwürfe zur Durchsicht gemeldet, die Meldung ging an Lars."
             if found else "Kein neuer Entwurf zur Durchsicht, keine Meldung.", ""]
    errors = [p for p in pages if p.get("error")]
    print("\n".join(note))
    if dry_run:
        if found:
            print("--- ALERTS ---\n" + alert_text(now, found, path))
        return 1 if errors else 0
    write_json(STATE, {**state, "pages": page_state(pages, state)})
    if path.exists():
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(note))
        print(f"report: note appended to {path}")
    else:
        print(f"report: {path} does not exist yet; the monthly run will include this")
    if found:
        from app import notify
        notify.send_telegram(alert_text(now, found, path), channel=notify.ALERTS)
    return 1 if errors else 0


def check_inbox(cfg: dict, now: dt.datetime) -> int:
    """For the invariant: one line per Mac page, last line the number not usable."""
    bad = 0
    for page in cfg["regulation"].get("pages") or []:
        if not page.get("inbox"):
            continue
        try:
            got = read_inbox(page, now)
            print(f"  {page['inbox']}: gelesen {got['fetched']:%Y-%m-%dT%H:%MZ}")
        except ValueError as exc:
            bad += 1
            print(f"  {page['inbox']}: {exc}")
    print(bad)
    return 0


def main(argv=None, now: dt.datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="read and print, no file, no state, no message")
    ap.add_argument("--pages-only", action="store_true",
                    help="only the pages the Mac delivers to the inbox")
    ap.add_argument("--check-inbox", action="store_true",
                    help="read-only, for the invariant: Mac pages missing or stale")
    args = ap.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    cfg = load_config()
    if args.check_inbox:
        return check_inbox(cfg, now)
    state = load_state()
    if args.pages_only:
        return pages_only(cfg, state, now, args.dry_run)
    errors: list = []

    tvl = vaults = x402 = fr = None
    try:
        protocols = fetch_json(PROTOCOLS_URL)
        tvl = agent_tvl(protocols, cfg)
        vaults = autonomous_vaults(protocols, cfg)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"DefiLlama Protokolle: {_err(exc)}")
    try:
        x402 = x402_volume(fetch_json(X402_URL), now, cfg["chains"])
    except Exception as exc:  # noqa: BLE001
        errors.append(f"DefiLlama x402: {_err(exc)}")
    since = state.get("fr_checked_until") or cfg["regulation"]["reviewed"]
    try:
        fr = federal_register(cfg, since)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Federal Register: {_err(exc)}")
    pages = watch_pages(cfg, state, now)
    feeds = watch_feeds(cfg)
    errors += [f"{x['name']}: {x['error']}" for x in pages + feeds if x.get("error")]

    found = triggers(cfg, tvl, fr, pages)
    path = DOWNLOADS / f"finance-watch-{now:%Y-%m}.md"
    text = report(now, cfg, tvl, vaults, x402, fr, pages, feeds, found, errors)

    if args.dry_run:
        print(text)
        if found:
            print("--- ALERTS ---\n" + alert_text(now, found, path))
        print("--- STATS ---\n" + stats_line(now, cfg, tvl, found, path))
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    new_state = {"last_run": now.isoformat(timespec="seconds"),
                 "fr_checked_until": now.date().isoformat() if fr is not None else since,
                 "pages": page_state(pages, state)}
    write_json(STATE, new_state)

    from app import notify
    if found:
        notify.send_telegram(alert_text(now, found, path), channel=notify.ALERTS)
    notify.send_telegram(stats_line(now, cfg, tvl, found, path), channel=notify.STATS)

    status = "partial" if errors else ("alert" if found else "ok")
    detail = (f"report {path}; agent TVL Base+Solana "
              + (f"{tvl['total']:.0f}" if tvl else "n/a")
              + f" USD; {len(found)} trigger(s); {len(errors)} source error(s)")
    heartbeat(status, detail, now)
    return 1 if errors and tvl is None and x402 is None else 0


if __name__ == "__main__":
    raise SystemExit(main())
