#!/usr/bin/env python3
"""End-to-end verification script for Direction 2 (Outbound GCP Agent Runtime -> 3P A2A Agent).

Modes:
1. Direct smoke test of the external 3P A2A Agent Card & JSON-RPC endpoint:
     python3 scripts/test_outbound_gcp_to_3p.py \
       --direct-3p-url "https://<user>-external-a2a-lms-agent.hf.space" \
       --bearer-token "<A2A_INBOUND_TOKEN>"
2. Full orchestration test via GCP Agent Runtime (ReasoningEngine resolves the 3P
   agent from Agent Registry and delegates the course lookup over A2A):
     python3 scripts/test_outbound_gcp_to_3p.py \
       --project "YOUR_PROJECT_ID" \
       --location "us-central1" \
       --reasoning-engine "projects/<PROJECT>/locations/us-central1/reasoningEngines/<ID>"
"""

from __future__ import annotations

import argparse
import json
import urllib.request
import uuid


def smoke_test_direct_3p(base_url: str, bearer_token: str | None, prompt: str) -> None:
    base = base_url.rstrip("/")
    card_url = f"{base}/.well-known/agent-card.json"
    print(f"[1/2] Fetching 3P A2A Agent Card from: {card_url}")
    with urllib.request.urlopen(card_url, timeout=30) as resp:
        card = json.loads(resp.read().decode("utf-8"))
    print(json.dumps(card, indent=2))

    a2a_url = f"{base}/a2a"
    print(f"\n[2/2] Sending A2A JSON-RPC message/send to: {a2a_url}")
    rpc_body = {
        "jsonrpc": "2.0",
        "id": f"smoke-{uuid.uuid4().hex[:8]}",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": f"msg-{uuid.uuid4().hex[:8]}",
                "kind": "message",
                "parts": [{"kind": "text", "text": prompt}],
            }
        },
    }
    headers = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    req = urllib.request.Request(
        a2a_url,
        data=json.dumps(rpc_body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    print(json.dumps(result, indent=2))


def test_via_reasoning_engine(project: str, location: str, reasoning_engine: str, prompt: str) -> None:
    import vertexai
    from vertexai.preview.reasoning_engines import ReasoningEngine

    vertexai.init(project=project, location=location)
    engine = ReasoningEngine(reasoning_engine)
    print(f"Querying GCP Agent Runtime ({reasoning_engine}) with prompt:\n  {prompt}\n")
    for event in engine.stream_query(user_id="demo-instructor", message=prompt):
        print(event)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Outbound GCP -> 3P A2A Agent Interoperability.")
    parser.add_argument("--direct-3p-url", help="Direct HTTPS URL of the 3P A2A agent for smoke testing.")
    parser.add_argument("--bearer-token", help="Bearer token for direct 3P A2A smoke test.")
    parser.add_argument("--project", help="Google Cloud Project ID.")
    parser.add_argument("--location", default="us-central1", help="Google Cloud region.")
    parser.add_argument("--reasoning-engine", help="Deployed ReasoningEngine resource name.")
    parser.add_argument(
        "--prompt",
        default="Look up the syllabus and next assignment deadline for CS-401, and check our campus late_submission policy.",
        help="Prompt to execute.",
    )
    args = parser.parse_args()

    if args.direct_3p_url:
        smoke_test_direct_3p(args.direct_3p_url, args.bearer_token, args.prompt)
        return

    if not (args.project and args.reasoning_engine):
        parser.error("Provide either --direct-3p-url or both --project and --reasoning-engine.")

    test_via_reasoning_engine(args.project, args.location, args.reasoning_engine, args.prompt)


if __name__ == "__main__":
    main()
