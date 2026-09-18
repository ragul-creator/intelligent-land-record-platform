FROM python:3.12-slim

WORKDIR /app

# F.4 workers use the existing Tesseract adapter. Traineddata stays an OS/runtime concern,
# never a repository artifact; Tamil plus English support the documented local demo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-tam \
    && rm -rf /var/lib/apt/lists/*

# The worker reuses C.4/C.6 directly; retain the package boundary instead of copying GIS logic.
COPY backend /app/backend
COPY ai /app/ai

WORKDIR /app/backend
RUN pip install --no-cache-dir --retries 5 --timeout 120 ".[dev]"

ENV PYTHONPATH=/app:/app/backend

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
