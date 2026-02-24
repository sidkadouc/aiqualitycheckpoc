<#
.SYNOPSIS
    Full end-to-end deployment of the OECD Quality Checker to Azure Container Apps.

.DESCRIPTION
    Deploys all four Bicep layers in order, builds and pushes three Docker
    images to ACR, then deploys the Container Apps layer with all secrets
    wired through Key Vault secret references via managed identity.

    Layers:
      1. Foundation   — RG, Managed Identity, Key Vault, Cosmos DB, AI Search,
                        AI Foundry (OpenAI), Storage, Monitoring, SQL
      2. Shared ACR   — Azure Container Registry (in its own RG)
      3. ACR Role     — AcrPull / AcrPush role assignment for the MI
      4. Docker build  — Build & push quality-api, pdf-pipeline, word-addin
      5. Container Apps — Container Apps Environment + 3 Container Apps

.PARAMETER EnvFile
    Path to the .env file (default: .env in the repo root).

.PARAMETER SkipInfra
    Skip foundation + ACR layers (useful when only re-deploying containers).

.PARAMETER InfraOnly
    Deploy only infrastructure (foundation + ACR + role assignment) — no Docker
    build and no Container Apps deployment. Useful for provisioning resources first.

.PARAMETER SkipBuild
    Skip Docker build & push (use existing images in ACR).

.PARAMETER ExistingResourceGroup
    Name of an existing resource group to deploy into. When set, the foundation
    layer re-uses this RG instead of creating a new one (rg-<envName>).

.PARAMETER ImageTag
    Docker image tag to use (default: git short SHA or 'latest').

.EXAMPLE
    .\deploy-all.ps1
    .\deploy-all.ps1 -InfraOnly
    .\deploy-all.ps1 -SkipInfra
    .\deploy-all.ps1 -ExistingResourceGroup "rg-myteam-dev"
    .\deploy-all.ps1 -ImageTag "v1.2.3"
#>

[CmdletBinding()]
param(
    [string]$EnvFile = ".env",
    [switch]$SkipInfra,
    [switch]$InfraOnly,
    [switch]$SkipBuild,
    [string]$ExistingResourceGroup = "",
    [string]$ImageTag = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = $PSScriptRoot

# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════

function Write-Step { param([string]$Msg) Write-Host "`n▶ $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "  ✔ $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "  ⚠ $Msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$Msg) Write-Host "  ✖ $Msg" -ForegroundColor Red }

function Load-EnvFile([string]$Path) {
    if (-not (Test-Path $Path)) {
        Write-Err "Env file not found: $Path"
        Write-Host "  Copy .env.example to .env and fill in required values." -ForegroundColor Gray
        exit 1
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $parts = $line -split "=", 2
            if ($parts.Count -eq 2) {
                [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
            }
        }
    }
}

function Require-Env([string]$Name) {
    $val = [System.Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($val)) {
        Write-Err "Required environment variable '$Name' is not set. Check your .env file."
        exit 1
    }
    return $val
}

function Get-Env([string]$Name, [string]$Default = "") {
    $val = [System.Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($val)) { return $Default }
    return $val
}

function Invoke-Az {
    param([Parameter(ValueFromRemainingArguments)]$Args)
    $result = & az @Args 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "az command failed: az $($Args -join ' ')"
        Write-Host ($result | Out-String) -ForegroundColor Red
        exit 1
    }
    return $result
}

function Get-DeploymentOutput([string]$Json, [string]$Key) {
    $obj = $Json | ConvertFrom-Json
    return $obj.properties.outputs.$Key.value
}

# ═════════════════════════════════════════════════════════════════════
# 0 — Prerequisites
# ═════════════════════════════════════════════════════════════════════
Write-Step "Checking prerequisites"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Err "Azure CLI (az) not found. Install from https://aka.ms/installazurecli"
    exit 1
}
Write-Ok "az CLI found"

if ($SkipInfra -and $InfraOnly) {
    Write-Err "-SkipInfra and -InfraOnly are mutually exclusive."
    exit 1
}

$needsDocker = (-not $SkipBuild) -and (-not $InfraOnly)
if ($needsDocker -and -not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "Docker not found. Install Docker Desktop or set -SkipBuild."
    exit 1
}
if ($needsDocker) { Write-Ok "Docker found" }

# ═════════════════════════════════════════════════════════════════════
# 1 — Load env & set defaults
# ═════════════════════════════════════════════════════════════════════
Write-Step "Loading environment from $EnvFile"
Load-EnvFile (Join-Path $ROOT $EnvFile)

$ENV_NAME       = Require-Env "AZURE_ENV_NAME"
$LOCATION       = Require-Env "AZURE_LOCATION"
$SUBSCRIPTION   = Require-Env "AZURE_SUBSCRIPTION_ID"
$ENV_TYPE       = Get-Env "AZURE_ENV_TYPE" "dev"
$APP_NAME       = Get-Env "APPLICATION_NAME" "oecd-quality"
$COSMOS_DB      = Get-Env "COSMOS_DB_DATABASE_NAME" "appdata"
$COSMOS_RULES   = Get-Env "AZURE_COSMOS_RULES_CONTAINER" "policy-rules"
$SQL_DB         = Get-Env "SQL_DATABASE_NAME" "AppDb"

# Existing resource group: param > env > empty (create new)
if ([string]::IsNullOrWhiteSpace($ExistingResourceGroup)) {
    $ExistingResourceGroup = Get-Env "EXISTING_RESOURCE_GROUP" ""
}
$USE_EXISTING_RG = -not [string]::IsNullOrWhiteSpace($ExistingResourceGroup)
if ($USE_EXISTING_RG) {
    $RG_NAME = $ExistingResourceGroup
} else {
    $RG_NAME = "rg-$ENV_NAME"
}

# Image tag: param > env > git sha > 'latest'
if ([string]::IsNullOrWhiteSpace($ImageTag)) {
    $ImageTag = Get-Env "IMAGE_TAG" ""
}
if ([string]::IsNullOrWhiteSpace($ImageTag)) {
    try { $ImageTag = (git -C $ROOT rev-parse --short HEAD 2>$null) } catch {}
}
if ([string]::IsNullOrWhiteSpace($ImageTag)) { $ImageTag = "latest" }

Write-Ok "ENV=$ENV_NAME  LOCATION=$LOCATION  TAG=$ImageTag  RG=$RG_NAME"
if ($USE_EXISTING_RG) { Write-Warn "Using existing resource group: $RG_NAME" }
if ($InfraOnly) { Write-Warn "Infrastructure-only mode (no Docker build / Container Apps)" }

# Set subscription
Invoke-Az account set --subscription $SUBSCRIPTION
Write-Ok "Subscription set to $SUBSCRIPTION"

# Validate existing resource group if specified
if ($USE_EXISTING_RG) {
    Write-Step "Verifying resource group '$RG_NAME' exists"
    $rgExists = az group exists --name $RG_NAME 2>$null
    if ($rgExists -ne "true") {
        Write-Err "Resource group '$RG_NAME' does not exist in subscription $SUBSCRIPTION."
        Write-Host "  Create it first or remove -ExistingResourceGroup to let the script create one." -ForegroundColor Gray
        exit 1
    }
    Write-Ok "Resource group '$RG_NAME' confirmed"
}

# Get current user info for SQL admin (if not specified)
$PRINCIPAL_ID    = Get-Env "AZURE_PRINCIPAL_ID" ""
$PRINCIPAL_LOGIN = Get-Env "AZURE_PRINCIPAL_LOGIN" ""
if ([string]::IsNullOrWhiteSpace($PRINCIPAL_ID)) {
    $acctJson = (Invoke-Az ad signed-in-user show -o json) | Out-String
    $acct = $acctJson | ConvertFrom-Json
    $PRINCIPAL_ID = $acct.id
    $PRINCIPAL_LOGIN = $acct.userPrincipalName
    Write-Ok "Auto-detected deployer: $PRINCIPAL_LOGIN ($PRINCIPAL_ID)"
}

# ═════════════════════════════════════════════════════════════════════
# 2 — Deploy Foundation Layer
# ═════════════════════════════════════════════════════════════════════
if (-not $SkipInfra) {
    Write-Step "Deploying Foundation layer (RG, MI, KV, Cosmos DB, AI Search, AI Foundry, Storage, Monitoring, SQL)"

    $foundationDeploy = Invoke-Az deployment sub create `
        --name "foundation-$ENV_NAME" `
        --location $LOCATION `
        --template-file "$ROOT/foundation/main.bicep" `
        --parameters "$ROOT/foundation/main.parameters.json" `
        --parameters environmentName=$ENV_NAME `
                     location=$LOCATION `
                     envType=$ENV_TYPE `
                     applicationName=$APP_NAME `
                     cosmosDbDatabaseName=$COSMOS_DB `
                     sqlDatabaseName=$SQL_DB `
                     currentUserPrincipalId=$PRINCIPAL_ID `
                     currentUserLogin=$PRINCIPAL_LOGIN `
                     azureOpenAIKey="$(Get-Env 'AZURE_OPENAI_KEY')" `
                     azureAISearchSecret="$(Get-Env 'AZURE_AISEARCH_SECRET')" `
                     apiKey="$(Get-Env 'API_KEY')" `
                     serviceApiKey="$(Get-Env 'SERVICE_API_KEY')" `
                     acsConnectionString="$(Get-Env 'ACS_CONNECTION_STRING')" `
                     jwtKey="$(Get-Env 'JWT_KEY')" `
                     emailClientId="$(Get-Env 'EMAIL_CLIENT_ID')" `
                     emailClientSecret="$(Get-Env 'EMAIL_CLIENT_SECRET')" `
                     resourceGroupName=$RG_NAME `
        --output json | Out-String

    Write-Ok "Foundation deployed"
} else {
    Write-Warn "Skipping Foundation (--SkipInfra). Reading existing outputs…"
    $foundationDeploy = Invoke-Az deployment sub show `
        --name "foundation-$ENV_NAME" `
        --output json | Out-String
}

# Extract foundation outputs
$MI_ID          = Get-DeploymentOutput $foundationDeploy "AZURE_MANAGED_IDENTITY_ID"
$MI_PRINCIPAL   = Get-DeploymentOutput $foundationDeploy "AZURE_MANAGED_IDENTITY_PRINCIPAL_ID"
$MI_CLIENT_ID   = Get-DeploymentOutput $foundationDeploy "AZURE_MANAGED_IDENTITY_CLIENT_ID"
$LA_CUSTOMER_ID = Get-DeploymentOutput $foundationDeploy "AZURE_LOG_ANALYTICS_CUSTOMER_ID"
$LA_SHARED_KEY  = Get-DeploymentOutput $foundationDeploy "AZURE_LOG_ANALYTICS_SHARED_KEY"
$APPINS_CONN    = Get-DeploymentOutput $foundationDeploy "AZURE_APPLICATION_INSIGHTS_CONNECTION_STRING"
$KV_NAME        = Get-DeploymentOutput $foundationDeploy "AZURE_KEY_VAULT_NAME"
$COSMOS_ENDPOINT= Get-DeploymentOutput $foundationDeploy "AZURE_COSMOS_ENDPOINT"

Write-Ok "Key Vault: $KV_NAME"
Write-Ok "Cosmos DB: $COSMOS_ENDPOINT"
Write-Ok "Managed Identity: $MI_CLIENT_ID"

# ═════════════════════════════════════════════════════════════════════
# 3 — Deploy Shared ACR
# ═════════════════════════════════════════════════════════════════════
if (-not $SkipInfra) {
    Write-Step "Deploying Shared ACR"

    $acrDeploy = Invoke-Az deployment sub create `
        --name "shared-acr-$ENV_NAME" `
        --location $LOCATION `
        --template-file "$ROOT/shared-acr/main.bicep" `
        --parameters "$ROOT/shared-acr/main.parameters.json" `
        --parameters environmentName=$ENV_NAME `
                     location=$LOCATION `
                     applicationName=$APP_NAME `
        --output json | Out-String

    Write-Ok "Shared ACR deployed"
} else {
    $acrDeploy = Invoke-Az deployment sub show `
        --name "shared-acr-$ENV_NAME" `
        --output json | Out-String
}

$ACR_RG       = Get-DeploymentOutput $acrDeploy "ACR_RESOURCE_GROUP_NAME"
$ACR_NAME     = Get-DeploymentOutput $acrDeploy "AZURE_CONTAINER_REGISTRY_NAME"
$ACR_ENDPOINT = Get-DeploymentOutput $acrDeploy "AZURE_CONTAINER_REGISTRY_ENDPOINT"

Write-Ok "ACR: $ACR_ENDPOINT"

# ═════════════════════════════════════════════════════════════════════
# 4 — ACR Role Assignment (MI → AcrPull/AcrPush)
# ═════════════════════════════════════════════════════════════════════
if (-not $SkipInfra) {
    Write-Step "Assigning ACR roles to Managed Identity"

    Invoke-Az deployment sub create `
        --name "acr-role-$ENV_NAME" `
        --location $LOCATION `
        --template-file "$ROOT/acr-role/main.bicep" `
        --parameters "$ROOT/acr-role/main.parameters.json" `
        --parameters environmentName=$ENV_NAME `
                     envType=$ENV_TYPE `
                     acrResourceGroupName=$ACR_RG `
                     containerRegistryName=$ACR_NAME `
                     managedIdentityPrincipalId=$MI_PRINCIPAL `
        --output json | Out-Null

    Write-Ok "ACR role assignment complete"
}

# ═════════════════════════════════════════════════════════════════════
# 5 — Docker Build & Push
# ═════════════════════════════════════════════════════════════════════
if (-not $SkipBuild -and -not $InfraOnly) {
    Write-Step "Building and pushing Docker images (tag: $ImageTag)"

    # Login to ACR
    Invoke-Az acr login --name $ACR_NAME
    Write-Ok "Logged in to ACR: $ACR_NAME"

    # -- Quality API --
    Write-Host "  Building quality-api…" -ForegroundColor Gray
    docker build `
        -t "${ACR_ENDPOINT}/quality-api:${ImageTag}" `
        -f "$ROOT/src/Dockerfile.api" `
        "$ROOT/src"
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker build failed for quality-api"; exit 1 }

    docker push "${ACR_ENDPOINT}/quality-api:${ImageTag}"
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker push failed for quality-api"; exit 1 }
    Write-Ok "quality-api:$ImageTag pushed"

    # -- PDF Pipeline --
    Write-Host "  Building pdf-pipeline…" -ForegroundColor Gray
    docker build `
        -t "${ACR_ENDPOINT}/pdf-pipeline:${ImageTag}" `
        -f "$ROOT/src/Dockerfile.pipeline" `
        "$ROOT/src"
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker build failed for pdf-pipeline"; exit 1 }

    docker push "${ACR_ENDPOINT}/pdf-pipeline:${ImageTag}"
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker push failed for pdf-pipeline"; exit 1 }
    Write-Ok "pdf-pipeline:$ImageTag pushed"

    # -- Word Add-in --
    Write-Host "  Building word-addin…" -ForegroundColor Gray
    docker build `
        -t "${ACR_ENDPOINT}/word-addin:${ImageTag}" `
        -f "$ROOT/word-addin/Dockerfile" `
        "$ROOT/word-addin"
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker build failed for word-addin"; exit 1 }

    docker push "${ACR_ENDPOINT}/word-addin:${ImageTag}"
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker push failed for word-addin"; exit 1 }
    Write-Ok "word-addin:$ImageTag pushed"
} elseif ($InfraOnly) {
    Write-Warn "Skipping Docker build (--InfraOnly)."
} else {
    Write-Warn "Skipping Docker build (--SkipBuild). Using existing images."
}

# ═════════════════════════════════════════════════════════════════════
# 6 — Deploy Container Apps
# ═════════════════════════════════════════════════════════════════════
if ($InfraOnly) {
    Write-Step "Infrastructure-only deployment complete. Skipping Container Apps."

    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  INFRASTRUCTURE DEPLOYMENT COMPLETE" -ForegroundColor Green
    Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Resource Group     : $RG_NAME" -ForegroundColor White
    Write-Host "  Key Vault          : $KV_NAME" -ForegroundColor White
    Write-Host "  Cosmos DB          : $COSMOS_ENDPOINT" -ForegroundColor White
    Write-Host "  Managed Identity   : $MI_CLIENT_ID" -ForegroundColor White
    Write-Host "  ACR                : $ACR_ENDPOINT" -ForegroundColor White
    Write-Host ""
    Write-Host "  To deploy containers later, run:" -ForegroundColor Yellow
    Write-Host "    .\deploy-all.ps1 -SkipInfra" -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

Write-Step "Deploying Container Apps (quality-api, pdf-pipeline, word-addin)"

$caDeploy = Invoke-Az deployment sub create `
    --name "container-apps-$ENV_NAME" `
    --location $LOCATION `
    --template-file "$ROOT/container-app/main.bicep" `
    --parameters "$ROOT/container-app/main.parameters.json" `
    --parameters environmentName=$ENV_NAME `
                 location=$LOCATION `
                 envType=$ENV_TYPE `
                 applicationName=$APP_NAME `
                 managedIdentityId=$MI_ID `
                 managedIdentityClientId=$MI_CLIENT_ID `
                 logAnalyticsCustomerId=$LA_CUSTOMER_ID `
                 logAnalyticsSharedKey=$LA_SHARED_KEY `
                 appInsightsConnectionString=$APPINS_CONN `
                 keyVaultName=$KV_NAME `
                 containerRegistryEndpoint=$ACR_ENDPOINT `
                 cosmosDbEndpoint=$COSMOS_ENDPOINT `
                 cosmosDbDatabaseName=$COSMOS_DB `
                 cosmosDbRulesContainerName=$COSMOS_RULES `
                 resourceGroupName=$RG_NAME `
    --output json | Out-String

Write-Ok "Container Apps deployed"

$QUALITY_API_URL = Get-DeploymentOutput $caDeploy "QUALITY_API_URL"
$ADDIN_URL       = Get-DeploymentOutput $caDeploy "WORD_ADDIN_URL"

# ═════════════════════════════════════════════════════════════════════
# 7 — Update Container App image tags (if not placeholder)
# ═════════════════════════════════════════════════════════════════════
Write-Step "Updating container app images to $ACR_ENDPOINT/*:$ImageTag"

Invoke-Az containerapp update `
    --name ca-quality-api `
    --resource-group $RG_NAME `
    --image "${ACR_ENDPOINT}/quality-api:${ImageTag}" `
    --output none

Invoke-Az containerapp update `
    --name ca-pdf-pipeline `
    --resource-group $RG_NAME `
    --image "${ACR_ENDPOINT}/pdf-pipeline:${ImageTag}" `
    --output none

Invoke-Az containerapp update `
    --name ca-word-addin `
    --resource-group $RG_NAME `
    --image "${ACR_ENDPOINT}/word-addin:${ImageTag}" `
    --output none

Write-Ok "All container apps updated to image tag: $ImageTag"

# ═════════════════════════════════════════════════════════════════════
# Done
# ═════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  Resource Group     : $RG_NAME" -ForegroundColor White
Write-Host "  Quality API        : $QUALITY_API_URL" -ForegroundColor White
Write-Host "  Word Add-in        : $ADDIN_URL" -ForegroundColor White
Write-Host "  Key Vault          : $KV_NAME" -ForegroundColor White
Write-Host "  Cosmos DB          : $COSMOS_ENDPOINT" -ForegroundColor White
Write-Host "  ACR                : $ACR_ENDPOINT" -ForegroundColor White
Write-Host "  Image Tag          : $ImageTag" -ForegroundColor White
Write-Host ""
Write-Host "  Auth: All services use managed identity ($MI_CLIENT_ID)." -ForegroundColor Gray
Write-Host "  Secrets: Stored in Key Vault and injected via secret refs." -ForegroundColor Gray
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "    1. Update word-addin manifest.xml URLs to: $ADDIN_URL" -ForegroundColor Yellow
Write-Host "    2. Run pdf-pipeline to extract rules into Cosmos DB" -ForegroundColor Yellow
Write-Host "    3. Quality API will load rules from Cosmos DB on startup" -ForegroundColor Yellow
Write-Host ""
