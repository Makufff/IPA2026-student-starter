import os
import re
import xml.etree.ElementTree as ET
from ipaddress import IPv4Interface
from xml.sax.saxutils import escape

import paramiko
from ncclient import manager
from ncclient.operations.errors import TimeoutExpiredError
from ncclient.transport.errors import (
    AuthenticationError,
    PermissionError as NetconfPermissionError,
    SessionError,
    TransportError,
)

from app.backends import planning


PORT = 830
TIMEOUT = 10


def _connect(router):
    return manager.connect(
        host=router,
        port=PORT,
        username=os.environ.get("ROUTER_USER", ""),
        password=os.environ.get("ROUTER_PASS", ""),
        hostkey_verify=False,
        allow_agent=False,
        look_for_keys=False,
        device_params={"name": "iosxe"},
        timeout=TIMEOUT,
        manager_params={"timeout": TIMEOUT},
    )


def _default_get_config(router, filter_xml):
    with _connect(router) as session:
        reply = session.get_config(
            source="running",
            filter=("subtree", filter_xml),
        )
        return reply.data_xml


def _default_edit_config(router, config_xml):
    # IOS XE accepts a Loopback "replace" that changes the primary IPv4 but
    # never applies the new address to the running config, so merge the
    # address in first, then replace to remove stale description/shutdown.
    merge_xml = config_xml.replace(' nc:operation="replace"', "").replace(
        "<shutdown/>", ""
    )
    with _connect(router) as session:
        for xml in (merge_xml, config_xml):
            reply = session.edit_config(target="running", config=xml)
            if not reply.ok:
                raise RuntimeError(str(reply.error))


def _error(code):
    return {"status": "error", "result": code}


def _exception_error(exc):
    if isinstance(
        exc,
        (
            PermissionError,
            AuthenticationError,
            NetconfPermissionError,
            paramiko.AuthenticationException,
        ),
    ):
        return _error("authentication_failed")
    if isinstance(
        exc,
        (
            TransportError,
            SessionError,
            TimeoutExpiredError,
            paramiko.SSHException,
            OSError,
            EOFError,
        ),
    ):
        return _error("connection_failed")
    return _error("backend_failed")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child(element, name):
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _text(element, name, strip=True):
    child = _child(element, name)
    if child is None or child.text is None:
        return None
    return child.text.strip() if strip else child.text


def _status_filter(interface_name):
    return (
        '<interfaces xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces">'
        "<interface>"
        f"<name>{escape(str(interface_name))}</name>"
        "</interface>"
        "</interfaces>"
    )


def _find_interface(root, interface_name):
    for interfaces in root.iter():
        if _local(interfaces.tag) != "interfaces":
            continue
        for interface in interfaces:
            if (
                _local(interface.tag) == "interface"
                and _text(interface, "name") == interface_name
            ):
                return interface
    return None


def _ipv4(interface):
    ipv4 = _child(interface, "ipv4")
    address = _child(ipv4, "address") if ipv4 is not None else None
    if address is None:
        return None

    ip = _text(address, "ip")
    if not ip:
        return None
    mask = _text(address, "netmask") or _text(address, "prefix-length")
    return IPv4Interface(f"{ip}/{mask}").with_prefixlen


def _normalize(interface):
    enabled = _text(interface, "enabled")

    return {
        "name": _text(interface, "name"),
        "ipv4": _ipv4(interface),
        "description": _text(interface, "description", strip=False),
        "admin_state": "down" if enabled == "false" else "up",
    }


def _loopback_number(name):
    match = re.fullmatch(r"Loopback(\d+)", str(name).strip(), re.IGNORECASE)
    if match is None:
        raise ValueError(f"not a Loopback interface: {name}")
    return match.group(1)


def _native_config(desired_interface):
    number = _loopback_number(desired_interface["name"])
    ipv4 = IPv4Interface(desired_interface["ipv4"])

    description = ""
    if desired_interface.get("description") is not None:
        description = (
            "<description>"
            f"{escape(str(desired_interface['description']))}"
            "</description>"
        )

    shutdown = ""
    if desired_interface["admin_state"] == "down":
        shutdown = "<shutdown/>"

    return (
        '<config xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" '
        'xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0">'
        '<native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">'
        "<interface>"
        '<Loopback nc:operation="replace">'
        f"<name>{number}</name>"
        f"{description}"
        "<ip><address><primary>"
        f"<address>{ipv4.ip}</address>"
        f"<mask>{ipv4.netmask}</mask>"
        "</primary></address></ip>"
        f"{shutdown}"
        "</Loopback>"
        "</interface>"
        "</native>"
        "</config>"
    )


def get_interface_state(
    router,
    interface_name,
    netconf_get_config=None,
):
    """Return normalized interface status using NETCONF."""
    netconf_get_config = netconf_get_config or _default_get_config

    try:
        xml_text = netconf_get_config(router, _status_filter(interface_name))
        interface = _find_interface(
            ET.fromstring(xml_text),
            str(interface_name),
        )

        if interface is None:
            return {"status": "ok", "result": "not_found", "interface": None}

        normalized = _normalize(interface)
    except Exception as exc:
        return _exception_error(exc)

    return {"status": "ok", "result": "found", "interface": normalized}


def plan_interface(
    router,
    desired_interface,
    netconf_get_config=None,
):
    """Return create/update/no_change plan using NETCONF status."""
    return planning.plan_interface(
        router,
        desired_interface,
        lambda router, name: get_interface_state(
            router,
            name,
            netconf_get_config,
        ),
    )


def apply_interface(
    router,
    desired_interface,
    netconf_edit_config=None,
):
    """Apply complete desired interface state using NETCONF."""
    netconf_edit_config = netconf_edit_config or _default_edit_config

    try:
        netconf_edit_config(router, _native_config(desired_interface))
    except Exception as exc:
        return _exception_error(exc)

    return {"status": "ok", "result": "applied"}
