// ─────────────────────────────────────────────────────────────────────
// pdf-pipeline.bicep — PDF Policy Extraction Pipeline
// Container App (Job-style, on-demand) with managed identity access to
// Cosmos DB, Azure OpenAI, AI Search, Document Intelligence, and Storage.
// ─────────────────────────────────────────────────────────────────────

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
param applicationName string = 'oecd-quality'

// Cosmos DB
@description('Cosmos DB endpoint')
param cosmosDbEndpoint string

@description('Cosmos DB database name')
param cosmosDbDatabaseName string = 'appdata'

@description('Cosmos DB rules container name')
param cosmosDbRulesContainerName string = 'policy-rules'

// Container image
@description('Container image name (without tag)')
param imageName string = 'pdf-pipeline'

@description('Container image tag')
param imageTag string = 'latest'

var serviceName = '${applicationName}-pdf-pipeline'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource pdfPipeline 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-pdf-pipeline'
  location: location
  tags: union(tags, { 'azd-service-name': 'pdf-pipeline' })
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
        external: false
        targetPort: 8080
        transport: 'auto'
      }
      registries: [
        {
          server: containerRegistryEndpoint
          identity: managedIdentityId
        }
      ]
      secrets: [
        {
          name: 'azure-openai-endpoint'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-openai-endpoint'
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
          name: 'azure-aisearch-uri'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-aisearch-uri'
          identity: managedIdentityId
        }
        {
          name: 'content-understanding-endpoint'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/content-understanding-endpoint'
          identity: managedIdentityId
        }
        {
          name: 'storage-connection-string'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/storage-connection-string'
          identity: managedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'pdf-pipeline'
          image: '${containerRegistryEndpoint}/${imageName}:${imageTag}'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            // ── Managed identity ──
            {
              name: 'AZURE_CLIENT_ID'
              value: managedIdentityClientId
            }
            {
              name: 'USE_MANAGED_IDENTITY'
              value: 'true'
            }
            // ── Cosmos DB (rules output — managed identity auth) ──
            {
              name: 'AZURE_COSMOS_ENDPOINT'
              value: cosmosDbEndpoint
            }
            {
              name: 'AZURE_COSMOS_DATABASE'
              value: cosmosDbDatabaseName
            }
            {
              name: 'AZURE_COSMOS_RULES_CONTAINER'
              value: cosmosDbRulesContainerName
            }
            // ── Azure OpenAI ──
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              secretRef: 'azure-openai-endpoint'
            }
            {
              name: 'AZURE_OPENAI_KEY'
              secretRef: 'azure-openai-key'
            }
            {
              name: 'AZURE_OPENAI_GPT41_DEPLOYMENT'
              value: 'gpt-4.1'
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
              value: 'text-embedding-3-large'
            }
            // ── Azure AI Search ──
            {
              name: 'AZURE_AISEARCH_ENDPOINT'
              secretRef: 'azure-aisearch-uri'
            }
            {
              name: 'AZURE_AISEARCH_KEY'
              secretRef: 'azure-aisearch-key'
            }
            // ── Document Intelligence ──
            {
              name: 'AZURE_CONTENT_UNDERSTANDING_ENDPOINT'
              secretRef: 'content-understanding-endpoint'
            }
            // ── Storage ──
            {
              name: 'AZURE_STORAGE_CONNECTION_STRING'
              secretRef: 'storage-connection-string'
            }
            // ── Observability ──
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
        maxReplicas: 2
      }
    }
  }
}

output name string = pdfPipeline.name
