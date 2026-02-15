"""
STEP 4 — Chunking sémantique contrôlé.

Stratégie :
- Un chunk = max N tokens (configurable, défaut 800)
- Respect des paragraphes complets (jamais couper au milieu d'une phrase)
- Aligné sur les sections / sous-sections
- Regroupement intelligent de paragraphes courts
- Métadonnées conservées (section, pages)
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import PipelineConfig
from .models import Chunk, Paragraph, Section, StructuredDocument

logger = logging.getLogger(__name__)


def estimate_tokens(text: str, factor: float = 1.4) -> int:
    """
    Estimate token count for a text string.
    Factor 1.4 is a reasonable approximation for French text with GPT tokenizers.
    """
    return int(len(text.split()) * factor)


def chunk_document(
    doc: StructuredDocument,
    pipeline_cfg: PipelineConfig,
) -> list[Chunk]:
    """
    Create semantic chunks from the structured document.

    Algorithm:
    1. Flatten sections (including subsections) into a processing list
    2. For each section, accumulate paragraphs into chunks
    3. Respect max token limit, never break mid-paragraph
    4. Merge very short sections with the previous chunk when possible
    """
    logger.info("Starting semantic chunking (max %d tokens)", pipeline_cfg.max_chunk_tokens)

    max_tokens = pipeline_cfg.max_chunk_tokens
    min_tokens = pipeline_cfg.min_chunk_tokens
    factor = pipeline_cfg.token_estimation_factor

    all_chunks: list[Chunk] = []
    chunk_counter = 0

    # Flatten all sections (with their full path for context)
    flat_sections = _flatten_sections(doc.sections)

    for section_id, section_title, paragraphs in flat_sections:
        if not paragraphs:
            continue

        current_texts: list[str] = []
        current_para_ids: list[str] = []
        current_tokens = 0
        current_page_start = paragraphs[0].page
        current_page_end = paragraphs[0].page

        for para in paragraphs:
            para_tokens = estimate_tokens(para.text, factor)

            # Special case: single paragraph exceeds max → it becomes its own chunk
            if para_tokens >= max_tokens:
                # Flush current accumulator first
                if current_texts:
                    chunk_counter += 1
                    all_chunks.append(
                        Chunk(
                            chunk_id=f"chunk_{chunk_counter}",
                            section_id=section_id,
                            section_title=section_title,
                            content="\n\n".join(current_texts),
                            page_start=current_page_start,
                            page_end=current_page_end,
                            token_count=current_tokens,
                            paragraph_ids=current_para_ids.copy(),
                        )
                    )
                    current_texts = []
                    current_para_ids = []
                    current_tokens = 0

                # Create chunk for the oversized paragraph
                chunk_counter += 1
                # Split by sentences to stay under token limit
                sub_chunks = _split_long_paragraph(
                    para, section_id, section_title, max_tokens, factor, chunk_counter
                )
                all_chunks.extend(sub_chunks)
                chunk_counter += len(sub_chunks) - 1  # counter already incremented once
                continue

            # If adding this paragraph would exceed limit → flush
            if current_tokens + para_tokens > max_tokens and current_texts:
                chunk_counter += 1
                all_chunks.append(
                    Chunk(
                        chunk_id=f"chunk_{chunk_counter}",
                        section_id=section_id,
                        section_title=section_title,
                        content="\n\n".join(current_texts),
                        page_start=current_page_start,
                        page_end=current_page_end,
                        token_count=current_tokens,
                        paragraph_ids=current_para_ids.copy(),
                    )
                )
                current_texts = []
                current_para_ids = []
                current_tokens = 0
                current_page_start = para.page

            # Accumulate paragraph
            current_texts.append(para.text)
            current_para_ids.append(para.id)
            current_tokens += para_tokens
            current_page_end = para.page

        # Flush remaining content for this section
        if current_texts:
            chunk_counter += 1
            all_chunks.append(
                Chunk(
                    chunk_id=f"chunk_{chunk_counter}",
                    section_id=section_id,
                    section_title=section_title,
                    content="\n\n".join(current_texts),
                    page_start=current_page_start,
                    page_end=current_page_end,
                    token_count=current_tokens,
                    paragraph_ids=current_para_ids.copy(),
                )
            )

    # Post-process: merge very small chunks with neighbors in the same section
    all_chunks = _merge_tiny_chunks(all_chunks, min_tokens, max_tokens, factor)

    # Reindex chunk IDs
    for i, chunk in enumerate(all_chunks, 1):
        chunk.chunk_id = f"chunk_{i}"

    logger.info(
        "Chunking complete: %d chunks created (avg ~%d tokens)",
        len(all_chunks),
        sum(c.token_count for c in all_chunks) // max(len(all_chunks), 1),
    )

    # Save intermediate result
    if pipeline_cfg.save_intermediate_files:
        import json
        output_dir = Path(pipeline_cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "04_chunks.json"
        path.write_text(
            json.dumps(
                [c.model_dump() for c in all_chunks],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        logger.info("Chunks saved to: %s", path)

    return all_chunks


def _flatten_sections(
    sections: list[Section], parent_title: str = "",
) -> list[tuple[str, str, list[Paragraph]]]:
    """
    Flatten nested sections into (section_id, full_title, paragraphs) tuples.
    Subsection titles are prefixed with parent title for context.
    """
    result = []
    for section in sections:
        full_title = (
            f"{parent_title} > {section.title}" if parent_title else section.title
        )
        if section.paragraphs:
            result.append((section.section_id, full_title, section.paragraphs))

        if section.subsections:
            result.extend(_flatten_sections(section.subsections, full_title))

    return result


def _split_long_paragraph(
    para: Paragraph,
    section_id: str,
    section_title: str,
    max_tokens: int,
    factor: float,
    start_counter: int,
) -> list[Chunk]:
    """
    Split a paragraph that exceeds max_tokens by sentences.
    Never splits mid-sentence.
    """
    import re

    # Split by sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", para.text)
    chunks = []
    current_text = ""
    current_tokens = 0
    counter = start_counter

    for sentence in sentences:
        sent_tokens = estimate_tokens(sentence, factor)

        if current_tokens + sent_tokens > max_tokens and current_text:
            chunks.append(
                Chunk(
                    chunk_id=f"chunk_{counter}",
                    section_id=section_id,
                    section_title=section_title,
                    content=current_text.strip(),
                    page_start=para.page,
                    page_end=para.page,
                    token_count=current_tokens,
                    paragraph_ids=[para.id],
                )
            )
            counter += 1
            current_text = ""
            current_tokens = 0

        current_text += " " + sentence
        current_tokens += sent_tokens

    if current_text.strip():
        chunks.append(
            Chunk(
                chunk_id=f"chunk_{counter}",
                section_id=section_id,
                section_title=section_title,
                content=current_text.strip(),
                page_start=para.page,
                page_end=para.page,
                token_count=current_tokens,
                paragraph_ids=[para.id],
            )
        )

    return chunks


def _merge_tiny_chunks(
    chunks: list[Chunk],
    min_tokens: int,
    max_tokens: int,
    factor: float,
) -> list[Chunk]:
    """Merge chunks that are below min_tokens with the next chunk in the same section."""
    if not chunks:
        return chunks

    merged = []
    i = 0

    while i < len(chunks):
        chunk = chunks[i]

        # If this chunk is tiny and the next one is in the same section, merge
        if (
            chunk.token_count < min_tokens
            and i + 1 < len(chunks)
            and chunks[i + 1].section_id == chunk.section_id
        ):
            next_chunk = chunks[i + 1]
            combined_tokens = chunk.token_count + next_chunk.token_count

            if combined_tokens <= max_tokens:
                # Merge into next chunk
                next_chunk.content = chunk.content + "\n\n" + next_chunk.content
                next_chunk.token_count = combined_tokens
                next_chunk.page_start = min(chunk.page_start, next_chunk.page_start)
                next_chunk.paragraph_ids = chunk.paragraph_ids + next_chunk.paragraph_ids
                i += 1
                continue

        merged.append(chunk)
        i += 1

    return merged
