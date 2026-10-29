"""Create or update the bot's Webex messages/created webhooks.

Usage:
    python scripts/register_webhook.py https://example.trycloudflare.com
    python scripts/register_webhook.py --from-tunnel

The token is read from WEBEX_BOT_TOKEN (environment or .env). One webhook
is kept per room listed in WEBEX_ROOM_TITLES (comma separated, default
"IPA2026,IPA2026 Exam Room"); each points at <public-url>/webhook.
"""

import argparse
import os
import sys
from pathlib import Path

import requests

WEBEX_API_BASE = "https://webexapis.com/v1"
DEFAULT_ROOM_TITLES = "IPA2026,IPA2026 Exam Room"
TUNNEL_METRICS_URL = "http://127.0.0.1:20241/quicktunnel"
TIMEOUT = 10


def load_env_file(path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def tunnel_url():
    response = requests.get(TUNNEL_METRICS_URL, timeout=TIMEOUT)
    response.raise_for_status()
    hostname = response.json().get("hostname")
    if not hostname:
        raise RuntimeError("cloudflared has not published a quick tunnel hostname yet")
    return f"https://{hostname}"


def api(session, method, path, **kwargs):
    response = session.request(method, f"{WEBEX_API_BASE}{path}", timeout=TIMEOUT, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("public_url", nargs="?", help="external HTTPS base URL of the bot")
    parser.add_argument("--from-tunnel", action="store_true",
                        help="read the quick tunnel URL from the cloudflared metrics port")
    parser.add_argument("--any-room", action="store_true",
                        help="keep one unfiltered webhook instead of one per room")
    args = parser.parse_args()

    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    token = os.environ.get("WEBEX_BOT_TOKEN")
    if not token:
        sys.exit("WEBEX_BOT_TOKEN is not set")

    if args.from_tunnel:
        base_url = tunnel_url()
    elif args.public_url:
        base_url = args.public_url
    else:
        parser.error("give a public URL or --from-tunnel")
    target_url = base_url.rstrip("/") + "/webhook"
    if not target_url.startswith("https://"):
        sys.exit("Webex requires an HTTPS target URL")

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    if args.any_room:
        # One unfiltered webhook: a bot only receives group messages that mention it.
        room_filters = {"any room": None}
    else:
        titles = [t.strip() for t in os.environ.get("WEBEX_ROOM_TITLES", DEFAULT_ROOM_TITLES).split(",")]
        rooms = api(session, "GET", "/rooms", params={"type": "group", "max": 1000})["items"]
        room_filters = {}
        for title in titles:
            matches = [room["id"] for room in rooms if room.get("title") == title]
            if len(matches) != 1:
                sys.exit(f"expected the bot to be in exactly one room titled {title!r}, found {len(matches)}")
            room_filters[title] = f"roomId={matches[0]}"

    webhooks = api(session, "GET", "/webhooks", params={"max": 100})["items"]
    for title, room_filter in room_filters.items():
        name = f"ipa2026 {title}"
        existing = [
            hook for hook in webhooks
            if hook.get("resource") == "messages"
            and hook.get("event") == "created"
            and hook.get("filter") == room_filter
        ]
        if existing:
            hook = existing[0]
            api(session, "PUT", f"/webhooks/{hook['id']}",
                json={"name": name, "targetUrl": target_url, "status": "active"})
            for duplicate in existing[1:]:
                api(session, "DELETE", f"/webhooks/{duplicate['id']}")
            print(f"updated  {title}: {target_url}")
        else:
            body = {
                "name": name,
                "targetUrl": target_url,
                "resource": "messages",
                "event": "created",
            }
            if room_filter:
                body["filter"] = room_filter
            api(session, "POST", "/webhooks", json=body)
            print(f"created  {title}: {target_url}")


if __name__ == "__main__":
    main()
