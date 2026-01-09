@description('Location for resources')
param location string

@description('Environment name')
param environmentName string

@description('Tags for resources')
param tags object

@description('Managed Identity name for AD admin')
param managedIdentityName string

@description('Managed Identity Principal ID for AD admin')
param managedIdentityPrincipalId string

@description('Key Vault name to store connection string')
param keyVaultName string

@description('Managed Identity Client ID')
param managedIdentityClientId string

@description('Current user principal ID (from deploying user) for SQL admin access')
param currentUserPrincipalId string = ''

@description('Current user login name (email) for SQL admin access')
param currentUserLogin string = ''

@description('SQL database name')
param databaseName string = 'AppDb'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: 'sql-${environmentName}-001'
  location: location
  tags: tags
  properties: {
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      azureADOnlyAuthentication: true
      login: !empty(currentUserLogin) ? currentUserLogin : managedIdentityName
      sid: !empty(currentUserPrincipalId) ? currentUserPrincipalId : managedIdentityPrincipalId
      tenantId: subscription().tenantId
      principalType: !empty(currentUserPrincipalId) ? 'User' : 'Application'
    }
  }
}

resource sqlFirewallRule 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: databaseName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
    tier: 'Basic'
    capacity: 5
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    maxSizeBytes: 2147483648
    catalogCollation: 'SQL_Latin1_General_CP1_CI_AS'
    zoneRedundant: false
  }
}

resource sqlConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'sql-connection-string'
  properties: {
    value: 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Initial Catalog=${databaseName};Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;Authentication=Active Directory Managed Identity;User Id=${managedIdentityClientId};'
  }
}

output fqdn string = sqlServer.properties.fullyQualifiedDomainName
output connectionStringSecretUri string = sqlConnectionStringSecret.properties.secretUri
