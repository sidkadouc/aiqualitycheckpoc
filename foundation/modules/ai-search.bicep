@description('Location for resources')
param location string

@description('Environment name')
param environmentName string

@description('Tags for resources')
param tags object

@description('Managed Identity Principal ID for RBAC')
param managedIdentityPrincipalId string

@description('Storage Account Name for role assignment')
param storageAccountName string

@description('Key Vault name to store connection string')
param keyVaultName string

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

// Azure AI Search Service
resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: 'srch-${environmentName}'
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    networkRuleSet: {
      ipRules: []
    }
    semanticSearch: 'free'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// Role: Storage Blob Data Contributor for AI Search to access blob storage
// Role ID: ba92f5b4-2d11-453d-a403-e96b0029c9fe
resource searchStorageBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, searchService.id, 'StorageBlobDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: searchService.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role: Storage Blob Data Reader for AI Search (backup role)
// Role ID: 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1
resource searchStorageBlobDataReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, searchService.id, 'StorageBlobDataReader')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
    principalId: searchService.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role: Search Index Data Contributor for Managed Identity (apps to write to index)
// Role ID: 8ebe5a00-799e-43f5-93ac-243d3dce84a7
resource managedIdentitySearchIndexDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, managedIdentityPrincipalId, 'SearchIndexDataContributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Role: Search Index Data Reader for Managed Identity (apps to query index)
// Role ID: 1407120a-92aa-4202-b7e9-c0e197c71c8f
resource managedIdentitySearchIndexDataReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, managedIdentityPrincipalId, 'SearchIndexDataReader')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Role: Search Service Contributor for Managed Identity (manage indexes, indexers, etc.)
// Role ID: 7ca78c08-252a-4471-8644-bb5ff32d4ba0
resource managedIdentitySearchServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, managedIdentityPrincipalId, 'SearchServiceContributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Store AI Search endpoint in Key Vault
resource searchEndpointSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-aisearch-endpoint'
  properties: {
    value: 'https://${searchService.name}.search.windows.net'
  }
}

// Store AI Search admin key in Key Vault
resource searchAdminKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-aisearch-key'
  properties: {
    value: searchService.listAdminKeys().primaryKey
  }
}

output name string = searchService.name
output endpoint string = 'https://${searchService.name}.search.windows.net'
output principalId string = searchService.identity.principalId
