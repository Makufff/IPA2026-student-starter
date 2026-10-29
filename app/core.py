import yaml


ALLOWED_ROUTERS = {
    "10.0.29.101",
    "10.0.29.102",
    "10.0.29.103",
    "10.0.29.104",
    "10.0.29.105",
}

METHOD_ACTIONS = {
    "restconf": {"status", "plan", "apply"},
    "netconf": {"status", "plan", "apply"},
    "ansible": {"apply"},
    "netmiko-textfsm": {"status", "delete"},
}

ADMIN_STATES = {"up", "down"}


def error(result):
    return {"status": "error", "result": result}


def is_missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def validate_request(data):
    """
    Validate one IPA2026 desired-state request.

    Return a response dictionary according to
    specs/response-spec-v1.md.
    """
    if not isinstance(data, dict):
        return error("invalid_yaml")

    # bool is a subclass of int, so check the exact type.
    version = data.get("version")
    if type(version) is not int or version != 1:
        return error("invalid_version")

    router = data.get("router")
    if is_missing(router):
        return error("missing_router")
    if not isinstance(router, str) or router.strip() not in ALLOWED_ROUTERS:
        return error("invalid_router")

    method = data.get("method")
    if is_missing(method):
        return error("missing_method")
    if not isinstance(method, str) or method not in METHOD_ACTIONS:
        return error("invalid_method")

    action = data.get("action")
    if is_missing(action):
        return error("missing_action")
    if not isinstance(action, str) or action not in METHOD_ACTIONS[method]:
        return error("invalid_action")

    desired = data.get("desired")
    if not isinstance(desired, dict):
        return error("missing_desired")

    interface = desired.get("interface")
    if not isinstance(interface, dict):
        return error("missing_interface")

    name = interface.get("name")
    if is_missing(name) or not isinstance(name, str):
        return error("missing_interface_name")

    if action in ("plan", "apply"):
        for field in ("ipv4", "description", "admin_state"):
            if is_missing(interface.get(field)):
                return error(f"missing_{field}")
        if interface["admin_state"] not in ADMIN_STATES:
            return error("invalid_admin_state")

    return {"status": "ok", "result": "accepted"}


def load_request_file(path):
    """
    Read and parse a YAML request file.
    Return the parsed data, or None when it cannot be read as YAML.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def process_request_file(path):
    """
    Read a YAML request file, validate it,
    and return a response dictionary.
    """
    data = load_request_file(path)
    if not isinstance(data, dict):
        return error("invalid_yaml")
    return validate_request(data)
