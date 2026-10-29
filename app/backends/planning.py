from app.planner import build_plan


def plan_interface(
    router,
    desired_interface,
    get_interface_state,
):
    """Build a Part 2 plan from backend status."""
    state = get_interface_state(router, desired_interface["name"])

    if not isinstance(state, dict):
        return {"status": "error", "result": "backend_failed"}
    if state.get("status") == "error":
        return state

    if state.get("result") == "not_found":
        return build_plan(desired_interface, None)
    if state.get("result") == "found" and isinstance(state.get("interface"), dict):
        return build_plan(desired_interface, state["interface"])

    return {"status": "error", "result": "backend_failed"}
