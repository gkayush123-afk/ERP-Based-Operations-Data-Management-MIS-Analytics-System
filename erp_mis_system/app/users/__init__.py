from flask import Blueprint

users_bp = Blueprint("users", __name__, url_prefix="/admin/users")

from . import routes  # noqa: E402,F401
