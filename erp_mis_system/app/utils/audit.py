import re
from collections.abc import Mapping

from flask import has_request_context, request

from ..extensions import db
from ..models import AuditLog

SENSITIVE_KEY = re.compile(r"(password|secret|token|credential)", re.IGNORECASE)


def _safe_details(value):
    if isinstance(value, Mapping):
        return {
            str(key): _safe_details(item)
            for key, item in value.items()
            if not SENSITIVE_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_details(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def log_action(user, action, entity, entity_id, details):
    """Stage an audit event in the caller's current DB transaction.

    Callers commit the event together with the operation being audited. Secrets
    are removed from details defensively and should never be passed by callers.
    """
    log = AuditLog(
        actor_user_id=user.id if user is not None else None,
        action=action,
        entity=entity,
        entity_id=entity_id,
        details=_safe_details(details or {}),
        ip_address=request.remote_addr if has_request_context() else None,
    )
    db.session.add(log)
    return log
