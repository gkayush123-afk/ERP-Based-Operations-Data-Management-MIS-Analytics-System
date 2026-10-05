from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..models import AttendanceRecord, AuditLog, Department, Employee, OperationRecord, User

OPERATION_STATUSES = ("verified", "pending", "rejected", "needs_correction")

STATUS_VALUES = ("present", "absent", "late", "leave", "half_day")
STATUS_LABELS = {
    "present": "Present",
    "absent": "Absent",
    "late": "Late",
    "leave": "Leave",
    "half_day": "Half-day",
}

ACTIVITY_LABELS = {
    "auth.login": "Signed in",
    "auth.logout": "Signed out",
    "auth.session_expired": "Session expired",
    "user.register": "Requested an account",
    "user.approve": "Approved a user",
    "user.reject": "Rejected a user",
    "user.password_changed": "Changed password",
    "attendance.create": "Marked attendance",
    "attendance.update": "Edited attendance",
    "attendance.import": "Imported attendance",
    "attendance.export": "Exported attendance",
    "attendance.verify": "Verified attendance",
    "attendance.reject": "Rejected attendance",
    "verification.approve": "Approved an operation record",
    "verification.reject": "Rejected an operation record",
    "reports.export": "Exported a report",
    "mis_reports.export": "Exported an MIS report",
}


def report_period(report_type, period_date=None, period_month=None, start_date=None, end_date=None):
    if report_type == "daily":
        if period_date is None:
            raise ValueError("Select a date for the daily report.")
        return period_date, period_date
    if report_type == "weekly":
        if period_date is None:
            raise ValueError("Select a date in the week to report.")
        start = period_date - timedelta(days=period_date.weekday())
        return start, start + timedelta(days=6)
    if report_type == "monthly":
        if period_month is None:
            raise ValueError("Select a month for the monthly report.")
        start = period_month.replace(day=1)
        return start, start.replace(day=monthrange(start.year, start.month)[1])
    if report_type == "department":
        if start_date is None or end_date is None:
            raise ValueError("Select both dates for the department summary.")
        if start_date > end_date:
            raise ValueError("Start date must not be after end date.")
        return start_date, end_date
    if report_type == "summary":
        if start_date is None or end_date is None:
            raise ValueError("Select both dates for the attendance summary.")
        if start_date > end_date:
            raise ValueError("Start date must not be after end date.")
        return start_date, end_date
    raise ValueError("Choose a valid report type.")


def _attendance_filter(start_date, end_date, department_id=None, status=None):
    conditions = [
        AttendanceRecord.attendance_date >= start_date,
        AttendanceRecord.attendance_date <= end_date,
        Employee.deleted_at.is_(None),
        # TODO(verification): once operation records feed MIS reports, restrict
        # aggregates to verified rows, e.g. OperationRecord.status == "verified".
    ]
    if department_id is not None:
        conditions.append(Employee.department_id == department_id)
    if status in STATUS_VALUES:
        conditions.append(AttendanceRecord.status == status)
    return conditions


def _status_columns():
    return [
        func.coalesce(
            func.sum(case((AttendanceRecord.status == status, 1), else_=0)), 0
        ).label(status)
        for status in STATUS_VALUES
    ]


def build_report(session: Session, report_type, start_date, end_date, department_id=None, status=None):
    conditions = _attendance_filter(start_date, end_date, department_id, status)
    if report_type == "department":
        status_conditions = _attendance_filter(
            start_date, end_date, department_id, status
        )
        attendance_totals = (
            select(Department.id.label("department_id"), *_status_columns())
            .join(Employee, Employee.department_id == Department.id)
            .join(AttendanceRecord, AttendanceRecord.employee_id == Employee.id)
            .where(*status_conditions)
            .group_by(Department.id)
            .subquery()
        )
        present_totals = (
            select(
                Department.id.label("department_id"),
                func.count(AttendanceRecord.id).label("present"),
            )
            .join(Employee, Employee.department_id == Department.id)
            .join(AttendanceRecord, AttendanceRecord.employee_id == Employee.id)
            .where(
                *_attendance_filter(start_date, end_date, department_id),
                AttendanceRecord.status == "present",
            )
            .group_by(Department.id)
            .subquery()
        )
        active_employee_totals = (
            select(
                Employee.department_id.label("department_id"),
                func.count(Employee.id).label("active_employees"),
            )
            .where(
                Employee.deleted_at.is_(None),
                Employee.status == "active",
                Employee.joining_date <= end_date,
                *([Employee.department_id == department_id] if department_id is not None else []),
            )
            .group_by(Employee.department_id)
            .subquery()
        )
        query = (
            select(
                Department.name.label("department"),
                func.coalesce(attendance_totals.c.present, 0).label("present"),
                func.coalesce(attendance_totals.c.absent, 0).label("absent"),
                func.coalesce(attendance_totals.c.late, 0).label("late"),
                func.coalesce(attendance_totals.c.leave, 0).label("leave"),
                func.coalesce(attendance_totals.c.half_day, 0).label("half_day"),
                func.coalesce(present_totals.c.present, 0).label("present_for_percentage"),
                func.coalesce(active_employee_totals.c.active_employees, 0).label(
                    "active_employees"
                ),
            )
            .outerjoin(attendance_totals, attendance_totals.c.department_id == Department.id)
            .outerjoin(present_totals, present_totals.c.department_id == Department.id)
            .outerjoin(
                active_employee_totals,
                active_employee_totals.c.department_id == Department.id,
            )
            .where(Department.is_active.is_(True))
        )
        if department_id is not None:
            query = query.where(Department.id == department_id)
        raw_rows = session.execute(query.order_by(Department.name)).all()
        expected_days = (end_date - start_date).days + 1
        rows = []
        for row in raw_rows:
            expected = row.active_employees * expected_days
            rows.append(
                {
                    "department": row.department,
                    "present": int(row.present),
                    "absent": int(row.absent),
                    "late": int(row.late),
                    "leave": int(row.leave),
                    "half_day": int(row.half_day),
                    "active_employees": int(row.active_employees),
                    "attendance_percentage": round(row.present_for_percentage * 100 / expected, 2)
                    if expected
                    else 0.0,
                    "expected_employee_days": expected,
                }
            )
        return rows

    if report_type == "summary":
        # Attendance % by department and employee: present records divided by
        # all records in the range for that employee.
        join_conditions = [
            AttendanceRecord.employee_id == Employee.id,
            AttendanceRecord.attendance_date >= start_date,
            AttendanceRecord.attendance_date <= end_date,
        ]
        if status in STATUS_VALUES:
            join_conditions.append(AttendanceRecord.status == status)
        query = (
            select(
                Department.name.label("department"),
                Employee.employee_code.label("employee_code"),
                Employee.full_name.label("employee_name"),
                *_status_columns(),
                func.count(AttendanceRecord.id).label("total"),
            )
            .join(Employee, Employee.department_id == Department.id)
            .outerjoin(AttendanceRecord, and_(*join_conditions))
            .where(
                Employee.deleted_at.is_(None),
                Employee.status == "active",
                Employee.joining_date <= end_date,
                Department.is_active.is_(True),
            )
            .group_by(Department.name, Employee.employee_code, Employee.full_name)
            .order_by(Department.name, Employee.employee_code)
        )
        if department_id is not None:
            query = query.where(Department.id == department_id)
        rows = []
        for row in session.execute(query):
            total = int(row.total)
            rows.append(
                {
                    "department": row.department,
                    "employee_code": row.employee_code,
                    "employee_name": row.employee_name,
                    **{status_name: int(getattr(row, status_name)) for status_name in STATUS_VALUES},
                    "total": total,
                    "attendance_percentage": round(row.present * 100 / total, 2) if total else 0.0,
                }
            )
        return rows

    query = (
        select(
            AttendanceRecord.attendance_date.label("report_date"),
            Department.name.label("department"),
            *_status_columns(),
        )
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .join(Department, Department.id == Employee.department_id)
        .where(*conditions)
        .group_by(AttendanceRecord.attendance_date, Department.name)
        .order_by(AttendanceRecord.attendance_date, Department.name)
    )
    return [
        {
            "date": row.report_date.isoformat(),
            "department": row.department,
            **{status_name: int(getattr(row, status_name)) for status_name in STATUS_VALUES},
        }
        for row in session.execute(query)
    ]


def build_dashboard_metrics(session: Session, department_id=None, today=None):
    today = today or date.today()
    employee_conditions = [
        Employee.deleted_at.is_(None),
        Employee.status == "active",
    ]
    attendance_conditions = [Employee.deleted_at.is_(None)]
    if department_id is not None:
        employee_conditions.append(Employee.department_id == department_id)
        attendance_conditions.append(Employee.department_id == department_id)

    total_employees = session.scalar(
        select(func.count(Employee.id)).where(*employee_conditions)
    ) or 0
    today_attendance = session.execute(
        select(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .where(
            *attendance_conditions,
            AttendanceRecord.attendance_date == today,
        )
        .group_by(AttendanceRecord.status)
    ).all()
    today_counts = {status: 0 for status in STATUS_VALUES}
    for status, count in today_attendance:
        if status in today_counts:
            today_counts[status] = int(count)

    pending_query = (
        select(func.count(AttendanceRecord.id))
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .where(
            *attendance_conditions,
            AttendanceRecord.verification_status == "pending",
        )
    )
    pending_verifications = int(session.scalar(pending_query) or 0)

    department_employee_counts = {
        name: int(count)
        for name, count in session.execute(
            select(Department.name, func.count(Employee.id))
            .join(Employee, Employee.department_id == Department.id)
            .where(
                Employee.deleted_at.is_(None),
                Employee.status == "active",
                Department.is_active.is_(True),
                *([Employee.department_id == department_id] if department_id is not None else []),
            )
            .group_by(Department.name)
        )
    }
    department_performance = []
    total_active_department_employees = sum(department_employee_counts.values())
    for name, count in sorted(department_employee_counts.items()):
        department_performance.append(
            {
                "name": name,
                "employees": count,
                "percent": round((count / total_active_department_employees) * 100, 1)
                if total_active_department_employees
                else 0,
            }
        )

    monthly_sales = total_employees * 950 + sum(today_counts.values()) * 220
    purchase_amount = total_employees * 640 + pending_verifications * 180
    completed_tasks = today_counts.get("present", 0) + today_counts.get("leave", 0)

    department_chart_query = (
        select(Department.name, AttendanceRecord.status, func.count(AttendanceRecord.id))
        .join(Employee, Employee.department_id == Department.id)
        .join(AttendanceRecord, AttendanceRecord.employee_id == Employee.id)
        .where(
            Employee.deleted_at.is_(None),
            AttendanceRecord.attendance_date == today,
            Department.is_active.is_(True),
            *([Employee.department_id == department_id] if department_id is not None else []),
        )
        .group_by(Department.name, AttendanceRecord.status)
        .order_by(Department.name, AttendanceRecord.status)
    )
    department_names = []
    department_series = {status: [] for status in STATUS_VALUES}
    departments = {}
    for name, status, count in session.execute(department_chart_query):
        if name not in departments:
            departments[name] = {key: 0 for key in STATUS_VALUES}
        if status in departments[name]:
            departments[name][status] = int(count)
    department_names = list(departments)
    for name in department_names:
        for status in STATUS_VALUES:
            department_series[status].append(departments[name][status])

    month_start = today.replace(day=1)
    first_month = (month_start.replace(day=1) - timedelta(days=1)).replace(day=1)
    for _ in range(10):
        first_month = (first_month - timedelta(days=1)).replace(day=1)
    trend_start = first_month
    year = func.extract("year", AttendanceRecord.attendance_date)
    month = func.extract("month", AttendanceRecord.attendance_date)
    monthly_query = (
        select(year.label("year"), month.label("month"), AttendanceRecord.status, func.count(AttendanceRecord.id))
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .where(
            *attendance_conditions,
            AttendanceRecord.attendance_date >= trend_start,
            AttendanceRecord.attendance_date <= today,
        )
        .group_by(year, month, AttendanceRecord.status)
    )
    monthly_counts = {}
    for report_year, report_month, status, count in session.execute(monthly_query):
        key = (int(report_year), int(report_month))
        monthly_counts.setdefault(key, {key: 0 for key in STATUS_VALUES})
        if status in monthly_counts[key]:
            monthly_counts[key][status] = int(count)

    trend_labels = []
    trend_series = {status: [] for status in STATUS_VALUES}
    cursor = trend_start
    for _ in range(12):
        key = (cursor.year, cursor.month)
        trend_labels.append(cursor.strftime("%b %Y"))
        for status in STATUS_VALUES:
            trend_series[status].append(monthly_counts.get(key, {}).get(status, 0))
        cursor = (cursor.replace(day=monthrange(cursor.year, cursor.month)[1]) + timedelta(days=1))

    operation_base = (
        [OperationRecord.department_id == department_id]
        if department_id is not None
        else []
    )
    operation_counts = {"verified": 0, "pending": 0, "rejected": 0, "needs_correction": 0}
    for status, count in session.execute(
        select(OperationRecord.status, func.count(OperationRecord.id))
        .where(*operation_base)
        .group_by(OperationRecord.status)
    ):
        if status in operation_counts:
            operation_counts[status] = int(count)
    operation_totals = {
        **operation_counts,
        "total": sum(operation_counts.values()),
    }

    operations_months = []
    operations_verified = []
    operations_total = []
    month_starts = []
    month_cursor = month_start
    for _ in range(5):
        month_cursor = (month_cursor - timedelta(days=1)).replace(day=1)
    for _ in range(6):
        month_starts.append(month_cursor)
        operations_months.append(month_cursor.strftime("%b %Y"))
        month_cursor = (month_cursor.replace(day=monthrange(month_cursor.year, month_cursor.month)[1]) + timedelta(days=1))

    range_start = month_starts[0]
    last_start = month_starts[-1]
    range_end = last_start.replace(day=monthrange(last_start.year, last_start.month)[1]) + timedelta(days=1)
    op_year = func.extract("year", OperationRecord.record_date)
    op_month = func.extract("month", OperationRecord.record_date)
    monthly_operation_counts: dict[tuple[int, int], dict[str, int]] = {}
    for report_year, report_month, status, count in session.execute(
        select(
            op_year.label("year"),
            op_month.label("month"),
            OperationRecord.status,
            func.count(OperationRecord.id),
        )
        .where(
            *operation_base,
            OperationRecord.record_date >= range_start,
            OperationRecord.record_date < range_end,
        )
        .group_by(op_year, op_month, OperationRecord.status)
    ):
        monthly_operation_counts.setdefault((int(report_year), int(report_month)), {})[status] = int(count)
    for start in month_starts:
        month_counts = monthly_operation_counts.get((start.year, start.month), {})
        month_verified = month_counts.get("verified", 0)
        operations_verified.append(month_verified)
        operations_total.append(
            month_verified + month_counts.get("pending", 0) + month_counts.get("rejected", 0)
        )

    activity_query = (
        select(AuditLog, User.full_name)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .limit(10)
    )
    if department_id is not None:
        activity_query = activity_query.where(
            or_(User.id.is_(None), User.department_id == department_id)
        )
    recent_activities = []
    for log, actor_name in session.execute(activity_query):
        target = f"{log.entity} #{log.entity_id}" if log.entity_id is not None else log.entity
        recent_activities.append(
            {
                "actor": actor_name or "System",
                "label": ACTIVITY_LABELS.get(log.action, log.action),
                "target": target,
                "when": log.occurred_at.strftime("%d %b %H:%M"),
            }
        )

    return {
        "total_employees": int(total_employees),
        "today_attendance": today_counts,
        "today_attendance_total": sum(today_counts.values()),
        "pending_verifications": pending_verifications,
        "completed_tasks": int(completed_tasks),
        "monthly_sales": int(monthly_sales),
        "purchase_amount": int(purchase_amount),
        "department_performance": department_performance,
        "department_labels": department_names,
        "department_series": department_series,
        "trend_labels": trend_labels,
        "trend_series": trend_series,
        "operation_totals": operation_totals,
        "operations_monthly": {
            "labels": operations_months,
            "verified": operations_verified,
            "total": operations_total,
        },
        "recent_activities": recent_activities,
    }


def previous_period(start_date, end_date):
    """Return the equal-length period immediately before [start_date, end_date]."""
    length = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    return prev_end - timedelta(days=length - 1), prev_end


def pct_change(current, previous):
    """Return (percent, direction) vs the previous period.

    Direction is "up"/"down"/"flat"; divide-by-zero yields 100.0 when the
    current value is positive, else 0.0.
    """
    if previous:
        percent = round((current - previous) * 100 / previous, 1)
    else:
        percent = 100.0 if current else 0.0
    if percent > 0:
        direction = "up"
    elif percent < 0:
        direction = "down"
    else:
        direction = "flat"
    return percent, direction


def _operation_scope(query, department_id=None, employee_id=None, search=""):
    if department_id is not None:
        query = query.where(OperationRecord.department_id == department_id)
    if employee_id is not None:
        query = query.where(OperationRecord.employee_id == employee_id)
    if search:
        like = f"%{search.lower()}%"
        query = query.where(
            Employee.employee_code.ilike(like) | Employee.full_name.ilike(like)
        )
    return query


def build_operation_summary(session, start_date, end_date, department_id=None, employee_id=None, search=""):
    """Aggregate operation-record counts by status over a date range.

    Only rows with status="verified" count as verified output; pending and
    rejected are reported separately and never mixed in.
    """
    query = (
        select(OperationRecord.status, func.count(OperationRecord.id))
        .join(Employee, Employee.id == OperationRecord.employee_id)
        .where(
            OperationRecord.record_date >= start_date,
            OperationRecord.record_date <= end_date,
        )
    )
    query = _operation_scope(query, department_id, employee_id, search)
    counts = {status: 0 for status in ("total", *OPERATION_STATUSES)}
    for status, total in session.execute(query.group_by(OperationRecord.status)):
        if status in counts:
            counts[status] = int(total)
    counts["total"] = sum(value for key, value in counts.items() if key != "total")
    return counts


def build_operation_trend(session, start_date, end_date, department_id=None, employee_id=None, search=""):
    """Daily totals per status for the line chart, zero-filled per day."""
    query = (
        select(
            OperationRecord.record_date,
            OperationRecord.status,
            func.count(OperationRecord.id),
        )
        .join(Employee, Employee.id == OperationRecord.employee_id)
        .where(
            OperationRecord.record_date >= start_date,
            OperationRecord.record_date <= end_date,
        )
        .group_by(OperationRecord.record_date, OperationRecord.status)
        .order_by(OperationRecord.record_date)
    )
    query = _operation_scope(query, department_id, employee_id, search)
    by_day = {}
    for record_date, status, total in session.execute(query):
        by_day.setdefault(record_date.isoformat(), {})[status] = int(total)
    labels, series = [], {"total": [], "verified": [], "pending": [], "rejected": [], "needs_correction": []}
    cursor = start_date
    while cursor <= end_date:
        key = cursor.isoformat()
        labels.append(key)
        day = by_day.get(key, {})
        verified = day.get("verified", 0)
        pending = day.get("pending", 0)
        rejected = day.get("rejected", 0)
        correction = day.get("needs_correction", 0)
        series["verified"].append(verified)
        series["pending"].append(pending)
        series["rejected"].append(rejected)
        series["needs_correction"].append(correction)
        series["total"].append(verified + pending + rejected + correction)
        cursor += timedelta(days=1)
    return {"labels": labels, "series": series}


def build_operation_by_department(session, start_date, end_date, department_id=None, employee_id=None, search=""):
    """Per-department totals with completion % and last update time."""
    query = (
        select(
            Department.name.label("department"),
            func.count(OperationRecord.id).label("total"),
            func.coalesce(
                func.sum(case((OperationRecord.status == "verified", 1), else_=0)), 0
            ).label("verified"),
            func.coalesce(
                func.sum(case((OperationRecord.status == "pending", 1), else_=0)), 0
            ).label("pending"),
            func.coalesce(
                func.sum(case((OperationRecord.status == "rejected", 1), else_=0)), 0
            ).label("rejected"),
            func.coalesce(
                func.sum(case((OperationRecord.status == "needs_correction", 1), else_=0)), 0
            ).label("needs_correction"),
            func.max(OperationRecord.updated_at).label("last_updated"),
        )
        .join(Department, Department.id == OperationRecord.department_id)
        .join(Employee, Employee.id == OperationRecord.employee_id)
        .where(
            OperationRecord.record_date >= start_date,
            OperationRecord.record_date <= end_date,
        )
        .group_by(Department.name)
        .order_by(func.count(OperationRecord.id).desc(), Department.name)
    )
    query = _operation_scope(query, department_id, employee_id, search)
    rows = []
    for row in session.execute(query):
        total = int(row.total)
        rows.append(
            {
                "department": row.department,
                "total": total,
                "verified": int(row.verified),
                "pending": int(row.pending),
                "rejected": int(row.rejected),
                "needs_correction": int(row.needs_correction),
                "completion_pct": round(row.verified * 100 / total, 1) if total else 0.0,
                "last_updated": row.last_updated.isoformat(sep=" ", timespec="minutes")
                if row.last_updated
                else "—",
            }
        )
    return rows


def build_operation_by_employee(session, start_date, end_date, department_id=None, employee_id=None, search="", limit=None):
    """Per-employee totals ordered by volume, for the bar chart and table."""
    query = (
        select(
            Employee.employee_code.label("employee_code"),
            Employee.full_name.label("employee_name"),
            Department.name.label("department"),
            func.count(OperationRecord.id).label("total"),
            func.coalesce(
                func.sum(case((OperationRecord.status == "verified", 1), else_=0)), 0
            ).label("verified"),
            func.coalesce(
                func.sum(case((OperationRecord.status == "pending", 1), else_=0)), 0
            ).label("pending"),
            func.coalesce(
                func.sum(case((OperationRecord.status == "rejected", 1), else_=0)), 0
            ).label("rejected"),
            func.coalesce(
                func.sum(case((OperationRecord.status == "needs_correction", 1), else_=0)), 0
            ).label("needs_correction"),
            func.max(OperationRecord.updated_at).label("last_updated"),
        )
        .join(Employee, Employee.id == OperationRecord.employee_id)
        .join(Department, Department.id == OperationRecord.department_id)
        .where(
            OperationRecord.record_date >= start_date,
            OperationRecord.record_date <= end_date,
        )
        .group_by(Employee.employee_code, Employee.full_name, Department.name)
        .order_by(func.count(OperationRecord.id).desc(), Employee.employee_code)
    )
    query = _operation_scope(query, department_id, employee_id, search)
    if limit is not None:
        query = query.limit(limit)
    rows = []
    for row in session.execute(query):
        total = int(row.total)
        rows.append(
            {
                "employee_code": row.employee_code,
                "employee_name": row.employee_name,
                "department": row.department,
                "total": total,
                "verified": int(row.verified),
                "pending": int(row.pending),
                "rejected": int(row.rejected),
                "needs_correction": int(row.needs_correction),
                "completion_pct": round(row.verified * 100 / total, 1) if total else 0.0,
                "last_updated": row.last_updated.isoformat(sep=" ", timespec="minutes")
                if row.last_updated
                else "—",
            }
        )
    return rows
