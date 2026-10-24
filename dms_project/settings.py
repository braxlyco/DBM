"""
Django settings for the Workshop DMS.

Development defaults. For production set DJANGO_SECRET_KEY, DJANGO_DEBUG=0 and
DJANGO_ALLOWED_HOSTS as environment variables.
"""
import os
from pathlib import Path

from django.contrib.messages import constants as message_constants

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "workshop",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dms_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "dms_project.wsgi.application"

# --- Database ---------------------------------------------------------------
# SQLite for now (a single file, db.sqlite3, next to manage.py).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
# PostgreSQL: `pip install "psycopg[binary]"`, then uncomment this block (it replaces the one above).
# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.postgresql",
#         "NAME": os.environ.get("DB_NAME", "dms"),
#         "USER": os.environ.get("DB_USER", "dms"),
#         "PASSWORD": os.environ.get("DB_PASSWORD", ""),
#         "HOST": os.environ.get("DB_HOST", "localhost"),
#         "PORT": os.environ.get("DB_PORT", "5432"),
#         "ATOMIC_REQUESTS": True,
#     }
# }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Karachi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "workshop:job_list"
LOGOUT_REDIRECT_URL = "login"

# Map Django's "error" message level to Bootstrap's "danger" alert colour.
MESSAGE_TAGS = {message_constants.ERROR: "danger"}

# --- DMS settings ------------------------------------------------------------
DMS_CURRENCY = "PKR"

# Which FinancePoster implementation handles SAP postings. Swap this dotted path for a
# real OData/BAPI adapter later; no business code changes. (See workshop/finance.py.)
DMS_FINANCE_POSTER = "workshop.finance.LoggingAdapter"

# SAP FI mapping. ALL VALUES ARE PLACEHOLDERS: replace them with your SAP configuration.
DMS_SAP_COMPANY_CODE = "PK01"
DMS_SAP_ONE_TIME_CUSTOMER = "ONETIME"  # used when a Customer has no sap_customer_no
DMS_SAP_DOC_TYPES = {
    "INVOICE": "DR",       # customer invoice
    "PARTS_ISSUE": "SA",   # G/L posting for parts consumed
    "PARTS_RETURN": "SA",
}
DMS_SAP_GL_ACCOUNTS = {
    "AR": "0000140000",                   # customer reconciliation account (AR line also carries customer_no)
    "REVENUE_LABOUR": "0000800100",
    "REVENUE_PARTS": "0000800200",
    "REVENUE_SUBLET": "0000800300",
    "OUTPUT_TAX_GOODS": "0000175000",     # sales tax on goods (FBR)
    "OUTPUT_TAX_SERVICES": "0000175100",  # sales tax on services (provincial authority)
    "COGS_PARTS": "0000890000",
    "INVENTORY_PARTS": "0000300000",
}
