"""Deploys the ADK FinOps & Governance Agent to Vertex AI Agent Runtime (`ReasoningEngine`).

Key best practices baked into this deployment script:
1. OpenTelemetry GenAI v1.39+ telemetry enabled (`enable_tracing=True`,
   `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`,
   `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY`) so Agent Platform
   Offline Trace Evaluations and Continuous Online Monitors (`OnlineEvaluator`)
   automatically ingest spans and inference events.
2. Regional `ReasoningEngine` (`us-central1`) paired with Global `gemini-3.8-flash`
   routing (`GOOGLE_CLOUD_LOCATION=global` set after `super().set_up()`).
3. Zero-skew container pickling (`app._tmpl_attrs["agent"] = None` + relative
   `extra_packages=["agent.py"]`) so the remote container builds the ADK 2.x
   agent cleanly at startup without cloudpickle version conflicts.
4. Dedicated least-privilege Service Account (`adk-eval-runtime-sa`), never the
   Default Compute Service Account.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import vertexai
from vertexai.agent_engines import AdkApp


class TelemetryEnabledAdkApp(AdkApp):
    """ADK ReasoningEngine wrapper with OTel GenAI v1.39+ & global model routing."""

    def clone(self) -> "TelemetryEnabledAdkApp":
        return self

    def set_up(self) -> None:
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental"
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "EVENT_ONLY"
        if self._tmpl_attrs.get("agent") is None:
            from agent import build_root_agent

            self._tmpl_attrs["agent"] = build_root_agent(profile="v2_optimized")
        super().set_up()
        # Route Gemini 3.8 Flash calls to the global Vertex AI endpoint while
        # keeping ReasoningEngine, Agent Registry, and OnlineEvaluator in us-central1.
        os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get("VERTEX_MODEL_LOCATION", "global")
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy ADK Agent with Evaluation Telemetry to Agent Runtime.")
    parser.add_argument("--project-id", default=os.environ.get("GCP_PROJECT_ID", ""), required=False)
    parser.add_argument("--region", default=os.environ.get("GCP_REGION", "us-central1"))
    parser.add_argument("--staging-bucket", default=os.environ.get("EVAL_STAGING_BUCKET", ""))
    parser.add_argument("--service-account", default=os.environ.get("AGENT_RUNTIME_SA", ""))
    parser.add_argument("--display-name", default="finops-governance-eval-agent")
    parser.add_argument("--output-json", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.project_id:
        raise SystemExit("Set --project-id or GCP_PROJECT_ID.")

    staging_bucket = args.staging_bucket or f"gs://{args.project_id}-agent-eval-flywheel"
    if not staging_bucket.startswith("gs://"):
        staging_bucket = f"gs://{staging_bucket}"

    service_account = args.service_account or f"adk-eval-runtime-sa@{args.project_id}.iam.gserviceaccount.com"

    agent_dir = Path(__file__).resolve().parent
    os.chdir(agent_dir)

    vertexai.init(project=args.project_id, location=args.region, staging_bucket=staging_bucket)

    from agent import build_root_agent

    app = TelemetryEnabledAdkApp(
        agent=build_root_agent(profile="v2_optimized"),
        enable_tracing=True,
    )
    # Defer agent instantiation to container startup to avoid local-vs-container pickle skew
    app._tmpl_attrs["agent"] = None

    requirements = [
        line.strip()
        for line in (agent_dir / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    env_vars: dict[str, str] = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "VERTEX_MODEL_LOCATION": "global",
        "ADK_MODEL_NAME": "gemini-3.8-flash",
        "AGENT_PROMPT_PROFILE": "v2_optimized",
        "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
    }

    existing_engines = list(vertexai.agent_engines.list(filter=f'display_name="{args.display_name}"'))
    if existing_engines:
        target = existing_engines[0]
        print(f"Updating existing ReasoningEngine: {target.resource_name}")
        remote_agent = target.update(
            agent_engine=app,
            requirements=requirements,
            extra_packages=["agent.py"],
            display_name=args.display_name,
            description="Enterprise Cloud FinOps & Governance ADK Agent instrumented for Agent Platform Evaluations.",
            env_vars=env_vars,
        )
    else:
        print(f"Creating new ReasoningEngine '{args.display_name}' in {args.project_id}/{args.region}...")
        remote_agent = vertexai.agent_engines.create(
            agent_engine=app,
            requirements=requirements,
            extra_packages=["agent.py"],
            display_name=args.display_name,
            description="Enterprise Cloud FinOps & Governance ADK Agent instrumented for Agent Platform Evaluations.",
            env_vars=env_vars,
            service_account=service_account,
        )

    metadata: dict[str, Any] = {
        "resource_name": remote_agent.resource_name,
        "display_name": args.display_name,
        "project_id": args.project_id,
        "region": args.region,
        "service_account": service_account,
        "staging_bucket": staging_bucket,
    }
    print(json.dumps(metadata, indent=2))
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(metadata, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
