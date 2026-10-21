FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY server.py ./server.py

RUN useradd --system ml
USER ml
EXPOSE 9000

CMD ["python", "server.py"]
