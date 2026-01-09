targetScope = 'subscription'

@description('Environment name')
param environmentName string

@description('Environment type (dev or prod)')
param envType string = 'dev'

@description('Primary location for resources')
param location string

@description('Application name for resource naming and tagging')
param applicationName string = 'myapp'

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

// Cosmos DB Configuration (should match foundation)
@description('Cosmos DB database name')
param cosmosDbDatabaseName string = 'appdata'

@description('Cosmos DB container names')
param cosmosDbContainerNames object = {
  conversations: 'conversations'
  callSessions: 'callsessions'
  transcriptions: 'transcriptions'
}

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
  name: 'rg-${environmentName}'
}

// Container Apps Environment
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



// Chat API Container App
module chatApi 'modules/chat-api.bicep' = {
  name: 'chat-api'
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
    cosmosDbDatabaseName: cosmosDbDatabaseName
    cosmosDbContainerNames: cosmosDbContainerNames
  }
}



// Frontend Container App
module frontend 'modules/frontend.bicep' = {
  name: 'frontend'
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
    chatApiUrl: chatApi.outputs.url
    keyVaultName: keyVaultName
    applicationName: applicationName
    cosmosDbDatabaseName: cosmosDbDatabaseName
    cosmosDbContainerNames: cosmosDbContainerNames
  }
}

// Outputs
output AZURE_CONTAINER_APPS_ENVIRONMENT_ID string = containerAppsEnv.outputs.id
