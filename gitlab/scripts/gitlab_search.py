#!/usr/bin/env python3

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request


APP_NAME = "GitLab Project Search"
MAX_RESULTS = 20
MIN_QUERY_LENGTH = 3
RERUN_INTERVAL = 0.3
PREFETCH_LOCK_TTL = 15
AVATAR_WAIT_TIMEOUT = 5
REQUEST_TIMEOUT = 10


def respond(items: list[dict], rerun: float | None = None) -> None:
    payload = {"items": items}
    if rerun is not None:
        payload["rerun"] = rerun
    print(json.dumps(payload))


def item(
    title: str,
    subtitle: str,
    *,
    arg: str | None = None,
    valid: bool = False,
    icon_path: str = "icon.png",
) -> dict:
    payload = {
        "title": title,
        "subtitle": subtitle,
        "valid": valid,
        "icon": {"path": icon_path},
    }
    if arg is not None:
        payload["arg"] = arg
    return payload


def normalize_host(raw_host: str) -> str:
    raw_host = raw_host.strip()
    if not raw_host:
        return ""
    if "://" not in raw_host:
        raw_host = f"https://{raw_host}"
    parsed = urllib.parse.urlparse(raw_host)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}".rstrip("/")


def is_local_host(host: str) -> bool:
    parsed = urllib.parse.urlparse(host)
    hostname = (parsed.hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}


def host_is_secure(host: str) -> bool:
    parsed = urllib.parse.urlparse(host)
    return parsed.scheme == "https" or is_local_host(host)


def masked_token(token: str) -> str:
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}{'*' * (len(token) - 8)}{token[-4:]}"


def workflow_cache_dir() -> pathlib.Path:
    cache_dir = os.environ.get("alfred_workflow_cache") or os.environ.get("ALFRED_WORKFLOW_CACHE")
    if cache_dir:
        return pathlib.Path(cache_dir)
    return pathlib.Path("/tmp/alfred-gitlab-search-cache")


def ensure_private_dir(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def avatar_cache_dir() -> pathlib.Path:
    return ensure_private_dir(workflow_cache_dir() / "avatars")


def prefetch_state_dir() -> pathlib.Path:
    return ensure_private_dir(workflow_cache_dir() / "prefetch")


def load_config() -> dict:
    return {
        "host": normalize_host(os.environ.get("GITLAB_HOST", "")),
        "user": os.environ.get("GITLAB_USER", "").strip(),
        "token": os.environ.get("GITLAB_TOKEN", "").strip(),
    }


def resolved_avatar_url(host: str, avatar_url: str) -> str:
    return urllib.parse.urljoin(f"{host}/", avatar_url)


def project_avatar_endpoint(host: str, project_id: int) -> str:
    return f"{host}/api/v4/projects/{project_id}/avatar"


def url_is_same_host(host: str, url: str) -> bool:
    base = urllib.parse.urlparse(host)
    target = urllib.parse.urlparse(url)
    return base.scheme == target.scheme and base.netloc == target.netloc


def cache_extension(response, url: str) -> str:
    content_type = response.headers.get_content_type()
    extensions = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
        "image/tiff": ".tiff",
        "image/webp": ".webp",
    }
    if content_type in extensions:
        return extensions[content_type]
    parsed = urllib.parse.urlparse(url)
    return pathlib.Path(parsed.path).suffix or ".png"


def download_avatar(config: dict, project_id: int, avatar_url: str | None) -> tuple[bytes, str]:
    candidate_urls = [project_avatar_endpoint(config["host"], project_id)]
    if avatar_url:
        resolved = resolved_avatar_url(config["host"], avatar_url)
        if url_is_same_host(config["host"], resolved):
            candidate_urls.append(resolved)

    headers = {
        "PRIVATE-TOKEN": config["token"],
        "User-Agent": "alfred-gitlab-search",
    }

    last_error = None
    for url in candidate_urls:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=AVATAR_WAIT_TIMEOUT) as response:
                return response.read(), cache_extension(response, url)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                continue
            raise
        except urllib.error.URLError as exc:
            last_error = exc
            continue

    if last_error:
        raise last_error
    raise RuntimeError("Avatar download failed")


def cached_avatar_path(config: dict, project_id: int, avatar_url: str | None) -> str:
    cache_dir = avatar_cache_dir()
    existing = sorted(cache_dir.glob(f"project-{project_id}.*"))
    if existing and existing[0].stat().st_size > 0:
        return str(existing[0])

    content, extension = download_avatar(config, project_id, avatar_url)
    icon_path = cache_dir / f"project-{project_id}{extension}"
    for stale_file in existing:
        stale_file.unlink(missing_ok=True)
    icon_path.write_bytes(content)
    return str(icon_path)


def cached_avatar_if_available(project_id: int) -> str | None:
    existing = sorted(avatar_cache_dir().glob(f"project-{project_id}.*"))
    if existing and existing[0].stat().st_size > 0:
        return str(existing[0])
    return None


def prefetch_lock_path(query: str) -> pathlib.Path:
    safe_name = urllib.parse.quote(query, safe="")
    return prefetch_state_dir() / f"{safe_name}.lock"


def should_start_prefetch(query: str) -> bool:
    lock_path = prefetch_lock_path(query)
    now = time.time()
    if lock_path.exists() and now - lock_path.stat().st_mtime < PREFETCH_LOCK_TTL:
        return False
    lock_path.write_text(str(now))
    return True


def clear_prefetch_lock(query: str) -> None:
    prefetch_lock_path(query).unlink(missing_ok=True)


def start_avatar_prefetch(config: dict, query: str, projects: list[dict]) -> None:
    if not should_start_prefetch(query):
        return

    payload = {
        "query": query,
        "projects": [
            {"id": project["id"], "avatar_url": project.get("avatar_url")}
            for project in projects
            if project.get("id") is not None
        ],
    }
    # Write the project list via stdin so secrets are not exposed in process arguments.
    # The child inherits Alfred workflow variables from the environment.
    # Best effort only; search results already returned to Alfred.
    try:
        proc = subprocess.Popen(
            [sys.executable, __file__, "--prefetch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )
        if proc.stdin is not None:
            proc.stdin.write(json.dumps(payload))
            proc.stdin.close()
    except Exception:
        clear_prefetch_lock(query)


def run_prefetch() -> int:
    payload = json.loads(sys.stdin.read())
    config = load_config()
    query = payload["query"]
    try:
        for project in payload["projects"]:
            try:
                cached_avatar_path(config, project["id"], project.get("avatar_url"))
            except Exception:
                continue
    finally:
        clear_prefetch_lock(query)
    return 0


def setup_items(config: dict) -> list[dict]:
    status_bits = [
        f"host={config['host'] or 'missing'}",
        f"user={config['user'] or 'missing'}",
        f"token={masked_token(config['token']) if config['token'] else 'missing'}",
    ]
    return [
        item(
            APP_NAME,
            "Set GITLAB_HOST, GITLAB_USER, and GITLAB_TOKEN in the workflow configuration. "
            + " | ".join(status_bits),
        ),
        item(
            "Open workflow configuration",
            "Alfred: Workflows > GitLab Project Search > Configure Workflow and fill in the variables.",
        ),
    ]


def search_projects(config: dict, search_term: str) -> tuple[list[dict], bool]:
    params = urllib.parse.urlencode(
        {
            "search": search_term,
            "simple": "true",
            "search_namespaces": "true",
            "order_by": "last_activity_at",
            "sort": "desc",
            "per_page": str(MAX_RESULTS),
        }
    )
    url = f"{config['host']}/api/v4/projects?{params}"
    request = urllib.request.Request(
        url,
        headers={
            "PRIVATE-TOKEN": config["token"],
            "Accept": "application/json",
            "User-Agent": "alfred-gitlab-search",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))

    items = []
    missing_avatars = []
    for project in payload:
        namespace = project.get("path_with_namespace") or project.get("name_with_namespace")
        project_name = project.get("name", namespace)
        icon_path = "icon.png"
        if project.get("id") is not None:
            cached_icon = cached_avatar_if_available(project["id"])
            if cached_icon:
                icon_path = cached_icon
            else:
                missing_avatars.append(project)
        items.append(
            item(
                namespace,
                project_name,
                arg=project.get("web_url", ""),
                valid=bool(project.get("web_url")),
                icon_path=icon_path,
            )
        )

    if missing_avatars:
        start_avatar_prefetch(config, search_term, missing_avatars)

    return items, bool(missing_avatars)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--prefetch":
        raise SystemExit(run_prefetch())

    query = " ".join(sys.argv[1:]).strip()
    config = load_config()

    missing = [field for field in ("host", "user", "token") if not config.get(field)]
    if missing:
        respond(setup_items(config))
        return

    if not host_is_secure(config["host"]):
        respond(
            [
                item(
                    APP_NAME,
                    "Use an HTTPS GitLab host in workflow configuration before searching.",
                )
            ]
        )
        return

    if not query:
        respond(
            [
                item(
                    APP_NAME,
                    f"Ready for {config['user']} on {config['host']}. Start typing to search.",
                )
            ]
        )
        return

    if len(query) < MIN_QUERY_LENGTH:
        respond(
            [
                item(
                    APP_NAME,
                    f"Type at least {MIN_QUERY_LENGTH} characters to search GitLab projects.",
                )
            ]
        )
        return

    try:
        results, missing_avatars = search_projects(config, query)
    except urllib.error.HTTPError as exc:
        respond(
            [
                item(
                    "GitLab request failed",
                    f"HTTP {exc.code}. Check GITLAB_HOST or GITLAB_TOKEN in workflow configuration.",
                )
            ]
        )
        return
    except urllib.error.URLError as exc:
        respond(
            [
                item(
                    "GitLab host unreachable",
                    f"{exc.reason}. Verify GITLAB_HOST in workflow configuration.",
                )
            ]
        )
        return
    except Exception as exc:
        respond([item("Search failed", str(exc))])
        return

    if not results:
        respond(
            [
                item(
                    "No matching projects",
                    f"No projects or repos found for '{query}'.",
                )
            ]
        )
        return

    respond(results, rerun=RERUN_INTERVAL if missing_avatars else None)


if __name__ == "__main__":
    main()
