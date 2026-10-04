import time
from datetime import date, timedelta

from app.attendance.routes import seed_attendance_records
from app.extensions import db
from app.models import AttendanceRecord, AuditLog, Department, Employee, User
from app.reports.service import build_report


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


def add_employee(app, code, department_id):
    with app.app_context():
        employee = Employee(
            employee_code=code,
            full_name=f"Employee {code}",
            email=f"{code.lower()}@example.com",
            designation="Analyst",
            department_id=department_id,
            joining_date=date(2020, 1, 1),
            status="active",
        )
        db.session.add(employee)
        db.session.commit()
        return employee.id


def add_attendance(app, employee_id, attendance_date, status="present", created_by=None):
    with app.app_context():
        record = AttendanceRecord(
            employee_id=employee_id,
            attendance_date=attendance_date,
            status=status,
            verification_status="pending",
            created_by=created_by,
        )
        db.session.add(record)
        db.session.commit()
        return record.id


def setup_module_data(app):
    with app.app_context():
        operations = Department(name="Operations", is_active=True)
        db.session.add(operations)
        db.session.commit()
        operations_id = operations.id
    manager_id = add_user(app, "att_manager", "manager", operations_id)
    staff_id = add_user(app, "att_staff", "data_entry", operations_id)
    employee_id = add_employee(app, "ATT100", operations_id)
    return operations_id, manager_id, staff_id, employee_id


def test_daily_index_defaults_stats_search_and_pagination(client, app):
    _dept, _manager, staff_id, employee_id = setup_module_data(app)
    add_attendance(app, employee_id, date.today(), created_by=staff_id)
    with app.app_context():
        for code in (f"ATT10{index}" for index in range(1, 11)):
            employee = Employee(
                employee_code=code,
                full_name=f"Employee {code}",
                email=f"{code.lower()}@example.com",
                designation="Analyst",
                department_id=2,
                joining_date=date(2020, 1, 1),
                status="active",
            )
            db.session.add(employee)
            db.session.flush()
            db.session.add(
                AttendanceRecord(
                    employee_id=employee.id,
                    attendance_date=date.today(),
                    status="present",
                    verification_status="pending",
                    created_by=staff_id,
                )
            )
        db.session.commit()
    authenticate_as(client, app, "att_manager")

    response = client.get("/attendance/")
    assert response.status_code == 200
    assert b"Total Employees" in response.data
    assert b"Attendance %" in response.data
    assert b"Mark Attendance" in response.data
    assert "Showing 1–10 of 11 records".encode() in response.data

    response = client.get("/attendance/", query_string={"page": 2})
    assert "Showing 11–11 of 11 records".encode() in response.data

    response = client.get("/attendance/", query_string={"q": "att100"})
    assert b"ATT100" in response.data
    response = client.get("/attendance/", query_string={"q": "no-such-person"})
    assert b"No attendance records match these filters." in response.data

    assert client.get("/attendance/", query_string={"attendance_date": "bad"}).status_code == 400


def test_late_status_accepted_on_create(client, app):
    _dept, _manager, _staff, employee_id = setup_module_data(app)
    authenticate_as(client, app, "att_manager")
    response = client.post(
        "/attendance/create",
        data={
            "employee_id": str(employee_id),
            "attendance_date": date.today().isoformat(),
            "status": "late",
            "in_time": "09:45",
            "out_time": "17:30",
            "remarks": "",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        record = db.session.scalar(db.select(AttendanceRecord))
        assert record.status == "late"


def test_mark_bulk_create_upsert_and_audit(client, app):
    _dept, manager_id, staff_id, employee_id = setup_module_data(app)
    second_id = add_employee(app, "ATT101", 2)
    authenticate_as(client, app, "att_manager")

    response = client.get("/attendance/mark")
    assert response.status_code == 200
    assert b"ATT100" in response.data
    assert b"Mark all present" in response.data

    payload = {
        "mark_date": date.today().isoformat(),
        "department_id": "2",
        "employee_id": [str(employee_id), str(second_id)],
        "status": ["present", "absent"],
        "in_time": ["09:00", ""],
        "out_time": ["17:30", ""],
        "remarks": ["", "On duty visit"],
    }
    response = client.post("/attendance/mark", data=payload, follow_redirects=True)
    assert b"2 created, 0 updated" in response.data
    with app.app_context():
        assert db.session.scalar(
            db.select(AttendanceRecord).where(
                AttendanceRecord.employee_id == second_id,
                AttendanceRecord.status == "absent",
            )
        ) is not None
        assert (
            db.session.scalar(
                db.select(AuditLog.id).where(AuditLog.action == "attendance.create")
            )
            is not None
        )

    payload["status"] = ["late", "absent"]
    response = client.post("/attendance/mark", data=payload, follow_redirects=True)
    assert b"0 created, 2 updated" in response.data
    with app.app_context():
        record = db.session.scalar(
            db.select(AttendanceRecord).where(AttendanceRecord.employee_id == employee_id)
        )
        assert record.status == "late"
        assert (
            db.session.scalar(
                db.select(AuditLog.id).where(AuditLog.action == "attendance.update")
            )
            is not None
        )
        assert record.verified_by is None


def test_mark_validation_rejects_bad_rows(client, app):
    _dept, _manager, _staff, employee_id = setup_module_data(app)
    authenticate_as(client, app, "att_manager")
    future = (date.today() + timedelta(days=1)).isoformat()

    response = client.post(
        "/attendance/mark",
        data={
            "mark_date": future,
            "department_id": "2",
            "employee_id": [str(employee_id)],
            "status": ["present"],
            "in_time": [""],
            "out_time": [""],
            "remarks": [""],
        },
        follow_redirects=True,
    )
    assert b"cannot be in the future" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(AttendanceRecord.id)) is None

    response = client.post(
        "/attendance/mark",
        data={
            "mark_date": date.today().isoformat(),
            "department_id": "2",
            "employee_id": [str(employee_id)],
            "status": ["present"],
            "in_time": ["17:00"],
            "out_time": ["09:00"],
            "remarks": [""],
        },
        follow_redirects=True,
    )
    assert b"out time cannot be earlier" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(AttendanceRecord.id)) is None


def test_mark_role_rules(client, app):
    _dept, manager_id, staff_id, employee_id = setup_module_data(app)
    old_date = (date.today() - timedelta(days=8)).isoformat()
    authenticate_as(client, app, "att_staff")

    assert client.get("/attendance/mark", query_string={"department_id": 1}).status_code == 403
    response = client.post(
        "/attendance/mark",
        data={
            "mark_date": old_date,
            "department_id": "2",
            "employee_id": [str(employee_id)],
            "status": ["present"],
            "in_time": [""],
            "out_time": [""],
            "remarks": [""],
        },
    )
    assert response.status_code == 403

    authenticate_as(client, app, "att_manager")
    response = client.post(
        "/attendance/mark",
        data={
            "mark_date": old_date,
            "department_id": "2",
            "employee_id": [str(employee_id)],
            "status": ["present"],
            "in_time": [""],
            "out_time": [""],
            "remarks": [""],
        },
        follow_redirects=True,
    )
    assert b"0 created, 1 updated" in response.data or b"1 created, 0 updated" in response.data

    with app.app_context():
        record = db.session.scalar(
            db.select(AttendanceRecord).where(
                AttendanceRecord.employee_id == employee_id,
                AttendanceRecord.attendance_date == date.today(),
            )
        )
        if record is None:
            record = AttendanceRecord(
                employee_id=employee_id,
                attendance_date=date.today(),
                status="present",
                verification_status="pending",
                created_by=manager_id,
            )
            db.session.add(record)
        record.verification_status = "verified"
        db.session.commit()
    authenticate_as(client, app, "att_staff")
    response = client.post(
        "/attendance/mark",
        data={
            "mark_date": date.today().isoformat(),
            "department_id": "2",
            "employee_id": [str(employee_id)],
            "status": ["absent"],
            "in_time": [""],
            "out_time": [""],
            "remarks": [""],
        },
        follow_redirects=True,
    )
    assert b"only pending attendance can be edited" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(AttendanceRecord)).status == "present"


def test_monthly_and_history_views(client, app):
    _dept, _manager, staff_id, employee_id = setup_module_data(app)
    add_attendance(app, employee_id, date.today(), "late", created_by=staff_id)
    authenticate_as(client, app, "att_manager")
    month = date.today().strftime("%Y-%m")

    response = client.get("/attendance/monthly", query_string={"month": month})
    assert response.status_code == 200
    assert b"ATT100" in response.data
    assert b"cal-" in response.data or b"Monthly Overview" in response.data

    response = client.get(f"/attendance/employee/{employee_id}", query_string={"month": month})
    assert response.status_code == 200
    assert b"cal-late" in response.data

    assert client.get("/attendance/monthly", query_string={"month": "bad"}).status_code == 400


def test_other_department_history_forbidden_for_manager(client, app):
    _dept, _manager, staff_id, _employee = setup_module_data(app)
    with app.app_context():
        finance = Department(name="Finance", is_active=True)
        db.session.add(finance)
        db.session.commit()
        finance_id = finance.id
    other_id = add_employee(app, "ATT200", finance_id)
    authenticate_as(client, app, "att_manager")
    assert client.get(f"/attendance/employee/{other_id}").status_code == 403


def test_export_csv_xlsx_and_audit(client, app):
    _dept, _manager, staff_id, employee_id = setup_module_data(app)
    add_attendance(app, employee_id, date.today(), created_by=staff_id)
    authenticate_as(client, app, "att_manager")

    response = client.get("/attendance/export/csv")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]
    assert b"ATT100" in response.data

    response = client.get("/attendance/export/xlsx")
    assert response.status_code == 200
    assert "spreadsheetml.sheet" in response.headers["Content-Type"]

    assert client.get("/attendance/export/pdf").status_code == 404
    with app.app_context():
        assert (
            db.session.scalar(
                db.select(AuditLog.id).where(AuditLog.action == "attendance.export")
            )
            is not None
        )


def test_summary_report_type(client, app):
    _dept, _manager, staff_id, employee_id = setup_module_data(app)
    add_attendance(app, employee_id, date.today(), created_by=staff_id)
    today = date.today()
    with app.app_context():
        rows = build_report(db.session, "summary", today, today)
        assert rows and rows[0]["employee_code"] == "ATT100"
        assert rows[0]["attendance_percentage"] == 100.0
        assert set(rows[0]) >= {"present", "absent", "late", "leave", "half_day", "total"}
    authenticate_as(client, app, "admin")
    response = client.get("/reports/", query_string={"type": "summary"})
    assert response.status_code == 200
    assert b"Attendance Summary" in response.data
    response = client.get("/reports/export/xlsx", query_string={"type": "summary"})
    assert response.status_code == 200


def test_seed_attendance_generates_thirty_days(app):
    _dept, _manager, staff_id, employee_id = setup_module_data(app)
    with app.app_context():
        created, message = seed_attendance_records()
        assert created > 0
        assert "30 days" in message
        oldest = db.session.scalar(db.select(AttendanceRecord.attendance_date).order_by(AttendanceRecord.attendance_date))
        assert (date.today() - oldest).days <= 29
        statuses = {
            row[0]
            for row in db.session.execute(db.select(AttendanceRecord.status).distinct()).all()
        }
        assert statuses <= {"present", "absent", "late", "leave", "half_day"}
        created_again, _ = seed_attendance_records()
        assert created_again == 0
