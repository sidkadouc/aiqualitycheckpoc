@description('Location for all resources')
param location string

@description('Environment name')
param environmentName string

@description('Tags for resources')
param tags object

// Azure AI Configuration
param azureOpenAIEndpoint string
@secure()
param azureOpenAIKey string
param azureAISearchUri string
@secure()
param azureAISearchSecret string
param azureOpenAIChatModel string
param embeddingModel string



@secure()
param apiKey string
@secure()
param serviceApiKey string

// SQL Admin Password - auto-generated secure password
@secure()
param sqlAdminPassword string = 'P@ss${uniqueString(newGuid())}!Wd1'

// Database seeding
param enableDatabaseSeeding bool

// =============================================================================
// User-Assigned Managed Identity
// =============================================================================
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-jurisimple-${environmentName}-001'
  location: location
  tags: tags
}

// =============================================================================
// Log Analytics Workspace
// =============================================================================
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-jurisimple-${environmentName}-001'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// =============================================================================
// Application Insights
// =============================================================================
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-aicheck-${environmentName}-001'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// =============================================================================
// Key Vault
// =============================================================================
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-aicheck-${environmentName}'
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
  }
}

resource apiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'api-key'
  properties: {
    value: apiKey
  }
}

resource serviceApiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'service-api-key'
  properties: {
    value: serviceApiKey
  }
}

resource openAIKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-openai-key'
  properties: {
    value: !empty(azureOpenAIKey) ? azureOpenAIKey : 'placeholder-key'
  }
}

resource aiSearchKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-aisearch-key'
  properties: {
    value: !empty(azureAISearchSecret) ? azureAISearchSecret : 'placeholder-key'
  }
}

// Key Vault access for managed identity
resource kvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, managedIdentity.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') // Key Vault Secrets User
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// =============================================================================
// Azure SQL Server and Database
// =============================================================================
resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: 'sql-jurisimple-${environmentName}-001'
  location: location
  tags: tags
  properties: {
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      azureADOnlyAuthentication: true
      login: managedIdentity.name
      sid: managedIdentity.properties.principalId
      tenantId: subscription().tenantId
      principalType: 'Application'
    }
  }
}

// Allow Azure services to access SQL Server
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
  name: 'JuriSimpleDb'
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

// Store SQL connection string in Key Vault (using Managed Identity)
resource sqlConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'sql-connection-string'
  properties: {
    value: 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Initial Catalog=JuriSimpleDb;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;Authentication=Active Directory Managed Identity;User Id=${managedIdentity.properties.clientId};'
  }
}

// =============================================================================
// Storage Account
// =============================================================================
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'staicheck${environmentName}'
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource documentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'documents'
  properties: {
    publicAccess: 'None'
  }
}

// Storage Blob Data Contributor role for managed identity
resource storageBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, managedIdentity.id, 'StorageBlobDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe') // Storage Blob Data Contributor
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Store storage connection string in Key Vault
resource storageConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'storage-connection-string'
  properties: {
    value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};EndpointSuffix=${environment().suffixes.storage}'
  }
}

// =============================================================================
// Azure Cosmos DB
// =============================================================================
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: 'cosmos-aicheck-${environmentName}-001'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAclBypass: 'AzureServices'
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    minimalTlsVersion: 'Tls12'
  }
}

// Cosmos DB SQL Database for discussions
resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmosAccount
  name: 'discussions'
  properties: {
    resource: {
      id: 'discussions'
    }
  }
}

// Cosmos DB Container for conversations
resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDatabase
  name: 'conversations'
  properties: {
    resource: {
      id: 'conversations'
      partitionKey: {
        paths: [
          '/userId'
        ]
        kind: 'Hash'
      }
      indexingPolicy: {
        automatic: true
        indexingMode: 'consistent'
      }
      defaultTtl: -1
    }
  }
}

// Cosmos DB SQL Role Definition for data access
resource cosmosDataContributorRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, managedIdentity.id, 'CosmosDBDataContributor')
  properties: {
    roleName: 'Cosmos DB Data Contributor'
    type: 'CustomRole'
    assignableScopes: [
      cosmosAccount.id
    ]
    permissions: [
      {
        dataActions: [
          'Microsoft.DocumentDB/databaseAccounts/readMetadata'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/*'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/*'
        ]
      }
    ]
  }
}

// Cosmos DB SQL Role Assignment for managed identity
resource cosmosRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, managedIdentity.id, 'CosmosDBRoleAssignment')
  properties: {
    roleDefinitionId: cosmosDataContributorRole.id
    principalId: managedIdentity.properties.principalId
    scope: cosmosAccount.id
  }
}

// Store Cosmos DB endpoint in Key Vault (using Managed Identity - no keys needed)
resource cosmosEndpointSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'cosmos-endpoint'
  properties: {
    value: cosmosAccount.properties.documentEndpoint
  }
}

// Store Cosmos DB key in Key Vault (for fallback scenarios where Managed Identity is not supported)
resource cosmosKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'cosmos-key'
  properties: {
    value: cosmosAccount.listKeys().primaryMasterKey
  }
}

// =============================================================================
// Container Registry
// =============================================================================
resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: 'craicheck${environmentName}'
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

// AcrPull role assignment for managed identity
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, managedIdentity.id, 'AcrPull')
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// =============================================================================
// Container Apps Environment
// =============================================================================
resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-aicheck-${environmentName}-001'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
  }
}

// =============================================================================
// Backend API Container App
// =============================================================================
resource backendApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-aicheck-backend'
  location: location
  tags: union(tags, { 'azd-service-name': 'backend-api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        corsPolicy: {
          allowedOrigins: ['*']
          allowedMethods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
          allowedHeaders: ['*']
          allowCredentials: false
        }
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: managedIdentity.id
        }
      ]
      secrets: [
        {
          name: 'sql-connection-string'
          keyVaultUrl: sqlConnectionStringSecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'storage-connection-string'
          keyVaultUrl: storageConnectionStringSecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'api-key'
          keyVaultUrl: apiKeySecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'service-api-key'
          keyVaultUrl: serviceApiKeySecret.properties.secretUri
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'backend-api'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'ASPNETCORE_ENVIRONMENT'
              value: 'Production'
            }
            {
              name: 'ConnectionStrings__DefaultConnection'
              secretRef: 'sql-connection-string'
            }
            {
              name: 'ConnectionStrings__BlobStorage'
              secretRef: 'storage-connection-string'
            }
            {
              name: 'JWT__Key'
              secretRef: 'jwt-key'
            }
            {
              name: 'JWT__Issuer'
              secretRef: 'jwt-issuer'
            }
            {
              name: 'JWT__Audience'
              secretRef: 'jwt-audience'
            }
            {
              name: 'ApiKey__Key'
              secretRef: 'api-key'
            }
            {
              name: 'ApiKey__ServiceApiKey'
              secretRef: 'service-api-key'
            }
            {
              name: 'ENABLE_DATABASE_SEEDING'
              value: string(enableDatabaseSeeding)
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'FilesStorage__ContainerName'
              value: 'documents'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
  dependsOn: [
    acrPullRole
    kvSecretsUserRole
  ]
}

// =============================================================================
// Chat API Container App
// =============================================================================
resource chatApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-aicheck-chat'
  location: location
  tags: union(tags, { 'azd-service-name': 'chat-api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        corsPolicy: {
          allowedOrigins: ['*']
          allowedMethods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
          allowedHeaders: ['*']
          allowCredentials: false
        }
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: managedIdentity.id
        }
      ]
      secrets: [
        {
          name: 'sql-connection-string'
          keyVaultUrl: sqlConnectionStringSecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'storage-connection-string'
          keyVaultUrl: storageConnectionStringSecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'jwt-key'
          keyVaultUrl: jwtKeySecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'jwt-issuer'
          keyVaultUrl: jwtIssuerSecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'jwt-audience'
          keyVaultUrl: jwtAudienceSecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'api-key'
          keyVaultUrl: apiKeySecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'service-api-key'
          keyVaultUrl: serviceApiKeySecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'azure-openai-key'
          keyVaultUrl: openAIKeySecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'azure-aisearch-key'
          keyVaultUrl: aiSearchKeySecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'cosmos-endpoint'
          keyVaultUrl: cosmosEndpointSecret.properties.secretUri
          identity: managedIdentity.id
        }
        {
          name: 'cosmos-key'
          keyVaultUrl: cosmosKeySecret.properties.secretUri
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'chat-api'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'ASPNETCORE_ENVIRONMENT'
              value: 'Production'
            }
            {
              name: 'ConnectionStrings__DefaultConnection'
              secretRef: 'sql-connection-string'
            }
            {
              name: 'ConnectionStrings__BlobStorage'
              secretRef: 'storage-connection-string'
            }
            {
              name: 'CosmosDb__Endpoint'
              secretRef: 'cosmos-endpoint'
            }
            {
              name: 'CosmosDb__Key'
              secretRef: 'cosmos-key'
            }
            {
              name: 'CosmosDb__DatabaseName'
              value: 'discussions'
            }
            {
              name: 'CosmosDb__ContainerName'
              value: 'conversations'
            }
            {
              name: 'CosmosDb__CallSessionsContainerName'
              value: 'callsessions'
            }
            {
              name: 'CosmosDb__TranscriptionsContainerName'
              value: 'transcriptions'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: managedIdentity.properties.clientId
            }
            {
              name: 'JWT__Key'
              secretRef: 'jwt-key'
            }
            {
              name: 'JWT__Issuer'
              secretRef: 'jwt-issuer'
            }
            {
              name: 'JWT__Audience'
              secretRef: 'jwt-audience'
            }
            {
              name: 'ApiKey__Key'
              secretRef: 'api-key'
            }
            {
              name: 'ApiKey__ServiceApiKey'
              secretRef: 'service-api-key'
            }
            {
              name: 'AzureOpenAI__Endpoint'
              value: azureOpenAIEndpoint
            }
            {
              name: 'AzureOpenAI__Key'
              secretRef: 'azure-openai-key'
            }
            {
              name: 'AzureOpenAI__ChatModel'
              value: azureOpenAIChatModel
            }
            {
              name: 'AzureOpenAI__EmbeddingModel'
              value: embeddingModel
            }
            {
              name: 'AzureAISearch__Uri'
              value: azureAISearchUri
            }
            {
              name: 'AzureAISearch__Key'
              secretRef: 'azure-aisearch-key'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
  dependsOn: [
    acrPullRole
    kvSecretsUserRole
  ]
}

// =============================================================================
// Outputs
// =============================================================================
output containerRegistryEndpoint string = containerRegistry.properties.loginServer
output containerAppsEnvironmentId string = containerAppsEnvironment.id
output chatApiUrl string = 'https://${chatApi.properties.configuration.ingress.fqdn}'
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output storageAccountName string = storageAccount.name
output keyVaultName string = keyVault.name
output managedIdentityId string = managedIdentity.id
output cosmosAccountName string = cosmosAccount.name
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint
