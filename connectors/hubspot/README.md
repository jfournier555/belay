# HubSpot connector (v0.8.3)

The HubSpot connector is the Salesforce alternative: the second CRM in
the harness. It covers the company record, open deals, and open support
tickets, the same shape as the Salesforce connector (account,
opportunities, cases), so it is the cheapest connector to build and it
doubles who can use Belay: a HubSpot shop gets the same renewal plan
without Salesforce.

## Tools

Follow the pattern in connectors/base.py: pure functions in `tools.py`,
FastMCP wrapper in `server.py`, same shape as Salesforce and Slack.

- `hubspot_get_company`: company summary (name, domain, revenue,
  industry, size, owner). The owner is the HubSpot owner id, not a name.
- `hubspot_list_deals`: open deals for the company (name, stage, amount,
  close date, type). Filters `hs_is_closed` false and resolves the
  company id first, because deal search has no company-name filter.
- `hubspot_list_tickets`: open support tickets for the company (subject,
  status, priority, age). Filters `hs_ticket_status` not "4", which is
  Closed in the default ticket pipeline; a portal with a custom pipeline
  may need that filter adjusted. Ages are computed from `createdate`.

## Credentials

HubSpot private app access token, via env var `HUBSPOT_ACCESS_TOKEN`
(Bearer auth). The portal (hub) id, for deep links, comes from env var
`HUBSPOT_PORTAL_ID` when set, else the account-info API
(`GET /account-info/v3/details`, needs the account-info scope). Without
a portal id the harness still gathers, but builds no HubSpot links: it
never guesses one. Stub mode returns sample company data when no token
is present, same as the other connectors.

## CRM selector

Renewal-desk gathers from HubSpot when HubSpot is configured and
Salesforce is not; otherwise it gathers from Salesforce. The choice is
the registry's `crm` attribute, set by `build_registry()` from the
config; a registry built directly defaults to Salesforce, so the demo
is unchanged. When both CRMs are configured, Salesforce wins. Only
renewal-desk is wired this way in v0.8.3; the other agents still gather
from Salesforce. The proof checklist follows the CRM: renewal-desk has
a HubSpot checklist (same labels, HubSpot tool names) used on the
HubSpot path.

## Deep links

Every HubSpot result carries record ids and the portal id. The harness
builds deep links from them (`https://app.hubspot.com/contacts/
<portalId>/company/<companyId>`, same pattern for `/deal/<dealId>` and
`/ticket/<ticketId>`; stub links use the `.invalid` host with portal id
0), the same way it builds Salesforce Lightning links. The model never
sees a URL: HubSpot results contain ids only, no URL fields.

## Live path status

Written, not verified: the live calls are real code, written against
the documented HubSpot CRM API (POST /crm/v3/objects/companies/search,
/deals/search, /tickets/search; GET /account-info/v3/details), but no
token was available while building v0.8.3, so they have not run against
a real portal. Stub mode is what runs in the demo and the tests.
