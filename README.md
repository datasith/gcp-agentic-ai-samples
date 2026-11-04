# GCP Agentic AI Samples & Reference Architectures

A modular collection of production-grade reference architectures, Infrastructure-as-Code (Terraform) blueprints, and runnable multi-agent implementations built on the **Gemini Enterprise Agent Platform**, **Google Agent Development Kit (ADK)**, **Agent Platform Evaluations (`agentplatform.Client.evals`)**, **Agent2Agent (A2A) Protocol**, **Model Context Protocol (MCP)**, **Agent Registry**, and **Agent Gateway**.

---

## Repository Structure

Each directory under [`demos/`](./demos/) is a self-contained, runnable architectural blueprint with its own Terraform configuration, agent source code, authentication harnesses, and operational runbook:

```text
gcp-agentic-ai-samples/
├── README.md                                     # Catalog index & architectural overview
├── LICENSE                                       # Apache 2.0 License
├── .gitignore                                    # Global exclusions (.env*, .terraform*, state files)
└── demos/
    ├── 01-cross-platform-3p-a2a-interop/         # Bidirectional 3P SaaS <-> GCP Agent Runtime (WIF, Agent Registry, Auth Manager, Dual-Mode Agent Gateway)
    │   ├── README.md                             # Architecture, auth handshakes, deployment guide, & 15 Day-2 operational gotchas
    │   ├── assets/                               # Open-source architecture diagrams (architecture.drawio.svg, architecture.excalidraw, architecture.drawio)
    │   ├── terraform/                            # Declarative IaC for WIF, Agent Registry, Auth Bindings, Dual Agent Gateways, Model Armor, IAM UAP, & ReasoningEngine
    │   ├── gcp_agent_runtime/                    # ADK Orchestrator & A2A Server running on Vertex AI Agent Runtime (gemini-3.8-flash)
    │   ├── external_3p_agent/                    # External 3P A2A Agent + OIDC/JWKS issuer (deployable to Hugging Face Spaces Docker in 2 minutes)
    │   └── scripts/                              # End-to-end Inbound (WIF -> GCP) and Outbound (GCP -> 3P) verification scripts
    ├── 02-agent-platform-evaluation-flywheel/    # End-to-End Agent Platform Evaluation & Quality Flywheel (ADK + Metric Registry + Simulation + Loss Clusters + Online Monitor)
    │   ├── README.md                             # 5-stage Quality Flywheel architecture, benchmark scorecard, & Day-2 operational gotchas
    │   ├── assets/                               # Open-source architecture diagrams (architecture.drawio.svg, architecture.excalidraw, architecture.drawio)
    │   ├── terraform/                            # Declarative IaC for Eval Artifacts Bucket, Least-Privilege ADK Runtime SA, P4SA Observability IAM, & Drift Alert Policy
    │   ├── adk_agent/                            # Enterprise Cloud FinOps & Governance ADK Agent (v1_baseline vs v2_optimized) + Agent Runtime deployer
    │   └── eval_framework/                       # Metric Registry setup, Golden + Multi-Turn User Simulation + Loss Cluster runner, CI/CD ship gate, & OnlineEvaluator
    └── _template-snippet/                        # Boilerplate template for adding new Agentic AI snippets & demos
        └── README.md
```

---

## Catalog of Demos & Snippets

| # | Demo / Snippet | Core Primitives Demonstrated | External / 3P Surface | Status |
| :--- | :--- | :--- | :--- | :--- |
| **01** | [**Cross-Platform 3P Agent <-> GCP Agent Runtime Interoperability**](./demos/01-cross-platform-3p-a2a-interop/) | `Agent Runtime` (`reasoningEngines`), `A2A` over HTTP+JSON, `IAM Workload Identity Federation (WIF)`, `Agent Registry`, `Agent Identity Auth Manager`, `Agent Gateway` (`CLIENT_TO_AGENT` + `AGENT_TO_ANYWHERE`), `Model Armor`, `IAM Unified Access Policies (UAP)` | Hugging Face Spaces (Docker FastAPI A2A Server + OIDC/JWKS Issuer) & GitHub Actions OIDC | Ready |
| **02** | [**Agent Platform Evaluation & Continuous Quality Flywheel**](./demos/02-agent-platform-evaluation-flywheel/) | `ADK` (`gemini-3.8-flash`), `Agent Runtime` (`reasoningEngines`), `agentplatform.Client.evals`, `Metric Registry` (`LLMMetric` + `CodeExecutionMetric` + Adaptive `RubricMetric`), `User Simulation` (`generate_conversation_scenarios`), `Automatic Loss Clusters` (`generate_loss_clusters`), `EvaluationExperiment` / `EvaluationRun`, `OnlineEvaluator` + `Cloud Monitoring` Drift Alerts | OpenTelemetry GenAI v1.39+ (`EVENT_ONLY` trace spans) & GitHub Actions / Cloud Build CI Ship Gate | Ready |

---

## Architectural Pillars Covered Across Snippets

1. **Cross-Boundary Interoperability (`A2A` + `Agent Registry`):**
   - Discovering and invoking external third-party agents (SaaS platforms, partner clouds, institutional LMS/ERP systems) without hardcoding URLs or credentials.
   - Exposing Google Cloud Agent Runtime (`reasoningEngines`) instances as standards-compliant A2A endpoints (`/v1/card`, `/v1/message:send`, `/v1/tasks/{id}`).
2. **Zero-Trust, Keyless Authentication (`WIF` + `Agent Identity` + `Auth Manager`):**
   - **Inbound (`3P -> GCP`):** RFC 8693 Security Token Service (`sts.googleapis.com`) OIDC JWT exchange via IAM Workload Identity Federation—eliminating static Service Account JSON keys in third-party platforms.
   - **Outbound (`GCP -> 3P`):** Workload-bound attested SPIFFE Agent Identities (`principal://agents.global.org-ORG_ID.system.id.goog/...`) paired with Agent Identity Auth Manager (`2LO`, `3LO`, and API Key providers) and declarative `google_agent_registry_binding` resources.
3. **Bidirectional Runtime Governance (`Agent Gateway` + `Model Armor` + `IAM UAP`):**
   - **Ingress (`CLIENT_TO_AGENT`):** Real-time prompt-injection, jailbreak, and sensitive data (PII/FERPA/HIPAA) screening before external requests reach agent code.
   - **Egress (`AGENT_TO_ANYWHERE`):** Default-deny outbound governance via Identity-Aware Proxy (IAP) and IAM Unified Access Policies (`iap.googleapis.com/resources.egressViaIAP`) scoped to registered Agent Registry destinations.
4. **Continuous Agent Evaluation & Quality Flywheel (`agentplatform.Client.evals` + `OnlineEvaluator`):**
   - **Pre-Deployment Optimization & CI Gate:** Versioned Metric Registry (`LLMMetric` + sandboxed Python `CodeExecutionMetric`), Adaptive & Static Rubric Autoraters (`FINAL_RESPONSE_QUALITY`, `TOOL_USE_QUALITY`, `HALLUCINATION`, `SAFETY`), Cold-Start Synthetic User Simulation (`generate_conversation_scenarios` + multi-turn `MULTI_TURN_TASK_SUCCESS`), and Automatic Loss Cluster root-cause diagnosis (`generate_loss_clusters`).
   - **Post-Deployment Production Monitoring:** OpenTelemetry GenAI v1.39+ (`gen_ai_latest_experimental` + `EVENT_ONLY`) trace ingestion into continuous `OnlineEvaluator` sampling every 10 minutes with Cloud Monitoring median-score drift alerts (`aiplatform.googleapis.com/online_evaluator/scores`).

---

## Adding a New Demo or Snippet

1. Copy [`demos/_template-snippet/`](./demos/_template-snippet/) to a new numbered directory (for example, `demos/02-cloud-run-streamable-http-mcp/`).
2. Keep all project, region, and organization identifiers parameterized via environment variables or `terraform.tfvars.example` (`YOUR_PROJECT_ID`, `YOUR_ORG_ID`).
3. Ensure `.gitignore`, `.dockerignore`, and `.gcloudignore` exclude `.env*` and local credential artifacts, and run all containers as a non-root user with a dedicated least-privilege Service Account.
4. Document the architecture, authentication flow, verification commands, and operational gotchas in the demo's `README.md`, then add an entry to the catalog table above.
