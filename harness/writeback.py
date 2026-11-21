"""Write-back (v1.3.0): the first write tools plus approval-gated
execution.

The rule is the whole design: a write tool is NEVER called by an agent
directly. The updater agent drafts the change, the draft sits in the
approval queue like any other draft, and only when a human approves it
does the harness execute the write. Rejected or dismissed drafts never
execute. Nothing writes directly.

Three pieces live here:
- parse_change_spec: the --change mini-syntax the CLI takes, turned
  into a change-spec dict.
- build_write_plan: change specs plus the account context the updater
  gathered, turned into a machine-readable write plan (the ops the
  executor runs) and a dry-run preview (the old -> new lines the human
  reads before approving). The preview is computed by the harness from
  the gathered records, never by the model, so an old value the harness
  did not gather is labeled "not in gathered context", never guessed.
- execute_writes: the single choke point. It runs a draft's write plan
  only when the draft is approved, only for ops not already recorded as
  applied (exactly once per op; a failed op can be retried by
  approving again), and only through tools flagged write=True in the
  registry. The results are recorded on the approval item itself.

Writes are explicit, logged, and only for the approved draft's scope:
no bulk writes, no writes outside the approved draft's plan.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .approvals import ApprovalItem, ApprovalQueue
from .registry import ToolRegistry

# Salesforce field names as the updater writes them, to the keys the
# read tools return. The stub records (and the summary-first live
# results) use the short keys; a field with no mapping here has its old
# value reported as "not in gathered context".
_OPPORTUNITY_FIELDS = {
    "StageName": "stage",
    "Amount": "amount",
    "CloseDate": "close_date",
    "Name": "name",
    "Type": "type",
}

_ACCOUNT_FIELDS = {
    "Name": "name",
    "AnnualRevenue": "arr",
    "Industry": "industry",
    "NumberOfEmployees": "employees",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Change specs: the --change mini-syntax.
# ---------------------------------------------------------------------------


def parse_change_spec(text: str) -> dict:
    """Parse one --change spec into a change-spec dict.

    Forms:
    - update <Object> <record_id> <Field>=<value>[, <Field>=<value>...]
      e.g. update Opportunity 006STUB000000001 StageName=Closed Won
    - stage <record_id> <StageName>
      e.g. stage 006STUB000000001 Closed Won
    - log <record_id> <Subject> [| <Description>]
      e.g. log 001STUB000000001 Call with Acme Corp | Discussed renewal

    Field values and stage names may contain spaces: everything after
    the fixed tokens is the value. Raises ValueError on a bad spec.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty change spec.")
    head, _, rest = raw.partition(" ")
    kind = head.lower()
    if kind == "update":
        parts = rest.strip().split(None, 2)
        if len(parts) < 3:
            raise ValueError(
                "update needs: update <Object> <record_id> <Field>=<value>"
            )
        object_name, record_id, fields_text = parts
        fields: dict[str, str] = {}
        for segment in fields_text.split(","):
            segment = segment.strip()
            if not segment:
                continue
            if "=" not in segment:
                raise ValueError(
                    "update field %r needs Field=value form." % segment
                )
            field, _, value = segment.partition("=")
            field = field.strip()
            value = value.strip()
            if not field or not value:
                raise ValueError(
                    "update field %r needs a field name and a value." % segment
                )
            fields[field] = value
        if not fields:
            raise ValueError("update needs at least one Field=value.")
        return {
            "kind": "update",
            "object": object_name,
            "record_id": record_id,
            "fields": fields,
        }
    if kind == "stage":
        parts = rest.strip().split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            raise ValueError("stage needs: stage <record_id> <StageName>")
        return {"kind": "stage", "record_id": parts[0], "stage": parts[1].strip()}
    if kind == "log":
        parts = rest.strip().split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            raise ValueError("log needs: log <record_id> <Subject>")
        subject, _, description = parts[1].partition("|")
        return {
            "kind": "log",
            "record_id": parts[0],
            "subject": subject.strip(),
            "description": description.strip() or None,
        }
    raise ValueError(
        "Unknown change spec %r: start with update, stage, or log." % head
    )


# ---------------------------------------------------------------------------
# Write plan: specs + gathered context -> ops + dry-run preview.
# ---------------------------------------------------------------------------


def _find_opportunity(gathered: dict, record_id: str) -> dict | None:
    """The opportunity with this id in the gathered results, or None."""
    result = gathered.get("salesforce_list_opportunities") or {}
    for opp in result.get("opportunities", []) or []:
        if opp.get("id") == record_id:
            return opp
    return None


def _find_account(gathered: dict, record_id: str) -> dict | None:
    """The account with this id in the gathered results, or None."""
    result = gathered.get("salesforce_get_account") or {}
    if result.get("id") == record_id:
        return result
    return None


def _record_label(object_name: str, record_id: str, gathered: dict) -> str:
    """A readable label for a record: its name when gathered, else its id."""
    if object_name == "Opportunity":
        opp = _find_opportunity(gathered, record_id)
        if opp:
            return opp.get("name") or record_id
    if object_name == "Account":
        account = _find_account(gathered, record_id)
        if account:
            return account.get("name") or record_id
    return record_id


def _old_value(
    object_name: str, record_id: str, field: str, gathered: dict
) -> str:
    """The current value of a field from the gathered records, or the
    honest fallback when the harness did not gather it."""
    if object_name == "Opportunity":
        record = _find_opportunity(gathered, record_id)
        key = _OPPORTUNITY_FIELDS.get(field)
    elif object_name == "Account":
        record = _find_account(gathered, record_id)
        key = _ACCOUNT_FIELDS.get(field)
    else:
        record, key = None, None
    if record is None or key is None or key not in record:
        return "not in gathered context"
    return str(record[key])


def build_write_plan(
    specs: list[dict], gathered: dict
) -> tuple[list[dict], list[str]]:
    """Turn change specs into (write ops, dry-run preview lines).

    Each op is what the executor runs, keyed by tool name. Each preview
    line shows old -> new for a change, or what will be created for a
    log entry. Old values come from the gathered records; a value the
    harness did not gather is labeled, never guessed. An op is never
    built from a spec the builder cannot parse: parse errors raise
    ValueError before any plan exists.
    """
    ops: list[dict] = []
    preview: list[str] = []
    for spec in specs:
        kind = spec.get("kind")
        if kind == "update":
            object_name = spec["object"]
            record_id = spec["record_id"]
            label = _record_label(object_name, record_id, gathered)
            for field, value in spec["fields"].items():
                old = _old_value(object_name, record_id, field, gathered)
                preview.append(
                    "%s %s (%s): %s: %s -> %s"
                    % (object_name, record_id, label, field, old, value)
                )
            ops.append(
                {
                    "tool": "salesforce_update_fields",
                    "object": object_name,
                    "record_id": record_id,
                    "fields": dict(spec["fields"]),
                    "label": "%s %s: %s"
                    % (
                        object_name,
                        record_id,
                        ", ".join(
                            "%s=%s" % (f, v)
                            for f, v in spec["fields"].items()
                        ),
                    ),
                }
            )
        elif kind == "stage":
            record_id = spec["record_id"]
            stage = spec["stage"]
            opp = _find_opportunity(gathered, record_id)
            old = opp.get("stage") if opp else "not in gathered context"
            label = opp.get("name") if opp else record_id
            preview.append(
                "Opportunity %s (%s): StageName: %s -> %s"
                % (record_id, label, old, stage)
            )
            ops.append(
                {
                    "tool": "salesforce_move_stage",
                    "record_id": record_id,
                    "stage": stage,
                    "previous_stage": old,
                    "label": "Opportunity %s stage -> %s" % (record_id, stage),
                }
            )
        elif kind == "log":
            record_id = spec["record_id"]
            subject = spec["subject"]
            description = spec.get("description")
            line = 'Log activity on %s: "%s"' % (record_id, subject)
            if description:
                line += ' (%s)' % description
            preview.append(line)
            op: dict = {
                "tool": "salesforce_log_activity",
                "record_id": record_id,
                "subject": subject,
                "label": 'Log activity on %s: "%s"' % (record_id, subject),
            }
            if description:
                op["description"] = description
            ops.append(op)
        else:
            raise ValueError("Unknown change spec kind: %r" % (kind,))
    return ops, preview


def format_preview(preview: list[str]) -> str:
    """The dry-run preview block that goes in the draft: what would
    change, with the warning that nothing has been applied yet."""
    lines = [
        "DRY-RUN PREVIEW - nothing has been applied. These changes apply "
        "only if a human approves this draft:"
    ]
    for line in preview:
        lines.append("- " + line)
    return "\n".join(lines)


def format_write_plan(ops: list[dict]) -> str:
    """The stored write plan as readable text, for `approvals show` and
    for the approval-time printout: exactly what approving will run."""
    if not ops:
        return "No writes in this draft."
    lines = ["Write plan (%d change(s)):" % len(ops)]
    for i, op in enumerate(ops, 1):
        lines.append("  %d. %s" % (i, op.get("label") or op.get("tool")))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Execution: the single choke point. Approved drafts only, exactly once.
# ---------------------------------------------------------------------------


def execute_writes(
    queue: ApprovalQueue, registry: ToolRegistry, item: ApprovalItem
) -> list[dict]:
    """Run an approved draft's write plan, and record the results on the
    item. Returns the recorded execution (one result per op, in plan
    order), each result carrying its op index and a success flag.

    The gate: only an item with status "approved" executes. A pending,
    rejected, or edited item raises ValueError and nothing runs. An
    item with no write plan returns [].

    Exactly once per op: an op already recorded as applied is skipped
    on a later call, so approving an already-applied draft again is a
    no-op. An op that failed is retried on the next call (approve the
    item again after fixing the cause); a successful op is never run
    twice, so a log entry is never created twice.

    A write op runs only through a tool the registry flags write=True.
    Anything else raises ValueError before it runs.
    """
    if item.status != "approved":
        raise ValueError(
            "Write plan for %s cannot execute: status is %r, not "
            "'approved'. Only an approved draft executes." % (item.id, item.status)
        )
    ops = item.writes or []
    if not ops:
        return []
    recorded = list(item.execution or [])
    by_index = {r.get("op_index"): r for r in recorded if isinstance(r, dict)}
    results: list[dict] = []
    for index, op in enumerate(ops):
        prior = by_index.get(index)
        if prior is not None and prior.get("success"):
            # Already applied: return the recorded result marked as
            # skipped, so the caller can say so instead of claiming it
            # applied again.
            skipped = dict(prior)
            skipped["skipped"] = True
            results.append(skipped)
            continue
        tool_name = op.get("tool")
        try:
            tool = registry.get(tool_name)
        except KeyError:
            result = {
                "op_index": index,
                "tool": tool_name,
                "success": False,
                "error": "Unknown tool: %r" % (tool_name,),
            }
            recorded.append(result)
            results.append(result)
            continue
        if not getattr(tool, "write", False):
            result = {
                "op_index": index,
                "tool": tool_name,
                "success": False,
                "error": "Tool %r is not a write tool; refusing to run it."
                % (tool_name,),
            }
            recorded.append(result)
            results.append(result)
            continue
        args = {k: v for k, v in op.items() if k not in ("tool", "label", "previous_stage")}
        try:
            outcome = registry.call(tool_name, **args)
        except Exception as exc:  # noqa: BLE001 - record the failure, keep going
            outcome = {"error": str(exc)}
        if isinstance(outcome, dict) and outcome.get("error"):
            result = {
                "op_index": index,
                "tool": tool_name,
                "success": False,
                "error": str(outcome["error"]),
                "result": outcome,
            }
        else:
            result = {
                "op_index": index,
                "tool": tool_name,
                "success": True,
                "result": outcome,
            }
        recorded.append(result)
        results.append(result)
    item.execution = recorded
    item.executed_at = _now_iso()
    queue.record_execution(item.id, recorded, item.executed_at)
    return results
