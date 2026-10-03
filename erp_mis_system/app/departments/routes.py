from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Department, Employee, User
from ..utils.audit import log_action
from ..utils.decorators import roles_required
from . import departments_bp
from .forms import DepartmentForm

PER_PAGE = 20


@departments_bp.route("/")
@roles_required("admin", "manager", "data_entry")
def index():
    search = request.args.get("q", "").strip()[:120]
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    query = db.select(Department).order_by(Department.name.asc(), Department.id.asc())

    if current_user.role == "data_entry":
        query = query.where(Department.id == current_user.department_id)
    elif current_user.role == "manager":
        query = query.where(Department.id == current_user.department_id)

    if search:
        query = query.where(Department.name.ilike(f"%{search}%"))

    pagination = db.paginate(query, page=page, per_page=PER_PAGE, error_out=False)
    return render_template("departments/index.html", pagination=pagination, search=search)


@departments_bp.route("/create", methods=["GET", "POST"])
@roles_required("admin")
def create():
    form = DepartmentForm()
    form.is_active.data = True if request.method == "GET" else form.is_active.data
    if form.validate_on_submit():
        name = form.name.data.strip()
        existing = db.session.scalar(
            db.select(Department.id).where(func.lower(Department.name) == name.lower())
        )
        if existing is not None:
            flash("A department with that name already exists.", "danger")
        else:
            department = Department(name=name, is_active=form.is_active.data)
            db.session.add(department)
            try:
                db.session.flush()
                log_action(
                    current_user,
                    "department.create",
                    "department",
                    department.id,
                    {"name": department.name, "is_active": department.is_active},
                )
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("A department with that name already exists.", "danger")
            else:
                flash(f"Department {department.name} was created.", "success")
                return redirect(url_for("departments.index"))

    if request.method == "POST":
        for errors in form.errors.values():
            for message in errors:
                flash(message, "danger")
    return render_template("departments/form.html", form=form, department=None)


@departments_bp.route("/<int:department_id>/edit", methods=["GET", "POST"])
@roles_required("admin")
def edit(department_id):
    department = db.get_or_404(Department, department_id)
    form = DepartmentForm(obj=department)
    if form.validate_on_submit():
        name = form.name.data.strip()
        duplicate = db.session.scalar(
            db.select(Department.id).where(
                func.lower(Department.name) == name.lower(),
                Department.id != department.id,
            )
        )
        if duplicate is not None:
            flash("A department with that name already exists.", "danger")
        else:
            previous = {"name": department.name, "is_active": department.is_active}
            department.name = name
            department.is_active = form.is_active.data
            log_action(
                current_user,
                "department.update",
                "department",
                department.id,
                {"before": previous, "after": {"name": name, "is_active": department.is_active}},
            )
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("A department with that name already exists.", "danger")
            else:
                flash(f"Department {department.name} was updated.", "success")
                return redirect(url_for("departments.index"))

    if request.method == "POST":
        for errors in form.errors.values():
            for message in errors:
                flash(message, "danger")
    return render_template("departments/form.html", form=form, department=department)


@departments_bp.route("/<int:department_id>/delete", methods=["POST"])
@roles_required("admin")
def delete(department_id):
    department = db.get_or_404(Department, department_id)
    employee_count = db.session.scalar(
        db.select(func.count(Employee.id)).where(Employee.department_id == department.id)
    )
    user_count = db.session.scalar(
        db.select(func.count(User.id)).where(User.department_id == department.id)
    )
    if employee_count or user_count:
        flash(
            "This department cannot be deleted while employees or user accounts are assigned to it.",
            "danger",
        )
        return redirect(url_for("departments.index"))

    department_id_value = department.id
    department_name = department.name
    log_action(
        current_user,
        "department.delete",
        "department",
        department_id_value,
        {"name": department_name},
    )
    db.session.delete(department)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            "This department cannot be deleted while employees or user accounts are assigned to it.",
            "danger",
        )
        return redirect(url_for("departments.index"))
    flash(f"Department {department_name} was deleted.", "success")
    return redirect(url_for("departments.index"))
