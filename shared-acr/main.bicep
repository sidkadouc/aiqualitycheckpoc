targetScope = 'subscription'

@description('Environment name')
param environmentName string

@description('Primary location for resources')
param location string

@description('Application name for resource naming')
param applicationName string = 'myapp'

@description('Existing ACR resource group (if reusing)')
param acrResourceGroupName string = ''

@description('Existing ACR name (if reusing)')
param existingAcrName string = ''

// Tags
var tags = {
  'azd-env-name': environmentName
  application: applicationName
  purpose: 'shared-acr'
  SecurityControl: 'Ignore'
}

// Shared ACR Resource Group
var acrRgName = !empty(acrResourceGroupName) ? acrResourceGroupName : 'rg-${applicationName}-acr-shared'
var acrName = 'cr${applicationName}shared'

resource acrRg 'Microsoft.Resources/resourceGroups@2024-03-01' = if (empty(existingAcrName)) {
  name: acrRgName
  location: location
  tags: tags
}

module containerRegistry 'modules/container-registry.bicep' = if (empty(existingAcrName)) {
  name: 'container-registry'
  scope: acrRg
  params: {
    location: location
    tags: tags
    registryName: acrName
  }
}

// Outputs
output ACR_RESOURCE_GROUP_NAME string = !empty(existingAcrName) ? acrRgName : acrRg.name
output AZURE_CONTAINER_REGISTRY_NAME string = !empty(existingAcrName) ? existingAcrName : acrName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = !empty(existingAcrName) ? '${existingAcrName}.azurecr.io' : '${acrName}.azurecr.io'
