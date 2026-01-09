@description('Location for resources')
param location string

@description('Environment name')
param environmentName string

@description('Tags for resources')
param tags object

@description('Managed Identity Principal ID for RBAC')
param managedIdentityPrincipalId string

@description('Key Vault name to store secrets')
param keyVaultName string

@description('Cosmos DB database name')
param databaseName string = 'appdata'

@description('Cosmos DB container names')
param containerNames object = {
  conversations: 'conversations'
  callSessions: 'callsessions'
  transcriptions: 'transcriptions'
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: 'cosmos-${environmentName}-001'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAclBypass: 'AzureServices'
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    minimalTlsVersion: 'Tls12'
  }
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmosAccount
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDatabase
  name: containerNames.conversations
  properties: {
    resource: {
      id: containerNames.conversations
      partitionKey: {
        paths: ['/userId']
        kind: 'Hash'
      }
      indexingPolicy: {
        automatic: true
        indexingMode: 'consistent'
      }
      defaultTtl: -1
    }
  }
}

resource callSessionsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDatabase
  name: containerNames.callSessions
  properties: {
    resource: {
      id: containerNames.callSessions
      partitionKey: {
        paths: ['/id']
        kind: 'Hash'
      }
      indexingPolicy: {
        automatic: true
        indexingMode: 'consistent'
      }
      defaultTtl: -1
    }
  }
}

resource transcriptionsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDatabase
  name: containerNames.transcriptions
  properties: {
    resource: {
      id: containerNames.transcriptions
      partitionKey: {
        paths: ['/sessionId']
        kind: 'Hash'
      }
      indexingPolicy: {
        automatic: true
        indexingMode: 'consistent'
      }
      defaultTtl: -1
    }
  }
}

resource cosmosDataContributorRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, managedIdentityPrincipalId, 'CosmosDBDataContributor')
  properties: {
    roleName: 'Cosmos DB Data Contributor'
    type: 'CustomRole'
    assignableScopes: [cosmosAccount.id]
    permissions: [
      {
        dataActions: [
          'Microsoft.DocumentDB/databaseAccounts/readMetadata'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/*'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/*'
        ]
      }
    ]
  }
}

resource cosmosRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, managedIdentityPrincipalId, 'CosmosDBRoleAssignment')
  properties: {
    roleDefinitionId: cosmosDataContributorRole.id
    principalId: managedIdentityPrincipalId
    scope: cosmosAccount.id
  }
}

resource cosmosEndpointSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'cosmos-endpoint'
  properties: {
    value: cosmosAccount.properties.documentEndpoint
  }
}

resource cosmosKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'cosmos-key'
  properties: {
    value: cosmosAccount.listKeys().primaryMasterKey
  }
}

output endpoint string = cosmosAccount.properties.documentEndpoint
output endpointSecretUri string = cosmosEndpointSecret.properties.secretUri
output keySecretUri string = cosmosKeySecret.properties.secretUri
