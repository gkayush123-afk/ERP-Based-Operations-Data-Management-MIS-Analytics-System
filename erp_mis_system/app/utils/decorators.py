from functools import wraps

from flask import abort
from flask_login import current_user, login_required


VALID_ROLES = {"admin", "manager", "data_entry"}


def roles_required(*roles):
    """Require an authenticated user whose role is one of the specified roles."""
    if not roles:
        raise ValueError("roles_required must be given at least one role.")
    invalid_roles = set(roles) - VALID_ROLES
    if invalid_roles:
        raise ValueError(f"Unknown role(s): {', '.join(sorted(invalid_roles))}")

    def decorator(view_function):
        @wraps(view_function)
        @login_required
        def wrapped_view(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator
