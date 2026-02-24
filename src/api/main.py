"""
FastAPI application — OECD Quality Checker API.

Endpoints
---------
POST /api/check          — full document check (multiple paragraphs)
POST /api/check-paragraph — single-paragraph check (low-latency, for real-time)
GET  /api/health         — health / readiness probe
GET  /api/rules/summary  — rule statistics

The add-in calls ``/api/check-paragraph`` on every paragraph change
(debounced) for near-real-time feedback, and ``/api/check`` for a full
document scan.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── bootstrap ──────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv

    _env = Path(__file__).resolve().parent.parent / ".env"
    if _env.exists():
        load_dotenv(_env)
except ImportError:
    pass

from quality_agent.addin_response import build_addin_response, build_doc_paragraph_map
from quality_agent.executors import TokenRateLimiter, get_or_create_rate_limiter
from quality_agent.model_router import ModelRouter
from quality_agent.models import AddinResponse, RuleInfo
from quality_agent.workflow import load_rules_from_cosmos, load_rules_from_json, run_quality_check

logger = logging.getLogger(__name__)

# ── global state (loaded once at startup) ──────────────────────────────────────
_rules: list[RuleInfo] = []
_router: ModelRouter | None = None

# Model configuration from environment
PRIMARY_MODEL = os.environ.get("AZURE_OPENAI_PRIMARY_DEPLOYMENT", "gpt-5.2")
PRIMARY_TPM = int(os.environ.get("AZURE_OPENAI_PRIMARY_TPM", "500000"))
PRIMARY_CONCURRENCY = int(os.environ.get("AZURE_OPENAI_PRIMARY_CONCURRENCY", "5"))
FALLBACK_MODEL = os.environ.get("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "gpt-4.1")
FALLBACK_TPM = int(os.environ.get("AZURE_OPENAI_FALLBACK_TPM", "256000"))
FALLBACK_CONCURRENCY = int(os.environ.get("AZURE_OPENAI_FALLBACK_CONCURRENCY", "3"))

# Cosmos DB configuration (preferred for production)
COSMOS_ENDPOINT = os.environ.get("AZURE_COSMOS_ENDPOINT", "")
COSMOS_DATABASE = os.environ.get("AZURE_COSMOS_DATABASE", "appdata")
COSMOS_RULES_CONTAINER = os.environ.get("AZURE_COSMOS_RULES_CONTAINER", "policy-rules")
MANAGED_IDENTITY_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
USE_COSMOS_RULES = os.environ.get("USE_COSMOS_RULES", "").lower() in ("true", "1", "yes")

# Fallback: local JSON file (for development)
_RULES_PATH = os.environ.get(
    "RULES_JSON_PATH",
    str(Path(__file__).resolve().parent.parent / "pipeline_output" / "05_extracted_rules.json"),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load rules once at startup so every request reuses them."""
    global _rules, _router

    # Load rules from Cosmos DB (production) or local JSON (dev fallback)
    if USE_COSMOS_RULES and COSMOS_ENDPOINT:
        logger.info("Loading rules from Cosmos DB: %s/%s", COSMOS_DATABASE, COSMOS_RULES_CONTAINER)
        _rules = load_rules_from_cosmos(
            cosmos_endpoint=COSMOS_ENDPOINT,
            database_name=COSMOS_DATABASE,
            container_name=COSMOS_RULES_CONTAINER,
            managed_identity_client_id=MANAGED_IDENTITY_CLIENT_ID or None,
        )
    else:
        logger.info("Loading rules from JSON file: %s", _RULES_PATH)
        _rules = load_rules_from_json(_RULES_PATH)

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    key = os.environ.get("AZURE_OPENAI_KEY") or None

    _router = ModelRouter.from_env(
        endpoint=endpoint,
        api_key=key,
        primary_model=PRIMARY_MODEL,
        primary_tpm=PRIMARY_TPM,
        primary_concurrency=PRIMARY_CONCURRENCY,
        fallback_model=FALLBACK_MODEL,
        fallback_tpm=FALLBACK_TPM,
        fallback_concurrency=FALLBACK_CONCURRENCY,
    )
    logger.info(
        "Loaded %d rules | primary=%s (%dK TPM) | fallback=%s (%dK TPM)",
        len(_rules), PRIMARY_MODEL, PRIMARY_TPM // 1000,
        FALLBACK_MODEL, FALLBACK_TPM // 1000,
    )
    yield


app = FastAPI(
    title="OECD Quality Checker API",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow the Word add-in (any origin during dev; restrict in prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── request / response schemas ─────────────────────────────────────────

class CheckRequest(BaseModel):
    """Full-document check request (same shape the add-in already sends)."""
    documentInfo: dict[str, Any] = Field(default_factory=dict)
    paragraphs: list[dict[str, Any]] = Field(default_factory=list)


class ParagraphCheckRequest(BaseModel):
    """Single-paragraph check for near-real-time feedback."""
    docParagraphIndex: int
    ooxml: str  # raw OOXML or pkg:package for this paragraph
    textPreview: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    rules_loaded: int = 0
    uptime_seconds: float = 0.0
    deployments: list[dict[str, Any]] = Field(default_factory=list)


_start_time = time.time()


# ── endpoints ──────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
async def health():
    router_status = _router.status() if _router else {"deployments": []}
    return HealthResponse(
        rules_loaded=len(_rules),
        uptime_seconds=round(time.time() - _start_time, 1),
        deployments=router_status["deployments"],
    )


@app.get("/api/rules/summary")
async def rules_summary():
    """Return high-level rule statistics."""
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for r in _rules:
        by_type[r.rule_type] = by_type.get(r.rule_type, 0) + 1
        by_severity[r.severity] = by_severity.get(r.severity, 0) + 1
    return {
        "total_rules": len(_rules),
        "by_type": by_type,
        "by_severity": by_severity,
    }


@app.post("/api/check", response_model=AddinResponse)
async def check_document(req: CheckRequest):
    """Full document check — processes all paragraphs, returns AddinResponse.

    For large documents the paragraphs are processed in batches to avoid
    blowing up the 256 K TPM quota in a single burst.
    """
    if not req.paragraphs:
        raise HTTPException(400, "No paragraphs provided")

    # Re-wrap into the JSON format that run_quality_check expects
    json_payload = req.model_dump()

    try:
        report = await run_quality_check(
            json_content=json_payload,
            rules=_rules,
            enable_prefilter=True,
            router=_router,
            max_concurrent_per_batch=2,  # conservative for large docs
        )
    except Exception as exc:
        logger.exception("Quality check failed")
        raise HTTPException(500, f"Quality check error: {exc}")

    doc_map = build_doc_paragraph_map(json_payload)
    return build_addin_response(report, doc_paragraph_map=doc_map)


@app.post("/api/check-paragraph", response_model=AddinResponse)
async def check_single_paragraph(req: ParagraphCheckRequest):
    """Single-paragraph check — fast path for near-real-time feedback.

    The add-in sends the OOXML for the paragraph the user is currently
    editing.  This endpoint wraps it into a minimal JSON request and
    runs the quality check against it.  Typical latency: 2-5 seconds.
    """
    if not req.ooxml.strip():
        raise HTTPException(400, "Empty OOXML")

    json_payload = {
        "documentInfo": {
            "totalParagraphsInDoc": 1,
            "selectedParagraphs": 1,
        },
        "paragraphs": [
            {
                "docParagraphIndex": req.docParagraphIndex,
                "selectionIndex": 1,
                "textPreview": req.textPreview,
                "ooxmlLength": len(req.ooxml),
                "ooxml": req.ooxml,
            }
        ],
    }

    try:
        report = await run_quality_check(
            json_content=json_payload,
            rules=_rules,
            enable_prefilter=True,
            router=_router,
        )
    except Exception as exc:
        logger.exception("Single-paragraph check failed")
        raise HTTPException(500, f"Quality check error: {exc}")

    doc_map = build_doc_paragraph_map(json_payload)
    return build_addin_response(report, doc_paragraph_map=doc_map)
