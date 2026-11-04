"""Deploys the ADK Orchestrator to Vertex AI Agent Runtime (ReasoningEngine)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import vertexai
from google.adk.agents.llm_agent import LlmAgent
from vertexai.preview.reasoning_engines import AdkApp, ReasoningEngine


class StreamingAdkApp(AdkApp):
    """Wraps root_agent with startup Agent Registry resolution & generator streaming on Agent Runtime."""

    def set_up(self) -> None:
        for key, value in (self._tmpl_attrs.get("env_vars") or {}).items():
            os.environ[key] = value
        from agent import build_root_agent

        self._tmpl_attrs["agent"] = build_root_agent()
        super().set_up()
        # Ensure Gemini 3.8 model calls inside the regional ReasoningEngine container
        # target the Vertex AI global model endpoint after super().set_up() runs.
        os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get("VERTEX_MODEL_LOCATION", "global")
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"

    def query(self, *args, **kwargs):
        return super().query(*args, **kwargs)

    def stream_query(self, *args, **kwargs):
        yield from super().stream_query(*args, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy ADK Orchestrator to GCP Agent Runtime.")
    parser.add_argument("--project", required=True, help="Google Cloud Project ID")
    parser.add_argument("--location", default="us-central1", help="GCP Region (default: us-central1)")
    parser.add_argument("--staging-bucket", required=True, help="GCS staging bucket URI (gs://...)")
    parser.add_argument(
        "--external-agent-id",
        default="external-3p-lms-agent",
        help="Agent Registry service ID or displayName for the external 3P agent",
    )
    parser.add_argument(
        "--fallback-bearer-token",
        default="",
        help="Optional fallback Bearer token for outbound calls before Agent Identity Binding is attached",
    )
    parser.add_argument(
        "--display-name",
        default="academic-interop-orchestrator",
        help="Display name for the ReasoningEngine resource",
    )
    parser.add_argument(
        "--out-file",
        default="/tmp/deployed_reasoning_engine.txt",
        help="File path to record the deployed ReasoningEngine resource name",
    )
    args = parser.parse_args()

    # Change working directory to gcp_agent_runtime/ so extra_packages=["agent.py"]
    # archives `agent.py` at the root (`./agent.py`) of dependencies.tar.gz.
    os.chdir(Path(__file__).resolve().parent)

    vertexai.init(
        project=args.project,
        location=args.location,
        staging_bucket=args.staging_bucket,
    )

    env_vars = {
        "GOOGLE_CLOUD_PROJECT": args.project,
        "AGENT_REGISTRY_LOCATION": args.location,
        "VERTEX_MODEL_LOCATION": "global",
        "EXTERNAL_AGENT_REGISTRY_ID": args.external_agent_id,
    }
    if args.fallback_bearer_token:
        env_vars["EXTERNAL_3P_FALLBACK_BEARER_TOKEN"] = args.fallback_bearer_token

    placeholder_agent = LlmAgent(
        model="gemini-3.8-flash",
        name="academic_interop_orchestrator",
        description="Placeholder replaced at container startup by StreamingAdkApp.set_up().",
    )
    app = StreamingAdkApp(agent=placeholder_agent, env_vars=env_vars)
    # Clear the in-memory LlmAgent reference before cloudpickle serialization so
    # reasoning_engine.pkl has zero google.adk class version-skew sensitivity;
    # StreamingAdkApp.set_up() constructs build_root_agent() inside the container.
    app._tmpl_attrs["agent"] = None

    remote_agent = ReasoningEngine.create(
        reasoning_engine=app,
        requirements=[
            "google-cloud-aiplatform[agent_engines,adk]>=1.88.0",
            "google-adk[a2a,agent-identity,mcp]==2.13.0",
            "httpx>=0.28.1",
        ],
        display_name=args.display_name,
        description="Cross-platform ADK orchestrator integrating with external 3P A2A agents via Agent Registry.",
        extra_packages=["agent.py"],
    )

    print(f"Deployed ReasoningEngine: {remote_agent.resource_name}")
    Path(args.out_file).write_text(remote_agent.resource_name + "\n", encoding="utf-8")
    print(f"Saved resource name to {args.out_file}")


if __name__ == "__main__":
    main()
