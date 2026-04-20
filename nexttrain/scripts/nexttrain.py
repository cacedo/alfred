#!/usr/bin/env python3

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime


API_URL = "https://search.ch/timetable/api/route.json"
WEB_URL = "https://search.ch/timetable/"
RESULT_LIMIT = 3
QUERY_TIMEOUT_SECONDS = 20
HELP_QUERY = "/help"


def alfred_items(items):
    print(json.dumps({"items": items}))


def item(title, subtitle, arg=None, valid=False, icon_path=None):
    result = {
        "title": title,
        "subtitle": subtitle,
        "valid": valid,
    }
    if arg is not None:
        result["arg"] = arg
    if icon_path:
        result["icon"] = {"path": icon_path}
    return result


def workflow_icon():
    return os.path.join(os.path.dirname(__file__), "..", "icon.png")


def config():
    return {
        "from": os.environ.get("NEXTTRAIN_FROM", "").strip(),
        "to": os.environ.get("NEXTTRAIN_TO", "").strip(),
    }


def timetable_url(origin, destination):
    return f"{WEB_URL}?{urllib.parse.urlencode({'from': origin, 'to': destination})}"


def route_url(origin, destination, departure_time):
    params = {
        "from": origin,
        "to": destination,
        "time": departure_time.strftime("%H:%M"),
        "date": departure_time.strftime("%m/%d/%Y"),
    }
    return timetable_url(origin, destination) + "&" + urllib.parse.urlencode(
        {"time": params["time"], "date": params["date"]}
    )


def http_json(url, params):
    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        request_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "alfred-nexttrain/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=QUERY_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from search.ch: {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error talking to search.ch: {exc.reason}") from exc


def parse_timestamp(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def format_clock(value):
    return parse_timestamp(value).strftime("%H:%M")


def format_duration(seconds):
    minutes = max(int(seconds // 60), 0)
    hours, remaining = divmod(minutes, 60)
    if hours:
        return f"{hours}h {remaining}m"
    return f"{remaining}m"


def minutes_until_departure(departure_time):
    delta = departure_time - datetime.now(departure_time.tzinfo)
    minutes = int(delta.total_seconds() // 60)
    return max(minutes, 0)


def summarize_legs(connection):
    for leg in connection.get("legs", []):
        if leg.get("type") == "walk":
            continue
        number = (leg.get("number") or leg.get("line") or "").strip()
        if number:
            return number
    return "Connection"


def transfer_count(connection):
    transit_legs = [leg for leg in connection.get("legs", []) if leg.get("line")]
    return max(len(transit_legs) - 1, 0)


def platform_text(connection):
    for leg in connection.get("legs", []):
        track = str(leg.get("track", "")).strip()
        if track:
            return f"Platform {track}"
    return None


def connection_item(connection, origin, destination):
    departure_time = parse_timestamp(connection["departure"])
    departure = departure_time.strftime("%H:%M")
    minutes = minutes_until_departure(departure_time)
    train_number = summarize_legs(connection)
    return item(
        f"In {minutes} minutes • {departure} • {train_number}",
        f"{origin} -> {destination}",
        arg=route_url(origin, destination, departure_time),
        valid=True,
        icon_path=workflow_icon(),
    )


def missing_config_items():
    cfg = config()
    examples = []
    if not cfg["from"]:
        examples.append("NEXTTRAIN_FROM")
    if not cfg["to"]:
        examples.append("NEXTTRAIN_TO")
    return [
        item(
            "Set the station names in Configure Workflow…",
            f"Missing: {', '.join(examples)}",
            icon_path=workflow_icon(),
        ),
        item(
            "Example",
            "Set NEXTTRAIN_FROM=Zurich HB and NEXTTRAIN_TO=Bern",
            icon_path=workflow_icon(),
        ),
    ]


def help_items():
    cfg = config()
    if cfg["from"] and cfg["to"]:
        subtitle = f"Configured route: {cfg['from']} -> {cfg['to']}"
    else:
        subtitle = "Configure NEXTTRAIN_FROM and NEXTTRAIN_TO in Alfred."
    return [
        item(
            "Show the next 3 departures",
            subtitle,
            icon_path=workflow_icon(),
        ),
        item(
            "Open the full timetable in the browser",
            "Press Enter on any result to open search.ch.",
            arg=timetable_url(cfg["from"], cfg["to"]) if cfg["from"] and cfg["to"] else None,
            valid=bool(cfg["from"] and cfg["to"]),
            icon_path=workflow_icon(),
        ),
    ]


def search():
    cfg = config()
    if not cfg["from"] or not cfg["to"]:
        alfred_items(missing_config_items())
        return

    try:
        payload = http_json(
            API_URL,
            {
                "from": cfg["from"],
                "to": cfg["to"],
                "num": RESULT_LIMIT,
                "show_delays": 1,
                "show_trackchanges": 1,
            },
        )
    except RuntimeError as exc:
        alfred_items(
            [
                item(
                    "Could not load departures",
                    str(exc),
                    icon_path=workflow_icon(),
                ),
                item(
                    "Open search.ch in the browser",
                    f"{cfg['from']} -> {cfg['to']}",
                    arg=timetable_url(cfg["from"], cfg["to"]),
                    valid=True,
                    icon_path=workflow_icon(),
                ),
            ]
        )
        return

    connections = payload.get("connections", [])[:RESULT_LIMIT]
    if not connections:
        alfred_items(
            [
                item(
                    "No departures found",
                    f"No upcoming connections from {cfg['from']} to {cfg['to']}.",
                    icon_path=workflow_icon(),
                ),
                item(
                    "Open search.ch in the browser",
                    f"{cfg['from']} -> {cfg['to']}",
                    arg=timetable_url(cfg["from"], cfg["to"]),
                    valid=True,
                    icon_path=workflow_icon(),
                ),
            ]
        )
        return

    items = [connection_item(connection, cfg["from"], cfg["to"]) for connection in connections]
    items.append(
        item(
            "Open full timetable",
            f"{cfg['from']} -> {cfg['to']}",
            arg=timetable_url(cfg["from"], cfg["to"]),
            valid=True,
            icon_path=workflow_icon(),
        )
    )
    alfred_items(items)


def action(argument):
    if not argument:
        return
    webbrowser.open(argument)


def main(argv):
    command = argv[1] if len(argv) > 1 else "search"
    argument = argv[2] if len(argv) > 2 else ""

    if command == "search":
        if argument.strip() == HELP_QUERY:
            alfred_items(help_items())
            return
        search()
        return

    if command == "action":
        action(argument)
        return

    alfred_items([item("Unknown command", command, icon_path=workflow_icon())])


if __name__ == "__main__":
    main(sys.argv)
