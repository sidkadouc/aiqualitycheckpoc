// ==============================================================================
// Key Vault role assignment (by name)
// ==============================================================================

targetScope = 'resourceGroup'

@description('Name of the existing Key Vault.')
param keyVaultName string

@description('Principal IDs to grant the role to.')
param principalIds string[]

@description('Built-in role definition GUID (e.g. 4633458b-17de-408a-b874-0445c86b69e6 for Key Vault Secrets User).')
param roleDefinitionId string

@description('Short suffix used in the assignment GUID to keep it stable per role.')
param roleSuffix string

@description('Principal type.')
@allowed(['ServicePrincipal', 'User', 'Group'])
param principalType string = 'ServicePrincipal'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource ras 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(keyVault.id, pid, roleSuffix)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: pid
    principalType: principalType
  }
}]
