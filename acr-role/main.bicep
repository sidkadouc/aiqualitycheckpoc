targetScope = 'subscription'

@description('Environment name')
param environmentName string

@description('Environment type (dev or prod)')
param envType string = 'dev'

@description('ACR Resource Group Name')
param acrResourceGroupName string

@description('Container Registry Name')
param containerRegistryName string

@description('Managed Identity Principal ID')
param managedIdentityPrincipalId string

// ACR Resource Group
resource acrRg 'Microsoft.Resources/resourceGroups@2024-03-01' existing = {
  name: acrResourceGroupName
}

// ACR Role Assignment Module
module acrRoleAssignment 'modules/acr-role-assignment.bicep' = {
  name: 'acr-role-assignment-${environmentName}'
  scope: acrRg
  params: {
    containerRegistryName: containerRegistryName
    managedIdentityPrincipalId: managedIdentityPrincipalId
    envType: envType
  }
}

// Outputs
output ACR_ROLE_ASSIGNED bool = true
