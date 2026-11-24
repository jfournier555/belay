# Google Calendar connector

Read-only. Lists upcoming events and matches them to accounts by
attendee email/domain, so the briefer and scout can ground a draft
in the account's upcoming meetings.

## Tools

- `gcal_list_events(days=7, limit=10, account=None)`: upcoming events,
  soonest first. Summary, start, end, attendees, link. With
  `account`, only that account's events (name or attendee domain
  match). No event body, no attachments.
- `gcal_get_event(event_id)`: one event, same summary fields.

## Credentials

OAuth 2.0 refresh-token flow, same shape as Gmail: a client id +
secret from an OAuth client (Desktop app type) in Google Cloud
Console with the Calendar API enabled, and a refresh token minted
once with the readonly scope
`https://www.googleapis.com/auth/calendar.readonly`. The connector
exchanges it for an access token on every call.

- `GCAL_CLIENT_ID`
- `GCAL_CLIENT_SECRET`
- `GCAL_REFRESH_TOKEN`

Without them the connector serves stub data for a fictional account.

## Read-only

There is no create, update, or delete tool in this connector, and
the OAuth scope is readonly: it can never create an event.
