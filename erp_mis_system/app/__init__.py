from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import logging

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, logout_user

from .config import DevelopmentConfig, ProductionConfig
from .extensions import csrf, db, limiter, login_manager


def create_app(config_class=None):
    app = Flask(__name__)
    initialize_database = config_class is None
    if config_class is None:
        config_class = (
            DevelopmentConfig
            if os.getenv("APP_ENV", "production").lower() == "development"
            else ProductionConfig
        )
    app.config.from_object(config_class)
    if not app.testing and not app.config.get("ALLOW_DEBUG", False):
        app.config["DEBUG"] = False
    if app.config.get("TRUST_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        trusted_hops = app.config.get("PROXY_FIX_HOPS", 1)
        if not isinstance(trusted_hops, int) or trusted_hops < 1:
            raise RuntimeError("PROXY_FIX_HOPS must be a positive integer.")
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=trusted_hops,
            x_proto=trusted_hops,
            x_host=trusted_hops,
        )

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("Set SECRET_KEY in your environment before starting the app.")
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError("Set DATABASE_URL in your environment before starting the app.")

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    app.config.setdefault("RATELIMIT_LOGIN", "10 per minute")
    app.config.setdefault("RATELIMIT_REGISTER", "5 per minute")
    limiter.init_app(app)

    if initialize_database and not app.config.get("TESTING", False):
        with app.app_context():
            from .models import Department, User

            db.create_all()

            default_department_names = ["HR", "Finance", "Sales", "Purchase", "Operations"]
            departments = {}
            for department_name in default_department_names:
                department = db.session.scalar(
                    db.select(Department).where(Department.name == department_name)
                )
                if department is None:
                    department = Department(name=department_name, is_active=True)
                    db.session.add(department)
                    db.session.flush()
                departments[department_name] = department

            primary_department = departments.get("HR") or db.session.scalar(
                db.select(Department).order_by(Department.id)
            )

            admin_username = (os.getenv("ADMIN_USERNAME") or "").strip()
            admin_email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
            if admin_username or admin_email:
                admin = db.session.scalar(
                    db.select(User).where(
                        (User.username == admin_username) | (User.email == admin_email)
                    )
                )
                if admin is None:
                    admin = User(
                        full_name=os.getenv("ADMIN_FULL_NAME", "System Administrator").strip(),
                        username=admin_username or "admin",
                        email=admin_email or "admin@example.com",
                        department_id=primary_department.id if primary_department else None,
                        role="admin",
                        status="active",
                    )
                    admin.set_password(os.getenv("ADMIN_PASSWORD", "Admin-Pass-1234!"))
                    db.session.add(admin)
            db.session.commit()

    log_directory = Path(app.instance_path) / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "erp_mis.log"
    if not any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", None) == str(log_path.resolve())
        for handler in app.logger.handlers
    ):
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access that page."
    login_manager.login_message_category = "info"

    from .auth import auth_bp
    from .main import main_bp
    from .departments import departments_bp
    from .employees import employees_bp
    from .attendance import attendance_bp
    from .data_quality import data_quality_bp
    from .reports import reports_bp
    from .users import users_bp
    from .verification import verification_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(departments_bp)
    app.register_blueprint(employees_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(data_quality_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(verification_bp)

    @app.cli.command("seed-verification")
    def seed_verification_command():
        """Insert sample operation records (development only, never automatic)."""
        import click

        if os.getenv("APP_ENV", "production").lower() != "development":
            raise click.ClickException(
                "seed-verification is only available with APP_ENV=development."
            )
        from .verification.routes import seed_verification_records

        _count, message = seed_verification_records()
        click.echo(message)

    @app.cli.command("seed-attendance")
    def seed_attendance_command():
        """Generate sample attendance for the last 30 days (development only)."""
        import click

        if os.getenv("APP_ENV", "production").lower() != "development":
            raise click.ClickException(
                "seed-attendance is only available with APP_ENV=development."
            )
        from .attendance.routes import seed_attendance_records

        _count, message = seed_attendance_records()
        click.echo(message)

    @app.context_processor
    def inject_current_year():
        return {"current_year": datetime.now(timezone.utc).year}

    @app.before_request
    def enforce_idle_timeout():
        if not current_user.is_authenticated:
            return None

        if current_user.status != "active":
            from .utils.audit import log_action

            log_action(
                current_user,
                "auth.session_revoked",
                "user",
                current_user.id,
                {"reason": "account_inactive"},
            )
            db.session.commit()
            logout_user()
            session.clear()
            flash("This account is no longer active. Please contact an administrator.", "warning")
            return redirect(url_for("auth.login"))

        now = datetime.now(timezone.utc).timestamp()
        last_activity = session.get("last_activity")
        timeout_seconds = app.config["PERMANENT_SESSION_LIFETIME"].total_seconds()

        if last_activity is not None and now - last_activity > timeout_seconds:
            from .utils.audit import log_action

            log_action(
                current_user,
                "auth.session_expired",
                "user",
                current_user.id,
                {"reason": "idle_timeout"},
            )
            db.session.commit()
            logout_user()
            session.clear()
            flash("You were logged out after 30 minutes of inactivity. Please log in again.", "warning")
            return redirect(url_for("auth.login"))

        session["last_activity"] = now
        session.permanent = True
        if (
            current_user.must_change_password
            and request.endpoint not in {"auth.change_password", "auth.logout", "static"}
        ):
            flash("Change your temporary password to continue.", "warning")
            return redirect(url_for("auth.change_password"))
        return None

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(429)
    def too_many_requests(_error):
        return render_template("errors/429.html"), 429

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        original_exception = getattr(error, "original_exception", None)
        logged_error = original_exception or error
        app.logger.error(
            "Unhandled application error: %s",
            logged_error,
            exc_info=(
                type(logged_error),
                logged_error,
                logged_error.__traceback__,
            ),
        )
        db.session.rollback()
        return render_template("errors/500.html"), 500

    return app
