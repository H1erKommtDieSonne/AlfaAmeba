FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# ml/ — пакет, чтобы одинаково работали python -m ml.server, python -m ml.worker
# (реальный pipeline) и python -m ml.stub_worker (локальный demo-stub).
COPY requirements.txt /app/ml/requirements.txt
RUN pip install --no-cache-dir -r /app/ml/requirements.txt
COPY . /app/ml/

RUN useradd --system ml
USER ml
EXPOSE 9000

CMD ["python", "-m", "ml.server"]
