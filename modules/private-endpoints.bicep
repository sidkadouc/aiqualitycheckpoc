// ==============================================================================
// Private endpoints orchestrator
// ------------------------------------------------------------------------------
// Stamps out one PE per service against the shared PE subnet and links each
// to its private DNS zone(s). Cross-region PEs are supported — the target
// resource can live in a region other than the PE subnet region.
// ==============================================================================

targetScope = 'resourceGroup'

// ──────────────────────────────────────────────────────────────────────────────
// Parameters
// ──────────────────────────────────────────────────────────────────────────────

@description('Azure region for the PEs (must match the PE subnet).')
param location string

@description('Environment name used in PE naming.')
param environmentName string

@description('Resource tags.')
param tags object = {}

@description('Resource ID of the subnet that will host the PE NICs.')
param privateEndpointSubnetId string

@description('Map of private DNS zone names → resource IDs (output of networking module).')
param privateDnsZoneIds object

@description('Resource ID of the AI Foundry (Cognitive Services AIServices) account.')
param aiFoundryId string

@description('Resource ID of the Azure AI Search service.')
param aiSearchId string

@description('Resource ID of the Cosmos DB account.')
param cosmosDbId string

@description('Resource ID of the Key Vault.')
param keyVaultId string

@description('Resource ID of the Storage Account.')
param storageAccountId string

@description('Resource ID of the Container Registry. Pass empty string to skip the ACR PE (e.g. when using a shared ACR in another resource group).')
param containerRegistryId string = ''

// ──────────────────────────────────────────────────────────────────────────────
// AI Foundry (account sub-resource — covers OpenAI + Document Intelligence)
// ──────────────────────────────────────────────────────────────────────────────

module foundryPe 'private-endpoint.bicep' = {
  name: 'pe-aifoundry'
  params: {
    name: 'pe-${environmentName}-aifoundry'
    location: location
    tags: tags
    targetResourceId: aiFoundryId
    groupId: 'account'
    subnetId: privateEndpointSubnetId
    privateDnsZoneIds: [
      privateDnsZoneIds.cognitiveServices
      privateDnsZoneIds.openAi
      privateDnsZoneIds.aiServices
    ]
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// AI Search
// ──────────────────────────────────────────────────────────────────────────────

module searchPe 'private-endpoint.bicep' = {
  name: 'pe-aisearch'
  params: {
    name: 'pe-${environmentName}-aisearch'
    location: location
    tags: tags
    targetResourceId: aiSearchId
    groupId: 'searchService'
    subnetId: privateEndpointSubnetId
    privateDnsZoneIds: [privateDnsZoneIds.search]
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Cosmos DB (Core/SQL API)
// ──────────────────────────────────────────────────────────────────────────────

module cosmosPe 'private-endpoint.bicep' = {
  name: 'pe-cosmos'
  params: {
    name: 'pe-${environmentName}-cosmos'
    location: location
    tags: tags
    targetResourceId: cosmosDbId
    groupId: 'Sql'
    subnetId: privateEndpointSubnetId
    privateDnsZoneIds: [privateDnsZoneIds.cosmosSql]
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Key Vault
// ──────────────────────────────────────────────────────────────────────────────

module keyVaultPe 'private-endpoint.bicep' = {
  name: 'pe-keyvault'
  params: {
    name: 'pe-${environmentName}-keyvault'
    location: location
    tags: tags
    targetResourceId: keyVaultId
    groupId: 'vault'
    subnetId: privateEndpointSubnetId
    privateDnsZoneIds: [privateDnsZoneIds.keyVault]
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Storage — blob + queue (separate PEs, one per sub-resource)
// ──────────────────────────────────────────────────────────────────────────────

module storageBlobPe 'private-endpoint.bicep' = {
  name: 'pe-storage-blob'
  params: {
    name: 'pe-${environmentName}-stblob'
    location: location
    tags: tags
    targetResourceId: storageAccountId
    groupId: 'blob'
    subnetId: privateEndpointSubnetId
    privateDnsZoneIds: [privateDnsZoneIds.storageBlob]
  }
}

module storageQueuePe 'private-endpoint.bicep' = {
  name: 'pe-storage-queue'
  params: {
    name: 'pe-${environmentName}-stqueue'
    location: location
    tags: tags
    targetResourceId: storageAccountId
    groupId: 'queue'
    subnetId: privateEndpointSubnetId
    privateDnsZoneIds: [privateDnsZoneIds.storageQueue]
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Container Registry (optional)
// ──────────────────────────────────────────────────────────────────────────────

module acrPe 'private-endpoint.bicep' = if (!empty(containerRegistryId)) {
  name: 'pe-acr'
  params: {
    name: 'pe-${environmentName}-acr'
    location: location
    tags: tags
    targetResourceId: containerRegistryId
    groupId: 'registry'
    subnetId: privateEndpointSubnetId
    privateDnsZoneIds: [privateDnsZoneIds.containerRegistry]
  }
}
