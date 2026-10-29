import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ANSIBLE_DIR = Path(__file__).resolve().parents[2] / "ansible"
PLAYBOOK = ANSIBLE_DIR / "apply_interface.yml"
INVENTORY = ANSIBLE_DIR / "inventory.ini"
PLAYBOOK_TIMEOUT = 15

AUTH_ERRORS = (
    "authentication failed",
    "failed to authenticate",
    "authenticationexception",
    "bad authentication",
    "authorization failed",
)
CONNECTION_ERRORS = (
    "unreachable!",
    "timed out",
    "timeout value",
    "unable to connect",
    "connection refused",
    "no route to host",
    "network is unreachable",
    "connection reset",
    "no existing session",
    "error reading ssh protocol banner",
    "unable to open shell",
)


def apply_interface(
    router,
    desired_interface,
    run_playbook=None,
):
    """Apply complete desired interface state using Ansible."""
    if run_playbook is None:
        run_playbook = _run_playbook

    try:
        run_playbook(_playbook_variables(router, desired_interface))
    except Exception as exc:
        return _error(exc)

    return {"status": "ok", "result": "applied"}


def _playbook_variables(router, desired_interface):
    address = ipaddress.IPv4Interface(str(desired_interface["ipv4"]).strip())

    return {
        "router": router,
        "interface_name": desired_interface["name"],
        "ipv4_address": str(address.ip),
        "netmask": str(address.netmask),
        "description": desired_interface["description"],
        "shutdown": desired_interface["admin_state"] == "down",
    }


def _error(exc):
    if isinstance(exc, PermissionError):
        result = "authentication_failed"
    elif isinstance(exc, (ConnectionError, TimeoutError)):
        result = "connection_failed"
    else:
        result = "backend_failed"

    return {"status": "error", "result": result}


def _run_playbook(variables):
    for key in ("router", "interface_name", "description"):
        if not str(variables[key]).isprintable():
            raise ValueError(f"{key} contains control characters")

    command = [
        _ansible_playbook(),
        "-i",
        str(INVENTORY),
        "-e",
        json.dumps(variables),
        str(PLAYBOOK),
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=PLAYBOOK_TIMEOUT,
            env=_ansible_environment(),
            cwd=ANSIBLE_DIR,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConnectionError("ansible-playbook timed out") from exc
    except OSError as exc:
        raise RuntimeError("ansible-playbook could not be started") from exc

    output = f"{completed.stdout}\n{completed.stderr}"

    if completed.returncode == 0 and re.search(r"\bok=[1-9]", output):
        return

    raise _playbook_error(output)


def _ansible_playbook():
    executable = Path(sys.executable).with_name("ansible-playbook")
    return str(executable) if executable.exists() else "ansible-playbook"


def _ansible_environment():
    env = dict(os.environ)
    env.update(
        {
            "ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.default",
            "ANSIBLE_NOCOLOR": "True",
            "ANSIBLE_RETRY_FILES_ENABLED": "False",
            "ANSIBLE_INVENTORY_UNPARSED_FAILED": "True",
        }
    )
    return env


def _playbook_error(output):
    text = output.lower()

    if any(pattern in text for pattern in AUTH_ERRORS):
        return PermissionError("router rejected the credentials")

    if re.search(r"unreachable=[1-9]", text) or any(
        pattern in text for pattern in CONNECTION_ERRORS
    ):
        return ConnectionError("router could not be reached")

    return RuntimeError("ansible playbook failed")
