from datetime import date, datetime, time
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from ..extensions import db
from ..models import AttendanceRecord, Department, Employee, utcnow
from ..utils.audit import log_action
from ..utils.decorators import roles_required
from . import attendance_bp
from .forms import ATTENDANCE_STATUSES, VERIFICATION_STATUSES, AttendanceForm

PER_PAGE = 25
IMPORT_COLUMNS = ("employee_code", "date", "status", "in_time", "out_time", "remarks")


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
    start_date = parse_workbook_date(request.args.get("start_date"))
    end_date = parse_workbook_date(request.args.get("end_date"))
    department_id = request.args.get("department_id", type=int)
    employee_id = request.args.get("employee_id", type=int)
    status_filter = request.args.get("status", "")
    verification_filter = request.args.get("verification_status", "")
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
    if department_id:
        if current_user.role == "data_entry":
            if department_id != current_user.department_id:
                abort(403)
        if current_user.role == "manager" and department_id != current_user.department_id:
            abort(403)
        query = query.where(Employee.department_id == department_id)

    pagination = db.paginate(
        query.order_by(AttendanceRecord.attendance_date.desc(), AttendanceRecord.id.desc()),
        page=page,
        per_page=PER_PAGE,
        error_out=False,
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

    return render_template(
        "attendance/index.html",
        pagination=pagination,
        departments=departments,
        employees=employees,
        start_date=start_date.isoformat() if start_date else "",
        end_date=end_date.isoformat() if end_date else "",
        department_id=department_id,
        employee_id=employee_id,
        status_filter=status_filter,
        verification_filter=verification_filter,
        statuses=ATTENDANCE_STATUSES,
        verification_statuses=VERIFICATION_STATUSES,
        can_verify=current_user.role in {"admin", "manager"},
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
            return render_template("attendance/form.html", form=form, record=None, employees=employees)

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
            return render_template("attendance/form.html", form=form, record=None, employees=employees)
        flash("Attendance record submitted for verification.", "success")
        return redirect(url_for("attendance.index"))

    if request.method == "POST":
        for errors in form.errors.values():
            for message in errors:
                flash(message, "danger")
    return render_template("attendance/form.html", form=form, record=None, employees=employees)


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
            return render_template("attendance/form.html", form=form, record=record, employees=employees)

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
            return render_template("attendance/form.html", form=form, record=record, employees=employees)
        flash("Pending attendance record was updated.", "success")
        return redirect(url_for("attendance.index"))

    if request.method == "POST":
        for errors in form.errors.values():
            for message in errors:
                flash(message, "danger")
    return render_template("attendance/form.html", form=form, record=record, employees=employees)


@attendance_bp.route("/<int:record_id>/verify", methods=["POST"])
@roles_required("admin", "manager")
def verify(record_id):
    record = db.get_or_404(AttendanceRecord, record_id)
    employee = db.get_or_404(Employee, record.employee_id)
    if current_user.role == "manager" and employee.department_id != current_user.department_id:
        abort(403)
    if record.verification_status != "pending":
        flash("Only pending attendance can be verified.", "warning")
        return redirect(url_for("attendance.index"))

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
    return redirect(url_for("attendance.index"))


@attendance_bp.route("/<int:record_id>/reject", methods=["POST"])
@roles_required("admin", "manager")
def reject(record_id):
    record = db.get_or_404(AttendanceRecord, record_id)
    employee = db.get_or_404(Employee, record.employee_id)
    if current_user.role == "manager" and employee.department_id != current_user.department_id:
        abort(403)
    if record.verification_status != "pending":
        flash("Only pending attendance can be rejected.", "warning")
        return redirect(url_for("attendance.index"))

    reason = request.form.get("rejection_reason", "").strip()
    if not reason or len(reason) > 500:
        flash("A rejection reason of 1 to 500 characters is required.", "danger")
        return redirect(url_for("attendance.index"))

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
    return redirect(url_for("attendance.index"))


@attendance_bp.route("/bulk-verify", methods=["POST"])
@roles_required("admin", "manager")
def bulk_verify():
    raw_ids = request.form.getlist("record_ids")
    if not raw_ids or len(raw_ids) > 500:
        flash("Select between 1 and 500 pending records to verify.", "warning")
        return redirect(url_for("attendance.index"))
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
    return redirect(url_for("attendance.index"))


@attendance_bp.route("/import", methods=["GET", "POST"])
@roles_required("admin", "manager", "data_entry")
def import_excel():
    row_results = []
    if request.method == "POST":
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            flash("Choose an .xlsx workbook.", "danger")
            return render_template("attendance/import.html", row_results=row_results)
        if Path(upload.filename).suffix.lower() != ".xlsx":
            flash("Only .xlsx workbooks are supported.", "danger")
            return render_template("attendance/import.html", row_results=row_results)
        try:
            workbook = load_workbook(upload, read_only=True, data_only=True)
            sheet = workbook.active
            rows = sheet.iter_rows(values_only=True)
            headers = [str(value or "").strip().lower() for value in (next(rows, None) or ())]
        except (InvalidFileException, BadZipFile, OSError, ValueError, KeyError, ParseError):
            flash("The workbook could not be read. Choose a valid .xlsx file.", "danger")
            return render_template("attendance/import.html", row_results=row_results)
        missing = [column for column in IMPORT_COLUMNS if column not in headers]
        if missing:
            workbook.close()
            flash(f"Missing required column(s): {', '.join(missing)}.", "danger")
            return render_template("attendance/import.html", row_results=row_results)
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
    return render_template("attendance/import.html", row_results=row_results)
