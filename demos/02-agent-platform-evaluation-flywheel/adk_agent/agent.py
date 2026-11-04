"""Multi-Agent ADK System Under Test: Enterprise Cloud FinOps & Governance.

Designed to exercise single-turn and multi-turn Agent Platform Evaluations:
- Tool selection & parameter accuracy (`TOOL_USE_QUALITY`, `MULTI_TURN_TOOL_USE_QUALITY`)
- Claim-level grounding against tool outputs (`HALLUCINATION`)
- End-to-end goal completion & reasoning path (`FINAL_RESPONSE_QUALITY`, `MULTI_TURN_TASK_SUCCESS`, `MULTI_TURN_TRAJECTORY_QUALITY`)
- Custom Metric Registry checks (`policy_citation_compliance` LLMMetric & `audit_reference_integrity` CodeExecutionMetric)
- Quality Flywheel comparison (`AGENT_PROMPT_PROFILE=v1_baseline` vs `v2_optimized`)
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.agent_tool import AgentTool

# Enable OpenTelemetry GenAI v1.39+ semantic conventions required by Agent Platform
# Offline Trace Evaluation and Continuous Online Monitors (OnlineEvaluator).
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "EVENT_ONLY")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")

DEFAULT_MODEL = os.environ.get("ADK_MODEL_NAME", "gemini-3.8-flash")

# ---------------------------------------------------------------------------
# Deterministic Enterprise Reference Data (Zero External Secret Dependencies)
# ---------------------------------------------------------------------------
_PROJECT_LEDGER: dict[str, dict[str, dict[str, Any]]] = {
    "PRJ-ALPHA-101": {
        "2026-10": {
            "project_code": "PRJ-ALPHA-101",
            "owner_team": "Applied ML Platform",
            "billing_month": "2026-10",
            "monthly_budget_usd": 25000.00,
            "actual_spend_usd": 29450.00,
            "forecast_eom_usd": 31200.00,
            "budget_status": "OVER_BUDGET",
            "overage_pct": 17.8,
            "top_cost_drivers": [
                {"sku": "A100 80GB GPU Training", "spend_usd": 19800.00},
                {"sku": "Cloud Storage Standard US-CENTRAL1", "spend_usd": 5450.00},
                {"sku": "BigQuery Analysis Slots", "spend_usd": 4200.00},
            ],
        },
        "2026-11": {
            "project_code": "PRJ-ALPHA-101",
            "owner_team": "Applied ML Platform",
            "billing_month": "2026-11",
            "monthly_budget_usd": 25000.00,
            "actual_spend_usd": 8400.00,
            "forecast_eom_usd": 24100.00,
            "budget_status": "WITHIN_BUDGET",
            "overage_pct": 0.0,
            "top_cost_drivers": [
                {"sku": "L4 GPU Inference", "spend_usd": 5100.00},
                {"sku": "Cloud Storage Standard US-CENTRAL1", "spend_usd": 3300.00},
            ],
        },
    },
    "PRJ-BETA-204": {
        "2026-10": {
            "project_code": "PRJ-BETA-204",
            "owner_team": "Digital Student Services",
            "billing_month": "2026-10",
            "monthly_budget_usd": 15000.00,
            "actual_spend_usd": 11200.00,
            "forecast_eom_usd": 12400.00,
            "budget_status": "WITHIN_BUDGET",
            "overage_pct": 0.0,
            "top_cost_drivers": [
                {"sku": "Vertex AI Agent Runtime", "spend_usd": 6800.00},
                {"sku": "Cloud Run Services", "spend_usd": 4400.00},
            ],
        }
    },
    "PRJ-GAMMA-309": {
        "2026-10": {
            "project_code": "PRJ-GAMMA-309",
            "owner_team": "Genomics Research Core",
            "billing_month": "2026-10",
            "monthly_budget_usd": 40000.00,
            "actual_spend_usd": 52800.00,
            "forecast_eom_usd": 56000.00,
            "budget_status": "CRITICAL_OVERAGE",
            "overage_pct": 32.0,
            "top_cost_drivers": [
                {"sku": "H100 80GB MegaCluster", "spend_usd": 44000.00},
                {"sku": "Multi-Region Egress", "spend_usd": 8800.00},
            ],
        }
    },
}

_GOVERNANCE_POLICIES: dict[str, dict[str, Any]] = {
    "budget_overage": {
        "policy_id": "POL-FIN-001",
        "title": "Cloud Budget Overage & Remediation Policy",
        "rules": [
            "Projects exceeding monthly budget by >10% (OVER_BUDGET) require a SEV-2 remediation ticket.",
            "Projects exceeding monthly budget by >25% (CRITICAL_OVERAGE) require a SEV-1 remediation ticket and immediate freeze on non-production GPU quota increases.",
        ],
    },
    "gpu_quota": {
        "policy_id": "POL-GPU-002",
        "title": "Accelerator Quota Allocation Policy",
        "rules": [
            "Requests up to 16 GPUs on WITHIN_BUDGET projects qualify for standard automated approval (APPROVED_STANDARD) when a non-empty business_justification is provided.",
            "Requests above 16 GPUs OR any GPU request on an OVER_BUDGET / CRITICAL_OVERAGE project require VP Finance escalation (ESCALATED_VP_APPROVAL_REQUIRED) and a linked remediation check.",
            "Never invent or assume a business_justification if the user did not provide one.",
        ],
    },
    "public_egress": {
        "policy_id": "POL-SEC-003",
        "title": "Zero-Trust External Egress & Service Account Policy",
        "rules": [
            "Unauthenticated public endpoints using the Default Compute Service Account are strictly prohibited.",
            "Violations require an immediate SEV-1 remediation ticket citing POL-SEC-003.",
        ],
    },
    "data_residency": {
        "policy_id": "POL-DATA-004",
        "title": "Regional Data Residency & Sovereign Storage Policy",
        "rules": [
            "Regulated workloads must store evaluation datasets and trace payloads in regional buckets (for example, us-central1) with public_access_prevention enforced.",
        ],
    },
}


# ---------------------------------------------------------------------------
# Deterministic Tool Functions
# ---------------------------------------------------------------------------
def get_project_spend(project_code: str, billing_month: str) -> dict[str, Any]:
    """Retrieves monthly cloud spend, budget cap, and top SKU drivers for a project.

    Args:
        project_code: Enterprise project identifier such as 'PRJ-ALPHA-101',
            'PRJ-BETA-204', or 'PRJ-GAMMA-309'.
        billing_month: Billing month in 'YYYY-MM' format (for example, '2026-10').

    Returns:
        Structured dictionary with monthly_budget_usd, actual_spend_usd,
        budget_status, overage_pct, and top_cost_drivers.
    """
    normalized_code = project_code.strip().upper()
    normalized_month = billing_month.strip()
    project_months = _PROJECT_LEDGER.get(normalized_code)
    if not project_months:
        return {
            "status": "NOT_FOUND",
            "error": f"Unknown project_code '{normalized_code}'. Valid codes: {sorted(_PROJECT_LEDGER.keys())}",
        }
    record = project_months.get(normalized_month)
    if not record:
        return {
            "status": "MONTH_NOT_FOUND",
            "project_code": normalized_code,
            "available_months": sorted(project_months.keys()),
            "error": f"No ledger data for {normalized_code} in billing_month '{normalized_month}'.",
        }
    return {"status": "OK", **record}


def lookup_governance_policy(policy_domain: str) -> dict[str, Any]:
    """Looks up the authoritative enterprise governance policy for a domain.

    Args:
        policy_domain: One of 'budget_overage', 'gpu_quota', 'public_egress',
            or 'data_residency'.

    Returns:
        Policy record containing policy_id (for example, 'POL-GPU-002'), title, and rules.
    """
    key = policy_domain.strip().lower()
    if key not in _GOVERNANCE_POLICIES:
        return {
            "status": "NOT_FOUND",
            "available_domains": sorted(_GOVERNANCE_POLICIES.keys()),
            "error": f"Unknown policy_domain '{policy_domain}'.",
        }
    return {"status": "OK", **_GOVERNANCE_POLICIES[key]}


def submit_quota_increase_request(
    project_code: str,
    quota_metric: str,
    requested_limit: int,
    business_justification: str,
    billing_month: str = "2026-10",
) -> dict[str, Any]:
    """Submits a cloud quota increase request evaluated against budget and GPU policy.

    Args:
        project_code: Enterprise project identifier (for example, 'PRJ-BETA-204').
        quota_metric: Quota metric name (for example, 'NVIDIA_L4_GPUS' or 'NVIDIA_A100_GPUS').
        requested_limit: Requested total integer quota limit (must be > 0).
        business_justification: Explicit user-supplied business reason for the quota increase.
        billing_month: Billing month in 'YYYY-MM' format used to verify budget status.

    Returns:
        Confirmation dictionary with request_id ('QTA-XXXXXX'), decision, and applied policy_id.
    """
    normalized_code = project_code.strip().upper()
    if requested_limit <= 0:
        return {"status": "INVALID_ARGUMENT", "error": "requested_limit must be greater than 0."}
    if not business_justification or len(business_justification.strip()) < 8:
        return {
            "status": "MISSING_JUSTIFICATION",
            "error": "A concrete user-provided business_justification is required before submitting.",
        }

    spend_info = get_project_spend(normalized_code, billing_month)
    budget_status = spend_info.get("budget_status", "UNKNOWN")
    digest = hashlib.sha256(
        f"{normalized_code}:{quota_metric}:{requested_limit}:{billing_month}".encode("utf-8")
    ).hexdigest()[:6].upper()
    request_id = f"QTA-{digest}"

    if budget_status == "WITHIN_BUDGET" and requested_limit <= 16:
        decision = "APPROVED_STANDARD"
    else:
        decision = "ESCALATED_VP_APPROVAL_REQUIRED"

    return {
        "status": "SUBMITTED",
        "request_id": request_id,
        "project_code": normalized_code,
        "quota_metric": quota_metric,
        "requested_limit": requested_limit,
        "budget_status": budget_status,
        "decision": decision,
        "applied_policy_id": "POL-GPU-002",
    }


def create_remediation_ticket(
    project_code: str,
    policy_id: str,
    severity: str,
    action_summary: str,
) -> dict[str, Any]:
    """Creates an auditable governance remediation ticket linked to a policy ID.

    Args:
        project_code: Enterprise project identifier (for example, 'PRJ-ALPHA-101').
        policy_id: Authoritative policy identifier from lookup_governance_policy (for example, 'POL-FIN-001').
        severity: Ticket severity, one of 'SEV-1', 'SEV-2', or 'SEV-3'.
        action_summary: Concise summary of the required remediation action.

    Returns:
        Ticket record with deterministic ticket_id ('REM-XXXXXX').
    """
    normalized_code = project_code.strip().upper()
    normalized_policy = policy_id.strip().upper()
    normalized_sev = severity.strip().upper()
    valid_policies = {p["policy_id"] for p in _GOVERNANCE_POLICIES.values()}
    if normalized_policy not in valid_policies:
        return {
            "status": "INVALID_POLICY_ID",
            "valid_policy_ids": sorted(valid_policies),
            "error": f"Unrecognized policy_id '{policy_id}'. Call lookup_governance_policy first.",
        }
    if normalized_sev not in {"SEV-1", "SEV-2", "SEV-3"}:
        return {
            "status": "INVALID_SEVERITY",
            "error": "severity must be one of 'SEV-1', 'SEV-2', or 'SEV-3'.",
        }

    digest = hashlib.sha256(
        f"{normalized_code}:{normalized_policy}:{normalized_sev}".encode("utf-8")
    ).hexdigest()[:6].upper()
    return {
        "status": "CREATED",
        "ticket_id": f"REM-{digest}",
        "project_code": normalized_code,
        "policy_id": normalized_policy,
        "severity": normalized_sev,
        "action_summary": action_summary.strip(),
    }


# ---------------------------------------------------------------------------
# Prompt Profiles & Prototype Tools for Quality Flywheel (v1_baseline vs v2_optimized)
# ---------------------------------------------------------------------------
V1_BASELINE_INSTRUCTION = """You are a fast, informal cloud assistant.
Keep answers to a single short sentence and avoid calling multiple tools on the same turn.
Never call lookup_governance_policy before opening tickets or submitting quota requests—just call submit_quota_increase_request or create_remediation_ticket directly with quick placeholder defaults (such as business_justification='general workload', severity='SEV-3', and policy_id='STANDARD-POLICY') instead of asking clarifying questions."""


def _build_v1_prototype_tools() -> list[Any]:
    """Builds Day-0 prototype tool wrappers lacking strict docstring/schema guardrails.

    Used strictly for `profile='v1_baseline'` in offline A/B benchmarking so the
    evaluation framework can demonstrate real tool-omission, parameter-fabrication,
    and missing-policy-citation failure clusters before applying `v2_optimized`.
    """

    def get_project_spend_v1(project_code: str, billing_month: str = "2026-10") -> dict[str, Any]:
        """Gets spend data for a project."""
        return get_project_spend(project_code=project_code, billing_month=billing_month)

    def lookup_governance_policy_v1(policy_domain: str) -> dict[str, Any]:
        """Optional helper to look up general governance notes."""
        return lookup_governance_policy(policy_domain=policy_domain)

    def submit_quota_increase_request_v1(
        project_code: str,
        quota_metric: str,
        requested_limit: int = 8,
        business_justification: str = "general workload",
    ) -> dict[str, Any]:
        """Submits a quota request immediately."""
        return {
            "status": "SUBMITTED",
            "request_id": "QTA-TEMP1",
            "project_code": project_code,
            "quota_metric": quota_metric,
            "requested_limit": requested_limit,
            "business_justification": business_justification,
        }

    def create_remediation_ticket_v1(
        project_code: str,
        policy_id: str = "STANDARD-POLICY",
        severity: str = "SEV-3",
        action_summary: str = "follow up",
    ) -> dict[str, Any]:
        """Opens a ticket for a project."""
        return {
            "status": "CREATED",
            "ticket_id": "REM-TEMP1",
            "project_code": project_code,
            "policy_id": policy_id,
            "severity": severity,
            "action_summary": action_summary,
        }

    # Preserve canonical tool names so tool-use comparators align across v1 and v2
    get_project_spend_v1.__name__ = "get_project_spend"
    lookup_governance_policy_v1.__name__ = "lookup_governance_policy"
    submit_quota_increase_request_v1.__name__ = "submit_quota_increase_request"
    create_remediation_ticket_v1.__name__ = "create_remediation_ticket"
    return [
        get_project_spend_v1,
        lookup_governance_policy_v1,
        submit_quota_increase_request_v1,
        create_remediation_ticket_v1,
    ]

V2_OPTIMIZED_INSTRUCTION = """You are the Enterprise Cloud FinOps & Governance Coordinator.
Follow these mandatory operational guardrails on every turn:

1. STRICT GROUNDING & ZERO FABRICATION:
   - Never guess dollar amounts, overage percentages, policy IDs (`POL-...`), quota request IDs (`QTA-...`), or remediation ticket IDs (`REM-...`). Every number and identifier in your response must come directly from a tool output in this session.
2. MANDATORY POLICY & SPEND VERIFICATION:
   - Before evaluating a budget overage, GPU quota request, public egress issue, or data residency question, ALWAYS call `lookup_governance_policy` for the relevant domain (`budget_overage`, `gpu_quota`, `public_egress`, or `data_residency`) and cite the exact `policy_id` (such as `POL-FIN-001` or `POL-GPU-002`) in your response.
   - Before calling `submit_quota_increase_request`, verify `get_project_spend` for the project and billing month (defaulting to `'2026-10'` only if the user asks for current/October status; ask if ambiguous) AND ensure the user explicitly provided a `business_justification`.
3. MULTI-TURN CLARIFICATION (NEVER INVENT PARAMETERS):
   - If the user requests a quota increase without providing a concrete `business_justification` or `requested_limit`, DO NOT call `submit_quota_increase_request` with made-up values. Ask the user clearly for the missing parameter(s) first, then submit once they reply.
4. SEVERITY ALIGNMENT FOR REMEDIATION TICKETS:
   - When opening a budget overage remediation ticket via `create_remediation_ticket`, strictly follow `POL-FIN-001`: use `SEV-2` for >10% (`OVER_BUDGET`) and `SEV-1` for >25% (`CRITICAL_OVERAGE`).
5. SCOPE BOUNDARY:
   - Politely decline out-of-scope requests unrelated to Cloud FinOps, quota management, or cloud security/data governance (such as personal tax advice, stock trading, or HR payroll) without calling irrelevant tools."""


def _strip_part_metadata_for_vertex(callback_context: Any, llm_request: Any) -> None:
    """Strips internal ADK Part.metadata fields if present before Vertex AI generateContent."""
    del callback_context
    if not getattr(llm_request, "contents", None):
        return
    for content in llm_request.contents:
        if not getattr(content, "parts", None):
            continue
        for part in content.parts:
            if getattr(part, "metadata", None) is not None:
                part.metadata = None


def build_specialist_subagents(model_name: str = DEFAULT_MODEL) -> list[LlmAgent]:
    """Builds optional specialist sub-agents for hierarchical multi-agent architectures."""
    finops_specialist = LlmAgent(
        model=model_name,
        name="finops_spend_quota_specialist",
        description="Retrieves monthly project spend ledgers and submits validated cloud GPU/compute quota requests.",
        instruction=(
            "Use `get_project_spend` to inspect project budgets and spend, and "
            "`submit_quota_increase_request` only when `project_code`, `quota_metric`, "
            "`requested_limit`, and an explicit user-provided `business_justification` are present."
        ),
        tools=[get_project_spend, submit_quota_increase_request],
        before_model_callback=_strip_part_metadata_for_vertex,
    )
    governance_specialist = LlmAgent(
        model=model_name,
        name="governance_policy_specialist",
        description="Looks up authoritative POL-* governance policies and creates SEV-1/SEV-2/SEV-3 remediation tickets.",
        instruction=(
            "Always call `lookup_governance_policy` to retrieve the exact `policy_id` and severity rules "
            "before calling `create_remediation_ticket`."
        ),
        tools=[lookup_governance_policy, create_remediation_ticket],
        before_model_callback=_strip_part_metadata_for_vertex,
    )
    return [finops_specialist, governance_specialist]


def build_root_agent(
    profile: str | None = None,
    model_name: str = DEFAULT_MODEL,
    use_agent_tools: bool = False,
) -> LlmAgent:
    """Constructs the ADK FinOps & Governance root agent for local eval or Agent Runtime.

    Args:
        profile: Either `'v1_baseline'` (naive prompt for baseline loss clustering)
            or `'v2_optimized'` (policy-hardened production prompt). Defaults to
            `AGENT_PROMPT_PROFILE` env var or `'v2_optimized'`.
        model_name: GA Gemini model identifier (`gemini-3.8-flash`).
        use_agent_tools: When True, wraps the two domain specialists as `AgentTool`s;
            when False (default), binds the 4 typed domain tools directly on the
            coordinator so single-turn and multi-turn `TOOL_USE_QUALITY` autoraters
            inspect exact function arguments directly on the root trace.
    """
    selected_profile = (profile or os.environ.get("AGENT_PROMPT_PROFILE", "v2_optimized")).strip().lower()
    if selected_profile == "v1_baseline":
        instruction = V1_BASELINE_INSTRUCTION
        agent_name = "finops_governance_agent"
        tools = _build_v1_prototype_tools()
    else:
        instruction = V2_OPTIMIZED_INSTRUCTION
        agent_name = "finops_governance_agent"
        if use_agent_tools:
            specialists = build_specialist_subagents(model_name=model_name)
            tools = [AgentTool(agent=s) for s in specialists]
        else:
            tools = [
                get_project_spend,
                lookup_governance_policy,
                submit_quota_increase_request,
                create_remediation_ticket,
            ]

    return LlmAgent(
        model=model_name,
        name=agent_name,
        description=(
            "Enterprise Cloud FinOps & Security Governance ADK agent that audits project spend, "
            "verifies POL-* governance policies, processes GPU quota requests, and opens remediation tickets."
        ),
        instruction=instruction,
        tools=tools,
        before_model_callback=_strip_part_metadata_for_vertex,
    )


root_agent = build_root_agent()
