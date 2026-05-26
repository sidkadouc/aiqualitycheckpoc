// ==============================================================================
// AI Foundry role assignment (by name) — wires N principal IDs to a given role
// on the AI Foundry account. Each assignment name is computed from the static
// `aiFoundryName` param so it's deploy-time-safe.
// ==============================================================================

targetScope = 'resourceGroup'

@description('Name of the existing AI Foundry (Cognitive Services AIServices) account.')
param aiFoundryName string

@description('Principal IDs to assign the role to.')
param principalIds string[]

@description('Built-in role definition GUID (e.g. a97b65f3-24c7-4388-baec-2e87135dc908 for Cognitive Services User).')
param roleDefinitionId string

@description('Short suffix used in the assignment GUID to keep it stable per role.')
param roleSuffix string

@description('Principal type.')
@allowed(['ServicePrincipal', 'User', 'Group'])
param principalType string = 'ServicePrincipal'

resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiFoundryName
}

resource ras 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(aiFoundry.id, pid, roleSuffix)
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: pid
    principalType: principalType
  }
}]
