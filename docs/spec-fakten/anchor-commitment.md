# Anchor Calldata Commitment — MolTrust Spec

**Status:** Draft 1 · **Created:** 2026-09-21 · **Chain:** Base (`eip155:8453`)
**Verified:** against live Base transactions, 2026-09-21 (see [Worked examples](#worked-examples))
**Defined by:** `app/provenance/anchor.py`

This defines how the 32 bytes in a MolTrust anchor transaction come into
existence: what a leaf is, how it is hashed, in which order leaves are taken,
how the tree is built, and how the root is written into calldata. It is written
so that a third party with no access to our systems can recompute the on-chain
value from the credential documents alone.

This is our own encoding, so this file carries the encoding itself rather than
facts about somebody else's spec.

## The transaction

An anchor is a **self-send with zero value**: the anchoring address sends 0 wei
to itself, and the entire payload lives in the transaction's `input` field. No
contract is called, no event is emitted, nothing is stored on-chain but the
calldata. The chain is used as a timestamped append-only log and nothing else.

| Field | Value |
|---|---|
| `to` | same as `from` |
| `value` | `0x0` |
| `chainId` | `8453` (Base mainnet) |
| `input` | the calldata string below, UTF-8 encoded |

Two addresses anchor, and which one signs tells you what kind of batch it is:

| Namespace | Signer | Environment variable |
|---|---|---|
| `MolTrust/VC/v1/` | `0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38` | `BASE_ANCHOR_ADDR` |
| `MolTrust/IPR/v1/` | `0x380238347e58435f40B4da1F1A045A271D5838F5` | `BASE_ADDR` |

Credential anchoring was given its own address so that running it dry cannot
stop the productive wallet. A verifier should treat the signer as a hint, not as
part of the commitment — the commitment is the calldata.

## The calldata

```
<prefix> "/" <root>
```

- `<prefix>` is `MolTrust/VC/v1` for credentials, `MolTrust/IPR/v1` for
  interaction proof records. Case-sensitive, one `l`, capital `T`.
- `<root>` is the Merkle root as **64 lowercase hex characters**, no `0x`.
- The whole string is ASCII and goes into `input` as raw UTF-8 bytes. There is
  no ABI encoding, no length prefix, no selector. The first four bytes of a VC
  anchor are `0x4d6f6c54` — the letters `MolT`, not a function selector.

A VC anchor is therefore always 79 bytes of calldata, an IPR anchor 80.

## The leaf

A leaf is the SHA-256 of a pipe-joined UTF-8 string. The digest is then used as
**32 raw bytes** inside the tree, although it is stored and transmitted as hex.

### Credentials (`MolTrust/VC/v1`)

```
leaf = SHA-256( cred_id | subject_did | credential_type | issued_at | proof_value )
```

joined by the single character `|` (U+007C), with no spaces and no trailing
separator. The five fields, in order:

| Field | Source | Serialization |
|---|---|---|
| `cred_id` | `credentials.id` | the integer in decimal, e.g. `1191` |
| `subject_did` | `credentials.subject_did` | as issued, e.g. `did:moltrust:d8bb033ed2224899` |
| `credential_type` | `credentials.credential_type` | e.g. `AgentTrustCredential` |
| `issued_at` | `credentials.issued_at` | ISO 8601, see below |
| `proof_value` | `credentials.proof_value` | the JWS detached signature; `""` if absent |

### Interaction proof records (`MolTrust/IPR/v1`)

```
leaf = SHA-256( output_hash | agent_did | produced_at | confidence )
```

| Field | Source | Serialization |
|---|---|---|
| `output_hash` | `interaction_proof_records.output_hash` | including its `sha256:` prefix |
| `agent_did` | `interaction_proof_records.agent_did` | as registered |
| `produced_at` | `interaction_proof_records.produced_at` | ISO 8601, see below |
| `confidence` | `interaction_proof_records.confidence` | Python `float` repr — `1.0`, never `1` |

### From a credential document to the leaf preimage

A holder verifying their own credential has the W3C document, not our table. The
mapping is not one-to-one, and two of the five fields need work:

| Preimage field | Where it is in the document |
|---|---|
| `cred_id` | `evidence[0].credentialId` |
| `subject_did` | `credentialSubject.id` |
| `credential_type` | the entry of `type[]` that is **not** `VerifiableCredential` |
| `issued_at` | `proof.created`, **with the UTC designator removed** |
| `proof_value` | `proof.proofValue` |

Three traps here, each of which silently produces a wrong leaf:

- **Read the timestamp from `proof.created`, not from the top level.** Two W3C
  data model versions are in circulation: `https://www.w3.org/ns/credentials/v2`
  calls the field `validFrom`, `https://www.w3.org/2018/credentials/v1` calls it
  `issuanceDate`, and both are in our published set (187 and 74 of 261 on
  2026-09-21). `proof.created` carries the same instant in every document and is
  the version-independent place to read it.
- **The UTC designator is not always `Z`.** Nearly every document renders
  `2026-09-21T12:11:59.792402Z`, but at least one renders
  `2026-04-02T14:10:19.710742+00:00`. The preimage carries neither suffix. Strip
  a trailing `Z` or a trailing `+00:00`; do not otherwise reformat or convert
  the timestamp, it is already UTC.
- **`credentialId` is new.** It was added on 2026-09-21 because the document did
  not previously carry it, which meant the leaf was an assertion of ours rather
  than something a holder could check. Every anchored credential in our store
  was backfilled, but a document a holder downloaded earlier and never re-fetched
  has an `evidence` block without the field; the anchor is still valid, and
  recomputing its leaf needs the id supplied separately.

### Timestamp serialization

This is the field that will cost an outside verifier the most time, so it is
spelled out.

Both timestamps are the output of Python's `datetime.isoformat()`, which means:

- date and time separated by `T`, never a space;
- microsecond precision, six digits;
- **when the microsecond component is exactly zero, `isoformat()` omits the
  fractional part entirely** — `2026-09-21T12:00:00`, not `…T12:00:00.000000`.
  No credential has hit this yet (0 of 263 on 2026-09-21), but a verifier that
  hardcodes six digits will eventually recompute the wrong leaf;
- `credentials.issued_at` is `timestamp without time zone` and carries **no
  offset suffix** — `2026-09-21T12:11:59.792402`. It is UTC by convention;
- `interaction_proof_records.produced_at` is `timestamp with time zone` and
  carries **`+00:00`** — `2026-05-24T12:50:41.223055+00:00`.

The two namespaces genuinely differ here. Copying a verifier from one to the
other without changing the suffix produces a leaf that never matches.

## The tree

Plain binary SHA-256 Merkle tree over the 32-byte leaves, bottom up:

1. **Ordering** is the batch's SQL order. Credentials: unanchored rows
   `ORDER BY issued_at ASC`, at most 200. IPRs: rows with
   `anchor_status = 'pending'` `ORDER BY created_at ASC`, at most 100. Leaf
   index 0 is the earliest.
2. **Pad the leaf level once, unconditionally**: if the number of leaves is odd,
   append a copy of the last leaf. This happens before anything else, so a
   one-leaf batch becomes two leaves and its root is `SHA-256(leaf ‖ leaf)`,
   never the leaf itself.
3. **Then reduce.** While the current level holds more than one node: if it is
   odd, append a copy of its last node; then replace it with the list of parents,
   where the **parent** of two nodes is `SHA-256(left ‖ right)` over the
   concatenated raw 32-byte digests. No domain separation, no sorting of the
   pair, no length prefix.
4. The single remaining node, hex-encoded lowercase, is the root.

Steps 2 and 3 are separate on purpose. Folding the leaf padding into the loop
condition — reducing *while* more than one node remains, padding inside — gives
the right answer for every batch in this system except the one-credential ones,
where it returns the bare leaf and no anchor matches. Batch `0x40d872fa…` is
exactly that case, and it is why the leaf level is padded outside the loop.

The duplicate created by padding is a real node of its level and shows up in
proofs as a node whose sibling is the node itself.

### Replaying a proof

A stored proof is `{leaf, path, root}` where each path element is
`{hash, position}` and `position` says on which side the sibling sits. Starting
from the leaf:

```
h = leaf
for step in path:
    h = SHA-256(step.hash ‖ h)  if step.position == "left"
    h = SHA-256(h ‖ step.hash)  if step.position == "right"
assert h == root
```

The root must then equal the 64 hex characters after the prefix in the calldata
of the named transaction. Both halves are required: a proof that replays to a
root nobody anchored proves nothing.

## Worked examples

Both were recomputed from the fields alone and compared against the live chain
on 2026-09-21.

### Credentials — three leaves, padded

Transaction
[`0xa5f77cb79c61ad7823d82c84e1c14d4322b6d4cf4951029f65696c18843947c4`](https://basescan.org/tx/0xa5f77cb79c61ad7823d82c84e1c14d4322b6d4cf4951029f65696c18843947c4),
block 51 604 928, three credentials. Odd count, so the padding rule is visible.

The three leaf preimages, in batch order (`proof_value` abbreviated — the full
128-character signature is in each credential's `proof.proofValue`):

```
1191|did:moltrust:d8bb033ed2224899|AgentTrustCredential|2026-09-21T12:11:59.792402|<sig>
1192|did:moltrust:cad78d76790d4a40|AgentTrustCredential|2026-09-21T12:33:58.784719|<sig>
1193|did:moltrust:147032513f28421f|AgentTrustCredential|2026-09-21T12:52:18.858979|<sig>
```

```
leaf[0] = bd782c2568457dd171630014937b667d8415586fb5a250dd354a2eb835a630f7
leaf[1] = 2e35f4aad10cb50470d5823b114fd111556bc8c91fa2cf8fa42e6a7007a14d18
leaf[2] = 9b6dc108ffd807e001d5fc9500257515f4da0975bac52e72c1c5e2d619697b50
leaf[3] = leaf[2]                                    (padding)

level 1:
  SHA-256(leaf[0] ‖ leaf[1]) = f90582cfc740d07d34fc6da4c4756e9b8ab8eb4d542a7284f50d613eb265093d
  SHA-256(leaf[2] ‖ leaf[2]) = 30f6cd9ebb0096346a01242fc109505dba5587aa5fbe105951e9559428ef6733

root:
  SHA-256(f90582… ‖ 30f6cd…) = fa5abbd0d3beaad1af343ea8d31b222b83bbde352b88443b89310933ff9ba658
```

Calldata:

```
MolTrust/VC/v1/fa5abbd0d3beaad1af343ea8d31b222b83bbde352b88443b89310933ff9ba658
```

as bytes, which is what `input` contains verbatim:

```
0x4d6f6c54727573742f56432f76312f6661356162626430643362656161643161
  6633343365613864333162323232623833626264653335326238383434336238
  393331303933336666396261363538
```

Credential 1193 is the padded one. Its stored proof reads
`[{hash: 9b6dc108…, position: right}, {hash: f90582cf…, position: left}]` — the
first sibling is its own leaf.

### Interaction proof records — two leaves

Transaction
[`0x142b8c1eff376fa69553246c181946e8a2abc9aeeb6d7f5a8bc5ed9c243af7ec`](https://basescan.org/tx/0x142b8c1eff376fa69553246c181946e8a2abc9aeeb6d7f5a8bc5ed9c243af7ec),
two records. First preimage:

```
sha256:884e8c474fa67313c11f202443eeda724776f170934d77a3abd6fb86ae7d5c4e|did:moltrust:b5e9021b277d443c|2026-05-24T12:50:41.223055+00:00|1.0
```

Even count, no padding: `root = SHA-256(leaf[0] ‖ leaf[1])` =
`e5684098d70e38a079e01557b435806ce800cb3fb92f6d0ee38a8b0895a1c282`, and the
calldata is `MolTrust/IPR/v1/e5684098…`.

Note `1.0` and the `+00:00`. Both differ from the credential example.

## Checking it yourself

`scripts/verify_anchor.py` is this document in executable form. It imports
nothing from this repository, needs no API key, and reads only a public Base RPC
endpoint:

```
python3 scripts/verify_anchor.py credential.json
```

It prints the leaf preimage, the recomputed leaf, the replayed root and the
calldata it found on chain, and exits non-zero if they disagree.

### Verification record

On 2026-09-21 a verifier written from this document alone — not from
`app/provenance/anchor.py` — was run against every anchored credential:

| Check | Result |
|---|---|
| Leaf recomputed from the document, path replayed, root compared to calldata | **261 of 261** across 15 transactions |
| Whole batch rebuilt from its documents, root compared to calldata | **15 of 15** batches |

Writing that verifier is what produced the `proof.created`, `+00:00` and
leaf-padding notes above. Each of those was a place where following the earlier
wording of this file gave a root the chain does not carry.

## What the anchor proves, and what it does not

**Proves:** that these exact field values existed, in this batch, no later than
the block that included the transaction. Change any character of any field and
the leaf changes, the root changes, and the on-chain string no longer matches.

**Does not prove:** that the statement in the credential is true, that the
signature over it is valid, or that the credential has not since been revoked.
The anchor binds a document to a point in time. Signature verification (the JWKS
at `https://moltrust.ch/.well-known/jwks.json`) and revocation are separate
checks, and all three have to pass.

**Does not prove absence.** Nothing on-chain enumerates what was *not* anchored.
A credential with no anchor is not thereby refuted; it is only unanchored.

## Caveats

- **Ties in the ordering column are not deterministic.** Two credentials with an
  identical `issued_at` could be ordered either way by Postgres, and the two
  orderings give different roots. This has not occurred (0 of 263 credentials
  share a timestamp on 2026-09-21), and it does not affect verification —
  a verifier replays the stored sibling path and never has to reconstruct the
  batch order. It would affect anyone trying to rebuild a whole batch from
  scratch.
- **The prefix is `MolTrust`, with one `l`.** `MoltTrust` appears in internal
  notes and is wrong; matching on it finds nothing.
- **`v1` is the version of this encoding**, not of the credential format. A
  change to the leaf preimage requires `v2` in the calldata, because a verifier
  has no other way to tell which rule applied.
