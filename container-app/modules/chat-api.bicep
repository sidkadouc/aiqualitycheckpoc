@description('Location for resources')
param location string

@description('Tags for resources')
param tags object

@description('Container Apps Environment ID')
param containerAppsEnvironmentId string

@description('Managed Identity ID')
param managedIdentityId string

@description('Managed Identity Client ID')
param managedIdentityClientId string

@description('Container Registry Endpoint')
param containerRegistryEndpoint string

@description('Key Vault Name')
param keyVaultName string

@description('Application Insights Connection String')
param appInsightsConnectionString string

@description('Environment type (dev or prod)')
param envType string = 'dev'

@description('Application name for service naming')
param applicationName string = 'myapp'

@description('Cosmos DB database name')
param cosmosDbDatabaseName string = 'appdata'

@description('Cosmos DB container names')
param cosmosDbContainerNames object = {
  conversations: 'conversations'
  callSessions: 'callsessions'
  transcriptions: 'transcriptions'
}

// Define frontendUrl based on the container app's ingress FQDN
var serviceName = '${applicationName}-api'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource chatApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-check-api'
  location: location
  tags: union(tags, { 'azd-service-name': 'chat-api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        corsPolicy: {
          allowedOrigins: ['*']
          allowedMethods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
          allowedHeaders: ['*']
          allowCredentials: false
        }
      }
      registries: [
        {
          server: containerRegistryEndpoint
          identity: managedIdentityId
        }
      ]
      secrets: [
        {
          name: 'sql-connection-string'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/sql-connection-string'
          identity: managedIdentityId
        }
        {
          name: 'storage-connection-string'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/storage-connection-string'
          identity: managedIdentityId
        }
        {
          name: 'jwt-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/jwt-key'
          identity: managedIdentityId
        }
        {
          name: 'jwt-issuer'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/jwt-issuer'
          identity: managedIdentityId
        }
        {
          name: 'jwt-audience'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/jwt-audience'
          identity: managedIdentityId
        }
        {
          name: 'api-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/api-key'
          identity: managedIdentityId
        }
        {
          name: 'service-api-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/service-api-key'
          identity: managedIdentityId
        }
        {
          name: 'azure-openai-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-openai-key'
          identity: managedIdentityId
        }
        {
          name: 'azure-aisearch-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-aisearch-key'
          identity: managedIdentityId
        }
        {
          name: 'cosmos-endpoint'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/cosmos-endpoint'
          identity: managedIdentityId
        }
        {
          name: 'cosmos-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/cosmos-key'
          identity: managedIdentityId
        }
        {
          name: 'azure-openai-chat-model'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-openai-chat-model'
          identity: managedIdentityId
        }
        {
          name: 'azure-openai-embedding-model'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-openai-embedding-model'
          identity: managedIdentityId
        }
        {
          name: 'azure-openai-endpoint'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-openai-endpoint'
          identity: managedIdentityId
        }
        {
          name: 'azure-aisearch-uri'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-aisearch-uri'
          identity: managedIdentityId
        }
        {
          name: 'acs-connection-string'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/acs-connection-string'
          identity: managedIdentityId
        }
        {
          name: 'content-understanding-endpoint'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/content-understanding-endpoint'
          identity: managedIdentityId
        }
        {
          name: 'azure-aifoundry-endpoint'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-aifoundry-endpoint'
          identity: managedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'chat-api'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'ASPNETCORE_ENVIRONMENT'
              value: envType == 'dev' ? 'Development' : 'Production'
            }
            {
              name: 'ConnectionStrings__DefaultConnection'
              secretRef: 'sql-connection-string'
            }
            {
              name: 'ConnectionStrings__BlobStorage'
              secretRef: 'storage-connection-string'
            }
            {
              name: 'CosmosDb__Endpoint'
              secretRef: 'cosmos-endpoint'
            }
            {
              name: 'CosmosDb__Key'
              secretRef: 'cosmos-key'
            }
            {
              name: 'CosmosDb__DatabaseName'
              value: cosmosDbDatabaseName
            }
            {
              name: 'CosmosDb__ContainerName'
              value: cosmosDbContainerNames.conversations
            }
            {
              name: 'CosmosDb__CallSessionsContainerName'
              value: cosmosDbContainerNames.callSessions
            }
            {
              name: 'CosmosDb__TranscriptionsContainerName'
              value: cosmosDbContainerNames.transcriptions
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: managedIdentityClientId
            }
            {
              name: 'ApiKey__Key'
              secretRef: 'api-key'
            }
            {
              name: 'ApiKey__ServiceApiKey'
              secretRef: 'service-api-key'
            }
            {
              name: 'AzureOpenAI__Endpoint'
              secretRef: 'azure-openai-endpoint'
            }
            {
              name: 'AzureOpenAI__Key'
              secretRef: 'azure-openai-key'
            }
            {
              name: 'AzureOpenAI__ChatModel'
              secretRef: 'azure-openai-chat-model'
            }
            {
              name: 'AzureOpenAI__EmbeddingModel'
              secretRef: 'azure-openai-embedding-model'
            }
            {
              name: 'AzureAISearch__Uri'
              secretRef: 'azure-aisearch-uri'
            }
            {
              name: 'AzureAISearch__Key'
              secretRef: 'azure-aisearch-key'
            }
            {
              name: 'ContentUnderstanding__Endpoint'
              secretRef: 'content-understanding-endpoint'
            }
            {
              name: 'AzureAIFoundry__Endpoint'
              secretRef: 'azure-aifoundry-endpoint'
            }
            {
              name: 'AzureCommunicationServices__ConnectionString'
              secretRef: 'acs-connection-string'
            }
            {
              name: 'AzureCommunicationServices__CallbackBaseUrl'
              value: '' // Will be set via app config or environment
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: serviceName
            }
            {
              name: 'OTEL_RESOURCE_ATTRIBUTES'
              value: 'service.name=${serviceName},service.namespace=${applicationName},deployment.environment=${envType}'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
}

output name string = chatApi.name
output url string = 'https://${chatApi.properties.configuration.ingress.fqdn}'
