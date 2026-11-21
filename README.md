# Belay

**A post sales AI harness.** Current version: **v1.12.0**, 15 agents, 9 connectors, 577 tests green.

![Belay: A post sales AI harness. Red rock mountains at golden hour.](assets/belay-banner.jpg)

Post-sales is a hard climb. Every account is a steep peak: the renewal, the expansion, proving the value is real. The reps climbing them are doing it with a full book and no rope. Belay is the rope and the harness. It is everything around the model: routing, tools, memory, approvals. The model is the engine, Belay is the chassis, and the dashboard is what post sales teams operate from. It keeps you from falling while you climb,

Belay is the sequel to ["AI is taking the admin. The judgment is the job."](https://jfournier.net/articles/ai-admin-judgment/): the article's "the wiring is the job" productized.

## First run

```bash
git clone https://github.com/jfournier555/belay.git
cd belay
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

Python 3.11 or newer. The commands below call the venv binaries directly (`.venv/bin/python`), so they work in bash, zsh, and fish alike with no activation step. If you prefer to activate the venv, use `source .venv/bin/activate` in bash/zsh or `source .venv/bin/activate.fish` in fish; plain `python` then works, but the bare `activate` script is bash syntax and fails in fish.

Then run the first-run wizard. It asks one question with two answers:

```bash
.venv/bin/python -m harness.run setup
```

1. **Fill with Demo data?** Seeds the stores with 4 demo drafts (one approved, one edited, two pending), 2 memory records, a daily schedule, and 3 open escalations, so the dashboard shows a lived-in harness instead of empty views. The drafts are generated through Ollama when it is reachable (real responses); otherwise they are stub-labeled and the output says so plainly. Refuses to seed over real data without explicit confirmation; clear it anytime with `python -m harness.run setup --clear-demo`.
2. **Run Setup Wizard?** Walks the LLM provider choice plus the nine connectors (Salesforce, Slack, Gong, HubSpot, Gmail, Zoom, Zendesk, Linear, Champify), writes them to `.env` preserving existing entries, and verifies each one live where a check exists. Secrets are never printed.

### The LLM: Ollama is the default

A new user gets real responses out of the box with no API key. The default LLM is **Ollama**, self-hosted and local (`llama3.1:8b`), reached at `http://localhost:11434`. The resolution order is: Ollama if reachable, then a configured API key provider, then the stub. The stub is the labeled fallback, never the silent default: every stub draft says so in its header.

If Ollama is not installed, the wizard offers to install it for you (official distribution only, explicit yes, exact command shown first): Linux via the official install script, macOS via Homebrew or the official download, Windows via winget or the official installer. After install it starts the server, verifies reachability, and offers to pull the model. Decline, and it falls back to an API key or stub.

To install Ollama by hand:

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh
# macOS
brew install --cask ollama
# Windows
winget install Ollama.Ollama
```

Then pull the default model and start the server:

```bash
ollama pull llama3.1:8b
ollama serve
```

The wizard also lists the hosted providers it can talk to (OpenAI, OpenRouter, Groq, Together, DeepSeek, Mistral) plus a Custom endpoint for anything else OpenAI-compatible. Each provider entry carries its default base URL and model; the wizard prompts for the API key and model only. A provider picked in the wizard wins over a locally running Ollama: your choice is not overridden.

To go live with real accounts, fill in `.env` with the credentials you have (or run the wizard), then run without `--stub`:

```bash
.venv/bin/python -m harness.run run --agent renewal-desk --account "Acme Corp"
```

Missing credentials fall back to stub mode for that connector, so you can connect one source at a time. The account is the unit: every run names one account, and every agent works from it. Connect each source once in `.env`; there is no per-agent or per-subaccount setup, subaccounts follow the account hierarchy in your CRM.

## How it works

```
Job (free text or --agent + --account)
  -> Router (keyword first, small-model LLM only when ambiguous)
  -> Specialist agent (1 of 15, all implemented: the original 8 in v0.7,
     value-finder in v0.9.0, salesforce-updater in v1.3.0, risk-watcher
     in v1.6.0, escalation-manager in v1.7.0, competitive-defense in
     v1.8.0, stakeholder-mapper in v1.9.0, case-triage in v1.10.0)
  -> Tools (9 connectors, summary-first results)
  -> Draft
  -> Proof checklist (the agent self-checks "done" before queueing)
  -> Approval queue (a human approves, rejects, or edits)
  -> Only an approved draft leaves the harness
```

![Belay architecture: the AI model at the center, the harness around it.](assets/belay-diagram.png)

Three rules, enforced in code:
1. **Model-agnostic.** Any LLM that speaks the OpenAI-compatible chat API
   works: OpenAI, Ollama, vLLM, anything. Point `HARNESS_LLM_BASE_URL` at
   it. No vendor lock-in.
2. **Low token use is real architecture.** Small model for the router.
   Tools return summaries, never raw transcripts. A per-job token budget
   stops one bad tool call from blowing up the context window.
3. **A human reads the draft, not the whole thread.** No agent output is
   sent, posted, or acted on directly. Every draft goes to the approval
   queue first. Nothing writes back to Salesforce until a human approves
   it. The queue gates writes as well as sends: the updater agent drafts
   changes, and only an approved write draft executes, through the
   executor in harness/writeback.py. Rejected drafts never execute.

## Proof checklist

Each agent self-checks its draft against a proof checklist before it
reaches the queue, and every draft carries clickable deep links to the
records it cites, so the reviewer checks the source, not a score. The
checklist is a completeness gate, not a grade: each item is a fact the
draft must contain or a source it must have gathered, and one item
checks the draft carries source links at all. A failed item is a flag,
never a block: the draft still goes to the queue either way, and human
approval remains the final gate. No aggregate score is shown anywhere:
a score would let the reviewer trust the number instead of opening the
record.

The links are built by the harness, never by the model: the Salesforce
tools return record ids and the instance URL, the Slack tool carries
each message's permalink, and the harness turns those into Lightning
record links and Slack permalinks. The model is never shown a URL and
is told never to write one, so a link can never be hallucinated. In
stub mode the links point at clearly fake hosts (`.invalid`), so a
demo link can never be mistaken for a real record.

## Quick start

```bash
pip install -r requirements.txt

# Demo with no credentials and no network: stub data + stub LLM.
python -m harness.run run --agent renewal-desk --account "Acme Corp" --stub --show

# Free-text routing (the router picks the agent).
python -m harness.run run --text "draft a renewal plan for Acme Corp" --stub

# Value report with quantified time saved. The rate and the hours are
# explicit inputs, never assumed: --hourly-rate, or
# HARNESS_LOADED_HOURLY_RATE in the environment.
python -m harness.run run --agent value-finder --account "Acme Corp" --stub --hourly-rate 150 --hours-saved 10 --show

# See the fifteen agents and the registered tools.
python -m harness.run agents
python -m harness.run tools

# Review and approve the draft. <id> is a placeholder: copy a real
# draft id from `approvals list` (in fish, typing <id> literally
# errors, since < is a redirect).
python -m harness.run approvals list
python -m harness.run approvals show <id>
python -m harness.run approvals approve <id>
```

## With real credentials

Copy `.env.example` to `.env` and fill in what you have. Everything is
optional: missing credentials fall back to stub mode for that connector,
and the LLM falls back to Ollama when it is reachable, then to the stub
(when neither Ollama nor an API key is available).

```bash
cp .env.example .env
# HARNESS_LLM_API_KEY=...        (only for a hosted provider; Ollama needs no key)
# SALESFORCE_INSTANCE_URL=...    (e.g. https://mycompany.my.salesforce.com)
# SALESFORCE_ACCESS_TOKEN=...    (OAuth access token)
# SLACK_BOT_TOKEN=...            (xoxb-...)
```

Then run without `--stub`:
```bash
python -m harness.run run --agent renewal-desk --account "Acme Corp"
```

## The fifteen agents

Each agent owns one job, not a schedule. Cadence is the scheduler
(v1.4.0), a separate architecture piece.

| Agent | Status | What it does |
|---|---|---|
| renewal-desk | implemented | Drafts a renewal plan: health, risks, next steps. |
| briefer | implemented | Drafts a pre-meeting brief: snapshot, changes, questions. |
| scout | implemented | Finds new information: stakeholders, org changes, hiring signals. |
| onboarding-guide | implemented | Drafts onboarding plans and first-value checklists. |
| expansion-spotter | implemented | Finds expansion openings: usage gaps, new teams. |
| qbr-builder | implemented | Drafts a quarterly business review: outcomes, adoption, risks. Composes from prior renewal-desk, expansion-spotter, and voice-of-customer drafts when they exist; gathers directly otherwise. |
| advocate-finder | implemented | Finds champions and advocates: who uses it most, who praises it, how much influence each carries, and which champions changed jobs. |
| voice-of-customer | implemented | Summarizes customer feedback: requests, pain points, sentiment. |
| value-finder | implemented | Finds realized and unrealized outcomes in money or time: time saved, revenue protected, gaps not yet captured. Every dollar figure is labeled measured or modeled with its basis. |
| salesforce-updater | implemented | Drafts Salesforce changes for approval: field changes, activity logs, stage moves. The draft carries a dry-run preview (old -> new) and the write plan; nothing applies until a human approves it. |
| risk-watcher | implemented | Watches for churn signals: champion departure, ticket spikes, stalled renewals, sponsor disengagement. Drafts a risk brief with signals ranked by severity (harness-computed), evidence, and a next step. On champion departure it asks whether the mandate changed, who the successor is, and what the successor's priorities are. With no signals the draft is a clean bill. Composes from prior renewal-desk, advocate-finder, and voice-of-customer drafts when they exist; gathers directly otherwise. |
| escalation-manager | implemented | Owns escalations end to end: drafts the escalation plan (situation with evidence, people to loop in, the ask, exec update and customer comms as drafts, fallback if it does not land) for high-severity signals, and tracks each escalation open, escalated, resolved in one file per signal per account. A second run for the same signal drafts a status note, not a second plan; approving a plan moves its record to escalated; a resolved record stays resolved. Composes from prior risk-watcher, renewal-desk, and voice-of-customer drafts when they exist; gathers directly otherwise. |
| competitive-defense | implemented | Watches for displacement risk: competitor mentions, evaluation signals, champion doubt. Drafts a defense brief with threats ranked by severity (harness-computed), evidence with its source, where the account is vulnerable, the wedge to hold the account, and the proof points to use. A rival is named only when the gathered records name one. With no signals the draft is a clean bill. Composes from prior voice-of-customer, expansion-spotter, and risk-watcher drafts when they exist; gathers directly otherwise. |
| stakeholder-mapper | implemented | Builds the relationship map: users, champions, economic buyers, blockers, and how they connect. Each person carries a role and an influence score (harness-computed, from observable signals only), the evidence behind both, and the relationships the records show. A role the records do not support stays unknown; the coverage gaps (no economic buyer, a departed champion with no successor named, a stakeholder known only by a display name or handle) are named, never papered over. With no people the map is empty. Composes from prior advocate-finder, risk-watcher, and voice-of-customer drafts when they exist; gathers directly otherwise. |
| case-triage | implemented | Triages the incoming cases, tickets, and issues: Salesforce cases, Zendesk tickets, Linear issues, and Gmail threads that read like support requests. Drafts a triage brief with the items grouped by subject across systems, ranked by priority (harness-computed from severity, age, and blocking impact), each with a suggested owner and a draft first response or internal summary. Items that are stale, duplicated across systems, or already covered by another agent's draft are flagged, not re-triaged. With no open items the draft is a clean bill. Composes from prior risk-watcher, escalation-manager, and voice-of-customer drafts when they exist; gathers directly otherwise. |

## Composition

Agents build on each other's outputs. The approval queue is the only
channel between them: no new store, no new framework.

The first use is qbr-builder. When prior drafts from renewal-desk,
expansion-spotter, and voice-of-customer exist for the account, the
QBR is built from those drafts instead of re-gathering the same
context. Only the account record is gathered directly. The composed
draft carries the deep links from its source drafts, and a
harness-written line names the agents it was composed from.

When no prior drafts exist, qbr-builder gathers directly, exactly as
before. Composition is an optimization, never a hard dependency.

The second use is risk-watcher. When prior drafts from renewal-desk,
advocate-finder, and voice-of-customer exist for the account, the risk
brief is built from those drafts; only the fresh risk signals (account
record, champion job changes, champion list, Zendesk tickets, Salesforce
cases) are gathered directly.

The third use is escalation-manager. When prior drafts from
risk-watcher, renewal-desk, and voice-of-customer exist for the account,
the escalation plan is built from those drafts; only the fresh
escalation signals (account record, opportunities, cases, champion job
changes, champion list, Zendesk tickets) are gathered directly.

The fourth use is competitive-defense. When prior drafts from
voice-of-customer, expansion-spotter, and risk-watcher exist for the
account, the defense brief is built from those drafts; only the fresh
competitive signals (account record, opportunities, champion list,
Gmail, Gong, Zoom, Zendesk, Slack) are gathered directly, and
Salesforce cases are left to the source drafts.

The fifth use is stakeholder-mapper. When prior drafts from
advocate-finder, risk-watcher, and voice-of-customer exist for the
account, the map is built from those drafts; only the fresh person
signals (account record, champion list and job changes, Gmail, Gong
calls, Zoom recordings, Slack) are gathered directly, and the
transcripts are left to the source drafts.

The sixth use is case-triage. When prior drafts from risk-watcher,
escalation-manager, and voice-of-customer exist for the account, the
brief is built from those drafts as well as the fresh gather: an item
the other agents already covered is flagged, not re-triaged. The
items themselves are always gathered directly (cases, tickets,
issues, Gmail threads); they are the job, not the context.

## Memory

The harness remembers what the human decided. Every decision in the
approval queue is recorded for that draft's account: approved,
rejected, and edited. An edit stores the correction itself, a diff
when the change is small, the edited text when the draft was
rewritten. The next run for the same account loads that memory as
context, newest first, bounded by the token budget. Fresh tool results
still come first; memory only spends what the budget has left.

Memory never crosses accounts, and it is local only: one JSON file
(`memory.json`, `HARNESS_MEMORY_FILE` to move it). Nothing in it is
ever sent anywhere. At most 50 records are kept per account.

```bash
# Run cold, without memory.
python -m harness.run run --agent renewal-desk --account "Acme Corp" --no-memory

# Inspect one account's memory, or forget it.
python -m harness.run memory show "Acme Corp"
python -m harness.run memory clear "Acme Corp"
python -m harness.run memory accounts
```

## Write-back

The first write tools plus approval-gated execution. The updater agent
drafts Salesforce changes for an account (field changes, activity logs,
stage moves) from the account context other agents gathered. The draft
carries a harness-computed dry-run preview (old -> new for every
change) and the machine-readable write plan. Nothing executes until a
human approves the draft in the queue; the executor in
harness/writeback.py then runs the plan, exactly once per change, and
records the result on the draft. A rejected draft never executes. A
failed change can be retried by approving again; a change that already
applied is never run twice, so an activity log is never created twice.

```bash
# Draft a stage move and an activity log (stub mode: simulated, nothing changes).
python -m harness.run run --agent salesforce-updater --account "Acme Corp" --stub \
  --change "stage 006STUB000000001 Closed Won" \
  --change "log 001STUB000000001 Call with Acme Corp | Discussed renewal" --show

# Review the preview, then approve: the write applies and is recorded.
python -m harness.run approvals show <id>
python -m harness.run approvals approve <id>
```

Changes are named explicitly with `--change`, repeatable:
`update <Object> <id> <Field>=<value>[, ...]`, `stage <id>
<StageName>`, `log <id> <Subject> [| <Description>]`. In stub mode a
write reports what would change and changes nothing; the result is
marked simulated, so a demo can never be mistaken for a real change.
Editing a write draft's text does not change its write plan; to change
what will execute, re-run the updater.

## Escalations

Escalation-manager owns escalations end to end. When a high-severity
signal fires (from a risk-watcher brief or from its own gather), it
drafts the escalation plan: the situation with evidence and deep links,
the people to loop in (account owner, active champions), the ask, the
exec update and the customer comms as drafts, and what happens if it
does not land. The plan, the ask, and the fallback are harness-written,
never model prose.

One record per signal per account lives in the escalation store
(`escalations.json`, `HARNESS_ESCALATIONS_FILE` to move it), with three
states: open, escalated, resolved. A second run for the same signal
drafts a status note, not a second plan. Approving a plan in the queue
moves its records to escalated. A rejected plan leaves its records
open: the human rejected the plan, not the risk, so the next run may
draft again (the rejection is in memory, so the next draft knows).
Resolving is a human decision only, two paths: the `escalations
resolve` command, or approving a closure draft the agent wrote when the
signal was no longer detected in a fresh gather. A resolved record
stays resolved even if the signal fires again, and the agent never
resolves a record on its own.

```bash
# Draft an escalation plan for the account's high-severity signals.
python -m harness.run run --agent escalation-manager --account "Acme Corp" --stub --show

# See the escalation records and their states.
python -m harness.run escalations list
python -m harness.run escalations show <id>

# Close one out, once the underlying issue is fixed.
python -m harness.run escalations resolve <id>
```

## Scheduling

A schedule names an agent, an account, and a cadence: daily or weekly,
at a wall-clock time in a named timezone. The scheduler runs due jobs
through the same path as a manual `run`, so a scheduled run drafts
into the approval queue exactly like a typed one: it never writes
back without human approval, it respects the per-job token budget and
the memory controls, and a failed run is logged on its schedule and
does not block the next job. A missed run fires once, not once per
missed cadence.

```bash
# A daily briefer at 07:00 Los Angeles time.
python -m harness.run schedules add --agent briefer --account "Acme Corp" --cadence daily --time 07:00 --timezone America/Los_Angeles

# A weekly renewal check on Mondays.
python -m harness.run schedules add --agent renewal-desk --account "Acme Corp" --cadence weekly --weekday Mon --time 08:00 --timezone America/Los_Angeles

python -m harness.run schedules list
python -m harness.run schedules remove <id>

# Run due jobs once, then exit. The mode a system cron calls.
python -m harness.run scheduler --once

# Or run the daemon: it holds a lock so two cannot run at once, wakes
# when the next job is due, and stops cleanly on Ctrl-C.
python -m harness.run scheduler
```

Schedules live in one JSON file (`schedules.json`, `HARNESS_SCHEDULES_FILE`
to move it), human-editable, same file-backed pattern as the approval
queue and memory. The daemon reloads it every cycle, so edits take
effect without a restart.

## MCP client

Until v1.5.0 the harness only called its own connector tools
in-process; the MCP servers in `connectors/` were standalone, for
other clients to call. The MCP client turns that around: it connects
to MCP servers the user already runs, over stdio or SSE, and
registers their tools in the registry alongside the built-ins. That
is the multiplier: instead of building connectors one by one, Belay
can use any MCP server the user already has connected.

Servers live in one JSON file (`mcp-servers.json`,
`HARNESS_MCP_SERVERS_FILE` to move it), human-editable, same
file-backed pattern as schedules and memory. A missing file means no
external servers.

```bash
# A stdio server: the harness's own Gmail server, as an external server.
python -m harness.run mcp-servers add --name gmail --stdio "python -m connectors.gmail.server" --read-only

# An SSE server.
python -m harness.run mcp-servers add --name docs --sse https://example.com/mcp

# List servers and their live status (tool count, or why unreachable).
python -m harness.run mcp-servers list
python -m harness.run mcp-servers remove gmail
```

External tools are namespaced by server (`gmail.gmail_search_messages`),
so a tool name can never collide with a built-in tool or with the same
tool from another server. Tool selection is by description: the job
text is scored against each external tool's name and description with
the same whole-word matching the router uses for agents, and only the
matching tools are called, with arguments filled from the job (the
account for account-like parameters, the account or job text for
search-like ones). A tool whose required arguments the job cannot fill
is skipped, never guessed.

Adapters: where an external tool's shape does not fit the harness, an
explicit adapter wraps it summary-first. Adapters are registered by
bare tool name (`register_adapter` in `harness/mcp_client.py`). The
shipped example adapts `gmail_search_messages`: it keeps only the
summary keys, caps matches at 5, and truncates excerpts to 200 chars,
so a full message body can never reach a prompt through that tool.

Safety: an external tool is a write tool unless its MCP annotations
declare `readOnlyHint=true`. A write tool is never selected for
gathering; it only ever runs through the approval-gated write-back
path, same as a built-in write tool. A server can also assert
read-only in its config (`--read-only`): the user states every tool
on that server only reads, which is how servers whose tools carry no
annotations become usable for gathering. The assertion is the user's
word, not a check, so assert it only for servers known to be
read-only. A server that fails to connect is reported and skipped,
never fatal. Connections are per call: each tool call opens a fresh
session, calls the tool, and closes it.

## Dashboard

The dashboard is the UI the CSM operates from. It is the single
interface for the whole harness: eight views over the live store
files, all numbers from real harness state, nothing invented.

- **Overview:** the queue stat strip (accounts watched, at-risk
  drafts, drafts ready, advocates found) plus the harness beyond the
  queue (open escalations, due schedules, accounts with memory, write
  drafts pending), sources, event log, token budget.
- **Queue:** every draft with its completeness check and source
  links; approve, edit, and reject call the same approval queue the
  CLI uses, and an edit is recorded as memory with the correction.
- **Memory:** per-account records, newest first, with the correction
  on edited drafts; forget one account from here.
- **Schedules:** every schedule with its next run time and last-run
  state; add and remove from here.
- **Escalations:** every escalation record with its state; resolve
  one from here, with a note.
- **Write-back:** write drafts with their plan and, once approved,
  what applied, op by op.
- **MCP servers:** configured external servers; add and remove from
  here, and probe one to list its tools or see why it is unreachable.
- **Agents:** the fifteen agents, one line each, with last-run state.

When a store is empty the view says so; a missing or corrupt store
file renders as an empty view, never a crash. Everything is a draft
until a human approves it.

```bash
python -m harness.run dashboard
# or: python -m harness.dashboard
```

Then open http://127.0.0.1:8000. Local-only, localhost, no auth. Approve
and reject buttons call the same approval queue the CLI uses.

## Architecture v1.x (all shipped)

Architecture first, then agents. These shipped as minor releases before the
agent candidates below.

| Piece | Target | What it will do |
|---|---|---|
| dashboard | v0.8.0 (shipped), finished v1.11.0 | Local web UI over the live harness state, one interface for the whole harness: overview, queue (approve/edit/reject), memory, schedules, escalations, write-back, MCP servers, agents. Pulled forward ahead of v1.0.0 at Justin's Nov 20 decision; finished after the v1 line, as v1.11.0, not v2.0.0: the spec reserves v2.0.0 for a major new capability family. |
| composition | v1.1.0 (shipped) | Agents build on each other's outputs; the QBR is built from renewal-desk, expansion-spotter, and voice-of-customer drafts, falling back to direct gathering when no prior drafts exist. |
| memory | v1.2.0 (shipped) | Persistent memory across runs: past drafts, decisions, and corrections per account, injected as context next run, bounded by the token budget. Capture in the approval queue; edits store the correction itself. Local only, never crosses accounts. |
| write-back | v1.3.0 (shipped) | The first write tools plus approval-gated execution. The Salesforce updater agent drafts field changes, activity logs, and stage moves; only an approved write draft executes, exactly once per change. |
| scheduling | v1.4.0 (shipped) | A daemon that runs the harness on its own, so watch agents actually watch: file-backed schedules (daily/weekly, named timezone), a run-once mode for system cron, a single-instance daemon, scheduled runs draft into the approval queue only. |
| MCP client | v1.5.0 (shipped) | A harness-side MCP client that picks up MCP servers the user already has connected: stdio and SSE, namespaced tools, selection by description, adapters, write-flag safety. |

## Agents v1.6.0–v1.10.0 (all shipped)

| Agent | Target | What it will do |
|---|---|---|
| risk-watcher | v1.6.0 (shipped) | Watches continuously for churn signals: usage drops, ticket spikes, champion departure, sponsor disengagement. When a champion leaves, it asks whether the org mandate changed, names the successor question, and validates the successor's priorities. |
| escalation-manager | v1.7.0 (shipped) | Owns escalations end to end: drafts the escalation plan (situation, people, ask, exec update, customer comms, fallback) for high-severity signals, and tracks each escalation open, escalated, resolved, one record per signal per account. |
| competitive-defense | v1.8.0 (shipped) | Watches for displacement risk: competitor mentions, evaluation signals, champion doubt. Drafts the defense brief: threats ranked by severity (harness-computed), evidence with its source, the vulnerability, the wedge to hold the account, and the proof points to use. A rival is named only when the records name one. |
| stakeholder-mapper | v1.9.0 (shipped) | Builds the relationship map: users, champions, economic buyers, blockers, and how they connect. Records influence, not just titles. |
| case-triage | v1.10.0 (shipped) | Triages incoming cases, tickets, and issues: groups them by subject across systems, ranks them by priority (harness-computed), and drafts a first response or internal summary per item with a suggested owner. Stale, duplicated, or already-covered items are flagged, not re-triaged. |

## Connectors

| Connector | Status | Tools |
|---|---|---|
| Salesforce | v0.2, writes v1.3.0 | `salesforce_get_account`, `salesforce_list_opportunities`, `salesforce_list_cases`, `salesforce_update_fields`, `salesforce_log_activity`, `salesforce_move_stage` |
| Slack | v0.2 | `slack_search_messages`, `slack_list_channels`, `slack_get_thread` |
| Gong | v0.8.2 | `gong_list_calls`, `gong_get_call_summary`, `gong_search_transcripts` |
| HubSpot | v0.8.3 | `hubspot_get_company`, `hubspot_list_deals`, `hubspot_list_tickets` |
| Gmail | v0.8.4 | `gmail_search_messages`, `gmail_get_thread` |
| Zoom | v0.8.5 | `zoom_list_recordings`, `zoom_get_recording_summary`, `zoom_search_transcripts` |
| Zendesk | v0.8.6 | `zendesk_list_tickets`, `zendesk_search_tickets`, `zendesk_get_ticket` |
| Linear | v0.8.7 | `linear_list_issues`, `linear_search_issues`, `linear_get_issue` |
| Champify | v0.8.8 | `champify_list_champions`, `champify_list_job_changes`, `champify_get_champion` |

Each connector exposes its tools two ways, from one implementation
(`tools.py`): in-process, through the harness tool registry (the v0.2 path),
and as a standalone MCP server (`server.py`, FastMCP over stdio) that any
MCP-capable LLM can call. Run a connector as an MCP server with:

```bash
python -m connectors.salesforce.server
python -m connectors.slack.server
python -m connectors.gong.server
python -m connectors.hubspot.server
python -m connectors.gmail.server
python -m connectors.zoom.server
python -m connectors.zendesk.server
python -m connectors.linear.server
python -m connectors.champify.server
```

The MCP servers are also callable from the harness via an MCP client
(v1.5.0): external servers connect over stdio or SSE and their tools
register in the registry, namespaced by server, alongside the
in-process connector tools.

## What is real vs scaffolded (v1.11.0 honesty)

**Real, tested (514 unit tests, all passing):**
- Router (explicit, keyword, small-model LLM, default), tool registry
  (summary-first), approval queue (file-backed, persists), token budget,
  stub LLM, OpenAI-compatible LLM client (tested with a mocked transport).
- All fifteen agents, end to end, in stub mode: renewal desk, briefer,
  expansion spotter, onboarding guide, scout, qbr builder, advocate
  finder, voice of customer, value finder, Salesforce updater, risk
  watcher, escalation manager, competitive defense, stakeholder
  mapper, and case triage.
- Write-back: the updater agent drafts changes with a harness-computed
  dry-run preview; only an approved write draft executes, exactly once
  per change, and rejected drafts never execute; stub writes simulate
  and change nothing.
- Proof checklist: every agent self-checks its draft against a "done"
  checklist before queueing, and every draft carries clickable deep
  links to the records it cites; failures are flagged in the draft
  metadata and shown in `approvals show`, never a block, and no
  aggregate score is shown anywhere.
- Salesforce, Slack, Gong, HubSpot, Gmail, Zoom, Zendesk, Linear, and Champify MCP servers: verified over the real MCP protocol
  (a client connected over stdio, listed tools, called one, got a result).
- The demo loop (`run` -> draft -> approval queue) in stub mode.
- Scheduling: file-backed schedules (daily/weekly, named timezone),
  a run-once mode for system cron, and a single-instance daemon.
  Scheduled runs draft into the approval queue only; a failed run is
  logged on its schedule and never blocks the next job.
- MCP client: stdio and SSE servers connect over the real MCP
  protocol and their tools register namespaced by server; tools are
  selected by description and called with arguments filled from the
  job; a server that fails to connect is reported and skipped; write
  tools are never gathered, only approval-gated execution runs them.
- The dashboard (v0.8.0, finished v1.11.0): local FastAPI server
  reading the live store files. Overview, approval queue with
  completeness checks, source links, and working approve/edit/reject,
  memory, schedules, escalations, write-back, MCP servers, agents.
  Every number comes from harness state; an empty store renders an
  honest empty state, and a corrupt store file degrades to empty,
  never a crash.

**Written, not verified against a live system:**
- The live Salesforce REST/SOQL calls, the live Slack Web API calls,
  the live Gong REST API calls, the live HubSpot CRM API calls, the
  live Gmail API calls, the live Zoom API calls, the live Zendesk API
  calls, the live Linear GraphQL API calls, the live Champify API calls,
  and the live Salesforce write calls (PATCH for field changes and
  stage moves, POST for activity logs). They are real code, written
  against the documented APIs, but no credentials were available while
  building v0.2 (Salesforce/Slack), v0.8.2 (Gong), v0.8.3 (HubSpot),
  v0.8.4 (Gmail), v0.8.5 (Zoom), v0.8.6 (Zendesk), v0.8.7 (Linear),
  v0.8.8 (Champify), or v1.3.0 (Salesforce writes), so they have not run
  against a real org, workspace, Gong account, HubSpot portal, mailbox,
  Zoom account, Zendesk instance, Linear workspace, or Champify account.
  Treat them as "written, not verified" until someone runs them with
  real credentials. Stub mode is what runs in the demo and the tests. One caveat, for Champify only:
  Champify publishes no public API reference, so its endpoint shapes
  are assumed from the product's data model, not from documentation,
  and must be corrected against the real API the first time a key is
  available.

**Shipped in the v1 line, nothing scaffolded left:**
- Architecture pieces: composition (v1.1.0), memory (v1.2.0),
  write-back (v1.3.0), scheduling (v1.4.0), MCP client (v1.5.0).
- New agents: risk-watcher (v1.6.0), escalation-manager (v1.7.0), competitive-defense (v1.8.0), stakeholder-mapper (v1.9.0), case-triage (v1.10.0).
- Finished dashboard (v1.11.0).

## Adding a connector

Copy `connectors/slack/` to `connectors/<name>/`. Write the pure tool
functions in `tools.py` (plain functions, return dicts, summary-first).
Wrap them in `server.py` with FastMCP. Register them in
`harness/run.py` `build_registry()`. Add the env vars to `.env.example`.
See `connectors/base.py` for the pattern, and `connectors/gong/` for a
worked example of a built connector.

## Related work

- [MCP Readiness Checker](https://jfournier.net/tools/mcp-readiness/):
  scores whether a team is ready to run agents against their data. Run
  it before wiring this harness to a real org.
- ["AI is taking the admin. The judgment is the job."](https://jfournier.net/articles/ai-admin-judgment/):
  the positioning piece this harness follows from.

## License

MIT. See [LICENSE](LICENSE).
