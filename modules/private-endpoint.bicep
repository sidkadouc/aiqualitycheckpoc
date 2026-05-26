// ==============================================================================
// Reusable private endpoint + DNS zone group
// ------------------------------------------------------------------------------
// Creates a single Private Endpoint targeting one sub-resource of a target
// service, and registers the per-zone A-record group(s) so DNS resolves to the
// PE NIC IP inside any VNet linked to the zone.
// ==============================================================================

targetScope = 'resourceGroup'

// ──────────────────────────────────────────────────────────────────────────────
// Parameters
// ──────────────────────────────────────────────────────────────────────────────

@description('Private endpoint name (must be unique within the resource group).')
@minLength(2)
@maxLength(80)
param name string

@description('Azure region for the PE (must match the subnet region).')
param location string

@description('Resource tags.')
param tags object = {}

@description('Resource ID of the target service.')
param targetResourceId string

@description('Sub-resource (groupId) on the target. e.g. "account", "vault", "blob", "sqlServer", "registry".')
param groupId string

@description('Resource ID of the subnet that will host the PE NIC. Must have privateEndpointNetworkPolicies = Disabled.')
param subnetId string

@description('Private DNS zone resource IDs to wire into the zone group. Pass 1+ when the service needs multiple FQDNs (e.g. AI Foundry needs 3).')
param privateDnsZoneIds string[]

// ──────────────────────────────────────────────────────────────────────────────
// Resources
// ──────────────────────────────────────────────────────────────────────────────

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [
      {
        name: '${name}-conn'
        properties: {
          privateLinkServiceId: targetResourceId
          groupIds: [groupId]
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [for (zoneId, i) in privateDnsZoneIds: {
      name: 'config${i}'
      properties: {
        privateDnsZoneId: zoneId
      }
    }]
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Outputs
// ──────────────────────────────────────────────────────────────────────────────

@description('Resource ID of the private endpoint.')
output id string = privateEndpoint.id

@description('Name of the private endpoint.')
output name string = privateEndpoint.name
