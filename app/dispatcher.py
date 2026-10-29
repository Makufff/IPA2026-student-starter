"""Part 4: route an already validated request to its Part 3 backend."""

from app.backends import ansible, netconf, netmiko_textfsm, restconf

# (method, action) -> (backend module, function name).
# The function is looked up on the module at call time, so the current
# backend implementation (or a test double patched onto it) is used.
ROUTES = {
    ("restconf", "status"): (restconf, "get_interface_state"),
    ("restconf", "plan"): (restconf, "plan_interface"),
    ("restconf", "apply"): (restconf, "apply_interface"),
    ("netconf", "status"): (netconf, "get_interface_state"),
    ("netconf", "plan"): (netconf, "plan_interface"),
    ("netconf", "apply"): (netconf, "apply_interface"),
    ("ansible", "apply"): (ansible, "apply_interface"),
    ("netmiko-textfsm", "status"): (netmiko_textfsm, "get_interface_state"),
    ("netmiko-textfsm", "delete"): (netmiko_textfsm, "delete_interface"),
}

METHODS = {method for method, _ in ROUTES}

# These actions identify the interface by name only.
NAME_ONLY_ACTIONS = {"status", "delete"}


def dispatch_request(data):
    """Call the backend for a validated request and return its response."""
    method = data.get("method")
    action = data.get("action")

    # Validation should already have rejected these; never guess a backend.
    if method not in METHODS:
        return {"status": "error", "result": "invalid_method"}
    route = ROUTES.get((method, action))
    if route is None:
        return {"status": "error", "result": "invalid_action"}

    module, function_name = route
    backend = getattr(module, function_name)

    router = data["router"]
    if isinstance(router, str):
        # Validation accepts surrounding whitespace; connect to the bare address.
        router = router.strip()
    interface = data["desired"]["interface"]

    if action in NAME_ONLY_ACTIONS:
        return backend(router, interface["name"])
    return backend(router, interface)
