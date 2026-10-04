from flask import abort, redirect, render_template, url_for
from flask_login import current_user, login_required
from datetime import date

from ..extensions import db
from ..models import User
from ..utils.decorators import roles_required
from ..data_quality.service import build_data_quality_report, summarize_data_quality
from ..reports.service import build_dashboard_metrics
from . import main_bp


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("public/index.html")


@main_bp.route("/dashboard")
@login_required
def dashboard():
    if current_user.role != "admin" and current_user.department_id is None:
        abort(403, description="Your account must be assigned to a department.")
    pending_users = []
    data_quality_count = None
    if current_user.role == "admin":
        pending_users = db.session.scalars(
            db.select(User)
            .where(User.status == "pending")
            .order_by(User.created_at.asc())
        ).all()
    if current_user.role == "admin" or current_user.department_id is not None:
        today = date.today()
        report = build_data_quality_report(
            db.session,
            start_date=today,
            end_date=today,
            department_id=current_user.department_id if current_user.role != "admin" else None,
            today=today,
        )
        data_quality_count = summarize_data_quality(report)["total"]
    metrics = build_dashboard_metrics(
        db.session,
        department_id=current_user.department_id if current_user.role != "admin" else None,
    )
    return render_template(
        "dashboard.html",
        pending_users=pending_users,
        data_quality_count=data_quality_count,
        metrics=metrics,
        today=date.today().isoformat(),
        active_nav="dashboard",
    )


@main_bp.route("/forbidden")
def forbidden():
    abort(403)


@main_bp.route("/workspace/records/add")
@roles_required("admin", "manager", "data_entry")
def add_record():
    return render_template(
        "placeholder.html",
        title="Add a record",
        description="Record entry will be available when the data-management module is added.",
    )


@main_bp.route("/workspace/records/edit-own")
@roles_required("admin", "manager", "data_entry")
def edit_own_records():
    return render_template(
        "placeholder.html",
        title="Edit records",
        description="Record editing will be available when the data-management module is added.",
    )


@main_bp.route("/workspace/department-data")
@roles_required("admin", "manager", "data_entry")
def department_data():
    return render_template(
        "placeholder.html",
        title="Department data",
        description=(
            "This page will show data for your department after the data-management module is added."
        ),
    )


@main_bp.route("/workspace/records/verify")
@roles_required("admin", "manager")
def verify_records():
    return redirect(url_for("attendance.index", view="verification"))


@main_bp.route("/workspace/reports")
@roles_required("admin", "manager")
def reports():
    return redirect(url_for("reports.index"))


@main_bp.route("/workspace/reports/export")
@roles_required("admin", "manager")
def export_reports():
    return redirect(url_for("reports.index"))


@main_bp.route("/workspace/records/delete")
@roles_required("admin")
def delete_records():
    return render_template(
        "placeholder.html",
        title="Delete records",
        description="Record deletion controls will be available to administrators in a later module.",
    )


@main_bp.route("/workspace/users")
@roles_required("admin")
def manage_users():
    return redirect(url_for("users.index"))


@main_bp.route("/workspace/audit")
@roles_required("admin")
def audit_logs():
    return redirect(url_for("users.audit_logs"))
