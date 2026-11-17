# Gong connector (planned, not v1)

The Gong connector is next after Salesforce and Slack. It is not built in
v1. This file records what it should be, so the next contributor does not
have to re-decide it.

## Why Gong third

Salesforce and Slack cover the two most universal CSM sources: the system
of record (Salesforce) and the internal conversation (Slack). Gong covers
the customer conversation (call transcripts, deal insights). It is the
third source, and the one that most needs the summary-first rule, because
a call transcript is the most token-expensive raw material in the harness.

## Tools it should expose

Follow the pattern in connectors/base.py: pure functions in `tools.py`,
FastMCP wrapper in `server.py`, same shape as Salesforce and Slack.

- `gong_list_calls`: calls for an account (date, participants, duration,
  title). Summary only: no transcript in the result.
- `gong_get_call_summary`: the call summary Gong already generates
  (topics, action items, next steps), not the raw transcript.
- `gong_search_transcripts`: search call transcripts for a keyword.
  Returns short excerpts with timestamps, never a full transcript.
  This is the tool where summary-first matters most: cap excerpts at
  ~200 chars and matches at 5, the same as Slack search.

## Credentials

Gong API access token, via env var `GONG_ACCESS_TOKEN`, and the Gong API
base URL. Same env-var pattern as Salesforce and Slack. Stub mode returns
sample call data when no token is present, same as the v1 connectors.

## Token budget note

A single call transcript can be 10k+ tokens. The harness must never put
a raw transcript in a prompt. `gong_get_call_summary` returns Gong's own
summary (already compressed). `gong_search_transcripts` returns excerpts.
If a future tool needs transcript detail, it should return a
per-speaker, per-topic extract, capped at the tool's max_chars, never the
full text.
