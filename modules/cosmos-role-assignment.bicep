// ==============================================================================
// Cosmos DB SQL data-plane role assignment (by name)
// ------------------------------------------------------------------------------
// Uses the built-in Cosmos DB Data Contributor role (00000000-0000-0000-0000-
// 000000000002), which is automatically present on every Cosmos account with
// SQL API. Assignments are created via the SQL role-assignment resource type
// (NOT Microsoft.Authorization/roleAssignments — Cosmos data plane is separate).
// ==============================================================================

targetScope = 'resourceGroup'

@description('Name of the existing Cosmos DB account (SQL API).')
param cosmosAccountName string

@description('Principal IDs to grant Cosmos Data Contributor.')
param principalIds string[]

@description('Built-in Cosmos role: 00000000-0000-0000-0000-000000000001 = Data Reader, 00000000-0000-0000-0000-000000000002 = Data Contributor.')
param builtinRoleId string = '00000000-0000-0000-0000-000000000002'

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}

resource ras 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [for pid in principalIds: {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, pid, builtinRoleId)
  properties: {
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${builtinRoleId}'
    principalId: pid
    scope: cosmosAccount.id
  }
}]
