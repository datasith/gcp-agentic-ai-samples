---
title: External 3P A2A LMS & Support Agent
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
---

# External 3P A2A Agent + OIDC WIF Issuer (Hugging Face Spaces Docker)

This container simulates an external third-party SaaS / LMS agent (for example, an academic course & student support agent running outside Google Cloud) that interoperates bidirectionally with **Google Cloud Agent Runtime (`reasoningEngines`)**:

1. **Outbound Target (`GCP Agent Runtime -> 3P Agent` via A2A):**
   - `GET /.well-known/agent-card.json`: Serves a standards-compliant A2A v0.3.0 Agent Card.
   - `POST /a2a`: Handles JSON-RPC 2.0 `message/send` requests for `course_syllabus_lookup` and `student_support_ticket`, enforcing Bearer authentication (`A2A_INBOUND_TOKEN`) injected by **GCP Agent Identity Auth Manager** (`google_agent_registry_binding`).
2. **Inbound Caller (`3P Agent -> GCP Agent Runtime` via IAM Workload Identity Federation):**
   - `GET /.well-known/openid-configuration` & `GET /.well-known/jwks.json`: Serves standard OIDC discovery and public RSA keys so **GCP IAM Workload Identity Federation (WIF)** can verify JWTs minted by this 3P agent.
   - `POST /delegate-to-gcp`: Mints a short-lived RS256 OIDC JWT, exchanges it at `https://sts.googleapis.com/v1/token` (RFC 8693) for a short-lived Google Cloud Bearer token, and invokes a target GCP Agent Runtime (`reasoningEngine`) instance—with **zero static GCP service account keys**.
