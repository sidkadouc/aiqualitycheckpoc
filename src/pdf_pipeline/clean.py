"""
STEP 3 — Nettoyage et normalisation du texte.

Élimine :
- Headers/footers répétitifs
- Numéros de page orphelins
- Artefacts de conversion (tirets de coupure, espaces multiples)
- Caractères spéciaux non-standard
- Tables brisées
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .config import PipelineConfig
from .models import Paragraph, Section, StructuredDocument

logger = logging.getLogger(__name__)


def clean_document(
    doc: StructuredDocument,
    pipeline_cfg: PipelineConfig,
) -> StructuredDocument:
    """
    Clean and normalize all text in the structured document.
    Operates in-place on the document and returns it.
    """
    logger.info("Cleaning document: %s", doc.document_name)

    # Detect repetitive headers/footers across pages
    repetitive_texts = _detect_repetitive_texts(doc)

    # Clean each section recursively
    for section in doc.sections:
        _clean_section(section, repetitive_texts)

    # Remove empty paragraphs and sections after cleaning
    doc.sections = [s for s in doc.sections if _has_content(s)]

    # Recount
    doc.total_paragraphs = sum(_count_paragraphs(s) for s in doc.sections)

    logger.info(
        "Cleaning complete: %d sections, %d paragraphs remaining",
        doc.total_sections,
        doc.total_paragraphs,
    )

    # Save intermediate result
    if pipeline_cfg.save_intermediate_files:
        output_dir = Path(pipeline_cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "03_cleaned_document.json"
        path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Cleaned document saved to: %s", path)

    return doc


def _clean_section(section: Section, repetitive_texts: set[str]) -> None:
    """Recursively clean a section and its subsections."""
    # Clean section title
    section.title = _clean_text(section.title)

    # Clean paragraphs
    cleaned_paragraphs = []
    for para in section.paragraphs:
        cleaned = _clean_text(para.text)

        # Skip if it's a known repetitive text
        if _normalize_for_comparison(cleaned) in repetitive_texts:
            continue

        # Skip if the cleaned text is too short
        if len(cleaned.strip()) < 5:
            continue

        para.text = cleaned
        para.word_count = len(cleaned.split())
        cleaned_paragraphs.append(para)

    section.paragraphs = cleaned_paragraphs

    # Recurse into subsections
    for sub in section.subsections:
        _clean_section(sub, repetitive_texts)

    # Remove empty subsections
    section.subsections = [s for s in section.subsections if _has_content(s)]


def _clean_text(text: str) -> str:
    """Apply all cleaning transformations to a text string."""
    if not text:
        return text

    # 1. Unicode normalization (NFC)
    text = unicodedata.normalize("NFC", text)

    # 2. Replace common problematic characters
    text = text.replace("\u00a0", " ")    # non-breaking space
    text = text.replace("\u200b", "")     # zero-width space
    text = text.replace("\u200c", "")     # zero-width non-joiner
    text = text.replace("\u200d", "")     # zero-width joiner
    text = text.replace("\ufeff", "")     # BOM
    text = text.replace("\u2028", "\n")   # line separator
    text = text.replace("\u2029", "\n")   # paragraph separator

    # 3. Fix broken hyphenation (word- \n continuation)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)

    # 4. Normalize dashes and quotes
    text = text.replace("–", "-")   # en-dash to hyphen where appropriate
    text = text.replace("—", " - ") # em-dash
    text = text.replace(""", '"')
    text = text.replace(""", '"')
    text = text.replace("'", "'")
    text = text.replace("'", "'")
    text = text.replace("«", '"')
    text = text.replace("»", '"')

    # 5. Collapse multiple newlines into one
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 6. Collapse multiple spaces
    text = re.sub(r"[ \t]{2,}", " ", text)

    # 7. Remove leading/trailing whitespace per line
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    # 8. Remove orphan page numbers (standalone numbers)
    text = re.sub(r"^\d{1,3}$", "", text, flags=re.MULTILINE)

    # 9. Strip overall
    text = text.strip()

    return text


def _detect_repetitive_texts(doc: StructuredDocument) -> set[str]:
    """
    Detect texts that appear on many pages (likely headers/footers).
    Returns a set of normalized texts to filter out.
    """
    text_counter: Counter[str] = Counter()

    for section in doc.sections:
        for para in _iter_all_paragraphs(section):
            normalized = _normalize_for_comparison(para.text)
            if len(normalized) < 100:  # only check short texts
                text_counter[normalized] += 1

    # If a text appears on more than 20% of pages, it's likely repetitive
    threshold = max(3, doc.total_pages * 0.2)
    repetitive = {text for text, count in text_counter.items() if count >= threshold}

    if repetitive:
        logger.info("Detected %d repetitive text patterns (headers/footers)", len(repetitive))

    return repetitive


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison (lowercase, no extra spaces)."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _iter_all_paragraphs(section: Section):
    """Iterate all paragraphs in a section and its subsections."""
    yield from section.paragraphs
    for sub in section.subsections:
        yield from _iter_all_paragraphs(sub)


def _has_content(section: Section) -> bool:
    """Check if a section has any content (paragraphs or non-empty subsections)."""
    if section.paragraphs:
        return True
    return any(_has_content(sub) for sub in section.subsections)


def _count_paragraphs(section: Section) -> int:
    """Recursively count paragraphs."""
    count = len(section.paragraphs)
    for sub in section.subsections:
        count += _count_paragraphs(sub)
    return count
