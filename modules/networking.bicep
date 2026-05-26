// ==============================================================================
// Networking module — VNet, subnets, NSGs, private DNS zones
// ------------------------------------------------------------------------------
// Provisions a hub-ready network footprint for fully-private deployments:
//   - VNet with ACA infrastructure subnet (delegated) and a separate PE subnet
//   - NSGs with baseline rules
//   - All private DNS zones needed by AI Foundry, AI Search, Cosmos DB,
//     Key Vault, Storage (blob/queue) and ACR, linked to the VNet
// ==============================================================================

targetScope = 'resourceGroup'

// ──────────────────────────────────────────────────────────────────────────────
// Parameters
// ──────────────────────────────────────────────────────────────────────────────

@description('Azure region for the VNet and NSGs (DNS zones are global).')
param location string

@description('Environment name used in resource naming.')
param environmentName string

@description('Resource tags.')
param tags object = {}

@description('VNet address space.')
param vnetAddressPrefix string = '10.10.0.0/16'

@description('ACA infrastructure subnet CIDR (must be >= /27 for workload profile envs; /23 recommended).')
param acaInfraSubnetPrefix string = '10.10.0.0/23'

@description('Private endpoint subnet CIDR. /27 fits ~25 PEs.')
param privateEndpointSubnetPrefix string = '10.10.2.0/27'

@description('Optional Azure Bastion subnet CIDR. Set to empty string to skip.')
param bastionSubnetPrefix string = ''

// ──────────────────────────────────────────────────────────────────────────────
// Variables
// ──────────────────────────────────────────────────────────────────────────────

var vnetName = 'vnet-${environmentName}'
var acaNsgName = 'nsg-${environmentName}-aca'
var peNsgName = 'nsg-${environmentName}-pe'

// All Azure private DNS zones used by this stack. Zone names are global; the
// region only matters for the *resources* the PEs point at.
var privateDnsZoneNames = [
  'privatelink.cognitiveservices.azure.com' // AI Foundry — Document Intelligence, Vision, Content Safety, Language
  'privatelink.openai.azure.com'             // AI Foundry — Azure OpenAI plane
  'privatelink.services.ai.azure.com'        // AI Foundry — Projects / Agent Service
  'privatelink.search.windows.net'           // Azure AI Search
  'privatelink.documents.azure.com'          // Cosmos DB (SQL/Core API)
  'privatelink.vaultcore.azure.net'          // Key Vault
  'privatelink.blob.${environment().suffixes.storage}'  // Storage blob
  'privatelink.queue.${environment().suffixes.storage}' // Storage queue
  'privatelink.azurecr.io'                   // Azure Container Registry
]

// ──────────────────────────────────────────────────────────────────────────────
// NSGs
// ──────────────────────────────────────────────────────────────────────────────

resource acaNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: acaNsgName
  location: location
  tags: tags
  properties: {
    // ACA workload-profile environments mostly manage their own NSG rules.
    // We add a defensive allow for intra-VNet 443 to the PE subnet so the apps
    // can reach private endpoints.
    securityRules: [
      {
        name: 'Allow-VNet-Outbound-Https'
        properties: {
          priority: 200
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

resource peNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: peNsgName
  location: location
  tags: tags
  properties: {
    // NSG rules don't apply to the PE NIC itself by default, but applying a
    // baseline keeps Azure Policy / security baselines happy.
    securityRules: [
      {
        name: 'Allow-VNet-Inbound-Https'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// VNet + subnets
// ──────────────────────────────────────────────────────────────────────────────

var baseSubnets = [
  {
    name: 'snet-aca-infra'
    properties: {
      addressPrefix: acaInfraSubnetPrefix
      networkSecurityGroup: { id: acaNsg.id }
      delegations: [
        {
          name: 'aca-environment'
          properties: { serviceName: 'Microsoft.App/environments' }
        }
      ]
      privateEndpointNetworkPolicies: 'Disabled'
      privateLinkServiceNetworkPolicies: 'Enabled'
    }
  }
  {
    name: 'snet-pe'
    properties: {
      addressPrefix: privateEndpointSubnetPrefix
      networkSecurityGroup: { id: peNsg.id }
      privateEndpointNetworkPolicies: 'Disabled'
      privateLinkServiceNetworkPolicies: 'Enabled'
    }
  }
]

var bastionSubnet = empty(bastionSubnetPrefix) ? [] : [
  {
    // Name MUST be exactly 'AzureBastionSubnet' for Bastion to attach.
    name: 'AzureBastionSubnet'
    properties: {
      addressPrefix: bastionSubnetPrefix
    }
  }
]

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: concat(baseSubnets, bastionSubnet)
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Private DNS zones + VNet links
// ──────────────────────────────────────────────────────────────────────────────

resource privateDnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for zoneName in privateDnsZoneNames: {
  name: zoneName
  location: 'global'
  tags: tags
}]

resource privateDnsZoneVnetLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zoneName, i) in privateDnsZoneNames: {
  parent: privateDnsZones[i]
  name: '${vnetName}-link'
  location: 'global'
  tags: tags
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}]

// ──────────────────────────────────────────────────────────────────────────────
// Outputs
// ──────────────────────────────────────────────────────────────────────────────

@description('Resource ID of the VNet.')
output vnetId string = vnet.id

@description('Name of the VNet.')
output vnetName string = vnet.name

@description('Resource ID of the ACA infrastructure subnet.')
output acaInfraSubnetId string = '${vnet.id}/subnets/snet-aca-infra'

@description('Resource ID of the private endpoint subnet.')
output privateEndpointSubnetId string = '${vnet.id}/subnets/snet-pe'

@description('Map of private DNS zone names → resource IDs, for use by PE deployments.')
output privateDnsZoneIds object = {
  cognitiveServices: privateDnsZones[0].id
  openAi: privateDnsZones[1].id
  aiServices: privateDnsZones[2].id
  search: privateDnsZones[3].id
  cosmosSql: privateDnsZones[4].id
  keyVault: privateDnsZones[5].id
  storageBlob: privateDnsZones[6].id
  storageQueue: privateDnsZones[7].id
  containerRegistry: privateDnsZones[8].id
}
