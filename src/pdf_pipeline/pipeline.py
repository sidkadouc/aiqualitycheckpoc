"""
Pipeline orchestrator — Main entry point.

Coordinates all pipeline steps:
  1. Extract PDF layout (Document Intelligence)
  2. Reconstruct document hierarchy
  3. Clean and normalize text
  4. Semantic chunking
  5. Extract normative rules (LLM)
  6. Index to AI Search + Cosmos DB
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .chunk import chunk_document
from .clean import clean_document
from .config import AzureConfig, PipelineConfig, load_config
from .extract import extract_pdf
from .extract_rules import extract_rules
from .index import index_chunks, store_rules_in_cosmos
from .models import Chunk, PolicyRuleSet, StructuredDocument
from .reconstruct import reconstruct_hierarchy

logger = logging.getLogger(__name__)


class PipelineResult:
    """Container for all pipeline outputs."""

    def __init__(self):
        self.structured_document: StructuredDocument | None = None
        self.chunks: list[Chunk] = []
        self.rules: PolicyRuleSet | None = None
        self.timings: dict[str, float] = {}
        self.errors: list[str] = []

    def summary(self) -> str:
        """Generate a human-readable summary of the pipeline run."""
        lines = ["=" * 60, "PIPELINE EXECUTION SUMMARY", "=" * 60]

        if self.structured_document:
            lines.append(f"Document:    {self.structured_document.document_name}")
            lines.append(f"Pages:       {self.structured_document.total_pages}")
            lines.append(f"Sections:    {self.structured_document.total_sections}")
            lines.append(f"Paragraphs:  {self.structured_document.total_paragraphs}")

        lines.append(f"Chunks:      {len(self.chunks)}")

        if self.rules:
            lines.append(f"Rules:       {self.rules.total_rules}")
            sev = {}
            rt = {}
            for r in self.rules.rules:
                sev[r.severity.value] = sev.get(r.severity.value, 0) + 1
                rt[r.rule_type.value] = rt.get(r.rule_type.value, 0) + 1
            for s, c in sorted(sev.items()):
                lines.append(f"  - {s}: {c}")
            lines.append("Rule types:")
            for t, c in sorted(rt.items()):
                lines.append(f"  - {t}: {c}")
            lines.append(f"Ref tables:  {self.rules.total_reference_tables}")
            lines.append(f"Sections:    {len(self.rules.sections)}")

        lines.append("-" * 60)
        lines.append("Timings:")
        for step, duration in self.timings.items():
            lines.append(f"  {step}: {duration:.1f}s")
        total = sum(self.timings.values())
        lines.append(f"  TOTAL: {total:.1f}s")

        if self.errors:
            lines.append("-" * 60)
            lines.append("Errors:")
            for err in self.errors:
                lines.append(f"  - {err}")

        lines.append("=" * 60)
        return "\n".join(lines)


def run_pipeline(
    pdf_path: str | Path,
    azure_cfg: AzureConfig | None = None,
    pipeline_cfg: PipelineConfig | None = None,
    *,
    skip_extraction: bool = False,
    skip_rules: bool = False,
    skip_indexing: bool = False,
) -> PipelineResult:
    """
    Run the full PDF extraction and indexing pipeline.

    Args:
        pdf_path: Path to the PDF file to process.
        azure_cfg: Azure configuration. If None, loaded from environment.
        pipeline_cfg: Pipeline configuration. If None, uses defaults.
        skip_extraction: Skip Document Intelligence extraction (load from cache).
        skip_rules: Skip LLM rule extraction.
        skip_indexing: Skip AI Search and Cosmos DB indexing.

    Returns:
        PipelineResult with all outputs and timings.
    """
    if azure_cfg is None or pipeline_cfg is None:
        loaded_azure, loaded_pipeline = load_config()
        azure_cfg = azure_cfg or loaded_azure
        pipeline_cfg = pipeline_cfg or loaded_pipeline

    result = PipelineResult()
    output_dir = Path(pipeline_cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # STEP 1: Extract PDF layout
    # =========================================================================
    t0 = time.time()
    try:
        if skip_extraction:
            cache_path = output_dir / "01_raw_layout.json"
            if cache_path.exists():
                logger.info("Loading cached extraction from %s", cache_path)
                from .models import RawExtractionResult
                raw = RawExtractionResult.model_validate_json(cache_path.read_text(encoding="utf-8"))
            else:
                raise FileNotFoundError(f"Cached extraction not found: {cache_path}")
        else:
            raw = extract_pdf(pdf_path, azure_cfg, pipeline_cfg)
    except Exception as e:
        result.errors.append(f"Step 1 (extraction): {e}")
        logger.error("Failed at extraction: %s", e)
        return result
    result.timings["1_extraction"] = time.time() - t0

    # =========================================================================
    # STEP 2: Reconstruct hierarchy
    # =========================================================================
    t0 = time.time()
    try:
        structured_doc = reconstruct_hierarchy(raw, pipeline_cfg)
        result.structured_document = structured_doc
    except Exception as e:
        result.errors.append(f"Step 2 (reconstruction): {e}")
        logger.error("Failed at reconstruction: %s", e)
        return result
    result.timings["2_reconstruction"] = time.time() - t0

    # =========================================================================
    # STEP 3: Clean and normalize
    # =========================================================================
    t0 = time.time()
    try:
        structured_doc = clean_document(structured_doc, pipeline_cfg)
        result.structured_document = structured_doc
    except Exception as e:
        result.errors.append(f"Step 3 (cleaning): {e}")
        logger.error("Failed at cleaning: %s", e)
        return result
    result.timings["3_cleaning"] = time.time() - t0

    # =========================================================================
    # STEP 4: Semantic chunking
    # =========================================================================
    t0 = time.time()
    try:
        chunks = chunk_document(structured_doc, pipeline_cfg)
        result.chunks = chunks
    except Exception as e:
        result.errors.append(f"Step 4 (chunking): {e}")
        logger.error("Failed at chunking: %s", e)
        return result
    result.timings["4_chunking"] = time.time() - t0

    # =========================================================================
    # STEP 5: Extract rules (LLM)
    # =========================================================================
    if not skip_rules:
        t0 = time.time()
        try:
            rules = extract_rules(chunks, azure_cfg, pipeline_cfg)
            result.rules = rules
        except Exception as e:
            result.errors.append(f"Step 5 (rule extraction): {e}")
            logger.error("Failed at rule extraction: %s", e)
            # Continue — rules are not blocking for indexing
            rules = PolicyRuleSet(document_name="", total_rules=0)
            result.rules = rules
        result.timings["5_rule_extraction"] = time.time() - t0
    else:
        rules = PolicyRuleSet(document_name="", total_rules=0)
        result.rules = rules

    # =========================================================================
    # STEP 6: Index to AI Search + Cosmos DB
    # =========================================================================
    if not skip_indexing:
        t0 = time.time()
        try:
            index_chunks(chunks, rules, azure_cfg, pipeline_cfg)
        except Exception as e:
            result.errors.append(f"Step 6a (AI Search indexing): {e}")
            logger.error("Failed at AI Search indexing: %s", e)

        try:
            store_rules_in_cosmos(rules, azure_cfg)
        except Exception as e:
            result.errors.append(f"Step 6b (Cosmos DB storage): {e}")
            logger.error("Failed at Cosmos DB storage: %s", e)

        result.timings["6_indexing"] = time.time() - t0

    # =========================================================================
    # Save final summary
    # =========================================================================
    summary_path = output_dir / "pipeline_summary.txt"
    summary_path.write_text(result.summary(), encoding="utf-8")
    logger.info("Pipeline summary saved to: %s", summary_path)

    return result
