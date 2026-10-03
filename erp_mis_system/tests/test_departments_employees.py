import time
from datetime import date
from io import BytesIO

from openpyxl import Workbook

from app.extensions import db
from app.models import AuditLog, Department, Employee, User


def authenticated_as(client, app, username):
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


def add_department(app, name):
    with app.app_context():
        department = Department(name=name)
        db.session.add(department)
        db.session.commit()
        return department.id


def create_employee(client, code, department_id="1"):
    return client.post(
        "/employees/create",
        data={
            "employee_code": code,
            "full_name": f"Employee {code}",
            "email": f"{code.lower()}@example.com",
            "phone": "+1 555 010 1234",
            "designation": "Analyst",
            "department_id": department_id,
            "joining_date": "2024-01-15",
            "status": "active",
        },
        follow_redirects=True,
    )


def make_workbook(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "employee_code",
            "full_name",
            "email",
            "phone",
            "designation",
            "department",
            "joining_date",
            "status",
        ]
    )
    for row in rows:
        sheet.append(row)
    content = BytesIO()
    workbook.save(content)
    content.seek(0)
    return content


def test_admin_department_crud_and_cannot_delete_assigned_department(client, app):
    authenticated_as(client, app, "admin")
    response = client.post(
        "/departments/create",
        data={"name": "Finance", "is_active": "y"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Department Finance was created" in response.data

    with app.app_context():
        department = db.session.scalar(db.select(Department).where(Department.name == "Finance"))
        finance_id = department.id
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "department.create")
        )

    response = client.post(
        f"/departments/{finance_id}/edit",
        data={"name": "Finance & Planning", "is_active": "y"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Finance &amp; Planning" in response.data

    with app.app_context():
        department = db.session.get(Department, finance_id)
        assert department.name == "Finance & Planning"
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "department.update")
        )

    create_employee(client, "FIN001", str(finance_id))
    response = client.post(f"/departments/{finance_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert b"cannot be deleted while employees or user accounts are assigned" in response.data
    with app.app_context():
        assert db.session.get(Department, finance_id) is not None


def test_department_name_is_unique_case_insensitively(client, app):
    authenticated_as(client, app, "admin")
    response = client.post(
        "/departments/create",
        data={"name": "General", "is_active": "y"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"department with that name already exists" in response.data


def test_admin_can_delete_empty_department_and_action_is_audited(client, app):
    department_id = add_department(app, "Temporary")
    authenticated_as(client, app, "admin")
    response = client.post(f"/departments/{department_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert b"Department Temporary was deleted" in response.data
    with app.app_context():
        assert db.session.get(Department, department_id) is None
        event = db.session.scalar(
            db.select(AuditLog).where(
                AuditLog.action == "department.delete",
                AuditLog.entity_id == department_id,
            )
        )
        assert event is not None


def test_employee_crud_soft_delete_and_audit(client, app):
    authenticated_as(client, app, "admin")
    response = create_employee(client, "EMP001")
    assert response.status_code == 200
    assert b"Employee EMP001 was created" in response.data

    with app.app_context():
        employee = db.session.scalar(
            db.select(Employee).where(Employee.employee_code == "EMP001")
        )
        assert employee.created_by is not None
        assert employee.joining_date == date(2024, 1, 15)
        employee_id = employee.id
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "employee.create")
        )

    response = client.post(
        f"/employees/{employee_id}/edit",
        data={
            "employee_code": "EMP001",
            "full_name": "Updated Employee",
            "email": "updated@example.com",
            "phone": "555-010-8899",
            "designation": "Senior Analyst",
            "department_id": "1",
            "joining_date": "2024-01-15",
            "status": "inactive",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        employee = db.session.get(Employee, employee_id)
        assert employee.full_name == "Updated Employee"
        assert employee.status == "inactive"
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "employee.update")
        )

    response = client.post(f"/employees/{employee_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        employee = db.session.get(Employee, employee_id)
        assert employee.deleted_at is not None
        assert employee.deleted_by is not None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "employee.soft_delete")
        )
    assert b"EMP001" not in client.get("/employees/").data


def test_employee_validation_rejects_bad_email_phone_and_duplicate_code(client, app):
    authenticated_as(client, app, "admin")
    invalid = client.post(
        "/employees/create",
        data={
            "employee_code": "BAD001",
            "full_name": "Invalid Employee",
            "email": "not-an-email",
            "phone": "letters-only",
            "designation": "Analyst",
            "department_id": "1",
            "joining_date": "2024-01-15",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert b"Enter a valid email address" in invalid.data
    assert b"Enter a valid phone number" in invalid.data
    with app.app_context():
        assert db.session.scalar(
            db.select(Employee.id).where(Employee.employee_code == "BAD001")
        ) is None

    create_employee(client, "DUP001")
    duplicate = create_employee(client, "DUP001")
    assert b"employee code is already in use" in duplicate.data


def test_employee_list_filter_sort_and_page(client, app):
    authenticated_as(client, app, "admin")
    create_employee(client, "SORT002")
    create_employee(client, "SORT001")

    response = client.get("/employees/?q=SORT&status=active&sort=employee_code&direction=asc")
    assert response.status_code == 200
    assert response.data.index(b"SORT001") < response.data.index(b"SORT002")


def test_data_entry_is_scoped_to_department_and_only_edits_own_created_rows(client, app):
    second_department_id = add_department(app, "Operations")
    add_user(app, "entry_one", "data_entry", 1)
    add_user(app, "entry_two", "data_entry", second_department_id)
    authenticated_as(client, app, "entry_one")
    create_employee(client, "OWN001")

    with app.app_context():
        own_employee = db.session.scalar(
            db.select(Employee).where(Employee.employee_code == "OWN001")
        )
        own_id = own_employee.id
        assert own_employee.department_id == 1

    client.post("/logout")
    authenticated_as(client, app, "admin")
    create_employee(client, "OTHER001", str(second_department_id))
    with app.app_context():
        other_id = db.session.scalar(
            db.select(Employee.id).where(Employee.employee_code == "OTHER001")
        )

    client.post("/logout")
    authenticated_as(client, app, "entry_one")
    response = client.get("/employees/")
    assert b"OWN001" in response.data
    assert b"OTHER001" not in response.data
    assert client.get(f"/employees/{other_id}/edit").status_code == 403

    client.post("/logout")
    authenticated_as(client, app, "entry_two")
    response = client.get("/employees/")
    assert b"OTHER001" in response.data
    assert b"OWN001" not in response.data
    assert client.get(f"/employees/{own_id}/edit").status_code == 403


def test_manager_department_scoping_and_employee_department_filter(client, app):
    second_department_id = add_department(app, "Operations")
    add_user(app, "manager_scoped", "manager", 1)
    authenticated_as(client, app, "admin")
    create_employee(client, "MGRGEN001")
    create_employee(client, "MGROPS001", str(second_department_id))

    client.post("/logout")
    authenticated_as(client, app, "manager_scoped")
    response = client.get("/employees/")
    assert b"MGRGEN001" in response.data
    assert b"MGROPS001" not in response.data
    assert client.get(f"/employees/?department_id={second_department_id}").status_code == 403
    departments_response = client.get("/departments/")
    assert b"General" in departments_response.data
    assert b"Operations" not in departments_response.data


def test_manager_and_data_entry_cannot_delete_employees(client, app):
    second_department_id = add_department(app, "Sales")
    add_user(app, "manager_one", "manager", 1)
    add_user(app, "entry_one", "data_entry", 1)
    authenticated_as(client, app, "admin")
    create_employee(client, "NODEL001")
    with app.app_context():
        employee_id = db.session.scalar(
            db.select(Employee.id).where(Employee.employee_code == "NODEL001")
        )

    for username in ("manager_one", "entry_one"):
        client.post("/logout")
        authenticated_as(client, app, username)
        assert client.post(f"/employees/{employee_id}/delete").status_code == 403


def test_employee_import_reports_each_row_and_audits_successes(client, app):
    authenticated_as(client, app, "admin")
    workbook = make_workbook(
        [
            ["IMP001", "Import Good", "good@example.com", "+1 555 010 1234", "Analyst", "General", "2024-01-15", "active"],
            ["", "Missing Code", "bad@example.com", "", "Analyst", "General", "2024-01-15", "active"],
            ["IMP001", "Duplicate Code", "dup@example.com", "", "Analyst", "General", "2024-01-15", "active"],
            ["IMP004", "Bad Email", "not-email", "", "Analyst", "General", "2024-01-15", "active"],
        ]
    )
    response = client.post(
        "/employees/import",
        data={"file": (workbook, "employees.xlsx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"Row 2" not in response.data
    assert b"Imported IMP001" in response.data
    assert b"employee_code is required" in response.data
    assert b"employee_code already exists" in response.data or b"duplicated in this workbook" in response.data
    assert b"email is invalid" in response.data
    with app.app_context():
        assert db.session.scalar(
            db.select(Employee.id).where(Employee.employee_code == "IMP001")
        )
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "employee.import")
        )


def test_data_entry_employee_import_forces_own_department(client, app):
    second_department_id = add_department(app, "Operations")
    add_user(app, "entry_import", "data_entry", 1)
    authenticated_as(client, app, "entry_import")
    workbook = make_workbook(
        [["IMPDEP01", "Department Import", "dept@example.com", "", "Clerk", "Operations", "2023-06-01", "active"]]
    )
    response = client.post(
        "/employees/import",
        data={"file": (workbook, "employees.xlsx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"Imported IMPDEP01" in response.data
    with app.app_context():
        employee = db.session.scalar(
            db.select(Employee).where(Employee.employee_code == "IMPDEP01")
        )
        assert employee.department_id == 1
        assert employee.department_id != second_department_id
