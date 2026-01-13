# Deploy Logic App Workflows
# This script deploys the Logic App workflow artifacts using zip deploy
# Uses System-Assigned Managed Identity for authentication

param(
    [Parameter(Mandatory=$true)]
    [string]$ResourceGroupName,
    
    [Parameter(Mandatory=$true)]
    [string]$LogicAppName,
    
    [Parameter(Mandatory=$false)]
    [string]$SubscriptionId,
    
    # Workflow Configuration Parameters
    [Parameter(Mandatory=$false)]
    [string]$BackendApiUrl,
    
    [Parameter(Mandatory=$false)]
    [string]$AiSearchEndpoint,
    
    [Parameter(Mandatory=$false)]
    [string]$AiSearchIndexerName,
    
    [Parameter(Mandatory=$false)]
    [string]$SharedMailboxAddress,
    
    [Parameter(Mandatory=$false)]
    [string]$BlobContainerPath = '/inputdata',
    
    # Key Vault for secrets
    [Parameter(Mandatory=$false)]
    [string]$KeyVaultName,
    
    [Parameter(Mandatory=$false)]
    [switch]$UseKeyVaultReferences = $true,
    
    [Parameter(Mandatory=$false)]
    [switch]$AutoDiscoverConnections = $true
)

$ErrorActionPreference = "Stop"

# Get script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$workflowsDir = Join-Path $scriptDir "logic-app-workflows"

Write-Host "Deploying Logic App workflows to: $LogicAppName" -ForegroundColor Cyan

# Auto-discover subscription ID if not provided
if (-not $SubscriptionId) {
    $SubscriptionId = az account show --query id -o tsv
    Write-Host "Using subscription: $SubscriptionId" -ForegroundColor Gray
}

# Get Logic App location (may differ from resource group location)
$logicAppLocation = az functionapp show --name $LogicAppName --resource-group $ResourceGroupName --query location -o tsv
if ($logicAppLocation) {
    # Normalize location (e.g., "North Central US" -> "northcentralus")
    $location = $logicAppLocation.ToLower() -replace '\s', ''
    Write-Host "Logic App location: $location" -ForegroundColor Gray
} else {
    # Fallback to resource group location
    $location = az group show --name $ResourceGroupName --query location -o tsv
    Write-Host "Resource group location (fallback): $location" -ForegroundColor Gray
}

# Initialize connection variables
$Office365ConnectionName = $null
$Office365ConnectionRuntimeUrl = $null
$AzureBlobConnectionName = $null
$AzureBlobConnectionRuntimeUrl = $null

# Auto-discover connections (always enabled by default)
if ($AutoDiscoverConnections) {
    Write-Host "Auto-discovering API connections..." -ForegroundColor Yellow
    
    # Try to find Office365 connection
    $office365Connections = az resource list -g $ResourceGroupName --resource-type "Microsoft.Web/connections" --query "[?contains(name, 'office365')].name" -o tsv
    if ($office365Connections) {
        $Office365ConnectionName = $office365Connections.Split("`n")[0].Trim()
        Write-Host "Found Office365 connection: $Office365ConnectionName" -ForegroundColor Green
        
        # Get runtime URL via REST API (only available this way)
        $connectionDetails = az rest --method GET --uri "https://management.azure.com/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Web/connections/$Office365ConnectionName`?api-version=2016-06-01" | ConvertFrom-Json
        if ($connectionDetails.properties.connectionRuntimeUrl) {
            $Office365ConnectionRuntimeUrl = $connectionDetails.properties.connectionRuntimeUrl
            Write-Host "Office365 Runtime URL: $Office365ConnectionRuntimeUrl" -ForegroundColor Gray
        }
    } else {
        Write-Host "Warning: No Office365 connection found. Create one via Bicep deployment first." -ForegroundColor Yellow
    }
    
    # Try to find Azure Blob connection
    $blobConnections = az resource list -g $ResourceGroupName --resource-type "Microsoft.Web/connections" --query "[?contains(name, 'azureblob')].name" -o tsv
    if ($blobConnections) {
        $AzureBlobConnectionName = $blobConnections.Split("`n")[0].Trim()
        Write-Host "Found Azure Blob connection: $AzureBlobConnectionName" -ForegroundColor Green
        
        # Get runtime URL via REST API
        $connectionDetails = az rest --method GET --uri "https://management.azure.com/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Web/connections/$AzureBlobConnectionName`?api-version=2016-06-01" | ConvertFrom-Json
        if ($connectionDetails.properties.connectionRuntimeUrl) {
            $AzureBlobConnectionRuntimeUrl = $connectionDetails.properties.connectionRuntimeUrl
            Write-Host "Azure Blob Runtime URL: $AzureBlobConnectionRuntimeUrl" -ForegroundColor Gray
        }
    } else {
        Write-Host "Warning: No Azure Blob connection found. Create one via Bicep deployment first." -ForegroundColor Yellow
    }
}

# Create a temporary directory for the deployment package
$tempDir = New-Item -ItemType Directory -Path (Join-Path $env:TEMP "logicapp-deploy-$(Get-Date -Format 'yyyyMMddHHmmss')") -Force
$zipPath = Join-Path $tempDir "logicapp.zip"

try {
    # Copy workflow files to temp directory
    Write-Host "Preparing deployment package..." -ForegroundColor Yellow
    
    # Copy all workflow artifacts
    Copy-Item -Path (Join-Path $workflowsDir "*") -Destination $tempDir -Recurse -Force
    
    # Update connections.json with actual values if provided
    $connectionsFile = Join-Path $tempDir "connections.json"
    if (Test-Path $connectionsFile) {
        $connections = Get-Content $connectionsFile -Raw | ConvertFrom-Json
        
        # The connections.json uses @appsetting() which will be resolved at runtime
        # No need to modify it here as it uses app settings
        
        Write-Host "Connections configuration will use app settings at runtime" -ForegroundColor Gray
    }
    
    # Create zip file
    Write-Host "Creating deployment package..." -ForegroundColor Yellow
    
    # Get all files to include
    $filesToZip = Get-ChildItem -Path $tempDir -Recurse -File | Where-Object { $_.Name -ne "local.settings.json" }
    
    # Create zip
    Compress-Archive -Path (Get-ChildItem -Path $tempDir -Exclude "local.settings.json") -DestinationPath $zipPath -Force
    
    Write-Host "Deployment package created: $zipPath" -ForegroundColor Green
    
    # Build app settings for workflow configuration
    Write-Host "Updating Logic App application settings..." -ForegroundColor Yellow
    
    $appSettings = @{}
    
    # Azure context (for @appsetting() references in connections.json)
    $appSettings["AZURE_SUBSCRIPTION_ID"] = $SubscriptionId
    $appSettings["AZURE_RESOURCE_GROUP"] = $ResourceGroupName
    $appSettings["AZURE_LOCATION"] = $location

    # Workflow configuration parameters
    if ($BackendApiUrl) {
        $appSettings["WORKFLOW_BACKEND_API_URL"] = $BackendApiUrl
    }
    
    if ($AiSearchEndpoint) {
        $appSettings["WORKFLOW_AISEARCH_ENDPOINT"] = $AiSearchEndpoint
    }
    
    if ($AiSearchIndexerName) {
        $appSettings["WORKFLOW_AISEARCH_INDEXER_NAME"] = $AiSearchIndexerName
    }
    
    if ($SharedMailboxAddress) {
        $appSettings["WORKFLOW_SHARED_MAILBOX_ADDRESS"] = $SharedMailboxAddress
    }
    
    if ($BlobContainerPath) {
        $appSettings["WORKFLOW_BLOB_CONTAINER_PATH"] = $BlobContainerPath
    }
    
    # Key Vault references for secrets
    if ($UseKeyVaultReferences) {
        $kvName = if ($KeyVaultName) { $KeyVaultName } else {
            # Try to get from existing app settings
            $existingSettings = az webapp config appsettings list --resource-group $ResourceGroupName --name $LogicAppName | ConvertFrom-Json
            $kvSetting = $existingSettings | Where-Object { $_.name -eq 'WORKFLOW_KEY_VAULT_NAME' }
            if ($kvSetting) { $kvSetting.value } else { "kv-$($LogicAppName -replace 'logic-', '')" }
        }
        $appSettings["WORKFLOW_BACKEND_API_KEY"] = "@Microsoft.KeyVault(VaultName=$kvName;SecretName=api-key)"
        $appSettings["WORKFLOW_AISEARCH_API_KEY"] = "@Microsoft.KeyVault(VaultName=$kvName;SecretName=azure-aisearch-key)"
        Write-Host "Using Key Vault references for API keys (KeyVault: $kvName)" -ForegroundColor Cyan
    }

    # Connection settings (discovered at runtime via REST API)
    if ($Office365ConnectionName) {
        $appSettings["OFFICE365_CONNECTION_NAME"] = $Office365ConnectionName
    }
    
    if ($Office365ConnectionRuntimeUrl) {
        $appSettings["OFFICE365_CONNECTION_RUNTIME_URL"] = $Office365ConnectionRuntimeUrl
    }
    
    if ($AzureBlobConnectionName) {
        $appSettings["AZUREBLOB_CONNECTION_NAME"] = $AzureBlobConnectionName
    }
    
    if ($AzureBlobConnectionRuntimeUrl) {
        $appSettings["AZUREBLOB_CONNECTION_RUNTIME_URL"] = $AzureBlobConnectionRuntimeUrl
    }
    
    # Convert app settings to the format needed by az webapp config
    if ($appSettings.Count -gt 0) {
        $settingsArray = @()
        foreach ($key in $appSettings.Keys) {
            $settingsArray += "$key=$($appSettings[$key])"
        }
        
        Write-Host "Setting application settings..." -ForegroundColor Yellow
        az webapp config appsettings set `
            --resource-group $ResourceGroupName `
            --name $LogicAppName `
            --settings $settingsArray
    }
    
    # Deploy using zip deploy
    Write-Host "Deploying workflows using zip deploy..." -ForegroundColor Yellow
    
    az logicapp deployment source config-zip `
        --resource-group $ResourceGroupName `
        --name $LogicAppName `
        --src $zipPath
    
    Write-Host "Logic App workflows deployed successfully!" -ForegroundColor Green
    
    # Output next steps
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "1. Authorize the Office 365 connection in the Azure Portal" -ForegroundColor White
    Write-Host "2. Verify the workflow is enabled in the Logic App" -ForegroundColor White
    Write-Host "3. Test by sending an email with attachments to the configured mailbox" -ForegroundColor White
    
} finally {
    # Cleanup
    if (Test-Path $tempDir) {
        Remove-Item -Path $tempDir -Recurse -Force
    }
}
