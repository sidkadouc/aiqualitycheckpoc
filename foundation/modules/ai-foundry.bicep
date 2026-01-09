// ==============================================================================
// Azure AI Foundry (Azure OpenAI) Module
// ==============================================================================
// Deploys Azure AI Foundry with models:
// - GPT-4.1 (gpt-4.1-2025-04-14)
// - GPT-4o (multimodal)
// - text-embedding-3-large
// ==============================================================================

@description('Name of the AI Foundry resource')
param aiFoundryName string

@description('Location for the AI Foundry resource - defaults to westeurope for model availability')
param location string = 'swedencentral'

@description('Tags for the resources')
param tags object = {}

@description('Key Vault name for storing secrets')
param keyVaultName string

@description('Principal ID of the managed identity to grant access')
param managedIdentityPrincipalId string

@description('Principal ID of the AI Search service to grant access')
param aiSearchPrincipalId string = ''

// ==============================================================================
// AI Foundry Resource (Azure AI Services)
// ==============================================================================

resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: aiFoundryName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  kind: 'AIServices'
  properties: {
    // Required to work in AI Foundry
    allowProjectManagement: true
    // Defines developer API endpoint subdomain
    customSubDomainName: aiFoundryName
    // Use managed identity instead of keys
    disableLocalAuth: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// ==============================================================================
// AI Foundry Project
// ==============================================================================

resource aiProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  name: '${aiFoundryName}-proj'
  parent: aiFoundry
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

// ==============================================================================
// Model Deployments
// ==============================================================================

// GPT-4.1 Deployment
resource gpt41Deployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiFoundry
  name: 'gpt-4.1'
  sku: {
    capacity: 50
    name: 'DataZoneStandard'
  }
  properties: {
    model: {
      name: 'gpt-4.1'
      format: 'OpenAI'
    }
  }
}

// GPT-4o Deployment (Multimodal)
resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiFoundry
  name: 'gpt-4o'
  sku: {
    capacity: 100
    name: 'Standard'
  }
  properties: {
    model: {
      name: 'gpt-4o'
      format: 'OpenAI'
    }
  }
  dependsOn: [gpt41Deployment]
}

// Text Embedding 3 Large Deployment
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiFoundry
  name: 'text-embedding-3-large'
  sku: {
    capacity: 100
    name: 'Standard'
  }
  properties: {
    model: {
      name: 'text-embedding-3-large'
      format: 'OpenAI'
    }
  }
  dependsOn: [gpt4oDeployment]
}

// ==============================================================================
// Role Assignments
// ==============================================================================

// Reference to Key Vault
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// Role: Cognitive Services OpenAI User for Managed Identity
// Role ID: 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd
resource managedIdentityOpenAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiFoundry.id, managedIdentityPrincipalId, 'CognitiveServicesOpenAIUser')
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Role: Cognitive Services OpenAI Contributor for Managed Identity
// Role ID: a001fd3d-188f-4b5d-821b-7da978bf7442
resource managedIdentityOpenAIContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiFoundry.id, managedIdentityPrincipalId, 'CognitiveServicesOpenAIContributor')
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a001fd3d-188f-4b5d-821b-7da978bf7442')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Role: Azure AI Developer for Managed Identity (required for AI Foundry Agents)
// Role ID: 64702f94-c441-49e6-a78b-ef80e0188fee
resource managedIdentityAIDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiFoundry.id, managedIdentityPrincipalId, 'AzureAIDeveloper')
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '64702f94-c441-49e6-a78b-ef80e0188fee')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Role: Azure AI User for Managed Identity (required for AI Foundry Projects - agents read/write)
// Role ID: 53ca6127-db72-4b80-b1b0-d745d6d5456d
resource managedIdentityAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiFoundry.id, managedIdentityPrincipalId, 'AzureAIUser')
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Role: Cognitive Services OpenAI User for AI Search (for vectorization)
// Role ID: 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd
resource aiSearchOpenAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(aiSearchPrincipalId)) {
  name: guid(aiFoundry.id, aiSearchPrincipalId, 'CognitiveServicesOpenAIUser-Search')
  scope: aiFoundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: aiSearchPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ==============================================================================
// Key Vault Secrets
// ==============================================================================

// Store AI Foundry endpoint in Key Vault
resource aiFoundryEndpointSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-openai-endpoint'
  properties: {
    value: aiFoundry.properties.endpoint
  }
}

// Store AI Foundry key in Key Vault (for backward compatibility)
resource aiFoundryKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-openai-key'
  properties: {
    value: aiFoundry.listKeys().key1
  }
}

// ==============================================================================
// Outputs
// ==============================================================================

@description('The name of the AI Foundry resource')
output name string = aiFoundry.name

@description('The endpoint of the AI Foundry resource')
output endpoint string = aiFoundry.properties.endpoint

@description('The principal ID of the AI Foundry system-assigned identity')
output principalId string = aiFoundry.identity.principalId

@description('The resource ID of the AI Foundry resource')
output resourceId string = aiFoundry.id

@description('Content Understanding endpoint (services.ai.azure.com domain)')
output contentUnderstandingEndpoint string = 'https://${aiFoundry.name}.services.ai.azure.com'

@description('GPT-4.1 deployment name')
output gpt41DeploymentName string = gpt41Deployment.name

@description('GPT-4o deployment name')
output gpt4oDeploymentName string = gpt4oDeployment.name

@description('Text Embedding 3 Large deployment name')
output embeddingDeploymentName string = embeddingDeployment.name
