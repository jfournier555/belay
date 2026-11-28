# CSV accounts connector

Read-only. Imports account records from a manual CSV file, so Belay
runs without live customer accounts. No vendor API, no credentials,
no network: the file is the source of truth.

## Tools

- `csv_list_accounts()`: every account in the file (name, domain, ARR,
  renewal date, health, owner). Soonest renewal first.
- `csv_get_account(account)`: one account record by name (or domain),
  same shape as `salesforce_get_account`: ARR, renewal date, health,
  owner, industry, employees, contacts, open cases, last touch, plus
  domain and notes from the CSV.
- `csv_list_opportunities(account)`: the account's renewal opportunity,
  from the `opp_*` columns when present.
- `csv_list_cases(account)`: the account's support cases, from the
  `cases` column when present.

When the CSV file is the CRM (configured, and neither Salesforce nor
HubSpot is), the agents' existing account-record calls
(`salesforce_get_account` and friends) are served from the file, so
every agent gathers real file data with no agent changes. Results
carry `"source": "csv"`. There are no deep links for CSV records:
no CRM UI exists to link to.

## Configuration

One env var, no credentials:

- `HARNESS_CSV_ACCOUNTS_FILE`: path to the CSV file, e.g.
  `accounts.csv` or an absolute path. Set it in `.env` or the
  environment.

## CSV schema

Header row; only `name` is required.

    name, domain, arr, currency, renewal_date, health, owner,
    industry, employees, contacts, open_cases, last_touch, notes,
    opp_name, opp_stage, opp_amount, opp_close_date, opp_type, cases

- `health` is `green`, `yellow`, or `red`. Anything else is a gap.
- `contacts` is semicolon-separated `"Name <email>"` entries.
- `cases` is semicolon-separated `"subject|status|priority|age_days"`
  entries.
- Dates are `YYYY-MM-DD`. Anything else is a gap.
- Numbers (`arr`, `employees`, `open_cases`, `opp_amount`, `age_days`)
  parse leniently; a bad number is a gap and the field stays empty.

## Gaps, not errors

A missing file, a missing account name, a bad number, a bad date, or
an unknown health value is a named gap in the returned dict. A run
never crashes on a bad row; the row is skipped and the gap says which
row and why. A missing file is a named gap, not an error.

## Sample accounts

`sample_accounts.csv` ships with the connector: ten fictional
accounts across health states (five green, three yellow, two red).
The first is Acme Corp, matching the stub account the demo and the
other connectors already use, so the demo drafts stay consistent
when they gather from the file instead of the stub.

`setup --demo` installs the sample file next to the stores as
`sample_accounts.csv` and runs the demo agents against it, so the
demo proves the machine runs end to end on file data. To keep using
the sample accounts after the demo, set
`HARNESS_CSV_ACCOUNTS_FILE` to that path. To use your own accounts,
point it at your own CSV instead. All sample data is fictional:
no real customer names.

## Read-only

There is no create, update, or delete tool in this connector, and
nothing here ever writes to the file.
