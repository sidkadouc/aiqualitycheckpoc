"""
Conflict resolver for contradictory rule violations.

When two rules produce violations on the **same text** in the **same paragraph**
with contradictory suggestions (e.g. one says "capitalize" while the other says
"use lowercase"), this module detects the conflict and marks the lower-priority
violation as superseded.

Priority order:  mandatory > recommended > informational > unspecified
Tie-breaker:     higher confidence wins.

This runs as a post-processing step in the aggregator after merging violations
from all batch checkers.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from .models import ParagraphResult, RuleViolation

logger = logging.getLogger(__name__)

# ── severity priority (higher number = higher priority) ───────────────
_SEVERITY_PRIORITY: dict[str, int] = {
    "mandatory": 30,
    "recommended": 20,
    "informational": 10,
    "unspecified": 0,
}

# ── section keywords that signal capitalisation / casing rules ────────
_CASING_KEYWORDS = {
    "capital", "capitalisation", "capitalization", "case", "title case",
    "sentence case", "upper", "lower",
}

# ── signals in explanations / suggestions that indicate opposing changes
_CAPITALIZE_SIGNALS = re.compile(
    r"capitali[sz]e|upper[\s-]?case|title[\s-]?case|start with.*capital",
    re.IGNORECASE,
)
_LOWERCASE_SIGNALS = re.compile(
    r"lower[\s-]?case|sentence[\s-]?case|should not be capitali[sz]ed|"
    r"do not capitali[sz]e|avoid.*title[\s-]?case",
    re.IGNORECASE,
)


def _severity_score(v: RuleViolation) -> int:
    return _SEVERITY_PRIORITY.get(v.severity.lower(), 0)


def _text_overlap(a: str, b: str) -> bool:
    """Return True if two violated_text spans overlap significantly."""
    a_clean = a.strip().lower()
    b_clean = b.strip().lower()
    if not a_clean or not b_clean:
        return False
    # One contains the other
    if a_clean in b_clean or b_clean in a_clean:
        return True
    # Significant word overlap (>50% of shorter text's words)
    words_a = set(a_clean.split())
    words_b = set(b_clean.split())
    if not words_a or not words_b:
        return False
    overlap = words_a & words_b
    min_words = min(len(words_a), len(words_b))
    return len(overlap) / min_words >= 0.5


def _is_casing_related(v: RuleViolation) -> bool:
    """Check if a violation relates to capitalisation / casing."""
    section = v.section_title.lower()
    explanation = v.explanation.lower()
    return (
        any(kw in section for kw in _CASING_KEYWORDS)
        or bool(_CAPITALIZE_SIGNALS.search(explanation))
        or bool(_LOWERCASE_SIGNALS.search(explanation))
    )


def _wants_capitalize(v: RuleViolation) -> bool:
    """True if the violation suggests capitalising (uppercasing) text."""
    text = f"{v.explanation} {v.suggestion} {v.fix_value}"
    return bool(_CAPITALIZE_SIGNALS.search(text))


def _wants_lowercase(v: RuleViolation) -> bool:
    """True if the violation suggests lowercasing text."""
    text = f"{v.explanation} {v.suggestion} {v.fix_value}"
    return bool(_LOWERCASE_SIGNALS.search(text))


def _are_contradictory(a: RuleViolation, b: RuleViolation) -> bool:
    """Detect if two violations give opposite advice on the same text."""
    # Must be on the same paragraph (caller guarantees this)
    # Must have overlapping violated text
    if not _text_overlap(a.violated_text, b.violated_text):
        return False

    # Case 1: Both are casing-related but push in opposite directions
    if _is_casing_related(a) and _is_casing_related(b):
        a_up = _wants_capitalize(a)
        a_down = _wants_lowercase(a)
        b_up = _wants_capitalize(b)
        b_down = _wants_lowercase(b)
        if (a_up and b_down) or (a_down and b_up):
            return True

    # Case 2: Both have replace_text but with different fix_value
    if a.fix_type == "replace_text" and b.fix_type == "replace_text":
        if a.fix_value.strip() and b.fix_value.strip():
            # If fix values differ meaningfully, it's a contradiction
            if a.fix_value.strip().lower() != b.fix_value.strip().lower():
                return True

    return False


def _pick_winner(a: RuleViolation, b: RuleViolation) -> tuple[RuleViolation, RuleViolation]:
    """Return (winner, loser) based on severity then confidence."""
    score_a = _severity_score(a)
    score_b = _severity_score(b)

    if score_a > score_b:
        return a, b
    if score_b > score_a:
        return b, a

    # Same severity: higher confidence wins
    if a.confidence >= b.confidence:
        return a, b
    return b, a


def resolve_contradictions(
    paragraph_results: Sequence[ParagraphResult],
) -> list[ParagraphResult]:
    """Scan all violations for contradictory pairs and supersede the loser.

    Modifies violations in-place by setting ``superseded_by`` on the
    losing violation.  Also removes superseded violations from the
    ``is_compliant`` computation.

    Returns the same list (mutated) for convenience.
    """
    total_superseded = 0

    for pr in paragraph_results:
        if len(pr.violations) < 2:
            continue

        # Compare all pairs
        n = len(pr.violations)
        superseded_indices: set[int] = set()

        for i in range(n):
            if i in superseded_indices:
                continue
            for j in range(i + 1, n):
                if j in superseded_indices:
                    continue

                vi = pr.violations[i]
                vj = pr.violations[j]

                # Skip already-superseded
                if vi.superseded_by or vj.superseded_by:
                    continue

                if _are_contradictory(vi, vj):
                    winner, loser = _pick_winner(vi, vj)
                    loser.superseded_by = winner.rule_id
                    total_superseded += 1

                    # Track which index is the loser
                    loser_idx = i if loser is vi else j
                    superseded_indices.add(loser_idx)

                    logger.info(
                        "Conflict resolved on P%d: %s (%s, %.0f%%) supersedes "
                        "%s (%s, %.0f%%) — overlapping text: '%s'",
                        pr.paragraph_index,
                        winner.rule_id,
                        winner.severity,
                        winner.confidence * 100,
                        loser.rule_id,
                        loser.severity,
                        loser.confidence * 100,
                        loser.violated_text[:60],
                    )

        # Recompute is_compliant: only count non-superseded violations
        active = [v for v in pr.violations if not v.superseded_by]
        pr.is_compliant = len(active) == 0

    if total_superseded:
        logger.info("Conflict resolution: %d violation(s) superseded", total_superseded)

    return list(paragraph_results)
