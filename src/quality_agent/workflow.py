"""
Workflow builder and public API for the Quality Checker Agent.

Builds the Agent Framework workflow graph::

    OpenXMLParserExecutor
      ── fan-out ──▶  [RuleBatchChecker_0 … RuleBatchChecker_N]
      ── fan-in  ──▶  ViolationAggregatorExecutor
      ── yield   ──▶  QualityCheckReport

Usage
-----
::

    from quality_agent import run_quality_check

    report = await run_quality_check(
        xml_content=open("word/document.xml").read(),
        rules_json_path="pipeline_output/05_extracted_rules.json",
    )
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Sequence

from agent_framework import WorkflowBuilder

from .executors import (
    OpenXMLParserExecutor,
    RuleBatchCheckerExecutor,
    TokenRateLimiter,
    ViolationAggregatorExecutor,
    get_or_create_rate_limiter,
)
from .model_router import ModelRouter
from .models import QualityCheckReport, RuleInfo
from .openxml_parser import parse_json_request, parse_openxml, StyleMap

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Rule loading
# ──────────────────────────────────────────────────────────────────────

def load_rules_from_json(path: str | Path) -> list[RuleInfo]:
    """Load ``RuleInfo`` objects from the pipeline's extracted-rules JSON file.

    Expected structure::

        {
          "rules": [
            { "rule_id": "…", "rule_text": "…", … },
            …
          ]
        }
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_rules = data.get("rules", [])
    rules: list[RuleInfo] = []
    for r in raw_rules:
        rules.append(
            RuleInfo(
                rule_id=r.get("rule_id", ""),
                rule_text=r.get("rule_text", ""),
                rule_summary=r.get("rule_summary", ""),
                rule_type=r.get("rule_type", "unspecified"),
                severity=r.get("severity", "recommended"),
                section_title=r.get("section_title", ""),
                keywords=r.get("keywords", []),
                page=r.get("page"),
            )
        )
    logger.info("Loaded %d rules from %s", len(rules), path)
    return rules


def load_rules_from_cosmos(
    cosmos_endpoint: str,
    database_name: str = "appdata",
    container_name: str = "policy-rules",
    credential: Any | None = None,
    managed_identity_client_id: str | None = None,
) -> list[RuleInfo]:
    """Load ``RuleInfo`` objects from Azure Cosmos DB.

    Uses managed identity (``DefaultAzureCredential``) when no explicit
    credential is supplied — ideal for production on Container Apps where
    the user-assigned managed identity has the *Cosmos DB Data Contributor*
    role.

    Parameters
    ----------
    cosmos_endpoint:
        Cosmos DB account URI  (``https://<account>.documents.azure.com:443/``).
    database_name:
        Database name (default ``appdata``).
    container_name:
        Container name (default ``policy-rules``).
    credential:
        Explicit credential or key string.  If ``None``, uses
        ``DefaultAzureCredential`` (managed identity).
    managed_identity_client_id:
        When using a user-assigned managed identity, the client ID to
        scope ``DefaultAzureCredential``.
    """
    from azure.cosmos import CosmosClient

    if credential is None:
        from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

        if managed_identity_client_id:
            credential = ManagedIdentityCredential(
                client_id=managed_identity_client_id,
            )
        else:
            credential = DefaultAzureCredential()

    client = CosmosClient(cosmos_endpoint, credential=credential)
    database = client.get_database_client(database_name)
    container = database.get_container_client(container_name)

    raw_rules = list(
        container.query_items(
            query="SELECT * FROM c WHERE c.type != 'ruleset_summary' AND IS_DEFINED(c.rule_id)",
            enable_cross_partition_query=True,
        )
    )

    rules: list[RuleInfo] = []
    for r in raw_rules:
        rules.append(
            RuleInfo(
                rule_id=r.get("rule_id", ""),
                rule_text=r.get("rule_text", ""),
                rule_summary=r.get("rule_summary", ""),
                rule_type=r.get("rule_type", "unspecified"),
                severity=r.get("severity", "recommended"),
                section_title=r.get("section_title", ""),
                keywords=r.get("keywords", []),
                page=r.get("page"),
            )
        )
    logger.info("Loaded %d rules from Cosmos DB (%s/%s)", len(rules), database_name, container_name)
    return rules


def _split_rules(
    rules: Sequence[RuleInfo],
    num_batches: int = 1,
) -> list[list[RuleInfo]]:
    """Split rules into approximately equal batches.

    When ``num_batches == 1`` (the default) all rules go into a single
    batch — one LLM call per paragraph.  When ``num_batches >= 2`` a
    fan-out topology is used.
    """
    num_batches = max(1, min(num_batches, len(rules)))
    if num_batches == 1:
        return [list(rules)]
    batch_size = math.ceil(len(rules) / num_batches)
    batches: list[list[RuleInfo]] = []
    for i in range(0, len(rules), batch_size):
        batches.append(list(rules[i : i + batch_size]))
    # pad to at least 2 if rules are very few (fan-out requires ≥ 2)
    while len(batches) < 2:
        batches.append([])
    return batches


# ──────────────────────────────────────────────────────────────────────
# Workflow construction
# ──────────────────────────────────────────────────────────────────────

def build_quality_workflow(
    rules: Sequence[RuleInfo],
    *,
    router: ModelRouter,
    num_batches: int = 1,
    enable_prefilter: bool = True,
    # legacy — kept for backward compat but ignored when router is set
    openai_endpoint: str = "",
    openai_key: str | None = None,
    model: str = "gpt-4.1",
    max_concurrent_per_batch: int = 3,
    rate_limiter: TokenRateLimiter | None = None,
) -> tuple[Any, ViolationAggregatorExecutor]:
    """Build and return the Agent Framework ``Workflow`` + aggregator ref.

    Parameters
    ----------
    rules:
        Full list of rules to check.
    openai_endpoint:
        Azure OpenAI endpoint URL.
    openai_key:
        API key (if ``None``, DefaultAzureCredential is used).
    model:
        Deployment name.
    num_batches:
        How many rule-batch checkers.  ``1`` (default) sends all rules
        in a single call per paragraph.  ``≥ 2`` uses fan-out/fan-in.
    max_concurrent_per_batch:
        Semaphore limit for parallel LLM calls.

    Returns
    -------
    (Workflow, ViolationAggregatorExecutor)
        The workflow object and a reference to the aggregator (needed to
        pre-populate the paragraph→XML map before running).
    """
    batches = _split_rules(rules, num_batches)
    logger.info(
        "Building workflow with %d rule-batch checkers (%s rules per batch)",
        len(batches),
        [len(b) for b in batches],
    )

    # ── create executor instances ──────────────────────────────────────
    parser = OpenXMLParserExecutor()
    aggregator = ViolationAggregatorExecutor()

    checkers: list[RuleBatchCheckerExecutor] = []
    for i, batch in enumerate(batches):
        checkers.append(
            RuleBatchCheckerExecutor(
                rules=batch,
                router=router,
                enable_prefilter=enable_prefilter,
                id=f"rule_checker_{i}",
            )
        )

    # ── wire the graph ─────────────────────────────────────────────────
    builder = WorkflowBuilder(start_executor=parser)
    if len(checkers) == 1:
        # Linear: parser → single checker → aggregator (no fan-out)
        builder = builder.add_edge(parser, checkers[0])
        builder = builder.add_edge(checkers[0], aggregator)
    else:
        # Fan-out / fan-in for multiple batches
        builder = builder.add_fan_out_edges(parser, checkers)
        builder = builder.add_fan_in_edges(checkers, aggregator)
    workflow = builder.build()

    return workflow, aggregator


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────

async def run_quality_check(
    xml_content: str | None = None,
    *,
    json_content: str | dict[str, Any] | None = None,
    style_map: StyleMap | None = None,
    rules: Sequence[RuleInfo] | None = None,
    rules_json_path: str | Path | None = None,
    router: ModelRouter | None = None,
    openai_endpoint: str | None = None,
    openai_key: str | None = None,
    model: str = "gpt-4.1",
    primary_model: str | None = None,
    primary_tpm: int | None = None,
    fallback_model: str | None = None,
    fallback_tpm: int | None = None,
    num_batches: int = 1,
    max_concurrent_per_batch: int = 3,
    enable_prefilter: bool = True,
    rate_limiter: TokenRateLimiter | None = None,
) -> QualityCheckReport:
    """Run the full quality-check workflow.

    Parameters
    ----------
    router:
        Pre-built ``ModelRouter``.  When supplied, endpoint/key/model
        params are ignored and the router is reused across calls.
    primary_model / primary_tpm / fallback_model / fallback_tpm:
        When *router* is ``None`` these are used to build one on-the-fly.
        Defaults: primary=gpt-5.2 @ 500 K TPM, fallback=gpt-4.1 @ 256 K TPM.

    Returns
    -------
    QualityCheckReport
    """
    import os

    # resolve rules
    if rules is None:
        if rules_json_path is None:
            raise ValueError("Provide `rules` or `rules_json_path`")
        rules = load_rules_from_json(rules_json_path)
    if not rules:
        raise ValueError("No rules to check against")

    # resolve Azure OpenAI settings
    endpoint = openai_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    key = openai_key or os.environ.get("AZURE_OPENAI_KEY") or None

    if not endpoint:
        raise ValueError(
            "Azure OpenAI endpoint required — set AZURE_OPENAI_ENDPOINT or pass openai_endpoint"
        )

    # ── build or reuse ModelRouter ─────────────────────────────────────
    if router is None:
        _primary = primary_model or os.environ.get("AZURE_OPENAI_PRIMARY_DEPLOYMENT", "gpt-5.2")
        _primary_tpm = primary_tpm or int(os.environ.get("AZURE_OPENAI_PRIMARY_TPM", "500000"))
        _fallback = fallback_model or os.environ.get("AZURE_OPENAI_FALLBACK_DEPLOYMENT", model)
        _fallback_tpm = fallback_tpm or int(os.environ.get("AZURE_OPENAI_FALLBACK_TPM", "256000"))

        router = ModelRouter.from_env(
            endpoint=endpoint,
            api_key=key,
            primary_model=_primary,
            primary_tpm=_primary_tpm,
            fallback_model=_fallback,
            fallback_tpm=_fallback_tpm,
        )

    # ── parse input (JSON or raw XML) ──────────────────────────────────
    if json_content is not None:
        parsed = parse_json_request(json_content)
    elif xml_content is not None:
        parsed = parse_openxml(xml_content, style_map=style_map)
    else:
        raise ValueError("Provide `xml_content` or `json_content`")

    # build workflow
    workflow, aggregator = build_quality_workflow(
        rules,
        router=router,
        num_batches=num_batches,
        enable_prefilter=enable_prefilter,
    )

    # give the aggregator the paragraph→XML mapping so it can highlight.
    # When JSON input was used, the pre-parsed paragraphs may have
    # docParagraphIndex-based indices (e.g. 7) while the
    # OpenXMLParserExecutor will re-parse cleaned_xml and assign
    # sequential indices (0, 1, …).  Re-parse now to get matching indices.
    if json_content is not None:
        reparsed = parse_openxml(parsed.cleaned_xml, style_map=style_map)
        aggregator.set_paragraph_xml_map(reparsed.paragraphs)
    else:
        aggregator.set_paragraph_xml_map(parsed.paragraphs)

    # ── run workflow ───────────────────────────────────────────────────
    # The start executor (OpenXMLParserExecutor) expects a raw XML
    # string.  When input was JSON, we've already extracted and stripped
    # the XML — pass the cleaned_xml from the parsed result.
    workflow_input = parsed.cleaned_xml if json_content is not None else (xml_content or "")

    logger.info("Starting quality-check workflow …")
    report: QualityCheckReport | None = None

    async for event in workflow.run(workflow_input, stream=True):
        if event.type == "output":
            report = event.data

    if report is None:
        logger.error("Workflow completed without producing a report")
        return QualityCheckReport()

    return report
