from datetime import date
from io import BytesIO

from flask import abort, make_response, render_template, request
from flask_login import current_user
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..extensions import db
from ..models import Department
from ..utils.audit import log_action
from ..utils.decorators import roles_required
from . import reports_bp
from .service import STATUS_LABELS, STATUS_VALUES, build_report, report_period


def _filters():
    report_type = request.args.get("type", "daily")
    today = date.today()
    try:
        period_date = date.fromisoformat(request.args.get("date", today.isoformat()))
        month_raw = request.args.get("month", today.strftime("%Y-%m"))
        period_month = date.fromisoformat(f"{month_raw}-01")
        start_date = date.fromisoformat(request.args.get("start_date", today.replace(day=1).isoformat()))
        end_date = date.fromisoformat(request.args.get("end_date", today.isoformat()))
    except ValueError:
        abort(400, description="Enter valid report dates.")
    try:
        start_date, end_date = report_period(
            report_type,
            period_date=period_date,
            period_month=period_month,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as error:
        abort(400, description=str(error))

    status = request.args.get("status") or None
    if status not in (*STATUS_VALUES, None):
        abort(400, description="Choose a valid attendance status.")
    department_raw = request.args.get("department_id", "")
    try:
        department_id = int(department_raw) if department_raw else None
        if department_id is not None and department_id < 1:
            raise ValueError
    except ValueError:
        abort(400, description="Choose a valid department.")
    if current_user.role == "manager":
        if current_user.department_id is None:
            abort(403, description="A manager must be assigned to a department.")
        department_id = current_user.department_id
    return {
        "type": report_type,
        "period_date": period_date,
        "period_month": period_month,
        "start_date": start_date,
        "end_date": end_date,
        "department_id": department_id,
        "status": status,
    }


def _report_data(filters):
    return build_report(
        db.session,
        filters["type"],
        filters["start_date"],
        filters["end_date"],
        department_id=filters["department_id"],
        status=filters["status"],
    )


def _department_options(filters):
    query = db.select(Department).where(Department.is_active.is_(True))
    if current_user.role == "manager":
        query = query.where(Department.id == current_user.department_id)
    return db.session.scalars(query.order_by(Department.name)).all()


def _chart_payload(rows, report_type):
    if report_type == "department":
        return {
            "labels": [row["department"] for row in rows],
            "series": {
                status: [row[status] for row in rows]
                for status in STATUS_VALUES
            },
        }
    totals = {}
    for row in rows:
        date_label = row["date"]
        values = totals.setdefault(date_label, {status: 0 for status in STATUS_VALUES})
        for status in STATUS_VALUES:
            values[status] += row[status]
    return {
        "labels": list(totals),
        "series": {
            status: [totals[label][status] for label in totals]
            for status in STATUS_VALUES
        },
    }


def _excel_value(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


@reports_bp.route("/")
@roles_required("admin", "manager")
def index():
    filters = _filters()
    rows = _report_data(filters)
    departments = _department_options(filters)
    return render_template(
        "reports/index.html",
        rows=rows,
        departments=departments,
        filters=filters,
        chart=_chart_payload(rows, filters["type"]),
        status_labels=STATUS_LABELS,
    )


@reports_bp.route("/export/<file_format>")
@roles_required("admin", "manager")
def export(file_format):
    if file_format not in {"xlsx", "pdf"}:
        abort(404)
    filters = _filters()
    rows = _report_data(filters)
    columns = (
        [
            ("department", "Department"),
            ("present", "Present"),
            ("absent", "Absent"),
            ("leave", "Leave"),
            ("half_day", "Half-day"),
            ("active_employees", "Active employees"),
            ("attendance_percentage", "Attendance %"),
        ]
        if filters["type"] == "department"
        else [
            ("date", "Date"),
            ("department", "Department"),
            ("present", "Present"),
            ("absent", "Absent"),
            ("leave", "Leave"),
            ("half_day", "Half-day"),
        ]
    )
    report_title = f"{filters['type'].title()} MIS Report"
    if file_format == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "MIS Report"
        sheet.append([report_title])
        sheet.append(
            [
                f"Period: {filters['start_date'].isoformat()} to {filters['end_date'].isoformat()}",
                f"Status filter: {STATUS_LABELS.get(filters['status'], 'All')}",
            ]
        )
        sheet.append([label for _, label in columns])
        for cell in sheet[3]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="174A7E")
        for row in rows:
            sheet.append([_excel_value(row[key]) for key, _ in columns])
        for column_cells in sheet.columns:
            width = min(max(max(len(str(cell.value or "")) for cell in column_cells) + 2, 12), 36)
            sheet.column_dimensions[column_cells[0].column_letter].width = width
        output = BytesIO()
        workbook.save(output)
        payload = output.getvalue()
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{filters['type']}_mis_report.xlsx"
    else:
        output = BytesIO()
        document = SimpleDocTemplate(output, pagesize=landscape(letter))
        styles = getSampleStyleSheet()
        data = [[label for _, label in columns]]
        data.extend([[str(row[key]) for key, _ in columns] for row in rows])
        table = Table(data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174a7e")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#ccd5df")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f6fa")]),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        document.build(
            [
                Paragraph(report_title, styles["Title"]),
                Paragraph(
                    f"Period: {filters['start_date'].isoformat()} to {filters['end_date'].isoformat()}",
                    styles["Normal"],
                ),
                Spacer(1, 12),
                table,
            ]
        )
        payload = output.getvalue()
        content_type = "application/pdf"
        filename = f"{filters['type']}_mis_report.pdf"

    log_action(
        current_user,
        "reports.export",
        "mis_report",
        None,
        {
            "report_type": filters["type"],
            "format": file_format,
            "start_date": filters["start_date"].isoformat(),
            "end_date": filters["end_date"].isoformat(),
            "department_id": filters["department_id"],
            "status": filters["status"],
        },
    )
    db.session.commit()
    response = make_response(payload)
    response.headers["Content-Type"] = content_type
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
