from datetime import timedelta
import os
from dotenv import load_dotenv

# Resolve .env from the project root (one level above app/).
_ENV_PATH = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=_ENV_PATH, override=False)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "fallback-secret-key")

    has_remote_db = bool(os.environ.get("DATABASE_URL") or os.environ.get("MYSQL_URL"))
    raw_use_sqlite = os.environ.get("DB_USE_SQLITE")
    if raw_use_sqlite is not None:
        USE_SQLITE = raw_use_sqlite.lower() == "true"
    else:
        USE_SQLITE = not has_remote_db

    if USE_SQLITE:
        # Default development database.
        SQLALCHEMY_DATABASE_URI = (
            "sqlite:///" + os.path.join(BASE_DIR, "..", "instance", "smartqualihome.db")
        )
        SQLALCHEMY_ENGINE_OPTIONS = {}
    else:
        database_url = os.environ.get("DATABASE_URL") or os.environ.get("MYSQL_URL")
        if database_url:
            if database_url.startswith("mysql://"):
                database_url = database_url.replace("mysql://", "mysql+pymysql://", 1)
            SQLALCHEMY_DATABASE_URI = database_url
        else:
            DB_HOST = os.environ.get("DB_HOST", "localhost")
            DB_PORT = os.environ.get("DB_PORT", "3306")
            DB_USER = os.environ.get("DB_USER", "root")
            DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
            DB_NAME = os.environ.get("DB_NAME", "smartqualihome")
            SQLALCHEMY_DATABASE_URI = (
                f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
                "?charset=utf8mb4"
            )

        engine_options = {
            "pool_pre_ping": True,
            "pool_recycle": 180,
            # Aiven is high-latency (Bangalore) — keep a warm pool so worker
            # processes don't serialize on connection setup.
            "pool_size": 10,
            "max_overflow": 20,
        }

        # Aiven (and most managed MySQL providers) require TLS connections.
        # DB_SSL_CA can point to a CA cert file path (e.g., Render's /etc/secrets/aiven-ca.pem,
        # a relative path like aiven-ca.pem in the project root, or instance/aiven-ca.pem).
        raw_ssl_ca = os.environ.get("DB_SSL_CA", "").strip()
        resolved_ssl_ca = None
        if raw_ssl_ca:
            if os.path.exists(raw_ssl_ca):
                resolved_ssl_ca = os.path.abspath(raw_ssl_ca)
            else:
                root_ca = os.path.abspath(os.path.join(BASE_DIR, "..", os.path.basename(raw_ssl_ca)))
                instance_ca = os.path.abspath(os.path.join(BASE_DIR, "..", "instance", os.path.basename(raw_ssl_ca)))
                if os.path.exists(root_ca):
                    resolved_ssl_ca = root_ca
                elif os.path.exists(instance_ca):
                    resolved_ssl_ca = instance_ca

        if resolved_ssl_ca:
            engine_options["connect_args"] = {"ssl": {"ca": resolved_ssl_ca}}
        else:
            # Railway internal network (mysql.railway.internal) and local XAMPP do not
            # use a CA certificate. Disabling client SSL avoids certificate verification errors.
            engine_options["connect_args"] = {"ssl_disabled": True}

        SQLALCHEMY_ENGINE_OPTIONS = engine_options

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True

    # File uploads
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "instance", "uploads")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB

    # Cloudinary (persistent image storage — Render's disk is ephemeral).
    # When all three credentials are present, uploads go to Cloudinary and
    # absolute URLs are stored in the DB; otherwise the local uploads folder
    # is used (local development).
    CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
    CLOUDINARY_API_KEY    = os.environ.get("CLOUDINARY_API_KEY", "")
    CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")
    CLOUDINARY_FOLDER     = os.environ.get("CLOUDINARY_FOLDER", "qualihome")

    # Session cookie security
    _IS_PRODUCTION = os.environ.get("FLASK_ENV", "development").lower() == "production"

    SESSION_COOKIE_HTTPONLY  = True
    SESSION_COOKIE_SAMESITE  = "Lax"
    # True when FLASK_ENV=production (Render serves over HTTPS).
    # Stays False for local http://localhost development.
    SESSION_COOKIE_SECURE    = _IS_PRODUCTION
    # Session lifetime (used when session.permanent = True)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)
    # Inactivity timeout in minutes (enforced in app.before_request).
    SESSION_TIMEOUT_MINS     = 60

    # Remember Me: keep session alive for 30 days
    REMEMBER_COOKIE_DURATION  = timedelta(days=30)
    REMEMBER_COOKIE_HTTPONLY  = True
    REMEMBER_COOKIE_SAMESITE  = "Lax"
    REMEMBER_COOKIE_SECURE    = _IS_PRODUCTION

    # SMTP mail settings (used by forgot/reset password)
    MAIL_SERVER    = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT      = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME  = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD  = os.environ.get("MAIL_PASSWORD", "")
    MAIL_USE_TLS   = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USE_SSL   = os.environ.get("MAIL_USE_SSL", "false").lower() == "true"
    MAIL_FROM      = os.environ.get("MAIL_FROM", MAIL_USERNAME or "no-reply@qualihome.local")