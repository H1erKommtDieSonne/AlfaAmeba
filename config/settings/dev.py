from .base import *  # noqa: F403
from .base import BASE_DIR, env

DEBUG = True
ALLOWED_HOSTS = ["*"]
SECRET_KEY = env("DJANGO_SECRET_KEY", default="unsafe-local-development-key")

# as_posix: в адресе нужны прямые слэши, иначе путь Windows не разберётся.
DATABASES = {
    "default": env.db(
        "DATABASE_URL", default=f"sqlite:///{(BASE_DIR / 'credit_scoring.db').as_posix()}"
    ),
}

LLM_SERVICE_TOKEN = env("LLM_SERVICE_TOKEN", default="unsafe-local-llm-token")
