@description('Location for resources')
param location string

@description('Environment name')
param environmentName string

@description('Environment type (dev or prod)')
param envType string

@description('Tags for resources')
param tags object

@description('Log Analytics Customer ID')
param logAnalyticsCustomerId string

@secure()
@description('Log Analytics Shared Key')
param logAnalyticsSharedKey string

@description('Application Insights Connection String')
param appInsightsConnectionString string

@description('Resource ID of an existing ACA infrastructure subnet (from the foundation networking module). When provided, the ACA env is VNet-integrated against this subnet and the legacy per-env VNet is skipped.')
param existingInfrastructureSubnetId string = ''

// Computed purely from input params so `if (...)` conditions can be evaluated
// at the start of the deployment (Bicep requires this).
var useExternalSubnet = !empty(existingInfrastructureSubnetId)
var useLegacyVnet = envType == 'prod' && !useExternalSubnet
var useVnet = useExternalSubnet || useLegacyVnet

// Legacy per-env VNet — created only when envType=prod and no external subnet is supplied
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = if (useLegacyVnet) {
  name: 'vnet-${environmentName}-001'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.0.0.0/16']
    }
    subnets: [
      {
        name: 'snet-container-apps'
        properties: {
          addressPrefix: '10.0.0.0/23'
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

var infraSubnetId = useExternalSubnet
  ? existingInfrastructureSubnetId
  : (useLegacyVnet ? vnet!.properties.subnets[0].id : '')

// Container Apps Environment - Dev (no VNET)
resource containerAppsEnvironmentDev 'Microsoft.App/managedEnvironments@2024-03-01' = if (envType == 'dev' && !useVnet) {
  name: 'cae-${environmentName}-001'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
    daprAIConnectionString: appInsightsConnectionString
    zoneRedundant: false
  }
}

// Aspire Dashboard for Dev environment
resource aspireDashboardDev 'Microsoft.App/managedEnvironments/dotNetComponents@2024-02-02-preview' = if (envType == 'dev' && !useVnet) {
  parent: containerAppsEnvironmentDev
  name: 'aspire-dashboard'
  properties: {
    componentType: 'AspireDashboard'
  }
}

// Container Apps Environment - Prod or VNet-integrated
resource containerAppsEnvironmentProd 'Microsoft.App/managedEnvironments@2024-03-01' = if (useVnet) {
  name: 'cae-${environmentName}-001'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
    daprAIConnectionString: appInsightsConnectionString
    vnetConfiguration: {
      infrastructureSubnetId: infraSubnetId
      internal: false // Set to true for fully private environment
    }
    zoneRedundant: true
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// Aspire Dashboard for Prod environment
resource aspireDashboardProd 'Microsoft.App/managedEnvironments/dotNetComponents@2024-02-02-preview' = if (useVnet) {
  parent: containerAppsEnvironmentProd
  name: 'aspire-dashboard'
  properties: {
    componentType: 'AspireDashboard'
  }
}

output id string = useVnet ? containerAppsEnvironmentProd!.id : containerAppsEnvironmentDev!.id
output name string = useVnet ? containerAppsEnvironmentProd!.name : containerAppsEnvironmentDev!.name
output defaultDomain string = useVnet ? containerAppsEnvironmentProd!.properties.defaultDomain : containerAppsEnvironmentDev!.properties.defaultDomain
