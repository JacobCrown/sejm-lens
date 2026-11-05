from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Render sets RENDER_EXTERNAL_HOSTNAME (e.g. sejm-lens.onrender.com) on every web service.
RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, RENDER_EXTERNAL_HOSTNAME]
    CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, f"https://{RENDER_EXTERNAL_HOSTNAME}"]

if not DEBUG:
    if SECRET_KEY in ("", "dev-insecure-change-me"):
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is false.")
    # Behind Render's TLS proxy: trust X-Forwarded-Proto and force HTTPS.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_HSTS_SECONDS = 3600
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.parliament",
    "apps.assets",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://sejm:sejm@localhost:5432/sejmwatch"),
}
# Serverless Postgres (Neon) can take a few seconds to wake up after idling.
DATABASES["default"].setdefault("OPTIONS", {}).setdefault("connect_timeout", 15)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # WhiteNoise serves hashed, compressed static files in production (collectstatic runs at image build).
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

SEJM_API_URL = env("SEJM_API_URL", default="https://api.sejm.gov.pl/sejm")
SEJM_TERM = env.int("SEJM_TERM", default=10)
SEJM_HTTP_WORKERS = env.int("SEJM_HTTP_WORKERS", default=8)

# Asset declarations: "mock" needs no keys, "ocr_llm" = Chandra plus any LLM.
ASSET_EXTRACTOR = env("ASSET_EXTRACTOR", default="mock")
ASSET_REVIEW_THRESHOLD = env.float("ASSET_REVIEW_THRESHOLD", default=0.7)
# Pause (seconds) between requests to sejm.gov.pl and ORKA during sync_declarations --fetch.
DECLARATION_FETCH_DELAY = env.float("DECLARATION_FETCH_DELAY", default=0.5)

# Chandra is optional and local. The paid path is the Datalab convert API.
OCR_ENGINE = env("OCR_ENGINE", default="datalab")
OCR_DEVICE = env("OCR_DEVICE", default="gpu")
OCR_RENDER_SCALE = env.float("OCR_RENDER_SCALE", default=1.6)
OCR_MIN_LINE_CONFIDENCE = env.float("OCR_MIN_LINE_CONFIDENCE", default=0.45)
OCR_MAX_PAGES = env.int("OCR_MAX_PAGES", default=20)

DATALAB_API_KEY = env("DATALAB_API_KEY", default="")
DATALAB_API_URL = env("DATALAB_API_URL", default="https://www.datalab.to")
# lite | fast | balanced | accurate. Scanned declarations want accurate.
DATALAB_MODE = env("DATALAB_MODE", default="accurate")
# Optional Cookie header from a browser that opened an MP page on sejm.gov.pl.
SEJM_COOKIE = env("SEJM_COOKIE", default="")

# LLM: dowolny dostawca z API zgodnym z OpenAI (/chat/completions).
LLM_PROVIDER = env("LLM_PROVIDER", default="openai")
LLM_BASE_URL = env("LLM_BASE_URL", default="")
LLM_API_KEY = env("LLM_API_KEY", default="")
LLM_MODEL = env("LLM_MODEL", default="")
LLM_TIMEOUT = env.float("LLM_TIMEOUT", default=180)
LLM_TEMPERATURE = env("LLM_TEMPERATURE", default="0")
LLM_MAX_TOKENS = env("LLM_MAX_TOKENS", default="")
LLM_RESPONSE_FORMAT = env("LLM_RESPONSE_FORMAT", default="json_object")
LLM_EXTRA_HEADERS = env.json("LLM_EXTRA_HEADERS", default={})
LLM_EXTRA_BODY = env.json("LLM_EXTRA_BODY", default={})
LLM_MAX_REPAIRS = env.int("LLM_MAX_REPAIRS", default=2)
NBP_API_URL = env("NBP_API_URL", default="https://api.nbp.pl/api")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"apps": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")}},
}
