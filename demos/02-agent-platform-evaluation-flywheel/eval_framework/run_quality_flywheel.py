"""End-to-End Agent Platform Evaluation & Quality Flywheel Runner.

Executes the full 5-stage Gemini Enterprise Agent Platform Quality Flywheel:
1. Metric Registry & EvaluationExperiment Setup (`register_metrics.py` +
   `create_evaluation_experiment`): Registers Custom `LLMMetric` & `CodeExecutionMetric`
   and creates/reuses a shared `EvaluationExperiment` container in Google Cloud Console
   so every baseline, candidate, and simulation run can be compared side-by-side.
2. Golden Regression A/B Comparison (`v1_baseline` vs. `v2_optimized`):
   Runs `client.evals.run_inference()` + `client.evals.evaluate()` across
   `FINAL_RESPONSE_QUALITY`, `TOOL_USE_QUALITY`, `HALLUCINATION`, `SAFETY`,
   and registered custom metrics (explicitly pinning the autorater judge to GA
   `gemini-3.8-flash` on `locations/global` with `allow_cross_region_model=True`),
   and logs both runs into the `EvaluationExperiment`.
3. Automatic Failure / Loss Cluster Analysis (`generate_loss_clusters` via
   `location="global"`): Classifies `v1_baseline` failures into actionable
   Google loss taxonomies (e.g. `Hallucination of Parameter Value`,
   `Omission of Required Tool Call`).
4. Cold-Start Synthetic User Simulation & Multi-Turn Evaluation:
   Synthesizes multi-turn user personas + hidden conversation plans from
   `AgentInfo.load_from_agent()` via `generate_conversation_scenarios()`,
   simulates multi-turn sessions (`max_turn=4`), scores `MULTI_TURN_TASK_SUCCESS`,
   `MULTI_TURN_TOOL_USE_QUALITY`, and `MULTI_TURN_TRAJECTORY_QUALITY`, and
   logs the multi-turn run into the same `EvaluationExperiment`.
5. Deterministic CI/CD Ship Gate: Enforces minimum score thresholds and outputs
   a structured JSON + Markdown Quality Scorecard.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import google.auth
import google.auth.transport.requests
import pandas as pd
from agentplatform import Client, types

# Ensure sibling package `adk_agent` is importable regardless of working directory
DEMO_ROOT = Path(__file__).resolve().parents[1]
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

from adk_agent.agent import build_root_agent  # noqa: E402
from eval_framework.register_metrics import register_or_get_custom_metrics  # noqa: E402


def _extract_summary_scores(eval_result: types.EvaluationResult) -> dict[str, float]:
    """Extracts {metric_name: mean_score} from an EvaluationResult object."""
    scores: dict[str, float] = {}
    summary_list = getattr(eval_result, "summary_metrics", None) or []
    for sm in summary_list:
        metrics_map = getattr(sm, "metrics", None)
        if isinstance(metrics_map, dict):
            for m_name, m_val in metrics_map.items():
                if isinstance(m_val, dict) and m_val.get("mean_score") is not None:
                    scores[m_name] = round(float(m_val["mean_score"]), 4)
                elif hasattr(m_val, "mean_score") and m_val.mean_score is not None:
                    scores[m_name] = round(float(m_val.mean_score), 4)
        m_name = getattr(sm, "metric_name", None)
        mean_val = getattr(sm, "mean_score", None)
        if m_name and mean_val is not None:
            scores[str(m_name)] = round(float(mean_val), 4)
    return scores


def _extract_loss_clusters(loss_resp: Any) -> list[dict[str, Any]]:
    """Extracts human-readable failure clusters from GenerateLossClustersResponse."""
    clusters_out: list[dict[str, Any]] = []
    for res in getattr(loss_resp, "results", None) or []:
        for cluster in getattr(res, "clusters", None) or []:
            clusters_out.append(
                {
                    "title": getattr(cluster, "title", None)
                    or getattr(cluster, "label", None)
                    or getattr(cluster, "loss_pattern", "Cluster"),
                    "category": getattr(cluster, "category", ""),
                    "description": getattr(cluster, "description", "")
                    or getattr(cluster, "summary", ""),
                    "count": getattr(cluster, "count", None)
                    or len(getattr(cluster, "examples", []) or []),
                }
            )
    return clusters_out


def _sanitize_multiturn_trace_events(dataset: types.EvaluationDataset) -> types.EvaluationDataset:
    """Filters out empty ADK turn-boundary metadata events where `content` is unset.

    ADK multi-turn sessions can emit completion/state-delta Event objects with `content=None`,
    which Agent Platform server-side multi-turn autoraters reject with
    `turns[i].events[j].content: Required field is not set`.
    """
    df = getattr(dataset, "eval_dataset_df", None)
    if df is None or "agent_data" not in df.columns:
        return dataset

    cleaned_cells: list[Any] = []
    for cell in df["agent_data"]:
        if isinstance(cell, types.evals.AgentData):
            cell_dict = cell.model_dump(exclude_none=True)
        elif isinstance(cell, dict):
            cell_dict = dict(cell)
        else:
            cleaned_cells.append(cell)
            continue

        turns = cell_dict.get("turns")
        if isinstance(turns, list):
            new_turns = []
            for turn in turns:
                if not isinstance(turn, dict):
                    new_turns.append(turn)
                    continue
                events = turn.get("events")
                if isinstance(events, list):
                    valid_events = [
                        ev
                        for ev in events
                        if isinstance(ev, dict)
                        and ev.get("content")
                        and (
                            not isinstance(ev.get("content"), dict)
                            or ev["content"].get("parts")
                        )
                    ]
                    turn = {**turn, "events": valid_events}
                new_turns.append(turn)
            cell_dict["turns"] = new_turns
        cleaned_cells.append(cell_dict)

    df["agent_data"] = cleaned_cells
    return dataset


def ensure_evaluation_experiment(
    client: Client,
    display_name: str = "finops-governance-quality-flywheel",
    reasoning_engine_id: str = "",
    region: str = "us-central1",
) -> str:
    """Idempotently retrieves or creates a shared EvaluationExperiment in Cloud Console.

    Attaches `vertex-ai-evaluation-agent-engine-id` and
    `vertex-ai-evaluation-agent-engine-location` labels whenever `reasoning_engine_id`
    is provided so Google Cloud Console surfaces the experiment under
    **Agent Platform -> Evaluations -> Experiments** as well as the deployed agent's
    **Evaluation** tab.
    """
    try:
        listed = client.evals.list_evaluation_experiments()
        items = getattr(listed, "evaluation_experiments", None) or listed
        for exp in items:
            if getattr(exp, "display_name", None) == display_name and getattr(exp, "name", None):
                exp_labels = getattr(exp, "labels", None) or {}
                if reasoning_engine_id and exp_labels.get("vertex-ai-evaluation-agent-engine-id") != reasoning_engine_id:
                    continue
                print(f"Reusing existing EvaluationExperiment: {exp.name}")
                return str(exp.name)
    except Exception as exc:
        print(f"Note: list_evaluation_experiments returned warning ({exc}); creating new experiment.")

    exp_labels: dict[str, str] = {
        "agent": "finops-governance",
        "framework": "adk",
        "model": "gemini-3-8-flash",
    }
    if reasoning_engine_id:
        exp_labels["vertex-ai-evaluation-agent-engine-id"] = reasoning_engine_id
        exp_labels["vertex-ai-evaluation-agent-engine-location"] = region

    created = client.evals.create_evaluation_experiment(
        display_name=display_name,
        labels=exp_labels,
        metadata={
            "domain": "enterprise-cloud-finops-governance",
            "suites": ["golden-regression", "multi-turn-user-simulation"],
        },
    )
    print(f"Created EvaluationExperiment: {created.name}")
    return str(created.name or "")


def cleanup_orphaned_code_exec_sandboxes(project_id: str, region: str) -> list[str]:
    """Deletes leftover `genai-eval-service-code-execution` ReasoningEngine shells.

    When `client.evals.evaluate()` runs a Custom `CodeExecutionMetric`, the Evaluation Service
    provisions ephemeral `genai-eval-service-code-execution` ReasoningEngine sandboxes in the
    project. If concurrent cold-start workers leave empty shells behind after child sandboxes
    are torn down, this helper removes them so Deployments stays uncluttered.
    """
    deleted: list[str] = []
    try:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        headers = {"Authorization": f"Bearer {creds.token}"}
        base = f"https://{region}-aiplatform.googleapis.com/v1beta1"
        list_url = f"{base}/projects/{project_id}/locations/{region}/reasoningEngines"
        with urllib.request.urlopen(urllib.request.Request(list_url, headers=headers), timeout=20) as resp:
            items = json.loads(resp.read().decode("utf-8")).get("reasoningEngines", []) or []
        for item in items:
            if item.get("displayName") != "genai-eval-service-code-execution":
                continue
            re_name = item.get("name", "")
            if not re_name:
                continue
            sb_url = f"{base}/{re_name}/sandboxEnvironments"
            with urllib.request.urlopen(urllib.request.Request(sb_url, headers=headers), timeout=20) as sb_resp:
                sandboxes = json.loads(sb_resp.read().decode("utf-8")).get("sandboxEnvironments", []) or []
            if not sandboxes:
                del_req = urllib.request.Request(f"{base}/{re_name}?force=true", method="DELETE", headers=headers)
                with urllib.request.urlopen(del_req, timeout=30):
                    deleted.append(re_name.split("/")[-1])
        if deleted:
            print(f"Cleaned up {len(deleted)} idle genai-eval-service-code-execution sandbox shell(s): {deleted}")
    except Exception as exc:
        print(f"Note: sandbox shell cleanup skipped ({exc}).")
    return deleted


def build_markdown_scorecard(report: dict[str, Any]) -> str:
    """Formats a concise Markdown Quality Flywheel Scorecard for PRs and runbooks."""
    runs_str = ", ".join(f"`{r}`" for r in report.get("managed_evaluation_runs", [])) or "`N/A`"
    lines = [
        "# Agent Platform Evaluation & Quality Flywheel Scorecard",
        "",
        f"- **Region:** `{report['region']}`",
        f"- **Target Mode:** `{report['target_mode']}`",
        f"- **Autorater Judge Model:** `{report['judge_model']}`",
        f"- **Evaluation Experiment:** `{report.get('evaluation_experiment') or 'N/A'}`",
        f"- **Managed Evaluation Runs Logged:** {runs_str}",
        f"- **Ship Gate Verdict:** **{'PASS' if report['gate_passed'] else 'FAIL'}**",
        "",
        "## 1. Golden Regression Suite: `v1_baseline` vs. `v2_optimized`",
        "",
        "| Metric | `v1_baseline` (Naive Prompt) | `v2_optimized` (Hardened Prompt) | Delta |",
        "| :--- | :--- | :--- | :--- |",
    ]
    v1_scores = report.get("golden_v1_baseline_scores", {})
    v2_scores = report.get("golden_v2_optimized_scores", {})
    all_keys = sorted(set(v1_scores.keys()) | set(v2_scores.keys()))
    for k in all_keys:
        s1 = v1_scores.get(k)
        s2 = v2_scores.get(k)
        s1_str = f"{s1:.2f}" if isinstance(s1, (int, float)) else "N/A"
        s2_str = f"{s2:.2f}" if isinstance(s2, (int, float)) else "N/A"
        delta_str = f"{(s2 - s1):+.2f}" if isinstance(s1, (int, float)) and isinstance(s2, (int, float)) else "N/A"
        lines.append(f"| `{k}` | {s1_str} | {s2_str} | **{delta_str}** |")

    if report.get("v1_loss_clusters"):
        lines.extend(
            [
                "",
                "## 2. Automatic Loss Cluster Analysis on `v1_baseline`",
                "",
                "| Failure Cluster / Loss Pattern | Category | Count | Diagnostic Summary |",
                "| :--- | :--- | :--- | :--- |",
            ]
        )
        for c in report["v1_loss_clusters"]:
            lines.append(
                f"| **{c['title']}** | `{c.get('category') or 'agent_behavior'}` | {c.get('count', 0)} | {c.get('description', '')} |"
            )

    if report.get("simulation_multiturn_scores"):
        lines.extend(
            [
                "",
                "## 3. Cold-Start Synthetic User Simulation (Multi-Turn)",
                "",
                "| Multi-Turn Metric | Score |",
                "| :--- | :--- |",
            ]
        )
        for k, val in sorted(report["simulation_multiturn_scores"].items()):
            lines.append(f"| `{k}` | **{val:.2f}** |")

    lines.extend(
        [
            "",
            "## 4. CI/CD Ship Gate Checks",
            "",
            "| Gate Check | Actual | Threshold | Status |",
            "| :--- | :--- | :--- | :--- |",
        ]
    )
    for chk in report.get("gate_checks", []):
        lines.append(
            f"| `{chk['metric']}` | {chk['actual']:.2f} | >= {chk['threshold']:.2f} | **{'PASS' if chk['passed'] else 'FAIL'}** |"
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Agent Platform Quality Flywheel.")
    parser.add_argument("--project-id", default=os.environ.get("GCP_PROJECT_ID", ""), required=False)
    parser.add_argument("--region", default=os.environ.get("GCP_REGION", "us-central1"))
    parser.add_argument(
        "--reasoning-engine",
        default=os.environ.get("REASONING_ENGINE_RESOURCE", ""),
        help="Optional deployed ReasoningEngine resource name (`projects/.../locations/.../reasoningEngines/...`) for v2_optimized.",
    )
    parser.add_argument(
        "--judge-model",
        default=os.environ.get("EVAL_JUDGE_MODEL", "gemini-3.8-flash"),
        help="GA Gemini autorater & user simulator model (served via global endpoint).",
    )
    parser.add_argument(
        "--experiment-display-name",
        default="finops-governance-quality-flywheel",
        help="Display name of the Agent Platform EvaluationExperiment grouping all runs.",
    )
    parser.add_argument("--gcs-dest", default=os.environ.get("EVAL_GCS_DEST", ""))
    parser.add_argument("--sim-count", type=int, default=3, help="Number of synthetic multi-turn scenarios to generate.")
    parser.add_argument("--skip-baseline", action="store_true", help="Skip v1_baseline comparison to run faster in CI.")
    parser.add_argument("--skip-simulation", action="store_true", help="Skip multi-turn user simulation.")
    parser.add_argument("--min-response-quality", type=float, default=0.70)
    parser.add_argument("--min-tool-use-quality", type=float, default=0.70)
    parser.add_argument("--min-safety", type=float, default=0.95)
    parser.add_argument("--output-dir", default="/tmp/eval_flywheel_results")
    args = parser.parse_args()

    if not args.project_id:
        raise SystemExit("Set --project-id or GCP_PROJECT_ID.")

    os.environ["GOOGLE_CLOUD_PROJECT"] = args.project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"

    judge_model_uri = (
        args.judge_model
        if args.judge_model.startswith("projects/")
        else f"projects/{args.project_id}/locations/global/publishers/google/models/{args.judge_model}"
    )
    step_autorater_cfgs = {
        "intent_extraction": {"autorater_model": judge_model_uri},
        "rubric_generation": {"autorater_model": judge_model_uri},
        "rubric_validation": {"autorater_model": judge_model_uri},
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gcs_dest = args.gcs_dest or f"gs://{args.project_id}-agent-eval-flywheel/runs"

    regional_client = Client(project=args.project_id, location=args.region)
    # Note: generate_loss_clusters requires the `global` endpoint per SDK specification
    global_client = Client(project=args.project_id, location="global")

    print("=== Step 1: Metric Registry & EvaluationExperiment Setup ===")
    custom_metrics_map = register_or_get_custom_metrics(
        args.project_id, args.region, judge_model_id=args.judge_model
    )
    reasoning_engine_id = args.reasoning_engine.split("/")[-1] if args.reasoning_engine else ""
    base_agent_labels: dict[str, str] = {}
    if reasoning_engine_id:
        base_agent_labels["vertex-ai-evaluation-agent-engine-id"] = reasoning_engine_id
        base_agent_labels["vertex-ai-evaluation-agent-engine-location"] = args.region

    experiment_name = ensure_evaluation_experiment(
        regional_client,
        display_name=args.experiment_display_name,
        reasoning_engine_id=reasoning_engine_id,
        region=args.region,
    )
    managed_runs: list[str] = []

    golden_path = Path(__file__).resolve().parent / "datasets" / "golden_eval_cases.json"
    golden_rows = json.loads(golden_path.read_text(encoding="utf-8"))
    golden_df = pd.DataFrame([{"prompt": row["prompt"]} for row in golden_rows])

    single_turn_metrics: list[Any] = [
        types.RubricMetric.FINAL_RESPONSE_QUALITY(
            judge_model=judge_model_uri,
            judge_model_step_configs=step_autorater_cfgs,
        ),
        types.RubricMetric.TOOL_USE_QUALITY(
            judge_model=judge_model_uri,
            judge_model_step_configs=step_autorater_cfgs,
        ),
        types.RubricMetric.HALLUCINATION(judge_model=judge_model_uri),
        types.RubricMetric.SAFETY(judge_model=judge_model_uri),
        *[
            types.Metric(
                name=m_name,
                metric_resource_name=rname,
                judge_model=judge_model_uri if "llm" in m_name or "policy" in m_name else None,
            )
            for m_name, rname in custom_metrics_map.items()
        ],
    ]

    v2_local_agent = build_root_agent(profile="v2_optimized")
    v2_agent_info = types.evals.AgentInfo.load_from_agent(agent=v2_local_agent)

    v1_scores: dict[str, float] = {}
    v1_loss_clusters: list[dict[str, Any]] = []

    if not args.skip_baseline:
        print("\n=== Step 2A: Evaluating v1_baseline (Naive Prompt) on Golden Suite ===")
        v1_agent = build_root_agent(profile="v1_baseline")
        v1_agent_info = types.evals.AgentInfo.load_from_agent(agent=v1_agent)
        v1_traces = regional_client.evals.run_inference(
            src=golden_df,
            agent=v1_agent,
            config={"allow_cross_region_model": True},
        )
        v1_traces.candidate_name = "finops_governance_v1_baseline"
        v1_eval_result = regional_client.evals.evaluate(
            dataset=v1_traces,
            metrics=single_turn_metrics,
            config={"allow_cross_region_model": True},
            agent_info=v1_agent_info,
        )
        v1_scores = _extract_summary_scores(v1_eval_result)
        print("v1_baseline summary scores:", json.dumps(v1_scores, indent=2))

        if experiment_name:
            try:
                # Note: When `dataset=v1_traces` already contains `agent_data.agents`,
                # omit `agent_info` so the API does not reject duplicate agent_configs.
                v1_run = regional_client.evals.create_evaluation_run(
                    display_name="v1-baseline-golden-regression",
                    evaluation_experiment=experiment_name,
                    dataset=v1_traces,
                    dest=f"{gcs_dest}/v1_baseline_golden",
                    metrics=single_turn_metrics[:4],
                    loss_analysis_metrics=[single_turn_metrics[0], single_turn_metrics[1]],
                    labels={**base_agent_labels, "candidate": "v1-baseline", "suite": "golden"},
                    config={"allow_cross_region_model": True},
                )
                if v1_run.name:
                    managed_runs.append(v1_run.name.split("/")[-1])
                    print(f"Logged v1_baseline EvaluationRun into Experiment: {v1_run.name}")
            except Exception as exc:
                print(f"Note: v1_baseline create_evaluation_run info ({exc}).")

        print("\n=== Step 2B: Running Automatic Loss Cluster Analysis on v1_baseline ===")
        for loss_metric in (
            single_turn_metrics[0],
            single_turn_metrics[1],
        ):
            try:
                loss_resp = global_client.evals.generate_loss_clusters(
                    eval_result=v1_eval_result,
                    metric=loss_metric,
                )
                extracted = _extract_loss_clusters(loss_resp)
                if extracted:
                    v1_loss_clusters.extend(extracted)
            except Exception as exc:
                print(f"Note: generate_loss_clusters ({getattr(loss_metric, 'name', 'metric')}) info ({exc}).")
        print(f"Identified {len(v1_loss_clusters)} loss cluster(s) on v1_baseline.")

    target_agent: Any = args.reasoning_engine if args.reasoning_engine else v2_local_agent
    target_mode = "deployed_reasoning_engine" if args.reasoning_engine else "local_adk_agent"

    print(f"\n=== Step 3: Evaluating v2_optimized ({target_mode}) on Golden Suite ===")
    v2_traces = regional_client.evals.run_inference(
        src=golden_df,
        agent=target_agent,
        config={"allow_cross_region_model": True},
    )
    # In google-cloud-aiplatform==2.7.0, run_inference(agent=reasoning_engine_str) leaves
    # `agent_data.agents` unset on remote traces; inject AgentData.get_agents_map() so
    # TOOL_USE_QUALITY autoraters inspect full ADK tool declarations.
    if args.reasoning_engine and getattr(v2_traces, "eval_dataset_df", None) is not None:
        agents_map = types.evals.AgentData.get_agents_map(v2_local_agent)
        updated_agent_data: list[Any] = []
        for cell in v2_traces.eval_dataset_df.get("agent_data", []):
            if isinstance(cell, dict) and not cell.get("agents"):
                cell = {**cell, "agents": agents_map}
            updated_agent_data.append(cell)
        if updated_agent_data:
            v2_traces.eval_dataset_df["agent_data"] = updated_agent_data
    v2_traces.candidate_name = "finops_governance_v2_optimized"

    v2_eval_result = regional_client.evals.evaluate(
        dataset=v2_traces,
        metrics=single_turn_metrics,
        config={"allow_cross_region_model": True},
        agent_info=v2_agent_info,
    )
    v2_scores = _extract_summary_scores(v2_eval_result)
    print("v2_optimized summary scores:", json.dumps(v2_scores, indent=2))

    if experiment_name:
        try:
            v2_run = regional_client.evals.create_evaluation_run(
                display_name="v2-optimized-golden-regression",
                evaluation_experiment=experiment_name,
                dataset=v2_traces,
                dest=f"{gcs_dest}/v2_optimized_golden",
                metrics=single_turn_metrics[:4],
                labels={**base_agent_labels, "candidate": "v2-optimized", "suite": "golden"},
                config={"allow_cross_region_model": True},
            )
            if v2_run.name:
                managed_runs.append(v2_run.name.split("/")[-1])
                print(f"Logged v2_optimized EvaluationRun into Experiment: {v2_run.name}")
        except Exception as exc:
            print(f"Note: v2_optimized create_evaluation_run info ({exc}).")

    sim_scores: dict[str, float] = {}

    if not args.skip_simulation:
        print(f"\n=== Step 4A: Generating {args.sim_count} Synthetic Multi-Turn Scenarios via User Simulation ===")
        sim_scenarios = regional_client.evals.generate_conversation_scenarios(
            agent_info=v2_agent_info,
            config={
                "count": args.sim_count,
                "model_name": judge_model_uri,
                "generation_instruction": (
                    "Generate realistic enterprise scenarios where an engineering lead asks about "
                    "October 2026 project spend (PRJ-ALPHA-101, PRJ-BETA-204, or PRJ-GAMMA-309), "
                    "requests a GPU quota increase initially omitting a justification before providing "
                    "it on follow-up, or asks to open a governance remediation ticket."
                ),
                "environment_context": (
                    "Active billing month is 2026-10. Available projects are PRJ-ALPHA-101 (OVER_BUDGET), "
                    "PRJ-BETA-204 (WITHIN_BUDGET), and PRJ-GAMMA-309 (CRITICAL_OVERAGE)."
                ),
            },
            allow_cross_region_model=True,
        )

        print("=== Step 4B: Simulating Multi-Turn Sessions & Scoring Multi-Turn Autoraters ===")
        # Note: LlmBackedUserSimulator pins its internal genai.Client location to
        # `api_client.location`, so call `global_client.evals.run_inference(...)` when
        # using `gemini-3.8-flash` (served on `locations/global`), while Step 4C logs
        # the server-side `EvaluationRun` in the regional `EvaluationExperiment`.
        sim_traces = global_client.evals.run_inference(
            src=sim_scenarios,
            agent=v2_local_agent,
            config={
                "user_simulator_config": {
                    "max_turn": 4,
                    "model_name": args.judge_model,
                },
                "allow_cross_region_model": True,
            },
        )
        sim_traces = _sanitize_multiturn_trace_events(sim_traces)
        sim_traces.candidate_name = "finops_governance_v2_multiturn"
        multiturn_metrics: list[Any] = [
            types.RubricMetric.MULTI_TURN_TASK_SUCCESS(
                judge_model=judge_model_uri,
                judge_model_step_configs=step_autorater_cfgs,
            ),
            types.RubricMetric.MULTI_TURN_TOOL_USE_QUALITY(
                judge_model=judge_model_uri,
                judge_model_step_configs=step_autorater_cfgs,
            ),
            types.RubricMetric.MULTI_TURN_TRAJECTORY_QUALITY(
                judge_model=judge_model_uri,
                judge_model_step_configs=step_autorater_cfgs,
            ),
        ]
        sim_eval_result = regional_client.evals.evaluate(
            dataset=sim_traces,
            metrics=multiturn_metrics,
            config={"allow_cross_region_model": True},
            agent_info=v2_agent_info,
        )
        sim_scores = _extract_summary_scores(sim_eval_result)
        print("Multi-turn simulation summary scores:", json.dumps(sim_scores, indent=2))

        if experiment_name:
            print("\n=== Step 4C: Logging Multi-Turn Simulation Run into EvaluationExperiment ===")
            try:
                sim_run = regional_client.evals.create_evaluation_run(
                    display_name="v2-optimized-multiturn-simulation",
                    evaluation_experiment=experiment_name,
                    dataset=sim_traces,
                    dest=f"{gcs_dest}/v2_multiturn_simulation",
                    metrics=multiturn_metrics,
                    loss_analysis_metrics=[multiturn_metrics[0], multiturn_metrics[1]],
                    labels={**base_agent_labels, "candidate": "v2-multiturn", "suite": "user-simulation"},
                    config={"allow_cross_region_model": True},
                )
                if sim_run.name:
                    managed_runs.append(sim_run.name.split("/")[-1])
                    print(f"Logged Multi-Turn EvaluationRun into Experiment: {sim_run.name}")
            except Exception as exc:
                print(f"Note: multi-turn create_evaluation_run info ({exc}).")

    def _find_score(scores_dict: dict[str, float], substring: str) -> float:
        for k, val in scores_dict.items():
            if substring.lower() in k.lower():
                return val
        return 0.0

    resp_q = _find_score(v2_scores, "final_response_quality")
    tool_q = _find_score(v2_scores, "tool_use_quality")
    safety_q = _find_score(v2_scores, "safety")

    gate_checks = [
        {
            "metric": "final_response_quality",
            "actual": resp_q,
            "threshold": args.min_response_quality,
            "passed": resp_q >= args.min_response_quality,
        },
        {
            "metric": "tool_use_quality",
            "actual": tool_q,
            "threshold": args.min_tool_use_quality,
            "passed": tool_q >= args.min_tool_use_quality,
        },
        {
            "metric": "safety",
            "actual": safety_q,
            "threshold": args.min_safety,
            "passed": safety_q >= args.min_safety,
        },
    ]
    gate_passed = all(c["passed"] for c in gate_checks)

    report: dict[str, Any] = {
        "region": args.region,
        "target_mode": target_mode,
        "judge_model": args.judge_model,
        "custom_metrics_registered": list(custom_metrics_map.keys()),
        "golden_v1_baseline_scores": v1_scores,
        "golden_v2_optimized_scores": v2_scores,
        "v1_loss_clusters": v1_loss_clusters,
        "simulation_multiturn_scores": sim_scores,
        "evaluation_experiment": experiment_name.split("/")[-1] if experiment_name else "",
        "managed_evaluation_runs": managed_runs,
        "gate_checks": gate_checks,
        "gate_passed": gate_passed,
    }

    cleanup_orphaned_code_exec_sandboxes(args.project_id, args.region)

    json_path = out_dir / "scorecard.json"
    md_path = out_dir / "scorecard.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_text = build_markdown_scorecard(report)
    md_path.write_text(md_text, encoding="utf-8")

    print(f"\nSaved Scorecard JSON -> {json_path}")
    print(f"Saved Scorecard Markdown -> {md_path}\n")
    print(md_text)

    if not gate_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
