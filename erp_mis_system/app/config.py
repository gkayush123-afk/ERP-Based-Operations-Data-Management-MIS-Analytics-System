import os
import warnings
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _sqlite_fallback_uri() -> str:
    db_path = Path(__file__).resolve().parent.parent / "instance" / "erp_mis_system.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def _resolve_database_uri() -> str:
    configured_uri = os.getenv("DATABASE_URL")
    if not configured_uri:
        return _sqlite_fallback_uri()

    try:
        engine = create_engine(configured_uri, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return configured_uri
    except Exception as exc:
        if os.getenv("ALLOW_SQLITE_FALLBACK", "true").lower() not in {"1", "true", "yes", "on"}:
            raise

        warnings.warn(
            "DATABASE_URL is unreachable; falling back to a local SQLite database for this session. "
            f"Original error: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return _sqlite_fallback_uri()


def _engine_options() -> dict:
    """Connection-pool options. QueuePool-only knobs are skipped for SQLite."""
    options: dict = {
        "pool_pre_ping": True,
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "3600")),
    }
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith("sqlite"):
        options["pool_size"] = int(os.getenv("DB_POOL_SIZE", "5"))
        options["max_overflow"] = int(os.getenv("DB_POOL_MAX_OVERFLOW", "10"))
    return options


class Config:
    DEBUG = False
    ALLOW_DEBUG = False
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _resolve_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options()
    WTF_CSRF_TIME_LIMIT = 3600
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_DURATION = timedelta(minutes=30)
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    TRUST_PROXY = False
    PROXY_FIX_HOPS = 1


class ProductionConfig(Config):
    DEBUG = False
    ALLOW_DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"
    PROXY_FIX_HOPS = int(os.getenv("PROXY_FIX_HOPS", "1"))


class DevelopmentConfig(Config):
    DEBUG = True
    ALLOW_DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
