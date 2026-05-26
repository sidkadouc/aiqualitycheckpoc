// ==============================================================================
// Storage role assignment (by name)
// ==============================================================================

targetScope = 'resourceGroup'

@description('Name of the existing storage account.')
param storageAccountName string

@description('Principal IDs to assign the role to.')
param principalIds string[]

@description('Built-in role definition GUID (e.g. ba92f5b4-... for Storage Blob Data Contributor).')
param roleDefinitionId string

@description('Short suffix used in the assignment GUID to keep it stable per role.')
param roleSuffix string

@description('Principal type.')
@allowed(['ServicePrincipal', 'User', 'Group'])
param principalType string = 'ServicePrincipal'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource ras 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(storageAccount.id, pid, roleSuffix)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: pid
    principalType: principalType
  }
}]
