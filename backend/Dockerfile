FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DB_PATH=/var/lib/shiftguard/shiftguard.db

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts

RUN useradd --system --uid 10001 shiftguard \
    && mkdir -p /var/lib/shiftguard \
    && chown shiftguard:shiftguard /var/lib/shiftguard
USER 10001

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
