from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..extensions import db
from ..models import Department, Employee, OperationRecord, User, utcnow
from ..utils.audit import log_action
from ..utils.decorators import roles_required
from . import verification_bp

PER_PAGE = 8
VERIFICATION_STATUSES = (
    ("pending", "Pending"),
    ("verified", "Verified"),
    ("rejected", "Rejected"),
    ("needs_correction", "Needs correction"),
)
MAX_BULK_IDS = 500


def parse_iso_date(value):
    """Return a date for YYYY-MM-DD input, or None for blank input."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def current_month_bounds():
    today = date.today()
    return today.replace(day=1), today


def scoped_base_query():
    """Base operation-record query with role scoping applied."""
    query = (
        db.select(OperationRecord)
        .join(Employee, Employee.id == OperationRecord.employee_id)
        .join(Department, Department.id == OperationRecord.department_id)
    )
    if current_user.role == "manager":
        if current_user.department_id is None:
            abort(403, description="Your account must be assigned to a department.")
        query = query.where(OperationRecord.department_id == current_user.department_id)
    return query


def get_record_or_404(record_id):
    record = db.session.get(OperationRecord, record_id)
    if record is None:
        abort(404)
    if (
        current_user.role == "manager"
        and record.department_id != current_user.department_id
    ):
        abort(403)
    return record


def deny_self_verification(record):
    if record.submitted_by is not None and record.submitted_by == current_user.id:
        abort(403, description="You cannot verify a record you submitted yourself.")


@verification_bp.route("/")
@roles_required("admin", "manager")
def index():
    employee_code = (request.args.get("employee_code") or "").strip()[:40]
    department_id = request.args.get("department_id", type=int)
    status_filter = request.args.get("status", "")
    record_date = parse_iso_date(request.args.get("record_date"))
    start_date = parse_iso_date(request.args.get("start_date"))
    end_date = parse_iso_date(request.args.get("end_date"))
    if start_date is None and request.args.get("start_date"):
        abort(400, description="Invalid start date.")
    if end_date is None and request.args.get("end_date"):
        abort(400, description="Invalid end date.")
    if record_date is None and request.args.get("record_date"):
        abort(400, description="Invalid date.")
    if not request.args.get("start_date") and not request.args.get("end_date"):
        start_date, end_date = current_month_bounds()
    if start_date and end_date and start_date > end_date:
        abort(400, description="Start date must not be after end date.")
    if status_filter not in {value for value, _label in VERIFICATION_STATUSES}:
        status_filter = ""
    if current_user.role == "manager" and department_id not in (
        None,
        current_user.department_id,
    ):
        abort(403)
    page = max(request.args.get("page", 1, type=int) or 1, 1)

    query = scoped_base_query()
    if record_date:
        query = query.where(OperationRecord.record_date == record_date)
    else:
        if start_date:
            query = query.where(OperationRecord.record_date >= start_date)
        if end_date:
            query = query.where(OperationRecord.record_date <= end_date)
    if employee_code:
        query = query.where(Employee.employee_code.ilike(f"%{employee_code}%"))
    if department_id:
        query = query.where(OperationRecord.department_id == department_id)
    if status_filter:
        query = query.where(OperationRecord.status == status_filter)

    summary_query = scoped_base_query().with_only_columns(
        OperationRecord.status, func.count(OperationRecord.id)
    )
    if record_date:
        summary_query = summary_query.where(OperationRecord.record_date == record_date)
    else:
        if start_date:
            summary_query = summary_query.where(OperationRecord.record_date >= start_date)
        if end_date:
            summary_query = summary_query.where(OperationRecord.record_date <= end_date)
    if employee_code:
        summary_query = summary_query.where(
            Employee.employee_code.ilike(f"%{employee_code}%")
        )
    if department_id:
        summary_query = summary_query.where(
            OperationRecord.department_id == department_id
        )
    status_counts = dict(
        db.session.execute(summary_query.group_by(OperationRecord.status)).all()
    )
    summary = {key: status_counts.get(key, 0) for key, _label in VERIFICATION_STATUSES}
    summary["total"] = sum(summary.values())

    pagination = db.paginate(
        query.order_by(OperationRecord.record_date.desc(), OperationRecord.id.desc()),
        page=page,
        per_page=PER_PAGE,
        error_out=False,
    )
    department_query = db.select(Department).where(Department.is_active.is_(True))
    if current_user.role == "manager":
        department_query = department_query.where(
            Department.id == current_user.department_id
        )
    departments = db.session.scalars(department_query.order_by(Department.name)).all()

    return render_template(
        "verification/index.html",
        active_nav="verification",
        pagination=pagination,
        summary=summary,
        departments=departments,
        employee_code=employee_code,
        department_id=department_id,
        status_filter=status_filter,
        statuses=VERIFICATION_STATUSES,
        record_date=record_date.isoformat() if record_date else "",
        start_date=start_date.isoformat() if start_date else "",
        end_date=end_date.isoformat() if end_date else "",
    )


@verification_bp.route("/<int:record_id>/approve", methods=["POST"])
@roles_required("admin", "manager")
def approve(record_id):
    record = get_record_or_404(record_id)
    deny_self_verification(record)
    if record.status != "pending":
        flash("Only pending records can be approved.", "warning")
        return redirect(url_for("verification.index"))

    record.status = "verified"
    record.verified_by = current_user.id
    record.verified_at = utcnow()
    record.rejection_reason = None
    log_action(
        current_user,
        "verification.approve",
        "operation_record",
        record.id,
        {
            "employee_id": record.employee_id,
            "record_date": record.record_date.isoformat(),
            "verified_by": current_user.username,
            "verified_at": record.verified_at.isoformat(),
        },
    )
    db.session.commit()
    flash("Record approved and verified.", "success")
    return redirect(url_for("verification.index"))


@verification_bp.route("/<int:record_id>/reject", methods=["POST"])
@roles_required("admin", "manager")
def reject(record_id):
    record = get_record_or_404(record_id)
    deny_self_verification(record)
    if record.status != "pending":
        flash("Only pending records can be rejected.", "warning")
        return redirect(url_for("verification.index"))

    reason = (request.form.get("rejection_reason") or "").strip()
    if not reason or len(reason) > 500:
        flash("A rejection reason of 1 to 500 characters is required.", "danger")
        return redirect(url_for("verification.index"))

    record.status = "rejected"
    record.verified_by = current_user.id
    record.verified_at = utcnow()
    record.rejection_reason = reason
    log_action(
        current_user,
        "verification.reject",
        "operation_record",
        record.id,
        {
            "employee_id": record.employee_id,
            "record_date": record.record_date.isoformat(),
            "verified_by": current_user.username,
            "verified_at": record.verified_at.isoformat(),
            "reason": reason,
        },
    )
    db.session.commit()
    flash("Record rejected.", "success")
    return redirect(url_for("verification.index"))


@verification_bp.route("/<int:record_id>/request-correction", methods=["POST"])
@roles_required("admin", "manager")
def request_correction(record_id):
    record = get_record_or_404(record_id)
    deny_self_verification(record)
    if record.status != "pending":
        flash("Only pending records can be sent back for correction.", "warning")
        return redirect(url_for("verification.index"))

    note = (request.form.get("correction_note") or "").strip()
    if not note or len(note) > 500:
        flash("A correction note of 1 to 500 characters is required.", "danger")
        return redirect(url_for("verification.index"))

    record.status = "needs_correction"
    record.correction_note = note
    log_action(
        current_user,
        "verification.request_correction",
        "operation_record",
        record.id,
        {
            "employee_id": record.employee_id,
            "record_date": record.record_date.isoformat(),
            "requested_by": current_user.username,
            "note": note,
        },
    )
    db.session.commit()
    flash("Correction requested. The record needs fixing before approval.", "success")
    return redirect(url_for("verification.index"))


@verification_bp.route("/<int:record_id>/edit", methods=["GET", "POST"])
@roles_required("admin", "manager")
def edit(record_id):
    record = get_record_or_404(record_id)
    deny_self_verification(record)
    if record.status != "needs_correction":
        flash("Only records sent back for correction can be edited.", "warning")
        return redirect(url_for("verification.index"))

    if request.method == "POST":
        task_operation = (request.form.get("task_operation") or "").strip()
        remarks = (request.form.get("remarks") or "").strip() or None
        try:
            total_records = int(request.form.get("total_records", ""))
            completed = int(request.form.get("completed", ""))
            pending = int(request.form.get("pending", ""))
        except (TypeError, ValueError):
            flash("Total, completed and pending must be whole numbers.", "danger")
            return render_template("verification/edit.html", active_nav="verification", record=record)
        errors = []
        if not task_operation or len(task_operation) > 200:
            errors.append("Task / operation is required (max 200 characters).")
        if min(total_records, completed, pending) < 0:
            errors.append("Total, completed and pending cannot be negative.")
        if completed + pending != total_records:
            errors.append("Completed plus pending must equal the total records.")
        if remarks is not None and len(remarks) > 1000:
            errors.append("Remarks must be at most 1000 characters.")
        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("verification/edit.html", active_nav="verification", record=record)

        before = {
            "task_operation": record.task_operation,
            "total_records": record.total_records,
            "completed": record.completed,
            "pending": record.pending,
            "remarks": record.remarks,
        }
        record.task_operation = task_operation
        record.total_records = total_records
        record.completed = completed
        record.pending = pending
        record.remarks = remarks
        record.status = "pending"
        record.correction_note = None
        log_action(
            current_user,
            "verification.edit",
            "operation_record",
            record.id,
            {
                "before": before,
                "after": {
                    "task_operation": record.task_operation,
                    "total_records": record.total_records,
                    "completed": record.completed,
                    "pending": record.pending,
                },
                "corrected_by": current_user.username,
            },
        )
        db.session.commit()
        flash("Corrections saved. The record is pending review again.", "success")
        return redirect(url_for("verification.index"))

    return render_template("verification/edit.html", active_nav="verification", record=record)


@verification_bp.route("/bulk", methods=["POST"])
@roles_required("admin", "manager")
def bulk():
    action = (request.form.get("action") or "").strip().lower()
    if action not in {"approve", "reject"}:
        abort(400, description="Bulk action must be approve or reject.")
    raw_ids = request.form.getlist("record_ids")
    if not raw_ids or len(raw_ids) > MAX_BULK_IDS:
        flash("Select between 1 and 500 pending records.", "warning")
        return redirect(url_for("verification.index"))
    try:
        record_ids = {int(value) for value in raw_ids}
    except ValueError:
        abort(400, description="Invalid record identifiers.")
    if len(record_ids) != len(raw_ids):
        abort(400, description="Duplicate record identifiers are not allowed.")

    reason = (request.form.get("rejection_reason") or "").strip()
    if action == "reject" and (not reason or len(reason) > 500):
        flash("A rejection reason of 1 to 500 characters is required.", "danger")
        return redirect(url_for("verification.index"))

    query = scoped_base_query().where(
        OperationRecord.id.in_(record_ids),
        OperationRecord.status == "pending",
        Employee.deleted_at.is_(None),
    )
    records = db.session.scalars(query.with_for_update()).all()
    if len(records) != len(record_ids):
        abort(403, description="All selected records must be pending and within your authorized scope.")
    for record in records:
        deny_self_verification(record)

    now = utcnow()
    for record in records:
        record.status = "verified" if action == "approve" else "rejected"
        record.verified_by = current_user.id
        record.verified_at = now
        record.rejection_reason = reason if action == "reject" else None
        details = {
            "employee_id": record.employee_id,
            "record_date": record.record_date.isoformat(),
            "verified_by": current_user.username,
            "verified_at": now.isoformat(),
            "bulk": True,
        }
        if action == "reject":
            details["reason"] = reason
        log_action(
            current_user,
            f"verification.{'approve' if action == 'approve' else 'reject'}",
            "operation_record",
            record.id,
            details,
        )
    db.session.commit()
    flash(
        f"{len(records)} record(s) {'approved' if action == 'approve' else 'rejected'}.",
        "success",
    )
    return redirect(url_for("verification.index"))


def seed_verification_records():
    """Insert sample operation records for local development.

    Never called automatically. Returns (count, message).
    """
    from datetime import timedelta

    db.create_all()
    if db.session.scalar(db.select(func.count(OperationRecord.id))):
        return 0, "operation_records already contains data; nothing seeded."

    department = db.session.scalar(
        db.select(Department)
        .where(Department.is_active.is_(True))
        .order_by(Department.name)
    )
    if department is None:
        department = Department(name="Operations", is_active=True)
        db.session.add(department)
        db.session.flush()

    submitter = db.session.scalar(
        db.select(User).where(User.status == "active").order_by(User.id)
    )
    if submitter is None:
        return 0, "Seed an active user (e.g. run seed.py) before seeding verification data."
    verifier = db.session.scalar(
        db.select(User)
        .where(User.status == "active", User.role.in_(["admin", "manager"]))
        .order_by(User.id)
    )

    employees = db.session.scalars(
        db.select(Employee)
        .where(Employee.deleted_at.is_(None), Employee.status == "active")
        .order_by(Employee.employee_code)
        .limit(4)
    ).all()
    if not employees:
        today = date.today()
        for index, (code, name) in enumerate(
            [
                ("EMP-0001", "Aarav Sharma"),
                ("EMP-0002", "Aditi Patel"),
                ("EMP-0003", "Ananya Rao"),
            ]
        ):
            employees.append(
                Employee(
                    employee_code=code,
                    full_name=name,
                    email=f"{code.lower()}@example.com",
                    designation="Associate",
                    department_id=department.id,
                    joining_date=today - timedelta(days=90 + index),
                    status="active",
                    created_by=submitter.id,
                )
            )
        db.session.add_all(employees)
        db.session.flush()

    tasks = [
        "Order processing",
        "Inventory audit",
        "Customer onboarding",
        "Invoice reconciliation",
        "Dispatch scheduling",
        "Quality inspection",
    ]
    sample_remarks = [
        "Cross-checked against the source register.",
        None,
        "Includes carry-forward from the previous day.",
        None,
        "Figures confirmed with the department head.",
        None,
    ]
    month_start, today = current_month_bounds()
    day_span = max((today - month_start).days + 1, 1)
    planned = [
        ("pending", None),
        ("pending", None),
        ("pending", None),
        ("pending", None),
        ("pending", None),
        ("pending", None),
        ("pending", None),
        ("needs_correction", "Recheck the completed count against the logbook."),
        ("verified", None),
        ("verified", None),
        ("rejected", "Totals do not match the source register."),
        ("rejected", "Duplicate submission for this date."),
    ]
    records = []
    for offset, (status, reason) in enumerate(planned):
        employee = employees[offset % len(employees)]
        total = 40 + (offset * 7) % 60
        completed = total - (offset % 5)
        records.append(
            OperationRecord(
                record_date=month_start + timedelta(days=offset % day_span),
                employee_id=employee.id,
                department_id=employee.department_id,
                task_operation=tasks[offset % len(tasks)],
                total_records=total,
                completed=completed,
                pending=total - completed,
                status=status,
                submitted_by=submitter.id,
                verified_by=verifier.id if status != "pending" and verifier else None,
                verified_at=utcnow() if status != "pending" and verifier else None,
                rejection_reason=reason if status == "rejected" else None,
                remarks=sample_remarks[offset % len(sample_remarks)],
                correction_note=reason if status == "needs_correction" else None,
            )
        )
    db.session.add_all(records)
    db.session.commit()
    return len(records), f"Seeded {len(records)} sample operation record(s)."
