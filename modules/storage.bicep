@description('Location for resources')
param location string

@description('Environment name (used in the storage account name; must be 3+ chars after stripping hyphens).')
@minLength(3)
param environmentName string

@description('Tags for resources')
param tags object

@description('Managed Identity Principal ID for RBAC')
param managedIdentityPrincipalId string

@description('Key Vault name to store connection string')
param keyVaultName string

@description('Allow shared key access (required for Logic App Standard)')
param allowSharedKeyAccess bool = true

@description('Whether the account is reachable from the public internet. Set to Disabled when fronting with a private endpoint.')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccess string = 'Enabled'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// Storage account name: only lowercase alphanumeric, 3-24 chars, globally unique.
// `replace` returns a string with no compile-time length floor; the @minLength(3)
// on `environmentName` makes the runtime length safe.
var storageNameClean = replace(environmentName, '-', '')
var storageName = take('st${storageNameClean}${uniqueString(resourceGroup().id)}', 24)
#disable-next-line BCP334
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: allowSharedKeyAccess
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: publicNetworkAccess
    networkAcls: {
      defaultAction: publicNetworkAccess == 'Disabled' ? 'Deny' : 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource documentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'documents'
  properties: {
    publicAccess: 'None'
  }
}

resource storageBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, managedIdentityPrincipalId, 'StorageBlobDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Queue Data Contributor — for pipeline workers that read/write queue messages
// Role ID: 974c5e8b-45b9-4653-ba55-5f855dd0fb88
resource storageQueueDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, managedIdentityPrincipalId, 'StorageQueueDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Store blob endpoint URI for managed identity authentication
resource storageConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'storage-connection-string'
  properties: {
    value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
  }
}

// Store blob endpoint URI separately for managed identity authentication
resource storageBlobEndpointSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'storage-blob-endpoint'
  properties: {
    value: storageAccount.properties.primaryEndpoints.blob
  }
}

output name string = storageAccount.name
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
output connectionStringSecretUri string = storageConnectionStringSecret.properties.secretUri
