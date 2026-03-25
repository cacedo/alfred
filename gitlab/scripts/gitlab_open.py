#!/usr/bin/env python3

import subprocess
import sys
import urllib.parse
import os


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


def main() -> None:
    if len(sys.argv) < 2:
        return
    target = sys.argv[1].strip()
    if not target.startswith(("http://", "https://")):
        return
    configured_host = normalize_host(os.environ.get("GITLAB_HOST", ""))
    if configured_host:
        target_url = urllib.parse.urlparse(target)
        host_url = urllib.parse.urlparse(configured_host)
        if (target_url.scheme, target_url.netloc) != (host_url.scheme, host_url.netloc):
            return
    subprocess.run(["open", target], check=False)


if __name__ == "__main__":
    main()
