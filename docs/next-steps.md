# Next steps

A pragmatic roadmap from "PoC works end-to-end" → "production-ready". Ordered by typical priority.

## 1 — Productionize the deploy loop

- [ ] **CI/CD pipeline** (GitHub Actions or Azure DevOps): bicep what-if on PR, deploy on merge to `main`, docker build+push tagged with git SHA.
- [ ] **`azd` integration**: convert `main.bicep` + `main.parameters.json` to an `azd` template so `azd up` does provision + image build + deploy in one shot.
- [ ] **Image scanning**: enable ACR Defender or Trivy scan as a pre-push gate.
- [ ] **OIDC federation** instead of service-principal secrets for the CI deploy identity.

## 2 — Multi-environment story

Today the bicep defaults assume one `dev` env. To go to `staging`/`prod`:

- [ ] Parameterize `main.parameters.json` into `main.parameters.dev.json` / `main.parameters.prod.json`.
- [ ] Use a different `environmentName` per env so resource names don't collide.
- [ ] Use **different RGs** per env (cleanest blast-radius isolation).
- [ ] For prod: switch ACA env to `internal = true` and front it with Front Door Premium → private origin.

## 3 — Observability

The infra already wires Application Insights + Log Analytics. Code-side:

- [ ] **OpenTelemetry**: the Python apps already export traces via `agent-framework`; confirm the ACA env vars wire them to App Insights correctly.
- [ ] **Distributed tracing across calls**: ensure quality-api → AI Search → Foundry calls share a trace context (use `opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-fastapi`).
- [ ] **Custom KQL dashboards**: per-rule trigger frequency, paragraph latency p95, LLM token cost by rule.

## 4 — Quality / accuracy

- [ ] **Evaluation harness**: a fixed set of "good" and "bad" paragraphs with expected verdicts; run on every PR via the agent-framework's continuous eval.
- [ ] **Prompt optimizer**: use Foundry's prompt optimizer (see [microsoft-foundry skill](../.github/skills/)) to iterate on the rule-checker system prompt.
- [ ] **Rule confidence scoring**: surface the LLM's confidence per violation so the UI can grey out low-confidence flags.
- [ ] **Rules versioning**: bump `policy-rules` Cosmos container with a version field so old extractions don't poison new rules.

## 5 — Word add-in polish

- [ ] **AAD authentication** on the API (instead of public ingress + CORS-only).
- [ ] **Real Office manifest**: replace localhost URLs with the deployed `word-addin` Container App URL and host the manifest at a stable HTTPS endpoint.
- [ ] **Centralized deployment**: submit the manifest to AppSource OR push via Microsoft 365 admin centre for the org.
- [ ] **Offline-tolerant UX**: the add-in should gracefully handle API timeouts (e.g. while the quality-api scales from 0 → 1).

## 6 — Security hardening

The bicep already has the right defaults (`publicNetworkAccess: Disabled`, MI everywhere, no shared keys in app config). Remaining items:

- [ ] **Defender for Cloud** on the subscription + recommendations remediation.
- [ ] **Azure Policy**: enforce "no public IPs" / "diagnostic settings required" on the RG.
- [ ] **WAF**: put Front Door Premium or App Gateway in front of `quality-api` ingress with OWASP Core Rule Set.
- [ ] **Customer-managed keys** on Storage / Cosmos / Key Vault if compliance requires it (current setup uses MS-managed keys).
- [ ] **Audit log shipping**: Diagnostic Settings on every PaaS service → the same Log Analytics workspace.

## 7 — Cost & capacity

- [ ] **Foundry capacity**: today we use `DataZoneStandard` SKU at 50 PTU for `gpt-4.1`. Profile real workload, then either move to `Standard` (cheaper, regional) or `GlobalStandard` (cheapest, no data residency).
- [ ] **ACA scale rules**: today `minReplicas: 0` — fine for dev, but enables cold starts. For prod, set `minReplicas: 1` for quality-api.
- [ ] **AI Search tier**: today `basic` (3 replicas max). If query latency or QPS becomes a concern, move to `standard` and enable semantic search at scale.
- [ ] **Cosmos DB throughput**: today serverless; if you exceed 5K RU/s sustained, switch to provisioned with autoscale.

## 8 — Documentation / onboarding

- [ ] **Runbook**: how to rotate Key Vault secrets, redeploy a specific app, recover from a region outage.
- [ ] **ADRs** for non-obvious choices: why dual MI (UAMI + SAMI), why Foundry in Sweden Central, why Premium ACR.
- [ ] **Demo script**: a recorded walkthrough showing PDF ingestion → rule extraction → live add-in check.

---

> Pick the items relevant to your timeline and risk profile. The infra is built to absorb most of these without restructuring — most items are config or single-module additions.
