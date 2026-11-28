"""Book-wide risk-watcher sweep (v0.18.2).

Runs the existing risk-watcher signal logic across every account in
the book, ranks the results, and stores them for the Overview page.
The ranking is harness-computed, never model prose: severity first
(high, medium, low, none), then renewal proximity (soonest first, no
renewal date last), with a one-line reason per account (the top
signal's text, or a clean bill when there is none).

What the sweep reuses, and what it deliberately does not do:

- It reuses risk-watcher's gather calls (the direct-gather path, the
  same nine calls the agent makes when no prior drafts exist) and its
  `_risk_signals` function, so the severity scale and the signal
  definitions are the agent's own, not a second risk model.
- It does NOT call the LLM and does NOT queue a draft per account. A
  book-wide sweep that drafted a risk brief for every account would
  flood the approval queue with ten near-identical drafts. The sweep
  is the ranked table; the human drills into one account (run
  risk-watcher for it) to get the full brief.
- Account enumeration is the accounts the harness already knows:
  the CSV accounts file first (the local, no-live-data path), then
  accounts with memory records. A live CRM has no list-accounts
  tool, so it cannot enumerate the book; the CSV import is the
  enumeration path for a live book, and the gap says so.

The result is stored in sweep.json (HARNESS_SWEEP_FILE, default in
the data dir), the same store pattern as approvals.json. The
dashboard reads the stored result on every state call, so the state
endpoint stays fast: the sweep itself runs on demand, from the CLI
(`belay sweep`) or the dashboard's Run sweep button. A missing or
corrupt sweep file reads as "no sweep yet", never a crash.
"""

from __future__ import annotations

import json
import os
from datetime import date as _date
from datetime import datetime, timezone

from connectors.csv.tools import load_accounts
from harness.agents import _gather_with_results, _risk_signals, _SEVERITY_RANK
from harness.memory import AccountMemory
from harness.summarizer import TokenBudget

# The gather calls risk-watcher makes when no prior drafts exist for
# the account: account record, opportunities, cases, messages,
# transcripts, tickets, issues. The sweep uses this path for every
# account, because the sweep has no per-account draft context to
# compose from.
_RISK_GATHER_CALLS = (
    ("salesforce_get_account", lambda account: {"account": account}),
    ("salesforce_list_opportunities", lambda account: {"account": account}),
    ("salesforce_list_cases", lambda account: {"account": account}),
    ("gmail_search_messages", lambda account: {"query": account, "limit": 5}),
    ("gong_search_transcripts", lambda account: {"query": account, "limit": 5}),
    ("zoom_search_transcripts", lambda account: {"query": account, "limit": 5}),
    ("zendesk_list_tickets", lambda account: {"organization": account}),
    ("linear_list_issues", lambda account: {}),
    ("slack_search_messages", lambda account: {"query": account, "limit": 5}),
)

# Rank order for the sweep table. "none" is a clean bill: it sorts
# after low, so the worst accounts are always on top.
_SWEEP_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "none": 3}

# A renewal date that never arrives: sorts after every real date.
_NO_RENEWAL = 10**9


def _parse_date(value) -> _date | None:
    """A YYYY-MM-DD string as a date, or None when it is not one."""
    try:
        year, month, day = (int(part) for part in str(value).split("-"))
        return _date(year, month, day)
    except (ValueError, TypeError, AttributeError):
        return None


def _days_to_renewal(renewal_date: str, today: _date) -> int | None:
    """Days from today to a renewal date, or None when there is none."""
    parsed = _parse_date(renewal_date)
    if parsed is None:
        return None
    return (parsed - today).days


def sweep_accounts(config, memory: AccountMemory | None = None):
    """Every account name in the book, with gaps.

    Returns (names, gaps, records): CSV file accounts first (they carry
    renewal dates), then accounts with memory records. Names are
    deduplicated case-insensitively; the CSV name wins the casing.
    `records` maps the lowercased name to the CSV record (for its
    renewal date). A book with no accounts is an empty list plus a
    gap naming why, never an exception.
    """
    names: list[str] = []
    seen: set[str] = set()
    gaps: list[str] = []
    records: dict[str, dict] = {}
    loaded = load_accounts(config)
    for record in loaded["accounts"]:
        key = record["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(record["name"])
        records[key] = record
    gaps.extend(loaded["gaps"])
    memory = memory or AccountMemory(config.memory_file)
    for account, _count in memory.accounts():
        key = account.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(account)
    if not names:
        gaps.append(
            "No accounts found: configure HARNESS_CSV_ACCOUNTS_FILE "
            "to a CSV file, or seed demo data with `belay setup --demo`."
        )
    return names, gaps, records


def _renewal_date(results: dict, record: dict | None) -> str:
    """The renewal date for one account, from the best source that
    has one: the enumerated CSV record first, then the gathered
    account record, then the soonest open renewal opportunity's
    close date. Empty when no source has one."""
    if record and record.get("renewal_date"):
        return str(record["renewal_date"])
    account = results.get("salesforce_get_account") or {}
    if account.get("renewal_date"):
        return str(account["renewal_date"])
    soonest = None
    opps = results.get("salesforce_list_opportunities") or {}
    for opp in opps.get("opportunities", []) or []:
        if str(opp.get("type", "")).lower() != "renewal":
            continue
        if str(opp.get("stage", "")).lower() in ("closed won", "closed lost"):
            continue
        close = _parse_date(opp.get("close_date", ""))
        if close is None:
            continue
        if soonest is None or close < soonest:
            soonest = close
    return soonest.isoformat() if soonest else ""


def sweep_account(registry, account: str, budget, record=None, today=None):
    """The sweep row for one account: gather, compute the signals,
    rank them, and reduce to one line.

    Returns {account, severity, top_signal, renewal_date,
    days_to_renewal, signal_count, signals}. Severity is the worst
    signal's severity, or "none" for a clean bill. The top signal is
    the worst signal's text, or "No churn signals found." The
    signals list carries every signal (severity, kind, text) as the
    evidence behind the row.
    """
    if today is None:
        today = _date.today()
    calls = [
        (tool_name, build_kwargs(account))
        for tool_name, build_kwargs in _RISK_GATHER_CALLS
    ]
    job = {"account": account, "text": account}
    _context, _sources, _links, results = _gather_with_results(
        registry, calls, budget, job=job
    )
    signals = _risk_signals(results, account, today=today)
    renewal = _renewal_date(results, record)
    if signals:
        severity = signals[0]["severity"]
        top_signal = signals[0]["text"]
    else:
        severity = "none"
        top_signal = "No churn signals found."
    return {
        "account": account,
        "severity": severity,
        "top_signal": top_signal,
        "renewal_date": renewal,
        "days_to_renewal": _days_to_renewal(renewal, today),
        "signal_count": len(signals),
        "signals": [
            {
                "severity": signal["severity"],
                "kind": signal["kind"],
                "text": signal["text"],
            }
            for signal in signals
        ],
    }


def rank_results(results: list[dict]) -> list[dict]:
    """Rank sweep rows: severity first (high, medium, low, none),
    then renewal proximity (soonest first, no renewal date last).
    The sort key is harness-computed; the model never ranks."""
    return sorted(
        results,
        key=lambda row: (
            _SWEEP_SEVERITY_RANK.get(row["severity"], _SWEEP_SEVERITY_RANK["none"]),
            (
                row["days_to_renewal"]
                if row["days_to_renewal"] is not None
                else _NO_RENEWAL
            ),
        ),
    )


def run_sweep(registry, config, today=None) -> dict:
    """Sweep the whole book and rank it.

    Returns {"ran_at", "accounts", "results", "gaps"}: ran_at is the
    UTC timestamp, results are the ranked rows, gaps name the
    accounts that could not be enumerated or loaded. Each account
    gets its own token budget (the per-job budget), so one noisy
    account cannot starve the rest of the book.
    """
    if today is None:
        today = _date.today()
    names, gaps, records = sweep_accounts(config)
    results = []
    for account in names:
        budget = TokenBudget(config.effective_token_budget)
        results.append(
            sweep_account(
                registry,
                account,
                budget,
                record=records.get(account.lower()),
                today=today,
            )
        )
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "accounts": len(names),
        "results": rank_results(results),
        "gaps": gaps,
    }


def empty_sweep() -> dict:
    """The sweep state before any sweep has run."""
    return {"ran_at": None, "accounts": 0, "results": [], "gaps": []}


class SweepStore:
    """The stored sweep result (sweep.json).

    One JSON object: the last run_sweep result. A missing file, an
    unreadable file, or a file that is not a sweep result reads as
    the empty state, never a crash: the Overview page shows "no
    sweep yet" instead of failing.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return empty_sweep()
        if not isinstance(data, dict) or "results" not in data:
            return empty_sweep()
        return data

    def save(self, result: dict) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=True)
