# Belay

**A post sales AI harness.**

![Belay: A post sales AI harness. Red rock mountains at golden hour.](assets/belay-banner.jpg)

Post-sales is a hard climb. Every account is a steep peak: the renewal, the expansion, proving the value is real. The reps climbing them are doing it with a full book and no rope. Belay is the rope and the harness. It is everything around the model: routing, tools, memory, approvals. The model is the engine, Belay is the chassis, and the dashboard (phase 2) is what post sales teams operate from. It keeps you from falling while you climb,

Belay is the sequel to ["AI is taking the admin. The judgment is the job."](https://jfournier.net/articles/ai-admin-judgment/): the article's "the wiring is the job" productized.

## How it works

```
Job (free text or --agent + --account)
  -> Router (keyword first, small-model LLM only when ambiguous)
  -> Specialist agent (1 of 8, 6 implemented in v0.5)
  -> Tools (Salesforce + Slack, summary-first results)
  -> Draft
  -> Proof checklist (the agent self-checks "done" before queueing)
  -> Approval queue (a human approves, rejects, or edits)
  -> Only an approved draft leaves the harness
```

Three rules, enforced in code:
1. **Model-agnostic.** Any LLM that speaks the OpenAI-compatible chat API
   works: OpenAI, Ollama, vLLM, anything. Point `HARNESS_LLM_BASE_URL` at
   it. No vendor lock-in.
2. **Low token use is real architecture.** Small model for the router.
   Tools return summaries, never raw transcripts. A per-job token budget
   stops one bad tool call from blowing up the context window.
3. **A human reads the draft, not the whole thread.** No agent output is
   sent, posted, or acted on directly. Every draft goes to the approval
   queue first.

## Proof checklist

Every agent defines what "done" looks like as a short checklist: the
evidence its draft must contain, as required sources gathered and key
points covered. After drafting, the agent self-checks its own output
against the checklist before the draft reaches the approval queue. The
results ride along in the draft metadata and show up in
`approvals show`, so the human reviewer sees them. A failed item is a
flag, never a block: the draft still goes to the queue either way, and
human approval remains the final gate.

## Quick start

```bash
pip install -r requirements.txt

# Demo with no credentials and no network: stub data + stub LLM.
python -m harness.run run --agent renewal-desk --account "Acme Corp" --stub --show

# Free-text routing (the router picks the agent).
python -m harness.run run --text "draft a renewal plan for Acme Corp" --stub

# See the eight agents and the registered tools.
python -m harness.run agents
python -m harness.run tools

# Review and approve the draft.
python -m harness.run approvals list
python -m harness.run approvals show <id>
python -m harness.run approvals approve <id>
```

## With real credentials

Copy `.env.example` to `.env` and fill in what you have. Everything is
optional: missing credentials fall back to stub mode for that connector,
and a missing LLM key falls back to the stub LLM.

```bash
cp .env.example .env
# HARNESS_LLM_API_KEY=...        (OpenAI, or any OpenAI-compatible key)
# SALESFORCE_INSTANCE_URL=...    (e.g. https://mycompany.my.salesforce.com)
# SALESFORCE_ACCESS_TOKEN=...    (OAuth access token)
# SLACK_BOT_TOKEN=...            (xoxb-...)
```

Then run without `--stub`:
```bash
python -m harness.run run --agent renewal-desk --account "Acme Corp"
```

## The eight agents

| Agent | Status | What it does |
|---|---|---|
| renewal-desk | implemented | Drafts a renewal plan: health, risks, next steps. |
| briefer | implemented | Drafts a pre-meeting brief: snapshot, changes, questions. |
| scout | implemented | Finds new information: stakeholders, org changes, hiring signals. |
| onboarding-guide | implemented | Drafts onboarding plans and first-value checklists. |
| expansion-spotter | implemented | Finds expansion openings: usage gaps, new teams. |
| qbr-builder | implemented | Drafts a quarterly business review: outcomes, adoption, risks. |
| advocate-finder | planned | Finds happy customers who could advocate or reference. |
| voice-of-customer | planned | Summarizes what customers are saying across sources. |

## Connectors

| Connector | Status | Tools |
|---|---|---|
| Salesforce | v0.2 | `salesforce_get_account`, `salesforce_list_opportunities`, `salesforce_list_cases` |
| Slack | v0.2 | `slack_search_messages`, `slack_list_channels`, `slack_get_thread` |
| Gong | planned | See `connectors/gong/README.md` for the planned tools. |

Each connector exposes its tools two ways, from one implementation
(`tools.py`): in-process, through the harness tool registry (the v0.2 path),
and as a standalone MCP server (`server.py`, FastMCP over stdio) that any
MCP-capable LLM can call. Run a connector as an MCP server with:

```bash
python -m connectors.salesforce.server
python -m connectors.slack.server
```

Calling the MCP servers from the harness via an MCP client (instead of
in-process) is a phase-2 item. The tool functions are already written once
and shared, so the cutover is a client, not a rewrite.

## What is real vs scaffolded (v0.5 honesty)

**Real, tested (56 unit tests, all passing):**
- Router (explicit, keyword, small-model LLM, default), tool registry
  (summary-first), approval queue (file-backed, persists), token budget,
  stub LLM, OpenAI-compatible LLM client (tested with a mocked transport).
- Renewal desk, briefer, expansion spotter, onboarding guide, scout, and
  qbr builder agents, end to end, in stub mode.
- Proof checklist: every agent self-checks its draft against a "done"
  checklist before queueing; failures are flagged in the draft metadata
  and shown in `approvals show`, never a block.
- Salesforce and Slack MCP servers: verified over the real MCP protocol
  (a client connected over stdio, listed tools, called one, got a result).
- The demo loop (`run` -> draft -> approval queue) in stub mode.

**Written, not verified against a live system:**
- The live Salesforce REST/SOQL calls and the live Slack Web API calls.
  They are real code, written against the documented APIs, but no
  credentials were available while building v0.2, so they have not run
  against a real org or workspace. Treat them as "written, not verified"
  until someone runs them with real credentials. Stub mode is what runs
  in the demo and the tests.

**Scaffolded / planned, not built:**
- Two of the eight agents (advocate-finder,
  voice-of-customer). Registered, routable
  in name only, `implemented=False`, and the router never sends them work.
- The Gong connector (see `connectors/gong/README.md`).
- The dashboard (phase 2). V1 is the harness + connectors + approval
  queue. The dashboard is the UI the CSM operates from, and it needs
  hosting or a local app. It is not in this repo yet.
- The MCP client inside the harness (phase 2). V1 calls connector tools
  in-process; the MCP servers are standalone.

## Adding a connector

Copy `connectors/slack/` to `connectors/<name>/`. Write the pure tool
functions in `tools.py` (plain functions, return dicts, summary-first).
Wrap them in `server.py` with FastMCP. Register them in
`harness/run.py` `build_registry()`. Add the env vars to `.env.example`.
See `connectors/base.py` for the pattern, and `connectors/gong/README.md`
for a worked example of what a planned connector looks like.

## Related work

- [MCP Readiness Checker](https://jfournier.net/tools/mcp-readiness/):
  scores whether a team is ready to run agents against their data. Run
  it before wiring this harness to a real org.
- ["AI is taking the admin. The judgment is the job."](https://jfournier.net/articles/ai-admin-judgment/):
  the positioning piece this harness follows from.

## License

MIT. See [LICENSE](LICENSE).
