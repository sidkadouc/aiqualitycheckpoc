// ==============================================================================
// ACR role assignment (by name) — supports multiple principals + multiple roles
// ==============================================================================

targetScope = 'resourceGroup'

@description('Name of the existing Container Registry.')
param containerRegistryName string

@description('Principal IDs to grant the role to.')
param principalIds string[]

@description('Built-in role definition GUID (e.g. 7f951dda-... for AcrPull, 8311e382-... for AcrPush).')
param roleDefinitionId string

@description('Short suffix used in the assignment GUID to keep it stable per role.')
param roleSuffix string

@description('Principal type.')
@allowed(['ServicePrincipal', 'User', 'Group'])
param principalType string = 'ServicePrincipal'

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: containerRegistryName
}

resource ras 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(containerRegistry.id, pid, roleSuffix)
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: pid
    principalType: principalType
  }
}]
