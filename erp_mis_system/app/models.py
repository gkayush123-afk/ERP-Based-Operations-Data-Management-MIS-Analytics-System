from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import JSON, CheckConstraint, Index, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager

ID_TYPE = db.BigInteger().with_variant(db.Integer, "sqlite")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(ID_TYPE, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    users = db.relationship("User", back_populates="department")
    employees = db.relationship("Employee", back_populates="department")


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        db.CheckConstraint(
            "role IN ('admin', 'manager', 'data_entry')",
            name="ck_users_role",
        ),
        db.CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'disabled')",
            name="ck_users_status",
        ),
    )

    id = db.Column(ID_TYPE, primary_key=True, autoincrement=True)
    full_name = db.Column(db.String(160), nullable=False)
    username = db.Column(db.String(80), nullable=False, unique=True, index=True)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    department_id = db.Column(
        ID_TYPE,
        db.ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    role = db.Column(db.String(20), nullable=False, default="data_entry")
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    failed_attempts = db.Column(db.SmallInteger, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    approved_by = db.Column(ID_TYPE, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    department = db.relationship("Department", back_populates="users")
    approver = db.relationship("User", remote_side=[id], foreign_keys=[approved_by])

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.status == "active"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_actor_occurred", "actor_user_id", "occurred_at"),
        Index("ix_audit_action_occurred", "action", "occurred_at"),
    )

    id = db.Column(ID_TYPE, primary_key=True, autoincrement=True)
    actor_user_id = db.Column(
        ID_TYPE,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action = db.Column(db.String(80), nullable=False, index=True)
    entity = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(ID_TYPE, nullable=True, index=True)
    details = db.Column(JSON, nullable=False, default=dict)
    occurred_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    ip_address = db.Column(db.String(45), nullable=True)

    actor = db.relationship("User", foreign_keys=[actor_user_id])


class Employee(db.Model):
    __tablename__ = "employees"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_employees_status",
        ),
        Index("ix_employees_department_active", "department_id", "status", "deleted_at"),
    )

    id = db.Column(ID_TYPE, primary_key=True, autoincrement=True)
    employee_code = db.Column(db.String(40), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(254), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    designation = db.Column(db.String(120), nullable=False)
    department_id = db.Column(
        ID_TYPE,
        db.ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    joining_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    created_by = db.Column(
        ID_TYPE,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)
    deleted_by = db.Column(
        ID_TYPE,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    department = db.relationship("Department", back_populates="employees")
    creator = db.relationship("User", foreign_keys=[created_by])
    deleter = db.relationship("User", foreign_keys=[deleted_by])


class AttendanceRecord(db.Model):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("employee_id", "attendance_date", name="uq_attendance_employee_date"),
        CheckConstraint(
            "status IN ('present', 'absent', 'leave', 'half_day')",
            name="ck_attendance_status",
        ),
        CheckConstraint(
            "verification_status IN ('pending', 'verified', 'rejected')",
            name="ck_attendance_verification_status",
        ),
        CheckConstraint(
            "in_time IS NULL OR out_time IS NULL OR out_time >= in_time",
            name="ck_attendance_time_order",
        ),
        Index("ix_attendance_date_verification", "attendance_date", "verification_status"),
        Index("ix_attendance_date_status", "attendance_date", "status"),
    )

    id = db.Column(ID_TYPE, primary_key=True, autoincrement=True)
    employee_id = db.Column(
        ID_TYPE,
        db.ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False)
    in_time = db.Column(db.Time, nullable=True)
    out_time = db.Column(db.Time, nullable=True)
    remarks = db.Column(db.String(1000), nullable=True)
    verification_status = db.Column(
        db.String(20),
        nullable=False,
        default="pending",
        index=True,
    )
    rejection_reason = db.Column(db.String(500), nullable=True)
    verified_by = db.Column(
        ID_TYPE,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    verified_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(
        ID_TYPE,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    employee = db.relationship("Employee", backref="attendance_records")
    verifier = db.relationship("User", foreign_keys=[verified_by])
    creator = db.relationship("User", foreign_keys=[created_by])


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
