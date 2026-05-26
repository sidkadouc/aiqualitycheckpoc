# OECD AI Quality Checker — Word Add-in PoC

A Word add-in that checks paragraphs against an editorial rulebook extracted from an OECD policy PDF, powered by Azure AI Foundry, AI Search and Cosmos DB.

## What this is

Three workloads, deployed inside a private VNet:

| Piece | Path | What it does | Runs as |
|---|---|---|---|
| **PDF extraction pipeline** | [src/pdf_pipeline/](src/pdf_pipeline/) | Reads a policy PDF from blob → Document Intelligence (layout) → cleans/chunks → extracts rules → indexes chunks in AI Search + rules in Cosmos DB + uploads artifacts back to blob | **ACA Job** (`Microsoft.App/jobs`), trigger=Manual |
| **Quality Checker API** | [src/api/](src/api/) + [src/quality_agent/](src/quality_agent/) | FastAPI backend the add-in calls. Hybrid-search retrieves relevant rules per paragraph and an LLM judges conformity | ACA Container App (external ingress) |
| **Word Add-in** | [src/word-addin/](src/word-addin/) | Office Web add-in (TypeScript) that calls the API and highlights non-conformities | ACA Container App (external ingress) |

## Quick links

| I want to… | Go here |
|---|---|
| Understand the architecture and data flow | [docs/architecture.md](docs/architecture.md) |
| Deploy the Azure infrastructure | [docs/deployment.md](docs/deployment.md) — or say *"deploy the infra"* to GitHub Copilot (triggers the [infra-deployment skill](.github/skills/infra-deployment/SKILL.md)) |
| Set up Azure DevOps pipelines | [docs/devops-setup.md](docs/devops-setup.md) |
| Run the pipeline / API locally | [src/README.md](src/README.md) |
| Develop the Word add-in locally | [src/word-addin/README.md](src/word-addin/README.md) |
| See what's next on the roadmap | [docs/next-steps.md](docs/next-steps.md) |

## How the PDF pipeline runs

1. Upload a PDF to blob: `stdev<token>/documents/input.pdf`
2. Trigger the job: `az containerapp job start -g rg-aiquality-dev -n job-pdf-pipeline`
3. The job's [entrypoint](src/pdf_pipeline/job_entrypoint.py) downloads the blob via **SAMI**, runs the pipeline, then uploads `pipeline_output/*` back to `documents/pipeline-output/<run-id>/`
4. Rules land in **Cosmos DB** (`appdata.policy-rules`), chunk vectors land in **AI Search** (`policy-chunks`)

Override the input blob name per-run via the ACA Portal **Run with overrides** dialog or by re-deploying with a different `inputPdfBlobName` parameter.

## Identity model — pure SAMI

Every container app/job uses a **system-assigned managed identity** (no shared user-assigned identity). Roles assigned by bicep on each SAMI:

| App | ACR | KV | Foundry | Storage | Cosmos | Search |
|---|---|---|---|---|---|---|
| `ca-quality-api` | AcrPull | Secrets User | Cognitive Services User | Blob Data Contributor | Built-in Data Contributor | Index Data Reader |
| `job-pdf-pipeline` | AcrPull | — *(no KV refs)* | Cognitive Services User | Blob + Queue Data Contributor | Built-in Data Contributor | Index Data Contributor + Service Contributor |
| `ca-word-addin` | AcrPull | — | — | — | — | — |

`Cognitive Services User` on the new Foundry resource (`kind: AIServices`) covers **all** Foundry features (Azure OpenAI plane + Document Intelligence + Vision + Content Safety + Speech) in one assignment.

## Deploying — three options

### Option A — Azure DevOps pipelines (recommended)

Two YAML pipelines under [`.azure-pipelines/`](.azure-pipelines/):

| File | Purpose | Triggered by |
|---|---|---|
| [infra-deploy.yml](.azure-pipelines/infra-deploy.yml) | Validate + deploy `main.bicep` | push to `main` touching bicep |
| [apps-deploy.yml](.azure-pipelines/apps-deploy.yml) | `az acr build` 3 images → re-deploy bicep with new `imageTag` → smoke-test the pdf-pipeline job | push to `main` touching `src/**` |

`az acr build` runs inside Azure ACR Tasks, so it works even though the ACR has `publicNetworkAccess: Disabled`.

One-time setup (service connection, variable group, environment, RG creation): see [docs/devops-setup.md](docs/devops-setup.md).

### Option B — One-command deploy from your laptop

```powershell
$env:AZURE_ENV_NAME      = "dev"
$env:AZURE_LOCATION      = "francecentral"
$env:AZURE_PRINCIPAL_ID  = (az ad signed-in-user show --query id -o tsv)

az group create -n rg-aiquality-dev -l francecentral
az deployment group create -g rg-aiquality-dev -f main.bicep -p main.parameters.json
```

Full procedure (including image build + push, since first deploy creates empty ACAs that fail to pull): [docs/deployment.md](docs/deployment.md).

### Option C — `deploy-all.ps1`

Legacy wrapper that does infra + docker build + push + deploy in one PowerShell run. Still works; the pipelines above are the preferred path.

## Stack at a glance

- **Compute**: Azure Container Apps (workload-profile env, VNet-integrated) — 2 apps + 1 Job
- **AI**: Azure AI Foundry (`kind: AIServices`) in Sweden Central — `gpt-4.1`, `gpt-5.4`, `text-embedding-3-large` all on `DataZoneStandard`
- **Storage**: Cosmos DB (rules) + Storage Account (PDFs + pipeline artifacts) + Azure AI Search (vector chunks)
- **Networking**: VNet with 2 subnets (ACA infra + private endpoints), 9 private DNS zones, all services `publicNetworkAccess: Disabled`
- **Identity**: Per-app system-assigned managed identity (SAMI), no shared UAMI

Everything lives in a single [main.bicep](main.bicep) that orchestrates ~20 small modules in [modules/](modules/).
