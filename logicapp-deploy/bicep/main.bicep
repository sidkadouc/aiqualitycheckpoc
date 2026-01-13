
targetScope = 'subscription'

@description('Environment name (e.g., dev, staging, prod)')
param environmentName string

@description('Environment type (dev or prod)')
param envType string = 'dev'

@description('Primary location for resources')
param location string

// Logic App Configuration
param enableLogicApp bool = true

@description('Location for Logic App (may differ from main location due to service availability)')
param logicAppLocation string = location

@description('Key Vault name for secrets (optional - will be created if not provided)')
param keyVaultName string = ''

// Tags
var tags = {
  'azd-env-name': environmentName
  application: 'poc-ai'
  environment: environmentName
  envType: envType
  SecurityControl: 'Ignore'
}

// Computed Key Vault name
var computedKeyVaultName = keyVaultName != '' ? keyVaultName : 'kv-${environmentName}'

// Resource Group
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

// =============================================================================
// Storage Account for Logic App
// =============================================================================
module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
  }
}

// =============================================================================
// Monitoring (Log Analytics + Application Insights)
// =============================================================================
module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
  }
}

// =============================================================================
// Logic App Standard (Email Processing Workflow)
// Uses System-Assigned Managed Identity for authentication
// =============================================================================
module logicApp 'modules/logic-app.bicep' = if (enableLogicApp) {
  name: 'logic-app'
  scope: rg
  params: {
    location: logicAppLocation
    environmentName: environmentName
    tags: tags
    storageAccountName: storage.outputs.name
    keyVaultName: computedKeyVaultName
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
  }
}

// =============================================================================
// Logic App API Connections (Office 365 & Azure Blob)
// =============================================================================
module logicAppConnections 'modules/logic-app-connections.bicep' = if (enableLogicApp) {
  name: 'logic-app-connections'
  scope: rg
  params: {
    location: logicAppLocation
    environmentName: environmentName
    tags: tags
    logicAppPrincipalId: logicApp.?outputs.?principalId ?? ''
    logicAppName: logicApp.?outputs.?name ?? ''
  }
}

// Outputs
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_STORAGE_ACCOUNT_NAME string = storage.outputs.name
output AZURE_APP_INSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString

// Logic App outputs (conditional)
output AZURE_LOGIC_APP_NAME string = logicApp.?outputs.?name ?? ''
output AZURE_LOGIC_APP_HOSTNAME string = logicApp.?outputs.?defaultHostname ?? ''
output AZURE_LOGIC_APP_PRINCIPAL_ID string = logicApp.?outputs.?principalId ?? ''
output AZURE_OFFICE365_CONNECTION_NAME string = logicAppConnections.?outputs.?office365ConnectionName ?? ''
output AZURE_OFFICE365_CONNECTION_RUNTIME_URL string = logicAppConnections.?outputs.?office365ConnectionRuntimeUrl ?? ''
output AZURE_AZUREBLOB_CONNECTION_NAME string = logicAppConnections.?outputs.?azureBlobConnectionName ?? ''
output AZURE_AZUREBLOB_CONNECTION_RUNTIME_URL string = logicAppConnections.?outputs.?azureBlobConnectionRuntimeUrl ?? ''
