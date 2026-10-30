import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _sqlite_fallback_uri() -> str:
    db_path = Path(__file__).resolve().parent.parent / "instance" / "erp_mis_system.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


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
    # Plain string (not a @property) so Flask stores a real URL string that
    # Flask-SQLAlchemy can consume; tests override this with their own SQLite.
    _database_url = os.getenv("DATABASE_URL") or _sqlite_fallback_uri()
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)
    if _database_url.startswith("postgresql://") and "+psycopg" not in _database_url:
        _database_url = _database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    SQLALCHEMY_DATABASE_URI = _database_url
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
