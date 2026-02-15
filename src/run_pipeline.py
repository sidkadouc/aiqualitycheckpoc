"""
Entry point for the PDF Policy Extraction Pipeline.

Usage:
    python run_pipeline.py <path_to_pdf> [--output-dir DIR] [--skip-rules] [--skip-indexing]

Environment variables required:
    AZURE_OPENAI_ENDPOINT             - Azure AI Foundry endpoint
    AZURE_OPENAI_KEY                  - API key (or use USE_MANAGED_IDENTITY=true)
    AZURE_CONTENT_UNDERSTANDING_ENDPOINT - Document Intelligence endpoint
    AZURE_AISEARCH_ENDPOINT           - Azure AI Search endpoint
    AZURE_AISEARCH_KEY                - Search admin key
    AZURE_COSMOS_ENDPOINT             - Cosmos DB endpoint
    AZURE_COSMOS_KEY                  - Cosmos DB key

Optional:
    AZURE_SEARCH_INDEX_NAME           - Search index name (default: policy-chunks)
    AZURE_COSMOS_DATABASE             - Cosmos DB database (default: appdata)
    AZURE_COSMOS_RULES_CONTAINER      - Container for rules (default: policy-rules)
    USE_MANAGED_IDENTITY              - Set to 'true' for MI auth
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load .env from the same directory as this script
load_dotenv(Path(__file__).parent / ".env")

from pdf_pipeline.config import AzureConfig, PipelineConfig
from pdf_pipeline.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(
        description="Extract and index policy rules from OECD PDF documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "pdf_path",
        type=str,
        help="Path to the PDF file to process",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="pipeline_output",
        help="Directory for intermediate and final output files (default: pipeline_output)",
    )
    parser.add_argument(
        "--max-chunk-tokens",
        type=int,
        default=800,
        help="Maximum tokens per chunk (default: 800)",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Skip Document Intelligence extraction (use cached result)",
    )
    parser.add_argument(
        "--skip-rules",
        action="store_true",
        help="Skip LLM rule extraction step",
    )
    parser.add_argument(
        "--skip-indexing",
        action="store_true",
        help="Skip Azure AI Search and Cosmos DB indexing",
    )
    parser.add_argument(
        "--no-intermediate",
        action="store_true",
        help="Don't save intermediate JSON files",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Validate PDF exists
    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        logging.error("PDF file not found: %s", pdf_path)
        sys.exit(1)

    # Configure pipeline
    azure_cfg = AzureConfig()
    pipeline_cfg = PipelineConfig(
        max_chunk_tokens=args.max_chunk_tokens,
        output_dir=args.output_dir,
        save_intermediate_files=not args.no_intermediate,
    )

    # Run pipeline
    logging.info("Starting pipeline for: %s", pdf_path.name)
    result = run_pipeline(
        pdf_path=pdf_path,
        azure_cfg=azure_cfg,
        pipeline_cfg=pipeline_cfg,
        skip_extraction=args.skip_extraction,
        skip_rules=args.skip_rules,
        skip_indexing=args.skip_indexing,
    )

    # Output summary
    print("\n" + result.summary())

    if result.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
