"""
Data models for the Quality Checker Agent.

All Pydantic / dataclass models used across the quality-check workflow.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing_extensions import Never  # noqa: F401 — re-exported for executors


# ──────────────────────────────────────────────────────────────────────
# OpenXML paragraph models
# ──────────────────────────────────────────────────────────────────────

class FormattingFlags(BaseModel):
    """Formatting properties of a single text run in OpenXML."""

    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    superscript: bool = False
    subscript: bool = False
    all_caps: bool = False
    small_caps: bool = False
    font_name: str | None = None
    font_size_half_pts: int | None = None
    font_color: str | None = None
    highlight_color: str | None = None

    def summary(self) -> str:
        """Return a compact human-readable tag string like 'bold+italic'."""
        flags: list[str] = []
        if self.bold:
            flags.append("bold")
        if self.italic:
            flags.append("italic")
        if self.underline:
            flags.append("underline")
        if self.strikethrough:
            flags.append("strikethrough")
        if self.superscript:
            flags.append("superscript")
        if self.subscript:
            flags.append("subscript")
        if self.all_caps:
            flags.append("ALL_CAPS")
        if self.small_caps:
            flags.append("small_caps")
        return "+".join(flags) if flags else "plain"


class TextRun(BaseModel):
    """A run of text with uniform formatting inside a paragraph."""

    text: str
    formatting: FormattingFlags = Field(default_factory=FormattingFlags)
    run_index: int = 0


class StyleInfo(BaseModel):
    """Definition of a Word style from ``styles.xml``."""

    style_id: str
    name: str = ""  # human-readable name, e.g. "O.N.E Author Body Text"
    style_type: str = "paragraph"  # paragraph | character | table | numbering
    base_formatting: FormattingFlags = Field(default_factory=FormattingFlags)


class ParsedParagraph(BaseModel):
    """A paragraph extracted from an OpenXML document part."""

    paragraph_index: int
    style: str | None = None  # raw style ID, e.g. "Heading1"
    style_name: str | None = None  # resolved display name, e.g. "Heading 1"
    runs: list[TextRun] = Field(default_factory=list)
    plain_text: str = ""
    formatting_summary: str = "plain"
    original_xml: str = ""  # cleaned XML of this <w:p> element


# ──────────────────────────────────────────────────────────────────────
# Workflow messages — passed between executors
# ──────────────────────────────────────────────────────────────────────

class DocumentCheckRequest(BaseModel):
    """Message sent from parser to rule-batch checkers."""

    paragraphs: list[ParsedParagraph] = Field(default_factory=list)
    cleaned_xml: str = ""  # full cleaned document XML (for reference / highlighting)


class RuleInfo(BaseModel):
    """Lightweight view of a single extracted rule (loaded from JSON)."""

    rule_id: str
    rule_text: str
    rule_summary: str = ""
    rule_type: str = "unspecified"  # do / dont / unspecified
    severity: str = "recommended"
    section_title: str = ""
    keywords: list[str] = Field(default_factory=list)
    page: int | None = None  # page in the PDF style guide

    # ── cached derived fields (computed once, reused across all paragraphs) ──
    _keywords_lower: list[str] | None = None
    _section_lower: str | None = None
    _rule_text_lower: str | None = None

    model_config = {"ignored_types": (type(None),)}

    @property
    def keywords_lower(self) -> list[str]:
        if self._keywords_lower is None:
            object.__setattr__(self, "_keywords_lower", [kw.lower() for kw in self.keywords])
        return self._keywords_lower  # type: ignore[return-value]

    @property
    def section_lower(self) -> str:
        if self._section_lower is None:
            object.__setattr__(self, "_section_lower", self.section_title.lower())
        return self._section_lower  # type: ignore[return-value]

    @property
    def rule_text_lower(self) -> str:
        if self._rule_text_lower is None:
            object.__setattr__(self, "_rule_text_lower", self.rule_text.lower())
        return self._rule_text_lower  # type: ignore[return-value]


# ──────────────────────────────────────────────────────────────────────
# Rule-check result models
# ──────────────────────────────────────────────────────────────────────

class RuleViolation(BaseModel):
    """A single rule violation detected in a paragraph."""

    rule_id: str
    rule_text: str = ""
    rule_type: str = "unspecified"
    severity: str = "recommended"
    section_title: str = ""
    page: int | None = None
    paragraph_index: int
    violated_text: str = ""  # the exact text that violates the rule
    violated_run_indices: list[int] = Field(default_factory=list)
    explanation: str = ""
    suggestion: str = ""
    confidence: float = 0.0

    # Fix metadata (populated by LLM or post-processing)
    fix_type: str = "manual"  # remove_formatting | replace_text | apply_style | manual
    fix_value: str = ""  # style name, replacement text, or empty for manual fixes

    # Conflict resolution (populated by conflict_resolver)
    superseded_by: str = ""  # rule_id that overrides this violation
    superseded_reason: str = ""  # why this violation was overridden


class ParagraphResult(BaseModel):
    """Aggregated check result for one paragraph."""

    paragraph_index: int
    plain_text: str = ""
    is_compliant: bool = True
    violations: list[RuleViolation] = Field(default_factory=list)


class BatchCheckResult(BaseModel):
    """Result produced by one rule-batch checker executor."""

    batch_id: str
    rules_checked: int = 0
    paragraph_results: list[ParagraphResult] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Final report — yielded as workflow output
# ──────────────────────────────────────────────────────────────────────

class QualityCheckReport(BaseModel):
    """Complete quality-check report for the document."""

    total_paragraphs: int = 0
    compliant_paragraphs: int = 0
    non_compliant_paragraphs: int = 0
    total_violations: int = 0
    total_rules_checked: int = 0
    paragraph_results: list[ParagraphResult] = Field(default_factory=list)
    highlighted_xml: str = ""  # OpenXML with violations annotated via <w:highlight>


# ──────────────────────────────────────────────────────────────────────
# Word Add-in response models
# ──────────────────────────────────────────────────────────────────────

class AddinFinding(BaseModel):
    """A single finding ready for the Word Web Add-in task pane.

    Each finding has a unique ``id`` that links the side-panel entry to
    the highlighted range in the document.
    """

    id: str  # unique finding ID, e.g. "f-0-rule_151"
    rule_id: str
    rule_text: str = ""
    rule_type: str = "unspecified"  # do / dont / unspecified
    severity: str = "recommended"  # mandatory / recommended

    # Location in document
    doc_paragraph_index: int  # 0-based index in the document
    search_text: str = ""  # exact text to search with body.search() in Office.js
    run_indices: list[int] = Field(default_factory=list)  # violated run indices

    # Readable info for the side panel
    explanation: str = ""
    suggestion: str = ""
    confidence: float = 0.0

    # Rule source reference (for looking up in the PDF style guide)
    section_title: str = ""  # e.g. "3 Capitalisation > General rules"
    page: int | None = None  # page number in the PDF style guide

    # Fix metadata for automatic correction
    fixable: bool = False
    fix_type: str = "manual"  # remove_formatting | replace_text | apply_style | manual
    fix_value: str = ""  # replacement text, style name, or "" for manual
    # Conflict resolution: when another rule supersedes this one
    superseded_by: str = ""  # rule_id of the winning rule, or "" if active
    superseded_reason: str = ""  # human-readable reason
    # Highlight colour (Office.js Word.HighlightColor enum value)
    highlight_color: str = "yellow"  # yellow = warning, red = error


class AddinParagraphGroup(BaseModel):
    """Group of findings for a single paragraph (for panel organisation)."""

    doc_paragraph_index: int
    text_preview: str = ""  # first 120 chars of the paragraph
    finding_count: int = 0
    findings: list[AddinFinding] = Field(default_factory=list)


class AddinResponse(BaseModel):
    """Top-level response consumed by the Word Web Add-in.

    Shape designed for:
    - Side panel: iterate ``paragraphs[].findings[]`` to render cards.
    - Highlights: for each finding use ``search_text`` +
      ``highlight_color`` via Office.js ``body.search()``.
    - Auto-fix: when ``finding.fixable`` is true, apply
      ``fix_type``/``fix_value`` via Office.js range manipulation.
    - Dependency: clicking a finding card scrolls to and highlights
      the matched range; dismissing a finding removes the highlight.
    """

    version: str = "1.0"
    total_paragraphs: int = 0
    total_findings: int = 0
    summary: dict[str, int] = Field(default_factory=dict)  # severity → count
    paragraphs: list[AddinParagraphGroup] = Field(default_factory=list)

    # Raw highlighted OOXML (optional — add-in can also highlight via JS)
    highlighted_ooxml: str = ""
