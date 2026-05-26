// ==============================================================================
// AI Quality Checker — consolidated deployment
// ------------------------------------------------------------------------------
// Single-file deployment for the whole stack into a pre-created resource group:
//   - Managed identity, monitoring, Key Vault, Storage, Cosmos DB,
//     AI Search, AI Foundry (in Sweden Central by default)
//   - Container Registry + AcrPull role for the MI
//   - VNet (2 subnets: ACA infra + PE), private DNS zones, private endpoints
//   - Container Apps Environment (VNet-integrated) + 3 apps:
//     quality-api, pdf-pipeline, word-addin
//   - All cross-service managed-identity role assignments wired
//
// Deploy:
//   az deployment group create `
//     -g <pre-created-rg> `
//     -f main.bicep `
//     -p @main.parameters.json
// ==============================================================================

targetScope = 'resourceGroup'

// ──────────────────────────────────────────────────────────────────────────────
// Parameters
// ──────────────────────────────────────────────────────────────────────────────

@description('Short environment name used in resource naming (e.g. dev, stg, prd). 3+ chars, lowercase alphanumeric or hyphens.')
@minLength(3)
@maxLength(12)
param environmentName string

@description('Primary Azure region (where the RG, VNet, Cosmos, Search, Storage, KV, ACR and ACA env live).')
param location string = resourceGroup().location

@description('Region for the AI Foundry account. Sweden Central has the widest GPT-5.x availability today.')
param aiFoundryLocation string = 'swedencentral'

@description('Application name (used in tags + child resource naming).')
param applicationName string = 'ai-quality'

@description('Deployment user object ID (used to grant Foundry data-plane access for local debug). Leave empty in CI.')
param currentUserPrincipalId string = ''

// ── Networking ────────────────────────────────────────────────────────────────

@description('VNet address space.')
param vnetAddressPrefix string = '10.10.0.0/16'

@description('ACA infrastructure subnet CIDR. Workload-profile env requires >= /27; /23 recommended.')
param acaInfraSubnetPrefix string = '10.10.0.0/23'

@description('Private endpoint subnet CIDR. /27 is plenty for ~25 PEs.')
param privateEndpointSubnetPrefix string = '10.10.2.0/27'

// ── Cosmos DB ────────────────────────────────────────────────────────────────

@description('Cosmos DB database name.')
param cosmosDbDatabaseName string = 'appdata'

@description('Cosmos DB container names.')
param cosmosDbContainerNames object = {
  conversations: 'conversations'
  callSessions: 'callsessions'
  transcriptions: 'transcriptions'
  policyRules: 'policy-rules'
}

// ── Container apps / images ──────────────────────────────────────────────────

@description('Container image tag for all three apps (use a git SHA in CI).')
param imageTag string = 'latest'

@description('Deploy the word-addin Container App (set false if you serve the add-in elsewhere).')
param deployWordAddin bool = true

// ──────────────────────────────────────────────────────────────────────────────
// Variables
// ──────────────────────────────────────────────────────────────────────────────

var tags = {
  application: applicationName
  environment: environmentName
  'azd-env-name': environmentName
}

// ──────────────────────────────────────────────────────────────────────────────
// 1. Identity, monitoring, KV (foundation primitives)
// ──────────────────────────────────────────────────────────────────────────────

module managedIdentity 'modules/managed-identity.bicep' = {
  name: 'managed-identity'
  params: {
    location: location
    environmentName: environmentName
    tags: tags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    environmentName: environmentName
    tags: tags
  }
}

module keyVault 'modules/key-vault.bicep' = {
  name: 'key-vault'
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    publicNetworkAccess: 'Disabled'
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// 2. Networking (VNet + 2 subnets + private DNS zones)
// ──────────────────────────────────────────────────────────────────────────────

module networking 'modules/networking.bicep' = {
  name: 'networking'
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    vnetAddressPrefix: vnetAddressPrefix
    acaInfraSubnetPrefix: acaInfraSubnetPrefix
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// 3. Data + AI services (public access disabled, fronted by PEs)
// ──────────────────────────────────────────────────────────────────────────────

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    keyVaultName: keyVault.outputs.name
    publicNetworkAccess: 'Disabled'
  }
}

module cosmosDb 'modules/cosmos-db.bicep' = {
  name: 'cosmos-db'
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    keyVaultName: keyVault.outputs.name
    databaseName: cosmosDbDatabaseName
    containerNames: cosmosDbContainerNames
    publicNetworkAccess: 'Disabled'
  }
}

module aiSearch 'modules/ai-search.bicep' = {
  name: 'ai-search'
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    storageAccountName: storage.outputs.name
    keyVaultName: keyVault.outputs.name
    publicNetworkAccess: 'disabled'
  }
}

module aiFoundry 'modules/ai-foundry.bicep' = {
  name: 'ai-foundry'
  params: {
    aiFoundryName: 'aoai-${environmentName}-${take(uniqueString(resourceGroup().id), 6)}'
    location: aiFoundryLocation
    tags: tags
    keyVaultName: keyVault.outputs.name
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    aiSearchPrincipalId: aiSearch.outputs.principalId
    publicNetworkAccess: 'Disabled'
    currentUserPrincipalId: currentUserPrincipalId
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// 4. Container Registry (Premium so it can have a private endpoint) + AcrPull
// ──────────────────────────────────────────────────────────────────────────────

module containerRegistry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  params: {
    location: location
    tags: tags
    registryName: 'cr${replace(environmentName, '-', '')}${uniqueString(resourceGroup().id)}'
    sku: 'Premium'
    publicNetworkAccess: 'Disabled'
  }
}

module acrPushUserSelf 'modules/acr-role-assignment.bicep' = if (!empty(currentUserPrincipalId)) {
  name: 'acr-push-user'
  params: {
    containerRegistryName: containerRegistry.outputs.name
    principalIds: [currentUserPrincipalId]
    roleDefinitionId: '8311e382-0749-4cb8-b61a-304f252e45ec' // AcrPush — for local docker push by the deploying user
    roleSuffix: 'AcrPush-CurrentUser'
    principalType: 'User'
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// 5. Private endpoints (stamped against the PE subnet)
// ──────────────────────────────────────────────────────────────────────────────

module privateEndpoints 'modules/private-endpoints.bicep' = {
  name: 'private-endpoints'
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    privateEndpointSubnetId: networking.outputs.privateEndpointSubnetId
    privateDnsZoneIds: networking.outputs.privateDnsZoneIds
    aiFoundryId: aiFoundry.outputs.resourceId
    aiSearchId: resourceId('Microsoft.Search/searchServices', aiSearch.outputs.name)
    cosmosDbId: resourceId('Microsoft.DocumentDB/databaseAccounts', cosmosDb.outputs.name)
    keyVaultId: resourceId('Microsoft.KeyVault/vaults', keyVault.outputs.name)
    storageAccountId: resourceId('Microsoft.Storage/storageAccounts', storage.outputs.name)
    containerRegistryId: containerRegistry.outputs.id
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// 6. Container Apps Environment (VNet-integrated on the ACA infra subnet)
// ──────────────────────────────────────────────────────────────────────────────

module containerAppsEnv 'modules/container-apps-environment.bicep' = {
  name: 'container-apps-env'
  params: {
    location: location
    environmentName: environmentName
    envType: 'prod' // forces VNet integration + zone redundancy
    tags: tags
    logAnalyticsCustomerId: monitoring.outputs.logAnalyticsCustomerId
    logAnalyticsSharedKey: monitoring.outputs.logAnalyticsSharedKey
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    existingInfrastructureSubnetId: networking.outputs.acaInfraSubnetId
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// 7. Container Apps
// ──────────────────────────────────────────────────────────────────────────────

module qualityApi 'modules/quality-api.bicep' = {
  name: 'quality-api'
  params: {
    location: location
    tags: tags
    containerAppsEnvironmentId: containerAppsEnv.outputs.id
    containerRegistryEndpoint: containerRegistry.outputs.loginServer
    keyVaultName: keyVault.outputs.name
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    envType: 'prod'
    applicationName: applicationName
    cosmosDbEndpoint: cosmosDb.outputs.endpoint
    cosmosDbDatabaseName: cosmosDbDatabaseName
    cosmosDbRulesContainerName: cosmosDbContainerNames.policyRules
    imageTag: imageTag
  }
  dependsOn: [
    privateEndpoints
  ]
}

module pdfPipelineJob 'modules/pdf-pipeline-job.bicep' = {
  name: 'pdf-pipeline-job'
  params: {
    location: location
    tags: tags
    containerAppsEnvironmentId: containerAppsEnv.outputs.id
    containerRegistryEndpoint: containerRegistry.outputs.loginServer
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    envType: 'prod'
    applicationName: applicationName
    cosmosDbEndpoint: cosmosDb.outputs.endpoint
    cosmosDbDatabaseName: cosmosDbDatabaseName
    cosmosDbRulesContainerName: cosmosDbContainerNames.policyRules
    storageAccountName: storage.outputs.name
    aiFoundryEndpoint: aiFoundry.outputs.endpoint
    aiSearchEndpoint: aiSearch.outputs.endpoint
    imageTag: imageTag
  }
  dependsOn: [
    privateEndpoints
  ]
}

module wordAddin 'modules/word-addin.bicep' = if (deployWordAddin) {
  name: 'word-addin'
  params: {
    location: location
    tags: tags
    containerAppsEnvironmentId: containerAppsEnv.outputs.id
    containerRegistryEndpoint: containerRegistry.outputs.loginServer
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    envType: 'prod'
    applicationName: applicationName
    qualityApiUrl: qualityApi.outputs.url
    imageTag: imageTag
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// 8. System-assigned managed identity (SAMI) role assignments
// ------------------------------------------------------------------------------
// Each container app has its own SAMI; we grant per-app least-privilege roles
// on ACR (pull), AI Foundry, Storage, Cosmos DB and AI Search.
// The shared UAMI keeps its existing roles (used for Key Vault secret refs).
// ──────────────────────────────────────────────────────────────────────────────

// Built-in role definition IDs (GUIDs only — modules call subscriptionResourceId)
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
// "Cognitive Services User" — on the new AI Foundry resource (kind=AIServices)
// this single role grants access to ALL Foundry features (OpenAI + Document
// Intelligence + Vision + Content Safety + Speech). Per Foundry RBAC docs.
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'

// Collected SAMI principal IDs per use-case
var samiAllPrincipalIds = deployWordAddin
  ? [qualityApi.outputs.principalId, pdfPipelineJob.outputs.principalId, wordAddin!.outputs.principalId]
  : [qualityApi.outputs.principalId, pdfPipelineJob.outputs.principalId]

var samiDataPlanePrincipalIds = [
  qualityApi.outputs.principalId
  pdfPipelineJob.outputs.principalId
]

// ── ACR — AcrPull for every SAMI (apps + job) ────────────────────────────────
module samiAcrPull 'modules/acr-role-assignment.bicep' = {
  name: 'sami-acr-pull'
  params: {
    containerRegistryName: containerRegistry.outputs.name
    principalIds: samiAllPrincipalIds
    roleDefinitionId: acrPullRoleId
    roleSuffix: 'AcrPull-Sami'
  }
}

// ── Key Vault — Secrets User for SAMIs that mount KV-backed secret refs ───────
module samiKeyVaultSecretsUser 'modules/key-vault-role-assignment.bicep' = {
  name: 'sami-kv-secrets-user'
  params: {
    keyVaultName: keyVault.outputs.name
    principalIds: samiDataPlanePrincipalIds
    roleDefinitionId: '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User
    roleSuffix: 'KeyVaultSecretsUser-Sami'
  }
}

// ── AI Foundry — Cognitive Services User (covers OpenAI + Doc Intelligence) ──
module samiFoundryUser 'modules/ai-foundry-role-assignment.bicep' = {
  name: 'sami-foundry-user'
  params: {
    aiFoundryName: aiFoundry.outputs.name
    principalIds: samiDataPlanePrincipalIds
    roleDefinitionId: cognitiveServicesUserRoleId
    roleSuffix: 'CognitiveServicesUser-Sami'
  }
}

// ── Storage — Blob Data Contributor for both data-plane apps ─────────────────
module samiStorageBlob 'modules/storage-role-assignment.bicep' = {
  name: 'sami-storage-blob'
  params: {
    storageAccountName: storage.outputs.name
    principalIds: samiDataPlanePrincipalIds
    roleDefinitionId: storageBlobDataContributorRoleId
    roleSuffix: 'StorageBlobDataContributor-Sami'
  }
}

// ── Storage — Queue Data Contributor only for the pipeline job ──────────────
module samiStorageQueue 'modules/storage-role-assignment.bicep' = {
  name: 'sami-storage-queue'
  params: {
    storageAccountName: storage.outputs.name
    principalIds: [pdfPipelineJob.outputs.principalId]
    roleDefinitionId: storageQueueDataContributorRoleId
    roleSuffix: 'StorageQueueDataContributor-Sami'
  }
}

// ── Cosmos DB — built-in Data Contributor for both data-plane apps ───────────
module samiCosmos 'modules/cosmos-role-assignment.bicep' = {
  name: 'sami-cosmos'
  params: {
    cosmosAccountName: cosmosDb.outputs.name
    principalIds: samiDataPlanePrincipalIds
  }
}

// ── AI Search — Index Data Reader for quality-api (reads chunks at query) ────
module samiSearchReader 'modules/ai-search-role-assignment.bicep' = {
  name: 'sami-search-reader'
  params: {
    searchServiceName: aiSearch.outputs.name
    principalIds: [qualityApi.outputs.principalId]
    roleDefinitionId: searchIndexDataReaderRoleId
    roleSuffix: 'SearchIndexDataReader-Sami'
  }
}

// ── AI Search — Index Data Contributor for pdf-pipeline job (writes chunks) ──
module samiSearchContributor 'modules/ai-search-role-assignment.bicep' = {
  name: 'sami-search-contributor'
  params: {
    searchServiceName: aiSearch.outputs.name
    principalIds: [pdfPipelineJob.outputs.principalId]
    roleDefinitionId: searchIndexDataContributorRoleId
    roleSuffix: 'SearchIndexDataContributor-Sami'
  }
}

// ── AI Search — Service Contributor for the pipeline so it can create the index ──
//    on its first run (data-plane index admin perms; one-time bootstrap).
module samiSearchServiceContributor 'modules/ai-search-role-assignment.bicep' = {
  name: 'sami-search-svc-contrib'
  params: {
    searchServiceName: aiSearch.outputs.name
    principalIds: [pdfPipelineJob.outputs.principalId]
    roleDefinitionId: '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor
    roleSuffix: 'SearchServiceContributor-Sami'
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Outputs (consumed by the deploy + docker-build scripts)
// ──────────────────────────────────────────────────────────────────────────────

output AZURE_RESOURCE_GROUP string = resourceGroup().name
output AZURE_LOCATION string = location

output AZURE_MANAGED_IDENTITY_ID string = managedIdentity.outputs.id
output AZURE_MANAGED_IDENTITY_CLIENT_ID string = managedIdentity.outputs.clientId
output AZURE_MANAGED_IDENTITY_PRINCIPAL_ID string = managedIdentity.outputs.principalId

output AZURE_KEY_VAULT_NAME string = keyVault.outputs.name
output AZURE_KEY_VAULT_URI string = keyVault.outputs.uri

output AZURE_STORAGE_ACCOUNT_NAME string = storage.outputs.name
output AZURE_COSMOS_ENDPOINT string = cosmosDb.outputs.endpoint
output AZURE_COSMOS_DATABASE string = cosmosDbDatabaseName
output AZURE_AISEARCH_NAME string = aiSearch.outputs.name
output AZURE_AISEARCH_ENDPOINT string = aiSearch.outputs.endpoint
output AZURE_OPENAI_NAME string = aiFoundry.outputs.name
output AZURE_OPENAI_ENDPOINT string = aiFoundry.outputs.endpoint
output AZURE_CONTENT_UNDERSTANDING_ENDPOINT string = aiFoundry.outputs.contentUnderstandingEndpoint
output AZURE_OPENAI_GPT41_DEPLOYMENT string = aiFoundry.outputs.gpt41DeploymentName
output AZURE_OPENAI_GPT54_DEPLOYMENT string = aiFoundry.outputs.gpt54DeploymentName
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = aiFoundry.outputs.embeddingDeploymentName

output AZURE_CONTAINER_REGISTRY_NAME string = containerRegistry.outputs.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.outputs.loginServer

output AZURE_LOG_ANALYTICS_WORKSPACE_ID string = monitoring.outputs.logAnalyticsWorkspaceId
output AZURE_APPLICATION_INSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString

output AZURE_VNET_ID string = networking.outputs.vnetId
output AZURE_ACA_INFRA_SUBNET_ID string = networking.outputs.acaInfraSubnetId
output AZURE_PRIVATE_ENDPOINT_SUBNET_ID string = networking.outputs.privateEndpointSubnetId

output QUALITY_API_URL string = qualityApi.outputs.url
output WORD_ADDIN_URL string = deployWordAddin ? wordAddin!.outputs.url : ''
