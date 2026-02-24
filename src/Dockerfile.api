# ───────────────────────────────────────────────────────────────
# Dockerfile — Quality Checker API (FastAPI + Uvicorn)
# Deploy to Azure Container Apps
# ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application source
COPY api/ ./api/
COPY quality_agent/ ./quality_agent/
COPY pdf_pipeline/models.py ./pdf_pipeline/models.py
COPY pdf_pipeline/__init__.py ./pdf_pipeline/__init__.py
COPY run_api.py .

# Expose port (ACA expects 8080 by default)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')"

# Run with Uvicorn — bind 0.0.0.0:8080 for ACA ingress
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", "--log-level", "info"]
