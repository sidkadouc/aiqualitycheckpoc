# ───────────────────────────────────────────────────────────────
# Dockerfile — PDF Policy Extraction Pipeline
# Deploy to Azure Container Apps (job or on-demand container)
# ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system dependencies (poppler for PDF, gcc for native deps)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc poppler-utils && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application source
COPY pdf_pipeline/ ./pdf_pipeline/
COPY run_pipeline.py .

# Output directory for intermediate files (overridable via env var)
RUN mkdir -p /app/pipeline_output
ENV PIPELINE_OUTPUT_DIR=/app/pipeline_output

# No HTTP ingress — this image is invoked as an ACA Job
# Default entrypoint:
#   1. Download INPUT_PDF_BLOB_NAME from $AZURE_STORAGE_CONTAINER via SAMI
#   2. Run run_pipeline.py on the downloaded file
#   3. Upload artifacts back to blob under OUTPUT_BLOB_PREFIX/<run_id>/
CMD ["python", "-m", "pdf_pipeline.job_entrypoint"]
