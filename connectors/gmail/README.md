# Gmail connector (v0.8.4)

The Gmail connector is the email source: the other half of the
systems-of-truth gap. Gong covers the calls; email covers every
renewal thread, escalation, and champion conversation. It is the
second source that most needs the summary-first rule, because a
message body is the most token-expensive raw material after a
transcript, and the one most likely to contain something the agent
should not repeat.

## Tools

Follow the pattern in connectors/base.py: pure functions in `tools.py`,
FastMCP wrapper in `server.py`, same shape as Salesforce and Slack.

- `gmail_search_messages`: search messages for an account or topic.
  Returns short excerpts (author, subject, date, first ~200 chars),
  max 5 matches, never a full message body. The live path lists
  message ids, then fetches each with `format=metadata`: headers plus
  Google's snippet, never a body.
- `gmail_get_thread`: thread summary (participants, message count, key
  excerpts, capped at 3). The live path fetches the thread with
  `format=metadata` and reduces each message to its From header and
  snippet.

## Credentials

Gmail does not accept a simple API key for reading mail. It needs
OAuth 2.0 with a refresh token, via three env vars:

- `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET`: from an OAuth client
  (Desktop app type) in Google Cloud Console, with the Gmail API
  enabled.
- `GMAIL_REFRESH_TOKEN`: minted once, with the readonly scope
  `https://www.googleapis.com/auth/gmail.readonly`. The usual way is
  the Google OAuth 2.0 Playground (select the Gmail readonly scope,
  authorize, exchange the code) or a one-off run of
  `google-auth-oauthlib`'s installed-app flow. Paste the refresh token
  into `.env`; the connector exchanges it for an access token
  (POST `oauth2.googleapis.com/token`) on every call and never stores
  one. The readonly scope is the whole point: the connector can only
  read mail, never send or delete it.

Stub mode returns sample email data when any of the three vars is
missing, same as the other connectors.

## Token budget note

A message body can be long, and a thread longer. The harness must
never put a body in a prompt. `gmail_search_messages` returns
Google's snippet (truncated to ~200 chars) plus headers only;
`gmail_get_thread` returns three short excerpts. If a future tool
needs message detail, it should return a per-message extract capped
at the tool's max_chars, never the full text.

## Deep links

Every Gmail result carries thread ids. The harness builds deep links
from them (`https://mail.google.com/mail/u/0/#inbox/<threadId>`; stub
links use the `.invalid` host), the same way it builds Salesforce
Lightning links. The model never sees a URL: Gmail results contain
ids only, no URL fields. Renewal-desk gathers from
`gmail_search_messages`, so its drafts link to the threads they quote.

## Live path status

Written, not verified: the live calls are real code, written against
the documented Gmail API (GET `/gmail/v1/users/me/messages`,
`/messages/{id}?format=metadata`, `/threads/{id}?format=metadata`),
but no credentials were available while building v0.8.4, so they have
not run against a real mailbox. Stub mode is what runs in the demo
and the tests.
