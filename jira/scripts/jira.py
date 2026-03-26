#!/usr/bin/env python3

import json
import os
import re
import secrets
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
DEFAULT_PORT = "8765"
DEFAULT_SCOPES = "offline_access read:jira-work"
STATE_TTL_SECONDS = 300
SITE_CACHE_TTL_SECONDS = 3600
SEARCH_LIMIT = 20
SEARCH_FETCH_LIMIT = 40
SITE_FILTER_QUERY = "/sites"
AUTH_QUERY = "/auth"
LOGOUT_QUERY = "/logout"
HELP_QUERY = "/help"
MY_OPEN_QUERY = "/mine"
JQL_PREFIX = "jql:"
ALLOWED_BROWSER_HOST_SUFFIXES = (
    ".atlassian.com",
    ".atlassian.net",
)


class JiraError(Exception):
    pass


def workflow_data_dir() -> Path:
    raw = os.environ.get("alfred_workflow_data")
    if raw:
        path = Path(raw)
    else:
        path = Path.home() / ".local" / "share" / "alfred-jira-oauth"
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_path() -> Path:
    return workflow_data_dir() / "tokens.json"


def icon_cache_dir() -> Path:
    path = workflow_data_dir() / "icons"
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_default_icon() -> str:
    candidates = [
        "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/ToolbarInfo.icns",
        "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/AlertNoteIcon.icns",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return ""


def config():
    port = os.environ.get("JIRA_REDIRECT_PORT", DEFAULT_PORT).strip() or DEFAULT_PORT
    redirect_uri = os.environ.get("JIRA_REDIRECT_URI", "").strip() or f"http://127.0.0.1:{port}/callback"
    scopes = os.environ.get("JIRA_SCOPES", "").strip() or DEFAULT_SCOPES
    return {
        "client_id": os.environ.get("JIRA_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("JIRA_CLIENT_SECRET", "").strip(),
        "redirect_uri": redirect_uri,
        "scopes": scopes,
        "redirect_port": port,
    }


def load_tokens():
    path = token_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise JiraError(f"Token store is invalid JSON: {exc}") from exc


def save_tokens(data):
    path = token_path()
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def clear_tokens():
    path = token_path()
    if path.exists():
        path.unlink()


def alfred_items(items):
    print(json.dumps({"items": items}))


def item(title, subtitle, arg=None, valid=False, uid=None, icon_path=None, mods=None, autocomplete=None, text=None):
    result = {
        "title": title,
        "subtitle": subtitle,
        "valid": valid,
    }
    if arg is not None:
        result["arg"] = arg
    if uid:
        result["uid"] = uid
    if icon_path:
        result["icon"] = {"path": icon_path}
    if mods:
        result["mods"] = mods
    if autocomplete:
        result["autocomplete"] = autocomplete
    if text:
        result["text"] = text
    return result


def command_arg(action, **payload):
    data = {"action": action}
    data.update(payload)
    return json.dumps(data)


def parse_loose_payload(arg):
    raw = arg.strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        return None
    inner = raw[1:-1].strip()
    if not inner:
        return {}

    result = {}
    parts = [part.strip() for part in inner.split(",") if part.strip()]
    for part in parts:
        if ":" not in part:
            return None
        key, value = part.split(":", 1)
        key = key.strip().strip("'\"")
        value = value.strip().strip("'\"")
        result[key] = value
    return result if "action" in result else None


def missing_config_items():
    return [
        item(
            "Set Jira OAuth settings in the workflow environment",
            "Required variables: JIRA_CLIENT_ID and JIRA_CLIENT_SECRET. Optional: JIRA_REDIRECT_PORT, JIRA_SCOPES.",
        ),
        item(
            "Expected callback URL",
            f"Register this redirect URI in your Atlassian OAuth app: {config()['redirect_uri']}",
        ),
        item(
            "Required scopes",
            "Add at least: offline_access read:jira-work",
        ),
    ]


def setup_help_items():
    return [
        item("Authenticate with Jira", "Press Enter to start the OAuth flow.", command_arg("auth"), True),
        item(
            "Search usage",
            f"Use `jira login page` to search, `jira KEY-123` for a ticket key, `{MY_OPEN_QUERY}` for your open issues, or `jira jql:assignee=currentUser()` for advanced search.",
        ),
        item(
            "Site selection",
            "Use `jira /sites` to choose which Jira Cloud site should be searched.",
        ),
    ]


def validate_config():
    cfg = config()
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise JiraError("Missing JIRA_CLIENT_ID or JIRA_CLIENT_SECRET.")
    return cfg


def http_json(url, method="GET", headers=None, payload=None):
    final_headers = {"Accept": "application/json"}
    if headers:
        final_headers.update(headers)
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=final_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise JiraError(f"HTTP {exc.code} from Jira: {details}") from exc
    except urllib.error.URLError as exc:
        raise JiraError(f"Network error talking to Jira: {exc.reason}") from exc


def ensure_access_token():
    cfg = validate_config()
    tokens = load_tokens()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_at = tokens.get("expires_at", 0)
    if access_token and time.time() < expires_at - 60:
        return access_token, tokens
    if not refresh_token:
        raise JiraError("Not authenticated. Run `jira`, then choose Authenticate with Jira.")
    refreshed = http_json(
        TOKEN_URL,
        method="POST",
        payload={
            "grant_type": "refresh_token",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": refresh_token,
        },
    )
    tokens["access_token"] = refreshed["access_token"]
    if "refresh_token" in refreshed:
        tokens["refresh_token"] = refreshed["refresh_token"]
    tokens["expires_at"] = time.time() + int(refreshed.get("expires_in", 3600))
    save_tokens(tokens)
    return tokens["access_token"], tokens


def accessible_sites(access_token):
    resources = http_json(
        ACCESSIBLE_RESOURCES_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    jira_sites = [
        site
        for site in resources
        if "read:jira-work" in site.get("scopes", [])
    ]
    if not jira_sites:
        raise JiraError("No Jira Cloud sites are available for this token.")
    jira_sites.sort(key=lambda site: site.get("name", "").lower())
    return jira_sites


def cached_sites(tokens):
    expires_at = tokens.get("sites_cache_expires_at", 0)
    sites = tokens.get("sites_cache")
    if not isinstance(sites, list) or time.time() >= expires_at:
        return None
    valid_sites = [
        site
        for site in sites
        if isinstance(site, dict) and site.get("id") and site.get("name") and site.get("url")
    ]
    return valid_sites or None


def store_sites_cache(tokens, sites):
    tokens["sites_cache"] = [
        {"id": site["id"], "name": site["name"], "url": site["url"]}
        for site in sites
    ]
    tokens["sites_cache_expires_at"] = time.time() + SITE_CACHE_TTL_SECONDS


def ensure_selected_site(tokens, access_token):
    sites = cached_sites(tokens)
    cache_updated = False
    if not sites:
        sites = accessible_sites(access_token)
        store_sites_cache(tokens, sites)
        cache_updated = True
    selected_id = tokens.get("selected_site_id")
    if selected_id:
        for site in sites:
            if site["id"] == selected_id:
                if cache_updated:
                    save_tokens(tokens)
                return site, sites
    chosen = sites[0]
    tokens["selected_site_id"] = chosen["id"]
    save_tokens(tokens)
    return chosen, sites


def site_base(site):
    return f"https://api.atlassian.com/ex/jira/{site['id']}"


def jql_escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def query_terms(value):
    return [term for term in re.split(r"\s+", value.strip()) if term]


def normalize_match_text(value):
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def build_default_search_jql(query):
    query = query.strip()
    escaped = jql_escape(query)
    key_match = re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", query)
    summary_terms = query_terms(query)
    clauses = []
    if key_match:
        clauses.append(f'key = "{escaped.upper()}"')
    if summary_terms:
        summary_clauses = [f'summary ~ "\\"{jql_escape(term)}*\\""' for term in summary_terms]
        if len(summary_clauses) == 1:
            clauses.append(summary_clauses[0])
        else:
            clauses.append("(" + " OR ".join(summary_clauses) + ")")
    return " OR ".join(clauses) + " ORDER BY updated DESC"


def build_global_search_jql(query):
    escaped = jql_escape(query.strip())
    return f'text ~ "\\"{escaped}\\"" ORDER BY updated DESC'


def build_my_open_issues_jql():
    return "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"


def build_empty_result_browser_jql(query):
    escaped = jql_escape(query.strip())
    return f'textfields ~ "{escaped}*"'


def search_by_jql(access_token, site, raw_jql, max_results=SEARCH_FETCH_LIMIT):
    payload = {
        "jql": raw_jql,
        "fields": ["summary", "status", "issuetype", "assignee", "created"],
        "maxResults": max_results,
        "fieldsByKeys": False,
    }
    url = f"{site_base(site)}/rest/api/3/search/jql"
    data = http_json(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {access_token}"},
        payload=payload,
    )
    return data.get("issues", [])


def issue_url(site, key):
    return f"{site['url'].rstrip('/')}/browse/{key}"


def issue_search_url(site, jql):
    encoded_jql = urllib.parse.quote(jql, safe="")
    return f"{site['url'].rstrip('/')}/issues/?jql={encoded_jql}"


def rank_default_search_results(issues, query):
    normalized_query = normalize_match_text(query)
    terms = query_terms(normalized_query)
    if not terms:
        return issues[:SEARCH_LIMIT]

    ranked = []
    for index, issue in enumerate(issues):
        fields = issue.get("fields", {})
        summary = fields.get("summary", "")
        normalized_summary = normalize_match_text(summary)
        normalized_key = normalize_match_text(issue.get("key", ""))
        exact_key_match = normalized_key == normalized_query
        phrase_match = normalized_query in normalized_summary if normalized_query else False
        all_terms_match = all(term in normalized_summary for term in terms)
        matched_terms = sum(1 for term in terms if term in normalized_summary)
        single_term_match = len(terms) == 1 and matched_terms == 1

        if not (exact_key_match or phrase_match or all_terms_match or single_term_match):
            continue

        ranked.append(
            (
                1 if exact_key_match else 0,
                1 if phrase_match else 0,
                1 if all_terms_match else 0,
                matched_terms,
                -index,
                issue,
            )
        )

    ranked.sort(reverse=True)
    return [issue for *_, issue in ranked[:SEARCH_LIMIT]]


def issue_type_icon_path(fields):
    issue_type = fields.get("issuetype") or {}
    issue_type_id = str(issue_type.get("id") or issue_type.get("name") or "issue").strip()
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", issue_type_id)
    destination = icon_cache_dir() / f"issuetype-v5-{safe_id}.png"
    if destination.exists() and destination.stat().st_size > 0:
        return str(destination)
    return bundled_default_icon()


def is_allowed_browser_url(value):
    parsed = urllib.parse.urlparse((value or "").strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
    ):
        return False
    return any(host == suffix[1:] or host.endswith(suffix) for suffix in ALLOWED_BROWSER_HOST_SUFFIXES)


def compact_status(fields):
    status = (fields.get("status", {}).get("name") or "").strip()
    mapping = {
        "to do": "To Do",
        "in progress": "In Prog.",
        "done": "Done",
        "selected for development": "Selected",
        "in review": "Review",
        "ready for qa": "QA",
        "blocked": "Blocked",
        "backlog": "Backlog",
    }
    lowered = status.lower()
    if lowered in mapping:
        return mapping[lowered]
    if len(status) <= 12:
        return status
    words = status.split()
    if len(words) > 1:
        compact = " ".join(word if i == len(words) - 1 else f"{word[:4]}." for i, word in enumerate(words))
        return compact[:16]
    return status[:16]


def assignee_name(fields):
    assignee = fields.get("assignee") or {}
    if not assignee:
        return "Unassigned"

    display_name = (assignee.get("displayName") or "").strip()
    if not display_name:
        return "Assigned"
    return display_name


def created_date(fields):
    raw = (fields.get("created") or "").strip()
    if not raw:
        return ""
    date_part = raw[:10]
    pieces = date_part.split("-")
    if len(pieces) != 3:
        return date_part
    year, month, day = pieces
    return f"{day}-{month}-{year}"


def search_items(query):
    cfg = config()
    if not cfg["client_id"] or not cfg["client_secret"]:
        return missing_config_items()

    try:
        access_token, tokens = ensure_access_token()
        selected_site, sites = ensure_selected_site(tokens, access_token)
    except JiraError as exc:
        return setup_help_items() + [item("Authentication required", str(exc), command_arg("auth"), True)]

    query = query.strip()
    if not query:
        return [
            item(
                f"Search Jira issues on {selected_site['name']}",
                f"Type a search query, or use {MY_OPEN_QUERY}, {AUTH_QUERY}, {SITE_FILTER_QUERY}, {LOGOUT_QUERY}, or {HELP_QUERY}.",
            ),
            item(
                "My open issues",
                f"Autocomplete {MY_OPEN_QUERY} to list your non-done issues on {selected_site['name']}, newest activity first.",
                autocomplete=MY_OPEN_QUERY,
            ),
            item("Authenticate again", "Refresh the Jira OAuth grant.", command_arg("auth"), True),
            item("Choose site", f"Current site: {selected_site['name']}", command_arg("list-sites"), True),
            item("Open current site", selected_site["url"], command_arg("open", url=selected_site["url"]), True),
            item("Sign out", "Delete the locally stored Jira tokens.", command_arg("logout"), True),
            item("Available Jira sites", ", ".join(site["name"] for site in sites), command_arg("list-sites"), True),
        ]

    if query == AUTH_QUERY:
        return [item("Authenticate with Jira", "Press Enter to start the OAuth flow.", command_arg("auth"), True)]
    if query == LOGOUT_QUERY:
        return [item("Sign out", "Delete the locally stored Jira tokens.", command_arg("logout"), True)]
    if query == HELP_QUERY:
        return setup_help_items()
    if query == MY_OPEN_QUERY:
        raw_jql = build_my_open_issues_jql()
        try:
            issues = search_by_jql(access_token, selected_site, raw_jql)
            return format_jql_items(issues, selected_site, raw_jql)
        except JiraError as exc:
            return [item("Jira search failed", str(exc))]
    if query.startswith(SITE_FILTER_QUERY):
        typed = query[len(SITE_FILTER_QUERY):].strip().lower()
        items = []
        for site in sites:
            haystack = f"{site.get('name', '')} {site.get('url', '')}".lower()
            if typed and typed not in haystack:
                continue
            subtitle = site["url"]
            if site["id"] == selected_site["id"]:
                subtitle = f"{subtitle} · current"
            items.append(
                item(
                    site["name"],
                    subtitle,
                    command_arg("choose-site", site_id=site["id"]),
                    True,
                    uid=site["id"],
                )
            )
        return items or [item("No matching Jira sites", "Try a different filter.")]

    try:
        if query.lower().startswith(JQL_PREFIX):
            raw_jql = query[len(JQL_PREFIX):].strip()
            issues = search_by_jql(access_token, selected_site, raw_jql)
            return format_jql_items(issues, selected_site, raw_jql)
        raw_jql = build_default_search_jql(query)
        issues = search_by_jql(access_token, selected_site, raw_jql)
        issues = rank_default_search_results(issues, query)
        return format_jql_items(issues, selected_site, build_empty_result_browser_jql(query))
    except JiraError as exc:
        return [item("Jira search failed", str(exc))]


def format_jql_items(issues, site, empty_result_jql):
    items = []
    for issue in issues[:SEARCH_LIMIT]:
        fields = issue.get("fields", {})
        status = compact_status(fields)
        owner = assignee_name(fields)
        created = created_date(fields)
        icon_path = issue_type_icon_path(fields)
        detail_bits = [status, owner]
        if created:
            detail_bits.append(created)
        items.append(
            item(
                f"{issue['key']}: {fields.get('summary', '')}",
                " · ".join(detail_bits),
                command_arg("open", url=issue_url(site, issue["key"])),
                True,
                uid=issue["key"],
                icon_path=icon_path,
                text={"copy": issue["key"]},
                mods={
                    "cmd": {
                        "valid": True,
                        "arg": issue["key"],
                        "subtitle": f"Copy issue key {issue['key']}",
                    },
                    "alt": {
                        "valid": True,
                        "arg": issue_url(site, issue["key"]),
                        "subtitle": "Copy issue URL",
                    },
                },
            )
        )
    if items:
        return items
    return [
        item(
            "No Jira issues found",
            f"Press Enter to open this search in {site['name']}.",
            command_arg("open", url=issue_search_url(site, empty_result_jql)),
            True,
        )
    ]


def copy_to_clipboard(value):
    import subprocess

    subprocess.run(["pbcopy"], input=value.encode("utf-8"), check=True)


def run_action(arg):
    direct = arg.strip()
    if direct == AUTH_QUERY:
        perform_auth()
        print("Jira authentication complete.")
        return
    if direct == LOGOUT_QUERY:
        clear_tokens()
        print("Jira tokens deleted.")
        return
    if direct == SITE_FILTER_QUERY:
        print("Use `jira /sites` to choose a Jira site.")
        return

    try:
        payload = json.loads(arg)
    except json.JSONDecodeError:
        payload = parse_loose_payload(arg)
        if payload is not None:
            action = payload.get("action")
            if action == "auth":
                perform_auth()
                print("Jira authentication complete.")
                return
            if action == "logout":
                clear_tokens()
                print("Jira tokens deleted.")
                return
            if action == "list-sites":
                print("Use `jira /sites` to choose a Jira site.")
                return
            if action == "choose-site":
                tokens = load_tokens()
                tokens["selected_site_id"] = payload["site_id"]
                save_tokens(tokens)
                print("Selected Jira site updated.")
                return
            if action == "open":
                url = payload["url"]
                if not is_allowed_browser_url(url):
                    raise JiraError("Refusing to open a non-Jira HTTPS URL.")
                webbrowser.open(url)
                return
        try:
            copy_to_clipboard(arg)
        except Exception as exc:
            raise JiraError(f"Could not copy value: {exc}") from exc
        print(f"Copied: {arg}")
        return

    action = payload.get("action")
    if action == "auth":
        perform_auth()
        print("Jira authentication complete.")
        return
    if action == "logout":
        clear_tokens()
        print("Jira tokens deleted.")
        return
    if action == "list-sites":
        print("Use `jira /sites` to choose a Jira site.")
        return
    if action == "choose-site":
        tokens = load_tokens()
        tokens["selected_site_id"] = payload["site_id"]
        save_tokens(tokens)
        print("Selected Jira site updated.")
        return
    if action == "open":
        url = payload["url"]
        if not is_allowed_browser_url(url):
            raise JiraError("Refusing to open a non-Jira HTTPS URL.")
        webbrowser.open(url)
        return
    raise JiraError(f"Unsupported action: {action}")


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        OAuthCallbackHandler.result = {
            "code": params.get("code", [None])[0],
            "state": params.get("state", [None])[0],
            "error": params.get("error", [None])[0],
            "error_description": params.get("error_description", [None])[0],
        }
        body = "<html><body><h2>Jira authentication received</h2><p>You can return to Alfred.</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *_args):
        return


def perform_auth():
    cfg = validate_config()
    parsed_redirect = urllib.parse.urlparse(cfg["redirect_uri"])
    if parsed_redirect.hostname not in {"127.0.0.1", "localhost"}:
        raise JiraError("This workflow expects a localhost redirect URI.")

    expected_port = int(parsed_redirect.port or cfg["redirect_port"])
    state = secrets.token_urlsafe(24)
    OAuthCallbackHandler.result = {}
    server = HTTPServer(("127.0.0.1", expected_port), OAuthCallbackHandler)
    server.timeout = 1

    query = urllib.parse.urlencode(
        {
            "audience": "api.atlassian.com",
            "client_id": cfg["client_id"],
            "scope": cfg["scopes"],
            "redirect_uri": cfg["redirect_uri"],
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
    )
    auth_url = f"{AUTH_URL}?{query}"
    webbrowser.open(auth_url)

    started = time.time()
    while time.time() - started < STATE_TTL_SECONDS:
        server.handle_request()
        result = OAuthCallbackHandler.result
        if result:
            break
    else:
        server.server_close()
        raise JiraError("Timed out waiting for the Jira OAuth callback.")

    server.server_close()
    result = OAuthCallbackHandler.result
    if result.get("error"):
        raise JiraError(f"OAuth error: {result['error']} ({result.get('error_description') or 'no description'})")
    if result.get("state") != state:
        raise JiraError("OAuth state mismatch.")
    code = result.get("code")
    if not code:
        raise JiraError("No authorization code returned by Jira.")

    token_data = http_json(
        TOKEN_URL,
        method="POST",
        payload={
            "grant_type": "authorization_code",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "code": code,
            "redirect_uri": cfg["redirect_uri"],
        },
    )
    tokens = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": time.time() + int(token_data.get("expires_in", 3600)),
    }
    access_token = tokens["access_token"]
    sites = accessible_sites(access_token)
    store_sites_cache(tokens, sites)
    tokens["selected_site_id"] = sites[0]["id"]
    save_tokens(tokens)


def main(argv):
    if len(argv) < 2:
        raise JiraError("Usage: jira.py <search|action> [query]")
    command = argv[1]
    if command == "search":
        query = argv[2] if len(argv) > 2 else ""
        alfred_items(search_items(query))
        return
    if command == "action":
        arg = argv[2] if len(argv) > 2 else ""
        run_action(arg)
        return
    raise JiraError(f"Unknown command: {command}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except JiraError as exc:
        if len(sys.argv) > 1 and sys.argv[1] == "search":
            alfred_items([item("Jira workflow error", str(exc))])
        else:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
