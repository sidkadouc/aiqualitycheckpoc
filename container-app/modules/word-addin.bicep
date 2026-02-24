// ─────────────────────────────────────────────────────────────────────
// word-addin.bicep — OECD Word Add-in (static SPA via nginx)
// Container App serving the built Word add-in assets.
// ─────────────────────────────────────────────────────────────────────

@description('Location for resources')
param location string

@description('Tags for resources')
param tags object

@description('Container Apps Environment ID')
param containerAppsEnvironmentId string

@description('Managed Identity ID')
param managedIdentityId string

@description('Managed Identity Client ID')
param managedIdentityClientId string

@description('Container Registry Endpoint')
param containerRegistryEndpoint string

@description('Application Insights Connection String')
param appInsightsConnectionString string

@description('Environment type (dev or prod)')
param envType string = 'dev'

@description('Application name for service naming')
param applicationName string = 'oecd-quality'

@description('Quality API URL (for add-in backend calls)')
param qualityApiUrl string

// Container image
@description('Container image name (without tag)')
param imageName string = 'word-addin'

@description('Container image tag')
param imageTag string = 'latest'

var serviceName = '${applicationName}-word-addin'

resource wordAddin 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-word-addin'
  location: location
  tags: union(tags, { 'azd-service-name': 'word-addin' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        corsPolicy: {
          allowedOrigins: ['*']
          allowedMethods: ['GET', 'OPTIONS']
          allowedHeaders: ['*']
          allowCredentials: false
        }
      }
      registries: [
        {
          server: containerRegistryEndpoint
          identity: managedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'word-addin'
          image: '${containerRegistryEndpoint}/${imageName}:${imageTag}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'QUALITY_API_URL'
              value: qualityApiUrl
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: serviceName
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
}

output name string = wordAddin.name
output url string = 'https://${wordAddin.properties.configuration.ingress.fqdn}'
