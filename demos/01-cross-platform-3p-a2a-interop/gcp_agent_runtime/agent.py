"""GCP Agent Runtime Orchestrator (ADK + Agent Registry + Auth Manager + A2A).

Demonstrates:
1. Dynamic startup resolution of an external 3P A2A agent from Agent Registry
   (`google.adk.integrations.agent_registry.AgentRegistry`).
2. Keyless credential attachment via Agent Identity Auth Manager
   (`CredentialManager.register_auth_provider(GcpAuthProvider())`) or fallback
   custom `header_provider`.
3. Vertex AI `part_metadata` sanitization callback for A2A round-trips.
"""

from __future__ import annotations

import os
from typing import Any

from google.adk.agents.llm_agent import LlmAgent
from google.adk.auth.credential_manager import CredentialManager
from google.adk.integrations.agent_identity import GcpAuthProvider
from google.adk.integrations.agent_registry import AgentRegistry

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
EXTERNAL_AGENT_ID = os.environ.get("EXTERNAL_AGENT_REGISTRY_ID", "external-3p-lms-agent")
MODEL_ID = os.environ.get("ADK_MODEL_ID", "gemini-3.8-flash")


def _strip_part_metadata_for_vertex(callback_context: Any, llm_request: Any) -> None:
    """Strips Part.part_metadata populated by A2A conversion so Vertex AI does not reject it."""
    for content in getattr(llm_request, "contents", None) or []:
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "part_metadata", None) is not None:
                part.part_metadata = None
    return None


def get_campus_policy(topic: str) -> dict[str, str]:
    """Returns representative GCP-hosted institutional academic policy details.

    Args:
        topic: Policy topic to look up (for example, 'late_submission', 'ferpa', 'ai_usage').
    """
    policies = {
        "late_submission": "Assignments submitted within 48 hours of the deadline incur a 10% deduction per day unless an advisor ticket is on file.",
        "ferpa": "All student academic records must remain inside governed boundaries; external 3P calls are screened by Model Armor and IAM UAPs.",
        "ai_usage": "Students may use approved Gemini Enterprise assistants with citation of prompts in lab submissions.",
    }
    key = topic.strip().lower()
    return {
        "topic": topic,
        "policy": policies.get(
            key,
            "Standard institutional academic policy applies. Available topics: late_submission, ferpa, ai_usage.",
        ),
    }


def _build_sub_agents() -> list[Any]:
    """Resolves the registered 3P A2A agent from Agent Registry once at startup."""
    if not PROJECT_ID:
        return []

    CredentialManager.register_auth_provider(GcpAuthProvider())

    fallback_token = os.environ.get("EXTERNAL_3P_FALLBACK_BEARER_TOKEN", "").strip()

    def _custom_headers(_context: Any) -> dict[str, str]:
        if fallback_token:
            return {"Authorization": f"Bearer {fallback_token}"}
        return {}

    registry = AgentRegistry(
        project_id=PROJECT_ID,
        location=LOCATION,
        header_provider=_custom_headers if fallback_token else None,
    )
    remote_3p_agent = registry.get_remote_a2a_agent(
        agent_name=f"agents/{EXTERNAL_AGENT_ID}",
    )
    return [remote_3p_agent]


root_agent = LlmAgent(
    model=MODEL_ID,
    name="academic_interop_orchestrator",
    description="GCP Agent Runtime orchestrator that combines internal institutional policies with the external 3P LMS A2A agent.",
    instruction=(
        "You are the Academic Interoperability Orchestrator running on Google Cloud Agent Runtime. "
        "Use `get_campus_policy` for institutional policies (late_submission, ferpa, ai_usage). "
        "Delegate course syllabus lookups (CS-401, AI-302), grading policies, assignment deadlines, "
        "and student support ticket creation to your registered 3P sub-agent discovered from Agent Registry."
    ),
    tools=[get_campus_policy],
    sub_agents=_build_sub_agents(),
    before_model_callback=_strip_part_metadata_for_vertex,
)
