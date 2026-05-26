---
name: infra-deployment
description: 'Deploy or modify the AI Quality Checker Azure infrastructure with Bicep. Use for: provisioning the stack (AI Foundry, AI Search, Cosmos DB, Storage, Key Vault, Container Apps, VNet, private endpoints, ACR); adding a new container app or Azure service; wiring managed-identity RBAC (UAMI / SAMI); enabling or troubleshooting private endpoints and private DNS zones; changing the deployment region or Foundry model deployments; debugging "image pull failed", "DNS resolution", "AcrPull", "Cognitive Services 401/403", "Cosmos data-plane 403" errors during deployment.'
---

# Infra Deployment — AI Quality Checker

## When to use

Trigger phrases: *deploy infra*, *deploy bicep*, *provision Azure*, *add a container app*, *add a private endpoint*, *add an RBAC role*, *change region*, *swap GPT model*, *fix image pull*, *fix DNS for private endpoint*, *grant access to Foundry/Cosmos/Storage*.

## Stack at a glance

Single entry point: [main.bicep](../../../main.bicep) (`targetScope = 'resourceGroup'`). The RG is **pre-created** by the platform team — bicep deploys everything else into it.

```
main.bicep
└── modules/                 (22 reusable modules)
    ├── infra primitives:    managed-identity, monitoring, key-vault
    ├── networking:          networking, private-endpoint, private-endpoints
    ├── data + AI services:  storage, cosmos-db, ai-search, ai-foundry, container-registry
    ├── compute:             container-apps-environment, quality-api,
    │                        pdf-pipeline-job (ACA Job), word-addin
    └── RBAC helpers:        acr-role-assignment, ai-foundry-role-assignment,
                             ai-search-role-assignment, cosmos-role-assignment,
                             storage-role-assignment, key-vault-role-assignment
```

**CI/CD**: two Azure DevOps pipelines under [`.azure-pipelines/`](../../../.azure-pipelines/) (see [docs/devops-setup.md](../../../docs/devops-setup.md)):
- `infra-deploy.yml` — validate + deploy bicep on every push to `main` that touches bicep
- `apps-deploy.yml` — `az acr build` 3 images + redeploy with new `imageTag` on every push to `src/**`

## Identity model (memorize this)

Pure SAMI — every container app and ACA Job has its own system-assigned managed identity. The legacy UAMI (`id-${env}-001`) still gets created by the bicep but **nothing mounts it** (kept for backward compatibility with the foundation modules; ignore at runtime).

| Identity | Scope | Used for |
|---|---|---|
| **SAMI** (per container app or Job, created by ACA) | one per workload | ACR image pull (`identity: 'system'` in `registries[]`); Key Vault secret refs (`identity: 'system'` in `secrets[]`); runtime calls to Foundry / Storage / Cosmos / Search via `DefaultAzureCredential` |
| **Deploying user** (`currentUserPrincipalId`) | optional | Granted `Cognitive Services User` on Foundry for local debug, and `AcrPush` on ACR for manual `docker push` |

SAMI principal IDs are runtime values — to assign roles on them, always use the per-service `*-role-assignment.bicep` modules.

## pdf-pipeline is an ACA Job (not a Container App)

Resource type: `Microsoft.App/jobs@2024-03-01`, `triggerType: 'Manual'`. Trigger it on demand:

```powershell
az containerapp job start -g <rg> -n job-pdf-pipeline
az containerapp job execution list -g <rg> -n job-pdf-pipeline -o table
az containerapp job logs show -g <rg> -n job-pdf-pipeline --execution <name> --follow
```

The image runs [src/pdf_pipeline/job_entrypoint.py](../../../src/pdf_pipeline/job_entrypoint.py) which:
1. Downloads `$AZURE_STORAGE_CONTAINER/$INPUT_PDF_BLOB_NAME` to `/tmp/` via SAMI
2. Runs `python run_pipeline.py <local>`
3. Uploads everything in `pipeline_output/` to `<container>/<OUTPUT_BLOB_PREFIX>/<run-id>/`

## Foundry RBAC — pick the right role

On the new Foundry resource (`kind: AIServices`):

| Role | GUID | Grants | Use when |
|---|---|---|---|
| **Cognitive Services User** | `a97b65f3-24c7-4388-baec-2e87135dc908` | **All Foundry features** (OpenAI + Doc Intel + Vision + Content Safety + Speech) | **Default for apps that use Foundry** — this is what main.bicep assigns to the SAMIs |
| Cognitive Services OpenAI User | `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd` | Only `/openai/*` plane | When you specifically want least-privilege OpenAI-only |
| Azure AI User | `53ca6127-db72-4b80-b1b0-d745d6d5456d` | Foundry Project data plane (Agents) | When using Foundry Projects / Agent Service |

Per [Microsoft Foundry RBAC docs](https://learn.microsoft.com/azure/foundry/how-to/upgrade-azure-openai#considerations-for-rbac-and-policy-during-upgrade), `Cognitive Services User` on the new resource grants access to ALL Foundry features in one assignment — that's why we use it.

## Deploy procedure

### Prerequisites

- Pre-created resource group (provided by platform team)
- Azure CLI logged in (`az login`), correct subscription selected
- (Optional) `AZURE_PRINCIPAL_ID` exported = your user object ID, so you get Foundry data-plane access for local debug

### First deploy

```powershell
$env:AZURE_ENV_NAME       = "dev"
$env:AZURE_LOCATION       = "francecentral"
$env:AZURE_PRINCIPAL_ID   = (az ad signed-in-user show --query id -o tsv)

az deployment group create `
  -g <pre-created-rg-name> `
  -f main.bicep `
  -p main.parameters.json
```

The first deploy **will create container apps that fail to pull the image** (no image exists in ACR yet). This is expected — the next step is:

```powershell
# Build + push images
$ACR = az acr show -g <rg> -n <acr-name> --query loginServer -o tsv
az acr login -n $ACR
docker build -t $ACR/quality-api:$tag    -f src/Dockerfile.api      src ; docker push $ACR/quality-api:$tag
docker build -t $ACR/pdf-pipeline:$tag   -f src/Dockerfile.pipeline src ; docker push $ACR/pdf-pipeline:$tag
docker build -t $ACR/word-addin:$tag     -f word-addin/Dockerfile word-addin ; docker push $ACR/word-addin:$tag

# Re-deploy bicep with the new tag to trigger ACA revision
az deployment group create -g <rg> -f main.bicep -p main.parameters.json `
  -p imageTag=$tag
```

### Incremental deploys

After the first time, just `az deployment group create` with the new `imageTag` — bicep is idempotent and only updates what changed.

## Common operations

### Add a new container app

1. Copy [modules/quality-api.bicep](../../../modules/quality-api.bicep) → `modules/<new-app>.bicep` and adjust env vars + image name.
2. Make sure the new module:
   - Has `identity: { type: 'SystemAssigned, UserAssigned' ... }`
   - Sets `registries[0].identity = 'system'`
   - Outputs `principalId string = <res>.identity.principalId`
3. In [main.bicep](../../../main.bicep), add a `module` call.
4. In the **SAMI role assignments** section, add the new app's `principalId` to the relevant role-assignment modules' `principalIds` array.

### Add a new Azure service with a private endpoint

1. Create the service module under `modules/` (use the existing storage/cosmos pattern). Set `publicNetworkAccess: 'Disabled'`.
2. Add the relevant private DNS zone name to `privateDnsZoneNames` in [modules/networking.bicep](../../../modules/networking.bicep). Add a matching key to the `privateDnsZoneIds` output.
3. Add a new PE block in [modules/private-endpoints.bicep](../../../modules/private-endpoints.bicep) (target the new service, pass the zone IDs).
4. Wire the new module + PE in main.bicep.
5. Create a `modules/<service>-role-assignment.bicep` if SAMIs need data-plane access.

### Switch GPT model deployments

Edit [modules/ai-foundry.bicep](../../../modules/ai-foundry.bicep) — the model `deployments` block. Validate availability with the region tables in [Microsoft Foundry models region availability](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability).

Region pattern: `DataZoneStandard` SKU gives EU data residency for `gpt-4.1`, `gpt-5`, `gpt-5.4`. `Standard` (single-region) supports fewer models — `gpt-5.4` is **not** available in regional standard in France Central; use `DataZoneStandard` or `GlobalStandard`.

### Lock down public access (already on by default in main.bicep)

All services in main.bicep have `publicNetworkAccess: 'Disabled'`. To temporarily allow public access for debugging:

```powershell
az resource update -g <rg> --resource-type Microsoft.CognitiveServices/accounts `
  -n aoai-<env> --set properties.publicNetworkAccess=Enabled
```

Don't commit the override.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| ACA stuck on "Provisioning" → "Failed: image pull failed" | First deploy, ACR empty | Build + push images, redeploy with new `imageTag` |
| ACA `403 AcrPull` after image exists | SAMI AcrPull role not propagated yet | Wait ~60s, restart revision: `az containerapp revision restart -g <rg> -n <app> --revision <rev>` |
| Foundry call returns `401 PermissionDenied` from inside ACA | SAMI missing `Cognitive Services User` | Re-deploy bicep — the `sami-foundry-user` module assigns it |
| Cosmos call returns `403 Forbidden` from inside ACA | SAMI missing Cosmos built-in Data Contributor (`00000000-0000-0000-0000-000000000002`) | Re-deploy bicep — `sami-cosmos` module assigns it |
| App resolves Foundry endpoint to a **public IP** instead of 10.x | Private DNS zone not linked to the VNet OR custom DNS not forwarding to 168.63.129.16 | Check `privateDnsZones/virtualNetworkLinks` in [modules/networking.bicep](../../../modules/networking.bicep); test from ACA: `nslookup <foundry>.openai.azure.com` |
| Bicep error: *"expression … requires a value that can be calculated at the start of the deployment"* on a role assignment | Tried to inline a role assignment in main.bicep with a runtime principal ID | Wrap the assignment in a per-service `*-role-assignment.bicep` module (pass `principalIds string[]` + role GUID) |
| `Storage account name … may be too short` lint warning | False positive; `@minLength(3)` on `environmentName` already guards it | Ignore — it's a warning, not an error |

## Layout reference

| Module | Purpose | Key params |
|---|---|---|
| `managed-identity.bicep` | Shared UAMI | `environmentName` |
| `monitoring.bicep` | Log Analytics + App Insights | — |
| `key-vault.bicep` | KV with RBAC auth + soft-delete | `publicNetworkAccess` |
| `networking.bicep` | VNet + 2 subnets + 9 private DNS zones | `vnetAddressPrefix`, `acaInfraSubnetPrefix`, `privateEndpointSubnetPrefix` |
| `private-endpoint.bicep` | Reusable single PE + DNS zone group | `targetResourceId`, `groupId`, `privateDnsZoneIds[]` |
| `private-endpoints.bicep` | Stamps 7 PEs (Foundry/Search/Cosmos/KV/Storage-blob/Storage-queue/ACR) | injected DNS + subnet IDs |
| `storage.bicep` | StorageV2 + `documents` blob container | `publicNetworkAccess` |
| `cosmos-db.bicep` | Serverless SQL API + custom data role | `publicNetworkAccess` |
| `ai-search.bicep` | Basic SKU + SAMI + SearchIndex roles | `publicNetworkAccess` |
| `ai-foundry.bicep` | AIServices kind + 3 model deployments + UAMI Foundry roles | `aiFoundryName`, `location` (defaults to swedencentral), `publicNetworkAccess` |
| `container-registry.bicep` | Premium ACR | `sku`, `publicNetworkAccess` |
| `container-apps-environment.bicep` | ACA env, VNet-integrated via `existingInfrastructureSubnetId` | infra subnet ID |
| `quality-api.bicep` / `pdf-pipeline.bicep` / `word-addin.bicep` | The 3 container apps; SAMI + UAMI dual identity; SAMI pulls from ACR | image tag, KV name, MI IDs |
| `<service>-role-assignment.bicep` (5 of them) | Per-service RBAC by-name with `principalIds string[]` | service name, role GUID |

## Anti-patterns

- ❌ **Inlining role assignments in main.bicep with runtime principal IDs** — Bicep can't compute `guid()` at deploy start. Always go through a `*-role-assignment.bicep` module.
- ❌ **Assigning `Cognitive Services OpenAI User` and expecting Document Intelligence to work** — it won't. Use `Cognitive Services User` to cover both.
- ❌ **Putting PEs in the ACA infra subnet** — that subnet is delegated to `Microsoft.App/environments` and can't host other resources. Always use the separate `snet-pe`.
- ❌ **Using a regional endpoint URL** (`https://<region>.api.cognitive.microsoft.com/`) in app config when public access is disabled — bypasses private DNS. Always use the custom-subdomain form: `https://<resource-name>.openai.azure.com/` and `https://<resource-name>.cognitiveservices.azure.com/`.
- ❌ **`/28` for the ACA infra subnet** — minimum is `/27` (workload-profile env); `/23` recommended.
- ❌ **Removing or downgrading a model deployment in `ai-foundry.bicep` to "fix" a deploy failure** — capacity or model-availability issues are an Azure-side concern; check the region/SKU first. Never remove the model without explicit user approval.
