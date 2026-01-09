targetScope = 'subscription'

@description('Environment name (e.g., dev, staging, prod)')
param environmentName string

@description('Environment type (dev or prod)')
param envType string = 'dev'

@description('Primary location for resources')
param location string

@description('Application name used for resource naming and tagging')
param applicationName string = 'myapp'

// Azure AI Configuration (secrets stored in Key Vault)
@secure()
param azureOpenAIKey string = ''
@secure()
param azureAISearchSecret string = ''

// Security - these are now optional, will be auto-generated if not provided
@secure()
param jwtKey string = ''
@secure()
param apiKey string = ''
@secure()
param serviceApiKey string = ''

// Email Settings (for Graph API) - must be provided if email is needed
@secure()
param emailClientId string = ''
@secure()
param emailClientSecret string = ''

// Azure Communication Services
@secure()
param acsConnectionString string = ''

// Current user info for SQL admin access (allows running post-provision scripts)
@description('Current deploying user principal ID')
param currentUserPrincipalId string = ''
@description('Current deploying user login (email)')
param currentUserLogin string = ''

// Cosmos DB Configuration
@description('Cosmos DB database name')
param cosmosDbDatabaseName string = 'appdata'
@description('Cosmos DB container names')
param cosmosDbContainerNames object = {
  conversations: 'conversations'
  callSessions: 'callsessions'
  transcriptions: 'transcriptions'
}

// SQL Database Configuration
@description('SQL database name')
param sqlDatabaseName string = 'AppDb'

// Tags
var tags = {
  'azd-env-name': environmentName
  application: applicationName
  environment: environmentName
  envType: envType
  SecurityControl: 'Ignore'
}

// Resource Group
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

// =============================================================================
// User-Assigned Managed Identity
// =============================================================================
module managedIdentity 'modules/managed-identity.bicep' = {
  name: 'managed-identity'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
  }
}

// =============================================================================
// Log Analytics & Application Insights
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
// Key Vault
// =============================================================================
module keyVault 'modules/key-vault.bicep' = {
  name: 'key-vault'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    apiKey: apiKey
    serviceApiKey: serviceApiKey
    azureOpenAIKey: azureOpenAIKey
    azureAISearchSecret: azureAISearchSecret
    emailClientId: emailClientId
    emailClientSecret: emailClientSecret
    acsConnectionString: acsConnectionString
  }
}

// =============================================================================
// SQL Server & Database
// =============================================================================
module sqlServer 'modules/sql-server.bicep' = {
  name: 'sql-server'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    managedIdentityName: managedIdentity.outputs.name
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    managedIdentityClientId: managedIdentity.outputs.clientId
    currentUserPrincipalId: currentUserPrincipalId
    currentUserLogin: currentUserLogin
    databaseName: sqlDatabaseName
  }
}

// =============================================================================
// Storage Account
// =============================================================================
module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    keyVaultName: keyVault.outputs.name
  }
}

// =============================================================================
// Cosmos DB
// =============================================================================
module cosmosDb 'modules/cosmos-db.bicep' = {
  name: 'cosmos-db'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    keyVaultName: keyVault.outputs.name
    databaseName: cosmosDbDatabaseName
    containerNames: cosmosDbContainerNames
  }
}

// =============================================================================
// Azure AI Search
// =============================================================================
module aiSearch 'modules/ai-search.bicep' = {
  name: 'ai-search'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    storageAccountName: storage.outputs.name
    keyVaultName: keyVault.outputs.name
  }
}

// =============================================================================
// Azure AI Foundry (Azure OpenAI)
// =============================================================================
module aiFoundry 'modules/ai-foundry.bicep' = {
  name: 'ai-foundry'
  scope: rg
  params: {
    aiFoundryName: 'aoai-${environmentName}'
    location: 'swedencentral'
    tags: tags
    keyVaultName: keyVault.outputs.name
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    aiSearchPrincipalId: aiSearch.outputs.principalId
  }
}




// =============================================================================
// Outputs
// =============================================================================
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_MANAGED_IDENTITY_ID string = managedIdentity.outputs.id
output AZURE_MANAGED_IDENTITY_NAME string = managedIdentity.outputs.name
output AZURE_MANAGED_IDENTITY_PRINCIPAL_ID string = managedIdentity.outputs.principalId
output AZURE_MANAGED_IDENTITY_CLIENT_ID string = managedIdentity.outputs.clientId
output AZURE_LOG_ANALYTICS_WORKSPACE_ID string = monitoring.outputs.logAnalyticsWorkspaceId
output AZURE_LOG_ANALYTICS_CUSTOMER_ID string = monitoring.outputs.logAnalyticsCustomerId
#disable-next-line outputs-should-not-contain-secrets
output AZURE_LOG_ANALYTICS_SHARED_KEY string = monitoring.outputs.logAnalyticsSharedKey
output AZURE_APPLICATION_INSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
output AZURE_KEY_VAULT_NAME string = keyVault.outputs.name
output AZURE_KEY_VAULT_URI string = keyVault.outputs.uri
output AZURE_SQL_SERVER_FQDN string = sqlServer.outputs.fqdn
output AZURE_STORAGE_ACCOUNT_NAME string = storage.outputs.name
output AZURE_COSMOS_ENDPOINT string = cosmosDb.outputs.endpoint
output AZURE_AISEARCH_NAME string = aiSearch.outputs.name
output AZURE_AISEARCH_ENDPOINT string = aiSearch.outputs.endpoint
output AZURE_OPENAI_ENDPOINT string = aiFoundry.outputs.endpoint
output AZURE_CONTENT_UNDERSTANDING_ENDPOINT string = aiFoundry.outputs.contentUnderstandingEndpoint
output AZURE_OPENAI_NAME string = aiFoundry.outputs.name
output AZURE_OPENAI_GPT41_DEPLOYMENT string = aiFoundry.outputs.gpt41DeploymentName
output AZURE_OPENAI_GPT4O_DEPLOYMENT string = aiFoundry.outputs.gpt4oDeploymentName
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = aiFoundry.outputs.embeddingDeploymentName
output AZURE_ENV_TYPE string = envType



