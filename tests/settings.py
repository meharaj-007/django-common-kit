"""Settings for the package's own test suite.

SQLite in memory by default; the Postgres leg runs with
``DJANGO_COMMON_UTILS_TEST_DB=postgres``, and ``test_postgres`` runs ONLY there. A
green SQLite run is not evidence the column types match.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "django-common-utils-tests"
DEBUG = False
USE_TZ = True
TIME_ZONE = "Australia/Sydney"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin",
    "django_common_utils",
    "tests.testapp",
]

MIDDLEWARE = [
    "django_common_utils.middleware.CurrentRequestMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "django.template.context_processors.request",
    ]},
}]

if os.environ.get("DJANGO_COMMON_UTILS_TEST_DB") == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("PGDATABASE", "django_common_utils_test"),
            "USER": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
            "PASSWORD": os.environ.get("PGPASSWORD", ""),
            "HOST": os.environ.get("PGHOST", "localhost"),
            "PORT": os.environ.get("PGPORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    }

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "django_common_utils.api.exceptions.custom_exception_handler",
    # None: the permission classes must survive an anonymous request where
    # `request.user` is None rather than AnonymousUser.
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_RATES": {"test_always": "1/min"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# The parameter warm-load thread would race the test database's creation.
# The key is a test fixture, generated for this file and used nowhere else;
# tests/testapp has an encrypted field, and without a key the system checks
# refuse to run the suite (common_control.E001).
DJANGO_COMMON_UTILS = {
    "PARAMETERS": {"AUTO_LOAD_ON_STARTUP": False},
    "ENCRYPTION": {"KEYS": ["NtShbux2yPWuYuOMN31mtF4itctD6NXO4FLk5TIweXE="]},
}

# The legs that install no extras run without cryptography, which is the point
# of them (tests/test_import_purity.py); the encrypted-field tests skip there.
import importlib.util  # noqa: E402

if importlib.util.find_spec("cryptography") is None:
    SILENCED_SYSTEM_CHECKS = ["common_control.E003"]

# Otherwise left empty on purpose: the suite must prove the package works on
# defaults. Individual tests use override_settings.
