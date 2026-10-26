"""Настройки демонстрационного LAN-стенда: DEBUG=False без Docker.

Запуск (только для закрытой демонстрационной сети, не production):

    $env:DJANGO_SETTINGS_MODULE = "config.settings.lan"
    $env:DJANGO_SECRET_KEY = "<длинный случайный секрет>"
    python manage.py runserver 0.0.0.0:8000 --insecure --noreload

``--insecure`` заставляет runserver раздавать статику при DEBUG=False. Это
удобно для показа, но не заменяет Gunicorn/Nginx: в Docker используйте
config.settings.prod, а наружу не публикуйте runserver.
"""

from .base import *  # noqa: F403
from .base import BASE_DIR, env

DEBUG = False
# Секрет обязателен: без него стенд не должен молча стартовать с dev-ключом.
SECRET_KEY = env("DJANGO_SECRET_KEY")

# Явный список вместо "*": стенд доступен с локальной машины и по LAN-адресу.
ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "192.168.1.103"],
)
# Origin включает схему и порт 8000 — иначе POST-формы и вход получат 403.
CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://192.168.1.103:8000",
    ],
)

# По умолчанию та же локальная SQLite, что и в dev; DATABASE_URL переопределяет.
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{(BASE_DIR / 'credit_scoring.db').as_posix()}",
    ),
}

# Демонстрационный стенд по HTTP: secure-куки сломали бы вход.
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = env.bool(
    "DJANGO_SECURE_COOKIES", default=False
)

# При DEBUG=False стандартный django-логгер не пишет в консоль; для runserver
# оставляем ошибки и предупреждения видимыми в терминале.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
