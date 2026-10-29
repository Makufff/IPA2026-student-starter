"""Part 4: connect Webex input, Part 1 validation and Part 3 backends."""

from app.core import load_request_file, validate_request

from app.dispatcher import dispatch_request

from app.request_handler import validate_webex_envelope


def process_request(mentioned_people, attachment_path, bot_person_id):
    """
    Validate the Webex envelope and the attached YAML request, then dispatch
    an accepted request and return the backend response unchanged.
    """
    envelope_error = validate_webex_envelope(
        mentioned_people,
        attachment_path,
        bot_person_id,
    )
    if envelope_error is not None:
        return envelope_error

    # Read and parse the attachment exactly once; the same parsed mapping is
    # validated and then dispatched.
    data = load_request_file(attachment_path)
    if not isinstance(data, dict):
        return {"status": "error", "result": "invalid_yaml"}

    validation = validate_request(data)
    if (
        not isinstance(validation, dict)
        or validation.get("status") != "ok"
        or validation.get("result") != "accepted"
    ):
        return validation

    return dispatch_request(data)
