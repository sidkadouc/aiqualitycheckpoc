# Architecture

End-to-end view of how a PDF policy becomes a Word add-in that flags non-conformities in real time.

## Data flow

```
                                                       ┌─────────────────────────┐
PDF (OECD ~150 pages)                                  │  Word document          │
        │                                              │  (user edits live)      │
        ▼                                              └────────────┬────────────┘
┌───────────────────────────────┐                                   │ paragraph
│ 1. Document Intelligence      │                                   ▼
│    prebuilt-layout            │                       ┌─────────────────────────┐
└──────────────┬────────────────┘                       │ Word Add-in (TypeScript)│
               │                                        │ src/word-addin/         │
               ▼                                        └────────────┬────────────┘
┌───────────────────────────────┐                                    │  HTTPS
│ 2. Reconstruct hierarchy      │                                    ▼
│    reconstruct.py             │                       ┌─────────────────────────┐
└──────────────┬────────────────┘                       │ Quality API (FastAPI)   │
               │                                        │ src/api + quality_agent │
               ▼                                        └─────┬─────────────┬─────┘
┌───────────────────────────────┐                             │             │
│ 3. Clean & normalize          │                             │ hybrid      │ rules
│    clean.py                   │                             │ search      │ lookup
└──────────────┬────────────────┘                             ▼             ▼
               │                                  ┌──────────────┐  ┌──────────────┐
               ▼                                  │ AI Search    │  │ Cosmos DB    │
┌───────────────────────────────┐                 │ policy-chunks│  │ policy-rules │
│ 4. Semantic chunking          │                 └──────▲───────┘  └──────▲───────┘
│    chunk.py — ≤800 tokens     │                        │                 │
└──────────────┬────────────────┘                        │ writes          │ writes
               │                                         │                 │
               ▼                                  ┌──────┴──────────────────┴──────┐
┌───────────────────────────────┐                 │ PDF pipeline (one-shot)        │
│ 5. Extract rules              │                 │ src/pdf_pipeline               │
│    extract_rules.py           │─────────────────┤ runs as a Container App job    │
│    determinist OR LLM         │                 └────────────────────────────────┘
└───────────────────────────────┘
```

## Azure topology

```
                  ┌────────────────────────────────────────────────────────────┐
                  │ rg-aiquality-dev (France Central)                          │
                  │                                                            │
                  │   VNet 10.10.0.0/16                                        │
                  │   ┌────────────────────────┐  ┌───────────────────────┐   │
                  │   │ snet-aca-infra /23     │  │ snet-pe /27           │   │
                  │   │ (delegated to ACA)     │  │                       │   │
                  │   │                        │  │  ┌─ PE → AI Foundry   │   │
                  │   │  ┌─ quality-api        │  │  ├─ PE → AI Search    │   │
                  │   │  ├─ pdf-pipeline       │  │  ├─ PE → Cosmos DB    │   │
                  │   │  └─ word-addin         │  │  ├─ PE → Key Vault    │   │
                  │   │     (3 ACAs, SAMI+UAMI)│  │  ├─ PE → Storage blob │   │
                  │   └────────────────────────┘  │  ├─ PE → Storage queue│   │
                  │                                │  └─ PE → ACR          │   │
                  │   9 private DNS zones linked   └───────────────────────┘   │
                  │                                                            │
                  │   AI Foundry → Sweden Central (cross-region PE)            │
                  └────────────────────────────────────────────────────────────┘
```

## Identity model

| Identity | Created on | Used for |
|---|---|---|
| **User-Assigned MI** (`id-${env}-001`) | up-front | Key Vault secret references in container apps; AcrPush so devs can push images |
| **System-Assigned MI** | per container app | ACR image pull; runtime calls to Foundry / Storage / Cosmos / Search via `DefaultAzureCredential` |

The dual identity avoids the deployment chicken-and-egg: the UAMI is granted Key Vault access **before** ACAs exist, so `secretRef` works on first deploy. SAMIs handle everything else at runtime.

## Per-app RBAC

| App | ACR | Foundry | Storage | Cosmos | Search |
|---|---|---|---|---|---|
| `quality-api` | AcrPull | **Cognitive Services User** (covers OpenAI + Doc Intel) | Blob Data Contributor | Built-in Data Contributor | Index Data Reader |
| `pdf-pipeline` | AcrPull | **Cognitive Services User** | Blob + **Queue** Data Contributor | Built-in Data Contributor | Index Data Contributor |
| `word-addin` | AcrPull | — | — | — | — |

`Cognitive Services User` is the right role on the **new** Foundry resource (`kind: AIServices`) — per [Foundry RBAC docs](https://learn.microsoft.com/azure/foundry/how-to/upgrade-azure-openai#considerations-for-rbac-and-policy-during-upgrade) it grants ALL Foundry features in one assignment.

## Repository layout

```
aiqualitycheckpoc/
├── main.bicep                       single-entry-point IaC
├── main.parameters.json
├── modules/                         20 reusable bicep modules
│   ├── core: managed-identity, monitoring, key-vault
│   ├── net:  networking, private-endpoint, private-endpoints
│   ├── data: storage, cosmos-db, ai-search, ai-foundry, container-registry
│   ├── app:  container-apps-environment, quality-api, pdf-pipeline, word-addin
│   └── rbac: acr/ai-foundry/ai-search/cosmos/storage role-assignment.bicep
├── src/
│   ├── api/                         FastAPI entry
│   ├── pdf_pipeline/                extraction + chunking + indexing
│   ├── quality_agent/               agent-framework workflow (rule batching, LLM eval)
│   ├── tests/
│   ├── word-addin/                  Office add-in (TypeScript)
│   ├── api/Dockerfile               quality-api image
│   ├── pdf_pipeline/Dockerfile      pdf-pipeline image (used by the ACA Job)
│   ├── pdf_pipeline/job_entrypoint.py  blob-aware wrapper for the ACA Job
│   ├── requirements.txt
│   └── run_api.py / run_pipeline.py
├── docs/                            this folder
├── .github/
│   ├── copilot-instructions.md      always-on guidance for Copilot
│   └── skills/infra-deployment/     on-demand deployment skill
└── deploy-all.ps1                   PowerShell wrapper around az + docker
```
