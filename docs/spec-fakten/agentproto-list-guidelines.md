# IETF agentproto — mailing list guidelines (CONTRIBUTING.md)

**Source:** <https://github.com/ietf-wg-agentproto/.github/blob/main/CONTRIBUTING.md>
at commit `722c1c1727d3ec64f5828dfe0f391220ba9bb4ac` (2026-10-10, "Update discussion
guidelines for chartered working group"), file sha256
`7dac281b3d40fb07f367aba1fceecb73c9fbe1222dd939bc1db314858dcc3cdf`.
**Read with it:** the charter, `charter-ietf-agentproto-01` (Datatracker state
`approved`, 2026-10-08), <https://www.ietf.org/charter/charter-ietf-agentproto-01.txt>,
sha256 `d0bbcbf7f095f7deb1219b462bcfaa48b1513f5be354eac999d80873431071c8`; and
`profile/README.md` in the same repo.
**Verified:** 2026-10-19 via `gh api` and `curl`. Re-check before any post: the file
went from first version to the current one within one day (commits `4c15be1`,
`722c1c1`).

List: `agentproto@ietf.org`, archive <https://mailarchive.ietf.org/arch/browse/agentproto>.
Chairs: `agentproto-chairs@ietf.org`.

## Section map

| § | Title | Binding? |
|---|---|---|
| 1 | Scope and agenda | condition of posting |
| 2 | Conduct | condition of posting |
| 3 | Etiquette | advice |
| 4 | AI-assisted posts | condition of posting |
| 5 | What "AI slop" means | (definition, not listed in either group) |
| 6 | Simplified Technical English | advice |
| 7 | Sustainability | advice |
| 8 | Governing documents | reference list |

The file says it supplements IETF procedures, and that those govern on conflict.

## What is on-topic

- §1: "Posts must relate to the chartered work and respect the chairs' directions on
  discussion topics and timing." The chairs "defer or redirect off-topic discussion"
  (RFC 2418 §3.1, §3.3, §6.1).
- The chartered work, in the file's words: "agentic dialog management, a reference
  architecture, and supporting use cases and requirements". Dialog context is
  "identifiers and lifecycle state, excluding prompts and conversation memory". The
  work "uses existing IETF protocols and reuses security building blocks".
- Substantive discussion and proposed decisions from meetings, GitHub and Slack belong
  on the list. The list is "the primary forum for working group discussion and
  confirmation of consensus".
- Proposed scope changes go to the chairs; expanding scope needs rechartering
  (RFC 2418 §5).

The three charter deliverables, from the charter text:

1. Agentic Dialog Management Protocol (Standards Track): dialog correlation, lifecycle
   (establishment, modification, termination, revocation of propagation relationships),
   recovery from path or intermediary failure, transport bindings over existing IETF
   protocols (HTTP, QUIC, WebTransport, WebRTC, MoQ), evaluation criteria for choosing
   bindings, privacy analysis of persistent dialog identifiers.
2. Reference Architecture (Informational): terms, and the functional blocks the protocol
   assumes, reusing identity, authentication, authorization and encryption building
   blocks "rather than defining new ones".
3. Use Cases and Requirements (Informational): use cases, requirements, deployment
   models.

## What is off-topic

- Anything outside the charter. The charter's explicit out-of-scope list: agent
  implementation details (models, backend infrastructure, reasoning, tool business
  logic); agent decision-making or planning semantics; agent behavioural security
  (e.g. hallucination); human-to-agent UI and rendering; **audit data structures and
  their production, validation or consumption** ("though dialog identifiers may be used
  in auditing").
- Changes to MCP or A2A themselves: the charter puts those with their maintainers.
- Changes to protocols owned by other active WGs: raised with those WGs (security
  coordination names OAuth, webbotauth, WIMSE; evidence and transparency names SCITT,
  RATS).
- Discussion of the AI policy itself (§4.4). It goes to `ietf@ietf.org`, thread
  "Dealing with LLMs in IETF discussions", or to `mod-discuss@ietf.org` under RFC 9945.
  Comments on the guidelines document go to the chairs.

## AI-assisted posts (§4, condition of posting)

1. No automatic posting: an AI tool or agent does not post. A human reviews and approves
   every message and is accountable for it.
2. Edit generated text down before sending. STE recommended (§6: sentences under 25
   words, one idea per sentence, active voice, same word for the same concept).
3. The chairs moderate off-agenda AI threads and say so on-list and to the sender.
   Contest with the chairs, then the responsible AD (RFC 2418 §3.4), RFC 9945 §4.
5. The chairs accept false positives and correct them on-list.

§5 markers of "AI slop": length or headings out of proportion to the point; empty praise;
guessing what an ambiguous message meant instead of asking; drifting toward agreement
or over-hedging; wrong details, including citations that do not say what is claimed; a
style that does not match the sender's other posts; near-identical replies, or replying
to every message in a thread.

## Answers to the three questions

**Which topics did the chairs ask for?** The file names no specific topics and no call
for input. It refers to "the agenda managed by the working group chairs" and to their
"directions on discussion topics and timing". Topics come from the charter and from
the chairs' messages on the list. This file does not show any such messages; check the
archive.

**May individual drafts be discussed there?** The file does not mention individual
drafts, adoption calls, or the `draft-<name>-…` naming. The test it gives is "relate to
the chartered work".

**Where do contributions go instead?**
- Document text: GitHub issues and pull requests in the WG repos (`profile/README.md`),
  with substantive points and decisions carried to the list.
- Scope changes: the chairs, then rechartering.
- AI-policy discussion: `ietf@ietf.org` or `mod-discuss@ietf.org`. Comments on the
  guidelines themselves: `agentproto-chairs@ietf.org`.
- Changes to another WG's protocol: that WG. Changes to MCP or A2A: their maintainers.
- Off-topic posts: the file says only that the chairs "defer or redirect" them.

## Attribution errors to avoid

- The file is the list guidelines (title "`agentproto` Mailing List Discussion
  Guidelines"), not a contribution guide for drafts. Do not cite it for adoption or
  draft procedure.
- §5 is neither condition nor advice. Only §§1, 2, 4 are conditions of posting.
- The charter .txt still carries the heading "Proposed Charter". Datatracker shows rev
  `01` as `approved`. Cite the Datatracker charter page, not the .txt heading.
