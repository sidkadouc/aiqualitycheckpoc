@description('Container Registry Name')
param containerRegistryName string

@description('Managed Identity Principal ID')
param managedIdentityPrincipalId string

@description('Environment type (dev or prod)')
param envType string

// Reference existing ACR
resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: containerRegistryName
}

// Role Definition IDs
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'  // AcrPull
var acrPushRoleId = '8311e382-0749-4cb8-b61a-304f252e45ec'  // AcrPush

// AcrPull role - always assigned (both dev and prod need to pull images)
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, managedIdentityPrincipalId, 'AcrPull')
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// AcrPush role - only for dev environments (prod only pulls, doesn't push)
resource acrPushRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (envType == 'dev') {
  name: guid(containerRegistry.id, managedIdentityPrincipalId, 'AcrPush')
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPushRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
