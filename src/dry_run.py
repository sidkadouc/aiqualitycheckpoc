"""
Dry-run script for the PDF Policy Extraction Pipeline.

Runs steps 1-5 locally (no AI Search, no Cosmos DB).
Outputs intermediate JSON files for inspection.

Usage:
    python dry_run.py <path_to_pdf>
    python dry_run.py <path_to_pdf> --skip-rules   # only extraction + chunking
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Load .env before anything else
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdf_pipeline.config import AzureConfig, PipelineConfig
from pdf_pipeline.extract import extract_pdf
from pdf_pipeline.reconstruct import reconstruct_hierarchy
from pdf_pipeline.clean import clean_document
from pdf_pipeline.chunk import chunk_document
from pdf_pipeline.extract_rules import extract_rules


def main():
    parser = argparse.ArgumentParser(description="Dry-run: extract PDF → JSON (no indexing)")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("--output-dir", default="pipeline_output", help="Output directory")
    parser.add_argument("--max-chunk-tokens", type=int, default=800)
    parser.add_argument("--skip-rules", action="store_true", help="Skip LLM rule extraction")
    parser.add_argument("--skip-extraction", action="store_true", help="Use cached extraction")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("dry_run")

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        logger.error("PDF not found: %s", pdf_path)
        sys.exit(1)

    azure_cfg = AzureConfig()
    pipeline_cfg = PipelineConfig(
        max_chunk_tokens=args.max_chunk_tokens,
        output_dir=args.output_dir,
        save_intermediate_files=True,
    )

    output_dir = Path(pipeline_cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timings = {}

    # ==============================
    # STEP 1: Extract PDF
    # ==============================
    logger.info("=" * 60)
    logger.info("STEP 1: Extracting PDF with Document Intelligence...")
    logger.info("=" * 60)
    t0 = time.time()

    if args.skip_extraction:
        cache = output_dir / "01_raw_layout.json"
        if cache.exists():
            logger.info("Loading cached extraction from %s", cache)
            from pdf_pipeline.models import RawExtractionResult
            raw = RawExtractionResult.model_validate_json(cache.read_text(encoding="utf-8"))
        else:
            logger.error("No cache found at %s, run without --skip-extraction first", cache)
            sys.exit(1)
    else:
        raw = extract_pdf(pdf_path, azure_cfg, pipeline_cfg)

    timings["extraction"] = time.time() - t0
    logger.info(
        "  -> %d pages, %d blocks, %d tables (%.1fs)",
        raw.total_pages, len(raw.blocks), len(raw.tables), timings["extraction"],
    )

    # ==============================
    # STEP 2: Reconstruct hierarchy
    # ==============================
    logger.info("=" * 60)
    logger.info("STEP 2: Reconstructing document hierarchy...")
    logger.info("=" * 60)
    t0 = time.time()

    doc = reconstruct_hierarchy(raw, pipeline_cfg)

    timings["reconstruction"] = time.time() - t0
    logger.info(
        "  -> %d sections, %d paragraphs (%.1fs)",
        doc.total_sections, doc.total_paragraphs, timings["reconstruction"],
    )

    # ==============================
    # STEP 3: Clean
    # ==============================
    logger.info("=" * 60)
    logger.info("STEP 3: Cleaning and normalizing text...")
    logger.info("=" * 60)
    t0 = time.time()

    doc = clean_document(doc, pipeline_cfg)

    timings["cleaning"] = time.time() - t0
    logger.info(
        "  -> %d paragraphs after cleaning (%.1fs)",
        doc.total_paragraphs, timings["cleaning"],
    )

    # ==============================
    # STEP 4: Semantic chunking
    # ==============================
    logger.info("=" * 60)
    logger.info("STEP 4: Creating semantic chunks...")
    logger.info("=" * 60)
    t0 = time.time()

    chunks = chunk_document(doc, pipeline_cfg)

    timings["chunking"] = time.time() - t0
    avg_tokens = sum(c.token_count for c in chunks) // max(len(chunks), 1)
    logger.info(
        "  -> %d chunks, avg %d tokens (%.1fs)",
        len(chunks), avg_tokens, timings["chunking"],
    )

    # ==============================
    # STEP 5: Extract rules (LLM)
    # ==============================
    if not args.skip_rules:
        logger.info("=" * 60)
        logger.info("STEP 5: Extracting rules with GPT-4.1...")
        logger.info("=" * 60)
        t0 = time.time()

        rules = extract_rules(chunks, azure_cfg, pipeline_cfg)

        timings["rule_extraction"] = time.time() - t0
        logger.info(
            "  -> %d rules extracted (%.1fs)",
            rules.total_rules, timings["rule_extraction"],
        )

        # Print severity breakdown
        from collections import Counter
        sev = Counter(r.severity.value for r in rules.rules)
        for severity, count in sorted(sev.items()):
            logger.info("    %s: %d", severity, count)
    else:
        logger.info("Skipping rule extraction (--skip-rules)")

    # ==============================
    # SUMMARY
    # ==============================
    logger.info("")
    logger.info("=" * 60)
    logger.info("DRY RUN COMPLETE")
    logger.info("=" * 60)
    logger.info("Output directory: %s", output_dir.resolve())
    logger.info("")
    logger.info("Generated files:")
    for f in sorted(output_dir.glob("*.json")):
        size_kb = f.stat().st_size / 1024
        logger.info("  %s (%.1f KB)", f.name, size_kb)
    logger.info("")
    logger.info("Timings:")
    for step, duration in timings.items():
        logger.info("  %-20s %.1fs", step, duration)
    logger.info("  %-20s %.1fs", "TOTAL", sum(timings.values()))


if __name__ == "__main__":
    main()
