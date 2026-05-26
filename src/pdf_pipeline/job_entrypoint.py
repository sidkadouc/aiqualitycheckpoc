"""
ACA Job entrypoint for the PDF pipeline.

Bridges the gap between Azure Blob Storage (where PDFs land) and the existing
`run_pipeline.py` CLI (which expects a local file path).

Flow (per job execution):

    1. Download   $AZURE_STORAGE_CONTAINER/$INPUT_PDF_BLOB_NAME
       to        /tmp/<blob-basename>
    2. Run       python run_pipeline.py /tmp/<blob-basename>
                                       --output-dir $PIPELINE_OUTPUT_DIR
                                       (extra args from $PIPELINE_EXTRA_ARGS)
    3. Upload    every file in $PIPELINE_OUTPUT_DIR/
       to        $AZURE_STORAGE_CONTAINER/$OUTPUT_BLOB_PREFIX/<run-id>/...

All Azure I/O uses `DefaultAzureCredential` (the SAMI when running inside ACA).

Required env vars:
    AZURE_STORAGE_ACCOUNT_NAME   - storage account (e.g. stdevu3c33...)
    AZURE_STORAGE_BLOB_ENDPOINT  - full https URL (e.g. https://<acct>.blob.core.windows.net/)
    AZURE_STORAGE_CONTAINER      - blob container (default: documents)
    INPUT_PDF_BLOB_NAME          - blob name to process (default: input.pdf)

Optional env vars:
    OUTPUT_BLOB_PREFIX           - prefix for output artifacts (default: pipeline-output)
    PIPELINE_OUTPUT_DIR          - local output dir (default: /app/pipeline_output)
    PIPELINE_EXTRA_ARGS          - extra args appended to run_pipeline.py (e.g. "--skip-indexing")
    JOB_RUN_ID                   - explicit run id; defaults to ISO timestamp
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient


def _log(msg: str) -> None:
    """Stdout, prefixed and flushed — ACA log streaming is line-buffered."""
    print(f"[job-entrypoint] {msg}", flush=True)


def _require(env: str) -> str:
    value = os.environ.get(env, "").strip()
    if not value:
        raise SystemExit(f"Missing required env var: {env}")
    return value


def _blob_service() -> BlobServiceClient:
    endpoint = _require("AZURE_STORAGE_BLOB_ENDPOINT")
    return BlobServiceClient(account_url=endpoint, credential=DefaultAzureCredential())


def download_input(svc: BlobServiceClient, container: str, blob_name: str) -> Path:
    local_path = Path("/tmp") / Path(blob_name).name
    _log(f"Downloading blob '{container}/{blob_name}' -> {local_path}")
    blob = svc.get_blob_client(container=container, blob=blob_name)
    with local_path.open("wb") as fh:
        stream = blob.download_blob()
        fh.write(stream.readall())
    size_kb = local_path.stat().st_size / 1024
    _log(f"Downloaded {size_kb:.1f} KiB")
    return local_path


def run_pipeline(local_pdf: Path, output_dir: Path) -> None:
    extra = os.environ.get("PIPELINE_EXTRA_ARGS", "").split()
    cmd = [
        sys.executable,
        "run_pipeline.py",
        str(local_pdf),
        "--output-dir",
        str(output_dir),
        *extra,
    ]
    _log(f"Running pipeline: {' '.join(cmd)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"Pipeline failed with exit code {completed.returncode}")
    _log("Pipeline finished successfully")


def upload_outputs(
    svc: BlobServiceClient,
    container: str,
    output_dir: Path,
    prefix: str,
    run_id: str,
) -> int:
    """Recursively upload every file in `output_dir` to <container>/<prefix>/<run_id>/."""
    if not output_dir.exists():
        _log(f"Output dir {output_dir} does not exist — skipping upload")
        return 0
    files = [p for p in output_dir.rglob("*") if p.is_file()]
    _log(f"Uploading {len(files)} artifact(s) to '{container}/{prefix}/{run_id}/'")
    for f in files:
        rel = f.relative_to(output_dir).as_posix()
        blob_name = f"{prefix.rstrip('/')}/{run_id}/{rel}"
        blob = svc.get_blob_client(container=container, blob=blob_name)
        with f.open("rb") as fh:
            blob.upload_blob(fh, overwrite=True)
    return len(files)


def main() -> int:
    container = os.environ.get("AZURE_STORAGE_CONTAINER", "documents")
    input_blob = os.environ.get("INPUT_PDF_BLOB_NAME", "input.pdf")
    output_prefix = os.environ.get("OUTPUT_BLOB_PREFIX", "pipeline-output")
    output_dir = Path(os.environ.get("PIPELINE_OUTPUT_DIR", "/app/pipeline_output"))
    run_id = os.environ.get("JOB_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    _log(f"run_id={run_id} container={container} input_blob={input_blob}")

    svc = _blob_service()
    local_pdf = download_input(svc, container, input_blob)
    run_pipeline(local_pdf, output_dir)
    uploaded = upload_outputs(svc, container, output_dir, output_prefix, run_id)
    _log(f"Done. Uploaded {uploaded} artifact(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
