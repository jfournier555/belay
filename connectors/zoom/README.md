# Zoom connector (v0.8.5)

The Zoom connector is the second transcript source, after Gong. If a team
records on Zoom instead of Gong, Belay is blind to their calls. It covers
recording lists, Zoom's AI-generated recording summaries, and transcript
search. It is the source that most needs the summary-first rule, because
a call transcript is the most token-expensive raw material in the harness.

## Tools

Follow the pattern in connectors/base.py: pure functions in `tools.py`,
FastMCP wrapper in `server.py`, same shape as Salesforce, Slack, and Gong.

- `zoom_list_recordings`: recordings for an account (date, duration,
  host, title). Summary only: no transcript in the result. The live path
  filters client-side, because Zoom has no account-name filter: it keeps
  recordings whose topic or host mentions the account.
- `zoom_get_recording_summary`: the recording's AI-generated summary
  (summary text, topics, action items), not the raw transcript. Capped
  at 2000 chars.
- `zoom_search_transcripts`: search recording transcripts for a keyword.
  Returns short excerpts with timestamps, never a full transcript.
  Excerpts are capped at ~200 chars and matches at 5, the same as Gong
  search. The live path lists recent recordings, downloads their
  transcript files, and scans the cues client-side.

## Credentials

Zoom server-to-server OAuth, via env vars `ZOOM_ACCOUNT_ID`,
`ZOOM_CLIENT_ID`, and `ZOOM_CLIENT_SECRET`. The connector exchanges
them for an access token (POST zoom.us/oauth/token,
grant_type=account_credentials) and caches it until shortly before
expiry. The Zoom app needs the recording:read:admin scope. Same
env-var pattern as the other connectors. Stub mode returns sample
recording data when no credentials are present, same as the other
connectors.

## Token budget note

A single call transcript can be 10k+ tokens. The harness must never put
a raw transcript in a prompt. `zoom_get_recording_summary` returns
Zoom's own summary (already compressed). `zoom_search_transcripts`
returns excerpts. If a future tool needs transcript detail, it should
return a per-speaker, per-topic extract, capped at the tool's max_chars,
never the full text.

## Deep links

Every Zoom result carries a recording play URL. The harness builds deep
links from it (the play URL from the API live, the same path on the
`.invalid` host for stub data), the same way it builds Gong call links.
The model never sees a URL: Zoom results contain no URL fields in the
rendered context, only ids. Voice-of-customer gathers from
`zoom_search_transcripts`, so its drafts link to the recordings they
quote.

## Live path status

Written, not verified: the live calls are real code, written against the
documented Zoom API (POST /oauth/token for server-to-server OAuth,
GET /v2/users/me/recordings for lists, GET /v2/meetings/{meetingId}/
recordings for summaries and transcript files), but no credentials were
available while building v0.8.5, so they have not run against a real
account. Stub mode is what runs in the demo and the tests.
