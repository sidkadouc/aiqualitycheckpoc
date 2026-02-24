# OECD AI Quality Checker — PoC

Three-part application: a **PDF extraction pipeline**, a **FastAPI quality-check API**, and a **Word Web Add-in**.

## Architecture overview

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

---

## Prerequisites

| Tool | Version | Required for |
|------|---------|-------------|
| Python | 3.11+ | API + Pipeline |
| Node.js | 20+ | Word Add-in |
| Azure CLI | latest | Deployment / azure credentials |
| Docker | latest | Container builds (optional for local dev) |
| Word Desktop | Microsoft 365 | Sideloading the add-in |

You also need at least the following Azure resources provisioned (see `foundation/main.bicep`):

- **Azure AI Foundry** (OpenAI) — GPT-4.1 + text-embedding-3-large deployments
- **Azure AI Search** — for hybrid vector search
- **Cosmos DB** (serverless) — for structured rules storage
- **Storage Account** — blob container `documents` for source PDFs
- **Key Vault** — stores secrets and endpoints

> **Tip**: Run `.\deploy-all.ps1 -InfraOnly` to provision all infrastructure before starting local development.

---

## Part 1 — Quality Checker API (FastAPI)

The API is the backend that the Word add-in calls. It loads rules at startup and evaluates paragraphs against them.

### 1.1 Setup

```bash
cd src

# Create & activate virtual environment
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 1.2 Configure environment variables

Create a file `src/.env` (auto-loaded by the API at startup):

```dotenv
# ── Azure OpenAI ─────────────────────────────────────────────────
AZURE_OPENAI_ENDPOINT=https://<your-ai-foundry>.openai.azure.com/
AZURE_OPENAI_KEY=<your-key>
AZURE_OPENAI_PRIMARY_DEPLOYMENT=gpt-4.1      # or gpt-5.2 if available
AZURE_OPENAI_FALLBACK_DEPLOYMENT=gpt-4.1

# ── Rules source ─────────────────────────────────────────────────
# Option A — Local JSON file (default, simplest for dev)
USE_COSMOS_RULES=false
RULES_JSON_PATH=pipeline_output/05_extracted_rules.json

# Option B — Cosmos DB (production-like)
# USE_COSMOS_RULES=true
# AZURE_COSMOS_ENDPOINT=https://<your-cosmos>.documents.azure.com:443/
# AZURE_COSMOS_DATABASE=appdata
# AZURE_COSMOS_RULES_CONTAINER=policy-rules
# AZURE_CLIENT_ID=                  # managed identity client ID (leave empty for DefaultAzureCredential)
```

> **Local dev shortcut**: Keep `USE_COSMOS_RULES=false`. The API will read rules from `pipeline_output/05_extracted_rules.json` (pre-generated or from a pipeline run). No Cosmos DB needed.

### 1.3 Run the API

```bash
cd src

# Default: http://localhost:8000
python run_api.py

# Custom port + auto-reload on code changes
python run_api.py --port 8000 --reload
```

Verify it works:

```bash
curl http://localhost:8000/api/health
# → {"status":"healthy","rules_loaded":<count>}

curl http://localhost:8000/api/rules/summary
```

### 1.4 API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health probe / readiness check |
| `GET` | `/api/rules/summary` | Rule count and statistics |
| `POST` | `/api/check` | Full document check (multiple paragraphs) |
| `POST` | `/api/check-paragraph` | Single-paragraph check (low-latency, real-time) |

---

## Part 2 — PDF Extraction Pipeline

Extracts rules from an OECD PDF, creates searchable chunks, and indexes them.

### 2.1 Setup

Same virtual environment as the API:

```bash
cd src
pip install -r requirements.txt  # already done if you set up the API
```

### 2.2 Configure environment variables

The pipeline reads from `src/.env` (same file as the API) but needs additional variables:

```dotenv
# ── Azure Document Intelligence / Content Understanding ──────────
AZURE_CONTENT_UNDERSTANDING_ENDPOINT=https://<your-ai-foundry>.services.ai.azure.com
# Alternatively, falls back to AZURE_OPENAI_ENDPOINT

# ── Azure AI Search (for indexing) ───────────────────────────────
AZURE_AISEARCH_ENDPOINT=https://<your-search>.search.windows.net
AZURE_AISEARCH_KEY=<your-search-admin-key>
AZURE_SEARCH_INDEX_NAME=policy-chunks            # default

# ── Azure Cosmos DB (for storing structured rules) ───────────────
AZURE_COSMOS_ENDPOINT=https://<your-cosmos>.documents.azure.com:443/
AZURE_COSMOS_KEY=<your-cosmos-key>               # or use managed identity below
AZURE_COSMOS_DATABASE=appdata
AZURE_COSMOS_RULES_CONTAINER=policy-rules

# ── Azure Storage (for PDF source blobs) ─────────────────────────
AZURE_STORAGE_CONNECTION_STRING=<your-storage-connection-string>
AZURE_STORAGE_CONTAINER=documents

# ── Optional: Managed Identity instead of keys ───────────────────
# USE_MANAGED_IDENTITY=true

# ── Optional: LLM-based rule extraction (more expensive) ─────────
# USE_LLM_EXTRACTION=true
```

### 2.3 Run the pipeline

```bash
cd src

# Full run
python run_pipeline.py path/to/oecd_document.pdf --verbose

# Common options
python run_pipeline.py document.pdf --output-dir pipeline_output --verbose
python run_pipeline.py document.pdf --skip-extraction     # reuse cached PDF extraction
python run_pipeline.py document.pdf --skip-indexing        # local-only, no Azure calls for indexing
python run_pipeline.py document.pdf --skip-rules           # chunking only, no rule extraction
python run_pipeline.py document.pdf --max-chunk-tokens 600 # smaller chunks
```

All options:

| Flag | Description |
|------|-------------|
| `--output-dir DIR` | Output folder (default: `pipeline_output`) |
| `--max-chunk-tokens N` | Max tokens per chunk (default: 800) |
| `--skip-extraction` | Use cached extraction (don't re-call Document Intelligence) |
| `--skip-rules` | Skip rule extraction (chunking only) |
| `--skip-indexing` | Skip Azure indexing (local processing only) |
| `--no-intermediate` | Don't save intermediate JSON files |
| `--verbose` / `-v` | Detailed logging |

### 2.4 Intermediate files

After a run, `pipeline_output/` contains:

| File | Description |
|------|-------------|
| `01_raw_layout.json` | Raw Document Intelligence extraction |
| `02_structured_document.json` | Hierarchical document tree |
| `03_cleaned_document.json` | Cleaned and normalised document |
| `04_chunks.json` | Semantic chunks |
| `05_extracted_rules.json` | Extracted policy rules (used by the API) |
| `pipeline_summary.txt` | Execution summary |

### 2.5 Step-by-step (Python API for debugging)

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

raw = extract_pdf("mon_document.pdf", azure_cfg, pipeline_cfg)
doc = reconstruct_hierarchy(raw, pipeline_cfg)
doc = clean_document(doc, pipeline_cfg)
chunks = chunk_document(doc, pipeline_cfg)
rules = extract_rules(chunks, azure_cfg, pipeline_cfg)
index_chunks(chunks, rules, azure_cfg, pipeline_cfg)
store_rules_in_cosmos(rules, azure_cfg)
```

---

## Part 3 — Word Web Add-in

A TypeScript + Office.js task pane that sends paragraphs to the Quality Checker API for real-time feedback.

### 3.1 Setup

```bash
cd word-addin

# Install dependencies
npm install

# Generate HTTPS dev certificates (required by Office add-ins)
npx office-addin-dev-certs install
```

> Office.js add-ins must be served over HTTPS, even locally. The `office-addin-dev-certs` package creates a trusted localhost certificate at `~/.office-addin-dev-certs/`.

### 3.2 Configure the API URL

Edit `word-addin/src/services/qualityService.ts`:

```typescript
const API_BASE = "http://localhost:8000";  // default — matches run_api.py
```

Change this if you run the API on a different port or against a deployed endpoint.

### 3.3 Start the dev server

```bash
cd word-addin
npm run dev        # opens https://localhost:3000 with HMR
# or
npm run start      # same, without auto-opening the browser
```

The webpack dev server starts on **https://localhost:3000** with:
- Hot Module Replacement (HMR)
- CORS headers for Office.js
- Self-signed HTTPS certificates

### 3.4 Sideload in Word

1. Open **Word Desktop** (Microsoft 365).
2. Go to **Insert** → **Add-ins** → **My Add-ins** → **Upload My Add-in**.
3. Select `word-addin/manifest.xml`.
4. The task pane should appear in the ribbon under **OECD Style Checker**.

> The manifest already points to `https://localhost:3000` — no changes needed for local dev.

### 3.5 Build for production

```bash
cd word-addin
npm run build      # outputs to word-addin/dist/
```

---

## Running all three parts together (local)

Open three terminals:

```
Terminal 1 — API
─────────────────────────
cd src
.venv\Scripts\Activate.ps1   # Windows
python run_api.py --reload

Terminal 2 — Word Add-in
─────────────────────────
cd word-addin
npm run dev

Terminal 3 — Pipeline (one-off)
─────────────────────────
cd src
.venv\Scripts\Activate.ps1
python run_pipeline.py path/to/oecd_rules.pdf --verbose
```

Then sideload the add-in in Word. The add-in calls `http://localhost:8000`, which the API serves.

### Quick-start without Azure (API only)

If you just want to test the API + add-in loop and already have a `05_extracted_rules.json` file:

1. Make sure `src/.env` has at least `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_KEY` set (the API needs OpenAI for evaluation).
2. Set `USE_COSMOS_RULES=false` (or omit it — this is the default).
3. Place rules in `src/pipeline_output/05_extracted_rules.json`.
4. Run the API: `python run_api.py --reload`.
5. Run the add-in: `cd word-addin && npm run dev`.
6. Sideload in Word and start editing.

---

## Docker (for container builds)

Three Dockerfiles are provided for Azure Container Apps deployment:

| Dockerfile | Image | Port | Description |
|------------|-------|------|-------------|
| `src/Dockerfile.api` | `quality-api` | 8080 | FastAPI server with uvicorn |
| `src/Dockerfile.pipeline` | `pdf-pipeline` | 8080 | Pipeline with poppler-utils |
| `word-addin/Dockerfile` | `word-addin` | 8080 | Static build served by nginx |

```bash
# Build locally (from repo root)
docker build -t quality-api -f src/Dockerfile.api src/
docker build -t pdf-pipeline -f src/Dockerfile.pipeline src/
docker build -t word-addin -f word-addin/Dockerfile word-addin/

# Run the API container locally
docker run -p 8080:8080 --env-file src/.env quality-api
```

---

## Deployment (Azure Container Apps)

Use the automated deployment script from the repo root:

```powershell
# 1. Copy and fill in environment values
cp .env.example .env

# 2. Deploy everything (infra + build + containers)
.\deploy-all.ps1

# Or deploy in stages
.\deploy-all.ps1 -InfraOnly                          # infrastructure only
.\deploy-all.ps1 -SkipInfra                           # re-deploy containers only
.\deploy-all.ps1 -ExistingResourceGroup "rg-my-team"  # use an existing RG
.\deploy-all.ps1 -SkipBuild                            # skip Docker (use existing images)
```

See `.env.example` for all configuration options.

---

## Environment variables reference

### API (`src/.env`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AZURE_OPENAI_ENDPOINT` | **yes** | — | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_KEY` | **yes** | — | Azure OpenAI API key |
| `AZURE_OPENAI_PRIMARY_DEPLOYMENT` | no | `gpt-5.2` | Primary model deployment name |
| `AZURE_OPENAI_FALLBACK_DEPLOYMENT` | no | `gpt-4.1` | Fallback model |
| `USE_COSMOS_RULES` | no | `false` | Set `true` to load rules from Cosmos DB |
| `AZURE_COSMOS_ENDPOINT` | if Cosmos | — | Cosmos DB endpoint |
| `AZURE_COSMOS_DATABASE` | no | `appdata` | Cosmos DB database name |
| `AZURE_COSMOS_RULES_CONTAINER` | no | `policy-rules` | Cosmos DB container name |
| `AZURE_CLIENT_ID` | no | — | Managed Identity client ID |
| `RULES_JSON_PATH` | no | `pipeline_output/05_extracted_rules.json` | Local rules file (when Cosmos disabled) |

### Pipeline (`src/.env`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AZURE_OPENAI_ENDPOINT` | **yes** | — | Azure OpenAI endpoint |
| `AZURE_OPENAI_KEY` | **yes** | — | Azure OpenAI key |
| `AZURE_CONTENT_UNDERSTANDING_ENDPOINT` | **yes** | — | Document Intelligence endpoint |
| `AZURE_AISEARCH_ENDPOINT` | for indexing | — | AI Search endpoint |
| `AZURE_AISEARCH_KEY` | for indexing | — | AI Search admin key |
| `AZURE_COSMOS_ENDPOINT` | for indexing | — | Cosmos DB endpoint |
| `AZURE_COSMOS_KEY` | for indexing | — | Cosmos DB key |
| `AZURE_STORAGE_CONNECTION_STRING` | for blobs | — | Storage connection string |
| `USE_LLM_EXTRACTION` | no | `false` | Use LLM for rule extraction (more expensive) |
| `USE_MANAGED_IDENTITY` | no | `false` | Use managed identity instead of keys |

---

## Estimated costs (150-page PDF)

| Service | Estimated cost |
|---------|---------------|
| Document Intelligence (layout) | ~$0.50–1.50 |
| OpenAI — Rule extraction (LLM mode) | ~$2–5 ($0 in deterministic mode) |
| OpenAI — Embeddings | ~$0.10 |
| AI Search (basic tier) | included in plan |
| Cosmos DB (serverless) | ~$0.01 |

## Why this pipeline avoids "bad chunks"

1. **Layout-aware**: Document Intelligence detects roles (heading, body, footer)
2. **Hierarchy-aligned**: Chunks follow section/subsection boundaries
3. **No mid-paragraph splits**: Chunking respects semantic units
4. **Smart cleaning**: PDF artifacts are removed before indexing
5. **Metadata preserved**: Every chunk keeps its section, pages, and IDs
