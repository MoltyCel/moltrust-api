# LinkedIn: the endpoint comes from the documentation, not from memory

There is **no LinkedIn API code in this repo**, and that is why this rule is
written now rather than after the first attempt. Today the LinkedIn side of
`agents/syndicate.py` produces a draft, sends it to Telegram, and Lars pastes
it into the page by hand. The moment anybody replaces that with a call to
`api.linkedin.com`, the rule below applies.

## The rule

**Before any LinkedIn API call is written, the endpoint, its required headers
and its version are checked against the Microsoft Learn MCP server.** Not from
memory, not from a blog post, not from a Stack Overflow answer, and not from
what an older piece of our own code did.

This is the same rule as *a field that exists is not proof*
(`moltrust-web/docs/website-deploy.md` §4.1a2), applied one layer earlier. That
one says: a response field that is present does not mean the resource behind it
resolves. This one says: **an endpoint that looks plausible is not
documentation.** `POST /v2/ugcPosts` and `POST /rest/posts` are both real, one
is legacy, and which one answers depends on a version header — none of which is
guessable from the shape of the URL.

LinkedIn is a Microsoft property and its developer documentation lives on
Microsoft Learn under `learn.microsoft.com/linkedin/`, which is exactly why
that server is the right source for it and not a general web search.

## How to ask

The server is configured in the console as `microsoft-learn`
(`https://learn.microsoft.com/api/mcp`, HTTP transport, no credentials,
read-only). Three tools:

| tool | argument | use |
|---|---|---|
| `microsoft_docs_search` | `query` | find the page — up to 10 hits with URLs |
| `microsoft_code_sample_search` | `query`, optional `language` | a worked example |
| `microsoft_docs_fetch` | `url` | the whole page as markdown |

The argument is `query`. Verified on 2026-10-04, because the first attempt used
`question` and got `{"results":[]}` back — an empty result that looks exactly
like "LinkedIn is not documented here" and is in fact a wrong parameter name.
A tool call that returns nothing is a finding to investigate, not an answer.

Measured on 2026-10-04, so the example is a real one:

```
microsoft_docs_search  query="LinkedIn create UGC post API ugcPosts endpoint"
→ 10 hits, first:
  UGC Post API
  learn.microsoft.com/linkedin/compliance/integrations/shares/ugc-post-api#create-ugc-posts

microsoft_docs_search  query="LinkedIn Marketing API versioning LinkedIn-Version header"
→ 10 hits, first:
  LinkedIn Marketing API Versioning
  learn.microsoft.com/linkedin/marketing/versioning?view=li-lms-2026-09
```

Note the `?view=li-lms-2026-09` on the second URL. The documentation is
versioned per month, and a page read without that parameter can describe a
different contract than the one the header selects. Read the version the code
will send, not the default.

## What counts as having checked

Three things in the commit or the PR body, or it has not been checked:

1. **The documentation URL**, including any `?view=` parameter.
2. **The required headers**, quoted from the page — `LinkedIn-Version` and
   `X-Restli-Protocol-Version` are the two that silently change behaviour.
3. **Which of the two post APIs** the call uses, and why that one.

A plausible-looking endpoint with none of the three is the thing this file
exists to stop. "It worked when I tried it" is also not a check: an endpoint
that answers today under an unversioned call is the definition of something
that breaks on a month boundary nobody chose.

## Scope note

`claude mcp add` wrote the server into the console's **local** config, scoped
to the project directory it ran in. Another worktree of this repo does not
inherit it; `claude mcp add --scope user …` would make it global. Left local on
purpose for now — but a rule that depends on a tool only one working directory
can see is worth knowing about before it bites.
