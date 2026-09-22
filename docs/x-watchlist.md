# X accounts with a special status

Accounts where something about the relationship changes what our tooling can or
should do. Kept here rather than in a comment, because the next person to wonder
why a handle is missing from `config/reply_targets.json` will look for a reason
and should find one.

| handle | status | since | what it means |
|---|---|---|---|
| [@steipete](https://x.com/steipete) | blocked | 2026-09-22 | A block exists between this account and @moltrust. The API refuses to add him to a list: `403 — You cannot add a member that is blocking you or that you have blocked.` Which direction the block runs is not visible through the API. |

## @steipete

Approved for the targets list on 22 September as part of the OpenClaw and
Moltbook surroundings, and then not addable. He is absent from
`config/reply_targets.json`, so the radar neither sees his posts through the
list nor ranks them if they arrive through search.

A reply would not reach him anyway, so nothing is lost operationally. What is
worth knowing is that the block exists at all, given the overlap between that
ecosystem and ours — `@openclaw` covers the same ground and is in the list.

If it is lifted, adding him back is one API call plus a line in the config; the
handle is already recorded in `~/Downloads/x-targets.md` under group B.
