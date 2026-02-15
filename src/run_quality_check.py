#!/usr/bin/env python
"""
CLI entry point for the Quality Checker Agent.

Usage
-----
Check an OpenXML document part::

    python run_quality_check.py --xml word/document.xml --rules pipeline_output/05_extracted_rules.json

Or extract from a .docx file::

    python run_quality_check.py --docx data/MyDocument.docx --rules pipeline_output/05_extracted_rules.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# ensure src/ is on the path when run directly
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Quality Checker Agent — check OpenXML documents against OECD style rules"
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--xml", help="Path to raw OpenXML file (e.g. word/document.xml)")
    src.add_argument("--docx", help="Path to .docx file (document.xml will be extracted)")
    src.add_argument("--json", dest="json_input", help="Path to JSON request file (from Word add-in)")

    p.add_argument(
        "--rules",
        required=True,
        help="Path to extracted rules JSON (pipeline_output/05_extracted_rules.json)",
    )
    p.add_argument("--model", default="gpt-4.1", help="Azure OpenAI deployment name")
    p.add_argument("--batches", type=int, default=1, help="Number of rule-batch fan-out checkers (1=all rules per call)")
    p.add_argument("--concurrency", type=int, default=3, help="Max concurrent LLM calls")
    p.add_argument("--no-prefilter", action="store_true", help="Disable smart rule pre-filtering")
    p.add_argument("--addin", action="store_true", help="Output add-in response format (for Word Web Add-in)")
    p.add_argument("--output", "-o", help="Output JSON path (default: stdout)")
    p.add_argument("--output-xml", help="Save highlighted OpenXML to this file")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


async def _main() -> None:
    # load .env before anything else
    try:
        from dotenv import load_dotenv

        env_path = _SRC / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    # ── load input ──────────────────────────────────────────────────────
    xml_content = None
    json_content = None
    style_map = None

    if args.json_input:
        json_content = Path(args.json_input).read_text(encoding="utf-8")
    elif args.docx:
        from quality_agent.openxml_parser import extract_docx_xml, extract_docx_styles

        xml_content = extract_docx_xml(args.docx)
        style_map = extract_docx_styles(args.docx)
    else:
        xml_content = Path(args.xml).read_text(encoding="utf-8")

    # ── run quality check ──────────────────────────────────────────────
    from quality_agent.workflow import run_quality_check

    report = await run_quality_check(
        xml_content,
        json_content=json_content,
        style_map=style_map,
        rules_json_path=args.rules,
        model=args.model,
        num_batches=args.batches,
        max_concurrent_per_batch=args.concurrency,
        enable_prefilter=not args.no_prefilter,
    )

    # ── output ─────────────────────────────────────────────────────────
    if args.addin:
        from quality_agent.addin_response import build_addin_response, build_doc_paragraph_map

        doc_map = None
        if json_content is not None:
            doc_map = build_doc_paragraph_map(json_content)

        addin = build_addin_response(report, doc_paragraph_map=doc_map)
        output_json = json.dumps(
            addin.model_dump(), indent=2, ensure_ascii=False
        )
    else:
        report_dict = report.model_dump(exclude={"highlighted_xml"})
        output_json = json.dumps(report_dict, indent=2, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"Report saved to {args.output}")
    else:
        print(output_json)

    if args.output_xml and report.highlighted_xml:
        Path(args.output_xml).write_text(report.highlighted_xml, encoding="utf-8")
        print(f"Highlighted XML saved to {args.output_xml}")

    # summary
    print(
        f"\n{'='*60}\n"
        f"Paragraphs: {report.total_paragraphs}  |  "
        f"Compliant: {report.compliant_paragraphs}  |  "
        f"Violations: {report.total_violations}  |  "
        f"Rules checked: {report.total_rules_checked}\n"
        f"{'='*60}"
    )


if __name__ == "__main__":
    asyncio.run(_main())
