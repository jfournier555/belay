# Gong connector (v0.8.2)

The Gong connector is the third source, after Salesforce and Slack. It
covers the customer conversation: call lists, Gong's generated call
summaries, and transcript search. It is the source that most needs the
summary-first rule, because a call transcript is the most
token-expensive raw material in the harness.

## Tools

Follow the pattern in connectors/base.py: pure functions in `tools.py`,
FastMCP wrapper in `server.py`, same shape as Salesforce and Slack.

- `gong_list_calls`: calls for an account (date, participants, duration,
  title). Summary only: no transcript in the result. The live path
  filters client-side, because Gong has no account-name filter: it keeps
  calls whose title or participants mention the account.
- `gong_get_call_summary`: the call summary Gong already generates
  (brief, key points, topics, call outcome), not the raw transcript.
- `gong_search_transcripts`: search call transcripts for a keyword.
  Returns short excerpts with timestamps, never a full transcript.
  Excerpts are capped at ~200 chars and matches at 5, the same as Slack
  search. The live path lists recent calls, pulls their transcripts, and
  scans the sentences client-side.

## Credentials

Gong API access token, via env var `GONG_ACCESS_TOKEN` (Bearer auth),
and the Gong API base URL via `GONG_API_BASE_URL` (default
`https://api.gong.io/v2`; regional instances take the form
`https://{region}.api.gong.io/v2`). Same env-var pattern as Salesforce
and Slack. Stub mode returns sample call data when no token is present,
same as the other connectors.

## Token budget note

A single call transcript can be 10k+ tokens. The harness must never put
a raw transcript in a prompt. `gong_get_call_summary` returns Gong's own
summary (already compressed). `gong_search_transcripts` returns excerpts.
If a future tool needs transcript detail, it should return a
per-speaker, per-topic extract, capped at the tool's max_chars, never the
full text.

## Deep links

Every Gong result carries call ids. The harness builds deep links from
them (`https://app.gong.io/call?id=<callId>`; stub links use the
`.invalid` host), the same way it builds Salesforce Lightning links.
The model never sees a URL: Gong results contain no URL fields, only
ids. Voice-of-customer gathers from `gong_search_transcripts`, so its
drafts link to the calls they quote.

## Live path status

Written, not verified: the live calls are real code, written against the
documented Gong API (POST /v2/calls/extensive for lists and summaries,
POST /v2/calls/transcript for transcripts), but no token was available
while building v0.8.2, so they have not run against a real workspace.
Stub mode is what runs in the demo and the tests.
