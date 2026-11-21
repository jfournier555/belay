# Linear connector (v0.8.7)

The Linear connector is the engineering-issue source. It covers open
issue lists, issue search, and single-issue summaries. It is the same
shape as the Zendesk ticket tools, so it is a fast follow, not a new
design. Linear was picked over Jira for this slot: its API is a single
GraphQL endpoint, auth is one API key matching the repo's env-var
pattern, and it is the tool the SaaS audience the harness targets
actually uses.

## Tools

Follow the pattern in connectors/base.py: pure functions in `tools.py`,
FastMCP wrapper in `server.py`, same shape as Salesforce and Slack.

- `linear_list_issues`: open issues, optionally for one team (team key,
  e.g. ENG). Without a team, lists open issues workspace-wide. Summary
  only. Completed and canceled issues are excluded: the harness only
  acts on issues still being worked.
- `linear_search_issues`: search issues by keyword. Returns short
  excerpts (title, first 200 chars of the description), capped at 5
  matches. Never a full issue or comment thread.
- `linear_get_issue`: one issue's summary (title, status, priority,
  assignee) plus a description excerpt, capped at ~200 chars. Never the
  full comment thread. Takes the issue id (UUID) or identifier
  (e.g. ENG-142).

## Credentials

Linear API key, via one env var:

- `LINEAR_API_KEY`: the API key (Linear Settings > API > Personal API
  keys). Auth is the raw key in the Authorization header.

Same env-var pattern as the other connectors. Stub mode returns sample
issue data when the key is missing, same as the other connectors.

## Token budget note

An issue's comment thread can run long. The harness must never put a
full thread in a prompt. `linear_get_issue` returns a description
excerpt only, no comments at all, and `linear_search_issues` returns
excerpts only. If a future tool needs comment detail, it should return
a per-comment extract, capped at the tool's max_chars, never the full
text.

## Deep links

Every Linear result carries issue identifiers and the team key. The
harness builds deep links from them
(`https://linear.app/{team}/issue/{identifier}`; stub links use the
`.invalid` host), the same way it builds Zendesk ticket links. The
model never sees a URL: Linear results contain ids, identifiers, and
team keys, not URLs. Renewal-desk gathers from `linear_list_issues`,
so its drafts link to the engineering issues they quote.

## Live path status

Written, not verified: the live calls are real code, written against
the documented Linear GraphQL API (POST https://api.linear.app/graphql;
`issues` with an IssueFilter for lists, `issueSearch` for keyword
search, `issues` filtered by id or identifier for a single issue), but
no credentials were available while building v0.8.7, so they have not
run against a real workspace. Stub mode is what runs in the demo and
the tests.
