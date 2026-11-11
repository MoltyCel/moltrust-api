# agentproto Topic 2 — dialog, dialog context, participant, intermediary

Source statements only. What six documents say about four terms, and how each names the
state of authorization between two steps of a delegation. Each quote is verbatim with
section and line. No assessment is made here.

**Verified:** 2026-10-20; the AHU entries on 2026-10-21 against revision 03. All texts were
fetched with `curl` and hashed locally.

**Line numbers:** 1-based, in the plain-text file exactly as fetched, with page headers and
footers counted. A quote spanning a line break is joined with one space. A quote from a
requirements table joins the description cells of the rows it spans.

| Key | Document | Quotes and line numbers from | Fetched from | sha256 of that revision |
|---|---|---|---|---|
| UCR | draft-agentic-ai-usecases-requirements-04 | revision 04 only | <https://www.ietf.org/archive/id/draft-agentic-ai-usecases-requirements-04.txt> | `f91854d231919dd9f6a67b68be4ead50f54c645b39b2ce311607558d66f0dc51` |
| ROS | draft-rosenberg-agentproto-usecases-00 | revision 00 only | <https://www.ietf.org/archive/id/draft-rosenberg-agentproto-usecases-00.txt> | `219d3e53f6ae88d77b61a98e802352f1fc91cec4e6c700388cb4275bf3fadc1e` |
| KON | draft-konda-agentproto-evaluation-state-01 | revision 01 only | <https://www.ietf.org/archive/id/draft-konda-agentproto-evaluation-state-01.txt> | `bc91d25fe36176423f9a95e5d8842d167183c94cbd53411a6498cfb882457861` |
| AHU | draft-ahuja-agent-routing-policy-03 | revision 03 only | <https://www.ietf.org/archive/id/draft-ahuja-agent-routing-policy-03.txt> | `abdbcb6eeb387c2b0f49de4b67505d58b546b02a14c0f44f0182f3c14335fc7f` |
| AHU, previous | draft-ahuja-agent-routing-policy-02 | quoted until 2026-10-21, no longer | <https://www.ietf.org/archive/id/draft-ahuja-agent-routing-policy-02.txt> | `0ce3ce9c905d651657ab89a113597739bfc12ffa4494f6af1156847ee27cc86d` |
| CH | charter-ietf-agentproto-01 | revision 01 only | <https://www.ietf.org/charter/charter-ietf-agentproto-01.txt> | `d0bbcbf7f095f7deb1219b462bcfaa48b1513f5be354eac999d80873431071c8` |
| AAE | draft-kroehl-agentic-trust-aae-02 | revision 02 only | <https://www.ietf.org/archive/id/draft-kroehl-agentic-trust-aae-02.txt> | `08e202ecc06d245b287e60591098c22d8af480fc88cb455defc9e7612a473b4c` |

Every quote and every line number in this file comes from the revision named in the
table, and applies to that revision only. A later revision can move the lines or change the
wording. The sha256 identifies the exact text quoted, so each quote stays checkable against
it.

The charter hash is the one recorded in `agentproto-list-guidelines.md`. The AAE hash is
the one recorded in `aae.md`.

KON: Datatracker lists a revision 02 as of 2026-10-20, sha256
`8f943c98b62945fe8a7e138c59acd9dd24ca7b6147beac5c2af9aa2d2f81b313`. This file quotes -01,
and its KON line numbers apply to -01 only. In -02, §5.3 is word-for-word the same as in
-01 but its lines move: §5.3 starts at L483, where -01 has L460. The three "delegated
authority" passages, L18, L121 and L133, keep their line numbers in -02.

AHU: until 2026-10-21 this file quoted revision 02. Every AHU quote and line number now comes
from revision 03. Where revision 03 changed the wording of a passage quoted from revision 02,
the entry says so and gives the revision 02 text with its revision 02 line numbers.

## Occurrence counts

These are case-insensitive line counts for `dialog`, `participant` and `intermediar`
together.

| UCR | ROS | KON | AHU | CH | AAE |
|---|---|---|---|---|---|
| 75 | 3 | 24 | 4 | 19 | 0 |

## dialog

- **UCR** §2 L213–216, glossary entry: "*Dialog*: The correlated sequence of interactions
  between two or more participants (users, agents, and tools) over the course of a task. A
  dialog may persist across multiple individual message exchanges and network connections."
- **UCR** §3 Table 1, CMN-11, L359–368: "The protocol is required to support establishment,
  modification, and termination of a dialog, distinct from cancellation of a delegated
  subtask within the dialog."
- **ROS**: no definition, one occurrence.
  - §3.3.3 L1075–1076: "The end user might continue its dialog with AI Agent A".
  - The document otherwise says "conversation" and "session". For example, §3.3.4.2
    L1141: "For conversational sessions, messages will be sent back and forth".
- **KON**: no definition. §1 L120–121: "The decision may be about whether to route a dialog,
  invoke a tool, accept a delegated authority, resume a suspended task".
- **AHU**: no definition, one occurrence. §3 L204–206, on UCR Section 4: "adds authentication
  and authorization requirements, which it assigns to the OAuth and WIMSE work, and privacy
  requirements on dialog context." -02 had no occurrence.
- **CH** L5: "A dialog is the correlated sequence of interactions between two or more
  participants over the course of a task."
- **CH** L5: "User-to-agent, agent-to-agent, and agent-to-tool interactions create dialogs
  between the users, agents, and tools."
- **AAE**: no occurrence.

## dialog context

- **UCR** §2 L229–232, glossary entry: "*Dialog Context*: The identifiers and lifecycle
  state needed to correlate and maintain a dialog. It does not include the content
  exchanged with AI models, such as conversation memory, retrieved documents, or prompts."
- **UCR** §2 L234–236, glossary entry: "*Task Context*: The data that a participant
  maintains to execute a task, such as conversation history and intermediate results. Task
  context is not carried by the protocol."
- **UCR** §4.4.3 Table 5, B3-9, L958–962: "The protocol is required to propagate the dialog
  context across each hop of a delegation chain, so that participants can correlate
  interactions along the chain."
- **ROS**: the term does not occur. It uses "context transfer".
  - §3.3.4.1 L1097–1099: "When AI Agent A first connects to AI Agent Z, it will likely want
    to transfer context about the user, about the conversation, about itself".
  - Listed item, L1113: "The transcript of the conversation to date."
- **KON**: the term occurs only as a reference to UCR.
  - §8 L702–703: "Sections 2 and 2.1 of that document distinguish dialog context from task
    context, which is not carried by the protocol."
  - What KON itself carries between hops is "evaluation state". §5.3 L462–463: "Evaluation
    state can be carried between hops, recorded locally, or both."
- **AHU**: the term occurs once, in the §3 passage quoted under dialog, L204–206, without a
  definition.
  - §9 condition 2, L604–606: "established inside the authenticated relation that binds
    the two hops, together with the delegation context they share".
  - "Delegation context" is not defined in AHU.
- **CH** L5: "The dialog context consists of the identifiers and lifecycle state needed to
  correlate and maintain an agentic dialog. It does not include the content exchanged with
  AI models, such as conversation memory, retrieved documents, or prompts."
- **CH** L67, coordination with vCon: "on conversation representation and its relationship
  to dialog context."
- **AAE**: the term does not occur.
  - §3 L1025, field description: "delegator_did: REQUIRED in delegation context."
  - "Delegation context" is not defined in AAE.

## participant

- **UCR**: the glossary has no entry of its own. The members are listed inside the dialog
  entry, §2 L213–214: "two or more participants (users, agents, and tools)".
- **UCR** §2.1 L253–254: "Any participant, including an intermediary, can use dialog
  context to correlate and maintain a dialog without interpreting the task."
- **ROS** §2.3 L485–486: "AI Agent A is not a participant in, and has no access to, the
  communications with AI Agent Z after the transfer."
- **ROS** §3.3.4.2 L1141–1142: "amongst the participants (user A, AI Agent A and AI Agent
  Z)".
- **ROS** §2.3 L565–568 names the two roles: "We refer to the AI agent which makes the
  decision to involve a second AI Agent as the invoking AI agent. The AI agent which is
  then brought into the conversation using one of the topologies above, is called the
  invoked AI Agent."
- **KON**: the term is used without a definition.
  - §1 L119: "Agent protocol participants frequently need to decide before acting."
  - §4.1 L287–290: "The downstream participant starts an operation", "the upstream
    participant needs to know".
  - The deciding role is defined as "decision point", §2 L191–193: "The component that
    decides whether an agent protocol action, hop, tool invocation, delegation, routing
    step, or audit conclusion is accepted, refused, retried, forwarded, or recorded."
- **AHU**: the term is used without a definition. -02 had no occurrence.
  - §4 L294–295: "[AGENTICREQ] B1-8 and CMN-12 require a participant to be able to determine
    whether a check failed or could not be completed."
  - §4 L301–303, on KON: "its Section 4.2 states what a report carrying either reason needs
    to carry when it reaches a participant in another domain."
  - The roles it defines in §2: "Domain", L144: "A jurisdiction of policy ownership."
    "Relying party", L177–178: "A party evaluating a hop or a chain in order to accept,
    delegate, or refuse a task." -02 §2 L177 read "accept, forward, or refuse a task".
- **CH** L9: "a participant that understands the dialog context can correlate and maintain
  a dialog".
- **CH** L27: "enabling dialog continuity when a participant changes device or network
  attachment".
- **AAE**: no occurrence. The roles it defines in §1.2, L229–240, are Agent, Principal,
  Issuer and Relying Party.
  - L238–239: "*Relying Party:* The system or service that receives a request from an agent
    and evaluates the AAE to determine whether to process it."

## intermediary

- **UCR** §2 L173–175, under "Agent-to-Agent Communication": "brokered communication
  involves an intermediary agent or coordination service".
- **UCR** §2.1 L258–260: "An intermediary uses the dialog identifier to correlate the
  requests it receives with the requests it forwards. Neither function requires access to
  the task context."
- **UCR** §4 L420–422: "whether they interact directly or through intermediaries."
- **UCR** §2 L194–196, glossary entry for a related role: "*Mediator*: An entity that serves
  as a proxy or mediator for external tools, APIs, databases, or other resources that other
  agents require but cannot directly access."
- **UCR** §4.6.1 L1078–1080, on that role: "The mediator may also validate agent requests
  before invocation, checking whether the action being requested matches the authorization
  granted to the agent".
- **UCR** §4.4.3 B3-4, L918–919: "authorization scope of any intermediate agent in the
  chain."
- **ROS**: the term does not occur. Its forwarding role is the passthrough proxy, §2.3
  L543–549: "it acts as a proxy, relaying content from AI Agent Z to User A, and relaying
  content from User A to AI Agent Z. The AI Agent doesn't take action on the content, but
  it might store it for contextual history. In SIP terminology, this can be considered a
  "B2BUA"."
- **KON**: the term does not occur. Its placements in §5.1 include:
  - L397–398: "at a gateway that mediates traffic between agents, tools, or administrative
    domains".
  - L400: "as an MCP proxy between the agent and its tools".
- **AHU**: the term does not occur.
  - §9 L660–662: "A mediator that terminates a delegation and originates a new one emits a
    value it did not compare against the value it received. Condition 1 fails at that hop,
    and no chain-wide statement spans it."
  - "Mediator" is not defined in AHU.
- **CH** L17: "Dialog context may need to be propagated and remain coherent across multiple
  intermediaries, and trust boundaries over time."
- **CH** L27, first deliverable: "propagation of dialog context across *multiple*
  intermediaries, trust boundaries, and transformation of modalities".
- **CH** L35: "recovery from failures of network paths and intermediaries".
- **AAE**: the term does not occur. Its only "proxy" is outbound, §5.2 L1695: "through
  a proxy that constrains where those requests may go."

## Authorization state between two delegation steps

- **UCR** §4.4.1 L833–835: "Each agent in the chain may delegate further to other agents,
  and authorization scope is required to be progressively constrained at each hop."
- **UCR** B3-1, L873–880: "At each hop, the delegating agent is required to present a
  credential that does not exceed the authorization scope of the credential it received."
- **UCR** B3-3, L891–909: "transferable credentials that carry the original authorization
  constraints across all hops in the delegation chain."
- **UCR** B3-8, L944–948: "a receiving party to determine that the authority conveyed to it
  does not exceed the authority conveyed to the hop it received from."
- **UCR** glossary: no entry for this state. "*Delegation*", L186–187: "The act of an agent
  requesting another agent to execute a task on its behalf."
- **ROS**: no name for it.
  - §3.3.5 L1191–1193 names "Conveyed Identity": "The invoking AI agent authenticates the
    end user, and passes this identity in a secure fashion to the invoked AI Agent".
  - L1239–1242: "How then can a user grant permissions to an AI Agent to limit what tasks
    it can perform, when those tasks are not simply an enumerated set of APIs within a
    specific scope? This would need to be sorted out."
- **KON**: as an input to a decision, "delegated authority", §1 L133: "delegated authority
  and whether it was allowed to be delegated".
  - Its state model is "evaluation state" per input, §6 L572–590. The values are
    established-positive, established-negative, not-established, stale and not-applicable.
  - L594–595: "The state "not-established" is not a fourth authorization decision. It is
    state about the evaluation process."
- **AHU**: no authorization term.
  - §3 L196–201: "An authorization decision does not identify who holds a task after the
    permitting credential expired ... Policy over delegation is not a generalised form of
    authorization and does not subsume it."
  - What passes between hops is the attribute "value" received and emitted, §9 L598–599:
    "the comparison of the value emitted by h(i) against the value received by h(i)
    returns NO_BROADER".
  - The relation, §2 L173–175: "Attenuation relation: The relation a profile defines
    between two values of an attribute, under which one value is no broader than another."
  - "Hop", §2 L156–157: "A single delegation, together with the domains on either side of
    it."
- **CH** L15: "users who delegated authority to them", "scoping of agent permissions to a
  subset of user permissions".
- **CH** L19: "delegated authorization across AI agent chains".
- **AAE**: the "mandate" of a "delegated AAE" against its "parent AAE".
  - §3 L985: "An agent may act under a mandate delegated from another agent."
  - L1076–1078: "Delegated AAEs MUST be strictly subordinate to their parent AAE. For the
    purposes of this document, "equal to or more restrictive" is defined per element".
  - The depth carried between steps, L1046–1048: "The effective delegation depth of a root
    AAE is 0; the effective delegation depth of a delegated AAE is its delegation.depth."
  - L1134–1136: "If a relying party cannot determine whether a delegated element is equal
    to or more restrictive than the corresponding parent element, the delegated AAE MUST be
    rejected."

## Forwarding and narrowing in the source text

These passages are where a source mentions forwarding, or narrowing, or both, in a
delegation step.

- **UCR** §2.1 L258–260, quoted above under intermediary: correlation of "the requests it
  receives with the requests it forwards".
- **AHU**: the word "forward" does not occur in revision 03. Revision 02 used it in the three
  passages below, and revision 03 rewords each. Changes Since -02, L957–958: "Editorial changes to terms describing what
  a domain does with a task."
  - §2 L177–178, relying party: "accept, delegate, or refuse a task". -02 §2 L177:
    "accept, forward, or refuse a task".
  - §5.3 L382–385: "A tag is part of the delegation it is attached to, and its meaning is
    defined only by the agreement. A domain that does not hold the agreement MUST NOT infer
    meaning from it." -02 §5.3 L372–374: "A receiving domain that does not hold the agreement
    MUST forward the tag unaltered if it forwards the delegation at all".
  - §7 L483–485: "A profile whose relation is not reflexive MUST state that an unchanged value
    will be refused. Under such a profile a delegation that passes on its value unchanged is
    refused". -02 §7 L471–472: "A domain forwarding a task without narrowing it under such a
    profile is refused".
- **AHU** §5.4 L418–420: "A receiving domain MUST NOT emit an onward delegation whose value
  returns BROADER against the value it received. A receiving domain MAY emit an onward
  delegation whose value returns NO_BROADER."
- **AHU** §5.4 L422–425, new in revision 03: "Two different steps return NO_BROADER: a delegation that
  passes on the value it received unchanged, and one that narrows it. Under a reflexive
  attenuation relation both do. Under a relation that is not reflexive only a narrowing
  delegation does, as Section 7 states."
  - Changes Since -02, L934–935: "Distinguished a delegation that passes on its value
    unchanged from one that narrows it."
- **AHU** §9 L584–585: "The holder has received no delegation and has narrowed nothing".
- **KON** §2 L191–193, decision point: "accepted, refused, retried, forwarded, or recorded".
- **KON** §5.3 L496–498: "This document does not require every hop to forward all of that
  state. It requires that a hop not turn "I did not establish the input" into "the input
  was negative" before forwarding or recording."
- **ROS** §2.3 L543–549: the passthrough proxy, quoted above.
- **AAE** §3 L1140–1141, on [PEDIGREE]: "how a delegating system mints and narrows the
  authority it hands on."

## Cross-references between the sources

- **AHU cites UCR-04.** Revision 03 §8 L568–570: "B3-8 requires a receiving party to be able to
  determine that the authority conveyed to it does not exceed the authority conveyed to the hop
  it received from". In UCR-04 that text is B3-8, L944–948.
  - The reference entry is at L845–850: "draft-agentic-ai-usecases-requirements-04".
  - -02 cited UCR-02 and gave the same text as "B3-12", §8 L542–544, reference entry
    L820–825.
- **AHU §3 restates UCR Section 4.** Revision 03 L203–206: "[AGENTICREQ] Section 4 states that crossing
  an administrative boundary adds authentication and authorization requirements, which it
  assigns to the OAuth and WIMSE work, and privacy requirements on dialog context." UCR-04 §4
  L415–417 reads: "Crossing an administrative boundary adds authentication and authorization
  requirements, which map to the OAuth and WIMSE work".
  - -02 §3 L203–204 read: "adds no new protocol requirement beyond authentication and
    authorization".
- **AHU cites KON-02.** Revision 03 §4 L300–301: "[KONDA] generalizes the distinction between the two
  reasons defined in [BCR] to evaluation state at any decision point". The reference entry,
  L915–919, names revision 02, L917: "agentproto-evaluation-state-02". This file quotes KON-01; see the note
  under the table. -02 did not cite KON.
- **KON cites UCR-03.** The reference entry is at L959–962. KON §8 L682 places B1-6 and
  B1-7 in "Section 4.2.3"; in UCR-04 they are at L691–707, inside §4.2.3.
- **KON §10 cites AAE §6.1.** L820–823: "Its Section 6.1 defines PERMIT, PENDING, and DENY
  verdicts, and says a relying party that cannot evaluate MUST deny." AAE-02 §6.1 L1743–1762
  has the three verdicts and, at L1761, "A relying party that cannot evaluate MUST deny".
- **AHU and KON both cite ROS Section 3.3.2.** That section is "Routing and Connection",
  ROS L920.
  - KON §5.2 L439–441: "the cross-administrative-domain variant of the delegation case in
    Section 3.3.2".
  - AHU §3 L215–216: "treats routing between agents as the invoking agent's decision".
