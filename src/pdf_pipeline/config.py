"""
Configuration for the PDF extraction pipeline.
Loads settings from environment variables (compatible with azd deployment outputs).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class AzureConfig:
    """Azure service configuration loaded from environment variables."""

    # --- Azure AI Foundry / OpenAI ---
    openai_endpoint: str = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    )
    openai_key: str = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_KEY", "")
    )
    gpt41_deployment: str = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_GPT41_DEPLOYMENT", "gpt-4.1")
    )
    gpt4o_deployment: str = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_GPT4O_DEPLOYMENT", "gpt-4o")
    )
    embedding_deployment: str = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    )

    # --- Azure Document Intelligence ---
    # Uses the same AI Services endpoint (AI Foundry with Content Understanding)
    doc_intelligence_endpoint: str = field(
        default_factory=lambda: os.environ.get(
            "AZURE_CONTENT_UNDERSTANDING_ENDPOINT",
            os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
        )
    )
    doc_intelligence_key: str = field(
        default_factory=lambda: os.environ.get("AZURE_DOC_INTELLIGENCE_KEY", "")
    )

    # --- Azure AI Search ---
    search_endpoint: str = field(
        default_factory=lambda: os.environ.get("AZURE_AISEARCH_ENDPOINT", "")
    )
    search_key: str = field(
        default_factory=lambda: os.environ.get("AZURE_AISEARCH_KEY", "")
    )
    search_index_name: str = field(
        default_factory=lambda: os.environ.get("AZURE_SEARCH_INDEX_NAME", "policy-chunks")
    )

    # --- Azure Cosmos DB ---
    cosmos_endpoint: str = field(
        default_factory=lambda: os.environ.get("AZURE_COSMOS_ENDPOINT", "")
    )
    cosmos_key: str = field(
        default_factory=lambda: os.environ.get("AZURE_COSMOS_KEY", "")
    )
    cosmos_database: str = field(
        default_factory=lambda: os.environ.get("AZURE_COSMOS_DATABASE", "appdata")
    )
    cosmos_rules_container: str = field(
        default_factory=lambda: os.environ.get("AZURE_COSMOS_RULES_CONTAINER", "policy-rules")
    )

    # --- Azure Storage ---
    storage_connection_string: str = field(
        default_factory=lambda: os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    )
    storage_container: str = field(
        default_factory=lambda: os.environ.get("AZURE_STORAGE_CONTAINER", "documents")
    )

    # --- Use Managed Identity (recommended for production) ---
    use_managed_identity: bool = field(
        default_factory=lambda: os.environ.get("USE_MANAGED_IDENTITY", "false").lower() == "true"
    )


@dataclass
class PipelineConfig:
    """Pipeline processing configuration."""

    # Chunking
    max_chunk_tokens: int = 800
    min_chunk_tokens: int = 100
    chunk_overlap_sentences: int = 1  # sentences overlap between chunks

    # Token estimation (approximation factor for French text)
    token_estimation_factor: float = 1.4

    # Cleaning
    remove_page_headers: bool = True
    remove_page_footers: bool = True
    remove_page_numbers: bool = True

    # Rule extraction
    use_llm_extraction: bool = field(
        default_factory=lambda: os.environ.get("USE_LLM_EXTRACTION", "false").lower() == "true"
    )
    rule_extraction_model: str = "gpt-4.1"  # which model to use for rule extraction
    rule_extraction_temperature: float = 0.1
    rule_extraction_batch_size: int = 5  # chunks per LLM call

    # Embedding
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 3072

    # Output
    output_dir: str = "pipeline_output"
    save_intermediate_files: bool = True


def load_config() -> tuple[AzureConfig, PipelineConfig]:
    """Load configuration from environment variables and defaults."""
    return AzureConfig(), PipelineConfig()
