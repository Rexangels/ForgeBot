"""Django settings for ForgeBot Phase 1.

Defaults run on SQLite + local filesystem so a one-person demo works with
no Docker. Point FORGEBOT_DATABASE_URL at PostgreSQL and
FORGEBOT_MEDIA_BACKEND=s3 for the multi-service shape; the storage and
queue boundaries stay behind small interfaces so they remain replaceable.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load the ignored local development file for `manage.py runserver`. Compose
# also injects this file through env_file; setdefault keeps process variables
# (including deployed secret-manager values) authoritative.
_LOCAL_ENV = BASE_DIR / ".env"
if _LOCAL_ENV.is_file():
    for _line in _LOCAL_ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _value = _line.split("=", 1)
        _key, _value = _key.strip(), _value.strip()
        if len(_value) >= 2 and _value[0] == _value[-1] and _value[0] in "\"'":
            _value = _value[1:-1]
        if _key:
            os.environ.setdefault(_key, _value)

SECRET_KEY = os.environ.get("FORGEBOT_SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("FORGEBOT_DEBUG", "1") == "1"
ALLOWED_HOSTS = [
    h for h in os.environ.get("FORGEBOT_HOSTS", "localhost,127.0.0.1").split(",") if h
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "core",
]

# The Vite dev server (default :5173) talks to Django through a proxy, so the
# browser's Origin differs from Django's own host:port. Trust it explicitly
# (CSRF protection itself stays on).
CSRF_TRUSTED_ORIGINS = [
    o
    for o in os.environ.get(
        "FORGEBOT_CSRF_TRUSTED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Database: SQLite by default; PostgreSQL is selected by env in deploy docs.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
_pg = os.environ.get("FORGEBOT_DATABASE_URL", "")
if _pg.startswith("postgres"):
    # Minimal URL parse without extra deps: postgres://user:pass@host:port/db
    from urllib.parse import urlparse

    u = urlparse(_pg)
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": (u.path or "/forgebot").lstrip("/"),
        "USER": u.username or "",
        "PASSWORD": u.password or "",
        "HOST": u.hostname or "",
        "PORT": str(u.port or ""),
    }

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MEDIA_ROOT = os.environ.get("FORGEBOT_MEDIA_ROOT", str(BASE_DIR / "media"))
MEDIA_BACKEND = os.environ.get("FORGEBOT_MEDIA_BACKEND", "local")  # local | s3

# Demo guardrails (enforced at the API boundary).
FORGEBOT_MAX_UPLOAD_MB = float(os.environ.get("FORGEBOT_MAX_UPLOAD_MB", "10"))
FORGEBOT_JOB_TIMEOUT_S = int(os.environ.get("FORGEBOT_JOB_TIMEOUT_S", "600"))
FORGEBOT_ARTIFACT_RETENTION_DAYS = int(
    os.environ.get("FORGEBOT_ARTIFACT_RETENTION_DAYS", "14")
)
FORGEBOT_MAX_ACTIVE_JOBS_PER_PROJECT = int(os.environ.get("FORGEBOT_MAX_ACTIVE_JOBS_PER_PROJECT", "10"))
FORGEBOT_MAX_JOBS_PER_USER_PER_DAY = int(os.environ.get("FORGEBOT_MAX_JOBS_PER_USER_PER_DAY", "100"))

# Meta Model API (server-side only). META_API is accepted as a compatibility
# alias for the key already present in this project's local .env.
MODEL_API_KEY = (
    os.environ.get("FORGEBOT_MODEL_API_KEY")
    or os.environ.get("META_API")
    or os.environ.get("MODEL_API_KEY", "")
)
MODEL_API_BASE_URL = os.environ.get(
    "FORGEBOT_MODEL_API_BASE_URL", "https://api.meta.ai/v1"
).rstrip("/")
MODEL_API_PROVIDER = os.environ.get("FORGEBOT_MODEL_PROVIDER", "meta-model-api")
MODEL_API_NAME = os.environ.get("FORGEBOT_MODEL_NAME", "muse-spark-1.3")
FORGEBOT_MODEL_TIMEOUT_S = int(os.environ.get("FORGEBOT_MODEL_TIMEOUT_S", "300"))
