"""Part 4: Webex webhook receiver and event processing."""

import json

import logging

import os

from collections import OrderedDict

from concurrent.futures import ThreadPoolExecutor

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path

from tempfile import TemporaryDirectory

from threading import Lock

from urllib.parse import urlsplit

from app.integration import process_request

from app.webex_client import WEBEX_API_BASE, WebexClient

logger = logging.getLogger("app.webhook_server")

MAX_BODY_BYTES = 1_000_000
# An oversized body is read and discarded up to this size before replying,
# so the client receives the 400 instead of a connection reset.
MAX_DISCARD_BYTES = 10 * MAX_BODY_BYTES

HEALTHY = {"status": "ok", "result": "healthy"}
ACCEPTED = {"status": "ok", "result": "accepted"}
INVALID_EVENT = {"status": "error", "result": "invalid_event"}
NOT_FOUND = {"status": "error", "result": "not_found"}
MULTIPLE_ATTACHMENTS = {"status": "error", "result": "multiple_attachments"}
BACKEND_FAILED = {"status": "error", "result": "backend_failed"}


def message_id_of(event):
    """Return the message ID of a messages/created event, else None."""
    if not isinstance(event, dict):
        return None
    if event.get("resource") != "messages" or event.get("event") != "created":
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    message_id = data.get("id")
    if not isinstance(message_id, str) or not message_id.strip():
        return None
    return message_id


class WebexEventHandler:
    def __init__(self, client, bot_person_id, processor=None):
        self.client = client
        self.bot_person_id = bot_person_id
        # None means app.integration.process_request, resolved at call time.
        self.processor = processor

    def handle_event(self, event):
        message_id = message_id_of(event)
        if message_id is None:
            return None

        # Skip the bot's own messages before any API call to avoid loops.
        if self._is_own(event["data"].get("personId")):
            return None

        message = self.client.get_message(message_id)
        if not isinstance(message, dict) or self._is_own(message.get("personId")):
            return None

        room_id = message.get("roomId")
        if not isinstance(room_id, str) or not room_id:
            logger.warning("Message %s has no roomId; ignoring", message_id)
            return None

        try:
            result = self._process_message(message)
        except Exception:
            # Still answer the room, then let the caller log the failure.
            self._reply_failure(room_id, message_id)
            raise

        self.client.send_message(room_id, json.dumps(result))
        logger.info(
            "Message %s: %s/%s",
            message_id,
            result.get("status"),
            result.get("result"),
        )
        return result

    def _is_own(self, person_id):
        return bool(self.bot_person_id) and person_id == self.bot_person_id

    def _process_message(self, message):
        processor = self.processor if self.processor is not None else process_request
        mentioned_people = message.get("mentionedPeople", [])

        files = message.get("files") or []
        if not isinstance(files, list):
            files = [files]

        if len(files) > 1:
            return dict(MULTIPLE_ATTACHMENTS)

        if not files:
            result = processor(mentioned_people, None, self.bot_person_id)
        else:
            content = self.client.download_file(files[0])
            if isinstance(content, str):
                content = content.encode("utf-8")
            # The directory and the YAML file inside it are removed on exit,
            # including when processing raises.
            with TemporaryDirectory(prefix="ipa2026-") as directory:
                path = Path(directory) / "request.yaml"
                path.write_bytes(content)
                result = processor(mentioned_people, path, self.bot_person_id)

        if not isinstance(result, dict) or "status" not in result or "result" not in result:
            logger.error("Processor returned an invalid response: %r", result)
            return dict(BACKEND_FAILED)
        return result

    def _reply_failure(self, room_id, message_id):
        logger.error("Processing message %s failed; replying backend_failed", message_id)
        try:
            self.client.send_message(room_id, json.dumps(BACKEND_FAILED))
        except Exception:
            logger.exception("Could not send failure reply for message %s", message_id)


def make_http_handler(submit_event):
    class WebhookRequestHandler(BaseHTTPRequestHandler):
        server_version = "IPA2026Webhook/1.0"
        # Socket timeout so a stalled client cannot hold a thread forever.
        timeout = 15

        def do_GET(self):
            if self._route() == "/health":
                self._send_json(200, HEALTHY)
            else:
                self._send_json(404, NOT_FOUND)

        def do_POST(self):
            if self._route() != "/webhook":
                self._discard_body(self._content_length())
                self._send_json(404, NOT_FOUND)
                return

            event = self._read_event()
            if event is None:
                self._send_json(400, INVALID_EVENT)
                return

            # submit_event only queues the event; processing happens later.
            try:
                submit_event(event)
            except Exception:
                logger.exception("Could not queue webhook event")
                self._send_json(503, BACKEND_FAILED)
                return
            self._send_json(200, ACCEPTED)

        def _route(self):
            return urlsplit(self.path).path

        def _content_length(self):
            if self.headers.get("Transfer-Encoding"):
                return None
            text = (self.headers.get("Content-Length") or "").strip()
            if not (text.isascii() and text.isdigit()):
                return None
            return int(text)

        def _read_event(self):
            length = self._content_length()
            if length is None:
                self.close_connection = True
                return None
            if length < 1 or length > MAX_BODY_BYTES:
                self._discard_body(length)
                return None

            try:
                body = self.rfile.read(length)
            except OSError:
                self.close_connection = True
                return None
            if len(body) != length:
                self.close_connection = True
                return None

            try:
                event = json.loads(body)
            except (ValueError, RecursionError):
                return None
            return event if isinstance(event, dict) else None

        def _discard_body(self, length):
            self.close_connection = True
            if not length or length > MAX_DISCARD_BYTES:
                return
            try:
                self.connection.settimeout(1)
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass

        def _send_json(self, status_code, body):
            payload = json.dumps(body).encode("utf-8")
            try:
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                if self.close_connection:
                    self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                logger.debug("Client disconnected before the response was sent")

        def log_message(self, format, *args):
            logger.debug("%s %s", self.address_string(), format % args)

        def log_error(self, format, *args):
            logger.warning("%s %s", self.address_string(), format % args)

    return WebhookRequestHandler


class SequentialEventQueue:
    """Process events one at a time in a background worker thread."""

    def __init__(self, handle_event, history=1000):
        self._handle_event = handle_event
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="webex-event",
        )
        # Recent message IDs, so a message delivered by two matching
        # webhooks is processed (and answered) only once.
        self._seen = OrderedDict()
        self._history = history
        self._lock = Lock()

    def submit(self, event):
        message_id = message_id_of(event)
        if message_id is not None:
            with self._lock:
                if message_id in self._seen:
                    logger.info("Duplicate delivery of message %s ignored", message_id)
                    return
                self._seen[message_id] = None
                if len(self._seen) > self._history:
                    self._seen.popitem(last=False)
        self._executor.submit(self._run, event)

    def _run(self, event):
        try:
            self._handle_event(event)
        except Exception:
            logger.exception("Failed to process Webex event")

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)


def resolve_bot_person_id(client):
    person = client.get_me()
    person_id = person.get("id") if isinstance(person, dict) else None
    if not isinstance(person_id, str) or not person_id:
        raise RuntimeError(f"{WEBEX_API_BASE}/people/me returned no person id")
    return person_id


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host = os.environ.get("WEBHOOK_HOST") or "0.0.0.0"
    port = int(os.environ.get("WEBHOOK_PORT") or 8000)

    client = WebexClient()
    bot_person_id = resolve_bot_person_id(client)
    handler = WebexEventHandler(client, bot_person_id)
    queue = SequentialEventQueue(handler.handle_event)

    server = ThreadingHTTPServer((host, port), make_http_handler(queue.submit))
    logger.info("Webhook listening on %s:%s for bot %s", host, port, bot_person_id)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        queue.shutdown()


if __name__ == "__main__":
    main()
