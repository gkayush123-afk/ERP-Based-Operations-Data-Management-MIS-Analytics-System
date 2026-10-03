from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError

from ..extensions import db
from ..models import Department, User
from ..utils.audit import log_action
from ..utils.decorators import roles_required
from . import auth_bp
from .forms import ChangePasswordForm, LoginForm, RegistrationForm

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION = timedelta(minutes=15)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def flash_form_errors(form):
    for errors in form.errors.values():
        for message in errors:
            flash(message, "danger")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        user = db.session.scalar(
            db.select(User)
            .where(func.lower(User.username) == username.lower())
            .with_for_update()
        )

        if user is None:
            log_action(
                None,
                "auth.login_failed",
                "user",
                None,
                {"username": username[:80], "reason": "invalid_credentials"},
            )
            db.session.commit()
            flash("Invalid username or password.", "danger")
            return render_template("auth/login.html", form=form)

        now = utcnow()
        if user.locked_until and user.locked_until > now:
            log_action(
                user,
                "auth.login_locked",
                "user",
                user.id,
                {"username": user.username},
            )
            db.session.commit()
            unlock_time = user.locked_until.strftime("%H:%M UTC")
            flash(f"This account is temporarily locked. Try again after {unlock_time}.", "warning")
            return render_template("auth/login.html", form=form)

        if user.locked_until and user.locked_until <= now:
            user.failed_attempts = 0
            user.locked_until = None

        if not user.check_password(form.password.data):
            user.failed_attempts += 1
            log_action(
                user,
                "auth.login_failed",
                "user",
                user.id,
                {"username": user.username, "failed_attempts": user.failed_attempts},
            )
            if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = now + LOCK_DURATION
                db.session.commit()
                flash("Too many failed attempts. Your account is locked for 15 minutes.", "danger")
            else:
                remaining = MAX_FAILED_ATTEMPTS - user.failed_attempts
                db.session.commit()
                flash(
                    f"Invalid username or password. {remaining} attempt(s) remain before a 15-minute lock.",
                    "danger",
                )
            return render_template("auth/login.html", form=form)

        user.failed_attempts = 0
        user.locked_until = None
        if user.status == "pending":
            log_action(user, "auth.login_denied", "user", user.id, {"status": user.status})
            db.session.commit()
            flash("Your account is waiting for admin approval.", "warning")
            return render_template("auth/login.html", form=form)
        if user.status == "rejected":
            log_action(user, "auth.login_denied", "user", user.id, {"status": user.status})
            db.session.commit()
            flash("Your registration was not approved. Please contact an administrator.", "danger")
            return render_template("auth/login.html", form=form)
        if user.status != "active":
            log_action(user, "auth.login_denied", "user", user.id, {"status": user.status})
            db.session.commit()
            flash("This account is deactivated. Please contact an administrator.", "danger")
            return render_template("auth/login.html", form=form)

        db.session.commit()
        login_user(user, remember=form.remember.data, fresh=True)
        log_action(user, "auth.login", "user", user.id, {"username": user.username})
        db.session.commit()
        session.permanent = True
        session["last_activity"] = datetime.now(timezone.utc).timestamp()
        flash(f"Welcome back, {user.full_name}.", "success")

        if user.must_change_password:
            return redirect(url_for("auth.change_password"))

        next_page = request.args.get("next", "")
        parsed_next = urlsplit(next_page)
        if (
            next_page.startswith("/")
            and not next_page.startswith("//")
            and "\\" not in next_page
            and not parsed_next.scheme
            and not parsed_next.netloc
        ):
            return redirect(next_page)
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        flash_form_errors(form)
    return render_template("auth/login.html", form=form)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegistrationForm()
    try:
        departments = db.session.scalars(
            db.select(Department).where(Department.is_active.is_(True)).order_by(Department.name)
        ).all()
    except OperationalError:
        db.session.rollback()
        current_app.logger.exception("Database unavailable while loading registration departments.")
        flash(
            "Registration is temporarily unavailable because the database cannot be reached.",
            "danger",
        )
        return render_template("auth/register.html", form=form, departments=[]), 503

    form.department_id.choices = [(department.id, department.name) for department in departments]

    if form.validate_on_submit():
        username = form.username.data.strip()
        email = form.email.data.strip().lower()

        if not departments:
            flash("Registration is temporarily unavailable because no departments are configured.", "warning")
            return render_template("auth/register.html", form=form, departments=departments)

        try:
            if db.session.scalar(
                db.select(User.id).where(func.lower(User.username) == username.lower())
            ):
                flash("That username is already in use. Please choose another.", "danger")
                return render_template("auth/register.html", form=form, departments=departments)
            if db.session.scalar(
                db.select(User.id).where(func.lower(User.email) == email)
            ):
                flash(
                    "An account with that email already exists. Please use another email.",
                    "danger",
                )
                return render_template("auth/register.html", form=form, departments=departments)

            user = User(
                full_name=form.full_name.data.strip(),
                username=username,
                email=email,
                department_id=form.department_id.data,
                role="data_entry",
                status="pending",
            )
            user.set_password(form.password.data)
            db.session.add(user)
            try:
                db.session.flush()
                log_action(
                    user,
                    "user.register",
                    "user",
                    user.id,
                    {
                        "username": user.username,
                        "department_id": user.department_id,
                        "status": user.status,
                    },
                )
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash(
                    "That username or email is already registered. Please check your details.",
                    "danger",
                )
                return render_template("auth/register.html", form=form, departments=departments)
        except OperationalError:
            db.session.rollback()
            current_app.logger.exception("Database unavailable while processing registration.")
            flash(
                "Registration could not be completed because the database cannot be reached.",
                "danger",
            )
            return render_template("auth/register.html", form=form, departments=departments), 503

        flash("Your account is waiting for admin approval.", "success")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        flash_form_errors(form)
    return render_template("auth/register.html", form=form, departments=departments)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    if current_user.is_authenticated:
        log_action(
            current_user,
            "auth.logout",
            "user",
            current_user.id,
            {"username": current_user.username},
        )
        db.session.commit()
        logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.index"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@roles_required("admin", "manager", "data_entry")
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        current_user.set_password(form.new_password.data)
        current_user.must_change_password = False
        from ..utils.audit import log_action

        log_action(
            current_user,
            "user.password_changed",
            "user",
            current_user.id,
            {"password_changed": True},
        )
        db.session.commit()
        flash("Your password was changed successfully.", "success")
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        flash_form_errors(form)
    return render_template("auth/change_password.html", form=form)


@auth_bp.route("/admin/approvals/<int:user_id>/<decision>", methods=["POST"])
@roles_required("admin")
def decide_registration(user_id, decision):
    if decision not in {"approve", "reject"}:
        abort(404)

    user = db.session.get(User, user_id)
    if user is None or user.status != "pending":
        flash("That pending registration could not be found.", "warning")
        return redirect(url_for("main.dashboard"))

    user.status = "active" if decision == "approve" else "rejected"
    user.approved_by = current_user.id
    user.approved_at = utcnow()
    from ..utils.audit import log_action

    log_action(
        current_user,
        f"user.{decision}",
        "user",
        user.id,
        {"username": user.username, "status": user.status},
    )
    db.session.commit()
    action = "approved" if decision == "approve" else "rejected"
    flash(f"Registration for {user.username} was {action}.", "success")
    return redirect(url_for("main.dashboard"))
