# Pipeline d'extraction de règles PDF — PoC AI Quality Check

## Architecture

```
PDF (OCDE ~150 pages)
  │
  ▼
┌─────────────────────────────────────┐
│  1. Azure Document Intelligence     │  Extraction Layout
│     (prebuilt-layout)               │  → paragraphes, titres, tables, pages
└────────────┬────────────────────────┘
             │
  ▼
┌─────────────────────────────────────┐
│  2. Reconstruction hiérarchique     │  reconstruct.py
│     Détection headings → arbre      │  → sections, sous-sections, paragraphes
└────────────┬────────────────────────┘
             │
  ▼
┌─────────────────────────────────────┐
│  3. Nettoyage & normalisation       │  clean.py
│     Retrait artifacts, Unicode fix  │  → texte propre
└────────────┬────────────────────────┘
             │
  ▼
┌─────────────────────────────────────┐
│  4. Chunking sémantique             │  chunk.py
│     Respect paragraphes complets    │  → chunks ≤800 tokens
│     Aligné sur sections             │
└────────────┬────────────────────────┘
             │
  ▼
┌─────────────────────────────────────┐
│  5. Structuration des règles         │  extract_rules.py
│     Déterministe (défaut) ou LLM    │  → PolicyRule[] avec sévérité
│     env: USE_LLM_EXTRACTION         │
└────────────┬────────────────────────┘
             │
  ▼
┌──────────────────┬──────────────────┐
│  6a. AI Search   │  6b. Cosmos DB   │  index.py
│  Chunks +        │  Règles          │
│  embeddings      │  structurées     │
│  (vecteurs)      │  (JSON)          │
└──────────────────┴──────────────────┘
             │
  ▼
┌─────────────────────────────────────┐
│  Word Add-in Quality Checker        │  quality_checker.py
│  Vérifie conformité paragraphe      │
│  → Recherche hybride → LLM eval    │
└─────────────────────────────────────┘
```

## Prérequis

- Python 3.11+
- Ressources Azure déployées (voir `foundation/main.bicep`)
- Variables d'environnement configurées (voir `.env.template`)

## Installation

```bash
cd src
pip install -r requirements.txt
```

## Utilisation

### 1. Exécuter la pipeline complète

```bash
# Depuis le dossier src/
python run_pipeline.py chemin/vers/document_ocde.pdf --verbose
```

Options :
- `--output-dir DIR` : Dossier de sortie (défaut: `pipeline_output`)
- `--max-chunk-tokens N` : Tokens max par chunk (défaut: 800)
- `--skip-extraction` : Utiliser le cache (ne pas ré-extraire le PDF)
- `--skip-rules` : Ne pas extraire les règles (chunking uniquement)
- `--skip-indexing` : Ne pas indexer (traitement local uniquement)
- `--no-intermediate` : Ne pas sauvegarder les fichiers intermédiaires
- `--verbose` / `-v` : Logs détaillés

### 2. Exécution par étapes (debug/développement)

```python
from pdf_pipeline.config import AzureConfig, PipelineConfig
from pdf_pipeline.extract import extract_pdf
from pdf_pipeline.reconstruct import reconstruct_hierarchy
from pdf_pipeline.clean import clean_document
from pdf_pipeline.chunk import chunk_document
from pdf_pipeline.extract_rules import extract_rules
from pdf_pipeline.index import index_chunks, store_rules_in_cosmos

azure_cfg = AzureConfig()
pipeline_cfg = PipelineConfig(output_dir="debug_output", save_intermediate_files=True)

# Étape 1 - Extraction
raw = extract_pdf("mon_document.pdf", azure_cfg, pipeline_cfg)

# Étape 2 - Reconstruction
doc = reconstruct_hierarchy(raw, pipeline_cfg)

# Étape 3 - Nettoyage
doc = clean_document(doc, pipeline_cfg)

# Étape 4 - Chunking
chunks = chunk_document(doc, pipeline_cfg)

# Étape 5 - Extraction de règles
rules = extract_rules(chunks, azure_cfg, pipeline_cfg)

# Étape 6 - Indexation
index_chunks(chunks, rules, azure_cfg, pipeline_cfg)
store_rules_in_cosmos(rules, azure_cfg)
```

### 3. Utiliser le Quality Checker (depuis l'API Word Add-in)

```python
from pdf_pipeline.quality_checker import QualityChecker

checker = QualityChecker()
report = checker.check_paragraph("Le texte du paragraphe Word en cours d'édition...")

print(f"Conforme: {report.is_compliant}")
print(f"Score: {report.overall_score}")
for issue in report.issues:
    print(f"  [{issue.severity}] {issue.issue_description}")
    print(f"  Suggestion: {issue.suggestion}")
```

## Fichiers intermédiaires générés

| Fichier | Description |
|---------|-------------|
| `01_raw_layout.json` | Extraction brute Document Intelligence |
| `02_structured_document.json` | Document hiérarchique reconstruit |
| `03_cleaned_document.json` | Document nettoyé et normalisé |
| `04_chunks.json` | Chunks sémantiques |
| `05_extracted_rules.json` | Règles normatives extraites |
| `pipeline_summary.txt` | Résumé d'exécution |

## Pourquoi cette pipeline évite les "bad chunks"

1. **Layout-aware** : Document Intelligence détecte les rôles (heading, body, footer)
2. **Respect de la hiérarchie** : Les chunks sont alignés sur les sections/sous-sections
3. **Jamais de coupure mid-paragraph** : Le chunking respecte les unités de sens
4. **Nettoyage intelligent** : Les artefacts PDF sont éliminés avant indexation
5. **Métadonnées conservées** : Chaque chunk garde sa section, ses pages, ses IDs

## Coûts estimés (150 pages)

| Service | Coût estimé |
|---------|------------|
| Document Intelligence (layout) | ~0.50-1.50$ |
| OpenAI — Extraction règles (LLM mode) | ~2-5$ (0$ en mode déterministe) |
| OpenAI — Embeddings | ~0.10$ |
| AI Search (basic) | inclus dans plan |
| Cosmos DB (serverless) | ~0.01$ |

## Infrastructure Azure requise

Déjà provisionnée via `foundation/main.bicep` :
- **Azure AI Foundry** : GPT-4.1 + GPT-4o + text-embedding-3-large
- **Azure AI Search** : Recherche hybride (vecteurs + texte) avec analyseur français
- **Cosmos DB** : Stockage structuré des règles (partitionné par section)
- **Storage Account** : Blob container `documents` pour les PDFs source
- **Key Vault** : Secrets (clés, endpoints)

## Container Cosmos DB supplémentaire requis

La pipeline crée automatiquement un container `policy-rules` dans la base `appdata`.
Pour le pré-provisionner via Bicep, ajouter au `cosmos-db.bicep` :

```bicep
resource policyRulesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDatabase
  name: 'policy-rules'
  properties: {
    resource: {
      id: 'policy-rules'
      partitionKey: {
        paths: ['/section_id']
        kind: 'Hash'
      }
      indexingPolicy: {
        automatic: true
        indexingMode: 'consistent'
      }
    }
  }
}
```
