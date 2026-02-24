targetScope = 'subscription'

@description('Environment name')
param environmentName string

@description('Environment type (dev or prod)')
param envType string = 'dev'

@description('Primary location for resources')
param location string

@description('Application name for resource naming and tagging')
param applicationName string = 'oecd-quality'

@description('Resource group name. Defaults to rg-<environmentName>.')
param resourceGroupName string = 'rg-${environmentName}'

// From Foundation Layer
@description('Managed Identity ID')
param managedIdentityId string

@description('Managed Identity Client ID')
param managedIdentityClientId string

@description('Log Analytics Customer ID')
param logAnalyticsCustomerId string

@secure()
@description('Log Analytics Shared Key')
param logAnalyticsSharedKey string

@description('Application Insights Connection String')
param appInsightsConnectionString string

@description('Key Vault Name')
param keyVaultName string

// From Shared ACR Layer
@description('Container Registry Endpoint')
param containerRegistryEndpoint string

// Cosmos DB Configuration (from foundation outputs)
@description('Cosmos DB endpoint')
param cosmosDbEndpoint string

@description('Cosmos DB database name')
param cosmosDbDatabaseName string = 'appdata'

@description('Cosmos DB rules container name')
param cosmosDbRulesContainerName string = 'policy-rules'

// Tags
var tags = {
  'azd-env-name': environmentName
  application: applicationName
  environment: environmentName
  envType: envType
  SecurityControl: 'Ignore'
}

// Resource Group (same as foundation)
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' existing = {
  name: resourceGroupName
}

// ═══════════════════════════════════════════════════════════════════════
// Container Apps Environment
// ═══════════════════════════════════════════════════════════════════════
module containerAppsEnv 'modules/container-apps-environment.bicep' = {
  name: 'container-apps-env'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    envType: envType
    tags: tags
    logAnalyticsCustomerId: logAnalyticsCustomerId
    logAnalyticsSharedKey: logAnalyticsSharedKey
    appInsightsConnectionString: appInsightsConnectionString
  }
}

// ═══════════════════════════════════════════════════════════════════════
// 1. Quality Checker API — FastAPI (reads rules from Cosmos DB)
// ═══════════════════════════════════════════════════════════════════════
module qualityApi 'modules/quality-api.bicep' = {
  name: 'quality-api'
  scope: rg
  params: {
    location: location
    envType: envType
    tags: tags
    containerAppsEnvironmentId: containerAppsEnv.outputs.id
    managedIdentityId: managedIdentityId
    managedIdentityClientId: managedIdentityClientId
    containerRegistryEndpoint: containerRegistryEndpoint
    keyVaultName: keyVaultName
    appInsightsConnectionString: appInsightsConnectionString
    applicationName: applicationName
    cosmosDbEndpoint: cosmosDbEndpoint
    cosmosDbDatabaseName: cosmosDbDatabaseName
    cosmosDbRulesContainerName: cosmosDbRulesContainerName
  }
}

// ═══════════════════════════════════════════════════════════════════════
// 2. PDF Pipeline — extracts rules and stores them in Cosmos DB
// ═══════════════════════════════════════════════════════════════════════
module pdfPipeline 'modules/pdf-pipeline.bicep' = {
  name: 'pdf-pipeline'
  scope: rg
  params: {
    location: location
    envType: envType
    tags: tags
    containerAppsEnvironmentId: containerAppsEnv.outputs.id
    managedIdentityId: managedIdentityId
    managedIdentityClientId: managedIdentityClientId
    containerRegistryEndpoint: containerRegistryEndpoint
    keyVaultName: keyVaultName
    appInsightsConnectionString: appInsightsConnectionString
    applicationName: applicationName
    cosmosDbEndpoint: cosmosDbEndpoint
    cosmosDbDatabaseName: cosmosDbDatabaseName
    cosmosDbRulesContainerName: cosmosDbRulesContainerName
  }
}

// ═══════════════════════════════════════════════════════════════════════
// 3. Word Add-in — static SPA served via nginx
// ═══════════════════════════════════════════════════════════════════════
module wordAddin 'modules/word-addin.bicep' = {
  name: 'word-addin'
  scope: rg
  params: {
    location: location
    envType: envType
    tags: tags
    containerAppsEnvironmentId: containerAppsEnv.outputs.id
    managedIdentityId: managedIdentityId
    managedIdentityClientId: managedIdentityClientId
    containerRegistryEndpoint: containerRegistryEndpoint
    appInsightsConnectionString: appInsightsConnectionString
    applicationName: applicationName
    qualityApiUrl: qualityApi.outputs.url // inject API URL into add-in config
  }
}

// ═══════════════════════════════════════════════════════════════════════
// Outputs
// ═══════════════════════════════════════════════════════════════════════
output AZURE_CONTAINER_APPS_ENVIRONMENT_ID string = containerAppsEnv.outputs.id
output QUALITY_API_URL string = qualityApi.outputs.url
output WORD_ADDIN_URL string = wordAddin.outputs.url
