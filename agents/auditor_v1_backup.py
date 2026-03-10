"""MolTrust Auditor Agent - Scans public agent infrastructure"""

import os, datetime, httpx, time, json

AGENT_DID = "did:moltrust:b64714929fc44277"
AGENT_NAME = "MolTrust Auditor"
API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LOG_DIR = os.path.expanduser("~/moltstack/logs")

TARGETS = [
    {"name": "MolTrust API", "url": "https://api.moltrust.ch", "own": True},
    {"name": "Moltbook API", "url": "https://api.moltbook.com", "own": False},
    {"name": "Moltbook Web", "url": "https://moltbook.com", "own": False},
]

CHECKS = [
    {"path": "/.env", "risk": "SECRET_LEAK", "desc": "Environment file exposed"},
    {"path": "/.git/config", "risk": "SOURCE_LEAK", "desc": "Git config exposed"},
    {"path": "/admin", "risk": "ADMIN_PANEL", "desc": "Admin panel accessible"},
    {"path": "/.well-known/did.json", "risk": "INFO", "desc": "DID document present"},
    {"path": "/health", "risk": "INFO", "desc": "Health endpoint"},
    {"path": "/.well-known/security.txt", "risk": "INFO", "desc": "Security contact"},
    {"path": "/robots.txt", "risk": "INFO", "desc": "Robots.txt"},
]


def check_headers(url):
    findings = []
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True)
        h = resp.headers
        if "strict-transport-security" not in h:
            findings.append({"severity": "MEDIUM", "issue": "Missing HSTS header"})
        if "x-frame-options" not in h and "content-security-policy" not in h:
            findings.append({"severity": "LOW", "issue": "Missing clickjacking protection"})
        if "x-content-type-options" not in h:
            findings.append({"severity": "LOW", "issue": "Missing X-Content-Type-Options"})
        if "server" in h:
            findings.append({"severity": "LOW", "issue": "Server header exposed: " + h["server"]})
        body = resp.text[:5000]
        for leak in ["sk-ant-", "sk-", "xprv", "AKIA", "BEGIN PRIVATE KEY"]:
            if leak in body:
                findings.append({"severity": "CRITICAL", "issue": "Possible secret: " + leak})
        if not findings:
            findings.append({"severity": "OK", "issue": "Headers look good"})
    except Exception as e:
        findings.append({"severity": "ERROR", "issue": str(e)})
    return findings


def check_paths(base_url):
    findings = []
    for check in CHECKS:
        try:
            url = base_url.rstrip("/") + check["path"]
            resp = httpx.get(url, timeout=8.0, follow_redirects=False)
            status = resp.status_code
            if check["risk"] == "INFO":
                findings.append({"severity": "INFO", "issue": check["path"] + " -> " + str(status)})
            elif status == 200:
                sev = "CRITICAL" if check["risk"] == "SECRET_LEAK" else "HIGH"
                findings.append({"severity": sev, "issue": check["path"] + " -> EXPOSED!"})
            else:
                findings.append({"severity": "OK", "issue": check["path"] + " -> " + str(status) + " blocked"})
        except Exception as e:
            findings.append({"severity": "ERROR", "issue": check["path"] + " -> " + str(e)})
        time.sleep(1)
    return findings


def check_ssl(url):
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True)
        if resp.url.scheme == "https":
            return [{"severity": "OK", "issue": "SSL/TLS active"}]
        return [{"severity": "HIGH", "issue": "No SSL/TLS"}]
    except Exception as e:
        return [{"severity": "ERROR", "issue": str(e)}]


def generate_report(results):
    prompt = "You are the MolTrust Auditor Agent (DID: " + AGENT_DID + ")."
    prompt += " You scanned public agent infrastructure. Generate a security report."
    prompt += " Scan results: " + json.dumps(results, indent=2)
    prompt += " Write: 1. EXECUTIVE SUMMARY 2. CRITICAL FINDINGS 3. RECOMMENDATIONS"
    prompt += " 4. MOLTRUST STATUS 5. CONTENT ANGLE for a thought leadership post."
    prompt += " Be factual and professional."
    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 2000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            return "\n".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        return "Report failed: " + str(resp.status_code)
    except Exception as e:
        return "Report failed: " + str(e)


def run():
    now = datetime.datetime.now(datetime.UTC)
    print("\n" + "=" * 60)
    print("MOLTRUST AUDITOR AGENT")
    print("DID: " + AGENT_DID)
    print("Time: " + now.strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 60 + "\n")

    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return

    results = {}
    for target in TARGETS:
        name = target["name"]
        url = target["url"]
        print("Scanning: " + name + " (" + url + ")...")
        results[name] = {
            "url": url, "own": target["own"],
            "ssl": check_ssl(url),
            "headers": check_headers(url),
            "paths": check_paths(url),
        }
        c = sum(1 for f in results[name]["headers"] + results[name]["paths"] if f["severity"] == "CRITICAL")
        h = sum(1 for f in results[name]["headers"] + results[name]["paths"] if f["severity"] == "HIGH")
        print("  -> " + str(c) + " critical, " + str(h) + " high\n")
        time.sleep(2)

    print("Generating report...\n")
    time.sleep(3)
    report_text = generate_report(results)

    date_str = now.strftime("%Y%m%d_%H%M")
    report_path = os.path.join(LOG_DIR, "auditor_" + date_str + ".md")
    with open(report_path, "w") as f:
        f.write("# MolTrust Auditor Report\n")
        f.write("**Date:** " + now.strftime("%Y-%m-%d %H:%M UTC") + "\n")
        f.write("**Agent:** " + AGENT_NAME + " (" + AGENT_DID + ")\n\n---\n\n")
        f.write(report_text)
        f.write("\n\n---\n*Generated by MolTrust Auditor Agent*\n")

    print(report_text)
    print("\nReport saved: " + report_path)


if __name__ == "__main__":
    run()
