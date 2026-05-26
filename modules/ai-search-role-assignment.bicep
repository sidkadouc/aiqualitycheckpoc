// ==============================================================================
// AI Search role assignment (by name)
// ==============================================================================

targetScope = 'resourceGroup'

@description('Name of the existing AI Search service.')
param searchServiceName string

@description('Principal IDs to assign the role to.')
param principalIds string[]

@description('Built-in role definition GUID (Search Index Data Reader/Contributor, Search Service Contributor, etc.).')
param roleDefinitionId string

@description('Short suffix used in the assignment GUID to keep it stable per role.')
param roleSuffix string

@description('Principal type.')
@allowed(['ServicePrincipal', 'User', 'Group'])
param principalType string = 'ServicePrincipal'

resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchServiceName
}

resource ras 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(searchService.id, pid, roleSuffix)
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: pid
    principalType: principalType
  }
}]
