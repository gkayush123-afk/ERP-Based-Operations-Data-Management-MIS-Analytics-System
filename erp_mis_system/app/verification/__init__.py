from flask import Blueprint

verification_bp = Blueprint("verification", __name__, url_prefix="/verification")

from . import routes  # noqa: E402,F401
