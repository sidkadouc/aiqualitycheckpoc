# AI Quality Checker — Project Guidelines

PoC with three parts:
- **PDF extraction pipeline** (Python, `src/pdf_pipeline/`) — runs Azure Document Intelligence + chunking + indexing into AI Search and Cosmos DB
- **Quality Checker API** (FastAPI, `src/api/` + `src/quality_agent/`) — backend the Word add-in calls
- **Word Add-in** (TypeScript, `src/word-addin/`) — Office add-in served from a Container App

## Architecture

- All Azure services are reached via **managed identity** in production. Two identities per Container App:
  - **User-Assigned MI** (single, shared) — used for Key Vault secret references (avoids deployment chicken-and-egg)
  - **System-Assigned MI** (per app) — used for ACR pull and all runtime data-plane calls (AI Foundry, Storage, Cosmos, AI Search)
- One **AI Foundry** account (`kind: AIServices`) exposes Azure OpenAI **and** Document Intelligence on the same endpoint. Use `Cognitive Services User` role — it grants access to all Foundry features in one assignment.
- Default region: France Central. AI Foundry deploys to Sweden Central by default for widest GPT-5.x availability.

## Code Style

- **Python**: 3.11+, type hints required, `pydantic` for data models. Match the style in [src/pdf_pipeline/models.py](src/pdf_pipeline/models.py) and [src/quality_agent/models.py](src/quality_agent/models.py).
- **Bicep**: Latest stable API versions, `@description` on every param, `@allowed`/`@minLength` where applicable. Use `existing` references for cross-module wiring; use small modules for repeated patterns (e.g. role assignments).
- **TypeScript** (add-in): see [src/word-addin/tsconfig.json](src/word-addin/tsconfig.json).

## Build and Test

```powershell
# Python API + pipeline
cd src
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_api.py            # FastAPI on :8080
python run_pipeline.py       # one-shot PDF → rules → index

# Word add-in
cd src/word-addin
npm install
npm run dev-server           # https://localhost:3000

# Bicep
az bicep build --file main.bicep
```

## Conventions

- Never commit secrets — all keys/endpoints live in Key Vault, referenced from Container Apps via `secretRef`.
- Quality-agent must NEVER call OpenAI directly with API keys when running in Azure — always go through `DefaultAzureCredential` + the SAMI.
- When adding a new Azure service to the stack, wire it into [main.bicep](main.bicep) AND add the matching SAMI role assignment via the `modules/*-role-assignment.bicep` helpers.

## Infra deployment

For anything Azure / Bicep / deployment-related (provisioning, adding services, RBAC, private endpoints, region changes, troubleshooting deploy failures), use the **infra-deployment** skill in `.github/skills/infra-deployment/`.
