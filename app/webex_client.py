"""Part 4: minimal Webex REST client for the student bot."""

import os

from urllib.parse import quote

import requests

WEBEX_API_BASE = 'https://webexapis.com/v1'

REQUEST_TIMEOUT = 10  # seconds, for every Webex API call


class WebexClient:
    def __init__(self, token=None, session=None):
        if not token:
            token = os.environ.get("WEBEX_BOT_TOKEN")
        token = token.strip() if isinstance(token, str) else ""
        if not token:
            raise PermissionError(
                "Webex bot token is unavailable; set WEBEX_BOT_TOKEN"
            )
        self.token = token
        self.session = session if session is not None else requests.Session()

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _get(self, url):
        response = self.session.get(
            url,
            headers=self.headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response

    def get_message(self, message_id):
        # Quote the ID so an event can only ever address one message.
        url = f"{WEBEX_API_BASE}/messages/{quote(str(message_id), safe='')}"
        return self._get(url).json()

    def get_me(self):
        """Return the bot's own person record (GET /v1/people/me)."""
        return self._get(f"{WEBEX_API_BASE}/people/me").json()

    def download_file(self, file_url):
        return self._get(file_url).content

    def send_message(self, room_id, text):
        response = self.session.post(
            f"{WEBEX_API_BASE}/messages",
            headers=self.headers,
            json={"roomId": room_id, "text": text},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
