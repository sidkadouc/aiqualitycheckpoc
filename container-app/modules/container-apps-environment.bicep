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

// VNET for production environment
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = if (envType == 'prod') {
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

// Container Apps Environment - Dev (no VNET)
resource containerAppsEnvironmentDev 'Microsoft.App/managedEnvironments@2024-03-01' = if (envType == 'dev') {
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
resource aspireDashboardDev 'Microsoft.App/managedEnvironments/dotNetComponents@2024-02-02-preview' = if (envType == 'dev') {
  parent: containerAppsEnvironmentDev
  name: 'aspire-dashboard'
  properties: {
    componentType: 'AspireDashboard'
  }
}

// Container Apps Environment - Prod (VNET integrated)
resource containerAppsEnvironmentProd 'Microsoft.App/managedEnvironments@2024-03-01' = if (envType == 'prod') {
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
      infrastructureSubnetId: vnet.properties.subnets[0].id
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
resource aspireDashboardProd 'Microsoft.App/managedEnvironments/dotNetComponents@2024-02-02-preview' = if (envType == 'prod') {
  parent: containerAppsEnvironmentProd
  name: 'aspire-dashboard'
  properties: {
    componentType: 'AspireDashboard'
  }
}

output id string = envType == 'prod' ? containerAppsEnvironmentProd.id : containerAppsEnvironmentDev.id
output name string = envType == 'prod' ? containerAppsEnvironmentProd.name : containerAppsEnvironmentDev.name
output defaultDomain string = envType == 'prod' ? containerAppsEnvironmentProd.properties.defaultDomain : containerAppsEnvironmentDev.properties.defaultDomain
