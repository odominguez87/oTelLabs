FROM python:3.12.7-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV SERVICE_NAME=data-service
ENV APP_VERSION=1.0.0

# Usuario sin privilegios (hallazgo de seguridad típico en escaneos de imagen)
RUN useradd --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8002

# 1 worker: el estado de chaos (/admin/chaos) vive en memoria del proceso
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
