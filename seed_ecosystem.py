#!/usr/bin/env python3
"""MolTrust Ecosystem Seed Script — creates showcase agents with ratings and credentials"""

import requests
import json
import time

API = "https://api.moltrust.ch"
KEY = "mt_test_key_2026"
HEADERS = {"X-API-Key": KEY, "Content-Type": "application/json"}

# --- Showcase Agents ---
AGENTS = [
    {"name": "Scout", "role": "Trend Intelligence", "desc": "Monitors AI agent ecosystem trends, ERC-8004 developments, and trust infrastructure news"},
    {"name": "Herald", "role": "Content Distribution", "desc": "Generates and publishes trust ecosystem insights to social channels"},
    {"name": "Auditor", "role": "Security Analyst", "desc": "Performs automated vulnerability scans and penetration testing on MolTrust infrastructure"},
    {"name": "Operator", "role": "Infrastructure Monitor", "desc": "24/7 health monitoring, uptime checks, and automated incident response"},
    {"name": "CodeReviewBot", "role": "Code Quality", "desc": "Reviews pull requests for security vulnerabilities, style compliance, and test coverage"},
    {"name": "DataPipelineAgent", "role": "Data Processing", "desc": "ETL pipeline management, data validation, and anomaly detection for analytics workflows"},
    {"name": "TranslatorAgent", "role": "Multilingual NLP", "desc": "Real-time translation and localization for agent-to-agent cross-language communication"},
    {"name": "ComplianceChecker", "role": "Regulatory Compliance", "desc": "Validates agent behavior against GDPR, SOC2, and industry-specific regulatory frameworks"},
]

# --- Cross-rating matrix (realistic, not all 5s) ---
# Format: (rater_index, target_index, score)
RATINGS = [
    (0, 1, 5), (0, 2, 4), (0, 3, 4), (0, 4, 5), (0, 6, 4),
    (1, 0, 5), (1, 3, 4), (1, 5, 3), (1, 7, 4),
    (2, 0, 4), (2, 3, 5), (2, 4, 5), (2, 7, 5),
    (3, 0, 4), (3, 1, 4), (3, 2, 5), (3, 5, 4), (3, 6, 3),
    (4, 0, 3), (4, 2, 5), (4, 5, 4), (4, 7, 4),
    (5, 3, 4), (5, 4, 4), (5, 6, 5), (5, 7, 3),
    (6, 0, 4), (6, 1, 5), (6, 5, 4),
    (7, 2, 5), (7, 4, 4), (7, 6, 4), (7, 0, 5),
]

def register_agent(name):
    r = requests.post(f"{API}/identity/register", headers=HEADERS, json={"name": name})
    data = r.json()
    print(f"  Registered: {name} -> {data.get('did', 'ERROR')}")
    return data.get("did")

def rate_agent(from_did, to_did, score):
    r = requests.post(f"{API}/reputation/rate", headers=HEADERS, json={
        "from_did": from_did, "to_did": to_did, "score": score
    })
    return r.json()

def issue_credential(subject_did):
    r = requests.post(f"{API}/credentials/issue", headers=HEADERS, json={
        "subject_did": subject_did
    })
    return r.json()

def get_reputation(did):
    r = requests.get(f"{API}/reputation/query/{did}", headers=HEADERS)
    return r.json()

def main():
    print("=" * 60)
    print("MolTrust Ecosystem Seed Script")
    print("=" * 60)

    # Step 1: Register agents
    print("\n[1/4] Registering showcase agents...")
    dids = []
    for agent in AGENTS:
        did = register_agent(agent["name"])
        dids.append(did)
        time.sleep(0.3)

    # Step 2: Cross-ratings
    print(f"\n[2/4] Applying {len(RATINGS)} cross-ratings...")
    for rater_idx, target_idx, score in RATINGS:
        if dids[rater_idx] and dids[target_idx]:
            result = rate_agent(dids[rater_idx], dids[target_idx], score)
            rater_name = AGENTS[rater_idx]["name"]
            target_name = AGENTS[target_idx]["name"]
            print(f"  {rater_name} -> {target_name}: {score}/5")
            time.sleep(0.2)

    # Step 3: Issue credentials
    print("\n[3/4] Issuing Verifiable Credentials...")
    for i, did in enumerate(dids):
        if did:
            vc = issue_credential(did)
            print(f"  VC for {AGENTS[i]['name']}: {vc.get('type', 'issued')}")
            time.sleep(0.2)

    # Step 4: Show results
    print("\n[4/4] Final reputation scores:")
    print("-" * 50)
    for i, did in enumerate(dids):
        if did:
            rep = get_reputation(did)
            score = rep.get("score", 0)
            ratings = rep.get("total_ratings", 0)
            name = AGENTS[i]["name"]
            role = AGENTS[i]["role"]
            stars = "*" * round(score) if score else "-"
            print(f"  {name:20s} {role:25s} {score:.1f}/5 ({ratings} ratings) {stars}")

    # Save registry
    registry = []
    for i, did in enumerate(dids):
        if did:
            rep = get_reputation(did)
            registry.append({
                "did": did,
                "name": AGENTS[i]["name"],
                "role": AGENTS[i]["role"],
                "description": AGENTS[i]["desc"],
                "score": rep.get("score", 0),
                "total_ratings": rep.get("total_ratings", 0),
            })

    with open("/home/moltstack/moltstack/agents/registry.json", "w") as f:
        json.dump(registry, f, indent=2)
    print(f"\nRegistry saved to ~/moltstack/agents/registry.json")
    print(f"Total: {len(registry)} agents, {len(RATINGS)} ratings, {len(registry)} credentials")
    print("\nDone!")

if __name__ == "__main__":
    main()
