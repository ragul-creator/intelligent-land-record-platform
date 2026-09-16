FROM python:3.12-slim

WORKDIR /app

# The worker reuses C.4/C.6 directly; retain the package boundary instead of copying GIS logic.
COPY backend /app/backend
COPY ai /app/ai

WORKDIR /app/backend
RUN pip install --no-cache-dir --retries 5 --timeout 120 ".[dev]"

ENV PYTHONPATH=/app:/app/backend

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
