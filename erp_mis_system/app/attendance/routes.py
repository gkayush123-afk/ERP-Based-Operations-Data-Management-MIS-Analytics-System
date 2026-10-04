from datetime import date, datetime, time, timedelta
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from flask import abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from ..extensions import db
from ..models import AttendanceRecord, Department, Employee, utcnow
from ..utils.audit import log_action
from ..utils.decorators import roles_required
from . import attendance_bp
from .forms import ATTENDANCE_STATUSES, VERIFICATION_STATUSES, AttendanceForm

PER_PAGE = 10
IMPORT_COLUMNS = ("employee_code", "date", "status", "in_time", "out_time", "remarks")
DAY_STATS_STATUSES = ("present", "absent", "late", "leave", "half_day")
EDITABLE_DAYS = 7


def scoped_employees(active_only=False):
    query = db.select(Employee).where(Employee.deleted_at.is_(None))
    if active_only:
        query = query.where(Employee.status == "active")
    if current_user.role in {"manager", "data_entry"}:
        query = query.where(Employee.department_id == current_user.department_id)
    return db.session.scalars(query.order_by(Employee.employee_code)).all()


def set_employee_choices(form, include_employee=None):
    employees = scoped_employees(active_only=True)
    if include_employee is not None and all(item.id != include_employee.id for item in employees):
        employees.append(include_employee)
        employees.sort(key=lambda item: item.employee_code.casefold())
    form.employee_id.choices = [
        (employee.id, f"{employee.employee_code} — {employee.full_name}")
        for employee in employees
    ]
    return employees


def attendance_scope_query():
    query = (
        db.select(AttendanceRecord)
        .join(Employee)
        .join(Department)
    )
    if current_user.role in {"manager", "data_entry"}:
        query = query.where(Employee.department_id == current_user.department_id)
    return query


def build_day_stats(start_date, end_date, department_id=None, employee_id=None, search=""):
    """Aggregate status counts over the selected range.

    Attendance % follows the MIS convention: present records divided by
    scoped active employees.
    """
    scoped = attendance_scope_query()
    if start_date:
        scoped = scoped.where(AttendanceRecord.attendance_date >= start_date)
    if end_date:
        scoped = scoped.where(AttendanceRecord.attendance_date <= end_date)
    if department_id:
        scoped = scoped.where(Employee.department_id == department_id)
    if employee_id:
        scoped = scoped.where(AttendanceRecord.employee_id == employee_id)
    if search:
        like = f"%{search.lower()}%"
        scoped = scoped.where(
            or_(
                func.lower(Employee.employee_code).like(like),
                func.lower(Employee.full_name).like(like),
            )
        )
    counts = dict(
        db.session.execute(
            scoped.with_only_columns(
                AttendanceRecord.status, func.count(AttendanceRecord.id)
            ).group_by(AttendanceRecord.status)
        ).all()
    )
    stats = {status: int(counts.get(status, 0)) for status in DAY_STATS_STATUSES}
    employee_query = db.select(func.count(Employee.id)).where(
        Employee.deleted_at.is_(None),
        Employee.status == "active",
    )
    if end_date:
        employee_query = employee_query.where(Employee.joining_date <= end_date)
    if current_user.role in {"manager", "data_entry"}:
        employee_query = employee_query.where(
            Employee.department_id == current_user.department_id
        )
    elif department_id:
        employee_query = employee_query.where(Employee.department_id == department_id)
    stats["total_employees"] = int(db.session.scalar(employee_query) or 0)
    stats["attendance_percentage"] = (
        round(stats["present"] * 100 / stats["total_employees"], 1)
        if stats["total_employees"]
        else 0.0
    )
    return stats


def resolve_mark_department(raw_department_id):
    """Resolve the department for the mark-attendance page with role scoping."""
    if current_user.role == "data_entry":
        if current_user.department_id is None:
            abort(403, description="Your account must be assigned to a department.")
        if raw_department_id not in (None, current_user.department_id):
            abort(403)
        return db.get_or_404(Department, current_user.department_id)
    if raw_department_id:
        department = db.session.get(Department, raw_department_id)
        if department is None or not department.is_active:
            abort(404, description="Department not found.")
        if (
            current_user.role == "manager"
            and department.id != current_user.department_id
        ):
            abort(403)
        return department
    if current_user.role == "manager":
        if current_user.department_id is None:
            abort(403, description="Your account must be assigned to a department.")
        return db.get_or_404(Department, current_user.department_id)
    return db.session.scalar(
        db.select(Department)
        .where(Department.is_active.is_(True))
        .order_by(Department.name)
    )


def mark_employees(department):
    if department is None:
        return []
    return db.session.scalars(
        db.select(Employee)
        .where(
            Employee.deleted_at.is_(None),
            Employee.status == "active",
            Employee.department_id == department.id,
        )
        .order_by(Employee.employee_code)
    ).all()


def editable_cutoff():
    return date.today() - timedelta(days=EDITABLE_DAYS)


def parse_workbook_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def parse_workbook_time(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    try:
        return time.fromisoformat(str(value).strip()).replace(second=0, microsecond=0)
    except (TypeError, ValueError):
        return None


@attendance_bp.route("/")
@roles_required("admin", "manager", "data_entry")
def index():
    single_date = parse_workbook_date(request.args.get("attendance_date"))
    start_date = parse_workbook_date(request.args.get("start_date"))
    end_date = parse_workbook_date(request.args.get("end_date"))
    verification_page = request.args.get("view") == "verification"
    if verification_page and current_user.role not in {"admin", "manager"}:
        abort(403)
    if single_date is None and request.args.get("attendance_date"):
        abort(400, description="Invalid attendance date.")
    if single_date is not None:
        start_date = end_date = single_date
    elif verification_page and not request.args.get("start_date") and not request.args.get("end_date"):
        today = date.today()
        start_date = today.replace(day=1)
        end_date = today
    elif not verification_page and not request.args.get("start_date") and not request.args.get("end_date"):
        start_date = end_date = date.today()
    department_id = request.args.get("department_id", type=int)
    employee_id = request.args.get("employee_id", type=int)
    status_filter = request.args.get("status", "")
    verification_filter = request.args.get("verification_status", "")
    search = (request.args.get("q") or "").strip()[:80]
    page = max(request.args.get("page", 1, type=int) or 1, 1)

    if start_date is None and request.args.get("start_date"):
        abort(400, description="Invalid start date.")
    if end_date is None and request.args.get("end_date"):
        abort(400, description="Invalid end date.")
    if start_date and end_date and start_date > end_date:
        abort(400, description="Start date must not be after end date.")
    if status_filter not in {value for value, _label in ATTENDANCE_STATUSES}:
        status_filter = ""
    if verification_filter not in {value for value, _label in VERIFICATION_STATUSES}:
        verification_filter = ""

    query = attendance_scope_query()
    if start_date:
        query = query.where(AttendanceRecord.attendance_date >= start_date)
    if end_date:
        query = query.where(AttendanceRecord.attendance_date <= end_date)
    if status_filter:
        query = query.where(AttendanceRecord.status == status_filter)
    if verification_filter:
        query = query.where(AttendanceRecord.verification_status == verification_filter)
    if employee_id:
        query = query.where(AttendanceRecord.employee_id == employee_id)
    if search:
        like = f"%{search.lower()}%"
        query = query.where(
            or_(
                func.lower(Employee.employee_code).like(like),
                func.lower(Employee.full_name).like(like),
            )
        )
    if department_id:
        if current_user.role == "data_entry":
            if department_id != current_user.department_id:
                abort(403)
        if current_user.role == "manager" and department_id != current_user.department_id:
            abort(403)
        query = query.where(Employee.department_id == department_id)

    if verification_page:
        summary_query = (
            db.select(AttendanceRecord.verification_status, func.count(AttendanceRecord.id))
            .join(Employee)
            .join(Department)
        )
        if current_user.role == "manager":
            summary_query = summary_query.where(
                Employee.department_id == current_user.department_id
            )
        if start_date:
            summary_query = summary_query.where(
                AttendanceRecord.attendance_date >= start_date
            )
        if end_date:
            summary_query = summary_query.where(AttendanceRecord.attendance_date <= end_date)
        if status_filter:
            summary_query = summary_query.where(AttendanceRecord.status == status_filter)
        if employee_id:
            summary_query = summary_query.where(
                AttendanceRecord.employee_id == employee_id
            )
        if department_id:
            summary_query = summary_query.where(Employee.department_id == department_id)
        verification_counts = dict(
            db.session.execute(
                summary_query.group_by(AttendanceRecord.verification_status)
            ).all()
        )
        verification_summary = {
            key: verification_counts.get(key, 0)
            for key in ("pending", "verified", "rejected")
        }
        verification_summary["total"] = sum(verification_summary.values())

    pagination = db.paginate(
        query.order_by(AttendanceRecord.attendance_date.desc(), AttendanceRecord.id.desc()),
        page=page,
        per_page=PER_PAGE,
        error_out=False,
    )
    day_stats = None
    if not verification_page:
        day_stats = build_day_stats(
            start_date,
            end_date,
            department_id=department_id,
            employee_id=employee_id,
            search=search,
        )
    department_query = db.select(Department).where(Department.is_active.is_(True))
    if current_user.role in {"manager", "data_entry"}:
        department_query = department_query.where(Department.id == current_user.department_id)
    departments = db.session.scalars(department_query.order_by(Department.name)).all()
    employee_query = db.select(Employee).where(Employee.deleted_at.is_(None))
    if current_user.role in {"manager", "data_entry"}:
        employee_query = employee_query.where(Employee.department_id == current_user.department_id)
    if department_id:
        employee_query = employee_query.where(Employee.department_id == department_id)
    employees = db.session.scalars(employee_query.order_by(Employee.employee_code)).all()

    template = "attendance/verification.html" if verification_page else "attendance/index.html"
    return render_template(
        template,
        pagination=pagination,
        departments=departments,
        employees=employees,
        start_date=start_date.isoformat() if start_date else "",
        end_date=end_date.isoformat() if end_date else "",
        attendance_date=single_date.isoformat() if single_date else "",
        department_id=department_id,
        employee_id=employee_id,
        search=search,
        status_filter=status_filter,
        verification_filter=verification_filter,
        statuses=ATTENDANCE_STATUSES,
        verification_statuses=VERIFICATION_STATUSES,
        can_verify=current_user.role in {"admin", "manager"},
        verification_page=verification_page,
        verification_summary=verification_summary if verification_page else None,
        day_stats=day_stats,
        active_nav="verification" if verification_page else "attendance",
    )


@attendance_bp.route("/create", methods=["GET", "POST"])
@roles_required("admin", "manager", "data_entry")
def create():
    form = AttendanceForm()
    employees = set_employee_choices(form)
    if request.method == "GET":
        requested_employee_id = request.args.get("employee_id", type=int)
        requested_date = parse_workbook_date(request.args.get("attendance_date"))
        if requested_employee_id and any(
            employee.id == requested_employee_id for employee in employees
        ):
            form.employee_id.data = requested_employee_id
        if requested_date:
            form.attendance_date.data = requested_date
    if not employees:
        flash("No active employees are available in your department.", "warning")
        return redirect(url_for("attendance.index"))

    if form.validate_on_submit():
        employee = db.session.get(Employee, form.employee_id.data)
        if (
            employee is None
            or employee.deleted_at is not None
            or employee.status != "active"
        ):
            abort(400, description="Select an active employee.")
        if current_user.role in {"manager", "data_entry"} and employee.department_id != current_user.department_id:
            abort(403)
        if db.session.scalar(
            db.select(AttendanceRecord.id).where(
                AttendanceRecord.employee_id == employee.id,
                AttendanceRecord.attendance_date == form.attendance_date.data,
            )
        ):
            flash("Attendance has already been entered for this employee and date.", "danger")
            return render_template("attendance/form.html", form=form, record=None, employees=employees, active_nav="attendance")

        record = AttendanceRecord(
            employee_id=employee.id,
            attendance_date=form.attendance_date.data,
            status=form.status.data,
            in_time=form.in_time.data,
            out_time=form.out_time.data,
            remarks=(form.remarks.data or "").strip() or None,
            verification_status="pending",
            created_by=current_user.id,
        )
        db.session.add(record)
        try:
            db.session.flush()
            log_action(
                current_user,
                "attendance.create",
                "attendance_record",
                record.id,
                {
                    "employee_id": record.employee_id,
                    "attendance_date": record.attendance_date.isoformat(),
                    "status": record.status,
                },
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Attendance has already been entered for this employee and date.", "danger")
            return render_template("attendance/form.html", form=form, record=None, employees=employees, active_nav="attendance")
        flash("Attendance record submitted for verification.", "success")
        return redirect(url_for("attendance.index"))

    if request.method == "POST":
        for errors in form.errors.values():
            for message in errors:
                flash(message, "danger")
    return render_template("attendance/form.html", form=form, record=None, employees=employees, active_nav="attendance")


@attendance_bp.route("/<int:record_id>/edit", methods=["GET", "POST"])
@roles_required("admin", "manager", "data_entry")
def edit(record_id):
    record = db.get_or_404(AttendanceRecord, record_id)
    employee = db.get_or_404(Employee, record.employee_id)
    if current_user.role == "data_entry" and record.verification_status != "pending":
        abort(403, description="Only pending attendance can be edited.")
    if current_user.role in {"manager", "data_entry"} and employee.department_id != current_user.department_id:
        abort(403)
    form = AttendanceForm(obj=record)
    employees = set_employee_choices(form, employee)
    if form.validate_on_submit():
        selected_employee = db.session.get(Employee, form.employee_id.data)
        if (
            selected_employee is None
            or selected_employee.deleted_at is not None
            or selected_employee.status != "active"
        ):
            abort(400, description="Select an active employee.")
        if current_user.role in {"manager", "data_entry"} and selected_employee.department_id != current_user.department_id:
            abort(403)
        duplicate = db.session.scalar(
            db.select(AttendanceRecord.id).where(
                AttendanceRecord.employee_id == selected_employee.id,
                AttendanceRecord.attendance_date == form.attendance_date.data,
                AttendanceRecord.id != record.id,
            )
        )
        if duplicate:
            flash("Attendance has already been entered for this employee and date.", "danger")
            return render_template("attendance/form.html", form=form, record=record, employees=employees, active_nav="attendance")

        before = {
            "employee_id": record.employee_id,
            "attendance_date": record.attendance_date.isoformat(),
            "status": record.status,
            "in_time": record.in_time.isoformat() if record.in_time else None,
            "out_time": record.out_time.isoformat() if record.out_time else None,
            "remarks": record.remarks,
        }
        record.employee_id = selected_employee.id
        record.attendance_date = form.attendance_date.data
        record.status = form.status.data
        record.in_time = form.in_time.data
        record.out_time = form.out_time.data
        record.remarks = (form.remarks.data or "").strip() or None
        log_action(
            current_user,
            "attendance.update",
            "attendance_record",
            record.id,
            {
                "before": before,
                "after": {
                    "employee_id": record.employee_id,
                    "attendance_date": record.attendance_date.isoformat(),
                    "status": record.status,
                },
            },
        )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Attendance has already been entered for this employee and date.", "danger")
            return render_template("attendance/form.html", form=form, record=record, employees=employees, active_nav="attendance")
        flash("Pending attendance record was updated.", "success")
        return redirect(url_for("attendance.index"))

    if request.method == "POST":
        for errors in form.errors.values():
            for message in errors:
                flash(message, "danger")
    return render_template("attendance/form.html", form=form, record=record, employees=employees, active_nav="attendance")


@attendance_bp.route("/<int:record_id>/verify", methods=["POST"])
@roles_required("admin", "manager")
def verify(record_id):
    record = db.get_or_404(AttendanceRecord, record_id)
    employee = db.get_or_404(Employee, record.employee_id)
    if current_user.role == "manager" and employee.department_id != current_user.department_id:
        abort(403)
    if record.verification_status != "pending":
        flash("Only pending attendance can be verified.", "warning")
        return redirect(
            url_for("attendance.index", view="verification")
            if request.form.get("view") == "verification"
            else url_for("attendance.index")
        )

    record.verification_status = "verified"
    record.verified_by = current_user.id
    record.verified_at = utcnow()
    record.rejection_reason = None
    log_action(
        current_user,
        "attendance.verify",
        "attendance_record",
        record.id,
        {"employee_id": record.employee_id, "attendance_date": record.attendance_date.isoformat()},
    )
    db.session.commit()
    flash("Attendance record verified.", "success")
    return redirect(
        url_for("attendance.index", view="verification")
        if request.form.get("view") == "verification"
        else url_for("attendance.index")
    )


@attendance_bp.route("/<int:record_id>/reject", methods=["POST"])
@roles_required("admin", "manager")
def reject(record_id):
    record = db.get_or_404(AttendanceRecord, record_id)
    employee = db.get_or_404(Employee, record.employee_id)
    if current_user.role == "manager" and employee.department_id != current_user.department_id:
        abort(403)
    if record.verification_status != "pending":
        flash("Only pending attendance can be rejected.", "warning")
        return redirect(
            url_for("attendance.index", view="verification")
            if request.form.get("view") == "verification"
            else url_for("attendance.index")
        )

    reason = request.form.get("rejection_reason", "").strip()
    if not reason or len(reason) > 500:
        flash("A rejection reason of 1 to 500 characters is required.", "danger")
        return redirect(
            url_for("attendance.index", view="verification")
            if request.form.get("view") == "verification"
            else url_for("attendance.index")
        )

    record.verification_status = "rejected"
    record.verified_by = current_user.id
    record.verified_at = utcnow()
    record.rejection_reason = reason
    log_action(
        current_user,
        "attendance.reject",
        "attendance_record",
        record.id,
        {
            "employee_id": record.employee_id,
            "attendance_date": record.attendance_date.isoformat(),
            "reason": reason,
        },
    )
    db.session.commit()
    flash("Attendance record rejected.", "success")
    return redirect(
        url_for("attendance.index", view="verification")
        if request.form.get("view") == "verification"
        else url_for("attendance.index")
    )


@attendance_bp.route("/bulk-verify", methods=["POST"])
@roles_required("admin", "manager")
def bulk_verify():
    raw_ids = request.form.getlist("record_ids")
    if not raw_ids or len(raw_ids) > 500:
        flash("Select between 1 and 500 pending records to verify.", "warning")
        return redirect(
            url_for("attendance.index", view="verification")
            if request.form.get("view") == "verification"
            else url_for("attendance.index")
        )
    try:
        record_ids = {int(value) for value in raw_ids}
    except ValueError:
        abort(400)
    if len(record_ids) != len(raw_ids):
        abort(400, description="Duplicate attendance identifiers are not allowed.")

    query = db.select(AttendanceRecord).join(Employee).where(
        AttendanceRecord.id.in_(record_ids),
        AttendanceRecord.verification_status == "pending",
        Employee.deleted_at.is_(None),
    )
    if current_user.role == "manager":
        query = query.where(Employee.department_id == current_user.department_id)
    records = db.session.scalars(query.with_for_update()).all()
    if len(records) != len(record_ids):
        abort(403, description="All selected records must be pending and within your authorized scope.")

    now = utcnow()
    for record in records:
        record.verification_status = "verified"
        record.verified_by = current_user.id
        record.verified_at = now
        record.rejection_reason = None
        log_action(
            current_user,
            "attendance.verify",
            "attendance_record",
            record.id,
            {"employee_id": record.employee_id, "attendance_date": record.attendance_date.isoformat(), "bulk": True},
        )
    db.session.commit()
    flash(f"{len(records)} attendance record(s) verified.", "success")
    return redirect(
        url_for("attendance.index", view="verification")
        if request.form.get("view") == "verification"
        else url_for("attendance.index")
    )


@attendance_bp.route("/import", methods=["GET", "POST"])
@roles_required("admin", "manager", "data_entry")
def import_excel():
    row_results = []
    if request.method == "POST":
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            flash("Choose an .xlsx workbook.", "danger")
            return render_template("attendance/import.html", row_results=row_results, active_nav="attendance")
        if Path(upload.filename).suffix.lower() != ".xlsx":
            flash("Only .xlsx workbooks are supported.", "danger")
            return render_template("attendance/import.html", row_results=row_results, active_nav="attendance")
        try:
            workbook = load_workbook(upload, read_only=True, data_only=True)
            sheet = workbook.active
            rows = sheet.iter_rows(values_only=True)
            headers = [str(value or "").strip().lower() for value in (next(rows, None) or ())]
        except (InvalidFileException, BadZipFile, OSError, ValueError, KeyError, ParseError):
            flash("The workbook could not be read. Choose a valid .xlsx file.", "danger")
            return render_template("attendance/import.html", row_results=row_results, active_nav="attendance")
        missing = [column for column in IMPORT_COLUMNS if column not in headers]
        if missing:
            workbook.close()
            flash(f"Missing required column(s): {', '.join(missing)}.", "danger")
            return render_template("attendance/import.html", row_results=row_results, active_nav="attendance")
        indices = {column: headers.index(column) for column in IMPORT_COLUMNS}
        for row_number, values in enumerate(rows, start=2):
            if not values or not any(value not in (None, "") for value in values):
                continue
            values_by_name = {
                name: values[index] if index < len(values) else None
                for name, index in indices.items()
            }
            errors = []
            employee_code = str(values_by_name["employee_code"] or "").strip()
            employee = None
            if not employee_code:
                errors.append("employee_code is required")
            else:
                employee_query = db.select(Employee).where(
                    func.lower(Employee.employee_code) == employee_code.lower(),
                    Employee.deleted_at.is_(None),
                    Employee.status == "active",
                )
                if current_user.role in {"manager", "data_entry"}:
                    employee_query = employee_query.where(
                        Employee.department_id == current_user.department_id
                    )
                employee = db.session.scalar(employee_query)
                if employee is None:
                    errors.append("employee_code does not match an active employee")

            attendance_date = parse_workbook_date(values_by_name["date"])
            if attendance_date is None:
                errors.append("date must be a valid date (YYYY-MM-DD)")
            elif attendance_date > date.today():
                errors.append("date cannot be in the future")
            status = str(values_by_name["status"] or "").strip().lower().replace("-", "_").replace(" ", "_")
            if status not in {choice for choice, _label in ATTENDANCE_STATUSES}:
                errors.append("status must be Present, Absent, Leave, or Half-day")
            in_time = parse_workbook_time(values_by_name["in_time"])
            out_time = parse_workbook_time(values_by_name["out_time"])
            if values_by_name["in_time"] not in (None, "") and in_time is None:
                errors.append("in_time must use HH:MM")
            if values_by_name["out_time"] not in (None, "") and out_time is None:
                errors.append("out_time must use HH:MM")
            if in_time and out_time and out_time < in_time:
                errors.append("out_time cannot be earlier than in_time")
            remarks = str(values_by_name["remarks"] or "").strip()
            if len(remarks) > 1000:
                errors.append("remarks must be at most 1000 characters")
            if employee and attendance_date and db.session.scalar(
                db.select(AttendanceRecord.id).where(
                    AttendanceRecord.employee_id == employee.id,
                    AttendanceRecord.attendance_date == attendance_date,
                )
            ):
                errors.append("attendance already exists for this employee and date")

            if errors:
                row_results.append({"row": row_number, "status": "error", "message": "; ".join(errors)})
                continue

            record = AttendanceRecord(
                employee_id=employee.id,
                attendance_date=attendance_date,
                status=status,
                in_time=in_time,
                out_time=out_time,
                remarks=remarks or None,
                verification_status="pending",
                created_by=current_user.id,
            )
            db.session.add(record)
            try:
                db.session.flush()
                log_action(
                    current_user,
                    "attendance.import",
                    "attendance_record",
                    record.id,
                    {
                        "employee_id": record.employee_id,
                        "attendance_date": record.attendance_date.isoformat(),
                        "source_row": row_number,
                    },
                )
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                row_results.append(
                    {"row": row_number, "status": "error", "message": "attendance already exists for this employee and date"}
                )
            else:
                row_results.append(
                    {"row": row_number, "status": "success", "message": f"Imported {employee_code}"}
                )
        workbook.close()
        successes = sum(result["status"] == "success" for result in row_results)
        flash(f"Import complete: {successes} imported, {len(row_results) - successes} row(s) failed.", "info")
    return render_template("attendance/import.html", row_results=row_results, active_nav="attendance")


@attendance_bp.route("/mark", methods=["GET", "POST"])
@roles_required("admin", "manager", "data_entry")
def mark():
    department = resolve_mark_department(request.values.get("department_id", type=int))
    mark_date = parse_workbook_date(request.values.get("mark_date"))
    date_valid = True
    if mark_date is None:
        if request.values.get("mark_date"):
            flash("Enter a valid date.", "danger")
        date_valid = False
        mark_date = date.today()
    elif mark_date > date.today():
        flash("Attendance date cannot be in the future.", "danger")
        date_valid = False
        mark_date = date.today()
    if mark_date < editable_cutoff() and current_user.role == "data_entry":
        abort(403, description="Only managers and admins can edit past dates.")

    departments = []
    if current_user.role == "admin":
        departments = db.session.scalars(
            db.select(Department)
            .where(Department.is_active.is_(True))
            .order_by(Department.name)
        ).all()
    employees = mark_employees(department)
    existing = {}
    if department is not None:
        existing = {
            record.employee_id: record
            for record in db.session.scalars(
                db.select(AttendanceRecord).where(
                    AttendanceRecord.attendance_date == mark_date,
                    AttendanceRecord.employee_id.in_(
                        [employee.id for employee in employees]
                    ),
                )
            ).all()
        } if employees else {}

    submitted = None
    if request.method == "POST" and date_valid:
        raw_ids = request.form.getlist("employee_id")
        raw_statuses = request.form.getlist("status")
        raw_ins = request.form.getlist("in_time")
        raw_outs = request.form.getlist("out_time")
        raw_remarks = request.form.getlist("remarks")
        if not (len(raw_ids) == len(raw_statuses) == len(raw_ins) == len(raw_outs) == len(raw_remarks)):
            abort(400, description="Mismatched attendance rows.")
        try:
            employee_ids = [int(value) for value in raw_ids]
        except ValueError:
            abort(400, description="Invalid employee identifiers.")
        if len(set(employee_ids)) != len(employee_ids):
            abort(400, description="Duplicate employee rows are not allowed.")
        allowed_ids = {employee.id for employee in employees}
        if any(employee_id not in allowed_ids for employee_id in employee_ids):
            abort(403, description="All marked employees must belong to the selected department.")

        submitted = []
        errors = []
        planned = []
        for position, employee_id in enumerate(employee_ids):
            employee = next(item for item in employees if item.id == employee_id)
            status = (raw_statuses[position] or "").strip().lower()
            in_time = parse_workbook_time(raw_ins[position])
            out_time = parse_workbook_time(raw_outs[position])
            remarks = (raw_remarks[position] or "").strip() or None
            label = f"{employee.employee_code}"
            if status not in {value for value, _label in ATTENDANCE_STATUSES}:
                errors.append(f"{label}: choose a valid status.")
                continue
            if raw_ins[position] and in_time is None:
                errors.append(f"{label}: in time must use HH:MM.")
                continue
            if raw_outs[position] and out_time is None:
                errors.append(f"{label}: out time must use HH:MM.")
                continue
            if in_time and out_time and out_time < in_time:
                errors.append(f"{label}: out time cannot be earlier than in time.")
                continue
            if remarks is not None and len(remarks) > 1000:
                errors.append(f"{label}: remarks must be at most 1000 characters.")
                continue
            record = existing.get(employee_id)
            if (
                record is not None
                and current_user.role == "data_entry"
                and record.verification_status != "pending"
            ):
                errors.append(f"{label}: only pending attendance can be edited.")
                continue
            planned.append((employee, record, status, in_time, out_time, remarks))
            submitted.append(
                {
                    "employee_id": employee_id,
                    "status": status,
                    "in_time": raw_ins[position],
                    "out_time": raw_outs[position],
                    "remarks": raw_remarks[position],
                }
            )

        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            created = updated = 0
            for employee, record, status, in_time, out_time, remarks in planned:
                if record is None:
                    record = AttendanceRecord(
                        employee_id=employee.id,
                        attendance_date=mark_date,
                        status=status,
                        in_time=in_time,
                        out_time=out_time,
                        remarks=remarks,
                        verification_status="pending",
                        created_by=current_user.id,
                    )
                    db.session.add(record)
                    db.session.flush()
                    log_action(
                        current_user,
                        "attendance.create",
                        "attendance_record",
                        record.id,
                        {
                            "employee_id": record.employee_id,
                            "attendance_date": record.attendance_date.isoformat(),
                            "status": record.status,
                        },
                    )
                    created += 1
                else:
                    before = {
                        "status": record.status,
                        "in_time": record.in_time.isoformat() if record.in_time else None,
                        "out_time": record.out_time.isoformat() if record.out_time else None,
                    }
                    record.status = status
                    record.in_time = in_time
                    record.out_time = out_time
                    record.remarks = remarks
                    if record.verification_status == "rejected":
                        record.verification_status = "pending"
                        record.rejection_reason = None
                        record.verified_by = None
                        record.verified_at = None
                    log_action(
                        current_user,
                        "attendance.update",
                        "attendance_record",
                        record.id,
                        {
                            "before": before,
                            "after": {
                                "status": record.status,
                                "in_time": record.in_time.isoformat() if record.in_time else None,
                                "out_time": record.out_time.isoformat() if record.out_time else None,
                            },
                        },
                    )
                    updated += 1
            db.session.commit()
            flash(f"Attendance marked for {mark_date.isoformat()}: {created} created, {updated} updated.", "success")
            return redirect(
                url_for(
                    "attendance.mark",
                    mark_date=mark_date.isoformat(),
                    department_id=department.id if department else None,
                )
            )

    return render_template(
        "attendance/mark.html",
        active_nav="attendance",
        department=department,
        departments=departments,
        employees=employees,
        existing=existing,
        mark_date=mark_date.isoformat(),
        editable_cutoff=editable_cutoff().isoformat(),
        statuses=ATTENDANCE_STATUSES,
        submitted={item["employee_id"]: item for item in (submitted or [])},
    )


@attendance_bp.route("/monthly")
@roles_required("admin", "manager", "data_entry")
def monthly():
    month_raw = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        month_start = date.fromisoformat(f"{month_raw}-01")
    except ValueError:
        abort(400, description="Enter a valid month (YYYY-MM).")
    from calendar import monthrange

    month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1])
    department_id = request.args.get("department_id", type=int)
    if current_user.role in {"manager", "data_entry"}:
        if current_user.department_id is None:
            abort(403, description="Your account must be assigned to a department.")
        if department_id not in (None, current_user.department_id):
            abort(403)
        department_id = current_user.department_id

    employee_query = db.select(Employee).where(
        Employee.deleted_at.is_(None),
        Employee.status == "active",
    )
    if department_id:
        employee_query = employee_query.where(Employee.department_id == department_id)
    employees = db.session.scalars(employee_query.order_by(Employee.employee_code)).all()

    counts = db.session.execute(
        db.select(
            AttendanceRecord.employee_id,
            AttendanceRecord.status,
            func.count(AttendanceRecord.id),
        )
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .where(
            AttendanceRecord.attendance_date >= month_start,
            AttendanceRecord.attendance_date <= month_end,
            Employee.deleted_at.is_(None),
            *([Employee.department_id == department_id] if department_id else []),
        )
        .group_by(AttendanceRecord.employee_id, AttendanceRecord.status)
    ).all()
    by_employee = {}
    for employee_id, status, total in counts:
        by_employee.setdefault(employee_id, {})[status] = int(total)

    rows = []
    for employee in employees:
        tally = {status: by_employee.get(employee.id, {}).get(status, 0) for status in DAY_STATS_STATUSES}
        total = sum(tally.values())
        rows.append(
            {
                "employee": employee,
                **tally,
                "total": total,
                "attendance_percentage": round(tally["present"] * 100 / total, 1) if total else 0.0,
            }
        )
    department_query = db.select(Department).where(Department.is_active.is_(True))
    if current_user.role in {"manager", "data_entry"}:
        department_query = department_query.where(Department.id == current_user.department_id)
    departments = db.session.scalars(department_query.order_by(Department.name)).all()
    return render_template(
        "attendance/monthly.html",
        active_nav="attendance",
        rows=rows,
        departments=departments,
        department_id=department_id,
        month=month_start.strftime("%Y-%m"),
        month_label=month_start.strftime("%B %Y"),
    )


@attendance_bp.route("/employee/<int:employee_id>")
@roles_required("admin", "manager", "data_entry")
def employee_history(employee_id):
    employee = db.get_or_404(Employee, employee_id)
    if employee.deleted_at is not None:
        abort(404)
    if current_user.role in {"manager", "data_entry"}:
        if employee.department_id != current_user.department_id:
            abort(403)
    month_raw = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        month_start = date.fromisoformat(f"{month_raw}-01")
    except ValueError:
        abort(400, description="Enter a valid month (YYYY-MM).")
    from calendar import monthcalendar, monthrange

    month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1])
    records = db.session.scalars(
        db.select(AttendanceRecord).where(
            AttendanceRecord.employee_id == employee.id,
            AttendanceRecord.attendance_date >= month_start,
            AttendanceRecord.attendance_date <= month_end,
        )
    ).all()
    by_day = {record.attendance_date.day: record for record in records}
    weeks = monthcalendar(month_start.year, month_start.month)
    tally = {status: 0 for status in DAY_STATS_STATUSES}
    for record in records:
        if record.status in tally:
            tally[record.status] += 1
    total = sum(tally.values())
    return render_template(
        "attendance/history.html",
        active_nav="attendance",
        employee=employee,
        weeks=weeks,
        by_day=by_day,
        tally=tally,
        total=total,
        attendance_percentage=round(tally["present"] * 100 / total, 1) if total else 0.0,
        month=month_start.strftime("%Y-%m"),
        month_label=month_start.strftime("%B %Y"),
    )


@attendance_bp.route("/export/<file_format>")
@roles_required("admin", "manager", "data_entry")
def export(file_format):
    if file_format not in {"csv", "xlsx"}:
        abort(404)
    single_date = parse_workbook_date(request.args.get("attendance_date"))
    start_date = parse_workbook_date(request.args.get("start_date"))
    end_date = parse_workbook_date(request.args.get("end_date"))
    if single_date is not None:
        start_date = end_date = single_date
    elif start_date is None and end_date is None:
        start_date = end_date = date.today()
    if (start_date is None and request.args.get("start_date")) or (
        end_date is None and request.args.get("end_date")
    ):
        abort(400, description="Enter valid export dates.")
    if start_date and end_date and start_date > end_date:
        abort(400, description="Start date must not be after end date.")
    department_id = request.args.get("department_id", type=int)
    employee_id = request.args.get("employee_id", type=int)
    status_filter = request.args.get("status", "")
    verification_filter = request.args.get("verification_status", "")
    search = (request.args.get("q") or "").strip()[:80]
    if status_filter not in {value for value, _label in ATTENDANCE_STATUSES}:
        status_filter = ""
    if verification_filter not in {value for value, _label in VERIFICATION_STATUSES}:
        verification_filter = ""
    if department_id:
        if current_user.role in {"manager", "data_entry"}:
            if department_id != current_user.department_id:
                abort(403)
    query = attendance_scope_query()
    if start_date:
        query = query.where(AttendanceRecord.attendance_date >= start_date)
    if end_date:
        query = query.where(AttendanceRecord.attendance_date <= end_date)
    if status_filter:
        query = query.where(AttendanceRecord.status == status_filter)
    if verification_filter:
        query = query.where(AttendanceRecord.verification_status == verification_filter)
    if employee_id:
        query = query.where(AttendanceRecord.employee_id == employee_id)
    if search:
        like = f"%{search.lower()}%"
        query = query.where(
            or_(
                func.lower(Employee.employee_code).like(like),
                func.lower(Employee.full_name).like(like),
            )
        )
    if department_id:
        query = query.where(Employee.department_id == department_id)
    records = db.session.scalars(
        query.order_by(AttendanceRecord.attendance_date.desc(), Employee.employee_code)
    ).all()

    columns = [
        ("Date", lambda record: record.attendance_date.isoformat()),
        ("Employee ID", lambda record: record.employee.employee_code),
        ("Employee Name", lambda record: record.employee.full_name),
        ("Department", lambda record: record.employee.department.name),
        ("Status", lambda record: record.status),
        ("Check In", lambda record: record.in_time.strftime("%H:%M") if record.in_time else ""),
        ("Check Out", lambda record: record.out_time.strftime("%H:%M") if record.out_time else ""),
        ("Remarks", lambda record: record.remarks or ""),
        ("Verification", lambda record: record.verification_status),
        ("Marked By", lambda record: record.creator.full_name if record.creator else ""),
    ]
    stamp = f"{start_date.isoformat()}_to_{end_date.isoformat()}" if start_date and end_date else "all"
    if file_format == "csv":
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([label for label, _getter in columns])
        for record in records:
            writer.writerow([_excel_safe(getter(record)) for _, getter in columns])
        payload = output.getvalue().encode("utf-8-sig")
        content_type = "text/csv"
        filename = f"attendance_{stamp}.csv"
    else:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Attendance"
        sheet.append([label for label, _getter in columns])
        for record in records:
            sheet.append([_excel_safe(getter(record)) for _, getter in columns])
        for column_cells in sheet.columns:
            width = min(max(max(len(str(cell.value or "")) for cell in column_cells) + 2, 12), 36)
            sheet.column_dimensions[column_cells[0].column_letter].width = width
        from io import BytesIO

        output = BytesIO()
        workbook.save(output)
        payload = output.getvalue()
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"attendance_{stamp}.xlsx"

    log_action(
        current_user,
        "attendance.export",
        "attendance_record",
        None,
        {
            "format": file_format,
            "records": len(records),
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
    )
    db.session.commit()
    response = make_response(payload)
    response.headers["Content-Type"] = content_type
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _excel_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def seed_attendance_records(days=30):
    """Generate sample attendance for the last 30 days (dev only, never automatic)."""
    import random

    from ..models import User

    db.create_all()
    if days < 1 or days > 90:
        return 0, "Seed between 1 and 90 days of attendance."
    submitter = db.session.scalar(
        db.select(User).where(User.status == "active").order_by(User.id)
    )
    if submitter is None:
        return 0, "Seed an active user (e.g. run seed.py) before seeding attendance."
    department = db.session.scalar(
        db.select(Department)
        .where(Department.is_active.is_(True))
        .order_by(Department.name)
    )
    if department is None:
        department = Department(name="Operations", is_active=True)
        db.session.add(department)
        db.session.flush()
    employees = db.session.scalars(
        db.select(Employee)
        .where(Employee.deleted_at.is_(None), Employee.status == "active")
        .order_by(Employee.employee_code)
        .limit(6)
    ).all()
    if not employees:
        today = date.today()
        for index, (code, name) in enumerate(
            [("EMP-0001", "Aarav Sharma"), ("EMP-0002", "Aditi Patel"), ("EMP-0003", "Ananya Rao")]
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

    random.seed(42)
    today = date.today()
    created = 0
    for back in range(days):
        day = today - timedelta(days=back)
        for employee in employees:
            exists = db.session.scalar(
                db.select(AttendanceRecord.id).where(
                    AttendanceRecord.employee_id == employee.id,
                    AttendanceRecord.attendance_date == day,
                )
            )
            if exists:
                continue
            roll = random.random()
            if roll < 0.80:
                status, in_time, out_time = "present", time(9, 0), time(17, 30)
            elif roll < 0.85:
                status, in_time, out_time = "late", time(9, 45), time(17, 30)
            elif roll < 0.90:
                status, in_time, out_time = "absent", None, None
            elif roll < 0.95:
                status, in_time, out_time = "leave", None, None
            else:
                status, in_time, out_time = "half_day", time(9, 0), time(13, 0)
            db.session.add(
                AttendanceRecord(
                    employee_id=employee.id,
                    attendance_date=day,
                    status=status,
                    in_time=in_time,
                    out_time=out_time,
                    verification_status="pending",
                    created_by=submitter.id,
                )
            )
            created += 1
    db.session.commit()
    return created, f"Seeded {created} sample attendance record(s) for the last {days} days."
