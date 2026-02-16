"""
Agent Framework executors for the quality-check workflow.

Workflow graph
--------------
::

    OpenXMLParserExecutor
      ── fan-out ──▶  RuleBatchCheckerExecutor  (× N batches)
      ── fan-in  ──▶  ViolationAggregatorExecutor
      ── yield   ──▶  QualityCheckReport

All executors subclass ``agent_framework.Executor`` and communicate via
typed messages through ``WorkflowContext``.

NOTE: Do NOT use ``from __future__ import annotations`` here — the Agent
Framework inspects handler type annotations at class-definition time and
requires them to be real type objects, not PEP 563 strings.
"""

import asyncio
import json
import logging
import time
from typing import Any, Sequence

from agent_framework import Executor, WorkflowContext, handler
from openai import AsyncAzureOpenAI, RateLimitError
from typing_extensions import Never

from .conflict_resolver import resolve_contradictions
from .model_router import ModelRouter
from .models import (
    BatchCheckResult,
    DocumentCheckRequest,
    ParagraphResult,
    QualityCheckReport,
    RuleInfo,
    RuleViolation,
)
from .openxml_parser import highlight_violated_runs, parse_openxml
from .prompts import (
    RULE_CHECKER_SYSTEM,
    build_checker_user_prompt,
    estimate_prompt_tokens,
    prefilter_rules,
    MAX_RULES_TOKENS,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Token-Per-Minute Rate Limiter
# ======================================================================

class TokenRateLimiter:
    """Sliding-window token-per-minute (TPM) rate limiter.

    Tracks estimated tokens consumed over a rolling 60-second window.
    When a request would exceed ``max_tpm``, the caller sleeps until
    enough capacity is freed.
    """

    def __init__(self, max_tpm: int = 256_000, safety_margin: float = 0.85):
        self._max_tpm = int(max_tpm * safety_margin)  # leave headroom
        self._lock = asyncio.Lock()
        self._entries: list[tuple[float, int]] = []  # (timestamp, tokens)

    def _purge_old(self) -> None:
        cutoff = time.monotonic() - 60.0
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.pop(0)

    @property
    def tokens_used(self) -> int:
        self._purge_old()
        return sum(t for _, t in self._entries)

    @property
    def tokens_remaining(self) -> int:
        return max(0, self._max_tpm - self.tokens_used)

    async def acquire(self, estimated_tokens: int) -> None:
        """Wait until ``estimated_tokens`` can fit in the rolling window."""
        while True:
            async with self._lock:
                self._purge_old()
                used = sum(t for _, t in self._entries)
                if used + estimated_tokens <= self._max_tpm:
                    self._entries.append((time.monotonic(), estimated_tokens))
                    return
                # how long until enough capacity frees up?
                need = used + estimated_tokens - self._max_tpm
                freed = 0
                wait_until = time.monotonic()
                for ts, tok in self._entries:
                    freed += tok
                    if freed >= need:
                        wait_until = ts + 60.0
                        break
            wait_secs = max(0.5, wait_until - time.monotonic())
            logger.info(
                "Rate limiter: throttling %.1fs (used %d/%d TPM, need %d)",
                wait_secs, used, self._max_tpm, estimated_tokens,
            )
            await asyncio.sleep(wait_secs)

    def record_actual(self, estimated: int, actual: int) -> None:
        """Correct the last entry if actual usage differs from estimate."""
        diff = actual - estimated
        if diff != 0 and self._entries:
            # adjust the most recent entry
            ts, tok = self._entries[-1]
            self._entries[-1] = (ts, max(0, tok + diff))


# Global default – overridden by the API or caller
_default_rate_limiter: TokenRateLimiter | None = None


def get_or_create_rate_limiter(max_tpm: int = 256_000) -> TokenRateLimiter:
    global _default_rate_limiter
    if _default_rate_limiter is None:
        _default_rate_limiter = TokenRateLimiter(max_tpm=max_tpm)
    return _default_rate_limiter


# ======================================================================
# Retry helper for 429 / rate-limit errors
# ======================================================================

async def _retry_with_backoff(
    coro_factory,
    *,
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
):
    """Call ``coro_factory()`` with exponential backoff on 429 errors."""
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except RateLimitError as exc:
            if attempt == max_retries:
                raise
            # Extract retry-after from the error if available
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is None:
                # parse from headers or message
                msg = str(exc)
                import re
                m = re.search(r"retry after (\d+)", msg, re.IGNORECASE)
                retry_after = float(m.group(1)) if m else None
            delay = retry_after if retry_after else min(base_delay * (2 ** attempt), max_delay)
            logger.warning(
                "Rate limited (attempt %d/%d) — retrying in %.1fs",
                attempt + 1, max_retries, delay,
            )
            await asyncio.sleep(delay)
        except Exception as exc:
            # For other transient errors, only retry once
            if attempt == 0 and "429" in str(exc):
                await asyncio.sleep(base_delay)
                continue
            raise


# ======================================================================
# 1. OpenXML Parser Executor  (start node)
# ======================================================================

class OpenXMLParserExecutor(Executor):
    """Parse raw OpenXML into structured paragraphs and forward downstream.

    Input : ``str``   — raw OpenXML content (``<w:document>…</w:document>``).
           OR ``DocumentCheckRequest`` — pre-parsed paragraphs (passthrough).
    Output: ``DocumentCheckRequest``  — parsed paragraphs with formatting.
    """

    def __init__(self, *, id: str = "openxml_parser"):
        super().__init__(id=id)

    @handler
    async def parse(
        self,
        xml_content: str,
        ctx: WorkflowContext[DocumentCheckRequest],
    ) -> None:
        logger.info("Parsing OpenXML document (%d chars)", len(xml_content))
        request = parse_openxml(xml_content)
        logger.info(
            "Extracted %d non-empty paragraphs", len(request.paragraphs)
        )
        await ctx.send_message(request)

    @handler
    async def passthrough(
        self,
        request: DocumentCheckRequest,
        ctx: WorkflowContext[DocumentCheckRequest],
    ) -> None:
        """Forward pre-parsed input without re-parsing."""
        logger.info(
            "Passthrough: forwarding %d pre-parsed paragraphs",
            len(request.paragraphs),
        )
        await ctx.send_message(request)


# ======================================================================
# 2. Rule-Batch Checker Executor  (fan-out targets)
# ======================================================================

class RuleBatchCheckerExecutor(Executor):
    """Check all paragraphs against an assigned batch of rules.

    Each instance is initialised with a distinct rule subset.  At runtime
    it receives the same ``DocumentCheckRequest`` from the fan-out edge,
    checks every paragraph against *its* rules via async LLM calls, and
    sends a ``BatchCheckResult`` downstream to the fan-in aggregator.

    Input : ``DocumentCheckRequest``
    Output: ``BatchCheckResult``
    """

    def __init__(
        self,
        *,
        rules: Sequence[RuleInfo],
        router: ModelRouter,
        enable_prefilter: bool = True,
        id: str = "rule_checker",
    ):
        super().__init__(id=id)
        self._rules = list(rules)
        self._router = router
        self._enable_prefilter = enable_prefilter

    # ── handler ────────────────────────────────────────────────────────

    @handler
    async def check(
        self,
        request: DocumentCheckRequest,
        ctx: WorkflowContext[BatchCheckResult],
    ) -> None:
        n_para = len(request.paragraphs)
        logger.info(
            "[%s] Checking %d paragraphs against %d rules (sequential)",
            self.id, n_para, len(self._rules),
        )

        # Process paragraphs sequentially to avoid overwhelming the
        # Databricks TPM rate limit.  Each call already uses the
        # ModelRouter's token budget + semaphore for retries.
        paragraph_results: list[ParagraphResult] = []
        t0 = time.time()
        for i, para in enumerate(request.paragraphs, 1):
            logger.info(
                "[%s] ── paragraph %d/%d (P%d, %d chars) ──",
                self.id, i, n_para, para.paragraph_index,
                len(para.plain_text),
            )
            try:
                result_p = await self._check_paragraph(para)
                paragraph_results.append(result_p)
            except Exception as exc:
                logger.warning(
                    "[%s] LLM call failed for P%d: %s",
                    self.id, para.paragraph_index, exc,
                )

        elapsed = time.time() - t0
        logger.info(
            "[%s] Finished %d paragraphs in %.1fs (%.1fs/para)",
            self.id, n_para, elapsed, elapsed / max(n_para, 1),
        )

        result = BatchCheckResult(
            batch_id=self.id,
            rules_checked=len(self._rules),
            paragraph_results=paragraph_results,
        )
        await ctx.send_message(result)

    # ── internal ───────────────────────────────────────────────────────

    async def _check_paragraph(self, paragraph: Any) -> ParagraphResult:
        t_start = time.time()

        # ── step 1: smart rule pre-filtering ──────────────────────────
        if self._enable_prefilter:
            active_rules = prefilter_rules(self._rules, paragraph)
            logger.info(
                "  P%d step 1/3 prefilter: %d → %d rules (%.0fms)",
                paragraph.paragraph_index, len(self._rules),
                len(active_rules), (time.time() - t_start) * 1000,
            )
        else:
            active_rules = self._rules

        if not active_rules:
            logger.info("  P%d — no rules matched, marking compliant", paragraph.paragraph_index)
            return ParagraphResult(
                paragraph_index=paragraph.paragraph_index,
                plain_text=paragraph.plain_text,
                is_compliant=True,
            )

        # ── step 2: build prompt ──────────────────────────────────────
        t_prompt = time.time()
        user_prompt = build_checker_user_prompt(paragraph, active_rules, compact=False)
        est = estimate_prompt_tokens(prebuilt_user_prompt=user_prompt)
        compact = est > MAX_RULES_TOKENS
        if compact:
            logger.info(
                "  P%d: ~%d tokens with %d rules — switching to compact mode",
                paragraph.paragraph_index, est, len(active_rules),
            )
            user_prompt = build_checker_user_prompt(paragraph, active_rules, compact=True)
            est = estimate_prompt_tokens(prebuilt_user_prompt=user_prompt)

        logger.info(
            "  P%d step 2/3 prompt: %d rules, ~%d tokens (%.0fms)",
            paragraph.paragraph_index, len(active_rules), est,
            (time.time() - t_prompt) * 1000,
        )

        # ── step 3: LLM call via ModelRouter ─────────────────────────
        t_llm = time.time()
        logger.info(
            "  P%d step 3/3 calling LLM (~%d tokens)…",
            paragraph.paragraph_index, est,
        )
        try:
            response, model_used = await self._router.chat_completion(
                messages=[
                    {"role": "system", "content": RULE_CHECKER_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                estimated_tokens=est,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            logger.error(
                "  P%d LLM call FAILED after %.1fs: %s",
                paragraph.paragraph_index, time.time() - t_llm, exc,
            )
            return ParagraphResult(
                paragraph_index=paragraph.paragraph_index,
                plain_text=paragraph.plain_text,
                is_compliant=True,  # optimistic on error
            )

        actual_tokens = response.usage.total_tokens if response.usage else 0
        logger.info(
            "  P%d done: est=%d actual=%d tokens [%s] (%.1fs LLM, %.1fs total)",
            paragraph.paragraph_index, est, actual_tokens, model_used,
            time.time() - t_llm, time.time() - t_start,
        )

        raw_json = response.choices[0].message.content or "{}"
        return self._parse_llm_response(raw_json, paragraph)

    def _parse_llm_response(self, raw_json: str, paragraph: Any) -> ParagraphResult:
        """Parse the LLM JSON response into a ParagraphResult."""
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from LLM for paragraph %d", paragraph.paragraph_index)
            return ParagraphResult(
                paragraph_index=paragraph.paragraph_index,
                plain_text=paragraph.plain_text,
            )

        MIN_CONFIDENCE = 0.6

        violations: list[RuleViolation] = []
        for v in data.get("violations", []):
            confidence = v.get("confidence", 0.0)
            # Skip low-confidence entries — these are typically false positives
            # where the LLM itself notes "no violation here".
            if confidence < MIN_CONFIDENCE:
                logger.debug(
                    "Dropping low-confidence (%.2f) result for rule %s on P%d",
                    confidence, v.get("rule_id", "?"), paragraph.paragraph_index,
                )
                continue

            rule_id = v.get("rule_id", "")
            # find the matching rule to enrich fields
            matched = next((r for r in self._rules if r.rule_id == rule_id), None)
            violations.append(
                RuleViolation(
                    rule_id=rule_id,
                    rule_text=matched.rule_text if matched else "",
                    rule_type=matched.rule_type if matched else "unspecified",
                    severity=matched.severity if matched else "recommended",
                    section_title=matched.section_title if matched else "",
                    page=matched.page if matched else None,
                    paragraph_index=paragraph.paragraph_index,
                    violated_text=v.get("violated_text", ""),
                    violated_run_indices=v.get("violated_run_indices", []),
                    explanation=v.get("explanation", ""),
                    suggestion=v.get("suggestion", ""),
                    confidence=confidence,
                    fix_type=v.get("fix_type", "manual"),
                    fix_value=v.get("fix_value", ""),
                )
            )

        return ParagraphResult(
            paragraph_index=paragraph.paragraph_index,
            plain_text=paragraph.plain_text,
            is_compliant=data.get("is_compliant", len(violations) == 0),
            violations=violations,
        )


# ======================================================================
# 3. Violation Aggregator Executor  (fan-in target / terminal)
# ======================================================================

class ViolationAggregatorExecutor(Executor):
    """Merge batch results into a single ``QualityCheckReport``.

    Also annotates the OpenXML with ``<w:highlight>`` for violated runs.

    Input : ``list[BatchCheckResult]``   (delivered automatically by fan-in)
    Output: yields ``QualityCheckReport`` as workflow output.
    """

    def __init__(self, *, id: str = "aggregator"):
        super().__init__(id=id)
        self._original_xml_by_paragraph: dict[int, str] = {}

    def set_paragraph_xml_map(self, paragraphs: Sequence[Any]) -> None:
        """Pre-populate the paragraph→XML mapping (called before workflow run)."""
        for p in paragraphs:
            self._original_xml_by_paragraph[p.paragraph_index] = p.original_xml

    @handler
    async def aggregate(
        self,
        batch_results: list[BatchCheckResult],
        ctx: WorkflowContext[Never, QualityCheckReport],
    ) -> None:
        await self._do_aggregate(batch_results if isinstance(batch_results, list) else [batch_results], ctx)

    @handler
    async def aggregate_single(
        self,
        batch_result: BatchCheckResult,
        ctx: WorkflowContext[Never, QualityCheckReport],
    ) -> None:
        await self._do_aggregate([batch_result], ctx)

    async def _do_aggregate(
        self,
        batch_results: list[BatchCheckResult],
        ctx: WorkflowContext[Never, QualityCheckReport],
    ) -> None:
        logger.info("Aggregating results from %d batch checkers", len(batch_results))

        # ── merge per-paragraph violations across batches ──────────────
        merged: dict[int, ParagraphResult] = {}
        total_rules_checked = 0

        for batch in batch_results:
            total_rules_checked += batch.rules_checked
            for pr in batch.paragraph_results:
                if pr.paragraph_index not in merged:
                    merged[pr.paragraph_index] = ParagraphResult(
                        paragraph_index=pr.paragraph_index,
                        plain_text=pr.plain_text,
                        is_compliant=True,
                    )
                target = merged[pr.paragraph_index]
                target.violations.extend(pr.violations)
                if not pr.is_compliant:
                    target.is_compliant = False

        # deduplicate violations by (rule_id, paragraph_index) — keep highest confidence
        for pr in merged.values():
            best: dict[str, RuleViolation] = {}
            for v in pr.violations:
                key = f"{v.rule_id}::{v.paragraph_index}"
                if key not in best or v.confidence > best[key].confidence:
                    best[key] = v
            pr.violations = list(best.values())
            pr.is_compliant = len(pr.violations) == 0

        # sort by paragraph index
        paragraph_results = sorted(merged.values(), key=lambda r: r.paragraph_index)

        # ── resolve contradictory violations ───────────────────────────
        paragraph_results = resolve_contradictions(paragraph_results)

        # ── highlight violated runs in OpenXML ─────────────────────────
        highlighted_paragraphs: list[str] = []
        for pr in paragraph_results:
            original_xml = self._original_xml_by_paragraph.get(pr.paragraph_index, "")
            if original_xml and pr.violations:
                all_run_indices: set[int] = set()
                for v in pr.violations:
                    all_run_indices.update(v.violated_run_indices)
                highlighted = highlight_violated_runs(
                    original_xml, sorted(all_run_indices)
                )
                highlighted_paragraphs.append(highlighted)
            elif original_xml:
                highlighted_paragraphs.append(original_xml)

        highlighted_xml = "\n".join(highlighted_paragraphs)

        # ── build final report ─────────────────────────────────────────
        compliant = sum(1 for pr in paragraph_results if pr.is_compliant)
        non_compliant = len(paragraph_results) - compliant
        total_violations = sum(len(pr.violations) for pr in paragraph_results)

        report = QualityCheckReport(
            total_paragraphs=len(paragraph_results),
            compliant_paragraphs=compliant,
            non_compliant_paragraphs=non_compliant,
            total_violations=total_violations,
            total_rules_checked=total_rules_checked,
            paragraph_results=paragraph_results,
            highlighted_xml=highlighted_xml,
        )

        logger.info(
            "Quality check complete: %d paragraphs, %d violations, %d rules checked",
            report.total_paragraphs,
            report.total_violations,
            report.total_rules_checked,
        )
        await ctx.yield_output(report)
