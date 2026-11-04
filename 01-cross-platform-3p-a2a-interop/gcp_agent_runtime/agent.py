"""GCP Agent Runtime Orchestrator (ADK + Agent Registry + Auth Manager + A2A).

Demonstrates:
1. Dynamic startup resolution of an external 3P A2A agent from Agent Registry
   (`google.adk.integrations.agent_registry.AgentRegistry`), resolving either a
   friendly `service_id`/`displayName` (e.g., `external-3p-lms-agent`) or a full
   `agents/agentregistry-...` resource name in the regional registry (`us-central1`).
2. Routing `gemini-3.8-flash` model inference to the `global` Vertex AI endpoint
   while keeping Agent Registry lookups regional (`AGENT_REGISTRY_LOCATION`).
3. Keyless credential attachment via Agent Identity Auth Manager
   (`CredentialManager.register_auth_provider(GcpAuthProvider())`) or fallback
   custom `httpx.AsyncClient` Bearer header.
4. Vertex AI `part_metadata` sanitization callback for A2A round-trips.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from google.adk.agents.llm_agent import LlmAgent
from google.adk.auth.credential_manager import CredentialManager
from google.adk.integrations.agent_identity import GcpAuthProvider
from google.adk.integrations.agent_registry import AgentRegistry

logger = logging.getLogger(__name__)


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


def _resolve_agent_registry_resource_name(registry: AgentRegistry, target_id: str) -> str:
    """Resolves a friendly service_id/displayName (e.g. 'external-3p-lms-agent') to its 'agents/agentregistry-...' name."""
    if target_id.startswith("projects/") or target_id.startswith("agents/agentregistry-"):
        return target_id
    clean_id = target_id.removeprefix("agents/")
    agents_resp = registry.list_agents()
    for entry in agents_resp.get("agents", []):
        name = entry.get("name", "")
        display_name = entry.get("displayName", "")
        agent_urn = entry.get("agentId", "")
        if clean_id in (display_name, name) or agent_urn.endswith(f":{clean_id}"):
            return name
    return f"agents/{clean_id}"


def _build_sub_agents() -> list[Any]:
    """Resolves the registered 3P A2A agent from Agent Registry once at startup."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project_id:
        return []

    registry_location = os.environ.get(
        "AGENT_REGISTRY_LOCATION",
        os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", "us-central1"),
    )
    if registry_location == "global":
        registry_location = "us-central1"

    external_agent_id = os.environ.get("EXTERNAL_AGENT_REGISTRY_ID", "external-3p-lms-agent")
    fallback_token = os.environ.get("EXTERNAL_3P_FALLBACK_BEARER_TOKEN", "").strip()

    try:
        CredentialManager.register_auth_provider(GcpAuthProvider())
        registry = AgentRegistry(
            project_id=project_id,
            location=registry_location,
        )
        resolved_name = _resolve_agent_registry_resource_name(registry, external_agent_id)
        custom_httpx = (
            httpx.AsyncClient(
                headers={"Authorization": f"Bearer {fallback_token}"},
                timeout=30.0,
                # AdkApp.stream_query() runs asyncio.run() per request and closes
                # the worker thread's event loop after each turn; disable keep-alive
                # pooling so httpcore never reuses a socket bound to a closed loop.
                limits=httpx.Limits(max_keepalive_connections=0),
            )
            if fallback_token
            else None
        )
        remote_3p_agent = registry.get_remote_a2a_agent(
            agent_name=resolved_name,
            httpx_client=custom_httpx,
        )
        return [remote_3p_agent]
    except Exception as exc:
        logger.warning("Agent Registry startup resolution warning: %s", exc)
        return []


def build_root_agent() -> LlmAgent:
    """Constructs the root ADK orchestrator agent."""
    sub_agents = _build_sub_agents()
    model_location = os.environ.get("VERTEX_MODEL_LOCATION", "global")
    os.environ["GOOGLE_CLOUD_LOCATION"] = model_location
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
    model_id = os.environ.get("ADK_MODEL_ID", "gemini-3.8-flash")

    return LlmAgent(
        model=model_id,
        name="academic_interop_orchestrator",
        description="GCP Agent Runtime orchestrator that combines internal institutional policies with the external 3P LMS A2A agent.",
        instruction=(
            "You are the Academic Interoperability Orchestrator running on Google Cloud Agent Runtime. "
            "Use `get_campus_policy` for institutional policies (late_submission, ferpa, ai_usage). "
            "Delegate course syllabus lookups (CS-401, AI-302), grading policies, assignment deadlines, "
            "and student support ticket creation to your registered 3P sub-agent (`external_3p_lms_agent`) "
            "discovered from Agent Registry."
        ),
        tools=[get_campus_policy],
        sub_agents=sub_agents,
        before_model_callback=_strip_part_metadata_for_vertex,
    )


if os.environ.get("GOOGLE_CLOUD_PROJECT"):
    root_agent = build_root_agent()
else:
    root_agent = LlmAgent(
        model="gemini-3.8-flash",
        name="academic_interop_orchestrator",
        description="GCP Agent Runtime orchestrator.",
        tools=[get_campus_policy],
        before_model_callback=_strip_part_metadata_for_vertex,
    )
