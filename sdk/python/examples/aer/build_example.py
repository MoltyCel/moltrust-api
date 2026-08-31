"""Erzeugt `decision.json` und `trust.json` neben dieser Datei.

Laeuft gegen das installierte Paket, ohne das Repo: `pip install moltrust-enforce`, dann
`python3 build_example.py`. Zweimal ausgefuehrt entstehen byte-gleiche Dateien — die
Quellschluessel haengen deterministisch an ihrem Namen, und das Buendel sortiert nach
Inhalt statt nach Abfragereihenfolge.

Die drei Quellen sind Demo-Material. Ihre Schluessel gehoeren keiner echten Quelle und
duerfen ausserhalb dieses Beispiels nirgends auftauchen. Eine echte Quelle signiert selbst;
dass hier ein Skript signiert, ist der Unterschied zwischen einem Beispiel und einem
Evidence Source Adapter.
"""
import base64
import hashlib
import json
import pathlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce import (
    PAYLOAD_TYPE, action_digest, build_bundle, evidence_payload_bytes, f_ext,
    make_envelope, make_statement, pae,
)

HERE = pathlib.Path(__file__).resolve().parent

ACTION = {"verb": "transfer", "asset": "USDC", "chain": "base"}
TO = "0xABCDEF0123456789ABCDEF0123456789ABCDEF01"
WINDOW = ("2026-08-31T11:00:00Z", "2026-08-31T13:00:00Z")
DECISION_TIMESTAMP = "2026-08-31T12:00:00Z"

REVOCATION_Q = {"kind": "revocation", "subject": "aae:0f3a"}
JURISDICTION_Q = {"kind": "jurisdiction", "subject": TO}
FX_Q = {"kind": "fx", "pair": "USDC/EUR"}
SOURCES = ("revocation", "jurisdiction", "fx")


def demo_key(name):
    seed = hashlib.sha256(b"moltrust:aer-demo-source:" + name.encode("utf-8")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def source_id(name):
    return "did:moltrust:demo-" + name


def signed_item(name, query, value):
    statement = make_statement(source_id(name), query, value, WINDOW[0], WINDOW[1],
                               "nonce-" + name)
    signature = demo_key(name).sign(pae(PAYLOAD_TYPE, evidence_payload_bytes(statement)))
    return make_envelope(statement, [{"keyid": "key-" + name,
                                      "sig": base64.b64encode(signature).decode("ascii")}])


def main():
    mandate = {"mandate_version": "1.0", "grants": [{
        "action_binding": action_digest(ACTION),
        "disposition": "allow",
        "constraints": [
            {"type": "exact", "field": "to", "value": TO},
            {"type": "evidence_bool", "query": REVOCATION_Q, "expect": False},
            {"type": "evidence_enum", "query": JURISDICTION_Q, "values": ["CH", "DE"]},
            # 500 * 920000 = 460000000 <= 500 * 10**6 — 460 EUR unter 500 EUR Limit.
            {"type": "evidence_scaled_range", "field": "amount", "query": FX_Q,
             "rate_scale": 6, "lo": 0, "hi": 500},
        ]}]}
    transaction = {"action": ACTION, "to": TO, "amount": 500, "region": "CH"}

    items = [signed_item("revocation", REVOCATION_Q, False),
             signed_item("jurisdiction", JURISDICTION_Q, "CH"),
             signed_item("fx", FX_Q, 920000)]
    bundle = build_bundle(items, mandate, transaction, DECISION_TIMESTAMP)
    record = f_ext(mandate, transaction, bundle)

    trust = {"trust_list_version": 1, "sources": {
        source_id(n): {"keys": [{
            "algorithm": "ed25519", "keyid": "key-" + n,
            "public_key": base64.b64encode(
                demo_key(n).public_key().public_bytes_raw()).decode("ascii")}]}
        for n in SOURCES}}

    write(HERE / "decision.json", {"record": record, "bundle": bundle,
                                   "mandate": mandate, "transaction": transaction})
    write(HERE / "trust.json", trust)
    print(f"{record['verdict']} — bundle_commit {bundle['bundle_commit']}")


def write(path, payload):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
