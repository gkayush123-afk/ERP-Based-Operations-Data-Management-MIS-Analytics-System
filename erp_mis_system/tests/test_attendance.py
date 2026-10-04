import time
from datetime import date, time as datetime_time, timedelta
from io import BytesIO

from openpyxl import Workbook

from app.extensions import db
from app.models import AttendanceRecord, AuditLog, Department, Employee, User


def authenticate_as(client, app, username):
    with app.app_context():
        user_id = db.session.scalar(db.select(User.id).where(User.username == username))
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_permanent"] = True
        session["last_activity"] = time.time()


def add_user(app, username, role, department_id):
    with app.app_context():
        user = User(
            full_name=username.replace("_", " ").title(),
            username=username,
            email=f"{username}@example.com",
            department_id=department_id,
            role=role,
            status="active",
        )
        user.set_password("Test-Pass-1234!")
        db.session.add(user)
        db.session.commit()
        return user.id


def add_employee(app, code, department_id=1, created_by=None):
    with app.app_context():
        employee = Employee(
            employee_code=code,
            full_name=f"Employee {code}",
            email=f"{code.lower()}@example.com",
            designation="Analyst",
            department_id=department_id,
            joining_date=date(2020, 1, 1),
            status="active",
            created_by=created_by,
        )
        db.session.add(employee)
        db.session.commit()
        return employee.id


def post_attendance(client, employee_id, attendance_date=None, status="present"):
    return client.post(
        "/attendance/create",
        data={
            "employee_id": str(employee_id),
            "attendance_date": (attendance_date or date.today()).isoformat(),
            "status": status,
            "in_time": "09:00",
            "out_time": "17:00",
            "remarks": "Daily entry",
        },
        follow_redirects=True,
    )


def attendance_workbook(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["employee_code", "date", "status", "in_time", "out_time", "remarks"])
    for row in rows:
        sheet.append(row)
    result = BytesIO()
    workbook.save(result)
    result.seek(0)
    return result


def test_data_entry_creates_edits_and_cannot_edit_after_verification(client, app):
    add_user(app, "attendance_entry", "data_entry", 1)
    employee_id = add_employee(app, "ATT001")
    authenticate_as(client, app, "attendance_entry")

    response = post_attendance(client, employee_id)
    assert b"submitted for verification" in response.data
    with app.app_context():
        record = db.session.scalar(db.select(AttendanceRecord))
        record_id = record.id
        assert record.verification_status == "pending"
        assert record.created_by is not None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "attendance.create")
        )

    response = client.post(
        f"/attendance/{record_id}/edit",
        data={
            "employee_id": str(employee_id),
            "attendance_date": date.today().isoformat(),
            "status": "half_day",
            "in_time": "09:00",
            "out_time": "13:00",
            "remarks": "Edited while pending",
        },
        follow_redirects=True,
    )
    assert b"Pending attendance record was updated" in response.data
    with app.app_context():
        record = db.session.get(AttendanceRecord, record_id)
        assert record.status == "half_day"
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "attendance.update")
        )

    client.post("/logout")
    authenticate_as(client, app, "admin")
    client.post(f"/attendance/{record_id}/verify")
    client.post("/logout")
    authenticate_as(client, app, "attendance_entry")
    assert client.get(f"/attendance/{record_id}/edit").status_code == 403


def test_duplicate_employee_and_date_is_rejected(client, app):
    authenticate_as(client, app, "admin")
    employee_id = add_employee(app, "ATTDUPE")
    today = date.today()
    assert b"submitted for verification" in post_attendance(client, employee_id, today).data
    response = post_attendance(client, employee_id, today)
    assert b"already been entered for this employee and date" in response.data
    with app.app_context():
        count = db.session.scalar(
            db.select(db.func.count(AttendanceRecord.id)).where(
                AttendanceRecord.employee_id == employee_id,
                AttendanceRecord.attendance_date == today,
            )
        )
        assert count == 1


def test_manager_verifies_or_rejects_only_own_department_records(client, app):
    operations = Department(name="Operations")
    with app.app_context():
        db.session.add(operations)
        db.session.commit()
        operations_id = operations.id
    manager_id = add_user(app, "attendance_manager", "manager", 1)
    own_employee = add_employee(app, "ATT-MGR-1", 1)
    other_employee = add_employee(app, "ATT-MGR-2", operations_id)
    authenticate_as(client, app, "admin")
    post_attendance(client, own_employee, date.today() - timedelta(days=1))
    post_attendance(client, other_employee, date.today() - timedelta(days=2))
    client.post("/logout")
    authenticate_as(client, app, "attendance_manager")

    with app.app_context():
        own_record = db.session.scalar(
            db.select(AttendanceRecord).where(AttendanceRecord.employee_id == own_employee)
        )
        other_record = db.session.scalar(
            db.select(AttendanceRecord).where(AttendanceRecord.employee_id == other_employee)
        )
        own_id, other_id = own_record.id, other_record.id

    assert client.post(f"/attendance/{other_id}/verify").status_code == 403
    response = client.post(
        f"/attendance/{own_id}/reject",
        data={"rejection_reason": "Please correct the time."},
        follow_redirects=True,
    )
    assert b"Attendance record rejected" in response.data
    with app.app_context():
        own_record = db.session.get(AttendanceRecord, own_id)
        assert own_record.verification_status == "rejected"
        assert own_record.verified_by == manager_id
        assert own_record.rejection_reason == "Please correct the time."
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "attendance.reject")
        )


def test_verification_dashboard_shows_summary_and_keeps_action_in_view(client, app):
    authenticate_as(client, app, "admin")
    employee_id = add_employee(app, "VERIFY001")
    today = date.today()
    with app.app_context():
        admin_id = db.session.scalar(db.select(User.id).where(User.username == "admin"))
        for offset, verification_status in enumerate(("pending", "verified", "rejected")):
            record = AttendanceRecord(
                employee_id=employee_id,
                attendance_date=today - timedelta(days=offset),
                status="present",
                in_time=datetime_time(9, 0),
                out_time=datetime_time(17, 0),
                verification_status=verification_status,
                created_by=admin_id,
                verified_by=admin_id if verification_status != "pending" else None,
            )
            db.session.add(record)
        db.session.commit()
        pending_id = db.session.scalar(
            db.select(AttendanceRecord.id).where(
                AttendanceRecord.verification_status == "pending"
            )
        )

    response = client.get("/attendance/?view=verification")
    assert response.status_code == 200
    assert b"Verification" in response.data
    assert b"Total Submitted" in response.data
    assert b"Pending Review" in response.data
    assert b"VERIFY001" in response.data

    response = client.post(
        f"/attendance/{pending_id}/verify",
        data={"view": "verification"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/attendance/?view=verification")


def test_data_entry_cannot_open_verification_dashboard(client, app):
    add_user(app, "verification_entry", "data_entry", 1)
    authenticate_as(client, app, "verification_entry")

    response = client.get("/attendance/?view=verification")

    assert response.status_code == 403


def test_bulk_verify_is_scoped_and_audited(client, app):
    manager_id = add_user(app, "bulk_manager", "manager", 1)
    employee_one = add_employee(app, "BULK001")
    employee_two = add_employee(app, "BULK002")
    authenticate_as(client, app, "bulk_manager")
    post_attendance(client, employee_one, date.today() - timedelta(days=1))
    post_attendance(client, employee_two, date.today() - timedelta(days=2))
    with app.app_context():
        records = db.session.scalars(
            db.select(AttendanceRecord).order_by(AttendanceRecord.id)
        ).all()
        record_ids = [record.id for record in records]

    response = client.post(
        "/attendance/bulk-verify",
        data={"record_ids": [str(record_id) for record_id in record_ids]},
        follow_redirects=True,
    )
    assert b"2 attendance record(s) verified" in response.data
    with app.app_context():
        assert all(
            record.verification_status == "verified" and record.verified_by == manager_id
            for record in db.session.scalars(db.select(AttendanceRecord)).all()
        )
        assert len(
            db.session.scalars(
                db.select(AuditLog.id).where(AuditLog.action == "attendance.verify")
            ).all()
        ) == 2


def test_filters_department_scope_and_excel_row_errors(client, app):
    operations = Department(name="Attendance Operations")
    with app.app_context():
        db.session.add(operations)
        db.session.commit()
        operations_id = operations.id
    add_user(app, "attendance_entry_scope", "data_entry", 1)
    general_employee = add_employee(app, "FILTER-GEN", 1)
    operations_employee = add_employee(app, "FILTER-OPS", operations_id)
    authenticate_as(client, app, "admin")
    post_attendance(client, general_employee, date.today() - timedelta(days=1))
    post_attendance(client, operations_employee, date.today() - timedelta(days=2))

    response = client.get(
        f"/attendance/?start_date={(date.today() - timedelta(days=1)).isoformat()}&"
        f"end_date={date.today().isoformat()}&employee_id={general_employee}&status=present"
    )
    assert response.status_code == 200
    assert b"FILTER-GEN" in response.data
    assert b"<strong>FILTER-OPS</strong>" not in response.data

    client.post("/logout")
    authenticate_as(client, app, "attendance_entry_scope")
    response = client.get(
        f"/attendance/?start_date={(date.today() - timedelta(days=2)).isoformat()}&"
        f"end_date={date.today().isoformat()}"
    )
    assert b"FILTER-GEN" in response.data
    assert b"<strong>FILTER-OPS</strong>" not in response.data

    workbook = attendance_workbook(
        [
            ["FILTER-GEN", (date.today() - timedelta(days=3)).isoformat(), "Absent", "09:00", "17:00", "valid row"],
            ["MISSING", (date.today() - timedelta(days=4)).isoformat(), "Present", "", "", ""],
            ["FILTER-GEN", (date.today() - timedelta(days=3)).isoformat(), "Leave", "", "", ""],
        ]
    )
    response = client.post(
        "/attendance/import",
        data={"file": (workbook, "attendance.xlsx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"Imported FILTER-GEN" in response.data
    assert b"employee_code does not match an active employee" in response.data
    assert b"attendance already exists" in response.data
    with app.app_context():
        imported = db.session.scalar(
            db.select(AttendanceRecord).where(AttendanceRecord.attendance_date == date.today() - timedelta(days=3))
        )
        assert imported is not None
        assert imported.verification_status == "pending"
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "attendance.import")
        )


def test_attendance_time_and_future_date_validation(client, app):
    authenticate_as(client, app, "admin")
    employee_id = add_employee(app, "VALIDTIME")
    response = client.post(
        "/attendance/create",
        data={
            "employee_id": str(employee_id),
            "attendance_date": (date.today() + timedelta(days=1)).isoformat(),
            "status": "present",
            "in_time": "17:00",
            "out_time": "09:00",
            "remarks": "",
        },
        follow_redirects=True,
    )
    assert b"cannot be in the future" in response.data
    assert b"Out time cannot be earlier than in time" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(AttendanceRecord.id)) is None
