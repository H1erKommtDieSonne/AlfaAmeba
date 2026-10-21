# CSS собирает standalone-бинарник Tailwind — тот же, что локально (tailwindcss.exe), без Node.js.
FROM debian:bookworm-slim AS frontend

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
ARG TAILWIND_VERSION=4.3.1
# Архитектуру подставляет BuildKit: на Mac с Apple Silicon образ собирается под arm64.
ARG TARGETARCH
RUN case "$TARGETARCH" in arm64) arch=arm64 ;; *) arch=x64 ;; esac \
    && curl -fsSL "https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-linux-${arch}" \
       -o /usr/local/bin/tailwindcss \
    && chmod +x /usr/local/bin/tailwindcss
COPY . .
RUN tailwindcss -i theme/input.css -o core/static/core/css/app.css --minify


FROM python:3.12-slim AS application

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

RUN groupadd --system django && useradd --system --gid django django

COPY pyproject.toml ./
RUN python -m pip install --upgrade pip && python -m pip install ".[docker]"

COPY . .
COPY --from=frontend /app/core/static/core/css/app.css ./core/static/core/css/app.css

RUN mkdir -p /app/staticfiles /app/media && chown -R django:django /app/staticfiles /app/media

USER django
EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
