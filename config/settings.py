from pathlib import Path

import environ

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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

SEJM_API_URL = env("SEJM_API_URL", default="https://api.sejm.gov.pl/sejm")
SEJM_TERM = env.int("SEJM_TERM", default=10)
SEJM_HTTP_WORKERS = env.int("SEJM_HTTP_WORKERS", default=8)

# Asset declarations: "mock" needs no keys, "ocr_llm" = PaddleOCR plus any LLM.
ASSET_EXTRACTOR = env("ASSET_EXTRACTOR", default="mock")
ASSET_REVIEW_THRESHOLD = env.float("ASSET_REVIEW_THRESHOLD", default=0.7)
# Pause (seconds) between requests to sejm.gov.pl and ORKA during sync_declarations --fetch.
DECLARATION_FETCH_DELAY = env.float("DECLARATION_FETCH_DELAY", default=0.5)

# OCR (PaddleOCR, lokalnie). Wymaga: uv sync --extra ocr
OCR_ENGINE = env("OCR_ENGINE", default="paddle")
OCR_LANG = env("OCR_LANG", default="pl")
OCR_DEVICE = env("OCR_DEVICE", default="cpu")
OCR_RENDER_SCALE = env.float("OCR_RENDER_SCALE", default=1.6)
OCR_MIN_LINE_CONFIDENCE = env.float("OCR_MIN_LINE_CONFIDENCE", default=0.45)
OCR_MAX_PAGES = env.int("OCR_MAX_PAGES", default=20)
OCR_DET_MODEL = env("OCR_DET_MODEL", default="")
OCR_REC_MODEL = env("OCR_REC_MODEL", default="")

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
