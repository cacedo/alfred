# Confluence Search for Alfred 5

Alfred 5 workflow to search and open Confluence Cloud pages with OAuth 2.0.

## Features

- Search Confluence pages from Alfred with the `confluence` keyword
- Open pages in the browser
- OAuth 2.0 authorization code flow with refresh tokens
- Multi-site support via Atlassian `accessible-resources`
- Advanced `cql:` mode for raw CQL queries

## Setup

Create an OAuth 2.0 (3LO) app in the Atlassian developer console and configure:

- Callback URL: `http://127.0.0.1:8765/callback`
- Scopes: `offline_access`, `search:confluence`, `read:confluence-content.summary`, `read:confluence-space.summary`

After importing the workflow into Alfred, set these workflow environment variables:

- `CONFLUENCE_CLIENT_ID`
- `CONFLUENCE_CLIENT_SECRET`
- `CONFLUENCE_REDIRECT_PORT` (optional, default `8765`)
- `CONFLUENCE_REDIRECT_URI` (optional, overrides the derived localhost callback URL)
- `CONFLUENCE_SCOPES` (optional, default `offline_access search:confluence read:confluence-content.summary read:confluence-space.summary`)

## Usage

- `confluence <query>`: search pages
- `confluence cql:type = page AND space = ENG`: raw CQL search
- `confluence /auth`: start OAuth authentication
- `confluence /sites`: choose the Confluence site to search
- `confluence /logout`: remove local tokens
- `confluence /help`: show usage help

Press `Enter` on a result to open it in the browser.

Modifiers on search results:

- `cmd`: copy page title
- `alt`: copy page URL

## Storage

OAuth tokens are stored in Alfred's workflow data directory as `tokens.json` with user-only file permissions.

## Atlassian API references

- Authorization and token exchange:
  https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/
- Calling product APIs and `accessible-resources`:
  https://developer.atlassian.com/cloud/oauth/getting-started/making-calls-to-api/
- Confluence search APIs:
  https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-search/
