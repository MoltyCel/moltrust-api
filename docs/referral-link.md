# The referral link

How a registration names the agent that brought it (ops KM2). The counting rule
is in `app/referral.py`; this page covers only the way in.

## The path

1. A registered agent hands out `https://api.moltrust.ch/join?ref=<its DID>`.
   The registration response already carries it twice: in the `badge` text
   ("Register with this agent as referrer: …") and in the `X-MolTrust-Verify`
   header.
2. `GET /join` checks the DID before passing it on (`app/main.py`, `join_redirect`):
   - it must have the `did:moltrust:` shape;
   - it must be registered here and not revoked, looked up with
     `referral.known_referrer`, the same query both registration paths use.

   If it passes, the redirect goes to
   `https://moltrust.ch/for-agents.html?referrer=<DID>`. Anything else goes to
   the same page without a parameter. A database that cannot be asked counts as
   "anything else".
3. `for-agents.html` writes the DID into both registration commands as
   `referrer_did`: `POST /identity/register-pop` (keyless) and
   `POST /identity/register` (with an API key).
4. The registration records the pair in `agent_referrals`. That row is what the
   referral figures and the cohort report count.

The home page does the same with `https://moltrust.ch/?ref=did:moltrust:…`: it
shows a link to `for-agents.html?referrer=<DID>`.

## Two meanings of `ref`

`?ref=` on `enterprise/` and `pricing` is a checkout tag. The referring DID
therefore travels as `?referrer=` once it leaves `/join`, so the two cannot be
confused.

## What stays open

A DID that is well formed but unknown or revoked, written into a registration
call by hand, is still refused with 400 by both registration paths. That is
the KM1 rule, and KM2 does not change it. `/join` only makes sure that such a
DID never arrives through the link.
