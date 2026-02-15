#!/usr/bin/env python
"""
Smoke test for the Quality Checker Agent.

Parses the sample OpenXML document, builds the workflow, and (optionally)
runs the full quality check against the extracted OECD rules.

Usage
-----
Quick parse-only test (no LLM calls)::

    python test_quality_agent.py

Full end-to-end test (requires Azure OpenAI credentials in .env)::

    python test_quality_agent.py --run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent  # src/
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# ── constants ──────────────────────────────────────────────────────────
SAMPLE_XML = _SRC / "test_data" / "sample_document.xml"
RULES_JSON = _SRC / "pipeline_output" / "05_extracted_rules.json"


def test_parse() -> None:
    """Test: OpenXML → structured paragraphs with formatting."""
    from quality_agent.openxml_parser import parse_openxml

    xml = SAMPLE_XML.read_text(encoding="utf-8")
    result = parse_openxml(xml)

    print(f"✓ Parsed {len(result.paragraphs)} paragraphs from sample document")
    print(f"  Cleaned XML size: {len(result.cleaned_xml):,} chars\n")

    for p in result.paragraphs:
        runs_desc = "  ".join(
            f"[{r.run_index}]{r.formatting.summary()}:{r.text!r}"
            for r in p.runs
        )
        print(f"  P{p.paragraph_index:>2}  style={p.style or '-':15s}  fmt={p.formatting_summary:20s}  {p.plain_text[:80]}")
        if any(r.formatting.summary() != "plain" for r in p.runs):
            print(f"       runs: {runs_desc}")

    assert len(result.paragraphs) >= 10, f"Expected ≥10 paragraphs, got {len(result.paragraphs)}"
    print("\n✓ Parse test passed\n")


def test_formatting_detection() -> None:
    """Test: bold / italic / underline / strikethrough / caps / superscript detected."""
    from quality_agent.openxml_parser import parse_openxml

    xml = SAMPLE_XML.read_text(encoding="utf-8")
    result = parse_openxml(xml)
    paragraphs = {p.paragraph_index: p for p in result.paragraphs}

    # P0 heading — bold
    p0 = paragraphs[0]
    assert p0.style == "Heading1", f"Expected Heading1, got {p0.style}"
    assert p0.runs[0].formatting.bold, "Heading should be bold"

    # P3 — italic run ("OECD Economic Surveys") + bold run ("significantly")
    p3 = paragraphs[3]
    italic_runs = [r for r in p3.runs if r.formatting.italic]
    bold_runs = [r for r in p3.runs if r.formatting.bold]
    assert italic_runs, "P3 should have italic runs"
    assert bold_runs, "P3 should have bold runs"
    assert "OECD Economic Surveys" in italic_runs[0].text

    # P6 — underline (ListBullet "Key recommendation:")
    p6 = paragraphs[6]
    underline_runs = [r for r in p6.runs if r.formatting.underline]
    assert underline_runs, "P6 should have underline runs"

    # P8 — superscript (footnote marker)
    p8 = paragraphs[8]
    sup_runs = [r for r in p8.runs if r.formatting.superscript]
    assert sup_runs, "P8 should have superscript runs"

    # P11 — ALL_CAPS
    p11 = paragraphs[11]
    caps_runs = [r for r in p11.runs if r.formatting.all_caps]
    assert caps_runs, "P11 should have ALL_CAPS runs"

    # P12 — strikethrough
    p12 = paragraphs[12]
    strike_runs = [r for r in p12.runs if r.formatting.strikethrough]
    assert strike_runs, "P12 should have strikethrough runs"

    print("✓ Formatting detection test passed\n")


def test_highlight() -> None:
    """Test: highlight_violated_runs injects <w:highlight> correctly."""
    from quality_agent.openxml_parser import highlight_violated_runs, parse_openxml

    xml = SAMPLE_XML.read_text(encoding="utf-8")
    result = parse_openxml(xml)

    # Pick a multi-run paragraph (P3) and highlight run 3 (bold "significantly")
    p3 = next(p for p in result.paragraphs if p.paragraph_index == 3)
    bold_idx = [r.run_index for r in p3.runs if r.formatting.bold]
    assert bold_idx, "Expected bold run in P3"

    highlighted = highlight_violated_runs(p3.original_xml, bold_idx, "yellow")
    assert "w:highlight" in highlighted, "Expected <w:highlight> in output"
    assert 'val="yellow"' in highlighted, "Expected yellow highlight color"
    print("✓ Highlight injection test passed\n")


def test_rules_loading() -> None:
    """Test: load rules from pipeline output JSON."""
    if not RULES_JSON.exists():
        print("⚠ Skipping rule loading test — rules JSON not found")
        return

    from quality_agent.workflow import load_rules_from_json

    rules = load_rules_from_json(RULES_JSON)
    assert len(rules) > 0, "Expected at least one rule"

    do_count = sum(1 for r in rules if r.rule_type == "do")
    dont_count = sum(1 for r in rules if r.rule_type == "dont")
    unspec = sum(1 for r in rules if r.rule_type == "unspecified")

    print(f"✓ Loaded {len(rules)} rules  (do={do_count}  dont={dont_count}  unspecified={unspec})")

    # estimate tokens
    total_chars = sum(len(r.rule_text) for r in rules)
    est_tokens = total_chars // 4
    print(f"  Estimated token footprint: ~{est_tokens:,} tokens\n")


def test_workflow_build() -> None:
    """Test: workflow is constructed without errors (no LLM calls)."""
    from quality_agent.models import RuleInfo
    from quality_agent.workflow import build_quality_workflow

    sample_rules = [
        RuleInfo(rule_id=f"R{i}", rule_text=f"Sample rule {i}", severity="recommended")
        for i in range(10)
    ]

    wf, agg = build_quality_workflow(
        sample_rules,
        openai_endpoint="https://fake.openai.azure.com",
        openai_key="fake-key",
        num_batches=3,
    )
    print("✓ Workflow built successfully (3 fan-out checkers)\n")


async def test_full_run() -> None:
    """End-to-end test — requires Azure OpenAI credentials in .env."""
    try:
        from dotenv import load_dotenv
        load_dotenv(_SRC / ".env")
    except ImportError:
        pass

    import os
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    if not endpoint:
        print("⚠ Skipping full run — AZURE_OPENAI_ENDPOINT not set")
        return

    from quality_agent.workflow import run_quality_check

    xml = SAMPLE_XML.read_text(encoding="utf-8")
    print("Running full quality check (this may take 30-60s) …")

    report = await run_quality_check(
        xml,
        rules_json_path=RULES_JSON,
        num_batches=3,
        max_concurrent_per_batch=2,
    )

    print(f"\n{'='*60}")
    print(f"Paragraphs : {report.total_paragraphs}")
    print(f"Compliant  : {report.compliant_paragraphs}")
    print(f"Violations : {report.total_violations}")
    print(f"Rules ckd  : {report.total_rules_checked}")
    print(f"{'='*60}\n")

    for pr in report.paragraph_results:
        status = "✓" if pr.is_compliant else "✗"
        print(f"  {status} P{pr.paragraph_index}: {pr.plain_text[:60]}…")
        for v in pr.violations:
            print(f"      ⚠ {v.rule_id} ({v.severity}) — {v.explanation[:80]}")

    # save report
    out_dir = _SRC / "test_data"
    report_path = out_dir / "test_report.json"
    report_path.write_text(
        json.dumps(report.model_dump(exclude={"highlighted_xml"}), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nReport saved to {report_path}")

    if report.highlighted_xml:
        xml_path = out_dir / "test_highlighted.xml"
        xml_path.write_text(report.highlighted_xml, encoding="utf-8")
        print(f"Highlighted XML saved to {xml_path}")

    print("\n✓ Full run test passed\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Quality Agent tests")
    parser.add_argument("--run", action="store_true", help="Run full end-to-end test (requires Azure OpenAI)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    print("=" * 60)
    print("  Quality Checker Agent — Test Suite")
    print("=" * 60 + "\n")

    test_parse()
    test_formatting_detection()
    test_highlight()
    test_rules_loading()
    test_workflow_build()

    if args.run:
        asyncio.run(test_full_run())

    print("All tests passed ✓")


if __name__ == "__main__":
    main()
