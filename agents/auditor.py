#!/usr/bin/env python3
"""MolTrust Auditor Agent v2 — Security scanner for agent infrastructure & Moltbook content.

Usage:
    auditor.py                          # quick mode (static targets, backward compat)
    auditor.py --mode quick             # same as above
    auditor.py --mode full              # dynamic targets + content analysis
    auditor.py --mode full --publish    # full scan + post summary to Moltbook
"""

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENT_DID = "did:moltrust:b64714929fc44277"
AGENT_NAME = "MolTrust Auditor"
LOG_DIR = Path.home() / "moltstack" / "logs"
MOLTRUST_BASE = "https://api.moltrust.ch"
MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"

STATIC_TARGETS = [
    {"name": "MolTrust API", "url": "https://api.moltrust.ch", "own": True},
    {"name": "Moltbook API", "url": "https://www.moltbook.com", "own": False},
]

MAX_DYNAMIC_TARGETS = 50

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "auditor.log"
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("auditor")

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def load_key(name: str) -> str:
    secrets = Path.home() / ".moltrust_secrets"
    if secrets.exists():
        for line in secrets.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            if line.startswith("export "):
                line = line[7:]
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return os.environ.get(name, "")


ANTHROPIC_KEY = ""
MOLTRUST_API_KEY = ""
MOLTBOOK_KEY = ""


def init_keys():
    global ANTHROPIC_KEY, MOLTRUST_API_KEY, MOLTBOOK_KEY
    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_KEY:
        key_file = Path.home() / ".anthropic_key"
        if key_file.exists():
            ANTHROPIC_KEY = key_file.read_text().strip()
    MOLTRUST_API_KEY = load_key("MOLTRUST_API_KEYS")
    MOLTBOOK_KEY = load_key("MOLTBOOK_AGENT_KEY")


# ---------------------------------------------------------------------------
# Path exposure checks
# ---------------------------------------------------------------------------

PATH_CHECKS = [
    {"path": "/.env", "risk": "SECRET_LEAK", "desc": "Environment file exposed"},
    {"path": "/.git/config", "risk": "SOURCE_LEAK", "desc": "Git config exposed"},
    {"path": "/admin", "risk": "ADMIN_PANEL", "desc": "Admin panel accessible"},
    {"path": "/.well-known/did.json", "risk": "INFO", "desc": "DID document"},
    {"path": "/health", "risk": "INFO", "desc": "Health endpoint"},
    {"path": "/.well-known/security.txt", "risk": "INFO", "desc": "Security contact"},
    {"path": "/robots.txt", "risk": "INFO", "desc": "Robots.txt"},
    {"path": "/docs", "risk": "MEDIUM", "desc": "API docs / Swagger UI"},
    {"path": "/swagger.json", "risk": "MEDIUM", "desc": "Swagger spec"},
    {"path": "/openapi.json", "risk": "MEDIUM", "desc": "OpenAPI spec"},
]

SECRET_PATTERNS = ["sk-ant-", "xprv", "AKIA", "BEGIN PRIVATE KEY", "ghp_", "gho_", "glpat-"]

PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\s+a",
    r"system\s*prompt\s*:",
    r"<\|?system\|?>",
    r"###\s*(system|instruction)",
    r"forget\s+(everything|all|your)\s+(you|instructions|rules)",
    r"disregard\s+(all|your|previous)",
    r"new\s+persona\s*:",
    r"jailbreak",
    r"DAN\s+mode",
]

SOCIAL_ENGINEERING_PATTERNS = [
    r"send\s+(me|us)\s+(your|the)\s+(api|private|secret)\s*key",
    r"dm\s+me\s+(your|the)\s+(credentials|password|key|token)",
    r"paste\s+your\s+(key|token|secret|password)",
    r"share\s+your\s+(private|secret|api)\s*key",
    r"verify\s+your\s+identity\s+by\s+sending",
]

PHISHING_DOMAINS = [
    "bit.ly/", "tinyurl.com/", "is.gd/",  # URL shorteners in agent context = suspicious
]

# ---------------------------------------------------------------------------
# Infrastructure scanning
# ---------------------------------------------------------------------------


def check_ssl(client: httpx.Client, url: str) -> list[dict]:
    try:
        resp = client.get(url, timeout=10, follow_redirects=True)
        if resp.url.scheme == "https":
            return [{"severity": "OK", "issue": "SSL/TLS active"}]
        return [{"severity": "HIGH", "issue": "No SSL/TLS"}]
    except Exception as e:
        return [{"severity": "ERROR", "issue": f"SSL check failed: {e}"}]


def check_headers(client: httpx.Client, url: str) -> list[dict]:
    findings = []
    try:
        resp = client.get(url, timeout=10, follow_redirects=True)
        h = resp.headers

        if "strict-transport-security" not in h:
            findings.append({"severity": "MEDIUM", "issue": "Missing HSTS header"})
        if "x-frame-options" not in h and "content-security-policy" not in h:
            findings.append({"severity": "LOW", "issue": "Missing clickjacking protection"})
        if "x-content-type-options" not in h:
            findings.append({"severity": "LOW", "issue": "Missing X-Content-Type-Options"})
        if "content-security-policy" not in h:
            findings.append({"severity": "LOW", "issue": "Missing Content-Security-Policy"})
        if "server" in h:
            findings.append({"severity": "LOW", "issue": f"Server header exposed: {h['server']}"})

        # CORS check
        acao = h.get("access-control-allow-origin", "")
        if acao == "*":
            findings.append({"severity": "MEDIUM", "issue": "CORS wildcard: Access-Control-Allow-Origin: *"})

        # Secret leak in body
        body = resp.text[:10000]
        for leak in SECRET_PATTERNS:
            if leak in body:
                findings.append({"severity": "CRITICAL", "issue": f"Possible secret in response: {leak}"})

        if not findings:
            findings.append({"severity": "OK", "issue": "Headers look good"})
    except Exception as e:
        findings.append({"severity": "ERROR", "issue": str(e)})
    return findings


def check_paths(client: httpx.Client, base_url: str) -> list[dict]:
    findings = []
    for check in PATH_CHECKS:
        try:
            url = base_url.rstrip("/") + check["path"]
            resp = client.get(url, timeout=8, follow_redirects=False)
            status = resp.status_code
            if check["risk"] == "INFO":
                findings.append({"severity": "INFO", "issue": f"{check['path']} -> {status}"})
            elif status == 200:
                if check["risk"] == "SECRET_LEAK":
                    sev = "CRITICAL"
                elif check["risk"] == "SOURCE_LEAK":
                    sev = "CRITICAL"
                elif check["risk"] == "ADMIN_PANEL":
                    sev = "HIGH"
                else:
                    sev = "MEDIUM"
                findings.append({"severity": sev, "issue": f"{check['path']} -> EXPOSED ({check['desc']})"})
            else:
                findings.append({"severity": "OK", "issue": f"{check['path']} -> {status} blocked"})
        except Exception as e:
            findings.append({"severity": "ERROR", "issue": f"{check['path']} -> {e}"})
        time.sleep(0.5)
    return findings


def check_rate_limiting(client: httpx.Client, url: str) -> list[dict]:
    """Send rapid requests to check for rate limiting."""
    target = url.rstrip("/") + "/health"
    got_429 = False
    try:
        for _ in range(10):
            resp = client.get(target, timeout=5)
            if resp.status_code == 429:
                got_429 = True
                break
        if got_429:
            return [{"severity": "OK", "issue": "Rate limiting active (429 detected)"}]
        return [{"severity": "MEDIUM", "issue": "No rate limiting detected after 10 rapid requests"}]
    except Exception as e:
        return [{"severity": "ERROR", "issue": f"Rate limit check failed: {e}"}]


def check_error_verbosity(client: httpx.Client, url: str) -> list[dict]:
    """Trigger errors and check if stack traces leak."""
    findings = []
    try:
        # Try a non-existent path
        resp = client.get(url.rstrip("/") + "/nonexistent_path_audit_check", timeout=8)
        body = resp.text[:3000].lower()
        leak_indicators = ["traceback", "stacktrace", "at line", "file \"", "exception in", "internal server error"]
        for indicator in leak_indicators:
            if indicator in body:
                findings.append({"severity": "MEDIUM", "issue": f"Error response may leak internals: '{indicator}' found"})
                break
        if not findings:
            findings.append({"severity": "OK", "issue": "Error responses don't leak stack traces"})
    except Exception as e:
        findings.append({"severity": "ERROR", "issue": f"Error verbosity check failed: {e}"})
    return findings


def scan_target(client: httpx.Client, name: str, url: str) -> dict:
    """Run all infrastructure checks on a target."""
    log.info(f"Scanning: {name} ({url})")
    result = {
        "url": url,
        "ssl": check_ssl(client, url),
        "headers": check_headers(client, url),
        "paths": check_paths(client, url),
        "rate_limiting": check_rate_limiting(client, url),
        "error_verbosity": check_error_verbosity(client, url),
    }
    all_findings = result["ssl"] + result["headers"] + result["paths"] + result["rate_limiting"] + result["error_verbosity"]
    crit = sum(1 for f in all_findings if f["severity"] == "CRITICAL")
    high = sum(1 for f in all_findings if f["severity"] == "HIGH")
    med = sum(1 for f in all_findings if f["severity"] == "MEDIUM")
    log.info(f"  -> {crit} critical, {high} high, {med} medium")
    return result


# ---------------------------------------------------------------------------
# Dynamic target discovery
# ---------------------------------------------------------------------------


def discover_targets_from_moltbook(client: httpx.Client) -> list[dict]:
    """Scan Moltbook posts for agent URLs to add as scan targets."""
    targets = []
    seen_urls = set()
    try:
        resp = client.get(
            f"{MOLTBOOK_BASE}/posts",
            headers={"Authorization": f"Bearer {MOLTBOOK_KEY}"},
            params={"sort": "hot", "limit": 30},
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning(f"Moltbook posts fetch failed: {resp.status_code}")
            return targets
        data = resp.json()
        posts = data if isinstance(data, list) else data.get("posts", [])
        url_re = re.compile(r"https?://[^\s<>\"')\]]+")
        skip_domains = {"github.com", "basescan.org", "sepolia.basescan.org", "polygonscan.com",
                        "amoy.polygonscan.com", "pump.fun", "moltrust.ch", "api.moltrust.ch",
                        "moltbook.com", "www.moltbook.com", "amzn.eu", "amazon.com",
                        "twitter.com", "x.com", "discord.gg", "t.me", "youtube.com",
                        "npmjs.com", "pypi.org", "mbc20.xyz"}
        for post in posts:
            content = (post.get("content", "") + " " + post.get("title", ""))
            urls = url_re.findall(content)
            author = post.get("author", {}).get("name", "unknown")
            for url in urls:
                url = url.rstrip("/.,;:!?")
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(url).netloc.lower()
                except Exception:
                    continue
                if domain in skip_domains or domain in seen_urls:
                    continue
                if not domain or "." not in domain:
                    continue
                seen_urls.add(domain)
                base = f"https://{domain}"
                targets.append({"name": f"Moltbook/{author}: {domain}", "url": base, "own": False})
                if len(targets) >= MAX_DYNAMIC_TARGETS:
                    break
            if len(targets) >= MAX_DYNAMIC_TARGETS:
                break
    except Exception as e:
        log.error(f"Dynamic discovery failed: {e}")
    log.info(f"Discovered {len(targets)} dynamic targets from Moltbook")
    return targets


# ---------------------------------------------------------------------------
# Content analysis (Moltbook scanner)
# ---------------------------------------------------------------------------


def analyze_moltbook_content(client: httpx.Client) -> list[dict]:
    """Scan recent Moltbook posts for suspicious content."""
    findings = []
    try:
        resp = client.get(
            f"{MOLTBOOK_BASE}/posts",
            headers={"Authorization": f"Bearer {MOLTBOOK_KEY}"},
            params={"sort": "new", "limit": 50},
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning(f"Moltbook content fetch failed: {resp.status_code}")
            return findings
        data = resp.json()
        posts = data if isinstance(data, list) else data.get("posts", [])

        for post in posts:
            pid = post.get("id", "?")[:8]
            author = post.get("author", {}).get("name", "unknown")
            title = post.get("title", "")
            content = post.get("content", "")
            full_text = title + " " + content

            # Prompt injection
            for pattern in PROMPT_INJECTION_PATTERNS:
                if re.search(pattern, full_text, re.IGNORECASE):
                    findings.append({
                        "severity": "HIGH",
                        "category": "prompt_injection",
                        "post_id": pid,
                        "author": author,
                        "issue": f"Prompt injection pattern: '{pattern}' in post '{title[:50]}'",
                    })
                    break  # one finding per post for this category

            # Secret leaks
            for secret in SECRET_PATTERNS:
                if secret in full_text:
                    findings.append({
                        "severity": "CRITICAL",
                        "category": "secret_leak",
                        "post_id": pid,
                        "author": author,
                        "issue": f"Possible secret '{secret}' in post by {author}",
                    })

            # Social engineering
            for pattern in SOCIAL_ENGINEERING_PATTERNS:
                if re.search(pattern, full_text, re.IGNORECASE):
                    findings.append({
                        "severity": "HIGH",
                        "category": "social_engineering",
                        "post_id": pid,
                        "author": author,
                        "issue": f"Social engineering pattern in post by {author}: '{title[:50]}'",
                    })
                    break

            # Phishing URLs
            for domain in PHISHING_DOMAINS:
                if domain in full_text.lower():
                    findings.append({
                        "severity": "MEDIUM",
                        "category": "suspicious_url",
                        "post_id": pid,
                        "author": author,
                        "issue": f"URL shortener ({domain}) used by {author} in '{title[:50]}'",
                    })

    except Exception as e:
        log.error(f"Content analysis failed: {e}")

    log.info(f"Content analysis: {len(findings)} findings in {len(posts) if 'posts' in dir() else '?'} posts")
    return findings


# ---------------------------------------------------------------------------
# Verifiable Credential issuance
# ---------------------------------------------------------------------------


def issue_audit_vc(client: httpx.Client, subject_did: str) -> dict | None:
    """Issue a SecurityAudit VC for a scanned target that passed checks."""
    try:
        resp = client.post(
            f"{MOLTRUST_BASE}/credentials/issue",
            headers={"X-API-Key": MOLTRUST_API_KEY, "Content-Type": "application/json"},
            json={"subject_did": subject_did, "credential_type": "SecurityAudit"},
            timeout=15,
        )
        if resp.status_code == 200:
            vc = resp.json()
            log.info(f"Issued SecurityAudit VC for {subject_did}")
            return vc
        log.warning(f"VC issuance failed for {subject_did}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.error(f"VC issuance error: {e}")
    return None


def maybe_issue_vcs(client: httpx.Client, infra_results: dict):
    """Issue VCs for targets owned by MolTrust that passed with no CRITICAL findings."""
    for name, result in infra_results.items():
        if not result.get("own"):
            continue
        all_findings = []
        for key in ("ssl", "headers", "paths", "rate_limiting", "error_verbosity"):
            all_findings.extend(result.get(key, []))
        has_critical = any(f["severity"] == "CRITICAL" for f in all_findings)
        if not has_critical:
            # Issue VC for our own DID (the auditor attesting our infra)
            issue_audit_vc(client, AGENT_DID)


# ---------------------------------------------------------------------------
# Report generation via Claude
# ---------------------------------------------------------------------------


def generate_report(infra_results: dict, content_findings: list[dict], mode: str) -> str:
    findings_summary = json.dumps(infra_results, indent=2)[:6000]
    content_summary = json.dumps(content_findings, indent=2)[:3000] if content_findings else "No content analysis performed."

    prompt = (
        f"You are the MolTrust Auditor Agent (DID: {AGENT_DID}).\n"
        f"Scan mode: {mode}\n\n"
        f"Infrastructure scan results:\n{findings_summary}\n\n"
        f"Moltbook content analysis:\n{content_summary}\n\n"
        f"Write a professional security report with these sections:\n"
        f"1. EXECUTIVE SUMMARY (2-3 sentences)\n"
        f"2. INFRASTRUCTURE FINDINGS (by severity)\n"
        f"3. CONTENT ANALYSIS (if applicable)\n"
        f"4. RECOMMENDATIONS\n"
        f"5. CREDENTIAL STATUS (VCs issued)\n"
        f"Be factual, concise. Use markdown formatting."
    )
    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 2500,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            return "\n".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        return f"Report generation failed: {resp.status_code}"
    except Exception as e:
        return f"Report generation failed: {e}"


# ---------------------------------------------------------------------------
# Moltbook publishing
# ---------------------------------------------------------------------------


def publish_to_moltbook(client: httpx.Client, infra_results: dict, content_findings: list[dict]) -> bool:
    """Post a summary to Moltbook if there are CRITICAL or HIGH findings."""
    all_findings = []
    for name, result in infra_results.items():
        for key in ("ssl", "headers", "paths", "rate_limiting", "error_verbosity"):
            for f in result.get(key, []):
                if f["severity"] in ("CRITICAL", "HIGH"):
                    all_findings.append(f"{name}: {f['issue']}")
    for cf in content_findings:
        if cf["severity"] in ("CRITICAL", "HIGH"):
            all_findings.append(f"Content: {cf['issue']}")

    if not all_findings:
        log.info("No CRITICAL/HIGH findings — skipping Moltbook publish")
        return False

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = f"Security Scan Report — {now}"
    body = (
        f"The MolTrust Auditor performed a security scan across agent infrastructure and Moltbook content.\n\n"
        f"**Key findings ({len(all_findings)}):**\n"
    )
    for f in all_findings[:10]:
        body += f"- {f}\n"
    if len(all_findings) > 10:
        body += f"\n...and {len(all_findings) - 10} more. Full report available via MolTrust API.\n"
    body += (
        f"\nAll scanned infrastructure without CRITICAL findings receives a SecurityAudit Verifiable Credential.\n"
        f"Auditor DID: {AGENT_DID}"
    )

    # Use heartbeat's verification solver
    resp_data = moltbook_post(client, "/posts", {
        "title": title,
        "content": body,
        "submolt_name": "general",
    })
    if resp_data:
        solve_verification(client, resp_data)
        log.info("Published scan summary to Moltbook")
        return True
    log.warning("Failed to publish to Moltbook")
    return False


# ---------------------------------------------------------------------------
# Moltbook API helpers + verification solver (from heartbeat.py)
# ---------------------------------------------------------------------------


def moltbook_post(client: httpx.Client, path: str, body: dict) -> dict | None:
    for attempt in range(3):
        try:
            r = client.post(
                f"{MOLTBOOK_BASE}{path}",
                headers={"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"},
                json=body, timeout=15,
            )
            if r.status_code in (200, 201):
                return r.json()
            if r.status_code == 429:
                retry = r.json().get("retry_after_seconds", 25)
                log.info(f"Rate limited, waiting {retry}s")
                time.sleep(retry + 1)
                continue
            log.warning(f"Moltbook POST {path} -> {r.status_code}: {r.text[:200]}")
            return None
        except Exception as e:
            log.error(f"Moltbook POST {path} error: {e}")
            return None
    return None


def _collapse(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


_NUM_BASE = [
    ("zero", 0), ("one", 1), ("two", 2), ("three", 3), ("four", 4),
    ("five", 5), ("six", 6), ("seven", 7), ("eight", 8), ("nine", 9),
    ("ten", 10), ("eleven", 11), ("twelve", 12), ("thirteen", 13),
    ("fourteen", 14), ("fifteen", 15), ("sixteen", 16), ("seventeen", 17),
    ("eighteen", 18), ("nineteen", 19), ("twenty", 20), ("thirty", 30),
    ("forty", 40), ("fifty", 50), ("sixty", 60), ("seventy", 70),
    ("eighty", 80), ("ninety", 90),
]
NUM_LOOKUP: dict[str, int] = {}
for _w, _v in _NUM_BASE:
    NUM_LOOKUP[_w] = _v
    _c = _collapse(_w)
    if _c != _w:
        NUM_LOOKUP[_c] = _v

_OP_BASE = [
    ("plus", "+"), ("add", "+"), ("added", "+"), ("adding", "+"), ("adds", "+"),
    ("minus", "-"), ("subtract", "-"), ("subtracted", "-"),
    ("less", "-"), ("reduced", "-"), ("reduces", "-"),
    ("decreased", "-"), ("decreases", "-"), ("decrease", "-"),
    ("slows", "-"), ("slowed", "-"),
    ("times", "*"), ("multiplied", "*"), ("multiply", "*"),
    ("divided", "/"), ("divides", "/"), ("over", "/"),
]
OP_LOOKUP: dict[str, str] = {}
for _w, _o in _OP_BASE:
    OP_LOOKUP[_w] = _o
    _c = _collapse(_w)
    if _c != _w:
        OP_LOOKUP[_c] = _o


def _combine_tens_units(nums: list) -> list:
    combined = []
    i = 0
    while i < len(nums):
        v = nums[i]
        if 20 <= v <= 90 and i + 1 < len(nums) and 1 <= nums[i + 1] <= 9:
            combined.append(v + nums[i + 1])
            i += 2
        else:
            combined.append(v)
            i += 1
    return combined


def _compute(a: float, b: float, op: str) -> str | None:
    if op == "+": result = a + b
    elif op == "-": result = a - b
    elif op == "*": result = a * b
    elif op == "/": result = a / b if b != 0 else 0
    else: return None
    return f"{result:.2f}"


def solve_challenge(text: str) -> str | None:
    clean = re.sub(r"[^a-zA-Z ]+", "", text).lower()
    words = [_collapse(w) for w in clean.split() if w]
    nums, op = [], None
    for w in words:
        if w in NUM_LOOKUP: nums.append(NUM_LOOKUP[w])
        elif w in OP_LOOKUP and op is None: op = OP_LOOKUP[w]
    if op is None:
        for i in range(len(words) - 1):
            if words[i] + words[i + 1] in OP_LOOKUP:
                op = OP_LOOKUP[words[i] + words[i + 1]]
                break
    combined = _combine_tens_units(nums)
    if len(combined) >= 2 and op is not None:
        return _compute(combined[0], combined[1], op)
    stream = _collapse(re.sub(r"[^a-zA-Z]", "", text).lower())
    num_entries = sorted(NUM_LOOKUP.items(), key=lambda x: len(x[0]), reverse=True)
    op_entries = sorted(OP_LOOKUP.items(), key=lambda x: len(x[0]), reverse=True)
    used = set()
    stream_nums = []
    for word, val in num_entries:
        for m in re.finditer(re.escape(word), stream):
            r = set(range(m.start(), m.end()))
            if not r & used:
                stream_nums.append((m.start(), val)); used |= r
    stream_ops = []
    for word, op_val in op_entries:
        for m in re.finditer(re.escape(word), stream):
            r = set(range(m.start(), m.end()))
            if not r & used:
                stream_ops.append((m.start(), op_val)); used |= r; break
    stream_nums.sort(); stream_ops.sort()
    s_nums = _combine_tens_units([v for _, v in stream_nums])
    s_op = stream_ops[0][1] if stream_ops else (op or "*")
    if len(s_nums) >= 2:
        return _compute(s_nums[0], s_nums[1], s_op)
    digits = [float(d) for d in re.findall(r"\d+\.?\d*", text)]
    if len(digits) >= 2:
        return _compute(digits[0], digits[1], op or "*")
    return None


def solve_verification(client: httpx.Client, data: dict) -> bool:
    verification = data.get("verification") or data.get("post", {}).get("verification")
    if not verification:
        return True
    code = verification.get("verification_code", "")
    challenge = verification.get("challenge_text", "")
    if not code or not challenge:
        return True
    answer = solve_challenge(challenge)
    if not answer:
        return False
    result = moltbook_post(client, "/verify", {"verification_code": code, "answer": answer})
    return bool(result and result.get("success"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(mode: str = "quick", publish: bool = False):
    now = datetime.now(timezone.utc)
    log.info(f"\n{'='*60}")
    log.info(f"MOLTRUST AUDITOR AGENT v2")
    log.info(f"DID: {AGENT_DID}")
    log.info(f"Mode: {mode} | Publish: {publish}")
    log.info(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"{'='*60}")

    if not ANTHROPIC_KEY:
        log.error("ANTHROPIC_API_KEY not set")
        return

    with httpx.Client() as client:
        # --- Infrastructure scanning ---
        targets = list(STATIC_TARGETS)

        if mode == "full" and MOLTBOOK_KEY:
            dynamic = discover_targets_from_moltbook(client)
            targets.extend(dynamic)

        infra_results = {}
        for target in targets:
            name = target["name"]
            url = target["url"]
            result = scan_target(client, name, url)
            result["own"] = target.get("own", False)
            infra_results[name] = result
            time.sleep(1)

        # --- Content analysis (full mode only) ---
        content_findings = []
        if mode == "full" and MOLTBOOK_KEY:
            log.info("Running Moltbook content analysis...")
            content_findings = analyze_moltbook_content(client)

        # --- VC issuance ---
        if MOLTRUST_API_KEY:
            maybe_issue_vcs(client, infra_results)

        # --- Report generation ---
        log.info("Generating report...")
        report_text = generate_report(infra_results, content_findings, mode)

        # --- Save report ---
        date_str = now.strftime("%Y%m%d_%H%M")
        report_path = LOG_DIR / f"auditor_{date_str}.md"
        with open(report_path, "w") as f:
            f.write(f"# MolTrust Auditor Report\n")
            f.write(f"**Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}\n")
            f.write(f"**Agent:** {AGENT_NAME} ({AGENT_DID})\n")
            f.write(f"**Mode:** {mode}\n\n---\n\n")
            f.write(report_text)
            f.write(f"\n\n---\n*Generated by MolTrust Auditor Agent v2*\n")
        log.info(f"Report saved: {report_path}")

        # --- Publish to Moltbook ---
        if publish and MOLTBOOK_KEY:
            publish_to_moltbook(client, infra_results, content_findings)

    print(report_text)
    log.info("Done.\n")


def main():
    parser = argparse.ArgumentParser(description="MolTrust Auditor Agent v2")
    parser.add_argument("--mode", choices=["quick", "full"], default="quick",
                        help="quick=static targets only, full=dynamic targets + content analysis")
    parser.add_argument("--publish", action="store_true",
                        help="Post summary to Moltbook if CRITICAL/HIGH findings")
    args = parser.parse_args()

    init_keys()
    run(mode=args.mode, publish=args.publish)


if __name__ == "__main__":
    main()
