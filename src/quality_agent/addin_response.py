"""
Transform a QualityCheckReport into an AddinResponse for the Word Web Add-in.

The AddinResponse is a JSON-serialisable structure optimised for:
- **Side panel (task pane)**: findings grouped by paragraph with metadata.
- **Document highlighting**: ``search_text`` per finding for Office.js
  ``body.search()`` + ``range.font.highlightColor``.
- **Automatic fixes**: ``fixable`` / ``fix_type`` / ``fix_value`` fields.
- **Finding ↔ highlight link**: unique ``id`` shared between panel card
  and highlighted range.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    AddinFinding,
    AddinParagraphGroup,
    AddinResponse,
    QualityCheckReport,
    RuleViolation,
)

logger = logging.getLogger(__name__)

# Fix types that can be applied automatically
_AUTOMATABLE_FIX_TYPES = {"remove_formatting", "replace_text", "apply_style"}

# Severity → highlight colour mapping
_SEVERITY_COLORS: dict[str, str] = {
    "mandatory": "red",
    "recommended": "yellow",
}


def _make_finding_id(paragraph_index: int, rule_id: str, ordinal: int) -> str:
    """Generate a deterministic, URL-safe finding ID."""
    return f"f-{paragraph_index}-{rule_id}-{ordinal}"


def _truncate(text: str, max_len: int = 120) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _compute_search_text(violation: RuleViolation) -> str:
    """Pick the best text to pass to ``body.search()`` in Office.js.

    Office.js ``search()`` uses plain text matching.  We pick the
    ``violated_text`` when it's short enough (≤ 255 chars — the
    Word search limit).  If it's too long we fall back to the first
    150 chars which is usually enough for a unique match.
    """
    text = violation.violated_text.strip()
    if not text:
        return ""
    # Word search API limit
    if len(text) <= 255:
        return text
    return text[:150]


def _resolve_doc_paragraph_index(
    paragraph_index: int,
    doc_paragraph_map: dict[int, int] | None,
) -> int:
    """Map the internal sequential paragraph index back to the original
    document-level index (``docParagraphIndex`` from the add-in request).

    When ``doc_paragraph_map`` is ``None`` (e.g. raw XML input), the
    internal index is used as-is.
    """
    if doc_paragraph_map and paragraph_index in doc_paragraph_map:
        return doc_paragraph_map[paragraph_index]
    return paragraph_index


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def build_addin_response(
    report: QualityCheckReport,
    *,
    doc_paragraph_map: dict[int, int] | None = None,
) -> AddinResponse:
    """Convert a ``QualityCheckReport`` into an ``AddinResponse``.

    Parameters
    ----------
    report:
        The quality check report produced by the workflow.
    doc_paragraph_map:
        Optional mapping of internal paragraph indices to the original
        document paragraph indices (from the Word add-in JSON request).
        When the add-in sends ``docParagraphIndex`` values, pass
        ``{0: 7, 1: 8, ...}`` so findings reference the correct
        paragraphs in the Word document.
    """
    severity_counts: dict[str, int] = {}
    groups: list[AddinParagraphGroup] = []

    for pr in report.paragraph_results:
        doc_idx = _resolve_doc_paragraph_index(
            pr.paragraph_index, doc_paragraph_map
        )
        findings: list[AddinFinding] = []
        ordinal = 0

        for v in pr.violations:
            ordinal += 1
            finding_id = _make_finding_id(doc_idx, v.rule_id, ordinal)
            fixable = v.fix_type in _AUTOMATABLE_FIX_TYPES
            color = _SEVERITY_COLORS.get(v.severity, "yellow")

            finding = AddinFinding(
                id=finding_id,
                rule_id=v.rule_id,
                rule_text=v.rule_text,
                rule_type=v.rule_type,
                severity=v.severity,
                doc_paragraph_index=doc_idx,
                search_text=_compute_search_text(v),
                run_indices=v.violated_run_indices,
                explanation=v.explanation,
                suggestion=v.suggestion,
                confidence=v.confidence,
                section_title=v.section_title,
                page=v.page,
                fixable=fixable,
                fix_type=v.fix_type,
                fix_value=v.fix_value,
                highlight_color=color,
            )
            findings.append(finding)

            # track severity totals
            severity_counts[v.severity] = severity_counts.get(v.severity, 0) + 1

        groups.append(
            AddinParagraphGroup(
                doc_paragraph_index=doc_idx,
                text_preview=_truncate(pr.plain_text),
                finding_count=len(findings),
                findings=findings,
            )
        )

    # only include paragraphs that have findings
    groups_with_findings = [g for g in groups if g.finding_count > 0]

    return AddinResponse(
        version="1.0",
        total_paragraphs=report.total_paragraphs,
        total_findings=report.total_violations,
        summary=severity_counts,
        paragraphs=groups_with_findings,
        highlighted_ooxml=report.highlighted_xml,
    )


def build_doc_paragraph_map(json_request: dict[str, Any] | str) -> dict[int, int]:
    """Extract the internal→document paragraph index mapping from a JSON
    request payload sent by the Word add-in.

    The add-in sends ``paragraphs[].docParagraphIndex`` values.  After
    ``parse_json_request`` processes the payload, paragraphs are
    re-indexed sequentially (0, 1, 2, …).  This function builds the
    reverse map: ``{sequential_index: docParagraphIndex}``.
    """
    import json as _json

    if isinstance(json_request, str):
        data = _json.loads(json_request)
    else:
        data = json_request

    mapping: dict[int, int] = {}
    for seq_idx, para in enumerate(data.get("paragraphs", [])):
        doc_idx = para.get("docParagraphIndex", seq_idx)
        mapping[seq_idx] = doc_idx
    return mapping
