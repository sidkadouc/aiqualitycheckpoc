@description('Location for resources')
param location string

@description('Environment name')
param environmentName string

@description('Tags for resources')
param tags object

@description('Managed Identity Principal ID for RBAC')
param managedIdentityPrincipalId string

// Secrets - will be auto-generated if not provided

@secure()
param apiKey string = ''
@secure()
param serviceApiKey string = ''
@secure()
param azureOpenAIKey string = ''
@secure()
param azureAISearchSecret string = ''
@secure()
param emailClientId string = ''
@secure()
param emailClientSecret string = ''
@secure()
param acsConnectionString string = ''

// Parameters for auto-generated secrets (newGuid can only be used as parameter default)



// Use provided secrets or generate from seeds


resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${environmentName}'
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

// Only create/update OpenAI key if a value is explicitly provided
resource openAIKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(azureOpenAIKey)) {
  parent: keyVault
  name: 'azure-openai-key'
  properties: {
    value: azureOpenAIKey
  }
}

// Note: azure-aisearch-key is created by ai-search.bicep module with the actual admin key
// Only create placeholder if external AI Search URI is provided (not using self-hosted AI Search)
resource aiSearchKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(azureAISearchSecret)) {
  parent: keyVault
  name: 'azure-aisearch-key-external'
  properties: {
    value: azureAISearchSecret
  }
}

// Email Client ID for Graph API
resource emailClientIdSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(emailClientId)) {
  parent: keyVault
  name: 'email-client-id'
  properties: {
    value: emailClientId
  }
}

// Email Client Secret for Graph API
resource emailClientSecretSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(emailClientSecret)) {
  parent: keyVault
  name: 'email-client-secret'
  properties: {
    value: emailClientSecret
  }
}

// Azure Communication Services connection string
resource acsConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(acsConnectionString)) {
  parent: keyVault
  name: 'acs-connection-string'
  properties: {
    value: acsConnectionString
  }
}

// Key Vault Secrets User role for managed identity
resource kvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, managedIdentityPrincipalId, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
