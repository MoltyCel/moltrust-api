# AAE profile `ads` — verdict gate for the Amazon Ads MCP Server

**Ours to define.** This file fixes how an advertising mandate is encoded for
`POST /enforce/check` and which facts about Amazon's tool surface it rests on.
Executable form: `sdk/python/src/moltrust_enforce/adapters/amazon_ads.py`
(`build_mandate`, `split_call`). Demo: `sdk/python/examples/amazon_ads/demo.py`.

**Verified:** 2026-10-12 against Amazon's public MCP docs (sources below).
**Status of the Amazon side:** open beta ("MCP Tools (beta)" in the docs TOC).
Tool names may change; re-check the sources before relying on a name.

## What MolTrust does and does not do

- MolTrust returns a verdict — allow (PERMIT), hold (PENDING), forbid (DENY) — on
  an intended tool call, with a record whose `core_digest` anyone can recompute.
- MolTrust holds, stores and proxies **no** Amazon credentials, tokens or Amazon
  data, and sends **no** request to an Amazon endpoint. The customer's agent runs
  the call with its own credentials (`execute` callable in the adapter).
- What reaches MolTrust per check: tool name, change class, account ID, brand,
  and the integer values the profile constrains. Nothing else from the arguments.

## Sources (fetched 2026-10-12)

| Source | URL | sha256 (first 16) |
|---|---|---|
| Overview | `https://d3a0d0y2hgofx6.cloudfront.net/en-us/mcp/mcp-overview.md` (rendered: `https://advertising.amazon.com/API/docs/en-us/mcp/mcp-overview`) | `ec3e880f2eb0c989` |
| Lite | `https://d3a0d0y2hgofx6.cloudfront.net/en-us/mcp/get-started-lite.md` | `01dfcff5274ee478` |
| campaign_management | `https://d3a0d0y2hgofx6.cloudfront.net/en-us/mcp/campaign_management.json` | `7e860398b23ff345` |
| reporting | `…/mcp/reporting.json` | `2d723917c0ce9e16` |
| user_permissions | `…/mcp/user_permissions.json` | `7daba74d8fc7a6d6` |
| user_invitations | `…/mcp/user_invitations.json` | `24e7d4b0f297f2e5` |
| user_invitation | `…/mcp/user_invitation.json` | `4660327ed0bd1b65` |
| manager_accounts | `…/mcp/manager_accounts.json` | `2826fdf78adf2b53` |
| Announcement (open beta, 2026-02-02) | `https://advertising.amazon.com/en-us/library/news/amazon-ads-mcp-server-open-beta/` | — |

The CloudFront files are the sources the docs page loads; they need no login.
Amazon publishes **input** schemas only. There are no response schemas for the
MCP tools, so any mocked Amazon response is illustrative.

## Facts about the Amazon tool surface

- Names are `<group>-<operationId>`, e.g. `campaign_management-update_campaign_budget`.
- The account is in the body: `body.accessRequestedAccount` is one of
  `{advertiserAccountId}` or `{profileId}` (docs also name `managerAccountId`).
- Write tools take **lists**; one call can change many items:

| Tool | List | Id | Gate-relevant fields in the item |
|---|---|---|---|
| `campaign_management-create_campaign` | `campaigns` | — | `brandId`, `budgets`, `state` |
| `campaign_management-update_campaign` | `campaigns` | `campaignId` | `budgets`, `state` |
| `campaign_management-update_campaign_budget` | `campaigns` | `campaignId` | `budgets` |
| `campaign_management-update_campaign_state` | `campaigns` | `campaignId` | `state` |
| `campaign_management-create_ad_group` | `adGroups` | — | `campaignId`, `bid`, `budgets`, `state` |
| `campaign_management-update_ad_group` | `adGroups` | `adGroupId` | `bid`, `budgets`, `state` |
| `campaign_management-create_target` | `targets` | — | `adGroupId`, `campaignId`, `bid`, `state` |
| `campaign_management-update_target` | `targets` | `targetId` | `campaignId`, `bid`, `state` |
| `campaign_management-update_target_bid` | `targets` | `targetId` | `bid` |

- `budgets[]` entry: `budgetType` (MONETARY), `recurrenceTimePeriod`
  (DAILY | LIFETIME | MONTHLY), `budgetValue.monetaryBudgetValue.monetaryBudget.value`
  plus optional `marketplaceSettings[].monetaryBudget.value` per marketplace.
- `bid`: `{bid, marketplaceSettings[]: {marketplace, bid}}`; ad groups use
  `{baseBid, defaultBid, marketplaceSettings[]: {defaultBid}}`.
- Lite endpoint (`/mcp/lite`, since 2026-09-29) exposes three tools:
  `amazon_ads_mcp-search_tools`, `-describe_tools`, `-invoke_tool`. `invoke_tool`
  carries the real tool name and arguments; **its argument names are not in the
  public docs** (UNVERIFIED). The adapter reads `toolName|tool_name|name|tool` and
  `arguments|args|input|toolInput`; anything else stays wrapped and is DENY.
- Access-changing groups: `user_permissions` (`list_/update_/delete_user_permissions`),
  `user_invitations` (`create`, `list`, `update`), `user_invitation` (`get`, `redeem`),
  `manager_accounts` (`associate_accounts`, `create_manager_account`,
  `disassociate_accounts`, `get_manager_accounts`).

## Encoding

**Action.** `{"verb": "ads_mcp_call", "tool": <name>, "change": <class>}`,
`type_fields = ["verb", "tool", "change"]`. One grant set per allowed tool and
change class. A call is split into one check per item and change class; the
strictest verdict decides (forbid > hold > allow).

| Class | Produced by | Constraints (besides account, brand, window) |
|---|---|---|
| `read` | query/list/get/retrieve/search/describe ops, `reporting-create_*report` | account + window only (Lite catalogue tools: window only) |
| `other` | any item field outside budgets/bid, or an item with none | — |
| `budget_daily` | `budgets[]` with DAILY | allow: `daily_budget` 0..hold_threshold; hold: hold_threshold+1..daily_budget_cap; both: `total_daily_budget` 0..total_daily_budget_cap |
| `budget_period` | `budgets[]` with LIFETIME or MONTHLY | `period_budget` 0..period_budget_cap |
| `bid` | `bid` on update_target, update_target_bid, update_ad_group | `bid` 0..max_bid; `bid_change_bp` ±max_bid_change_bp |
| `bid_new` | `bid` on create_target, create_ad_group | `bid` 0..max_bid |

- Common: `account_id` enum over the profile's accounts; `brand` enum over its
  brands (not on `read`); `t_min` range over the validity window in UTC epoch
  minutes.
- Money is integer minor units of the account currency. Floats are rounded half
  up to the cent; a per-marketplace value counts, and the largest value of an
  entry is the one checked. An unreadable value makes the field absent.
- `bid_change_bp` = change against the caller's current bid in basis points,
  rounded away from zero, so a cap cannot be undercut by rounding.
- `total_daily_budget` = caller's current account total − the campaign's current
  daily budget + the new one.
- Hold has no field of its own: two grants on the same action, `allow` below the
  threshold and `hold` above it (kernel picks the first matching grant that holds).
- Forbidden groups (`user_permissions-`, `user_invitations-`, `user_invitation-`,
  `manager_accounts-`) are refused by `AdsProfile.problem()`, so no grant for them
  can exist; a call to them is unaddressed → DENY.
- `amazon_ads_mcp-invoke_tool` itself can never be granted; it is unwrapped.

## Where the values come from

Kernel 3.0 is stateless and reads no clock. `t_min`, the current budget, the
account's daily total, current bids and the brand of an existing campaign or
target are the **caller's own statement**, derived in code by the adapter from
the caller's state (`context`) — never from the model's text. A value the adapter
cannot derive is absent, and an absent value fails its constraint: unknown means
DENY.

## AER

Each check also gets an AER bundle and an `f_ext` record over the same mandate and
transaction; `moltrust-verify` (V1–V4) recomputes it offline. The SDK signs
nothing: the caller passes `attest(statement)` backed by its own key and the
bundle carries one statement `{"kind": "ads_decision", mandate, tool, change,
item}` with value `t_min` and a window of exactly the decision second. Without
`attest` the bundle is empty; V1–V4 still pass, but nothing attests the time.

## Known gaps

- The decision time and the running account total are client-asserted. A server
  clock or a signed spend source would close this; neither exists in kernel 3.0.
- Server and local verdicts are compared per check; a mismatch is DENY. The AER
  record is computed locally, not by the server.
- Lite `invoke_tool` argument names are a guess until Amazon documents them.
- No response schemas from Amazon; demo responses are illustrative.

## Attribution errors to avoid

- Do not call MolTrust an Amazon partner, or the integration "official", before an
  Amazon Ads Partner Network listing exists. Descriptive use only ("for the Amazon
  Ads MCP Server").
- Do not say the server is GA; Amazon's docs say beta.
- Do not cite the IAB Tech Lab registry's tool list as Amazon's: on 2026-10-12 it
  showed 62 tools against 110 in Amazon's docs.
