"""Deploys the ADK Orchestrator to Vertex AI Agent Runtime (ReasoningEngine)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import vertexai
from vertexai.preview.reasoning_engines import AdkApp, ReasoningEngine

from agent import root_agent


class StreamingAdkApp(AdkApp):
    """Wraps root_agent with explicit generator streaming support on Agent Runtime."""

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
        help="Agent Registry service ID for the external 3P agent",
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

    os.environ["GOOGLE_CLOUD_PROJECT"] = args.project
    os.environ["GOOGLE_CLOUD_LOCATION"] = args.location
    os.environ["EXTERNAL_AGENT_REGISTRY_ID"] = args.external_agent_id

    vertexai.init(
        project=args.project,
        location=args.location,
        staging_bucket=args.staging_bucket,
    )

    app = StreamingAdkApp(agent=root_agent)

    remote_agent = ReasoningEngine.create(
        reasoning_engine=app,
        requirements=[
            "google-cloud-aiplatform[agent_engines,adk]>=1.88.0",
            "google-adk[a2a]>=1.29.0",
            "a2a-sdk[http-server]==0.3.26",
            "httpx>=0.28.1",
        ],
        display_name=args.display_name,
        description="Cross-platform ADK orchestrator integrating with external 3P A2A agents via Agent Registry.",
        extra_packages=[str(Path(__file__).resolve().parent / "agent.py")],
    )

    print(f"Deployed ReasoningEngine: {remote_agent.resource_name}")
    Path(args.out_file).write_text(remote_agent.resource_name + "\n", encoding="utf-8")
    print(f"Saved resource name to {args.out_file}")


if __name__ == "__main__":
    main()
