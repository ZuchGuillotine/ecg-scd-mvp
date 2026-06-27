FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ECG_SCD_STORAGE=/app/storage \
    ECG_SCD_MODEL_REGISTRY=/app/models/model_registry.json

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.inference.txt /app/requirements.inference.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.inference.txt

COPY backend /app/backend
COPY frontend /app/frontend
COPY models /app/models

EXPOSE 8080

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
