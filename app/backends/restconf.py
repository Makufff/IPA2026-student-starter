import os
import re
from ipaddress import IPv4Interface
from urllib.parse import quote

import requests
import urllib3

from app.backends import planning


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TIMEOUT = 10
HEADERS = {
    "Accept": "application/yang-data+json",
    "Content-Type": "application/yang-data+json",
}
SUCCESS_CODES = (200, 201, 204)


def _auth():
    return (
        os.environ.get("ROUTER_USER", ""),
        os.environ.get("ROUTER_PASS", ""),
    )


def _default_get(url):
    return requests.get(
        url,
        auth=_auth(),
        headers=HEADERS,
        verify=False,
        timeout=TIMEOUT,
    )


def _default_put(url, json):
    return requests.put(
        url,
        json=json,
        auth=_auth(),
        headers=HEADERS,
        verify=False,
        timeout=TIMEOUT,
    )


def _error(code):
    return {"status": "error", "result": code}


def _http_error(status_code):
    if status_code in (401, 403):
        return _error("authentication_failed")
    return _error("backend_failed")


def _exception_error(exc):
    if isinstance(exc, PermissionError):
        return _error("authentication_failed")
    if isinstance(
        exc,
        (requests.exceptions.ConnectionError, requests.exceptions.Timeout),
    ):
        return _error("connection_failed")
    if isinstance(exc, requests.exceptions.RequestException):
        return _error("backend_failed")
    if isinstance(exc, OSError):
        return _error("connection_failed")
    return _error("backend_failed")


def _not_found():
    return {"status": "ok", "result": "not_found", "interface": None}


def _to_cidr(address):
    ip = address.get("ip")
    if not ip:
        return None
    mask = address.get("netmask") or address.get("prefix-length")
    return IPv4Interface(f"{ip}/{mask}").with_prefixlen


def _normalize(entry, interface_name):
    ipv4 = entry.get("ietf-ip:ipv4") or entry.get("ipv4") or {}
    addresses = ipv4.get("address") or []
    enabled = entry.get("enabled", True)

    return {
        "name": entry.get("name", interface_name),
        "ipv4": _to_cidr(addresses[0]) if addresses else None,
        "description": entry.get("description"),
        "admin_state": "down" if str(enabled).lower() == "false" else "up",
    }


def _loopback_number(name):
    match = re.fullmatch(r"Loopback(\d+)", str(name).strip(), re.IGNORECASE)
    if match is None:
        raise ValueError(f"not a Loopback interface: {name}")
    return int(match.group(1))


def _native_payload(desired_interface, number):
    ipv4 = IPv4Interface(desired_interface["ipv4"])
    loopback = {"name": number}

    if desired_interface.get("description") is not None:
        loopback["description"] = str(desired_interface["description"])

    loopback["ip"] = {
        "address": {
            "primary": {
                "address": str(ipv4.ip),
                "mask": str(ipv4.netmask),
            },
        },
    }

    if desired_interface["admin_state"] == "down":
        loopback["shutdown"] = [None]

    return {"Cisco-IOS-XE-native:Loopback": loopback}


def get_interface_state(
    router,
    interface_name,
    http_get=None,
):
    """Return normalized interface status using RESTCONF."""
    http_get = http_get or _default_get

    try:
        url = (
            f"https://{router}/restconf/data/"
            f"ietf-interfaces:interfaces/interface="
            f"{quote(str(interface_name), safe='')}"
        )
        response = http_get(url)

        if response.status_code in (204, 404):
            return _not_found()
        if response.status_code != 200:
            return _http_error(response.status_code)

        data = response.json()
        entry = data.get("ietf-interfaces:interface", data.get("interface"))
        if isinstance(entry, list):
            entry = entry[0] if entry else None
        if not entry:
            return _not_found()

        interface = _normalize(entry, interface_name)
    except Exception as exc:
        return _exception_error(exc)

    return {"status": "ok", "result": "found", "interface": interface}


def plan_interface(
    router,
    desired_interface,
    http_get=None,
):
    """Return create/update/no_change plan using RESTCONF status."""
    return planning.plan_interface(
        router,
        desired_interface,
        lambda router, name: get_interface_state(router, name, http_get),
    )


def apply_interface(
    router,
    desired_interface,
    http_put=None,
):
    """Apply complete desired interface state using RESTCONF."""
    http_put = http_put or _default_put

    try:
        number = _loopback_number(desired_interface["name"])
        url = (
            f"https://{router}/restconf/data/"
            f"Cisco-IOS-XE-native:native/interface/Loopback={number}"
        )
        response = http_put(
            url,
            json=_native_payload(desired_interface, number),
        )

        if response.status_code not in SUCCESS_CODES:
            return _http_error(response.status_code)
    except Exception as exc:
        return _exception_error(exc)

    return {"status": "ok", "result": "applied"}
