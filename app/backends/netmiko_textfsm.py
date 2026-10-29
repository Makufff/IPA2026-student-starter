import os
import re
from contextlib import contextmanager


DEVICE_TYPE = "cisco_ios"
CONNECT_TIMEOUT = 8

ABBREVIATIONS = {
    "lo": "Loopback",
    "gi": "GigabitEthernet",
    "te": "TenGigabitEthernet",
    "fa": "FastEthernet",
    "et": "Ethernet",
    "tu": "Tunnel",
    "vl": "Vlan",
    "po": "Port-channel",
    "se": "Serial",
}
MISSING_MARKERS = (
    "invalid input",
    "incomplete command",
    "ambiguous command",
    "not found",
)


def get_interface_state(
    router,
    interface_name,
    netmiko_get=None,
):
    """Return normalized interface status using Netmiko + TextFSM."""
    if netmiko_get is None:
        netmiko_get = _netmiko_get

    try:
        ip_info, descriptions = netmiko_get(router, interface_name)
        interface = _normalize_interface(ip_info, descriptions, interface_name)
    except Exception as exc:
        return _error(exc)

    if interface is None:
        return _not_found()

    return {"status": "ok", "result": "found", "interface": interface}


def delete_interface(
    router,
    interface_name,
    netmiko_delete=None,
):
    """Delete an interface using Netmiko."""
    if netmiko_delete is None:
        netmiko_delete = _netmiko_delete

    try:
        result = netmiko_delete(router, interface_name)
    except Exception as exc:
        return _error(exc)

    if result == "deleted":
        return {"status": "ok", "result": "deleted"}

    if result == "not_found":
        return _not_found()

    return {"status": "error", "result": "backend_failed"}


def _not_found():
    return {"status": "ok", "result": "not_found", "interface": None}


def _error(exc):
    if isinstance(exc, PermissionError):
        result = "authentication_failed"
    elif isinstance(exc, (ConnectionError, TimeoutError)):
        result = "connection_failed"
    else:
        result = "backend_failed"

    return {"status": "error", "result": result}


def _normalize_interface(ip_info, descriptions, interface_name):
    record = _find_present(ip_info, interface_name)
    if record is None:
        return None

    address = _first(record.get("ip_address"))
    prefix = _first(record.get("prefix_length"))
    ipv4 = f"{address}/{prefix}" if address and prefix else None

    description = None
    description_record = _find_record(descriptions, "port", interface_name)
    if description_record is not None:
        description = description_record.get("description") or None

    link_status = str(record.get("link_status") or "").lower()

    return {
        "name": _expand_name(record["interface"]),
        "ipv4": ipv4,
        "description": description,
        "admin_state": "down" if "administratively down" in link_status else "up",
    }


def _find_present(ip_info, interface_name):
    record = _find_record(ip_info, "interface", interface_name)
    if record is None:
        return None

    if str(record.get("link_status") or "").strip().lower() == "deleted":
        return None

    return record


def _find_record(records, key, interface_name):
    if isinstance(records, str):
        if _is_missing(records):
            return None
        raise RuntimeError("CLI output was not parsed by TextFSM")

    if isinstance(records, dict):
        records = [records]

    target = _expand_name(interface_name).lower()
    for record in records or []:
        if _expand_name(record.get(key, "")).lower() == target:
            return record

    return None


def _expand_name(name):
    name = "".join(str(name).split())
    match = re.fullmatch(r"([A-Za-z-]+)(\d\S*)", name)
    if match and match.group(1).lower() in ABBREVIATIONS:
        return ABBREVIATIONS[match.group(1).lower()] + match.group(2)

    return name


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None

    return value


def _is_missing(output):
    text = str(output).strip().lower()
    return not text or any(marker in text for marker in MISSING_MARKERS)


def _valid_name(interface_name):
    return isinstance(interface_name, str) and bool(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9 ./:-]*", interface_name)
    )


def _netmiko_get(router, interface_name):
    if not _valid_name(interface_name):
        return [], []

    with _connect(router) as connection:
        ip_info = _show_ip_interface(connection, interface_name)
        if _find_present(ip_info, interface_name) is None:
            return [], []

        command = "show interfaces description"
        descriptions = _parse(command, connection.send_command(command))

    return ip_info, descriptions


def _netmiko_delete(router, interface_name):
    if not _valid_name(interface_name):
        return "not_found"

    with _connect(router) as connection:
        record = _find_present(
            _show_ip_interface(connection, interface_name),
            interface_name,
        )
        if record is None:
            return "not_found"

        connection.send_config_set([f"no interface {record['interface']}"])

        if _find_present(
            _show_ip_interface(connection, interface_name),
            interface_name,
        ) is not None:
            raise RuntimeError(f"{interface_name} is still present")

    return "deleted"


def _show_ip_interface(connection, interface_name):
    command = f"show ip interface {interface_name}"
    output = connection.send_command(command)
    if _is_missing(output):
        return []

    return _parse(command, output)


def _parse(command, output):
    from ntc_templates.parse import parse_output

    return parse_output(platform=DEVICE_TYPE, command=command, data=output)


@contextmanager
def _connect(router):
    from netmiko import ConnectHandler
    from netmiko.exceptions import NetmikoTimeoutException, ReadTimeout
    from paramiko.ssh_exception import AuthenticationException, SSHException

    try:
        with ConnectHandler(
            device_type=DEVICE_TYPE,
            host=router,
            username=os.environ.get("ROUTER_USER", ""),
            password=os.environ.get("ROUTER_PASS", ""),
            conn_timeout=CONNECT_TIMEOUT,
            auth_timeout=CONNECT_TIMEOUT,
            banner_timeout=CONNECT_TIMEOUT,
        ) as connection:
            yield connection
    except AuthenticationException as exc:
        raise PermissionError(str(exc)) from exc
    except (NetmikoTimeoutException, ReadTimeout, SSHException, EOFError, OSError) as exc:
        raise ConnectionError(str(exc)) from exc
