# Deployment

> **Tip**: for any deployment task, you can just tell GitHub Copilot what you want (*"add a private endpoint for X"*, *"swap the GPT model"*, *"fix this AcrPull error"*) and the [infra-deployment skill](../.github/skills/infra-deployment/SKILL.md) will load automatically.

## Prerequisites

| Tool | Version |
|---|---|
| Azure CLI | latest |
| Bicep CLI | latest (installed automatically by `az`) |
| Docker | latest (for image build + push) |
| A pre-created resource group | provided by you or the platform team |

You must also be `Owner` (or `Contributor` + `User Access Administrator`) on the RG, because the bicep creates role assignments.

## Architecture summary

`main.bicep` is the single entry point. It deploys **everything** into your pre-created RG: VNet, private DNS zones, 7 private endpoints, all data + AI services with public access disabled, ACR (Premium), the Container Apps Environment (VNet-integrated) and 3 container apps with system-assigned managed identities.

See [architecture.md](architecture.md) for the full diagram.

## First deploy

### 1. Set env vars

```powershell
$env:AZURE_ENV_NAME       = "dev"
$env:AZURE_LOCATION       = "francecentral"
$env:AZURE_PRINCIPAL_ID   = (az ad signed-in-user show --query id -o tsv)  # optional: gives you Foundry data-plane access
```

### 2. Create the RG (if it doesn't exist)

```powershell
az group create -n rg-aiquality-dev -l francecentral
```

### 3. Deploy bicep

```powershell
az deployment group create `
  -g rg-aiquality-dev `
  -f main.bicep `
  -p main.parameters.json
```

This takes 8–12 minutes. The container apps will provision but **fail to pull their image** on first attempt — that's expected because ACR is empty.

### 4. Build + push the 3 images

```powershell
$ACR = az acr show -g rg-aiquality-dev -n (az deployment group show -g rg-aiquality-dev -n main --query properties.outputs.AZURE_CONTAINER_REGISTRY_NAME.value -o tsv) --query loginServer -o tsv
az acr login -n $ACR.Split('.')[0]

$tag = (git rev-parse --short HEAD)

docker build -t "${ACR}/quality-api:${tag}"   -f src/api/Dockerfile         src             ; docker push "${ACR}/quality-api:${tag}"
docker build -t "${ACR}/pdf-pipeline:${tag}"  -f src/pdf_pipeline/Dockerfile src             ; docker push "${ACR}/pdf-pipeline:${tag}"
docker build -t "${ACR}/word-addin:${tag}"    -f src/word-addin/Dockerfile  src/word-addin  ; docker push "${ACR}/word-addin:${tag}"
```

### 5. Re-deploy with the new tag

```powershell
az deployment group create -g rg-aiquality-dev -f main.bicep -p main.parameters.json -p imageTag=$tag
```

Container Apps now pick up the image — the revisions restart and become healthy.

## Incremental deploys

After the first time, any change (bicep edit OR new image tag) is one command:

```powershell
az deployment group create -g rg-aiquality-dev -f main.bicep -p main.parameters.json -p imageTag=$tag
```

Bicep is idempotent — only changed resources are touched.

## What-if before applying

```powershell
az deployment group what-if -g rg-aiquality-dev -f main.bicep -p main.parameters.json
```

## Tearing it down

```powershell
az group delete -n rg-aiquality-dev --yes
```

If you used Key Vault, the soft-delete keeps the name reserved for 90 days. Purge it with:

```powershell
az keyvault purge -n kv-dev
```

## Troubleshooting cheatsheet

See the [infra-deployment skill](../.github/skills/infra-deployment/SKILL.md#troubleshooting) for the full troubleshooting table (image pull failed, AcrPull lag, Cognitive Services 401/403, DNS resolution, deploy-time Bicep errors).
