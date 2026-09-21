import json, urllib.request, datetime, sys, time
BASE = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"
NEEDLES = ("moltrust", "moltguard", "0x380238347e58435f40b4da1f1a045a271d5838f5")

hits, offset, scanned, total, pages = [], 0, 0, None, 0
while True:
    url = f"{BASE}?limit=100&offset={offset}"
    for attempt in range(3):
        try:
            d = json.load(urllib.request.urlopen(url, timeout=30)); break
        except Exception as e:
            if attempt == 2:
                print("ABBRUCH bei offset", offset, "-", e); sys.exit(1)
            time.sleep(1.5)
    items = d.get("items", [])
    total = (d.get("pagination") or {}).get("total", total)
    scanned += len(items); pages += 1
    for it in items:
        blob = json.dumps(it).lower()
        if any(n in blob for n in NEEDLES):
            hits.append({
                "resource": it.get("resource"),
                "lastUpdated": it.get("lastUpdated"),
                "payTo": [a.get("payTo") for a in (it.get("accepts") or [])],
            })
    if not items or scanned >= (total or 0):
        break
    offset += 100

ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
print(f"[{ts} UTC] Seiten {pages} | gescannt {scanned} von {total} | Treffer {len(hits)}")
for h in hits[:15]:
    print("   ", json.dumps(h))
if scanned < (total or 0):
    print(f"   UNVOLLSTÄNDIG: nur {scanned}/{total} gesehen")
