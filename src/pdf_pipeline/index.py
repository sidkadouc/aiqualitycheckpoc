"""
STEP 6 — Indexation dans Azure AI Search + Cosmos DB.

Deux destinations :
1. Azure AI Search : chunks avec embeddings vectoriels pour recherche sémantique
2. Cosmos DB : règles structurées pour application directe des policies

L'index AI Search permet au Word Add-in de retrouver les règles pertinentes
par rapport au paragraphe en cours d'édition.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from openai import AzureOpenAI

from .config import AzureConfig, PipelineConfig
from .models import Chunk, PolicyRule, PolicyRuleSet, SearchIndexDocument

logger = logging.getLogger(__name__)


# =============================================================================
# Embedding generation
# =============================================================================

def _get_openai_client(azure_cfg: AzureConfig) -> AzureOpenAI:
    """Create Azure OpenAI client for embedding generation."""
    return AzureOpenAI(
        azure_endpoint=azure_cfg.openai_endpoint,
        api_key=azure_cfg.openai_key or None,
        api_version="2025-01-01-preview",
        azure_ad_token_provider=(
            None
            if azure_cfg.openai_key
            else _get_token_provider()
        ),
    )


def _get_token_provider():
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    credential = DefaultAzureCredential()
    return get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )


def generate_embeddings(
    texts: list[str],
    azure_cfg: AzureConfig,
    pipeline_cfg: PipelineConfig,
) -> list[list[float]]:
    """
    Generate embeddings for a list of texts using Azure OpenAI.
    Handles batching to stay within API limits.
    """
    client = _get_openai_client(azure_cfg)
    deployment = pipeline_cfg.embedding_model
    batch_size = 16  # Azure OpenAI embedding batch limit

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            response = client.embeddings.create(
                model=deployment,
                input=batch,
                dimensions=pipeline_cfg.embedding_dimensions,
            )
            for item in response.data:
                all_embeddings.append(item.embedding)
        except Exception as e:
            logger.error("Error generating embeddings (batch %d): %s", i, str(e))
            # Append zero vectors as fallback
            for _ in batch:
                all_embeddings.append([0.0] * pipeline_cfg.embedding_dimensions)
            time.sleep(2)

        # Rate limiting
        if i + batch_size < len(texts):
            time.sleep(0.5)

    return all_embeddings


# =============================================================================
# Azure AI Search index management
# =============================================================================

def _get_search_credential(azure_cfg: AzureConfig):
    """Get credential for Azure AI Search."""
    if azure_cfg.use_managed_identity:
        return DefaultAzureCredential()
    if not azure_cfg.search_key:
        raise ValueError("AZURE_AISEARCH_KEY must be set (or enable USE_MANAGED_IDENTITY).")
    return AzureKeyCredential(azure_cfg.search_key)


def create_search_index(
    azure_cfg: AzureConfig,
    pipeline_cfg: PipelineConfig,
) -> None:
    """Create or update the Azure AI Search index for policy chunks."""
    credential = _get_search_credential(azure_cfg)
    index_client = SearchIndexClient(
        endpoint=azure_cfg.search_endpoint,
        credential=credential,
    )

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="section_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="section_title", type=SearchFieldDataType.String),
        SearchableField(
            name="content",
            type=SearchFieldDataType.String,
            analyzer_name="fr.microsoft",  # French analyzer for OECD docs
        ),
        SimpleField(name="page_start", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
        SimpleField(name="page_end", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="token_count", type=SearchFieldDataType.Int32),
        SearchableField(name="document_name", type=SearchFieldDataType.String, filterable=True),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=pipeline_cfg.embedding_dimensions,
            vector_search_profile_name="vector-profile",
        ),
        SimpleField(
            name="rule_ids",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SearchableField(
            name="rule_summaries",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(name="hnsw-config"),
        ],
        profiles=[
            VectorSearchProfile(
                name="vector-profile",
                algorithm_configuration_name="hnsw-config",
            ),
        ],
    )

    index = SearchIndex(
        name=azure_cfg.search_index_name,
        fields=fields,
        vector_search=vector_search,
    )

    index_client.create_or_update_index(index)
    logger.info("Search index '%s' created/updated", azure_cfg.search_index_name)


def index_chunks(
    chunks: list[Chunk],
    rules: PolicyRuleSet,
    azure_cfg: AzureConfig,
    pipeline_cfg: PipelineConfig,
) -> None:
    """
    Index all chunks into Azure AI Search with embeddings.
    Also associates related rules to each chunk.
    """
    logger.info("Indexing %d chunks to Azure AI Search", len(chunks))

    # Step 1: Create/update index
    create_search_index(azure_cfg, pipeline_cfg)

    # Step 2: Build rule lookup (section_id → rules)
    rules_by_section: dict[str, list[PolicyRule]] = {}
    for rule in rules.rules:
        if rule.section_id not in rules_by_section:
            rules_by_section[rule.section_id] = []
        rules_by_section[rule.section_id].append(rule)

    # Step 3: Generate embeddings for all chunks
    texts = [f"{c.section_title}\n\n{c.content}" for c in chunks]
    logger.info("Generating embeddings for %d chunks...", len(chunks))
    embeddings = generate_embeddings(texts, azure_cfg, pipeline_cfg)

    # Step 4: Build index documents
    documents = []
    for chunk, embedding in zip(chunks, embeddings):
        section_rules = rules_by_section.get(chunk.section_id, [])
        doc = SearchIndexDocument(
            id=chunk.chunk_id.replace("_", "-"),  # Azure Search requires specific ID format
            chunk_id=chunk.chunk_id,
            section_id=chunk.section_id,
            section_title=chunk.section_title,
            content=chunk.content,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            token_count=chunk.token_count,
            document_name=rules.document_name,
            embedding=embedding,
            rule_ids=[r.rule_id for r in section_rules],
            rule_summaries=[r.rule_summary for r in section_rules],
        )
        documents.append(doc.model_dump())

    # Step 5: Upload in batches
    credential = _get_search_credential(azure_cfg)
    search_client = SearchClient(
        endpoint=azure_cfg.search_endpoint,
        index_name=azure_cfg.search_index_name,
        credential=credential,
    )

    batch_size = 100
    for i in range(0, len(documents), batch_size):
        batch = documents[i : i + batch_size]
        result = search_client.upload_documents(documents=batch)
        succeeded = sum(1 for r in result if r.succeeded)
        logger.info(
            "Uploaded batch %d-%d: %d/%d succeeded",
            i,
            i + len(batch),
            succeeded,
            len(batch),
        )

    logger.info("AI Search indexing complete")


# =============================================================================
# Cosmos DB — Store structured rules
# =============================================================================

def store_rules_in_cosmos(
    rules: PolicyRuleSet,
    azure_cfg: AzureConfig,
) -> None:
    """
    Store extracted rules in Cosmos DB for structured access.
    Each rule is stored as a separate document with section-based partitioning.
    """
    from azure.cosmos import CosmosClient, PartitionKey

    logger.info("Storing %d rules in Cosmos DB", rules.total_rules)

    if azure_cfg.use_managed_identity:
        credential = DefaultAzureCredential()
        client = CosmosClient(azure_cfg.cosmos_endpoint, credential=credential)
    else:
        client = CosmosClient(azure_cfg.cosmos_endpoint, credential=azure_cfg.cosmos_key)

    database = client.create_database_if_not_exists(id=azure_cfg.cosmos_database)

    # Create container for policy rules (partitioned by section_id)
    container = database.create_container_if_not_exists(
        id=azure_cfg.cosmos_rules_container,
        partition_key=PartitionKey(path="/section_id"),
    )

    # Upsert each rule
    for rule in rules.rules:
        doc = rule.model_dump()
        doc["id"] = rule.rule_id
        container.upsert_item(doc)

    # Also store the full ruleset as a summary document
    summary_doc = {
        "id": f"ruleset-{rules.document_name}",
        "section_id": "_summary",
        "type": "ruleset_summary",
        "document_name": rules.document_name,
        "total_rules": rules.total_rules,
        "rules_by_severity": {
            "mandatory": sum(1 for r in rules.rules if r.severity.value == "mandatory"),
            "recommended": sum(1 for r in rules.rules if r.severity.value == "recommended"),
            "optional": sum(1 for r in rules.rules if r.severity.value == "optional"),
            "informational": sum(1 for r in rules.rules if r.severity.value == "informational"),
        },
        "rule_ids": [r.rule_id for r in rules.rules],
    }
    container.upsert_item(summary_doc)

    logger.info("Cosmos DB storage complete")
