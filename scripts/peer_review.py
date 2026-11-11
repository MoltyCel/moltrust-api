#!/usr/bin/env python3
"""
MolTrust Peer Review Runner (external models only, no synthesis).

Sends an identical briefing (with a fixed reviewer prompt prefix) to the
strongest available *reasoning* model of each configured provider, one turn
each, in parallel. Writes each model's answer UNABRIDGED to
<outdir>/<model-id>.md. No synthesis, no verdict, no Telegram.

Model IDs are NOT hardcoded: each provider's /models endpoint is queried at
runtime and the strongest reasoning variant is selected by a documented
ranking. (Exception: Perplexity has no /models endpoint -> availability probe
over a candidate priority list; flagged in the run summary.)

Secrets precedence: os.environ -> ~/.env -> ~/.moltrust_secrets
"""
import argparse
import concurrent.futures
import json
import re
import time
from pathlib import Path

import httpx

PROMPT_PREFIX = """You are an external reviewer. Answer strictly per the requirements in
section 6 of the briefing: concrete numbers, name the weakest assumption
explicitly, single most important change first, answer Q1-Q7 individually,
cite public evidence where available. Web research encouraged if your
API supports it. Be adversarial. No praise. State your model ID first.
English."""

READ_TIMEOUT = 1500  # 25 min; pro reasoning + web search can be slow


# ── secrets ───────────────────────────────────────────────────────────────
def load_secrets():
    import os
    filedict = {}
    for f in (Path.home() / ".moltrust_secrets", Path.home() / ".env"):  # .env overrides secrets
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    filedict[k.strip().replace("export ", "").strip()] = v.strip().strip('"').strip("'")

    def get(key):
        return os.environ.get(key) or filedict.get(key)

    return get


# ── model selection ─────────────────────────────────────────────────────────
def _ver(i):
    m = re.search(r"(\d+(?:\.\d+)?)", i)
    return float(m.group(1)) if m else 0.0


def pick_openai(key):
    r = httpx.get("https://api.openai.com/v1/models",
                  headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", [])]
    BAD = ("image", "audio", "realtime", "tts", "transcribe", "whisper", "search",
           "codex", "embed", "moderation", "ocr", "vibe", "robotic", "computer-use",
           "chat-latest", "instruct", "-mini", "-nano", "turbo", "3.5", "dall")
    cands = [i for i in ids
             if i.startswith(("gpt-", "o1", "o3", "o4", "o5")) and not any(b in i for b in BAD)]
    tier = lambda i: 3 if "pro" in i else 0                    # 'pro' = reasoning-max tier
    cands.sort(key=lambda i: (tier(i), _ver(i), len(i)), reverse=True)
    return cands[0], cands[:8]


def pick_gemini(key):
    r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                  headers={"x-goog-api-key": key},
                  params={"pageSize": 200}, timeout=30)
    r.raise_for_status()
    models = [m for m in r.json().get("models", [])
              if "generateContent" in m.get("supportedGenerationMethods", [])]
    ids = [m["name"].split("/")[-1] for m in models]
    BAD = ("image", "tts", "embedding", "robotic", "computer-use", "omni",
           "-lite", "customtools", "vision", "aqa")
    cands = [i for i in ids if i.startswith("gemini") and not any(b in i for b in BAD)]
    tier = lambda i: 3 if "pro" in i else (1 if "flash" in i else 2)  # pro > (other) > flash
    cands.sort(key=lambda i: (tier(i), _ver(i), len(i)), reverse=True)
    return cands[0], cands[:8]


def pick_mistral(key):
    r = httpx.get("https://api.mistral.ai/v1/models",
                  headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", [])
    chat = []
    for m in data:
        caps = m.get("capabilities", {}) or {}
        if caps.get("completion_chat", caps.get("completion", True)):
            chat.append(m["id"])
    # 'magistral' = Mistral's dedicated reasoning line -> top tier
    def tier(i):
        if "magistral" in i:
            return 3
        if "large" in i:
            return 2
        if i.startswith("mistral-medium"):
            return 1
        return 0
    size = lambda i: 2 if "medium" in i else (1 if "small" in i else 0)
    cands = [i for i in set(chat) if tier(i) > 0]
    cands.sort(key=lambda i: (tier(i), size(i), _ver(i), len(i)), reverse=True)
    return cands[0], cands[:8]


PPLX_CANDIDATES = ["sonar-reasoning-pro", "sonar-reasoning", "sonar-pro",
                   "sonar-deep-research", "sonar"]


def pick_perplexity(key):
    # No /models endpoint (404). Probe candidates by priority; first that answers wins.
    tried = []
    for mid in PPLX_CANDIDATES:
        try:
            r = httpx.post("https://api.perplexity.ai/chat/completions",
                           headers={"Authorization": f"Bearer {key}"},
                           json={"model": mid, "messages": [{"role": "user", "content": "hi"}],
                                 "max_tokens": 16}, timeout=40)
            tried.append(f"{mid}:{r.status_code}")
            if r.status_code == 200:
                return mid, tried
        except Exception as e:
            tried.append(f"{mid}:ERR")
    raise RuntimeError("no perplexity model answered probe: " + ", ".join(tried))


# ── provider calls ───────────────────────────────────────────────────────────
def call_openai(key, mid, prompt):
    # reasoning models: cap must exceed reasoning tokens or the final message is truncated
    tool_variants = [[{"type": "web_search"}], [{"type": "web_search_preview"}], []]
    r = None
    used_tools = []
    for tv in tool_variants:
        body = {"model": mid, "input": prompt, "reasoning": {"effort": "high"},
                "max_output_tokens": 100000}
        if tv:
            body["tools"] = tv
        r = httpx.post("https://api.openai.com/v1/responses",
                       headers={"Authorization": f"Bearer {key}"}, json=body,
                       timeout=httpx.Timeout(READ_TIMEOUT, connect=30))
        if r.status_code < 400:
            used_tools = tv
            break
    r.raise_for_status()
    d = r.json()
    parts = []
    for item in d.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
    u = d.get("usage", {}) or {}
    usage = {"input": u.get("input_tokens"), "output": u.get("output_tokens"),
             "total": u.get("total_tokens")}
    text = "\n".join(parts).strip()
    if not text:
        raise RuntimeError(f"no output_text (status={d.get('status')}, "
                           f"incomplete={d.get('incomplete_details')})")
    return text, usage, bool(used_tools)


def call_gemini(key, mid, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{mid}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 16000}}
    web = True
    headers = {"x-goog-api-key": key}
    r = httpx.post(url, headers=headers, json=body,
                   timeout=httpx.Timeout(READ_TIMEOUT, connect=30))
    if r.status_code >= 400:
        body.pop("tools", None)
        web = False
        r = httpx.post(url, headers=headers, json=body,
                       timeout=httpx.Timeout(READ_TIMEOUT, connect=30))
    r.raise_for_status()
    d = r.json()
    cand = (d.get("candidates") or [{}])[0]
    parts = [p.get("text", "") for p in cand.get("content", {}).get("parts", []) if "text" in p]
    um = d.get("usageMetadata", {}) or {}
    usage = {"input": um.get("promptTokenCount"),
             "output": (um.get("candidatesTokenCount") or 0) + (um.get("thoughtsTokenCount") or 0),
             "total": um.get("totalTokenCount")}
    return "\n".join(parts).strip(), usage, web


def call_perplexity(key, mid, prompt):
    r = httpx.post("https://api.perplexity.ai/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": mid, "messages": [{"role": "user", "content": prompt}]},
                   timeout=httpx.Timeout(READ_TIMEOUT, connect=30))
    r.raise_for_status()
    d = r.json()
    text = d["choices"][0]["message"]["content"].strip()
    cites = d.get("citations") or d.get("search_results") or []
    if cites:
        lines = []
        for i, c in enumerate(cites, 1):
            if isinstance(c, dict):
                lines.append(f"{i}. {c.get('title', '')} {c.get('url', '')}".strip())
            else:
                lines.append(f"{i}. {c}")
        text += "\n\n## Citations (API)\n" + "\n".join(lines)
    u = d.get("usage", {}) or {}
    usage = {"input": u.get("prompt_tokens"), "output": u.get("completion_tokens"),
             "total": u.get("total_tokens")}
    return text, usage, True


def _mistral_content(content):
    # magistral returns a list of {type: thinking|text} chunks; string for non-reasoning models
    if isinstance(content, str):
        return content.strip()
    answer, thinking = [], []
    for ch in content:
        if not isinstance(ch, dict):
            answer.append(str(ch))
            continue
        t = ch.get("type")
        if t == "text":
            answer.append(ch.get("text", ""))
        elif t == "thinking":
            for tp in ch.get("thinking", []):
                if isinstance(tp, dict):
                    thinking.append(tp.get("text", ""))
    out = "\n".join(a for a in answer if a).strip()
    tr = "\n".join(t for t in thinking if t).strip()
    if tr:
        out += "\n\n---\n## Reasoning trace (magistral)\n\n" + tr
    return out.strip()


def call_mistral(key, mid, prompt):
    r = httpx.post("https://api.mistral.ai/v1/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": mid, "messages": [{"role": "user", "content": prompt}],
                         "max_tokens": 40000},
                   timeout=httpx.Timeout(READ_TIMEOUT, connect=30))
    r.raise_for_status()
    d = r.json()
    text = _mistral_content(d["choices"][0]["message"]["content"])
    u = d.get("usage", {}) or {}
    usage = {"input": u.get("prompt_tokens"), "output": u.get("completion_tokens"),
             "total": u.get("total_tokens")}
    return text, usage, False


def pick_anthropic(key):
    # discover via /v1/models; user chose Opus 4.8 -> prefer it, else highest opus
    r = httpx.get("https://api.anthropic.com/v1/models",
                  headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=30)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", [])]
    opus = [i for i in ids if "opus" in i]
    if "claude-opus-4-8" in opus:
        return "claude-opus-4-8", opus[:8]
    opus.sort(key=lambda i: (_ver(i), len(i)), reverse=True)
    if not opus:
        raise RuntimeError("no opus model in anthropic /v1/models")
    return opus[0], opus[:8]


def call_anthropic(key, mid, prompt):
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    tool = [{"type": "web_search_20260209", "name": "web_search"}]  # Opus 4.6+, no beta header

    def body(msgs, tools):
        b = {"model": mid, "max_tokens": 32000,
             "thinking": {"type": "adaptive"},          # budget_tokens 400s on 4.8; omit==no thinking
             "output_config": {"effort": "high"},
             "messages": msgs}
        if tools:
            b["tools"] = tools
        return b

    web = True
    msgs = [{"role": "user", "content": prompt}]
    r = httpx.post("https://api.anthropic.com/v1/messages", headers=headers,
                   json=body(msgs, tool), timeout=httpx.Timeout(READ_TIMEOUT, connect=30))
    if r.status_code >= 400:          # web_search unsupported -> retry tool-less
        web = False
        r = httpx.post("https://api.anthropic.com/v1/messages", headers=headers,
                       json=body(msgs, None), timeout=httpx.Timeout(READ_TIMEOUT, connect=30))
    r.raise_for_status()
    d = r.json()
    in_t = out_t = 0
    def acc(u):
        nonlocal in_t, out_t
        in_t += (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) \
            + (u.get("cache_creation_input_tokens") or 0)
        out_t += (u.get("output_tokens") or 0)
    acc(d.get("usage", {}) or {})
    guard = 0
    while d.get("stop_reason") == "pause_turn" and guard < 6:   # resume server-tool loop
        guard += 1
        msgs.append({"role": "assistant", "content": d.get("content", [])})
        r = httpx.post("https://api.anthropic.com/v1/messages", headers=headers,
                       json=body(msgs, tool if web else None),
                       timeout=httpx.Timeout(READ_TIMEOUT, connect=30))
        r.raise_for_status()
        d = r.json()
        acc(d.get("usage", {}) or {})
    parts = [b.get("text", "") for b in d.get("content", []) if b.get("type") == "text"]
    usage = {"input": in_t, "output": out_t, "total": in_t + out_t}
    return "\n".join(p for p in parts if p).strip(), usage, web


PROVIDERS = {
    "openai":     {"key": "OPENAI_API_KEY",     "pick": pick_openai,     "call": call_openai},
    "gemini":     {"key": "GEMINI_API_KEY",     "pick": pick_gemini,     "call": call_gemini},
    "perplexity": {"key": "PERPLEXITY_API_KEY", "pick": pick_perplexity, "call": call_perplexity},
    "mistral":    {"key": "MISTRAL_API_KEY",    "pick": pick_mistral,    "call": call_mistral},
    "anthropic":  {"key": "ANTHROPIC_API_KEY",  "pick": pick_anthropic,  "call": call_anthropic},
}


_URL_QUERY = re.compile(r"(https?://[^\s'\"]*?)\?[^\s'\"]*")


def _scrub(text):
    """Drop the query string from any URL in an error text.

    Error texts travel into console, run.log, PANEL.md and the summary JSON.
    Credentials belong in headers (see the callers above); this keeps a query
    string out of the message regardless, so that rule holds on every path.
    """
    return _URL_QUERY.sub(r"\1?<redacted>", str(text))


def _error_detail(exc):
    """Pull the error fields out of an HTTP error response, across providers.

    OpenAI/Mistral:  error.type / error.code / error.message
    Gemini:          error.status / error.code / error.message
    Anthropic:       error.type / error.message
    Returns only these fields, never headers, auth or the request body.
    """
    out = {"http_status": None, "error_type": None, "error_code": None}
    resp = getattr(exc, "response", None)
    if resp is None:
        return out
    out["http_status"] = getattr(resp, "status_code", None)
    try:
        err = (resp.json() or {}).get("error", {})
    except Exception:
        return out
    if not isinstance(err, dict):
        return out
    out["error_type"] = err.get("type") or err.get("status")
    out["error_code"] = err.get("code")
    return out


def _panel_report(records):
    """One line of reason per provider, so that a failure does not go unnoticed."""
    lines = []
    for r in sorted(records, key=lambda x: x.get("provider", "")):
        name = r.get("provider")
        if r.get("status") == "ok":
            lines.append(f"- {name}: OK (model={r.get('model_id')}, "
                         f"web_search={r.get('web_search')})")
        else:
            reason = r.get("error_code") or r.get("error_type") or r.get("error") or r.get("status")
            http = f" HTTP {r['http_status']}" if r.get("http_status") else ""
            lines.append(f"- {name}: **AUSGEFALLEN**{http} (grund: {reason})")
    return lines


def run_provider(name, cfg, key, prompt, outdir):
    t0 = time.time()
    rec = {"provider": name, "model_id": None, "status": None, "web_search": None,
           "usage": None, "file": None, "error": None, "model_ranking": None,
           "http_status": None, "error_type": None, "error_code": None}
    try:
        mid, ranking = cfg["pick"](key)
        rec["model_id"] = mid
        rec["model_ranking"] = ranking
        text, usage, web = cfg["call"](key, mid, prompt)
        rec["usage"] = usage
        rec["web_search"] = web
        if not text:
            raise RuntimeError("empty response body")
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", mid)
        fp = Path(outdir) / f"{safe}.md"
        fm = (f"---\nprovider: {name}\nmodel_id: {mid}\nweb_search: {web}\n"
              f"input_tokens: {usage.get('input')}\noutput_tokens: {usage.get('output')}\n"
              f"total_tokens: {usage.get('total')}\n---\n\n")
        fp.write_text(fm + text)
        rec["file"] = str(fp)
        rec["status"] = "ok"
    except Exception as e:
        rec["status"] = "error"
        rec["error"] = _scrub(f"{type(e).__name__}: {e}")
        # The response body carries error.type/.code (e.g. insufficient_quota vs
        # rate_limit_exceeded). raise_for_status() drops it, but httpx.HTTPStatusError
        # keeps the response, so read it here and a 429 stays distinguishable.
        # Only error fields are copied, never headers or keys.
        rec.update(_error_detail(e))
    rec["elapsed_s"] = round(time.time() - t0, 1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--providers", default="openai,gemini,perplexity,mistral,anthropic")
    ap.add_argument("--prefix", default="",
                    help="Reviewer prompt prefix prepended to the briefing. "
                         "Empty (default) sends a self-contained briefing verbatim. "
                         "Pass 'pricing' for the legacy section-6 pricing prefix.")
    args = ap.parse_args()

    get = load_secrets()
    briefing = Path(args.input).read_text()
    prefix = PROMPT_PREFIX if args.prefix == "pricing" else args.prefix
    prompt = (prefix + "\n\n" + briefing) if prefix else briefing
    Path(args.outdir).mkdir(parents=True, exist_ok=True)

    wanted = [p.strip() for p in args.providers.split(",") if p.strip()]
    records = []
    tasks = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(wanted)) as ex:
        for name in wanted:
            cfg = PROVIDERS.get(name)
            if not cfg:
                records.append({"provider": name, "status": "skipped", "error": "unknown provider"})
                continue
            key = get(cfg["key"])
            if not key:
                records.append({"provider": name, "status": "skipped",
                                "error": f"missing key {cfg['key']}"})
                print(f"[skip] {name}: missing key {cfg['key']}", flush=True)
                continue
            tasks[ex.submit(run_provider, name, cfg, key, prompt, args.outdir)] = name
            print(f"[start] {name}", flush=True)
        for fut in concurrent.futures.as_completed(tasks):
            rec = fut.result()
            records.append(rec)
            print(f"[done] {rec['provider']}: {rec['status']} "
                  f"model={rec.get('model_id')} web={rec.get('web_search')} "
                  f"elapsed={rec.get('elapsed_s')}s err={rec.get('error')}", flush=True)

    # Name failed providers explicitly. Without this block a panel where one
    # provider dropped out silently looks complete in the outdir: there is
    # simply one file fewer (this happened on two earlier panels, both missing
    # the OpenAI voice without a note).
    ok = [r for r in records if r.get("status") == "ok"]
    failed = [r for r in records if r.get("status") != "ok"]
    panel_lines = _panel_report(records)

    panel_md = (f"# Panel-Status\n\n"
                f"**{len(ok)} von {len(records)} Providern haben geantwortet.**\n\n"
                + "\n".join(panel_lines) + "\n")
    if failed:
        panel_md += ("\n> Die Auswertung stuetzt sich auf "
                     f"{len(ok)} Stimme(n), nicht auf {len(records)}. "
                     "Ausgefallene Provider sind oben mit Grund benannt.\n")
    (Path(args.outdir) / "PANEL.md").write_text(panel_md)

    print("\n[PANEL] %d/%d Provider ok" % (len(ok), len(records)), flush=True)
    for line in panel_lines:
        print("  " + line.replace("**", ""), flush=True)

    summary = {"input": args.input, "outdir": args.outdir,
               "panel": {"requested": len(records), "ok": len(ok), "failed": len(failed),
                         "failed_providers": [r.get("provider") for r in failed]},
               "records": records}
    print("\n===SUMMARY_JSON===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
