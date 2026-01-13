@description('Location for resources')
param location string

@description('Environment name')
param environmentName string

@description('Tags for resources')
param tags object

@description('Logic App Principal ID for access policies')
param logicAppPrincipalId string

@description('Logic App name for setting connection app settings')
param logicAppName string

// Reference existing Logic App to update app settings
resource logicApp 'Microsoft.Web/sites@2023-12-01' existing = {
  name: logicAppName
}

// Office 365 API Connection
resource office365Connection 'Microsoft.Web/connections@2016-06-01' = {
  name: 'office365-${environmentName}'
  location: location
  tags: tags
  properties: {
    displayName: 'Office 365 Email Connection'
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
    }
  }
}

// Access policy for Logic App to use Office 365 connection
resource office365AccessPolicy 'Microsoft.Web/connections/accessPolicies@2016-06-01' = {
  parent: office365Connection
  name: logicAppPrincipalId
  location: location
  properties: {
    principal: {
      type: 'ActiveDirectory'
      identity: {
        tenantId: subscription().tenantId
        objectId: logicAppPrincipalId
      }
    }
  }
}

// Azure Blob Storage API Connection (using Managed Identity)
resource azureBlobConnection 'Microsoft.Web/connections@2016-06-01' = {
  name: 'azureblob-${environmentName}'
  location: location
  tags: tags
  properties: {
    displayName: 'Azure Blob Storage Connection'
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'azureblob')
    }
    // Empty parameterValues - authentication handled via ManagedServiceIdentity in connections.json
    parameterValues: {}
  }
}

// Access policy for Logic App to use Blob connection
resource azureBlobAccessPolicy 'Microsoft.Web/connections/accessPolicies@2016-06-01' = {
  parent: azureBlobConnection
  name: logicAppPrincipalId
  location: location
  properties: {
    principal: {
      type: 'ActiveDirectory'
      identity: {
        tenantId: subscription().tenantId
        objectId: logicAppPrincipalId
      }
    }
  }
}

// Update Logic App with connection app settings
resource logicAppConnectionSettings 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: logicApp
  name: 'appsettings'
  properties: {
    // Preserve existing settings by referencing them
    FUNCTIONS_EXTENSION_VERSION: '~4'
    FUNCTIONS_WORKER_RUNTIME: 'dotnet'
    APP_KIND: 'workflowApp'
    AzureFunctionsJobHost__extensionBundle__id: 'Microsoft.Azure.Functions.ExtensionBundle.Workflows'
    AzureFunctionsJobHost__extensionBundle__version: '[1.*, 2.0.0)'
    // Connection settings - runtime URLs are populated after deployment via script
    OFFICE365_CONNECTION_NAME: office365Connection.name
    AZUREBLOB_CONNECTION_NAME: azureBlobConnection.name
  }
  dependsOn: [
    office365AccessPolicy
    azureBlobAccessPolicy
  ]
}


output office365ConnectionId string = office365Connection.id
output office365ConnectionName string = office365Connection.name
output office365ConnectionRuntimeUrl string = 'RETRIEVE_AFTER_DEPLOYMENT'
output azureBlobConnectionId string = azureBlobConnection.id
output azureBlobConnectionName string = azureBlobConnection.name
output azureBlobConnectionRuntimeUrl string = 'RETRIEVE_AFTER_DEPLOYMENT'
