# Champify connector (v0.8.8)

The Champify connector is the champion job-change source. It covers
tracked champions for an account, their recent job changes, and one
champion's detail. It is the smallest connector in the set, and the
last one in the current queue. Advocate-finder gathers from it: a
champion who changed jobs is the departure signal the agent already
flags, and a champion who moved to a new company is a new pipeline
signal, not just a loss.

## Tools

Follow the pattern in connectors/base.py: pure functions in `tools.py`,
FastMCP wrapper in `server.py`, same shape as Salesforce and Slack.

- `champify_list_champions`: champions tracked for an account (name,
  role, company, status). Summary only, no full profile or contact
  record.
- `champify_list_job_changes`: recent job changes among an account's
  champions (who moved, from where to where, when). Summary only.
- `champify_get_champion`: one champion's detail (current role and
  company, job history, contact status). Short job history only,
  never a full profile.

## Credentials

Champify API key, via env vars:

- `CHAMPIFY_API_KEY`: the API key. Auth is the raw key as a Bearer
  token.
- `CHAMPIFY_API_BASE_URL`: the API base URL. Default:
  `https://api.champify.io/v1`.

Same env-var pattern as the other connectors. Stub mode returns sample
champion data when the key is missing, same as the other connectors.

## Token budget note

A champion record is small. The harness must never put a full profile
or contact record in a prompt. `champify_get_champion` returns a short
job history only, and the list tools return metadata only.

## Deep links

Every Champify result carries champion ids. The harness builds deep
links from them (`https://app.champify.io/champions/{id}`; stub links
use the `.invalid` host), the same way it builds Gong call links. The
model never sees a URL: Champify results contain ids, names, and roles,
not URLs. Advocate-finder gathers from `champify_list_job_changes`,
so its drafts link to the champions whose moves they quote.

## Live path status

Written, not verified, with one honest caveat stronger than the other
connectors: Champify publishes no public API reference, so the live
calls are written against assumed endpoint shapes
(GET /v1/champions, GET /v1/champions/{id}, GET /v1/job-changes),
inferred from the product's own data model (champions with job
histories), not from documentation. No credentials were available
while building v0.8.8, so they have not run against a real account.
Correct the endpoints against the real API the first time a key is
available. Stub mode is what runs in the demo and the tests.
