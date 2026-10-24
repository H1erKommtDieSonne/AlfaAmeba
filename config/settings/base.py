from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")


DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS = []
LOCAL_APPS = [
    "core",
    "users",
    "organizations",
    "products",
    "applications",
    "documents",
    "analytics",
    "node_integration",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

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
WSGI_APPLICATION = "config.wsgi.application"

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
                "core.context_processors.section",
                "users.context_processors.permissions",
            ],
        },
    }
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

# Своя статика у каждого приложения (<app>/static/<app>/); CSS собирает Tailwind
# из theme/input.css в core/static/core/css/app.css — см. README.
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

TEST_RUNNER = "core.test_runner.MediaIsolatedRunner"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "users.User"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

ML_SERVICE_URL = env("ML_SERVICE_URL", default="http://127.0.0.1:9000")
# Pull-очередь документов (documents/): по этому токену ходит LLM-воркер, сессии у него нет.
# Пусто — API закрыто совсем и отвечает 503: забытая настройка не должна пускать всех.
LLM_SERVICE_TOKEN = env("LLM_SERVICE_TOKEN", default="")
LLM_JOB_LEASE_SECONDS = env.int("LLM_JOB_LEASE_SECONDS", default=600)
LLM_RETRY_DELAY_SECONDS = env.int("LLM_RETRY_DELAY_SECONDS", default=60)

# Pull-очередь аналитики: отдельный токен, LLM-воркер и сессии сюда не допускаются.
ANALYTICS_SERVICE_TOKEN = env("ANALYTICS_SERVICE_TOKEN", default="")
ANALYTICS_JOB_LEASE_SECONDS = env.int("ANALYTICS_JOB_LEASE_SECONDS", default=600)
ANALYTICS_RETRY_DELAY_SECONDS = env.int("ANALYTICS_RETRY_DELAY_SECONDS", default=60)

# Внешний узел проверки документов. Пустой CALLBACK_TOKEN закрывает приём
# результатов (503), пока интеграция не настроена.
DOCUMENT_NODE_ENABLED = env.bool("DOCUMENT_NODE_ENABLED", default=False)
DOCUMENT_NODE_URL = env("DOCUMENT_NODE_URL", default="http://192.168.1.105:8000")
DOCUMENT_NODE_API_KEY = env("DOCUMENT_NODE_API_KEY", default="")
DOCUMENT_NODE_CALLBACK_TOKEN = env("DOCUMENT_NODE_CALLBACK_TOKEN", default="")
DOCUMENT_NODE_TIMEOUT_SECONDS = env.int("DOCUMENT_NODE_TIMEOUT_SECONDS", default=30)
DOCUMENT_NODE_POLL_SECONDS = env.int("DOCUMENT_NODE_POLL_SECONDS", default=5)
