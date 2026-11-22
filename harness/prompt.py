"""Dashboard prompt window (v1.15.0): free text in, queued draft out.

The prompt bar on the dashboard takes natural language ("draft a renewal
brief for Acme") and turns it into exactly the same run the CLI does:
the existing router picks the agent, the agent runs, the draft lands in
the approval queue with its proof checklist, and the approval gate is
unchanged. Nothing here re-implements routing or queueing; it only maps
free text to the job dict `execute_job` already takes.

Account resolution, in order:
1. A known account named in the text. Known means the harness has seen
   the account before: memory, the queue, or a schedule. The longest
   matching name wins ("Acme Corp" over "Acme"); two unrelated names
   matching at the same length is ambiguous, and the caller is told to
   name one account.
2. The router's existing rule: the words after the last "for" in the
   text. A brand-new account comes in this way.
3. Otherwise the account is unknown, and the prompt is refused with a
   plain message instead of guessing: a draft for the wrong account is
   worse than no draft.
"""

from __future__ import annotations

import re

from .agents import _extract_account
from .approvals import ApprovalQueue
from .config import Config
from .memory import AccountMemory
from .run import execute_job
from .scheduler import ScheduleStore


class UnknownAccountError(ValueError):
    """The prompt names no account the harness can resolve."""


class AmbiguousAccountError(ValueError):
    """The prompt names two or more accounts and none clearly wins."""

    def __init__(self, candidates: list[str]) -> None:
        super().__init__(
            "Ambiguous account: %s" % ", ".join(candidates)
        )
        self.candidates = candidates


def known_accounts(
    queue: ApprovalQueue | None = None,
    memory: AccountMemory | None = None,
    schedules: ScheduleStore | None = None,
) -> set[str]:
    """Every account the harness has seen: memory, queue, schedules."""
    accounts: set[str] = set()
    if memory is not None:
        accounts.update(name for name, _count in memory.accounts())
    if queue is not None:
        for item in queue.list_all():
            name = _extract_account(item.job)
            if name:
                accounts.add(name)
    if schedules is not None:
        for schedule in schedules.list():
            if schedule.account:
                accounts.add(schedule.account)
    return accounts


def resolve_account(text: str, known: set[str]) -> str:
    """Work out which account a prompt is about.

    A known account named in the text wins; the longest match wins
    among several; a tie between unrelated names is ambiguous. With no
    known match, the router's existing "for <Name>" rule applies. With
    neither, the account is unknown.
    """
    text = (text or "").strip()
    matches = [
        name
        for name in known
        if re.search(
            r"\b" + re.escape(name) + r"\b", text, re.IGNORECASE
        )
    ]
    if matches:
        longest = max(len(name) for name in matches)
        winners = sorted(
            name for name in matches if len(name) == longest
        )
        if len(winners) == 1:
            return winners[0]
        raise AmbiguousAccountError(winners)
    if " for " in text.lower():
        candidate = _extract_account({"text": text})
        if candidate and candidate != text:
            return candidate
    raise UnknownAccountError(
        "No account in the prompt. Name the account, e.g. "
        "'draft a renewal brief for Acme Corp'."
    )


def run_prompt(
    config: Config,
    text: str,
    queue: ApprovalQueue | None = None,
    memory: AccountMemory | None = None,
    schedules: ScheduleStore | None = None,
) -> dict:
    """Run one dashboard prompt through the existing execution path.

    Resolves the account from the text, then hands the job to
    `execute_job`: the router picks the agent, the agent drafts, the
    draft lands in the approval queue with its proof checklist. Raises
    ValueError on empty text or a routing failure, UnknownAccountError
    when no account resolves, AmbiguousAccountError when two accounts
    tie. Returns the queued item, the agent, the route, and the
    account.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Prompt needs some text")
    known = known_accounts(
        queue if queue is not None else ApprovalQueue(config.approvals_file),
        memory if memory is not None else AccountMemory(config.memory_file),
        schedules if schedules is not None else ScheduleStore(config.schedules_file),
    )
    account = resolve_account(text, known)
    job = {"text": text, "account": account}
    item, _draft, agent, route = execute_job(config, job)
    return {
        "item": item,
        "agent": agent.name,
        "display_name": agent.display_name,
        "route": {"confidence": route.confidence, "reason": route.reason},
        "account": account,
    }
