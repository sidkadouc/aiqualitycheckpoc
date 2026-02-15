"""
PDF Policy Extraction Pipeline
================================
Pipeline Azure-native pour extraire, structurer et indexer les règles/policies
à partir de documents PDF (OCDE ~150 pages).

Architecture:
  PDF → Document Intelligence → Reconstruction hiérarchique → Nettoyage
  → Chunking sémantique → Extraction de règles (LLM) → Indexation (AI Search + Cosmos DB)
"""

__version__ = "0.1.0"
