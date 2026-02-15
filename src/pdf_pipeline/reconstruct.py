"""
STEP 2 — Reconstruction hiérarchique du document.

Transforme les blocs bruts (RawExtractionResult) en un arbre structuré
(StructuredDocument) avec sections, sous-sections et paragraphes.

Stratégie :
1. Détecter les headings (title, sectionHeading) depuis les rôles Document Intelligence
2. Utiliser des heuristiques complémentaires (numérotation, longueur, casse)
3. Construire un arbre hiérarchique section → sous-section → paragraphes
4. Reconstruire une TOC implicite même si le PDF n'en contient pas
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .config import PipelineConfig
from .models import (
    BlockRole,
    Paragraph,
    RawBlock,
    RawExtractionResult,
    Section,
    StructuredDocument,
)

logger = logging.getLogger(__name__)

# Patterns for detecting numbered headings (e.g., "1.", "1.2", "1.2.3", "Chapitre 1")
_NUMBERED_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:chapitre|chapter|section|partie|part|annexe|annex)\s+\d+|"  # Chapitre 1, Annex 2
    r"\d+(?:\.\d+){0,3}\.?\s"  # 1. , 1.2 , 1.2.3
    r")",
    re.IGNORECASE,
)

# Pattern for Roman numeral headings (I., II., III., IV., etc.)
_ROMAN_HEADING_RE = re.compile(
    r"^(?:(?:X{0,3})(IX|IV|V?I{0,3}))\.?\s",
    re.IGNORECASE,
)


# Known short headings that Document Intelligence may mis-classify as body text.
# "Do" is systematically reported as role=body while "Don't" gets sectionHeading.
_KNOWN_SHORT_HEADINGS = {
    "do", "do's", "don't", "don'ts", "dont",
}

# Pattern to detect URL fragments that should NOT be treated as headings
_URL_FRAGMENT_RE = re.compile(
    r"(?:https?://|www\.|[a-f0-9]{6,}-[a-z]{2}/|\.html|\.aspx|\.pdf)",
    re.IGNORECASE,
)


def _infer_heading_level(
    text: str,
    role: BlockRole,
    last_numbered_level: int = 1,
) -> int | None:
    """
    Infer the heading level from role and text pattern.

    Args:
        text: block text
        role: Document Intelligence role
        last_numbered_level: level of the most recent *numbered* heading.
            Non-numbered headings are anchored relative to this so they
            stay as siblings instead of cascading to ever-deeper levels.

    Returns None if the block is not a heading.
    """
    stripped = text.strip()
    stripped_lower = stripped.lower()

    # --- Reject URL fragments that start with digits (e.g. "2025 3713cf73-en/...") ---
    if _URL_FRAGMENT_RE.search(stripped):
        return None

    # --- Known short headings (fix "Do" misclassified as body) ---
    if stripped_lower in _KNOWN_SHORT_HEADINGS:
        # Do / Don't sit two levels below the last numbered heading
        return last_numbered_level + 2

    if role == BlockRole.TITLE:
        return 1

    if role == BlockRole.SECTION_HEADING:
        # Detect sub-level from numbering pattern
        match = _NUMBERED_HEADING_RE.match(stripped)
        if match:
            matched = match.group(0).strip()
            # Count dots to determine depth: "1." = level 1, "1.2" = level 2
            dots = matched.count(".")
            # Headings starting with "Chapitre", "Part" are always level 1
            if re.match(r"(?:chapitre|chapter|partie|part)", matched, re.IGNORECASE):
                return 1
            if re.match(r"(?:annexe|annex)", matched, re.IGNORECASE):
                return 1
            return min(dots + 1, 4)  # Cap at level 4

        # Non-numbered sectionHeading: one level below last numbered heading.
        # All non-numbered headings under the same numbered parent stay flat.
        return last_numbered_level + 1

    # Heuristic: body text that looks like a heading
    if role == BlockRole.BODY:
        # Short, uppercase text is likely a heading
        if len(stripped) < 120 and stripped.isupper() and len(stripped.split()) >= 2:
            return last_numbered_level + 1
        # Numbered heading pattern in body text
        if _NUMBERED_HEADING_RE.match(stripped) and len(stripped) < 200:
            dots = stripped.split()[0].count(".")
            return min(dots + 1, 4)

    return None


def _should_skip_block(block: RawBlock, cfg: PipelineConfig) -> bool:
    """Determine if a block should be skipped (headers, footers, page numbers)."""
    if cfg.remove_page_headers and block.role == BlockRole.PAGE_HEADER:
        return True
    if cfg.remove_page_footers and block.role == BlockRole.PAGE_FOOTER:
        return True
    if cfg.remove_page_numbers and block.role == BlockRole.PAGE_NUMBER:
        return True
    # Skip very short blocks that are likely artifacts
    # BUT exempt known short headings like "Do" (only 2 chars)
    stripped_lower = block.text.strip().lower()
    if len(block.text.strip()) < 3 and stripped_lower not in _KNOWN_SHORT_HEADINGS:
        return True
    return False


def reconstruct_hierarchy(
    raw: RawExtractionResult,
    pipeline_cfg: PipelineConfig,
) -> StructuredDocument:
    """
    Build a hierarchical document structure from raw extraction blocks.

    Algorithm:
    1. Filter out headers/footers/page numbers
    2. Classify each block as heading (with level) or paragraph
    3. Build a flat list of sections, each collecting child paragraphs
    4. Nest subsections under parent sections based on heading level
    """
    logger.info("Reconstructing document hierarchy from %d blocks", len(raw.blocks))

    # Phase 1: Classify blocks
    section_stack: list[Section] = []
    all_sections: list[Section] = []
    para_counter = 0
    section_counter = 0

    # Create a default section for paragraphs before the first heading
    default_section = Section(
        section_id="sec_0",
        title="Préambule",
        level=0,
        page_start=1,
    )
    current_section = default_section
    last_numbered_level = 1  # Level of the most recent *numbered* heading

    for block in raw.blocks:
        if _should_skip_block(block, pipeline_cfg):
            continue

        heading_level = _infer_heading_level(block.text, block.role, last_numbered_level)

        if heading_level is not None:
            # Save current section if it has content
            if current_section.paragraphs:
                current_section.page_end = current_section.paragraphs[-1].page
                all_sections.append(current_section)

            # Update last_numbered_level only for numbered headings
            if _NUMBERED_HEADING_RE.match(block.text.strip()):
                last_numbered_level = heading_level

            section_counter += 1
            current_section = Section(
                section_id=f"sec_{section_counter}",
                title=block.text.strip(),
                level=heading_level,
                page_start=block.page,
            )
        else:
            # It's a paragraph — add to current section
            para_counter += 1
            word_count = len(block.text.split())
            para = Paragraph(
                id=f"para_{para_counter}",
                text=block.text.strip(),
                page=block.page,
                original_role=block.role,
                word_count=word_count,
            )
            current_section.paragraphs.append(para)

    # Don't forget the last section
    if current_section.paragraphs or current_section.title != "Préambule":
        if current_section.paragraphs:
            current_section.page_end = current_section.paragraphs[-1].page
        else:
            current_section.page_end = current_section.page_start
        all_sections.append(current_section)

    # Phase 2: Build nested hierarchy
    root_sections = _nest_sections(all_sections)

    # Count totals
    total_paragraphs = sum(_count_paragraphs(s) for s in root_sections)

    doc = StructuredDocument(
        document_name=raw.document_name,
        total_pages=raw.total_pages,
        total_sections=len(all_sections),
        total_paragraphs=total_paragraphs,
        sections=root_sections,
    )

    logger.info(
        "Hierarchy reconstruction complete: %d sections, %d paragraphs",
        doc.total_sections,
        doc.total_paragraphs,
    )

    # Save intermediate result
    if pipeline_cfg.save_intermediate_files:
        output_dir = Path(pipeline_cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "02_structured_document.json"
        path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Structured document saved to: %s", path)

    return doc


def _nest_sections(flat_sections: list[Section]) -> list[Section]:
    """
    Nest sections based on heading levels.
    Level 1 sections contain level 2 subsections, etc.
    """
    if not flat_sections:
        return []

    root: list[Section] = []
    stack: list[Section] = []

    for section in flat_sections:
        # Pop stack until we find a parent with a lower level
        while stack and stack[-1].level >= section.level:
            stack.pop()

        if stack:
            # This section is a child of the last item on the stack
            stack[-1].subsections.append(section)
        else:
            # This is a root-level section
            root.append(section)

        stack.append(section)

    return root


def _count_paragraphs(section: Section) -> int:
    """Recursively count paragraphs in a section and its subsections."""
    count = len(section.paragraphs)
    for sub in section.subsections:
        count += _count_paragraphs(sub)
    return count
