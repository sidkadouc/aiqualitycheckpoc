"""
Quality Checker Agent — Microsoft Agent Framework based quality checker.

Uses fan-out / fan-in workflow to check OpenXML document parts against
OECD Style Guide rules in parallel.  Supports raw OpenXML, pkg:package
clipboard format, and JSON request payloads from Word add-ins.
"""

from .addin_response import build_addin_response, build_doc_paragraph_map
from .executors import TokenRateLimiter, get_or_create_rate_limiter
from .model_router import ModelRouter
from .openxml_parser import (
    extract_document_from_pkg,
    extract_docx_styles,
    extract_styles_from_pkg,
    parse_json_request,
)
from .workflow import build_quality_workflow, run_quality_check

__all__ = [
    "run_quality_check",
    "build_quality_workflow",
    "build_addin_response",
    "build_doc_paragraph_map",
    "extract_document_from_pkg",
    "extract_docx_styles",
    "extract_styles_from_pkg",
    "parse_json_request",
    "TokenRateLimiter",
    "get_or_create_rate_limiter",
    "ModelRouter",
]
