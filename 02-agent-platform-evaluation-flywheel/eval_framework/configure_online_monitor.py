"""Configures an Agent Platform Continuous Online Monitor (`OnlineEvaluator`) and seeds live OTel traces.

1. Sends realistic live user queries to the deployed `ReasoningEngine` so it emits
   OpenTelemetry GenAI v1.39+ spans (`gen_ai_latest_experimental` + `EVENT_ONLY`)
   into Cloud Trace and Cloud Logging.
2. Idempotently creates or verifies an `OnlineEvaluator` resource via the
   Agent Platform `v1beta1` REST API (`projects/{project}/locations/{region}/onlineEvaluators`)
   sampling production traces every 10 minutes and exporting scores to Cloud Monitoring
   (`aiplatform.googleapis.com/online_evaluator/scores`).
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import google.auth
import google.auth.transport.requests
import vertexai
from vertexai import agent_engines


def _authorized_request(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    quota_project: str | None = None,
) -> dict[str, Any]:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }
    if quota_project:
        headers["x-goog-user-project"] = quota_project
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {err.code} calling {method} {url}: {detail}") from err


def ensure_observability_trace_bucket(project_id: str, trace_location: str = "us") -> str:
    """Ensures the Cloud Observability `_Trace` bucket (`Spans/_AllSpans`) exists.

    Agent Platform `OnlineEvaluator` queries OpenTelemetry traces via Logging Analytics on
    `projects/{project_id}/locations/us/buckets/_Trace/datasets/Spans/views/_AllSpans`.
    In newly created GCP projects where Cloud Trace UI has not yet been opened, the `_Trace`
    Observability bucket may not be initialized yet—causing the first monitor run to fail
    with `Trace view not found`. Calling the Observability API initializes `_Trace` + `_AllSpans`.
    """
    obs_base = f"https://observability.googleapis.com/v1/projects/{project_id}/locations/{trace_location}"
    view_name = f"projects/{project_id}/locations/{trace_location}/buckets/_Trace/datasets/Spans/views/_AllSpans"
    try:
        bucket = _authorized_request("GET", f"{obs_base}/buckets/_Trace", quota_project=project_id)
        if bucket.get("name"):
            print(f"Verified Cloud Observability _Trace bucket: {bucket['name']}")
    except RuntimeError as err:
        if "HTTP 404" not in str(err):
            raise
        print(f"Initializing Cloud Observability _Trace bucket in locations/{trace_location}...")
        op = _authorized_request(
            "POST",
            f"{obs_base}/buckets?bucketId=_Trace",
            {
                "displayName": "Default Trace Bucket",
                "description": "OpenTelemetry trace storage bucket backing Agent Platform OnlineEvaluator",
            },
            quota_project=project_id,
        )
        op_name = op.get("name", "")
        if op_name and not op.get("done", False):
            for _ in range(30):
                time.sleep(2)
                polled = _authorized_request(
                    "GET",
                    f"https://observability.googleapis.com/v1/{op_name}",
                    quota_project=project_id,
                )
                if polled.get("done"):
                    if "error" in polled:
                        raise RuntimeError(f"Observability _Trace bucket creation failed: {polled['error']}")
                    break

    # Wait for the system-created `Spans/_AllSpans` view (~60-90s on first creation)
    view_url = f"https://observability.googleapis.com/v1/{view_name}"
    for _ in range(45):
        try:
            view_obj = _authorized_request("GET", view_url, quota_project=project_id)
            if view_obj.get("name"):
                print(f"Verified Cloud Observability trace view: {view_name}")
                return view_name
        except RuntimeError as err:
            if "HTTP 404" not in str(err):
                raise
        time.sleep(3)
    print(f"Initialized Cloud Observability trace bucket; waiting for {view_name} propagation.")
    return view_name


def seed_live_traces(project_id: str, region: str, reasoning_engine_name: str) -> list[str]:
    """Invokes the deployed ReasoningEngine across 3 prompts to emit live OTel traces."""
    vertexai.init(project=project_id, location=region)
    remote_agent = agent_engines.get(reasoning_engine_name)
    prompts = [
        "Audit PRJ-ALPHA-101 spend for 2026-10, check our budget overage policy, and open the required remediation ticket.",
        "What is our data residency governance policy ID and what does it require for evaluation storage?",
        "Request 8 NVIDIA_L4_GPUS for PRJ-BETA-204 (check 2026-10 spend and GPU quota policy). Business justification: Supporting Q4 ML evaluation pipeline.",
    ]
    previews: list[str] = []
    for idx, prompt in enumerate(prompts, 1):
        chunks: list[str] = []
        for event in remote_agent.stream_query(user_id=f"eval-trace-seed-{idx}", message=prompt):
            if isinstance(event, dict):
                content = event.get("content", {})
                for part in content.get("parts", []) if isinstance(content, dict) else []:
                    if isinstance(part, dict) and part.get("text"):
                        chunks.append(part["text"])
        text = "".join(chunks).strip()
        print(f"  [Live Trace {idx}] {len(text)} chars -> {text[:140]}...")
        previews.append(text[:200])
    return previews


def ensure_online_evaluator(
    project_id: str,
    region: str,
    reasoning_engine_name: str,
    display_name: str = "finops-governance-online-monitor",
    judge_model_id: str = "gemini-3.8-flash",
    sampling_percentage: int = 100,
    max_samples_per_run: int = 20,
) -> dict[str, Any]:
    """Creates or returns an active OnlineEvaluator attached to `reasoning_engine_name`."""
    base_url = f"https://{region}-aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/{region}"
    listed = _authorized_request("GET", f"{base_url}/onlineEvaluators")
    for item in listed.get("onlineEvaluators", []) or []:
        if item.get("agentResource") == reasoning_engine_name or item.get("displayName") == display_name:
            if item.get("state") == "FAILED":
                print(
                    f"Existing OnlineEvaluator {item.get('name')} is in state=FAILED "
                    f"({item.get('stateDetails')}); deleting to recreate cleanly..."
                )
                del_op = _authorized_request(
                    "DELETE", f"https://{region}-aiplatform.googleapis.com/v1beta1/{item['name']}"
                )
                del_op_name = del_op.get("name", "")
                if del_op_name and not del_op.get("done", False):
                    for _ in range(20):
                        time.sleep(2)
                        polled_del = _authorized_request(
                            "GET", f"https://{region}-aiplatform.googleapis.com/v1beta1/{del_op_name}"
                        )
                        if polled_del.get("done"):
                            break
                continue
            print(f"Reusing existing OnlineEvaluator: {item.get('name')} (state={item.get('state')})")
            return item

    payload: dict[str, Any] = {
        "displayName": display_name,
        "agentResource": reasoning_engine_name,
        "cloudObservability": {
            "traceScope": {},
            "openTelemetry": {
                "semconvVersion": "1.39.0",
            },
        },
        "metricSources": [
            {"metric": {"predefinedMetricSpec": {"metricSpecName": "final_response_quality_v1"}}},
            {"metric": {"predefinedMetricSpec": {"metricSpecName": "tool_use_quality_v1"}}},
            {"metric": {"predefinedMetricSpec": {"metricSpecName": "hallucination_v1"}}},
            {"metric": {"predefinedMetricSpec": {"metricSpecName": "safety_v1"}}},
        ],
        "config": {
            "maxEvaluatedSamplesPerRun": str(max_samples_per_run),
            "randomSampling": {
                "percentage": sampling_percentage,
            },
        },
    }

    print(f"Creating OnlineEvaluator '{display_name}' for {reasoning_engine_name}...")
    op = _authorized_request("POST", f"{base_url}/onlineEvaluators", payload)
    op_name = op.get("name", "")
    if op_name and not op.get("done", False):
        op_url = f"https://{region}-aiplatform.googleapis.com/v1beta1/{op_name}"
        for _ in range(30):
            time.sleep(3)
            polled = _authorized_request("GET", op_url)
            if polled.get("done"):
                if "error" in polled:
                    raise RuntimeError(f"OnlineEvaluator LRO failed: {polled['error']}")
                return polled.get("response", polled)
    return op.get("response", op)


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure Continuous OnlineEvaluator & seed OTel traces.")
    parser.add_argument("--project-id", default=os.environ.get("GCP_PROJECT_ID", ""), required=False)
    parser.add_argument("--region", default=os.environ.get("GCP_REGION", "us-central1"))
    parser.add_argument("--reasoning-engine", default=os.environ.get("REASONING_ENGINE_RESOURCE", ""), required=False)
    parser.add_argument("--skip-trace-seed", action="store_true")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    if not args.project_id or not args.reasoning_engine:
        raise SystemExit("Provide --project-id and --reasoning-engine.")

    print("=== Step 0: Verifying Cloud Observability _Trace Bucket (_AllSpans View) ===")
    trace_view = ensure_observability_trace_bucket(args.project_id, trace_location="us")

    trace_previews: list[str] = []
    if not args.skip_trace_seed:
        print("\n=== Step 1: Sending Live Queries to Emit OpenTelemetry GenAI v1.39+ Traces ===")
        trace_previews = seed_live_traces(args.project_id, args.region, args.reasoning_engine)

    print("\n=== Step 2: Provisioning Agent Platform OnlineEvaluator ===")
    evaluator = ensure_online_evaluator(
        project_id=args.project_id,
        region=args.region,
        reasoning_engine_name=args.reasoning_engine,
    )
    summary = {
        "online_evaluator_name": evaluator.get("name", ""),
        "display_name": evaluator.get("displayName", ""),
        "state": evaluator.get("state", ""),
        "agent_resource": evaluator.get("agentResource", args.reasoning_engine),
        "trace_view": trace_view,
        "seeded_trace_count": len(trace_previews),
    }
    print(json.dumps(summary, indent=2))
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
