"""
STEP 1 — Extraction structurée du PDF via Azure Document Intelligence.

Utilise le modèle prebuilt-layout pour récupérer :
- Paragraphes avec rôles (heading, body, footer, etc.)
- Tables
- Positions et pages
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import (
    AnalyzeDocumentRequest,
    AnalyzeResult,
    DocumentContentFormat,
    DocumentAnalysisFeature,
)
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential

from .config import AzureConfig, PipelineConfig
from .models import BlockRole, RawBlock, RawExtractionResult, RawTable

logger = logging.getLogger(__name__)


# Mapping from Document Intelligence roles to our BlockRole enum
_ROLE_MAP: dict[str | None, BlockRole] = {
    "title": BlockRole.TITLE,
    "sectionHeading": BlockRole.SECTION_HEADING,
    "pageHeader": BlockRole.PAGE_HEADER,
    "pageFooter": BlockRole.PAGE_FOOTER,
    "pageNumber": BlockRole.PAGE_NUMBER,
    "footnote": BlockRole.FOOTNOTE,
    None: BlockRole.BODY,
}


def _get_client(azure_cfg: AzureConfig) -> DocumentIntelligenceClient:
    """Create Document Intelligence client using key or managed identity."""
    endpoint = azure_cfg.doc_intelligence_endpoint
    if not endpoint:
        raise ValueError(
            "AZURE_CONTENT_UNDERSTANDING_ENDPOINT or AZURE_OPENAI_ENDPOINT must be set."
        )

    key = azure_cfg.doc_intelligence_key or azure_cfg.openai_key
    if key and not azure_cfg.use_managed_identity:
        credential = AzureKeyCredential(key)
    else:
        credential = DefaultAzureCredential()

    return DocumentIntelligenceClient(endpoint=endpoint, credential=credential)


def extract_pdf(
    pdf_path: str | Path,
    azure_cfg: AzureConfig,
    pipeline_cfg: PipelineConfig,
) -> RawExtractionResult:
    """
    Extract structured text from a PDF using Azure Document Intelligence (prebuilt-layout).

    Returns a RawExtractionResult with all blocks (paragraphs with roles) and tables.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info("Starting Document Intelligence extraction for: %s", pdf_path.name)

    client = _get_client(azure_cfg)

    with open(pdf_path, "rb") as f:
        poller = client.begin_analyze_document(
            "prebuilt-layout",
            body=f,
            output_content_format=DocumentContentFormat.MARKDOWN,
            features=[DocumentAnalysisFeature.STYLE_FONT],
        )

    result = poller.result()

    # ------------------------------------------------------------------
    # Extract paragraphs with roles
    # ------------------------------------------------------------------
    blocks: list[RawBlock] = []
    if result.paragraphs:
        for para in result.paragraphs:
            page_number = (
                para.bounding_regions[0].page_number
                if para.bounding_regions
                else 1
            )
            role = _ROLE_MAP.get(para.role, BlockRole.UNKNOWN)

            blocks.append(
                RawBlock(
                    text=para.content,
                    role=role,
                    page=page_number,
                    confidence=para.confidence if hasattr(para, "confidence") else 1.0,
                    span_offset=para.spans[0].offset if para.spans else 0,
                    span_length=para.spans[0].length if para.spans else len(para.content),
                )
            )

    # Sort blocks by page then by offset for correct reading order
    blocks.sort(key=lambda b: (b.page, b.span_offset))

    # ------------------------------------------------------------------
    # Extract tables
    # ------------------------------------------------------------------
    tables: list[RawTable] = []
    if result.tables:
        for table in result.tables:
            page_number = (
                table.bounding_regions[0].page_number
                if table.bounding_regions
                else 1
            )
            cells = []
            for cell in table.cells:
                cells.append(
                    {
                        "row": cell.row_index,
                        "col": cell.column_index,
                        "text": cell.content,
                        "kind": cell.kind if hasattr(cell, "kind") else "content",
                    }
                )

            # Generate markdown representation of the table
            md = _table_to_markdown(table)

            tables.append(
                RawTable(
                    page=page_number,
                    row_count=table.row_count,
                    column_count=table.column_count,
                    cells=cells,
                    markdown=md,
                )
            )

    total_pages = len(result.pages) if result.pages else 0

    extraction = RawExtractionResult(
        document_name=pdf_path.stem,
        total_pages=total_pages,
        blocks=blocks,
        tables=tables,
    )

    logger.info(
        "Extraction complete: %d pages, %d blocks, %d tables",
        total_pages,
        len(blocks),
        len(tables),
    )

    # Save intermediate result if configured
    if pipeline_cfg.save_intermediate_files:
        output_dir = Path(pipeline_cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_path = output_dir / "01_raw_layout.json"
        raw_path.write_text(
            extraction.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info("Raw extraction saved to: %s", raw_path)

    return extraction


def _table_to_markdown(table) -> str:
    """Convert a Document Intelligence table to Markdown format."""
    rows: dict[int, dict[int, str]] = {}
    for cell in table.cells:
        row_idx = cell.row_index
        col_idx = cell.column_index
        if row_idx not in rows:
            rows[row_idx] = {}
        rows[row_idx][col_idx] = cell.content.replace("\n", " ").strip()

    if not rows:
        return ""

    lines = []
    max_col = table.column_count

    for row_idx in sorted(rows.keys()):
        cells = [rows[row_idx].get(c, "") for c in range(max_col)]
        lines.append("| " + " | ".join(cells) + " |")
        # Add header separator after first row
        if row_idx == 0:
            lines.append("| " + " | ".join(["---"] * max_col) + " |")

    return "\n".join(lines)
