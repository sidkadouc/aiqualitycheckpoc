@description('Location for resources')
param location string

@description('Tags for resources')
param tags object

@description('Container registry name')
param registryName string

@description('ACR SKU. Premium is required for private endpoints.')
@allowed(['Basic', 'Standard', 'Premium'])
param sku string = 'Basic'

@description('Whether the registry is reachable from the public internet. Set to Disabled when fronting with a private endpoint (requires Premium SKU).')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccess string = 'Enabled'

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: publicNetworkAccess
  }
}

output name string = containerRegistry.name
output loginServer string = containerRegistry.properties.loginServer
output id string = containerRegistry.id
