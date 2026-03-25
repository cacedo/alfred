# Jira Search for Alfred 5

Alfred 5 workflow to search and open Jira Cloud tickets with OAuth 2.0.

## Features

- Search Jira issues from Alfred with the `jira` keyword
- Open issues in the browser
- OAuth 2.0 authorization code flow with refresh tokens
- Multi-site support via Atlassian `accessible-resources`
- Advanced `jql:` mode for raw JQL queries

## Setup

Create an OAuth 2.0 (3LO) app in the Atlassian developer console and configure:

- Callback URL: `http://127.0.0.1:8765/callback`
- Scopes: `offline_access`, `read:jira-work`

After importing the workflow into Alfred, set these workflow environment variables:

- `JIRA_CLIENT_ID`
- `JIRA_CLIENT_SECRET`
- `JIRA_REDIRECT_PORT` (optional, default `8765`)
- `JIRA_REDIRECT_URI` (optional, overrides the derived localhost callback URL)
- `JIRA_SCOPES` (optional, default `offline_access read:jira-work`)

## Usage

- `jira <query>`: search issues
- `jira KEY-123`: quick issue lookup
- `jira jql:assignee = currentUser() AND statusCategory != Done`: raw JQL search
- `jira /auth`: start OAuth authentication
- `jira /sites`: choose the Jira site to search
- `jira /logout`: remove local tokens
- `jira /help`: show usage help

Press `Enter` on a result to open it in the browser.

Modifiers on search results:

- `cmd`: copy issue key
- `alt`: copy issue URL

## Storage

OAuth tokens are stored in Alfred's workflow data directory as `tokens.json` with user-only file permissions.

## Atlassian API references

- Authorization and token exchange:
  https://developer.atlassian.com/cloud/jira/service-desk/oauth-2-authorization-code-grants-3lo-for-apps/
- Calling product APIs and `accessible-resources`:
  https://developer.atlassian.com/cloud/oauth/getting-started/making-calls-to-api/
- Jira issue search APIs:
  https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/
