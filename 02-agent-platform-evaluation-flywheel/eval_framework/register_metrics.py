"""Registers reusable custom metrics in the Agent Platform Metric Registry.

Demonstrates the two custom metric types supported alongside Google's predefined
`types.RubricMetric` autoraters:
1. Custom `LLMMetric`: Judge LLM rubric verifying authoritative `POL-*` policy
   citations when governance or quota decisions are rendered.
2. Custom `CodeExecutionMetric`: Deterministic Python validator ensuring any
   cited `QTA-*` or `REM-*` confirmation IDs match canonical 6-hex format
   rather than fabricated placeholders (`QTA-12345`, `REM-001`).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from agentplatform import Client, types

POLICY_CITATION_PROMPT_TEMPLATE = """You are an enterprise governance auditor evaluating an AI agent response.

User Prompt:
{prompt}

Agent Response:
{response}

Evaluation Rubric:
- If the user prompt asks about cloud budget overages, GPU quota requests, public egress violations, or data residency rules, the agent response MUST cite the authoritative policy ID (`POL-FIN-001`, `POL-GPU-002`, `POL-SEC-003`, or `POL-DATA-004`).
- If the user prompt is completely out-of-scope (for example, personal finance/stocks) and the agent politely declines without inventing a policy ID, award full credit (`1.0`).
- Return ONLY a JSON object with keys `"score"` (float `1.0` for compliant, `0.5` for partial explanation without policy ID, `0.0` for missing/fabricated policy ID) and `"explanation"` (one concise sentence).
"""

POLICY_CITATION_PARSER = """import json, re
def parse_results(responses):
    raw = responses[0] if isinstance(responses, list) and responses else str(responses)
    match = re.search(r'\\{.*\\}', raw, re.DOTALL)
    payload = json.loads(match.group(0)) if match else {"score": 0.0, "explanation": raw[:200]}
    return {
        "score": float(payload.get("score", 0.0)),
        "explanation": str(payload.get("explanation", "")),
    }
"""

AUDIT_ID_FORMAT_CODE = """import re
def evaluate(instance: dict) -> float:
    response_text = str(instance.get("response", ""))
    mentioned_ids = re.findall(r"\\b(?:QTA|REM)-[A-Za-z0-9]+\\b", response_text)
    if not mentioned_ids:
        return 1.0
    valid_pattern = re.compile(r"^(?:QTA|REM)-[0-9A-F]{6}$")
    return 1.0 if all(valid_pattern.match(token) for token in mentioned_ids) else 0.0
"""


def register_or_get_custom_metrics(
    project_id: str,
    location: str,
    judge_model_id: str = "gemini-3.8-flash",
) -> dict[str, str]:
    """Idempotently creates or retrieves custom evaluation metrics in Metric Registry."""
    client = Client(project=project_id, location=location)
    judge_model_uri = (
        judge_model_id
        if judge_model_id.startswith("projects/")
        else f"projects/{project_id}/locations/global/publishers/google/models/{judge_model_id}"
    )

    existing_by_display_name: dict[str, str] = {}
    try:
        listed = client.evals.list_evaluation_metrics()
        for item in getattr(listed, "evaluation_metrics", []) or []:
            dname = getattr(item, "display_name", None)
            rname = getattr(item, "name", None)
            if dname and rname:
                existing_by_display_name[dname] = rname
    except Exception as exc:
        print(f"Note: list_evaluation_metrics returned warning ({exc}); proceeding to create.")

    registered: dict[str, str] = {}

    llm_display = "finops_policy_citation_compliance"
    if llm_display in existing_by_display_name:
        registered[llm_display] = existing_by_display_name[llm_display]
        print(f"Reusing existing Custom LLMMetric: {registered[llm_display]}")
    else:
        llm_metric = types.LLMMetric(
            name=llm_display,
            prompt_template=POLICY_CITATION_PROMPT_TEMPLATE,
            result_parsing_function=POLICY_CITATION_PARSER,
            judge_model=judge_model_uri,
        )
        resource_name = client.evals.create_evaluation_metric(
            display_name=llm_display,
            description="Verifies that governance and quota responses cite authoritative POL-* policy IDs.",
            metric=llm_metric,
        )
        registered[llm_display] = resource_name
        print(f"Registered Custom LLMMetric: {resource_name}")

    code_display = "finops_audit_id_format_hygiene"
    if code_display in existing_by_display_name:
        registered[code_display] = existing_by_display_name[code_display]
        print(f"Reusing existing Custom CodeExecutionMetric: {registered[code_display]}")
    else:
        code_metric = types.CodeExecutionMetric(
            name=code_display,
            custom_function=AUDIT_ID_FORMAT_CODE,
        )
        resource_name = client.evals.create_evaluation_metric(
            display_name=code_display,
            description="Validates that any cited QTA-* or REM-* confirmation IDs match deterministic 6-hex format.",
            metric=code_metric,
        )
        registered[code_display] = resource_name
        print(f"Registered Custom CodeExecutionMetric: {resource_name}")

    return registered


def main() -> None:
    parser = argparse.ArgumentParser(description="Register custom metrics in Agent Platform Metric Registry.")
    parser.add_argument("--project-id", default=os.environ.get("GCP_PROJECT_ID", ""), required=False)
    parser.add_argument("--location", default=os.environ.get("GCP_REGION", "us-central1"))
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()
    if not args.project_id:
        raise SystemExit("Set --project-id or GCP_PROJECT_ID.")

    result: dict[str, Any] = register_or_get_custom_metrics(args.project_id, args.location)
    print(json.dumps(result, indent=2))
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
