"""CSV accounts connector tools (v0.18.1, the 10th connector).

A local connector: it imports account records from a manual CSV file,
so Belay runs without live customer accounts. No vendor API, no
credentials, no network. The file path comes from config
(HARNESS_CSV_ACCOUNTS_FILE).

Tools exposed (both in-process and as MCP tools via server.py):
- csv_list_accounts: every account in the file (summary fields).
- csv_get_account: one account record, same shape as
  salesforce_get_account (name, ARR, renewal date, health, owner,
  industry, employees, open cases, last touch), plus domain, contacts,
  and notes from the CSV.
- csv_list_opportunities: the account's renewal opportunity, from the
  opp_* columns when present.
- csv_list_cases: the account's support cases, from the `cases` column
  when present.

Read-only, by design: there is no create, update, or delete tool, and
nothing here ever writes to the file.

The CSV is the CRM when it is configured and neither Salesforce nor
HubSpot is: build_registry then serves the account-record tools
agents call (salesforce_get_account and friends) from this file, so
every agent gathers real file data with no agent changes. Those
results carry "source": "csv", so the provenance is explicit in
every result. There are no deep links for CSV records: no CRM UI
exists to link to, and a missing link is a gap, never a guess.

Gaps, not errors: a missing file, a missing account name, a bad
number, a bad date, or an unknown health value is a named gap in
the returned dict. A run never crashes on a bad row; the row is
skipped and the gap says which row and why.

CSV schema (header row; only `name` is required):
    name, domain, arr, currency, renewal_date, health, owner,
    industry, employees, contacts, open_cases, last_touch, notes,
    opp_name, opp_stage, opp_amount, opp_close_date, opp_type, cases

- health is green, yellow, or red. Anything else is a gap.
- contacts is semicolon-separated "Name <email>" entries.
- cases is semicolon-separated "subject|status|priority|age_days"
  entries.
- dates are YYYY-MM-DD. Anything else is a gap.
- numbers (arr, employees, open_cases, opp_amount, age_days) parse
  leniently; a bad number is a gap and the field stays empty.
"""

from __future__ import annotations

import csv
import os
import re

from harness.config import Config
from harness.registry import Tool

# The sample accounts file shipped with the connector. Ten fictional
# accounts; the first is Acme Corp, matching the stub account the
# demo and the other connectors already use, so the demo drafts stay
# consistent when they gather from the file instead of the stub.
SAMPLE_CSV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sample_accounts.csv"
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEALTH_VALUES = ("green", "yellow", "red")

# Columns the loader understands. Anything else in the header is
# ignored: a user may keep extra columns for their own notes.
_COLUMNS = (
    "name",
    "domain",
    "arr",
    "currency",
    "renewal_date",
    "health",
    "owner",
    "industry",
    "employees",
    "contacts",
    "open_cases",
    "last_touch",
    "notes",
    "opp_name",
    "opp_stage",
    "opp_amount",
    "opp_close_date",
    "opp_type",
    "cases",
)


def _clean(value) -> str:
    """One CSV cell as stripped text. None and non-strings become ""."""
    if value is None:
        return ""
    return str(value).strip()


def _parse_number(raw: str):
    """A CSV number as int when whole, else float, else None. Commas
    and a leading $ are tolerated (a CSM pasting from a sheet)."""
    text = _clean(raw).replace(",", "").lstrip("$")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def _parse_int(raw: str):
    """A CSV integer, or None when blank or unparseable."""
    number = _parse_number(raw)
    if number is None:
        return None
    return int(number)


def _parse_contacts(raw: str) -> list[dict]:
    """Semicolon-separated "Name <email>" entries. An entry without
    an email keeps its name and no email, never a guess."""
    contacts = []
    for entry in _clean(raw).split(";"):
        entry = entry.strip()
        if not entry:
            continue
        name, email = entry, None
        match = re.search(r"<([^<>]+)>", entry)
        if match:
            email = match.group(1).strip()
            name = entry[: match.start()].strip() or email
        contacts.append({"name": name, "email": email})
    return contacts


def _parse_cases(raw: str, account: str) -> list[dict]:
    """Semicolon-separated "subject|status|priority|age_days"
    entries. A short entry (subject only) still loads, with the rest
    empty; a bad age is a gap on the entry, not a dropped case."""
    cases = []
    for index, entry in enumerate(_clean(raw).split(";")):
        entry = entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split("|")]
        age = _parse_int(parts[3]) if len(parts) > 3 else None
        cases.append(
            {
                "number": None,
                "id": "csv:%s:case:%d" % (account, index + 1),
                "subject": parts[0],
                "status": parts[1] if len(parts) > 1 else "",
                "priority": parts[2] if len(parts) > 2 else "",
                "age_days": age,
            }
        )
    return cases


def _account_id(name: str) -> str:
    """A stable id for a CSV account. Identity only: there is no CRM
    UI to link to, so no deep link is ever built from it."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return "csv:" + (slug or "account")


def _row_to_account(row: dict, row_number: int) -> tuple[dict | None, str | None]:
    """One CSV row as an account record, or (None, gap) when the row
    cannot load. Only a missing name drops a row; every other bad
    field is a gap on that field and the row still loads."""
    gaps = []
    name = _clean(row.get("name"))
    if not name:
        return None, "Row %d: no account name, skipped." % row_number
    health = _clean(row.get("health")).lower()
    if health and health not in _HEALTH_VALUES:
        gaps.append(
            "Row %d (%s): health %r is not green, yellow, or red; left empty."
            % (row_number, name, health)
        )
        health = ""
    renewal_date = _clean(row.get("renewal_date"))
    if renewal_date and not _DATE_RE.match(renewal_date):
        gaps.append(
            "Row %d (%s): renewal_date %r is not YYYY-MM-DD; left empty."
            % (row_number, name, renewal_date)
        )
        renewal_date = ""
    last_touch = _clean(row.get("last_touch"))
    if last_touch and not _DATE_RE.match(last_touch):
        gaps.append(
            "Row %d (%s): last_touch %r is not YYYY-MM-DD; left empty."
            % (row_number, name, last_touch)
        )
        last_touch = ""
    arr = _parse_number(row.get("arr"))
    if _clean(row.get("arr")) and arr is None:
        gaps.append(
            "Row %d (%s): arr %r is not a number; left empty."
            % (row_number, name, row.get("arr"))
        )
    employees = _parse_int(row.get("employees"))
    if _clean(row.get("employees")) and employees is None:
        gaps.append(
            "Row %d (%s): employees %r is not a number; left empty."
            % (row_number, name, row.get("employees"))
        )
    open_cases = _parse_int(row.get("open_cases"))
    if _clean(row.get("open_cases")) and open_cases is None:
        gaps.append(
            "Row %d (%s): open_cases %r is not a number; left empty."
            % (row_number, name, row.get("open_cases"))
        )
    opp_amount = _parse_number(row.get("opp_amount"))
    if _clean(row.get("opp_amount")) and opp_amount is None:
        gaps.append(
            "Row %d (%s): opp_amount %r is not a number; left empty."
            % (row_number, name, row.get("opp_amount"))
        )
    opp_close = _clean(row.get("opp_close_date"))
    if opp_close and not _DATE_RE.match(opp_close):
        gaps.append(
            "Row %d (%s): opp_close_date %r is not YYYY-MM-DD; left empty."
            % (row_number, name, opp_close)
        )
        opp_close = ""
    opportunity = None
    if _clean(row.get("opp_name")):
        opportunity = {
            "name": _clean(row.get("opp_name")),
            "id": _account_id(name) + ":opp",
            "stage": _clean(row.get("opp_stage")),
            "amount": opp_amount,
            "close_date": opp_close,
            "type": _clean(row.get("opp_type")),
        }
    account = {
        "source": "csv",
        "name": name,
        "id": _account_id(name),
        "domain": _clean(row.get("domain")),
        "arr": arr,
        "currency": _clean(row.get("currency")) or "USD",
        "renewal_date": renewal_date,
        "health": health,
        "owner": _clean(row.get("owner")),
        "industry": _clean(row.get("industry")),
        "employees": employees,
        "contacts": _parse_contacts(row.get("contacts")),
        "open_cases": open_cases,
        "last_touch": last_touch,
        "notes": _clean(row.get("notes")),
        "opportunity": opportunity,
        "cases": _parse_cases(row.get("cases"), name),
    }
    gap = "; ".join(gaps) if gaps else None
    return account, gap


def load_accounts(config: Config) -> dict:
    """Load every account record from the configured CSV file.

    Returns {"accounts": [...], "gaps": [...], "source": "csv",
    "file": path}. A missing file path, a missing file, or an
    unreadable file is a named gap, never an exception: the caller
    gets zero accounts and the gap says why. Malformed rows are
    skipped and named in the gaps with their row number.
    """
    path = (config.csv_accounts_file or "").strip() or None
    if not path:
        return {
            "accounts": [],
            "gaps": [
                "No CSV accounts file configured. Set "
                "HARNESS_CSV_ACCOUNTS_FILE to a CSV file path."
            ],
            "source": "csv",
            "file": None,
        }
    if not os.path.exists(path):
        return {
            "accounts": [],
            "gaps": ["CSV accounts file not found: %s" % path],
            "source": "csv",
            "file": path,
        }
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = reader.fieldnames
    except (OSError, csv.Error) as exc:
        return {
            "accounts": [],
            "gaps": ["CSV accounts file unreadable (%s): %s" % (path, exc)],
            "source": "csv",
            "file": path,
        }
    if fieldnames is None:
        return {
            "accounts": [],
            "gaps": ["CSV accounts file is empty (no header row): %s" % path],
            "source": "csv",
            "file": path,
        }
    accounts: list[dict] = []
    gaps: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        row_number = index + 2  # Row 1 is the header.
        if row is None:
            continue
        clean_row = {
            (key or "").strip(): value
            for key, value in row.items()
            if key is not None
        }
        account, gap = _row_to_account(clean_row, row_number)
        if gap:
            gaps.append(gap)
        if account is None:
            continue
        key = account["name"].lower()
        if key in seen:
            gaps.append(
                "Row %d (%s): duplicate account name, skipped."
                % (row_number, account["name"])
            )
            continue
        seen.add(key)
        accounts.append(account)
    return {
        "accounts": accounts,
        "gaps": gaps,
        "source": "csv",
        "file": path,
    }


def _find_account(accounts: list[dict], account: str) -> dict | None:
    """Find one account by name: exact match first (case-insensitive),
    then domain, then substring. No match means None, never a guess."""
    wanted = (account or "").strip().lower()
    if not wanted:
        return None
    for record in accounts:
        if record["name"].lower() == wanted:
            return record
    for record in accounts:
        if record.get("domain", "").lower() == wanted:
            return record
    for record in accounts:
        if wanted in record["name"].lower():
            return record
    return None


def _summary(record: dict) -> dict:
    """An account record reduced to the summary fields agents act on:
    the same shape salesforce_get_account returns, plus domain,
    contacts, and notes from the CSV."""
    return {
        "source": "csv",
        "name": record["name"],
        "id": record["id"],
        "domain": record.get("domain", ""),
        "arr": record.get("arr"),
        "currency": record.get("currency", "USD"),
        "renewal_date": record.get("renewal_date", ""),
        "health": record.get("health", ""),
        "owner": record.get("owner", ""),
        "industry": record.get("industry", ""),
        "employees": record.get("employees"),
        "contacts": record.get("contacts", []),
        "open_cases": record.get("open_cases"),
        "last_touch": record.get("last_touch", ""),
        "notes": record.get("notes", ""),
    }


def list_accounts(config: Config) -> dict:
    """Every account in the CSV file (summary fields), soonest renewal
    first. Accounts with no renewal date sort last. Gaps from the
    load ride along; a missing file is a gap, not an error."""
    loaded = load_accounts(config)
    accounts = sorted(
        loaded["accounts"],
        key=lambda record: (not record.get("renewal_date"), record.get("renewal_date", "")),
    )
    return {
        "source": "csv",
        "file": loaded["file"],
        "accounts": [_summary(record) for record in accounts],
        "gaps": loaded["gaps"],
    }


def get_account(config: Config, account: str) -> dict:
    """One account record by name (or domain), same shape as
    salesforce_get_account. A missing file is a named gap; an
    unknown account is an error in Salesforce's shape, never a
    guessed record."""
    loaded = load_accounts(config)
    if not loaded["accounts"] and loaded["gaps"]:
        return {
            "source": "csv",
            "file": loaded["file"],
            "gaps": loaded["gaps"],
        }
    record = _find_account(loaded["accounts"], account)
    if record is None:
        return {
            "error": "No account matched %r." % account,
            "source": "csv",
            "file": loaded["file"],
        }
    result = _summary(record)
    if loaded["gaps"]:
        result["gaps"] = loaded["gaps"]
    return result


def list_opportunities(config: Config, account: str) -> dict:
    """The account's renewal opportunity, from the opp_* columns.
    At most one per account in the CSV: a row holds one opportunity.
    An account with no opp columns returns an empty list and a gap
    note, never an invented opportunity."""
    loaded = load_accounts(config)
    if not loaded["accounts"] and loaded["gaps"]:
        return {
            "account": account,
            "opportunities": [],
            "source": "csv",
            "file": loaded["file"],
            "gaps": loaded["gaps"],
        }
    record = _find_account(loaded["accounts"], account)
    if record is None:
        return {
            "error": "No account matched %r." % account,
            "source": "csv",
            "file": loaded["file"],
        }
    opportunity = record.get("opportunity")
    opportunities = [opportunity] if opportunity else []
    gaps = list(loaded["gaps"])
    if not opportunities:
        gaps.append(
            "No opportunities in the CSV for %s." % record["name"]
        )
    return {
        "account": record["name"],
        "source": "csv",
        "file": loaded["file"],
        "opportunities": opportunities,
        "gaps": gaps,
    }


def list_cases(config: Config, account: str) -> dict:
    """The account's support cases, from the `cases` column. An
    account with no cases returns an empty list and a gap note."""
    loaded = load_accounts(config)
    if not loaded["accounts"] and loaded["gaps"]:
        return {
            "account": account,
            "cases": [],
            "source": "csv",
            "file": loaded["file"],
            "gaps": loaded["gaps"],
        }
    record = _find_account(loaded["accounts"], account)
    if record is None:
        return {
            "error": "No account matched %r." % account,
            "source": "csv",
            "file": loaded["file"],
        }
    cases = record.get("cases", [])
    gaps = list(loaded["gaps"])
    if not cases:
        gaps.append("No cases in the CSV for %s." % record["name"])
    return {
        "account": record["name"],
        "source": "csv",
        "file": loaded["file"],
        "cases": cases,
        "gaps": gaps,
    }


def _account_tools(config: Config, prefix: str) -> list[Tool]:
    """The account-record tools under the given name prefix. `csv`
    is the connector's own names; `salesforce` is the CRM alias used
    when the CSV file is the CRM, so agents' existing gather plans
    call the same tool names and get real file data. Either way the
    results carry source=csv."""
    if prefix == "salesforce":
        get_desc = (
            "Get an account summary from the CSV accounts file (the "
            "CSV file is the CRM): ARR, renewal date, health, owner. "
            "Takes account (name or id)."
        )
        opp_desc = (
            "List open opportunities for an account from the CSV "
            "accounts file (the CSV file is the CRM): stage, amount, "
            "close date, type. Takes account (name or id)."
        )
        cases_desc = (
            "List open support cases for an account from the CSV "
            "accounts file (the CSV file is the CRM): subject, status, "
            "priority, age. Takes account (name or id)."
        )
    else:
        get_desc = (
            "Get an account summary from the CSV accounts file: ARR, "
            "renewal date, health, owner. Takes account (name or id)."
        )
        opp_desc = (
            "List open opportunities for an account from the CSV "
            "accounts file: stage, amount, close date, type. Takes "
            "account (name or id)."
        )
        cases_desc = (
            "List open support cases for an account from the CSV "
            "accounts file: subject, status, priority, age. Takes "
            "account (name or id)."
        )
    return [
        Tool(
            name="%s_get_account" % prefix,
            description=get_desc,
            parameters={
                "type": "object",
                "properties": {"account": {"type": "string"}},
                "required": ["account"],
            },
            handler=lambda account: get_account(config, account),
        ),
        Tool(
            name="%s_list_opportunities" % prefix,
            description=opp_desc,
            parameters={
                "type": "object",
                "properties": {"account": {"type": "string"}},
                "required": ["account"],
            },
            handler=lambda account: list_opportunities(config, account),
        ),
        Tool(
            name="%s_list_cases" % prefix,
            description=cases_desc,
            parameters={
                "type": "object",
                "properties": {"account": {"type": "string"}},
                "required": ["account"],
            },
            handler=lambda account: list_cases(config, account),
        ),
    ]


def csv_tools(config: Config) -> list[Tool]:
    """The CSV tools under their own names, for the registry.
    Read-only: there is no create, update, or delete tool."""
    return _account_tools(config, "csv") + [
        Tool(
            name="csv_list_accounts",
            description=(
                "List every account in the CSV accounts file: name, "
                "domain, ARR, renewal date, health, owner. Soonest "
                "renewal first. Read-only."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda: list_accounts(config),
        ),
    ]


def csv_crm_tools(config: Config) -> list[Tool]:
    """The CSV account tools under the salesforce_* names, for use
    when the CSV file is the CRM (build_registry). Agents' existing
    gather plans call salesforce_get_account, and the registry serves
    real CSV data for it, so every agent works with file data and no
    agent changes. Results carry source=csv, so the provenance is
    explicit in every result."""
    return _account_tools(config, "salesforce")
