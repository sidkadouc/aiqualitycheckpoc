"""
Quality Checker — Module pour le Word Add-in.

Ce module fournit l'API pour vérifier un paragraphe Word contre les règles
extraites du document OCDE.

Utilisation depuis le Word Add-in :
1. L'add-in envoie le paragraphe en cours d'édition
2. Le checker cherche les règles pertinentes via Azure AI Search (vecteurs)
3. Le LLM évalue la conformité du paragraphe à chaque règle trouvée
4. Retourne un rapport de conformité structuré
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery
from openai import AzureOpenAI

from .config import AzureConfig, PipelineConfig

logger = logging.getLogger(__name__)


@dataclass
class ComplianceIssue:
    """A single compliance issue found in a paragraph."""
    rule_id: str
    rule_summary: str
    severity: str  # mandatory, recommended, optional
    issue_description: str
    suggestion: str
    confidence: float = 0.0


@dataclass
class QualityReport:
    """Quality check report for a paragraph."""
    paragraph_text: str
    is_compliant: bool
    overall_score: float  # 0.0 to 1.0
    issues: list[ComplianceIssue] = field(default_factory=list)
    relevant_rules_count: int = 0
    checked_rules_count: int = 0


_CHECKER_SYSTEM_PROMPT = """Tu es un expert en conformité réglementaire.
Tu évalues si un paragraphe est conforme aux règles normatives fournies.

Pour chaque règle non respectée, tu dois :
1. Expliquer clairement pourquoi le paragraphe ne la respecte pas
2. Proposer une suggestion de correction concrète
3. Évaluer ton niveau de confiance (0.0 = incertain, 1.0 = certain)

Réponds UNIQUEMENT en JSON avec ce format :
{
  "is_compliant": true/false,
  "overall_score": 0.0-1.0,
  "issues": [
    {
      "rule_id": "...",
      "issue_description": "...",
      "suggestion": "...",
      "confidence": 0.0-1.0
    }
  ]
}

Si le paragraphe est conforme à toutes les règles, retourne is_compliant=true avec un tableau issues vide.
Sois rigoureux mais évite les faux positifs — ne signale que les violations claires."""


class QualityChecker:
    """
    Check paragraph quality against indexed policy rules.
    Designed to be called from the Word Add-in backend API.
    """

    def __init__(
        self,
        azure_cfg: AzureConfig | None = None,
        pipeline_cfg: PipelineConfig | None = None,
    ):
        if azure_cfg is None or pipeline_cfg is None:
            from .config import load_config
            loaded_a, loaded_p = load_config()
            azure_cfg = azure_cfg or loaded_a
            pipeline_cfg = pipeline_cfg or loaded_p

        self.azure_cfg = azure_cfg
        self.pipeline_cfg = pipeline_cfg
        self._search_client = self._init_search_client()
        self._openai_client = self._init_openai_client()

    def _init_search_client(self) -> SearchClient:
        if self.azure_cfg.use_managed_identity:
            credential = DefaultAzureCredential()
        else:
            credential = AzureKeyCredential(self.azure_cfg.search_key)

        return SearchClient(
            endpoint=self.azure_cfg.search_endpoint,
            index_name=self.azure_cfg.search_index_name,
            credential=credential,
        )

    def _init_openai_client(self) -> AzureOpenAI:
        return AzureOpenAI(
            azure_endpoint=self.azure_cfg.openai_endpoint,
            api_key=self.azure_cfg.openai_key or None,
            api_version="2025-01-01-preview",
            azure_ad_token_provider=(
                None
                if self.azure_cfg.openai_key
                else self._get_token_provider()
            ),
        )

    @staticmethod
    def _get_token_provider():
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        credential = DefaultAzureCredential()
        return get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )

    def check_paragraph(
        self,
        paragraph_text: str,
        top_k: int = 10,
    ) -> QualityReport:
        """
        Check a Word paragraph against indexed policy rules.

        1. Vector search for relevant rules/chunks
        2. LLM evaluation of compliance
        3. Return structured quality report
        """
        if not paragraph_text.strip():
            return QualityReport(
                paragraph_text=paragraph_text,
                is_compliant=True,
                overall_score=1.0,
            )

        # Step 1: Find relevant rules via hybrid search (vector + keyword)
        logger.info("Searching for relevant rules for paragraph (%d chars)", len(paragraph_text))

        results = self._search_client.search(
            search_text=paragraph_text,
            vector_queries=[
                VectorizableTextQuery(
                    text=paragraph_text,
                    k_nearest_neighbors=top_k,
                    fields="embedding",
                ),
            ],
            top=top_k,
            select=["chunk_id", "section_title", "content", "rule_ids", "rule_summaries"],
        )

        relevant_chunks = []
        all_rule_summaries = []
        all_rule_ids = []

        for result in results:
            relevant_chunks.append({
                "section": result["section_title"],
                "content": result["content"],
            })
            if result.get("rule_ids"):
                all_rule_ids.extend(result["rule_ids"])
            if result.get("rule_summaries"):
                all_rule_summaries.extend(result["rule_summaries"])

        if not relevant_chunks:
            return QualityReport(
                paragraph_text=paragraph_text,
                is_compliant=True,
                overall_score=1.0,
            )

        # Step 2: Build context for LLM
        rules_context = "\n".join(
            f"- [{rid}] {summary}"
            for rid, summary in zip(all_rule_ids, all_rule_summaries)
        )

        chunks_context = "\n\n".join(
            f"[{c['section']}]\n{c['content']}" for c in relevant_chunks[:5]
        )

        # Step 3: LLM compliance check
        user_message = f"""Paragraphe à vérifier :
---
{paragraph_text}
---

Règles normatives applicables :
{rules_context}

Contexte du document source :
{chunks_context}

Évalue la conformité du paragraphe aux règles ci-dessus."""

        try:
            response = self._openai_client.chat.completions.create(
                model=self.pipeline_cfg.gpt41_deployment
                if hasattr(self.pipeline_cfg, "gpt41_deployment")
                else "gpt-4.1",
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _CHECKER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            )

            raw = json.loads(response.choices[0].message.content)

            issues = []
            for issue_data in raw.get("issues", []):
                rule_id = issue_data.get("rule_id", "")
                # Find matching summary
                idx = all_rule_ids.index(rule_id) if rule_id in all_rule_ids else -1
                summary = all_rule_summaries[idx] if idx >= 0 else ""

                issues.append(
                    ComplianceIssue(
                        rule_id=rule_id,
                        rule_summary=summary,
                        severity="recommended",  # Will be enriched from Cosmos DB
                        issue_description=issue_data.get("issue_description", ""),
                        suggestion=issue_data.get("suggestion", ""),
                        confidence=issue_data.get("confidence", 0.5),
                    )
                )

            return QualityReport(
                paragraph_text=paragraph_text,
                is_compliant=raw.get("is_compliant", True),
                overall_score=raw.get("overall_score", 1.0),
                issues=issues,
                relevant_rules_count=len(all_rule_ids),
                checked_rules_count=len(set(all_rule_ids)),
            )

        except Exception as e:
            logger.error("Error during LLM compliance check: %s", e)
            return QualityReport(
                paragraph_text=paragraph_text,
                is_compliant=True,
                overall_score=0.0,
                issues=[],
                relevant_rules_count=len(all_rule_ids),
            )
