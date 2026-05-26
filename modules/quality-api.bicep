// ─────────────────────────────────────────────────────────────────────
// quality-api.bicep — AI Quality Checker API (FastAPI)
// Container App with managed identity access to Cosmos DB, AI Search,
// Azure OpenAI, and Key Vault.
// ─────────────────────────────────────────────────────────────────────

@description('Location for resources')
param location string

@description('Tags for resources')
param tags object

@description('Container Apps Environment ID')
param containerAppsEnvironmentId string

@description('Container Registry Endpoint')
param containerRegistryEndpoint string

@description('Key Vault Name')
param keyVaultName string

@description('Application Insights Connection String')
param appInsightsConnectionString string

@description('Environment type (dev or prod)')
param envType string = 'dev'

@description('Application name for service naming')
param applicationName string = 'ai-quality'

// Cosmos DB
@description('Cosmos DB endpoint')
param cosmosDbEndpoint string

@description('Cosmos DB database name')
param cosmosDbDatabaseName string = 'appdata'

@description('Cosmos DB rules container name')
param cosmosDbRulesContainerName string = 'policy-rules'

// Container image
@description('Container image name (without tag)')
param imageName string = 'quality-api'

@description('Container image tag')
param imageTag string = 'latest'

var serviceName = '${applicationName}-quality-api'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource qualityApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-quality-api'
  location: location
  tags: union(tags, { 'azd-service-name': 'quality-api' })
  identity: {
    type: 'SystemAssigned'
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
          allowedMethods: ['GET', 'POST', 'OPTIONS']
          allowedHeaders: ['*']
          allowCredentials: false
        }
      }
      registries: [
        {
          server: containerRegistryEndpoint
          identity: 'system'
        }
      ]
      secrets: [
        {
          name: 'azure-openai-endpoint'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-openai-endpoint'
          identity: 'system'
        }
        {
          name: 'azure-openai-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-openai-key'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'quality-api'
          image: '${containerRegistryEndpoint}/${imageName}:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            // ── DefaultAzureCredential picks the SAMI automatically ──
            {
              name: 'USE_MANAGED_IDENTITY'
              value: 'true'
            }
            // ── Cosmos DB (rules storage — managed identity auth) ──
            {
              name: 'USE_COSMOS_RULES'
              value: 'true'
            }
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
              name: 'AZURE_OPENAI_PRIMARY_DEPLOYMENT'
              value: 'gpt-5.4'
            }
            {
              name: 'AZURE_OPENAI_FALLBACK_DEPLOYMENT'
              value: 'gpt-4.1'
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
        maxReplicas: 5
      }
    }
  }
}

output name string = qualityApi.name
output url string = 'https://${qualityApi.properties.configuration.ingress.fqdn}'

@description('Principal ID of the container app\'s system-assigned managed identity.')
output principalId string = qualityApi.identity.principalId
