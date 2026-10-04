import time
from datetime import date, timedelta

from app.extensions import db
from app.models import AuditLog, Department, Employee, OperationRecord, User


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


def add_department(app, name):
    with app.app_context():
        department = Department(name=name, is_active=True)
        db.session.add(department)
        db.session.commit()
        return department.id


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


def add_record(app, employee_id, department_id, submitted_by, day_offset=0, status="pending"):
    with app.app_context():
        record = OperationRecord(
            record_date=date.today() - timedelta(days=day_offset),
            employee_id=employee_id,
            department_id=department_id,
            task_operation="Order processing",
            total_records=50,
            completed=48,
            pending=2,
            status=status,
            submitted_by=submitted_by,
        )
        db.session.add(record)
        db.session.commit()
        return record.id


def setup_verification_data(app):
    operations_id = add_department(app, "Operations")
    manager_id = add_user(app, "verify_manager", "manager", operations_id)
    staff_id = add_user(app, "verify_staff", "data_entry", operations_id)
    employee_id = add_employee(app, "VER001", operations_id)
    return operations_id, manager_id, staff_id, employee_id


def test_data_entry_and_anonymous_users_cannot_access_verification(client, app):
    operations_id, _manager, staff_id, employee_id = setup_verification_data(app)
    record_id = add_record(app, employee_id, operations_id, staff_id)
    authenticate_as(client, app, "verify_staff")

    assert client.get("/verification/").status_code == 403
    assert client.post(f"/verification/{record_id}/approve").status_code == 403
    assert client.post(f"/verification/{record_id}/reject").status_code == 403
    assert client.post("/verification/bulk").status_code == 403

    client.post("/logout")
    response = client.get("/verification/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_manager_views_filters_and_pagination(client, app):
    operations_id, _manager, staff_id, employee_id = setup_verification_data(app)
    for _ in range(10):
        add_record(app, employee_id, operations_id, staff_id)
    authenticate_as(client, app, "verify_manager")

    response = client.get("/verification/")
    assert response.status_code == 200
    assert b"Verification" in response.data
    assert "Showing 1–8 of 10 records".encode() in response.data

    response = client.get("/verification/", query_string={"page": 2})
    assert response.status_code == 200
    assert "Showing 9–10 of 10 records".encode() in response.data

    response = client.get("/verification/", query_string={"status": "verified"})
    assert "Showing 0–0 of 0 records".encode() in response.data
    assert b"No records found." in response.data

    response = client.get("/verification/", query_string={"employee_code": "ver001"})
    assert "of 10 records".encode() in response.data

    response = client.get(
        "/verification/", query_string={"record_date": date.today().isoformat()}
    )
    assert response.status_code == 200

    assert client.get("/verification/", query_string={"start_date": "nope"}).status_code == 400


def test_manager_approves_record_with_audit_trail(client, app):
    operations_id, manager_id, staff_id, employee_id = setup_verification_data(app)
    record_id = add_record(app, employee_id, operations_id, staff_id)
    authenticate_as(client, app, "verify_manager")

    response = client.post(f"/verification/{record_id}/approve", follow_redirects=True)
    assert b"Record approved and verified." in response.data
    with app.app_context():
        record = db.session.get(OperationRecord, record_id)
        assert record.status == "verified"
        assert record.verified_by == manager_id
        assert record.verified_at is not None
        audit = db.session.scalar(
            db.select(AuditLog).where(
                AuditLog.action == "verification.approve",
                AuditLog.entity_id == record_id,
            )
        )
        assert audit is not None
        assert audit.actor_user_id == manager_id


def test_user_cannot_verify_own_submission(client, app):
    operations_id, manager_id, _staff, employee_id = setup_verification_data(app)
    record_id = add_record(app, employee_id, operations_id, manager_id)
    authenticate_as(client, app, "verify_manager")

    assert client.post(f"/verification/{record_id}/approve").status_code == 403
    assert client.post(f"/verification/{record_id}/reject").status_code == 403
    with app.app_context():
        assert db.session.get(OperationRecord, record_id).status == "pending"


def test_reject_requires_reason_and_only_pending(client, app):
    operations_id, _manager, staff_id, employee_id = setup_verification_data(app)
    record_id = add_record(app, employee_id, operations_id, staff_id)
    authenticate_as(client, app, "verify_manager")

    response = client.post(f"/verification/{record_id}/reject", data={}, follow_redirects=True)
    assert b"rejection reason" in response.data
    with app.app_context():
        assert db.session.get(OperationRecord, record_id).status == "pending"

    response = client.post(
        f"/verification/{record_id}/reject",
        data={"rejection_reason": "Totals do not match."},
        follow_redirects=True,
    )
    assert b"Record rejected." in response.data
    with app.app_context():
        record = db.session.get(OperationRecord, record_id)
        assert record.status == "rejected"
        assert record.rejection_reason == "Totals do not match."
        audit = db.session.scalar(
            db.select(AuditLog).where(
                AuditLog.action == "verification.reject",
                AuditLog.entity_id == record_id,
            )
        )
        assert audit.details["reason"] == "Totals do not match."

    response = client.post(f"/verification/{record_id}/approve", follow_redirects=True)
    assert b"Only pending records can be approved." in response.data


def test_bulk_approve_and_reject(client, app):
    operations_id, _manager, staff_id, employee_id = setup_verification_data(app)
    first_id = add_record(app, employee_id, operations_id, staff_id)
    second_id = add_record(app, employee_id, operations_id, staff_id)
    third_id = add_record(app, employee_id, operations_id, staff_id)
    authenticate_as(client, app, "verify_manager")

    response = client.post(
        "/verification/bulk",
        data={"action": "approve", "record_ids": [str(first_id), str(second_id)]},
        follow_redirects=True,
    )
    assert b"2 record(s) approved." in response.data
    with app.app_context():
        assert db.session.get(OperationRecord, first_id).status == "verified"
        assert (
            db.session.scalar(
                db.select(AuditLog).where(
                    AuditLog.action == "verification.approve",
                    AuditLog.entity == "operation_record",
                )
            )
            is not None
        )

    response = client.post(
        "/verification/bulk",
        data={
            "action": "reject",
            "record_ids": [str(third_id)],
            "rejection_reason": "Duplicate entry.",
        },
        follow_redirects=True,
    )
    assert b"1 record(s) rejected." in response.data
    with app.app_context():
        assert db.session.get(OperationRecord, third_id).status == "rejected"

    response = client.post(
        "/verification/bulk", data={"action": "approve"}, follow_redirects=True
    )
    assert b"Select between 1 and 500" in response.data


def test_manager_is_scoped_to_own_department(client, app):
    operations_id, _manager, staff_id, employee_id = setup_verification_data(app)
    finance_id = add_department(app, "Finance")
    other_employee_id = add_employee(app, "VER002", finance_id)
    add_record(app, employee_id, operations_id, staff_id)
    other_record_id = add_record(app, other_employee_id, finance_id, staff_id)
    authenticate_as(client, app, "verify_manager")

    response = client.get("/verification/")
    assert "of 1 records".encode() in response.data
    assert client.post(f"/verification/{other_record_id}/approve").status_code == 403
    assert (
        client.post(
            "/verification/bulk",
            data={"action": "approve", "record_ids": [str(other_record_id)]},
        ).status_code
        == 403
    )


def test_operation_remarks_shown_in_table_and_modal(client, app):
    _dept, _manager, staff_id, employee_id = setup_verification_data(app)
    with app.app_context():
        record = OperationRecord(
            record_date=date.today(),
            employee_id=employee_id,
            department_id=2,
            task_operation="Order processing",
            total_records=50,
            completed=48,
            pending=2,
            status="pending",
            submitted_by=staff_id,
            remarks="Cross-checked against the source register.",
        )
        db.session.add(record)
        db.session.commit()
    authenticate_as(client, app, "verify_manager")

    response = client.get("/verification/")
    assert response.status_code == 200
    assert b"Remarks" in response.data
    assert b"Cross-checked against the source register." in response.data
    assert b'data-remarks="Cross-checked against the source register."' in response.data


def _correction_record_id(app, employee_id, department_id, submitted_by):
    with app.app_context():
        record = OperationRecord(
            record_date=date.today(),
            employee_id=employee_id,
            department_id=department_id,
            task_operation="Order processing",
            total_records=50,
            completed=40,
            pending=10,
            status="pending",
            submitted_by=submitted_by,
        )
        db.session.add(record)
        db.session.commit()
        return record.id


def test_request_correction_flow(client, app):
    operations_id, _manager, staff_id, employee_id = setup_verification_data(app)
    record_id = _correction_record_id(app, employee_id, operations_id, staff_id)
    authenticate_as(client, app, "verify_manager")

    response = client.post(
        f"/verification/{record_id}/request-correction",
        data={},
        follow_redirects=True,
    )
    assert b"correction note" in response.data
    with app.app_context():
        assert db.session.get(OperationRecord, record_id).status == "pending"

    response = client.post(
        f"/verification/{record_id}/request-correction",
        data={"correction_note": "Recheck the completed count."},
        follow_redirects=True,
    )
    assert b"Correction requested" in response.data
    with app.app_context():
        record = db.session.get(OperationRecord, record_id)
        assert record.status == "needs_correction"
        assert record.correction_note == "Recheck the completed count."
        audit = db.session.scalar(
            db.select(AuditLog).where(
                AuditLog.action == "verification.request_correction",
                AuditLog.entity_id == record_id,
            )
        )
        assert audit is not None
        assert audit.details["note"] == "Recheck the completed count."

    response = client.get("/verification/", query_string={"status": "needs_correction"})
    assert b"Needs Correction" in response.data
    assert b"Needs correction" in response.data


def test_request_correction_guards(client, app):
    operations_id, manager_id, staff_id, employee_id = setup_verification_data(app)
    own_id = _correction_record_id(app, employee_id, operations_id, manager_id)
    other_id = _correction_record_id(app, employee_id, operations_id, staff_id)
    authenticate_as(client, app, "verify_manager")

    assert client.post(f"/verification/{own_id}/request-correction").status_code == 403
    response = client.post(
        f"/verification/{other_id}/request-correction",
        data={"correction_note": "Fix it."},
        follow_redirects=True,
    )
    assert b"Correction requested" in response.data
    response = client.post(
        f"/verification/{other_id}/request-correction",
        data={"correction_note": "Again."},
        follow_redirects=True,
    )
    assert b"Only pending records can be sent back" in response.data

    authenticate_as(client, app, "verify_staff")
    assert client.post(f"/verification/{other_id}/request-correction").status_code == 403
    assert client.get(f"/verification/{other_id}/edit").status_code == 403


def test_edit_correction_resubmits_to_pending(client, app):
    operations_id, _manager, staff_id, employee_id = setup_verification_data(app)
    record_id = _correction_record_id(app, employee_id, operations_id, staff_id)
    authenticate_as(client, app, "verify_manager")
    client.post(
        f"/verification/{record_id}/request-correction",
        data={"correction_note": "Recheck the completed count."},
    )

    response = client.get(f"/verification/{record_id}/edit")
    assert response.status_code == 200
    assert b"Recheck the completed count." in response.data

    response = client.post(
        f"/verification/{record_id}/edit",
        data={
            "task_operation": "Order processing",
            "total_records": "50",
            "completed": "30",
            "pending": "30",
            "remarks": "",
        },
        follow_redirects=True,
    )
    assert b"must equal the total" in response.data
    with app.app_context():
        assert db.session.get(OperationRecord, record_id).status == "needs_correction"

    response = client.post(
        f"/verification/{record_id}/edit",
        data={
            "task_operation": "Order processing corrected",
            "total_records": "50",
            "completed": "45",
            "pending": "5",
            "remarks": "Fixed",
        },
        follow_redirects=True,
    )
    assert b"pending review again" in response.data
    with app.app_context():
        record = db.session.get(OperationRecord, record_id)
        assert record.status == "pending"
        assert record.completed == 45
        assert record.correction_note is None
        audit = db.session.scalar(
            db.select(AuditLog).where(
                AuditLog.action == "verification.edit",
                AuditLog.entity_id == record_id,
            )
        )
        assert audit is not None


def test_edit_guards_wrong_status_scope_and_self(client, app):
    operations_id, manager_id, staff_id, employee_id = setup_verification_data(app)
    pending_id = _correction_record_id(app, employee_id, operations_id, staff_id)
    own_id = _correction_record_id(app, employee_id, operations_id, manager_id)
    authenticate_as(client, app, "verify_manager")

    response = client.get(f"/verification/{pending_id}/edit", follow_redirects=True)
    assert b"Only records sent back for correction can be edited." in response.data
    assert client.get(f"/verification/{own_id}/edit").status_code == 403

    finance_id = add_department(app, "Finance")
    finance_employee = add_employee(app, "VER009", finance_id)
    finance_id_record = _correction_record_id(app, finance_employee, finance_id, staff_id)
    with app.app_context():
        record = db.session.get(OperationRecord, finance_id_record)
        record.status = "needs_correction"
        db.session.commit()
    assert client.get(f"/verification/{finance_id_record}/edit").status_code == 403
