# Applying to X for automated replies

Read-only lookup, 22 September 2026. **Nothing has been submitted**, and this
file does not propose submitting anything; it records what the route is so the
decision can be made on facts rather than on a guess.

## The two things that are easy to confuse

They are not the same restriction and one does not obviously lift the other.

| | what it is | who lifts it |
|---|---|---|
| **The 403** | `You can only reply to or quote posts where you are mentioned or are the author.` A technical limit on `POST /2/tweets`, introduced February 2026, on every tier. | unknown — see below |
| **The policy** | X's automation rules require prior written approval before deploying AI-generated replies at all, approved or not, endpoint or not. | X, via the Policy Support form |

We are currently compliant with the second by construction: a list draft is
posted by a human from the intent link, which is not an automated reply. The
mention path is automated, and a mention is the one case the automation rules
allow without approval — "only if user engaged first, max 1 reply per
interaction" — which is exactly what the radar does.

**Whether an approval also lifts the 403 is not stated anywhere we could
find.** X's developer guidelines describe the approval as a policy permission,
not an API capability. It is possible to be approved and still get the 403.
That uncertainty is the main argument for asking before building anything on
the assumption.

## The route

**Policy Support form: <https://help.x.com/forms/platform>**

Named by X's own developer guidelines (<https://docs.x.com/developer-guidelines>,
fetched 22.09.2026) under *AI-Generated Content & Replies*:

> Requires prior approval from X before deployment.
> Must still follow all rules (no unsolicited mentions, properly labeled).
> Contact X via the Policy Support form before launching.
> Deploying AI-generated replies without approval is a violation, even if the
> content itself is helpful.

Two caveats on that link, both worth knowing before anyone spends a morning on
it:

- **We could not open it ourselves.** `help.x.com` answers every non-browser
  client with 403, including pages that certainly exist, so the 403 is not
  evidence about the form. It has to be opened in a browser to see what it
  asks for. The URL itself is from X's own documentation, not from a third
  party.
- **The other named channel does not apply to us.** For auto-response
  campaigns X's help pages say to "reach out to your account or partner
  manager". We have neither; the $8 Premium tier does not come with one.

Developers on X's own forum have been asking since at least early 2026 for a
developer-portal intake for exactly this review and report not finding one, so
the Policy Support form appears to be the whole of it.

## What an application would have to say

From the rules as written, an application has to cover:

1. **What the bot does**, concretely — that it drafts, that a human approves
   each draft, that it answers only accounts that engaged with us.
2. **One reply per interaction.** The radar already caps at 8 a day overall and
   marks each target seen, so it cannot answer the same post twice.
3. **Labeling.** @moltrust is an automated account in X's sense for the mention
   path; the profile should say so if replies are ever automated at scale.
4. **No unsolicited mentions.** We never initiate; the mention path is by
   definition a response.
5. **What the replies contain** — every draft carries a figure or a named
   specification and cites the page it came from, which is a better answer to
   "is this spam" than most applicants can give.

## Recommendation

Not now. The current shape works without it: mentions are permitted, list
drafts go out by hand, and the measurement is the same either way. Ask when
there is a reason — a mention volume that makes the manual step expensive, or a
plan that actually needs API replies to third parties — and ask the 403
question explicitly in the application, because that is the part nobody has
answered in public.

## Sources

- <https://docs.x.com/developer-guidelines> — the approval requirement and the
  form, fetched live 22.09.2026.
- <https://help.x.com/en/rules-and-policies/x-automation> — the automation
  rules themselves. Not fetchable by us (403 to every non-browser client).
- <https://help.x.com/forms/platform> — the form. Same 403; unopened.
