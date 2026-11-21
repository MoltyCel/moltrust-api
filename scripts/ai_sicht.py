"""Visibility in AI answers: the same questions to every model, answers kept verbatim (ops AI-SICHT).

Lars, 21.11.2026: ask ten assistants the same fixed questions every four
weeks, keep each answer word for word with date and model version, measure
both the model on its own and the model with web search, and record whether
MolTrust is named, where, with which source, and who is named before it.

What runs here are the models with an API key on the server. The browser-only
surfaces (Copilot, Meta AI and those without a key) are asked from a Console
tab through Claude in Chrome and imported with `--import`; their lines carry
`weg: "browser"`.

Every endpoint below was checked against the provider's primary docs on
21.11.2026 (CLAUDE.md "Fremde APIs"):
- Anthropic  POST https://api.anthropic.com/v1/messages, tool web_search_20260209
  (platform.claude.com/docs/en/about-claude/pricing, web search tool section)
- OpenAI     POST https://api.openai.com/v1/responses, tool {"type": "web_search"},
  citations as url_citation annotations
  (developers.openai.com/api/docs/guides/tools-web-search)
- Gemini     POST /v1beta/models/{model}:generateContent, tool googleSearch,
  candidates[].groundingMetadata.groundingChunks[].web.uri, modelVersion
  (aistudio.google.com/api-reference/generate-content)
- Mistral    POST https://api.mistral.ai/v1/conversations, tool {"type": "web_search"},
  sources as tool_reference chunks; web search is not offered on chat
  completions (docs.mistral.ai/agents/tools/built-in/websearch)
- Perplexity POST https://api.perplexity.ai/v1/sonar, `citations`,
  `search_results`; `disable_search` is the Sonar switch for a run without
  search (docs.perplexity.ai/docs/agent-api/migrate-from-sonar/how-to.md)

Cost: the run stops before a call that could take the total over the cap
(--deckel, 5 USD by default, AR9). Prices are list prices read on 21.11.2026;
where a provider publishes none (Mistral Medium, Mistral web search), a
deliberately high ceiling stands in, so the estimate errs upwards. Perplexity
returns its own cost per call, and that figure is used.

No Telegram: a run writes files only. Output per run date under
~/selftest/ai-sicht/<YYYY-MM-DD>/: antworten.jsonl (one line per answer),
auswertung.jsonl (one line per judged answer), bericht.md.
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_DIR = pathlib.Path(os.path.expanduser("~/selftest/ai-sicht"))
FACTS = [ROOT / "config" / "ai_sicht_fakten.md", ROOT / "agents" / "scout_facts.md"]

# Fixed wording and order (AR2, AR6). Changing a line breaks comparability
# with earlier runs; add a new id instead.
FRAGEN = [
    ("A1", "A", "Was ist MolTrust?"),
    ("A2", "A", "Wer ist Lars Kroehl?"),
    ("A3", "A", "Was ist der Agent Authorization Envelope?"),
    ("B1", "B", "Wie prüfe ich, wozu ein Agent befugt war?"),
    ("B2", "B", "Wie weise ich nach, dass ein Agent sein Mandat überschritten hat?"),
    ("B3", "B", "Welche Standards gibt es für Agenten-Autorisierung?"),
    ("B4", "B", "Wer haftet, wenn ein Agent falsch kauft?"),
    ("C1", "C", "Welche Anbieter gibt es für Agenten-Identität und Nachweisbarkeit?"),
    ("C2", "C", "ERC-8004 und Alternativen?"),
]
WEGE = ("ohne_suche", "mit_suche")

# Models, chosen as the closest API stand-in for each consumer app (resolved
# live on 21.11.2026: OpenAI lists `chat-latest`, Gemini `gemini-pro-latest`
# -> gemini-3.1-pro-preview, Mistral `mistral-medium-latest`).
MODELLE = {
    "chatgpt": {"key": "OPENAI_API_KEY", "model": "chat-latest"},
    "claude": {"key": "ANTHROPIC_API_KEY", "model": "claude-sonnet-5-5"},
    "gemini": {"key": "GEMINI_API_KEY", "model": "gemini-pro-latest"},
    "perplexity": {"key": "PERPLEXITY_API_KEY", "model": "sonar-pro"},
    "mistral": {"key": "MISTRAL_API_KEY", "model": "mistral-medium-latest"},
}
# AR1 models that have an API but no key on the server. Named, not procured (AR7).
OHNE_SCHLUESSEL_API = {"grok": "XAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "qwen": "DASHSCOPE_API_KEY"}
# AR1 surfaces without a public chat API for the consumer product.
NUR_BROWSER = ("copilot", "meta_ai")

# USD per million tokens (in, out) and per search. Sources and dates in the
# module docstring; `None` = the provider reports its own cost.
PREISE = {
    "chatgpt": (5.0, 30.0, 0.01),       # ceiling: OpenAI is out of credit, no call is made today
    "claude": (2.0, 10.0, 0.01),        # list price Sonnet 5.5; web search 10 USD / 1000
    "gemini": (2.0, 12.0, 0.014),       # list price 3.1 Pro <=200k; grounding 14 USD / 1000 queries
    "mistral": (2.0, 6.0, 0.03),        # ceiling: no published Medium price on mistral.ai/pricing
    "perplexity": None,
}
# Reserve held back before each call so that a single call cannot cross the cap.
RESERVE = {"ohne_suche": 0.05, "mit_suche": 0.25}
MAX_OUT = 3000
JUDGE_MODEL = "claude-haiku-5-5"       # 0.10 / 0.50 USD per MTok
JUDGE_PREIS = (0.10, 0.50)
SUMMARY_MODEL = "claude-sonnet-5-5"
TIMEOUT = 180

NAME_RE = re.compile(r"moltrust|kroehl|agent authorization envelope|draft-kroehl", re.I)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Budget:
    def __init__(self, deckel):
        self.deckel = deckel
        self.summe = 0.0

    def erlaubt(self, reserve):
        return self.summe + reserve <= self.deckel

    def buchen(self, usd):
        self.summe += usd or 0.0


def kosten(modell, tin, tout, suchen):
    p = PREISE.get(modell)
    if p is None:
        return None
    return tin / 1e6 * p[0] + tout / 1e6 * p[1] + suchen * p[2]


# --- provider calls -------------------------------------------------------
# Each returns dict(text, version, quellen, tin, tout, suchen, usd) or raises.

def _anthropic(model, frage, suche, key, http):
    body = {"model": model, "max_tokens": MAX_OUT, "messages": [{"role": "user", "content": frage}]}
    if suche:
        body["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
    r = http.post("https://api.anthropic.com/v1/messages", json=body, timeout=TIMEOUT,
                  headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    r.raise_for_status()
    j = r.json()
    text, quellen = [], []
    for b in j.get("content", []):
        if b.get("type") == "text":
            text.append(b.get("text", ""))
            for c in b.get("citations") or []:
                if c.get("url"):
                    quellen.append(c["url"])
        elif b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            quellen += [x.get("url") for x in b["content"] if x.get("url")]
    u = j.get("usage", {})
    tin = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
    suchen = (u.get("server_tool_use") or {}).get("web_search_requests", 0)
    return dict(text="".join(text), version=j.get("model"), quellen=_einmal(quellen),
                tin=tin, tout=u.get("output_tokens", 0), suchen=suchen)


def _openai(model, frage, suche, key, http):
    body = {"model": model, "input": frage, "max_output_tokens": MAX_OUT}
    if suche:
        body["tools"] = [{"type": "web_search"}]
    r = http.post("https://api.openai.com/v1/responses", json=body, timeout=TIMEOUT,
                  headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    j = r.json()
    text, quellen, suchen = [], [], 0
    for item in j.get("output", []):
        if item.get("type") == "web_search_call":
            suchen += 1
        for c in item.get("content") or []:
            if c.get("type") == "output_text":
                text.append(c.get("text", ""))
                quellen += [a.get("url") for a in c.get("annotations") or [] if a.get("url")]
    u = j.get("usage", {})
    return dict(text="".join(text), version=j.get("model"), quellen=_einmal(quellen),
                tin=u.get("input_tokens", 0), tout=u.get("output_tokens", 0), suchen=suchen)


def _gemini(model, frage, suche, key, http):
    body = {"contents": [{"parts": [{"text": frage}]}], "generationConfig": {"maxOutputTokens": MAX_OUT * 3}}
    if suche:
        body["tools"] = [{"googleSearch": {}}]
    r = http.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                  params={"key": key}, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    cand = (j.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
    gm = cand.get("groundingMetadata") or {}
    quellen = [((c.get("web") or {}).get("uri")) for c in gm.get("groundingChunks", [])]
    u = j.get("usageMetadata", {})
    tout = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    return dict(text=text, version=j.get("modelVersion"), quellen=_einmal(quellen),
                tin=u.get("promptTokenCount", 0), tout=tout, suchen=len(gm.get("webSearchQueries", [])))


def _mistral(model, frage, suche, key, http):
    h = {"Authorization": f"Bearer {key}"}
    if not suche:
        r = http.post("https://api.mistral.ai/v1/chat/completions", headers=h, timeout=TIMEOUT,
                      json={"model": model, "max_tokens": MAX_OUT, "messages": [{"role": "user", "content": frage}]})
        r.raise_for_status()
        j = r.json()
        u = j.get("usage", {})
        return dict(text=j["choices"][0]["message"]["content"], version=j.get("model"), quellen=[],
                    tin=u.get("prompt_tokens", 0), tout=u.get("completion_tokens", 0), suchen=0)
    r = http.post("https://api.mistral.ai/v1/conversations", headers=h, timeout=TIMEOUT,
                  json={"model": model, "inputs": frage, "tools": [{"type": "web_search"}], "store": False,
                        "completion_args": {"max_tokens": MAX_OUT}})
    r.raise_for_status()
    j = r.json()
    text, quellen, suchen, version = [], [], 0, None
    for o in j.get("outputs", []):
        if o.get("type") == "tool.execution":
            suchen += 1
        elif o.get("type") == "message.output":
            version = o.get("model") or version
            c = o.get("content")
            if isinstance(c, str):
                text.append(c)
            else:
                for ch in c or []:
                    if ch.get("type") == "text":
                        text.append(ch.get("text", ""))
                    elif ch.get("type") == "tool_reference" and ch.get("url"):
                        quellen.append(ch["url"])
    u = j.get("usage", {})
    return dict(text="".join(text), version=version, quellen=_einmal(quellen),
                tin=u.get("prompt_tokens", 0), tout=u.get("completion_tokens", 0), suchen=suchen)


def _perplexity(model, frage, suche, key, http):
    body = {"model": model, "messages": [{"role": "user", "content": frage}], "max_tokens": MAX_OUT}
    if not suche:
        body["disable_search"] = True
    r = http.post("https://api.perplexity.ai/v1/sonar", json=body, timeout=TIMEOUT,
                  headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    j = r.json()
    u = j.get("usage", {})
    quellen = list(j.get("citations") or []) + [s.get("url") for s in j.get("search_results") or []]
    usd = (u.get("cost") or {}).get("total_cost")
    return dict(text=j["choices"][0]["message"]["content"], version=j.get("model"), quellen=_einmal(quellen),
                tin=u.get("prompt_tokens", 0), tout=u.get("completion_tokens", 0),
                suchen=0 if not suche else 1, usd=usd)


AUFRUF = {"claude": _anthropic, "chatgpt": _openai, "gemini": _gemini,
          "mistral": _mistral, "perplexity": _perplexity}


def _einmal(xs):
    seen, out = set(), []
    for x in xs:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def schluessel_stand(env=os.environ):
    """(present, missing) model names, by key name only; values are never read out."""
    da = [m for m, c in MODELLE.items() if env.get(c["key"])]
    fehlt = [m for m, c in MODELLE.items() if not env.get(c["key"])]
    fehlt += [m for m, k in OHNE_SCHLUESSEL_API.items() if not env.get(k)]
    return da, fehlt


def befragen(modelle, budget, env=os.environ, http=None, aufruf=AUFRUF, schlaf=0.5):
    """One line per (question, model, way), in the fixed order. Never raises for a provider error."""
    http = http or httpx.Client()
    zeilen, gestoppt, gesperrt = [], False, {}
    for fid, gruppe, frage in FRAGEN:
        for weg in WEGE:
            for m in modelle:
                cfg = MODELLE[m]
                z = {"ts": now_iso(), "frage_id": fid, "gruppe": gruppe, "frage": frage,
                     "modell": m, "angefragt": cfg["model"], "weg": weg, "kanal": "api"}
                if m in gesperrt:
                    z["fehler"] = f"nicht angefragt: {gesperrt[m]}"
                    zeilen.append(z)
                    continue
                if gestoppt or not budget.erlaubt(RESERVE[weg]):
                    gestoppt = True
                    z["fehler"] = f"kostendeckel {budget.deckel:.2f} USD erreicht"
                    zeilen.append(z)
                    continue
                try:
                    res = aufruf[m](cfg["model"], frage, weg == "mit_suche", env[cfg["key"]], http)
                except httpx.HTTPStatusError as e:
                    z["fehler"] = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                    # No credit or a rejected key will not change within the run.
                    if e.response.status_code in (401, 403) or "insufficient_quota" in e.response.text:
                        gesperrt[m] = f"HTTP {e.response.status_code} bei {fid} {weg}"
                    zeilen.append(z)
                    continue
                except (httpx.HTTPError, KeyError, ValueError) as e:
                    z["fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
                    zeilen.append(z)
                    continue
                usd = res.pop("usd", None)
                if usd is None:
                    usd = kosten(m, res["tin"], res["tout"], res["suchen"])
                budget.buchen(usd)
                z.update(res, usd=round(usd or 0, 5))
                zeilen.append(z)
                time.sleep(schlaf)
    return zeilen


# --- judging ----------------------------------------------------------------

def fakten():
    out = []
    for p in FACTS:
        t = p.read_text(encoding="utf-8")
        i = t.find("\n## ")
        if i < 0:
            raise ValueError(f"{p}: no '## ' section")
        out.append(t[i + 1:].strip())
    return "\n\n".join(out)


def position(text):
    """Offset of the first own name as a share of the text (0.0 = first word), or None."""
    m = NAME_RE.search(text or "")
    if not m:
        return None
    return round(m.start() / max(len(text), 1), 3)


JUDGE_PROMPT = """You judge one answer an AI assistant gave to a fixed question. Facts about MolTrust:

<facts>
{fakten}
</facts>

Question ({gruppe}): {frage}
Sources the assistant attached: {quellen}

<answer>
{text}
</answer>

Return only a JSON object with these keys:
- "genannt": true if the answer names MolTrust, Lars Kroehl or the Agent Authorization Envelope (AAE) as MolTrust's, else false.
- "stelle": where the first such mention stands, e.g. "first of 6 providers listed", "paragraph 3 of 5", or null.
- "quelle": the URL from the attached sources that the mention rests on, or null if none.
- "namen_davor": list of other providers, standards or products named before the first mention (all named ones if not mentioned), in order, at most 15.
- "urteil": for group A one of "richtig", "teilweise", "veraltet", "erfunden", "nicht_genannt"; for groups B and C null.
- "begruendung": one sentence in German naming the decisive statement, for group A the wrong or outdated detail if any.
"""


def _judge_json(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("judge returned no JSON object")
    j = json.loads(m.group(0))
    for k in ("genannt", "stelle", "quelle", "namen_davor", "urteil", "begruendung"):
        j.setdefault(k, None)
    if not isinstance(j["namen_davor"], list):
        j["namen_davor"] = []
    return j


def _claude_text(prompt, model, key, http, max_tokens=800):
    r = http.post("https://api.anthropic.com/v1/messages", timeout=TIMEOUT,
                  headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                  json={"model": model, "max_tokens": max_tokens,
                        "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    j = r.json()
    u = j.get("usage", {})
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text"), u


def bewerten(zeilen, budget, key, http=None):
    http = http or httpx.Client()
    f = fakten()
    out = []
    for z in zeilen:
        if z.get("fehler") or not z.get("text"):
            continue
        e = {k: z[k] for k in ("frage_id", "gruppe", "modell", "weg", "kanal")}
        e["version"] = z.get("version")
        e["position"] = position(z["text"])
        e["genannt_wortlaut"] = e["position"] is not None
        if not budget.erlaubt(0.01):
            e["fehler"] = "kostendeckel vor der Bewertung erreicht"
            out.append(e)
            continue
        prompt = JUDGE_PROMPT.format(fakten=f, gruppe=z["gruppe"], frage=z["frage"],
                                     quellen=json.dumps(z.get("quellen") or [])[:3000], text=z["text"][:20000])
        try:
            raw, u = _claude_text(prompt, JUDGE_MODEL, key, http)
            budget.buchen(u.get("input_tokens", 0) / 1e6 * JUDGE_PREIS[0] + u.get("output_tokens", 0) / 1e6 * JUDGE_PREIS[1])
            e.update(_judge_json(raw))
        except (httpx.HTTPError, ValueError) as ex:
            e["fehler"] = f"bewertung: {type(ex).__name__}: {str(ex)[:200]}"
        out.append(e)
    return out


def satz_je_modell(bew):
    """AR4, one sentence per model and way, computed from the judged lines, not written by a model."""
    saetze = {}
    for (m, weg) in sorted({(b["modell"], b["weg"]) for b in bew}):
        rows = [b for b in bew if b["modell"] == m and b["weg"] == weg and not b.get("fehler")]
        teil = []
        for g, n in (("A", 3), ("B", 4), ("C", 2)):
            gr = [b for b in rows if b["gruppe"] == g]
            hit = sum(1 for b in gr if b.get("genannt"))
            teil.append(f"{g} {hit}/{len(gr) or n}")
        urteile = [b.get("urteil") for b in rows if b["gruppe"] == "A" and b.get("urteil")]
        u = ", ".join(f"{k} {urteile.count(k)}" for k in sorted(set(urteile))) or "keine Urteile"
        saetze[(m, weg)] = f"{m} ({weg.replace('_', ' ')}) nennt uns in {', '.join(teil)}; Gruppe A: {u}."
    return saetze


QUELLEN_PROMPT = """Below are the group B answers (questions about agent authorization that do not name MolTrust) from several AI assistants, with the sources they cited and the names they listed. MolTrust (facts below) was {status}.

<facts>
{fakten}
</facts>

<answers>
{auszug}
</answers>

List, in German, the sources that would have to exist for MolTrust to appear in such answers: for each one line "- <kind of source> auf/bei <concrete site or publication named in the cited sources> — <why, citing which question>". Only sites that occur in the cited sources above. At most 10 lines, no introduction.
"""


def quellenliste(zeilen, bew, budget, key, http=None):
    http = http or httpx.Client()
    b_rows = [z for z in zeilen if z["gruppe"] == "B" and z.get("text")]
    if not b_rows or not budget.erlaubt(0.15):
        return "(nicht erstellt: keine Antworten der Gruppe B oder Kostendeckel)"
    genannt = any(b.get("genannt") for b in bew if b["gruppe"] == "B")
    auszug = "\n\n".join(
        f"[{z['modell']} {z['weg']} {z['frage_id']}] sources: {', '.join((z.get('quellen') or [])[:8])}\n{z['text'][:1500]}"
        for z in b_rows)
    prompt = QUELLEN_PROMPT.format(status="named in at least one" if genannt else "named in none of them",
                                   fakten=fakten(), auszug=auszug[:60000])
    try:
        raw, u = _claude_text(prompt, SUMMARY_MODEL, key, http, max_tokens=1200)
    except httpx.HTTPError as ex:
        return f"(nicht erstellt: {type(ex).__name__})"
    budget.buchen(u.get("input_tokens", 0) / 1e6 * 2.0 + u.get("output_tokens", 0) / 1e6 * 10.0)
    return raw.strip()


def bericht(datum, zeilen, bew, quellen_md, budget, fehlt):
    s = satz_je_modell(bew)
    lines = [f"# KI-Sichtbarkeit {datum}", "",
             f"Kosten geschätzt {budget.summe:.2f} USD bei Deckel {budget.deckel:.2f} USD "
             "(Listenpreise 21.11.2026, Mistral mit Obergrenze, Perplexity eigene Angabe).",
             f"Ohne Schlüssel oder ohne Lauf: {', '.join(fehlt) or 'keine'}.", "",
             "## Je Modell (AR4)", ""]
    lines += [f"- {v}" for v in s.values()] or ["- keine bewerteten Antworten"]
    err = [z for z in zeilen if z.get("fehler")]
    if err:
        lines += ["", "## Ohne Antwort", ""]
        seen = set()
        for z in err:
            k = (z["modell"], z["fehler"][:80])
            if k not in seen:
                seen.add(k)
                n = sum(1 for y in err if y["modell"] == z["modell"])
                lines.append(f"- {z['modell']}: {n} Anfragen, {z['fehler'][:160]}")
    lines += ["", "## Je Antwort (AR3)", "",
              "| Frage | Modell | Weg | genannt | Stelle | Quelle | Namen davor | Urteil |", "|---|---|---|---|---|---|---|---|"]
    for b in bew:
        if b.get("fehler"):
            lines.append(f"| {b['frage_id']} | {b['modell']} | {b['weg']} | – | – | – | – | {b['fehler'][:60]} |")
            continue
        davor = ", ".join(b.get("namen_davor") or [])[:120].replace("|", "/")
        lines.append(f"| {b['frage_id']} | {b['modell']} | {b['weg']} | {'ja' if b.get('genannt') else 'nein'} | "
                     f"{(b.get('stelle') or '–')[:50]} | {(b.get('quelle') or '–')[:60]} | {davor or '–'} | {b.get('urteil') or '–'} |")
    lines += ["", "## Welche Quelle müsste existieren, damit wir in Gruppe B vorkommen (AR4, abgeleitet)", "", quellen_md, ""]
    return "\n".join(lines)


def faellig(base=BASE_DIR, heute=None, tage=28):
    """True if the newest run directory is at least `tage` days old, or none exists (AR5)."""
    heute = heute or datetime.date.today()
    runs = sorted(p.name for p in base.glob("20??-??-??") if p.is_dir()) if base.exists() else []
    if not runs:
        return True
    return (heute - datetime.date.fromisoformat(runs[-1])).days >= tage


def _schreiben(path, zeilen):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for z in zeilen:
            f.write(json.dumps(z, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deckel", type=float, default=5.0, help="cost cap in USD for this run (AR9)")
    ap.add_argument("--faellig", action="store_true", help="only run if the last run is 28+ days old (cron)")
    ap.add_argument("--nur", help="comma-separated subset of models")
    ap.add_argument("--import", dest="imp", help="append browser answers (JSONL) to a run and re-judge it")
    ap.add_argument("--lauf", help="run date YYYY-MM-DD for --import (default: newest)")
    ap.add_argument("--schluessel", action="store_true", help="print which keys exist and exit")
    a = ap.parse_args(argv)

    da, fehlt = schluessel_stand()
    if a.schluessel:
        print("vorhanden:", ", ".join(da) or "keine")
        print("fehlt:", ", ".join(fehlt) or "keine")
        return 0
    if a.faellig and not a.imp and not faellig():
        print(f"{now_iso()} ai_sicht: nicht faellig (letzter Lauf juenger als 28 Tage)")
        return 0
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ai_sicht: ANTHROPIC_API_KEY fehlt, ohne Bewerter kein Lauf", file=sys.stderr)
        return 2
    budget = Budget(a.deckel)
    http = httpx.Client()

    if a.imp:
        runs = sorted(p.name for p in BASE_DIR.glob("20??-??-??"))
        datum = a.lauf or (runs[-1] if runs else None)
        if not datum:
            print("ai_sicht: kein Lauf zum Importieren", file=sys.stderr)
            return 2
        d = BASE_DIR / datum
        zeilen = [json.loads(x) for x in (d / "antworten.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        neu = [json.loads(x) for x in pathlib.Path(a.imp).read_text(encoding="utf-8").splitlines() if x.strip()]
        for z in neu:
            for k in ("ts", "frage_id", "gruppe", "frage", "modell", "weg", "text", "version"):
                if k not in z:
                    raise ValueError(f"import line without {k}: {z.get('modell')} {z.get('frage_id')}")
            z["kanal"] = "browser"
        zeilen += neu
        alt_bew = [json.loads(x) for x in (d / "auswertung.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        bew = alt_bew + bewerten(neu, budget, key, http)
    else:
        modelle = [m for m in (a.nur.split(",") if a.nur else MODELLE) if m in da]
        datum = datetime.date.today().isoformat()
        d = BASE_DIR / datum
        d.mkdir(parents=True, exist_ok=True)
        zeilen = befragen(modelle, budget, http=http)
        bew = bewerten(zeilen, budget, key, http)

    _schreiben(d / "antworten.jsonl", zeilen)
    _schreiben(d / "auswertung.jsonl", bew)
    ql = quellenliste(zeilen, bew, budget, key, http)
    nie = fehlt + [m for m in NUR_BROWSER if not any(z["modell"] == m for z in zeilen)]
    (d / "bericht.md").write_text(bericht(datum, zeilen, bew, ql, budget, nie), encoding="utf-8")
    ok = sum(1 for z in zeilen if not z.get("fehler"))
    print(f"{now_iso()} ai_sicht: {ok}/{len(zeilen)} Antworten, {len(bew)} bewertet, "
          f"{budget.summe:.2f} USD geschaetzt, Ablage {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
