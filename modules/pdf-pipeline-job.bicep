// ─────────────────────────────────────────────────────────────────────
// pdf-pipeline-job.bicep — PDF Policy Extraction Pipeline as an ACA Job
// ------------------------------------------------------------------------------
// Runs on demand via `az containerapp job start`. Uses a system-assigned
// managed identity for ACR pull, Key Vault secret refs, and runtime data-plane
// calls (Cosmos DB, AI Foundry, Storage, AI Search).
// ─────────────────────────────────────────────────────────────────────

@description('Location for resources')
param location string

@description('Tags for resources')
param tags object

@description('Container Apps Environment ID')
param containerAppsEnvironmentId string

@description('Container Registry login server (e.g. crfoo.azurecr.io)')
param containerRegistryEndpoint string

@description('Application Insights Connection String')
param appInsightsConnectionString string

@description('Environment type (dev or prod)')
param envType string = 'dev'

@description('Application name for service naming')
param applicationName string = 'oecd-quality'

@description('Cosmos DB endpoint')
param cosmosDbEndpoint string

@description('Cosmos DB database name')
param cosmosDbDatabaseName string = 'appdata'

@description('Cosmos DB rules container name')
param cosmosDbRulesContainerName string = 'policy-rules'

@description('Storage account name (for blob output)')
param storageAccountName string

@description('Storage blob container for source PDFs / intermediate output')
param storageBlobContainerName string = 'documents'

@description('Blob name (inside storageBlobContainerName) of the PDF to process.')
param inputPdfBlobName string = 'input.pdf'

@description('Blob prefix (inside storageBlobContainerName) under which pipeline artifacts are uploaded after a run. The job appends /<run-id>/<file>.')
param outputBlobPrefix string = 'pipeline-output'

@description('Run the LLM-based rule extraction step. Set to false to skip and use deterministic extraction only.')
param useLlmExtraction bool = true

@description('AI Foundry endpoint (used for OpenAI + Document Intelligence)')
param aiFoundryEndpoint string

@description('AI Search endpoint')
param aiSearchEndpoint string

@description('Container image name (without tag)')
param imageName string = 'pdf-pipeline'

@description('Container image tag')
param imageTag string = 'latest'

@description('Max runtime in seconds for one job execution (1800 = 30 min)')
@minValue(60)
@maxValue(86400)
param replicaTimeoutSeconds int = 1800

@description('Number of retries per replica before the execution is marked Failed.')
@minValue(0)
@maxValue(10)
param replicaRetryLimit int = 1

var serviceName = '${applicationName}-pdf-pipeline'

resource pdfPipelineJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-pdf-pipeline'
  location: location
  tags: union(tags, { 'azd-service-name': 'pdf-pipeline' })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: replicaTimeoutSeconds
      replicaRetryLimit: replicaRetryLimit
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: containerRegistryEndpoint
          identity: 'system'
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
            // ── Cosmos DB (rules output — SAMI auth) ──
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
            // ── Storage (PDF input / artifact output — SAMI auth) ──
            {
              name: 'AZURE_STORAGE_ACCOUNT_NAME'
              value: storageAccountName
            }
            {
              name: 'AZURE_STORAGE_BLOB_ENDPOINT'
              value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}/'
            }
            {
              name: 'AZURE_STORAGE_CONTAINER'
              value: storageBlobContainerName
            }
            {
              name: 'INPUT_PDF_BLOB_NAME'
              value: inputPdfBlobName
            }
            {
              name: 'OUTPUT_BLOB_PREFIX'
              value: outputBlobPrefix
            }
            // ── Azure OpenAI (SAMI auth via DefaultAzureCredential) ──
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: aiFoundryEndpoint
            }
            {
              name: 'AZURE_OPENAI_GPT41_DEPLOYMENT'
              value: 'gpt-4.1'
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
              value: 'text-embedding-3-large'
            }
            {
              name: 'USE_LLM_EXTRACTION'
              value: '${useLlmExtraction}'
            }
            // ── Azure AI Search (SAMI auth) ──
            {
              name: 'AZURE_AISEARCH_ENDPOINT'
              value: aiSearchEndpoint
            }
            // ── Document Intelligence (same Foundry endpoint, SAMI auth) ──
            {
              name: 'AZURE_CONTENT_UNDERSTANDING_ENDPOINT'
              value: aiFoundryEndpoint
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
            // Marker for the SDK: use DefaultAzureCredential, not keys
            {
              name: 'USE_MANAGED_IDENTITY'
              value: 'true'
            }
          ]
        }
      ]
    }
  }
}

@description('Name of the ACA job.')
output name string = pdfPipelineJob.name

@description('Principal ID of the job\'s system-assigned managed identity.')
output principalId string = pdfPipelineJob.identity.principalId
