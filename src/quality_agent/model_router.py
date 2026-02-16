"""
Multi-deployment model router with proactive token-budget switching.

Manages a **primary** model (Claude Opus 4.6 on Databricks, by default)
and a **fallback** model (GPT-4.1 on Azure OpenAI).  Each deployment gets
its own sliding-window TokenRateLimiter and concurrency semaphore.

The primary uses the Databricks model serving endpoint which exposes an
OpenAI-compatible ``/chat/completions`` API (``AsyncOpenAI`` client,
``base_url=<host>/serving-endpoints``).

The fallback keeps ``AsyncAzureOpenAI`` so existing Azure deployments
continue to work as a safety net.

Routing logic
-------------
For every LLM call the router:

1. Checks if the *primary* deployment still has enough capacity
   (remaining TPM ≥ estimated_tokens × 1.3 headroom).
2. If yes → routes to primary.
3. If no  → checks fallback capacity.  If the fallback has room → use it.
4. If *both* are near capacity → waits on whichever frees earliest.

The router **never** waits for a 429.  After each response it records
the *actual* ``usage.total_tokens`` so the sliding window stays accurate.

Both deployments can serve requests concurrently, effectively doubling
throughput when both have budget remaining.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncAzureOpenAI, AsyncOpenAI, RateLimitError

logger = logging.getLogger(__name__)


# ======================================================================
# Sliding-window token-per-minute limiter  (per deployment)
# ======================================================================

class TokenBudget:
    """Sliding-window TPM tracker for a single deployment."""

    def __init__(self, max_tpm: int, *, safety: float = 0.80):
        self._max = int(max_tpm * safety)
        self._lock = asyncio.Lock()
        self._entries: list[tuple[float, int]] = []  # (monotonic_ts, tokens)

    # ── helpers ────────────────────────────────────────────────────────

    def _purge(self) -> None:
        cutoff = time.monotonic() - 60.0
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.pop(0)

    @property
    def used(self) -> int:
        self._purge()
        return sum(t for _, t in self._entries)

    @property
    def remaining(self) -> int:
        return max(0, self._max - self.used)

    @property
    def effective_limit(self) -> int:
        return self._max

    # ── public API ─────────────────────────────────────────────────────

    async def try_acquire(self, tokens: int) -> bool:
        """Non-blocking: reserve *tokens* if capacity exists."""
        async with self._lock:
            self._purge()
            if self.used + tokens <= self._max:
                self._entries.append((time.monotonic(), tokens))
                return True
            return False

    async def acquire_blocking(self, tokens: int) -> None:
        """Blocking: wait until *tokens* can fit in the window."""
        while True:
            async with self._lock:
                self._purge()
                used = sum(t for _, t in self._entries)
                if used + tokens <= self._max:
                    self._entries.append((time.monotonic(), tokens))
                    return
                # estimate wait
                need = used + tokens - self._max
                freed = 0
                wait_until = time.monotonic()
                for ts, tok in self._entries:
                    freed += tok
                    if freed >= need:
                        wait_until = ts + 60.0
                        break
            await asyncio.sleep(max(0.5, wait_until - time.monotonic()))

    def record_actual(self, estimated: int, actual: int) -> None:
        """Adjust the most recent entry to reflect real usage."""
        diff = actual - estimated
        if diff and self._entries:
            ts, tok = self._entries[-1]
            self._entries[-1] = (ts, max(0, tok + diff))


# ======================================================================
# Deployment descriptor
# ======================================================================

@dataclass
class Deployment:
    """One model deployment with its own budget and concurrency.

    The *client* can be either ``AsyncOpenAI`` (Databricks serving
    endpoint) or ``AsyncAzureOpenAI`` — both expose the same
    ``chat.completions.create()`` interface.
    """

    name: str
    client: AsyncOpenAI  # AsyncAzureOpenAI is a subclass of AsyncOpenAI
    budget: TokenBudget
    semaphore: asyncio.Semaphore
    priority: int = 0                # lower = preferred
    total_calls: int = field(default=0, init=False)
    total_tokens: int = field(default=0, init=False)


# ======================================================================
# Model Router
# ======================================================================

class ModelRouter:
    """Route LLM calls across multiple Azure OpenAI deployments.

    Parameters
    ----------
    deployments : list[Deployment]
        Ordered by priority (index 0 = primary).
    headroom : float
        Factor applied to *estimated_tokens* when deciding capacity.
        1.3 means "need 30 % more room than the estimate to be safe".
    """

    def __init__(
        self,
        deployments: list[Deployment],
        *,
        headroom: float = 1.3,
    ):
        if not deployments:
            raise ValueError("At least one deployment is required")
        self._deployments = sorted(deployments, key=lambda d: d.priority)
        self._headroom = headroom

    # ── convenience factory ────────────────────────────────────────────

    @classmethod
    def from_env(
        cls,
        *,
        # ── Databricks primary (Claude) ──────────────────────────────
        databricks_host: str = "",
        databricks_token: str = "",
        primary_model: str = "databricks-claude-opus-4-6",
        primary_tpm: int = 500_000,
        primary_concurrency: int = 2,
        # ── Azure OpenAI fallback ────────────────────────────────────
        azure_endpoint: str = "",
        azure_api_key: str | None = None,
        fallback_model: str = "gpt-4.1",
        fallback_tpm: int = 256_000,
        fallback_concurrency: int = 3,
        # ── legacy (kept for backward compat) ────────────────────────
        endpoint: str = "",
        api_key: str | None = None,
    ) -> "ModelRouter":
        """Build a two-deployment router.

        Primary  — Claude Opus 4.6 on a Databricks model serving endpoint
                   (OpenAI-compatible ``/serving-endpoints`` API).
        Fallback — GPT-4.1 on Azure OpenAI (AsyncAzureOpenAI).
        """

        # ── primary: Databricks (AsyncOpenAI) ─────────────────────────
        db_host = databricks_host.rstrip("/")
        if not db_host:
            raise ValueError(
                "Databricks host required — set DATABRICKS_HOST or pass databricks_host"
            )
        primary_client = AsyncOpenAI(
            api_key=databricks_token,
            base_url=f"{db_host}/serving-endpoints",
        )

        primary = Deployment(
            name=primary_model,
            client=primary_client,
            budget=TokenBudget(primary_tpm),
            semaphore=asyncio.Semaphore(primary_concurrency),
            priority=0,
        )

        # ── fallback: Azure OpenAI (AsyncAzureOpenAI) ─────────────────
        az_ep = azure_endpoint or endpoint  # backward compat
        az_key = azure_api_key or api_key
        deployments: list[Deployment] = [primary]

        if az_ep:
            def _make_azure_client() -> AsyncAzureOpenAI:
                extra: dict[str, Any] = {}
                if az_key:
                    extra["api_key"] = az_key
                else:
                    from azure.identity import (
                        DefaultAzureCredential,
                        get_bearer_token_provider,
                    )
                    extra["azure_ad_token_provider"] = get_bearer_token_provider(
                        DefaultAzureCredential(),
                        "https://cognitiveservices.azure.com/.default",
                    )
                return AsyncAzureOpenAI(
                    azure_endpoint=az_ep,
                    api_version="2025-01-01-preview",
                    **extra,
                )

            fallback = Deployment(
                name=fallback_model,
                client=_make_azure_client(),
                budget=TokenBudget(fallback_tpm),
                semaphore=asyncio.Semaphore(fallback_concurrency),
                priority=1,
            )
            deployments.append(fallback)
            logger.info(
                "ModelRouter: primary=%s [Databricks] (%dK TPM, %d conc), "
                "fallback=%s [Azure] (%dK TPM, %d conc)",
                primary_model, primary_tpm // 1000, primary_concurrency,
                fallback_model, fallback_tpm // 1000, fallback_concurrency,
            )
        else:
            logger.info(
                "ModelRouter: primary=%s [Databricks] (%dK TPM, %d conc), no fallback",
                primary_model, primary_tpm // 1000, primary_concurrency,
            )

        return cls(deployments)

    # ── properties ─────────────────────────────────────────────────────

    @property
    def primary(self) -> Deployment:
        return self._deployments[0]

    @property
    def fallback(self) -> Deployment | None:
        return self._deployments[1] if len(self._deployments) > 1 else None

    def status(self) -> dict[str, Any]:
        """Health-check snapshot."""
        out: list[dict[str, Any]] = []
        for d in self._deployments:
            out.append({
                "model": d.name,
                "tpm_limit": d.budget.effective_limit,
                "tpm_used": d.budget.used,
                "tpm_remaining": d.budget.remaining,
                "total_calls": d.total_calls,
                "total_tokens": d.total_tokens,
            })
        return {"deployments": out}

    # ── routing core ───────────────────────────────────────────────────

    def _pick_deployment(self, estimated_tokens: int) -> Deployment | None:
        """Return the best deployment that currently has capacity,
        or ``None`` if all are exhausted (caller must wait)."""
        need = int(estimated_tokens * self._headroom)
        for d in self._deployments:
            if d.budget.remaining >= need:
                return d
        return None

    async def chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        estimated_tokens: int,
        temperature: float = 0.1,
        response_format: dict[str, str] | None = None,
        max_retries: int = 3,
    ) -> tuple[Any, str]:
        """Make a chat-completion call, routing to the best deployment.

        Returns
        -------
        (response, deployment_name)
            The raw OpenAI response and the name of the deployment used.
        """

        dep = self._pick_deployment(estimated_tokens)

        if dep is None:
            # All deployments near capacity — fall back to blocking on the
            # one with the *most* remaining room (it'll free soonest).
            dep = max(self._deployments, key=lambda d: d.budget.remaining)
            logger.info(
                "All deployments near capacity — waiting on %s (remaining %d)",
                dep.name, dep.budget.remaining,
            )
            await dep.budget.acquire_blocking(estimated_tokens)
        else:
            # Non-blocking reservation succeeded in _pick_deployment check,
            # but we still need to formally reserve the tokens.
            acquired = await dep.budget.try_acquire(estimated_tokens)
            if not acquired:
                # Race condition: another coroutine grabbed it — block.
                await dep.budget.acquire_blocking(estimated_tokens)

        # ── make the call with retry ──────────────────────────────────
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            async with dep.semaphore:
                try:
                    kwargs: dict[str, Any] = {
                        "model": dep.name,
                        "temperature": temperature,
                        "messages": messages,
                    }
                    if response_format:
                        kwargs["response_format"] = response_format

                    response = await dep.client.chat.completions.create(**kwargs)

                    # ── track actual usage ─────────────────────────────
                    actual = 0
                    if response.usage:
                        actual = response.usage.total_tokens
                        dep.budget.record_actual(estimated_tokens, actual)
                    dep.total_calls += 1
                    dep.total_tokens += actual

                    logger.debug(
                        "[%s] est=%d actual=%d remaining=%d",
                        dep.name, estimated_tokens, actual, dep.budget.remaining,
                    )
                    return response, dep.name

                except RateLimitError as exc:
                    last_exc = exc
                    # Despite proactive routing, a 429 can still occur
                    # (e.g. other consumers on the same deployment).
                    retry_after = _parse_retry_after(exc)
                    logger.warning(
                        "[%s] 429 (attempt %d/%d) — retry in %.1fs",
                        dep.name, attempt + 1, max_retries, retry_after,
                    )

                    # Try switching to the *other* deployment immediately
                    other = self._find_alternative(dep, estimated_tokens)
                    if other is not None:
                        logger.info(
                            "Switching from %s → %s due to 429",
                            dep.name, other.name,
                        )
                        dep = other
                        acquired = await dep.budget.try_acquire(estimated_tokens)
                        if not acquired:
                            await dep.budget.acquire_blocking(estimated_tokens)
                        continue  # retry immediately on the other deployment

                    await asyncio.sleep(retry_after)

                except Exception as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        await asyncio.sleep(2.0 * (2 ** attempt))
                        continue
                    raise

        raise last_exc or RuntimeError("chat_completion failed")

    def _find_alternative(
        self, exclude: Deployment, estimated_tokens: int
    ) -> Deployment | None:
        need = int(estimated_tokens * self._headroom)
        for d in self._deployments:
            if d is not exclude and d.budget.remaining >= need:
                return d
        return None


# ── helpers ────────────────────────────────────────────────────────────

def _parse_retry_after(exc: RateLimitError) -> float:
    """Extract retry-after seconds from a 429 error."""
    retry = getattr(exc, "retry_after", None)
    if retry is not None:
        return float(retry)
    m = re.search(r"retry after (\d+)", str(exc), re.IGNORECASE)
    return float(m.group(1)) if m else 5.0
