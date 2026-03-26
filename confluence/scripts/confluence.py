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
DEFAULT_SCOPES = "offline_access search:confluence read:confluence-content.summary read:confluence-space.summary"
STATE_TTL_SECONDS = 300
SEARCH_LIMIT = 20
SITE_FILTER_QUERY = "/sites"
AUTH_QUERY = "/auth"
LOGOUT_QUERY = "/logout"
HELP_QUERY = "/help"
CQL_PREFIX = "cql:"


class ConfluenceError(Exception):
    pass


def workflow_data_dir() -> Path:
    raw = os.environ.get("alfred_workflow_data")
    if raw:
        path = Path(raw)
    else:
        path = Path.home() / ".local" / "share" / "alfred-confluence-oauth"
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_path() -> Path:
    return workflow_data_dir() / "tokens.json"


def ensure_required_scopes(raw_scopes):
    required_scopes = [
        "offline_access",
        "search:confluence",
        "read:confluence-content.summary",
        "read:confluence-space.summary",
    ]
    seen = set()
    scopes = []
    for scope in (raw_scopes or "").split():
        if scope and scope not in seen:
            scopes.append(scope)
            seen.add(scope)
    for scope in required_scopes:
        if scope not in seen:
            scopes.append(scope)
            seen.add(scope)
    return " ".join(scopes)


def config():
    port = os.environ.get("CONFLUENCE_REDIRECT_PORT", DEFAULT_PORT).strip() or DEFAULT_PORT
    redirect_uri = os.environ.get("CONFLUENCE_REDIRECT_URI", "").strip() or f"http://127.0.0.1:{port}/callback"
    scopes = ensure_required_scopes(os.environ.get("CONFLUENCE_SCOPES", "").strip() or DEFAULT_SCOPES)
    return {
        "client_id": os.environ.get("CONFLUENCE_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("CONFLUENCE_CLIENT_SECRET", "").strip(),
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
        raise ConfluenceError(f"Token store is invalid JSON: {exc}") from exc


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


def item(title, subtitle, arg=None, valid=False, uid=None, icon_path=None, mods=None):
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
            "Set Confluence OAuth settings in the workflow environment",
            "Required variables: CONFLUENCE_CLIENT_ID and CONFLUENCE_CLIENT_SECRET. Optional: CONFLUENCE_REDIRECT_PORT, CONFLUENCE_SCOPES.",
        ),
        item(
            "Expected callback URL",
            f"Register this redirect URI in your Atlassian OAuth app: {config()['redirect_uri']}",
        ),
        item(
            "Required scopes",
            "Add at least: offline_access search:confluence read:confluence-content.summary read:confluence-space.summary",
        ),
    ]


def setup_help_items():
    scopes = config()["scopes"]
    return [
        item("Authenticate with Confluence", f"Press Enter to start the OAuth flow. Scopes: {scopes}", command_arg("auth"), True),
        item("Sign out", "Delete the locally stored Confluence tokens.", command_arg("logout"), True),
        item(
            "Search usage",
            "Use `confluence roadmap` to search pages, or `confluence cql:type = page AND space = ENG` for raw CQL.",
        ),
        item(
            "Site selection",
            "Use `confluence /sites` to choose which Confluence Cloud site should be searched.",
        ),
    ]


def validate_config():
    cfg = config()
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise ConfluenceError("Missing CONFLUENCE_CLIENT_ID or CONFLUENCE_CLIENT_SECRET.")
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
        raise ConfluenceError(f"HTTP {exc.code} from Confluence: {details}") from exc
    except urllib.error.URLError as exc:
        raise ConfluenceError(f"Network error talking to Confluence: {exc.reason}") from exc


def ensure_access_token():
    cfg = validate_config()
    tokens = load_tokens()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_at = tokens.get("expires_at", 0)
    if access_token and time.time() < expires_at - 60:
        return access_token, tokens
    if not refresh_token:
        raise ConfluenceError("Not authenticated. Run `confluence`, then choose Authenticate with Confluence.")
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
    if not resources:
        raise ConfluenceError("No Confluence Cloud sites are available for this token.")
    resources.sort(key=lambda site: site.get("name", "").lower())
    return resources


def ensure_selected_site(tokens, access_token):
    sites = accessible_sites(access_token)
    selected_id = tokens.get("selected_site_id")
    if selected_id:
        for site in sites:
            if site["id"] == selected_id:
                return site, sites
    chosen = sites[0]
    tokens["selected_site_id"] = chosen["id"]
    save_tokens(tokens)
    return chosen, sites


def site_base(site):
    return f"https://api.atlassian.com/ex/confluence/{site['id']}"


def confluence_logo_path():
    return str(Path(__file__).resolve().parent.parent / "assets" / "confluence-logo.svg")


def cql_escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_default_search_cql(query):
    escaped = cql_escape(query.strip())
    return f'type = page AND title ~ "{escaped}" ORDER BY lastmodified DESC'


def search_by_cql(access_token, site, raw_cql):
    params = urllib.parse.urlencode(
        {
            "cql": raw_cql,
            "limit": str(SEARCH_LIMIT),
            "expand": "space",
        }
    )
    url = f"{site_base(site)}/wiki/rest/api/content/search?{params}"
    data = http_json(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return data.get("results", [])


def search_result_title(result):
    return (result.get("title") or "").strip()


def search_result_space_name(result):
    candidate = result.get("space")
    if isinstance(candidate, dict):
        name = (candidate.get("name") or "").strip()
        if name:
            return name
    return "Unknown space"


def search_result_url(site, result):
    links = result.get("_links") or {}
    webui = (links.get("webui") or "").strip()
    if webui:
        return urllib.parse.urljoin(site["url"].rstrip("/") + "/", webui.lstrip("/"))

    tinyui = (links.get("tinyui") or "").strip()
    if tinyui:
        return urllib.parse.urljoin(site["url"].rstrip("/") + "/", tinyui.lstrip("/"))

    content_id = result.get("id")
    if content_id:
        return f"{site['url'].rstrip('/')}/wiki/pages/viewpage.action?pageId={content_id}"
    return site["url"]


def confluence_search_url(site, query):
    params = urllib.parse.urlencode({"text": query.strip()})
    return f"{site['url'].rstrip('/')}/wiki/search?{params}"


def search_items(query):
    cfg = config()
    if not cfg["client_id"] or not cfg["client_secret"]:
        return missing_config_items()

    try:
        access_token, tokens = ensure_access_token()
        selected_site, sites = ensure_selected_site(tokens, access_token)
    except ConfluenceError as exc:
        return setup_help_items() + [item("Authentication required", str(exc), command_arg("auth"), True)]

    query = query.strip()
    if not query:
        return [
            item(
                f"Search Confluence pages on {selected_site['name']}",
                f"Type a search query, or use {AUTH_QUERY}, {SITE_FILTER_QUERY}, {LOGOUT_QUERY}, or {HELP_QUERY}.",
            ),
            item("Authenticate again", "Refresh the Confluence OAuth grant.", command_arg("auth"), True),
            item("Choose site", f"Current site: {selected_site['name']}", command_arg("list-sites"), True),
            item("Open current site", selected_site["url"], command_arg("open", url=selected_site["url"]), True),
            item("Sign out", "Delete the locally stored Confluence tokens.", command_arg("logout"), True),
            item("Available Confluence sites", ", ".join(site["name"] for site in sites), command_arg("list-sites"), True),
        ]

    if query == AUTH_QUERY:
        return [item("Authenticate with Confluence", "Press Enter to start the OAuth flow.", command_arg("auth"), True)]
    if query == LOGOUT_QUERY:
        return [item("Sign out", "Delete the locally stored Confluence tokens.", command_arg("logout"), True)]
    if query == HELP_QUERY:
        return setup_help_items()
    if query.startswith(SITE_FILTER_QUERY):
        typed = query[len(SITE_FILTER_QUERY):].strip().lower()
        items = []
        for site in sites:
            haystack = f"{site.get('name', '')} {site.get('url', '')}".lower()
            if typed and typed not in haystack:
                continue
            subtitle = site["url"]
            if site["id"] == selected_site["id"]:
                subtitle = f"{subtitle} - current"
            items.append(
                item(
                    site["name"],
                    subtitle,
                    command_arg("choose-site", site_id=site["id"]),
                    True,
                    uid=site["id"],
                )
            )
        return items or [item("No matching Confluence sites", "Try a different filter.")]

    try:
        if query.lower().startswith(CQL_PREFIX):
            results = search_by_cql(access_token, selected_site, query[len(CQL_PREFIX):].strip())
        else:
            results = search_by_cql(access_token, selected_site, build_default_search_cql(query))
        return format_search_items(results, selected_site, query)
    except ConfluenceError as exc:
        return [item("Confluence search failed", str(exc))]


def format_search_items(results, site, query):
    items = []
    icon_path = confluence_logo_path()
    for result in results[:SEARCH_LIMIT]:
        title = search_result_title(result)
        if not title:
            continue
        url = search_result_url(site, result)
        space_name = search_result_space_name(result)
        uid = str(result.get("id") or url)
        items.append(
            item(
                title,
                space_name,
                command_arg("open", url=url),
                True,
                uid=uid,
                icon_path=icon_path,
                mods={
                    "cmd": {
                        "valid": True,
                        "arg": title,
                        "subtitle": f"Copy page title {title}",
                    },
                    "alt": {
                        "valid": True,
                        "arg": url,
                        "subtitle": "Copy page URL",
                    },
                },
            )
        )
    if items:
        return items
    search_url = confluence_search_url(site, query)
    return [
        item(
            "No Confluence pages found",
            f"Press Enter to search for “{query.strip()}” in the browser on {site['name']}.",
            command_arg("open", url=search_url),
            True,
            icon_path=icon_path,
        )
    ]


def copy_to_clipboard(value):
    import subprocess

    subprocess.run(["pbcopy"], input=value.encode("utf-8"), check=True)


def run_action(arg):
    direct = arg.strip()
    if direct == AUTH_QUERY:
        perform_auth()
        print("Confluence authentication complete.")
        return
    if direct == LOGOUT_QUERY:
        clear_tokens()
        print("Confluence tokens deleted.")
        return
    if direct == SITE_FILTER_QUERY:
        print("Use `confluence /sites` to choose a Confluence site.")
        return

    try:
        payload = json.loads(arg)
    except json.JSONDecodeError:
        payload = parse_loose_payload(arg)
        if payload is not None:
            action = payload.get("action")
            if action == "auth":
                perform_auth()
                print("Confluence authentication complete.")
                return
            if action == "logout":
                clear_tokens()
                print("Confluence tokens deleted.")
                return
            if action == "list-sites":
                print("Use `confluence /sites` to choose a Confluence site.")
                return
            if action == "choose-site":
                tokens = load_tokens()
                tokens["selected_site_id"] = payload["site_id"]
                save_tokens(tokens)
                print("Selected Confluence site updated.")
                return
            if action == "open":
                webbrowser.open(payload["url"])
                return
        try:
            copy_to_clipboard(arg)
        except Exception as exc:
            raise ConfluenceError(f"Could not copy value: {exc}") from exc
        print(f"Copied: {arg}")
        return

    action = payload.get("action")
    if action == "auth":
        perform_auth()
        print("Confluence authentication complete.")
        return
    if action == "logout":
        clear_tokens()
        print("Confluence tokens deleted.")
        return
    if action == "list-sites":
        print("Use `confluence /sites` to choose a Confluence site.")
        return
    if action == "choose-site":
        tokens = load_tokens()
        tokens["selected_site_id"] = payload["site_id"]
        save_tokens(tokens)
        print("Selected Confluence site updated.")
        return
    if action == "open":
        webbrowser.open(payload["url"])
        return
    raise ConfluenceError(f"Unsupported action: {action}")


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
        body = "<html><body><h2>Confluence authentication received</h2><p>You can return to Alfred.</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *_args):
        return


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


def perform_auth():
    cfg = validate_config()
    parsed_redirect = urllib.parse.urlparse(cfg["redirect_uri"])
    if parsed_redirect.hostname not in {"127.0.0.1", "localhost"}:
        raise ConfluenceError("This workflow expects a localhost redirect URI.")

    expected_port = int(parsed_redirect.port or cfg["redirect_port"])
    state = secrets.token_urlsafe(24)
    OAuthCallbackHandler.result = {}
    try:
        server = ReusableHTTPServer(("127.0.0.1", expected_port), OAuthCallbackHandler)
    except OSError as exc:
        if exc.errno == 48:
            raise ConfluenceError(
                f"OAuth callback port {expected_port} is already in use. "
                "Close any previous auth attempt or set CONFLUENCE_REDIRECT_PORT to a different port."
            ) from exc
        raise
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
        },
        quote_via=urllib.parse.quote,
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
        raise ConfluenceError("Timed out waiting for the Confluence OAuth callback.")

    server.server_close()
    result = OAuthCallbackHandler.result
    if result.get("error"):
        raise ConfluenceError(f"OAuth error: {result['error']} ({result.get('error_description') or 'no description'})")
    if result.get("state") != state:
        raise ConfluenceError("OAuth state mismatch.")
    code = result.get("code")
    if not code:
        raise ConfluenceError("No authorization code returned by Confluence.")

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
    tokens["selected_site_id"] = sites[0]["id"]
    save_tokens(tokens)


def main(argv):
    if len(argv) < 2:
        raise ConfluenceError("Usage: confluence.py <search|action> [query]")
    command = argv[1]
    if command == "search":
        query = argv[2] if len(argv) > 2 else ""
        alfred_items(search_items(query))
        return
    if command == "action":
        arg = argv[2] if len(argv) > 2 else ""
        run_action(arg)
        return
    raise ConfluenceError(f"Unknown command: {command}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except ConfluenceError as exc:
        if len(sys.argv) > 1 and sys.argv[1] == "search":
            alfred_items([item("Confluence workflow error", str(exc))])
        else:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
