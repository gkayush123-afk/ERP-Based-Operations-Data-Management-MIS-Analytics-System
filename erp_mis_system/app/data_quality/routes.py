from datetime import date

from flask import abort, render_template, request
from flask_login import current_user

from ..extensions import db
from ..models import Department
from ..utils.decorators import roles_required
from . import data_quality_bp
from .service import build_data_quality_report, summarize_data_quality


@data_quality_bp.route("/")
@roles_required("admin", "manager")
def index():
    today = date.today()
    start_raw = request.args.get("start_date", "")
    end_raw = request.args.get("end_date", "")
    try:
        start_date = date.fromisoformat(start_raw) if start_raw else today
        end_date = date.fromisoformat(end_raw) if end_raw else today
    except ValueError:
        abort(400, description="Enter valid start and end dates.")
    if start_date > end_date:
        abort(400, description="Start date must not be after end date.")

    issue_filter = request.args.get("issue", "")
    if issue_filter not in {"", "missing", "duplicates", "invalid"}:
        issue_filter = ""

    department_id = request.args.get("department_id", type=int)
    if current_user.role == "manager":
        if current_user.department_id is None:
            abort(403, description="A manager must be assigned to a department.")
        department_id = current_user.department_id

    report = build_data_quality_report(
        db.session,
        start_date=start_date,
        end_date=end_date,
        department_id=department_id,
        today=today,
    )
    summary = summarize_data_quality(report)
    department_query = db.select(Department).where(Department.is_active.is_(True))
    if current_user.role == "manager":
        department_query = department_query.where(Department.id == current_user.department_id)
    departments = db.session.scalars(department_query.order_by(Department.name)).all()

    return render_template(
        "data_quality/index.html",
        report=report,
        summary=summary,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        department_id=department_id,
        departments=departments,
        issue_filter=issue_filter,
        active_nav="verification",
    )
