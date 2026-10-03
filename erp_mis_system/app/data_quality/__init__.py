from flask import Blueprint

data_quality_bp = Blueprint("data_quality", __name__, url_prefix="/data-quality")

from . import routes  # noqa: E402,F401
