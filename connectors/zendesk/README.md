# Zendesk connector (v0.8.6)

The Zendesk connector is the support-ticket source for organizations
that do not run Salesforce Service Cloud. It covers open ticket lists,
ticket search, and single-ticket summaries. It is the same shape as
the Salesforce cases tool, so it is a fast follow, not a new design.

## Tools

Follow the pattern in connectors/base.py: pure functions in `tools.py`,
FastMCP wrapper in `server.py`, same shape as Salesforce and Slack.

- `zendesk_list_tickets`: open tickets for an organization (subject,
  status, priority, age). Summary only. Solved and closed tickets are
  excluded: the harness only acts on tickets still being worked. The
  live path finds the organization by name, then lists its tickets,
  newest first.
- `zendesk_search_tickets`: search tickets by keyword. Returns short
  excerpts (subject, first 200 chars of the description), capped at 5
  matches. Never a full ticket or comment thread.
- `zendesk_get_ticket`: one ticket's summary (subject, status,
  priority, requester) plus up to three comment excerpts, capped at
  ~200 chars each. Never the full comment thread.

## Credentials

Zendesk API token auth, via three env vars:

- `ZENDESK_SUBDOMAIN`: the Zendesk subdomain, e.g. `acme` for
  `acme.zendesk.com`.
- `ZENDESK_EMAIL`: the email of the API user.
- `ZENDESK_API_TOKEN`: the API token (Admin Center > Apps and
  integrations > APIs > Zendesk API).

Auth is HTTP Basic with username `{email}/token` and password the API
token. Same env-var pattern as the other connectors. Stub mode returns
sample ticket data when any of the three is missing, same as the other
connectors.

## Token budget note

A ticket's comment thread can run long. The harness must never put a
full thread in a prompt. `zendesk_get_ticket` returns at most three
comment excerpts, capped at ~200 chars each, and `zendesk_search_tickets`
returns excerpts only. If a future tool needs more ticket detail, it
should return a per-comment extract, capped at the tool's max_chars,
never the full text.

## Deep links

Every Zendesk result carries ticket ids and the subdomain. The harness
builds deep links from them
(`https://{subdomain}.zendesk.com/agent/tickets/{id}`; stub links use
the `.invalid` host), the same way it builds Salesforce Lightning
links. The model never sees a URL: Zendesk results contain ids and a
subdomain, not URLs. Voice-of-customer gathers from
`zendesk_list_tickets`, so its drafts link to the tickets they quote.

## Live path status

Written, not verified: the live calls are real code, written against
the documented Zendesk API (GET /search.json for organization and
ticket search, GET /organizations/{id}/tickets.json for lists, GET
/tickets/{id}.json and GET /tickets/{id}/comments.json for a single
ticket), but no credentials were available while building v0.8.6, so
they have not run against a real instance. Stub mode is what runs in
the demo and the tests.
