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
3. Otherwise the account is unknown, and the prompt is NOT refused
   (v1.16.0): a cross-account question is answered from the queue and
   memory data Belay already has ("what account has most risk" is
   answered from the risk-watcher drafts in the queue), and anything
   else comes back as a needs-account reply that asks which account,
   with a working example, instead of an error. A draft is never
   queued for the wrong account, and a question that genuinely needs
   one is asked, not refused.
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
    ValueError on empty text or a routing failure.

    The result always carries "kind":
    - "draft": the prompt ran an agent; "item", "agent",
      "display_name", "route", "account" as before.
    - "answer": a cross-account question answered from the queue and
      memory data, with no agent run and no new draft; "answer",
      "accounts", "item_ids".
    - "needs_account": the prompt names no account and the question
      cannot be answered without one; "message" asks which account,
      with a working "example", and "known_accounts" lists the
      accounts Belay knows. No draft is queued. The caller shows
      this in place instead of an error: asking is not refusing.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Prompt needs some text")
    queue = queue if queue is not None else ApprovalQueue(config.approvals_file)
    memory = memory if memory is not None else AccountMemory(config.memory_file)
    schedules = (
        schedules if schedules is not None else ScheduleStore(config.schedules_file)
    )
    known = known_accounts(queue, memory, schedules)
    try:
        account = resolve_account(text, known)
    except AmbiguousAccountError as exc:
        return {
            "kind": "needs_account",
            "message": (
                "I can't tell which account you mean: %s. "
                "Name one account."
                % " and ".join(exc.candidates)
            ),
            "candidates": exc.candidates,
            "known_accounts": sorted(known),
            "example": "draft a renewal brief for %s" % exc.candidates[0],
        }
    except UnknownAccountError:
        answer = answer_cross_account(text, queue, memory)
        if answer is not None:
            return answer
        return {
            "kind": "needs_account",
            "message": (
                "I don't know which account you mean. Name the "
                "account, and I'll draft for it."
            ),
            "known_accounts": sorted(known),
            "example": (
                "draft a renewal brief for %s"
                % (sorted(known)[0] if known else "Acme Corp")
            ),
        }
    job = {"text": text, "account": account}
    item, _draft, agent, route = execute_job(config, job)
    return {
        "kind": "draft",
        "item": item,
        "agent": agent.name,
        "display_name": agent.display_name,
        "route": {"confidence": route.confidence, "reason": route.reason},
        "account": account,
    }


# ---------------------------------------------------------------------------
# Cross-account questions: answered from data Belay already has.
# ---------------------------------------------------------------------------

_RISK_WORDS = ("risk", "at-risk", "at risk", "churn", "riskiest")
_ACCOUNT_LIST_WORDS = ("which accounts", "list accounts", "what accounts")


def _risk_answer(text: str, queue: ApprovalQueue) -> dict | None:
    """Answer a risk question from the risk-watcher drafts in the
    queue. The account with the most risk-watcher drafts is the one
    with the most risk signals; renewal-desk drafts pending are the
    other at-risk work. Returns None when the text is not a risk
    question."""
    lowered = text.lower()
    if not any(word in lowered for word in _RISK_WORDS):
        return None
    items = queue.list_all()
    risk_items = [item for item in items if item.agent == "risk-watcher"]
    renewal_items = [
        item
        for item in items
        if item.agent == "renewal-desk" and item.status == "pending"
    ]
    counts: dict[str, list] = {}
    for item in risk_items:
        account = _extract_account(item.job)
        if account:
            counts.setdefault(account, []).append(item)
    if not counts and not renewal_items:
        return {
            "kind": "answer",
            "answer": (
                "No accounts are flagged at-risk in the queue right "
                "now: there are no risk-watcher drafts on file."
            ),
            "accounts": [],
            "item_ids": [],
        }
    ranked = sorted(
        counts.items(), key=lambda kv: (-len(kv[1]), kv[0])
    )
    accounts = [account for account, _items in ranked]
    lines = []
    for account, account_items in ranked:
        lines.append(
            "%s: %d risk signal(s), latest draft %s"
            % (account, len(account_items), account_items[-1].id)
        )
    if renewal_items:
        renewal_accounts = sorted(
            {
                _extract_account(item.job)
                for item in renewal_items
                if _extract_account(item.job)
            }
        )
        for account in renewal_accounts:
            if account not in accounts:
                accounts.append(account)
        lines.append(
            "%d renewal draft(s) pending for: %s"
            % (len(renewal_items), ", ".join(renewal_accounts))
        )
    answer = "Accounts flagged at-risk (%d): %s" % (
        len(accounts),
        "; ".join(lines),
    )
    if ranked:
        answer = "Most risk: %s. %s" % (ranked[0][0], answer)
    return {
        "kind": "answer",
        "answer": answer,
        "accounts": accounts,
        "item_ids": [item.id for item in risk_items + renewal_items],
    }


def _accounts_answer(text: str, known: set[str]) -> dict | None:
    """Answer a "which accounts" question from the accounts Belay
    knows. Returns None when the text is not an account-list
    question."""
    lowered = text.lower()
    if not any(word in lowered for word in _ACCOUNT_LIST_WORDS):
        return None
    if not known:
        return {
            "kind": "answer",
            "answer": "Belay doesn't know any accounts yet.",
            "accounts": [],
            "item_ids": [],
        }
    return {
        "kind": "answer",
        "answer": "Accounts Belay knows: %s." % ", ".join(sorted(known)),
        "accounts": sorted(known),
        "item_ids": [],
    }


def answer_cross_account(
    text: str,
    queue: ApprovalQueue,
    memory: AccountMemory | None = None,
) -> dict | None:
    """Answer a question that spans accounts from the queue and
    memory data, with no agent run and no new draft. Risk questions
    are answered from the risk-watcher drafts; account-list
    questions from the known accounts. Anything else is None: the
    caller asks which account instead."""
    risk = _risk_answer(text, queue)
    if risk is not None:
        return risk
    known = known_accounts(queue, memory, None)
    return _accounts_answer(text, known)
