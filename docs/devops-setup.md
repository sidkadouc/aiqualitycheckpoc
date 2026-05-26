# Azure DevOps setup — one-time

The two pipelines in [.azure-pipelines/](../.azure-pipelines/) are ready to import. Before they can run, you need to provision a few things in your Azure DevOps project.

## 1. Pre-create the Azure resource group

```powershell
az group create -n rg-aiquality-dev -l francecentral
```

This is the only manual Azure step. Everything else (VNet, KV, ACR, Foundry, Cosmos, Storage, ACA env, apps, role assignments, private endpoints) is created by the bicep.

## 2. Service connection (Azure Resource Manager)

> **Recommended**: Workload Identity Federation (no secrets, OIDC-based trust between ADO and Entra ID).

1. **Project settings → Service connections → New service connection → Azure Resource Manager**
2. Authentication method: **Workload identity federation (automatic)**
3. Scope level: **Subscription** (or `Resource Group` scoped to `rg-aiquality-dev` — preferred for blast-radius)
4. Subscription: pick the one containing `rg-aiquality-dev`
5. Resource group: `rg-aiquality-dev`
6. Service connection name: **`azure-aiquality-dev-sc`** (must match the variable group value below)
7. Grant access permission to all pipelines: yes (or scope explicitly later)

### RBAC the service principal needs

Once the connection is created, ADO provisions a service principal/MI behind the scenes. Grant it on the **resource group**:

| Role | Why |
|---|---|
| `Contributor` | Deploy/update any resource in the RG |
| `User Access Administrator` | Bicep creates role assignments (SAMI → Cosmos, Foundry, etc.) |

Or simply `Owner` on the RG (covers both).

```powershell
$spnObjectId = '<copy from the service connection in ADO UI: Manage Service Principal → Object ID>'
az role assignment create --role 'Contributor' --assignee $spnObjectId --scope /subscriptions/<sub>/resourceGroups/rg-aiquality-dev
az role assignment create --role 'User Access Administrator' --assignee $spnObjectId --scope /subscriptions/<sub>/resourceGroups/rg-aiquality-dev
```

The apps pipeline uses `az acr build`, which runs inside Azure ACR Tasks — `Contributor` on the RG already covers the registry, no extra perms needed.

## 3. Variable group `aiquality-dev`

**Pipelines → Library → + Variable group → Name: `aiquality-dev`**

| Name | Value |
|---|---|
| `azureSubscriptionConnection` | `azure-aiquality-dev-sc` |
| `resourceGroupName` | `rg-aiquality-dev` |
| `environmentName` | `dev` |
| `location` | `francecentral` |
| `aiFoundryLocation` | `swedencentral` |
| `acrName` | `crdevu3c33cd4fa3hm` *(replace with the actual ACR name from `az acr list -g rg-aiquality-dev`)* |
| `pdfPipelineJobName` | `job-pdf-pipeline` |

Save. Then **Pipeline permissions → Grant access to both pipelines.**

> `acrName` only exists after the first infra deployment. Run the **infra-deploy** pipeline once, grab the value from the deployment outputs (`AZURE_CONTAINER_REGISTRY_NAME`), and update the variable group before running the apps pipeline.

## 4. Environment `aiquality-dev` (optional — for approval gates)

**Pipelines → Environments → New environment → Name: `aiquality-dev`** (matches the `environment:` field in the YAMLs)

Add an **Approval check** under "Approvals and checks" if you want a human gate before each deploy. Without checks, the deploy stage runs automatically when `main` is updated.

## 5. Import the YAML pipelines

For each YAML file:

1. **Pipelines → New pipeline**
2. Where is your code? — **GitHub** (since this repo lives on GitHub)
3. Authorize the **GitHub service connection** if first time
4. Pick the repo, then **Existing Azure Pipelines YAML file**
5. Path: `/.azure-pipelines/infra-deploy.yml` (then repeat for `apps-deploy.yml`)
6. Save (no run yet) and **rename** them to `aiquality-infra` and `aiquality-apps` for clarity

## 6. First deploy order

```text
1. aiquality-infra              ← deploys empty-image ACAs (image pull fails on first round, expected)
2. (Optional) update acrName    in the variable group with the real ACR
3. aiquality-apps               ← builds images via ACR Tasks, then redeploys with the new tag
4. (apps pipeline smoke-tests   the pdf-pipeline job automatically in the last stage)
```

After the second run, both pipelines run incrementally on every push to `main`:
- A bicep change triggers `aiquality-infra`
- A `src/**` change triggers `aiquality-apps`

## 7. Inspecting / debugging

```powershell
# List runs
az pipelines runs list --org https://dev.azure.com/<org> --project <proj>

# Stream the job logs from a single run
az containerapp job logs show -g rg-aiquality-dev -n job-pdf-pipeline --execution <execName> --follow
```

For deeper deploy troubleshooting, see the [infra-deployment skill](../.github/skills/infra-deployment/SKILL.md).

## 8. Why ACR Tasks (`az acr build`) instead of `docker build` + `docker push`?

The ACR is deployed with `publicNetworkAccess: Disabled` and a private endpoint. Microsoft-hosted ADO agents have dynamic public IPs that can't reach the private ACR. `az acr build` runs the build inside Azure — the ARM API call is public but the registry I/O happens on the Azure backbone. No need for a self-hosted agent or to poke holes in the ACR firewall.

If you switch to a public ACR later, `docker build && docker push` from the agent works too.
