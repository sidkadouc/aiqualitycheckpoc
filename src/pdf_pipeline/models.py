"""
Data models for the PDF extraction pipeline.
Pydantic models for type safety and serialization.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Raw extraction models (from Document Intelligence)
# =============================================================================

class BlockRole(str, Enum):
    """Roles detected by Document Intelligence layout analysis."""
    TITLE = "title"
    SECTION_HEADING = "sectionHeading"
    PAGE_HEADER = "pageHeader"
    PAGE_FOOTER = "pageFooter"
    PAGE_NUMBER = "pageNumber"
    FOOTNOTE = "footnote"
    BODY = "body"  # default paragraph
    UNKNOWN = "unknown"


class RawBlock(BaseModel):
    """A raw text block extracted from Document Intelligence."""
    text: str
    role: BlockRole = BlockRole.BODY
    page: int
    confidence: float = 1.0
    span_offset: int = 0
    span_length: int = 0


class RawTable(BaseModel):
    """A table extracted from the PDF."""
    page: int
    row_count: int
    column_count: int
    cells: list[dict] = Field(default_factory=list)
    markdown: str = ""


class RawExtractionResult(BaseModel):
    """Full result from Document Intelligence extraction."""
    document_name: str
    total_pages: int
    blocks: list[RawBlock] = Field(default_factory=list)
    tables: list[RawTable] = Field(default_factory=list)


# =============================================================================
# Structured document models (after hierarchy reconstruction)
# =============================================================================

class Paragraph(BaseModel):
    """A paragraph within a section."""
    id: str
    text: str
    page: int
    original_role: BlockRole = BlockRole.BODY
    word_count: int = 0


class Section(BaseModel):
    """A document section with title and paragraphs."""
    section_id: str
    title: str
    level: int = 1  # heading level (1=chapter, 2=section, 3=subsection)
    page_start: int = 0
    page_end: int = 0
    paragraphs: list[Paragraph] = Field(default_factory=list)
    subsections: list[Section] = Field(default_factory=list)


class StructuredDocument(BaseModel):
    """Complete structured document with hierarchy."""
    document_name: str
    total_pages: int
    total_sections: int = 0
    total_paragraphs: int = 0
    sections: list[Section] = Field(default_factory=list)


# =============================================================================
# Chunk models (after semantic chunking)
# =============================================================================

class Chunk(BaseModel):
    """A semantic chunk ready for indexing."""
    chunk_id: str
    section_id: str
    section_title: str
    content: str
    page_start: int
    page_end: int
    token_count: int = 0
    paragraph_ids: list[str] = Field(default_factory=list)


# =============================================================================
# Rule / Policy models (after LLM extraction)
# =============================================================================

class RuleSeverity(str, Enum):
    """Severity level of a normative rule."""
    MANDATORY = "mandatory"       # MUST / SHALL / OBLIGATOIRE
    RECOMMENDED = "recommended"   # SHOULD / DEVRAIT
    OPTIONAL = "optional"         # MAY / PEUT
    INFORMATIONAL = "informational"


class RuleType(str, Enum):
    """Whether the rule comes from a Do, Don't, or unspecified context."""
    DO = "do"
    DONT = "dont"
    UNSPECIFIED = "unspecified"


class PolicyRule(BaseModel):
    """A single normative rule extracted from the document."""
    rule_id: str
    section_id: str
    section_title: str
    rule_text: str
    rule_summary: str = ""
    severity: RuleSeverity = RuleSeverity.RECOMMENDED
    rule_type: RuleType = RuleType.UNSPECIFIED
    keywords: list[str] = Field(default_factory=list)
    page: int = 0
    source_chunk_id: str = ""


# =============================================================================
# Reference table models (glossaries, lookup tables)
# =============================================================================

class ReferenceEntry(BaseModel):
    """A single entry in a reference table (e.g. abbreviation -> full form)."""
    key: str              # e.g. "cf.", "e.g.", "Dr, Drs"
    value: str            # e.g. "compare or refer to", "Doctor, Doctors"
    note: str = ""        # optional extra info


class ReferenceTable(BaseModel):
    """A reference/lookup table extracted from the document."""
    table_id: str
    section_id: str
    section_title: str
    category: str         # e.g. "Academic writing", "Contracted titles", "Statistics"
    entries: list[ReferenceEntry] = Field(default_factory=list)
    page: int = 0
    source_chunk_id: str = ""


# =============================================================================
# Section summary for hierarchy tracking
# =============================================================================

class SectionSummary(BaseModel):
    """Lightweight summary of a document section and its contents."""
    section_id: str
    section_title: str
    parent_section: str = ""          # title of parent section
    content_types: list[str] = Field(default_factory=list)  # e.g. ["rules:do", "rules:dont", "reference_table"]
    rule_count: int = 0
    reference_table_count: int = 0


class PolicyRuleSet(BaseModel):
    """Complete set of extracted policy rules and reference tables."""
    document_name: str
    total_rules: int = 0
    total_reference_tables: int = 0
    sections: list[SectionSummary] = Field(default_factory=list)
    rules: list[PolicyRule] = Field(default_factory=list)
    reference_tables: list[ReferenceTable] = Field(default_factory=list)


# =============================================================================
# Index models (for AI Search)
# =============================================================================

class SearchIndexDocument(BaseModel):
    """Document to be indexed in Azure AI Search."""
    id: str
    chunk_id: str
    section_id: str
    section_title: str
    content: str
    page_start: int
    page_end: int
    token_count: int = 0
    document_name: str = ""
    embedding: list[float] = Field(default_factory=list)
    # Related rules
    rule_ids: list[str] = Field(default_factory=list)
    rule_summaries: list[str] = Field(default_factory=list)
