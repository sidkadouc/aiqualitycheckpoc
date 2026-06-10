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

    Verbose diagnostics are emitted at every step so that connectivity,
    auth, DNS, RBAC and schema issues can be diagnosed from the logs alone.
    """
    import socket
    import time
    from urllib.parse import urlparse

    from azure.cosmos import CosmosClient
    from azure.cosmos.exceptions import (
        CosmosHttpResponseError,
        CosmosResourceNotFoundError,
    )

    # ── 1. Validate endpoint + DNS pre-flight ─────────────────────────
    if not cosmos_endpoint:
        raise ValueError("cosmos_endpoint is empty — set AZURE_COSMOS_ENDPOINT")

    parsed = urlparse(cosmos_endpoint)
    host = parsed.hostname or ""
    logger.info(
        "Cosmos: endpoint=%s host=%s database=%s container=%s",
        cosmos_endpoint, host, database_name, container_name,
    )

    try:
        resolved_ip = socket.gethostbyname(host)
        is_private = resolved_ip.startswith(("10.", "172.", "192.168."))
        logger.info(
            "Cosmos: DNS '%s' -> %s (%s)",
            host, resolved_ip,
            "PRIVATE — private endpoint OK" if is_private else "PUBLIC — no private endpoint in use",
        )
    except socket.gaierror as e:
        logger.error("Cosmos: DNS resolution FAILED for %s: %s", host, e)
        raise

    # ── 2. Build credential with explicit logging ─────────────────────
    if credential is None:
        from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

        if managed_identity_client_id:
            logger.info(
                "Cosmos: auth=ManagedIdentityCredential (user-assigned, client_id=%s)",
                managed_identity_client_id,
            )
            credential = ManagedIdentityCredential(client_id=managed_identity_client_id)
        else:
            logger.info("Cosmos: auth=DefaultAzureCredential (chain: env, MI, CLI, …)")
            credential = DefaultAzureCredential()
    else:
        logger.info("Cosmos: auth=%s (caller-supplied)", type(credential).__name__)

    # ── 3. Probe token acquisition (optional but very useful) ─────────
    if hasattr(credential, "get_token"):
        try:
            t0 = time.perf_counter()
            token = credential.get_token("https://cosmos.azure.com/.default")
            logger.info(
                "Cosmos: AAD token acquired in %.0f ms (expires_on=%s)",
                (time.perf_counter() - t0) * 1000, token.expires_on,
            )
        except Exception as e:  # noqa: BLE001 — diagnostic only
            logger.error(
                "Cosmos: AAD token acquisition FAILED (%s: %s) — "
                "check managed identity assignment and AAD reachability",
                type(e).__name__, e,
            )
            # don't re-raise — let the actual Cosmos call surface the error

    # ── 4. Build client + verify database/container exist ─────────────
    try:
        client = CosmosClient(cosmos_endpoint, credential=credential)
    except Exception as e:  # noqa: BLE001
        logger.exception("Cosmos: CosmosClient construction FAILED")
        raise

    try:
        database = client.get_database_client(database_name)
        db_info = database.read()
        logger.info("Cosmos: database '%s' accessible (id=%s)", database_name, db_info.get("id"))
    except CosmosResourceNotFoundError:
        logger.error(
            "Cosmos: database '%s' NOT FOUND — check AZURE_COSMOS_DATABASE", database_name,
        )
        raise
    except CosmosHttpResponseError as e:
        logger.error(
            "Cosmos: database read FAILED (status=%s, substatus=%s) — likely RBAC. "
            "SAMI needs 'Cosmos DB Built-in Data Reader' or Data Contributor role.",
            e.status_code, getattr(e, "sub_status", None),
        )
        raise

    try:
        container = database.get_container_client(container_name)
        container_info = container.read()
        logger.info(
            "Cosmos: container '%s' accessible (partitionKey=%s)",
            container_name, container_info.get("partitionKey", {}).get("paths"),
        )
    except CosmosResourceNotFoundError:
        logger.error(
            "Cosmos: container '%s' NOT FOUND in database '%s' — "
            "has the pdf-pipeline job ever populated it?",
            container_name, database_name,
        )
        raise

    # ── 5. Run the actual query with timing + raw counts ──────────────
    query = "SELECT * FROM c WHERE c.type != 'ruleset_summary' AND IS_DEFINED(c.rule_id)"
    logger.info("Cosmos: executing query: %s", query)

    try:
        t0 = time.perf_counter()
        raw_rules = list(
            container.query_items(query=query, enable_cross_partition_query=True)
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("Cosmos: query returned %d raw rows in %.0f ms", len(raw_rules), elapsed_ms)
    except CosmosHttpResponseError as e:
        logger.error(
            "Cosmos: query FAILED (status=%s, substatus=%s, message=%s)",
            e.status_code, getattr(e, "sub_status", None), e.message,
        )
        raise

    # ── 6. Diagnose empty / malformed result sets ─────────────────────
    if not raw_rules:
        logger.warning(
            "Cosmos: query returned 0 rows — checking total document count …",
        )
        try:
            total = list(container.query_items(
                query="SELECT VALUE COUNT(1) FROM c",
                enable_cross_partition_query=True,
            ))[0]
            logger.warning(
                "Cosmos: container has %d total documents (none matched rule filter). "
                "Possible causes: (a) container is empty, (b) docs missing 'rule_id' field, "
                "(c) all docs have type='ruleset_summary'.", total,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Cosmos: count probe failed")

    # ── 7. Map to RuleInfo with per-row validation ────────────────────
    rules: list[RuleInfo] = []
    skipped = 0
    for r in raw_rules:
        if not r.get("rule_id"):
            skipped += 1
            continue
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

    if skipped:
        logger.warning("Cosmos: %d rows skipped (empty rule_id)", skipped)

    if rules:
        logger.info(
            "Cosmos: loaded %d rules (first id=%s, last id=%s)",
            len(rules), rules[0].rule_id, rules[-1].rule_id,
        )
    else:
        logger.error("Cosmos: 0 valid rules loaded — API will reject all checks!")

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
