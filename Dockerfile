FROM node:22-alpine AS frontend

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY assets ./assets
RUN mkdir -p static/css static/js && npm run build


FROM python:3.12-slim AS application

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system django && useradd --system --gid django django

COPY pyproject.toml ./
RUN python -m pip install --upgrade pip && python -m pip install ".[docker]"

COPY . .
COPY --from=frontend /app/static ./static

RUN mkdir -p /app/staticfiles /app/media && chown -R django:django /app/staticfiles /app/media

USER django
EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
