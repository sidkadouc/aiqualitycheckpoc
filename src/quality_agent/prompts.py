"""
Prompt templates for the Quality Checker Agent.

Each function returns a fully-rendered prompt string.
Includes smart rule pre-filtering and token-budget management.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from .models import ParsedParagraph, RuleInfo, TextRun

logger = logging.getLogger(__name__)

# ── token constants ────────────────────────────────────────────────────
# Conservative estimate: ~4 chars per token for English text
CHARS_PER_TOKEN = 4
# Max tokens we want to spend on rules text inside one prompt.
# Claude Opus 4.6 has a 200K context window; keeping prompts lean saves $
# and reduces latency.  128K is generous — about 500K chars of rules.
MAX_RULES_TOKENS = 128_000
# Below this threshold we send full rule_text; above we truncate.
RULE_TRUNCATE_LEN = 300

# ──────────────────────────────────────────────────────────────────────
# System prompt for the rule-batch checker
# ──────────────────────────────────────────────────────────────────────

RULE_CHECKER_SYSTEM = """\
You are an expert document compliance checker for the OECD Style Guide.

You will receive:
1. A paragraph from a Word document with its formatting details (bold, italic, underline, etc.)
2. A numbered list of rules to check.

### Instructions
- Check EVERY rule against the paragraph text AND its formatting.
- Formatting rules matter: if a rule says text must be bold, italic, or a specific style,
  verify that the formatting metadata matches.
- Report ONLY actual, clear, unambiguous violations — avoid false positives.
- If the paragraph COMPLIES with a rule, do NOT include that rule in the violations array.
  Only include rules that are genuinely violated.
- For each real violation, identify the exact text span and the run indices involved.
- If the paragraph is fully compliant with all rules, return is_compliant = true with an
  empty violations array.
- Assign a confidence score (0.0–1.0) to each violation reflecting how certain you are.
  Do NOT include any violation with confidence below 0.6.

### Rule Priority & Conflict Resolution
Rules have different severity levels with this priority order: **mandatory > recommended > informational**.

When checking a paragraph, two or more rules may give CONTRADICTORY advice for the same
text. Examples of contradictions:
- Rule A (mandatory): "Capitalise the first letter of a sentence" → wants uppercase.
  Rule B (informational): "Use sentence case for policy brief titles" → wants lowercase.
- Rule A says "use title case" while Rule B says "use sentence case".
- Rule A says "capitalise X" while Rule B says "use lowercase for X".
- Rule A says "keep the text as-is" while Rule B says "replace the text".

When you detect such contradictions:
1. **Only report the violation for the higher-severity rule.** A mandatory rule always
   overrides an informational or recommended rule. A recommended rule overrides informational.
2. If both have the **same severity**, prefer the **more specific** rule — e.g. a rule about
   title formatting for a specific document type (policy brief titles) is more specific than a
   general capitalisation rule.
3. **Do NOT report both contradictory violations.** The user must not see one finding saying
   "capitalise this" and another saying "make this lowercase" on the same text.
4. In the winning violation's explanation, briefly note which other rule was considered but
   overridden, e.g. "Note: rule_62 (sentence case for titles) does not apply here because
   rule_59 (mandatory: capitalise sentence starts) takes priority."

### Fix classification
For each violation, classify whether it can be fixed automatically in Word:
- **fix_type** — one of:
  - `"remove_formatting"` — remove bold, italic, underline, etc. from the violated runs.
  - `"replace_text"` — replace the violated text with corrected text.
  - `"apply_style"` — apply a named Word style to the paragraph / runs.
  - `"manual"` — no automatic fix possible; the author must decide.
- **fix_value** — depends on fix_type:
  - For `remove_formatting`: comma-separated formatting to remove, e.g. `"bold,italic"`.
  - For `replace_text`: the corrected replacement text.
  - For `apply_style`: the style name to apply, e.g. `"O.N.E Author Body Text"`.
  - For `manual`: leave empty `""`.

### Response format (strict JSON)
{
  "is_compliant": true | false,
  "violations": [
    {
      "rule_id": "<rule ID from the provided list>",
      "violated_text": "<exact text portion that violates>",
      "violated_run_indices": [0, 1],
      "explanation": "<why this violates the rule — mention overridden rules if applicable>",
      "suggestion": "<concrete fix description>",
      "confidence": 0.95,
      "fix_type": "remove_formatting",
      "fix_value": "bold,italic"
    }
  ]
}

CRITICAL: Only return violations that are REAL. If a paragraph follows a rule correctly,
that rule must NOT appear in the violations array. An empty violations array is perfectly
acceptable and preferred over including false positives.
When two rules contradict each other on the same text, report ONLY the higher-priority one.
Do NOT wrap the JSON in markdown fences — return raw JSON only.
"""


# ──────────────────────────────────────────────────────────────────────
# Rule pre-filtering
# ──────────────────────────────────────────────────────────────────────

# Section keywords that signal formatting-specific rules
_FORMATTING_SECTIONS = {
    "italic", "roman", "bold", "font", "typeface", "underline",
    "capitalisation", "capitalization", "caps",
}
_HYPHEN_SECTIONS = {"hyphen", "hyphenation"}
_NUMBER_SECTIONS = {"number", "numeral", "unit", "measure", "decimal", "percent"}
_DATE_SECTIONS = {"date", "time"}
_ABBREV_SECTIONS = {"abbreviation", "acronym", "sign"}
_REFERENCE_SECTIONS = {"bibliograph", "reference", "citation", "footnote", "note"}
_PUNCTUATION_SECTIONS = {"punctuation", "comma", "colon", "semicolon", "bracket", "parenthes"}


def prefilter_rules(
    rules: Sequence[RuleInfo],
    paragraph: ParsedParagraph,
) -> list[RuleInfo]:
    """Select the subset of rules likely relevant to *paragraph*.

    The strategy:
    - **Always include** rules without keywords (generic / catch-all).
    - **Include** rules whose keywords appear in the paragraph text.
    - **Include** formatting rules if the paragraph has non-plain formatting.
    - **Include** section-specific rules based on paragraph content signals:
      hyphens → hyphenation rules, digits → number rules, etc.

    Returns at least 20 % of all rules to avoid over-pruning.
    """
    text_lower = paragraph.plain_text.lower()
    fmt = paragraph.formatting_summary.lower()
    has_formatting = fmt != "plain"

    selected: list[RuleInfo] = []
    for rule in rules:
        if _rule_matches(rule, text_lower, fmt, has_formatting):
            selected.append(rule)

    # Safety floor: always include at least 20% of rules
    min_rules = max(20, len(rules) // 5)
    if len(selected) < min_rules:
        # Fill up from unselected rules (prefer those with low keywords count)
        remaining = [r for r in rules if r not in selected]
        remaining.sort(key=lambda r: len(r.keywords))
        selected.extend(remaining[: min_rules - len(selected)])

    logger.debug(
        "Pre-filtered %d → %d rules for P%d (%s)",
        len(rules), len(selected), paragraph.paragraph_index,
        paragraph.formatting_summary,
    )
    return selected


def _rule_matches(
    rule: RuleInfo,
    text_lower: str,
    fmt_lower: str,
    has_formatting: bool,
) -> bool:
    """Decide whether *rule* is potentially relevant to the paragraph."""
    section = rule.section_lower  # cached lowercase

    # 1. Rules without keywords are generic → always include
    if not rule.keywords:
        return True

    # 2. Keyword match: any keyword appears in paragraph text
    for kw in rule.keywords_lower:  # cached lowercase
        if kw in text_lower:
            return True

    # 3. Formatting match: if paragraph has bold/italic/etc., include
    #    rules whose section relates to formatting
    if has_formatting and _section_matches(section, _FORMATTING_SECTIONS):
        return True

    # 4. Content-signal matches
    if _has_digits(text_lower) and _section_matches(section, _NUMBER_SECTIONS):
        return True
    if "-" in text_lower and _section_matches(section, _HYPHEN_SECTIONS):
        return True
    if _has_date_like(text_lower) and _section_matches(section, _DATE_SECTIONS):
        return True
    if _has_abbreviation(text_lower) and _section_matches(section, _ABBREV_SECTIONS):
        return True
    if _section_matches(section, _REFERENCE_SECTIONS) and _has_reference_marker(text_lower):
        return True
    if _section_matches(section, _PUNCTUATION_SECTIONS):
        # Punctuation rules almost always apply
        return True

    # 5. Rule text mentions a formatting flag that the paragraph has
    if has_formatting:
        rule_text_lower = rule.rule_text_lower  # cached lowercase
        for flag in fmt_lower.split("+"):
            if flag in rule_text_lower:
                return True

    return False


def _section_matches(section: str, keywords: set[str]) -> bool:
    return any(k in section for k in keywords)


# Pre-compiled regex patterns (compiled once at module load, reused per call)
_RE_DIGITS = re.compile(r"\d")
_RE_DATE_LIKE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}|\b\d{4}\b|january|february|march|april|may|june|july|august|september|october|november|december",
    re.IGNORECASE,
)
_RE_ABBREVIATION = re.compile(r"\b[A-Z]{2,}\b")
_RE_REFERENCE = re.compile(
    r"\(\d{4}\)|\bpp?\.\s*\d|\bop\.\s*cit|ibid|et al\.",
    re.IGNORECASE,
)


def _has_digits(text: str) -> bool:
    return bool(_RE_DIGITS.search(text))


def _has_date_like(text: str) -> bool:
    return bool(_RE_DATE_LIKE.search(text))


def _has_abbreviation(text: str) -> bool:
    return bool(_RE_ABBREVIATION.search(text))


def _has_reference_marker(text: str) -> bool:
    return bool(_RE_REFERENCE.search(text))


# ──────────────────────────────────────────────────────────────────────
# Token estimation
# ──────────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough estimate of token count (~4 chars/token for English)."""
    return len(text) // CHARS_PER_TOKEN


def estimate_prompt_tokens(
    paragraph: ParsedParagraph | None = None,
    rules: Sequence[RuleInfo] | None = None,
    *,
    prebuilt_user_prompt: str | None = None,
) -> int:
    """Estimate the total token count for a single checker prompt.

    When *prebuilt_user_prompt* is supplied the prompt is **not** rebuilt,
    saving a redundant ``build_checker_user_prompt`` call.
    """
    sys_tokens = estimate_tokens(RULE_CHECKER_SYSTEM)
    if prebuilt_user_prompt is not None:
        user_tokens = estimate_tokens(prebuilt_user_prompt)
    else:
        user_prompt = build_checker_user_prompt(paragraph, rules)  # type: ignore[arg-type]
        user_tokens = estimate_tokens(user_prompt)
    return sys_tokens + user_tokens


# ──────────────────────────────────────────────────────────────────────
# User prompt builder
# ──────────────────────────────────────────────────────────────────────

def build_checker_user_prompt(
    paragraph: ParsedParagraph,
    rules: Sequence[RuleInfo],
    *,
    compact: bool = False,
) -> str:
    """Build the user message that pairs one paragraph with a rule batch.

    Parameters
    ----------
    compact:
        When True, truncate long rule_text to save tokens.
    """
    runs_detail = _format_runs(paragraph.runs)
    rules_text = _format_rules(rules, compact=compact)

    # Show resolved style name when available, else raw ID
    style_display = paragraph.style_name or paragraph.style or "(none)"
    if paragraph.style_name and paragraph.style and paragraph.style_name != paragraph.style:
        style_display = f"{paragraph.style_name} (id: {paragraph.style})"

    return (
        "## Paragraph to check\n"
        f"**Index:** {paragraph.paragraph_index}\n"
        f"**Style:** {style_display}\n"
        f"**Formatting:** {paragraph.formatting_summary}\n"
        f"**Text:** {paragraph.plain_text}\n\n"
        "### Runs detail\n"
        f"{runs_detail}\n\n"
        "---\n"
        "## Rules\n"
        f"{rules_text}\n\n"
        "---\n"
        "Check this paragraph against ALL the rules above. "
        "Report only clear violations."
    )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _format_runs(runs: Sequence[TextRun]) -> str:
    lines: list[str] = []
    for r in runs:
        fmt = r.formatting.summary()
        lines.append(f"  [{r.run_index}] \"{r.text}\" — {fmt}")
    return "\n".join(lines) if lines else "  (no runs)"


def _format_rules(rules: Sequence[RuleInfo], *, compact: bool = False) -> str:
    lines: list[str] = []
    for r in rules:
        tag = f"[{r.rule_type.upper()}]" if r.rule_type != "unspecified" else ""
        text = r.rule_text
        if compact and len(text) > RULE_TRUNCATE_LEN:
            text = text[:RULE_TRUNCATE_LEN] + "…"
        lines.append(
            f"- **{r.rule_id}** {tag} ({r.severity}): {text}"
        )
    return "\n".join(lines) if lines else "(no rules)"
