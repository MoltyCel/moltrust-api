# Changelog — MolTrust API

Changes to the public HTTP contract of `api.moltrust.ch`, newest first. Each
entry names the date it went live, what a caller sees differently, and when a
deprecated field goes away.

## 2026-10-12 — `GET /identity/resolve/{did}`: the 404 is a DID resolution result

Applies to a well-formed `did:moltrust` identifier that nobody registered, for
example `did:moltrust:0000000000000000`. The status stays `404`, and a hit
(`200`, the DID document) is unchanged.

Two changes on the same day.

**1. #741, live since 2026-10-12 05:21:58 UTC.** The body moved from FastAPI's
`detail` envelope to the shape of a DID resolution result: `didDocument: null`,
the error in `didResolutionMetadata`, `didDocumentMetadata: {}`. The error was
the string `"notFound"`. The message and `register_hint` moved under
`didResolutionMetadata.moltrust`. `detail` was dropped.

**2. This change, live with its deploy.** `didResolutionMetadata.error` is an object instead of the
string, and `detail.error` comes back for 90 days.

DID Resolution v1, W3C Candidate Recommendation Draft, 1 October 2026,
section 4.4:

> If the input DID does not exist, return the following result:
> didResolutionMetadata: error object with type set to
> https://www.w3.org/ns/did#NOT_FOUND
> didDocument: null
> didDocumentMetadata: «[ ]»

Section 11:

> Implementers SHOULD use [RFC9457] to encode the error data structure. If
> [RFC9457] is used: The type value of the error object MUST be a URL. […]
> The title value SHOULD provide a short but specific human-readable string
> for the error. The detail value SHOULD provide a longer human-readable
> string for the error.

The string `"notFound"` appears in that draft only in the examples of section
4.2, and section 1.2 says examples are non-normative. The error object carries
`type`, `title` and `detail`. `status` is not set: section 11 does not name it,
and the HTTP status already carries it.

| | before #741 | #741 | now |
|---|---|---|---|
| status | 404 | 404 | 404 |
| error | `detail.error = "did_not_found"` | `didResolutionMetadata.error = "notFound"` | `didResolutionMetadata.error.type = "https://www.w3.org/ns/did#NOT_FOUND"` |
| `detail` | yes | no | `{"error": "did_not_found"}`, deprecated |
| message, `register_hint` | under `detail` | under `didResolutionMetadata.moltrust` | unchanged from #741 |
| `didDocument`, `didDocumentMetadata` | absent | `null`, `{}` | `null`, `{}` |

Response bodies for `did:moltrust:0000000000000000`, sha256:

- before #741 (up to `7e7aac1`):
  `d0906813b181c4c06a2f70b5a292c6da5a43a85f01dbf3a4b042e6df0fc7536e`,
  876 bytes. **Reconstructed, not captured live**: rendered from `app/main.py`
  and `app/register_hint.py` at `7e7aac1` through FastAPI's exception handler.
  The same rendering path reproduces the live #741 body byte for byte.
- #741 (from `a832ab0`):
  `c421d2fd9c84d5608fc729c7c4c6a1618fa93020984171333bd9fc0bc3d9ee54`,
  943 bytes, captured live on 2026-10-12 at 05:45:03 GMT.
- now: `a94ee4e17598c5333e6c5f6259fd335ca62705f4d0459c3e4575e866378dc95e`,
  1093 bytes, rendered from this change before deploy.

**Deprecated: `detail.error = "did_not_found"`, removed on 2027-01-10.** The
path is public and we cannot see who reads `detail`. Until then a miss carries
both forms. A client should read `didResolutionMetadata.error.type`. Only the
code returns under `detail`; the message and the register hint stay under
`didResolutionMetadata.moltrust`.
