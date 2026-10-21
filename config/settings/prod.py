from .base import *  # noqa: F403
from .base import env

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 60

# Без этого при DEBUG=False Django пишет ошибки только на почту ADMINS — в логи контейнера ничего.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Куки только по HTTPS. По умолчанию выключено: Docker-демо открывается по
# http://127.0.0.1:8080, и secure-куки там не доходили бы до сервера — вход бы не работал.
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=False)
