"""Join the three existing offline stages; never run or steer the coding policy."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys

from multimodalcode.io import read_json, write_json
from .checks import test_summary
from .episodes import extract_episodes
from .repair_pilot import assess_output, digest_tree, functional_pass, regression_result, repair_outcome
from .visual_judgment import summarize as visual_summary
from .workflow import load_workflow

STAGES = ("test", "visual_judgment", "safe_repair")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rate(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def effective_test_rate(groups):
    """Strict joint success; keep execution/relevance labels unchanged."""
    assessed = [g for g in groups
                if g["execution"]["status"] in {"pass", "partial", "fail"}
                and g["reasonableness"]["status"] in {"reasonable", "partial", "unreasonable"}]
    return rate(sum(g["execution"]["status"] == "pass"
                    and g["reasonableness"]["status"] == "reasonable" for g in assessed), len(assessed))


def make_scorecard(stages):
    """A percentage view of one task's evidence, not another judging stage."""
    def record(stage, metric, total=None):
        numerator, denominator = metric.get("numerator"), metric.get("denominator")
        return {"numerator": numerator, "denominator": denominator,
                "score": 100 * numerator / denominator if numerator is not None and denominator else None,
                "excluded_count": total - denominator if total is not None and denominator is not None else None,
                "stage_status": stage.get("status", "missing")}

    test, visual, repair = [stages.get(name, {}) for name in STAGES]
    t, j, r = [stage.get("summary", {}) for stage in (test, visual, repair)]
    coverage = t.get("workflow_coverage", {})
    c = record(test, coverage)
    # Unknown workflow items remain in the fixed coverage denominator.
    c.pop("excluded_count")
    c.update({"unknown_count": sum(i["status"] == "unknown" for i in coverage["items"]) if "items" in coverage else None,
              "is_lower_bound": coverage.get("is_lower_bound")})
    transitions = r.get("regression_status_counts", {})
    regression = rate(transitions.get("regression", 0),
                      transitions.get("regression", 0) + transitions.get("no_observed_regression", 0)) if r else {}
    regression_score = record(repair, regression, r.get("transition_count"))
    regression_score["target_regression_count"] = r.get("outcome_counts", {}).get("regression", 0) if r else None
    return {"schema": "multimodalcode-vsv-scorecard-1", "scale": "0-100",
            "T": record(test, t.get("effective_test", {}), t.get("action_group_count")),
            "C": c,
            "J": record(visual, j.get("strict_correct_given_decidable", {}),
                        j["judgment_count"] + j.get("unavailable_count", 0) if "judgment_count" in j else None),
            "R": record(repair, r.get("fixed_without_observed_regression", {}), r.get("target_count")),
            # Include every assessed transition, even if its original target was already correct.
            "regression": regression_score}


def run_missing_stage(stage, settings, config, root, output):
    """Delegate to existing CLIs, with explicit model profiles and bounded execution."""
    resolve = lambda p: (root / p).resolve()
    common = ["--output-dir", str(output), "--config", str(resolve(config["judge_config"]))]
    if stage == "safe_repair":
        if settings.get("adapter") != "configured_replay":
            raise ValueError("Safe Repair needs an audited reconstruction/replay adapter; none is configured")
        command = [sys.executable, str(root / "scripts/vision2web/score_repair_pilot.py"),
                   "--fixture", str(resolve(settings["fixture"])), "--spec", str(resolve(settings["spec"])),
                   "--judge-profile", settings["profile"], *common]
    else:
        command = [sys.executable, str(root / "scripts/vision2web/score_vsv.py"),
                   "--run-json", str(resolve(config["run_json"])),
                   "--task-root", str(resolve(config["scorer_task_root" if stage == "test" else "agent_task_root"])),
                   "--primary-profile", settings["profile"],
                   "--test-only" if stage == "test" else "--visual-only", *common]
    print(f"Running offline stage: {stage}", flush=True)
    subprocess.run(command, check=True, timeout=int(settings.get("timeout_seconds", 7200)))


def validate_source(value, extracted, source_hash):
    if value["case_id"] != extracted["case_id"] or sha256(value["source_run"]) != source_hash:
        raise ValueError("Stage belongs to a different task or trajectory")
    if value.get("source_run_sha256", source_hash) != source_hash:
        raise ValueError("Source trajectory differs from the stage's recorded input hash")
    expected = {e["episode_id"] for e in extracted["episodes"]}
    if "episodes" in value:
        actual = [e["episode_id"] for e in value["episodes"]]
        if set(actual) != expected or len(actual) != len(expected):
            raise ValueError("Stage episode set differs; do not present a subset as the whole trajectory")


def collect_visual(value, timeline, remember, task_text):
    rows = []
    for episode in value["episodes"]:
        for row in episode["visual_judgment"]["judgments"]:
            ordinal = row["judgment_ordinal"]
            if timeline[ordinal].get("text") != row["policy_quote"]:
                raise ValueError(f"Visual judgment quote differs from original event {ordinal}")
            packet = read_json(remember(row["input_path"]))["packet"]
            if packet["policy_quote"] != row["policy_quote"] or packet["judgment_ordinal"] != ordinal:
                raise ValueError("Visual packet and scored judgment disagree")
            if packet["task"] != task_text:
                raise ValueError("Visual packet does not contain the original task")
            if any(r["ordinal"] >= ordinal for r in packet["events_before_judgment"]):
                raise ValueError("Future evidence in Visual Judgment packet")
            for image in packet["image_order"]:
                if image.get("ordinal", -1) >= ordinal:
                    raise ValueError("Future image in Visual Judgment packet")
                remember(image["path"])
            rows.append(row)
    summary = visual_summary(rows)
    counts = summary["assessment_counts"]
    decidable = sum(counts.get(k, 0) for k in ("correct", "partially_correct", "incorrect"))
    summary.update({"unit": "expressed image-conditioned judgment", "human_calibrated": False,
                    "strict_correct_given_decidable": rate(counts.get("correct", 0), decidable),
                    "decidable_given_present": rate(decidable, summary["judgment_count"]),
                    "episode_status_counts": dict(Counter(e["visual_judgment"]["status"] for e in value["episodes"]))})
    complete = (value.get("selected_ordinals") is None and not summary["unavailable_count"]
                and all(e["visual_judgment"]["status"] != "not_evaluable" for e in value["episodes"]))
    return summary, complete


def collect_repair(value, source_hash, remember):
    """Recompute pilot outcomes from stored execution evidence, never a patch opinion."""
    reconstruction = read_json(remember(value["reconstruction"]))
    if reconstruction["source_run_sha256"] != source_hash:
        raise ValueError("Repair reconstruction belongs to a different trajectory")
    versions = {v["version"]: v for v in reconstruction["versions"]}
    configured = value.get("schema") == "multimodalcode-repair-score-2"
    records = read_json(remember(value["repair_records"])) if configured else []
    code_subdir = value.get("runtime", {}).get("code_subdir", "app")
    replays, reset_specs = {}, []
    for name, version in versions.items():
        if digest_tree(Path(version["workspace"]) / code_subdir) != version["code_manifest"]:
            raise ValueError(f"Reconstructed source files changed: {name}")
        replay = read_json(remember(value["replays"][name]))
        if replay["code_sha256"] != version["code_manifest"]["sha256"]:
            raise ValueError(f"Replay is bound to a different code version: {name}")
        spec = read_json(remember(Path(value["replays"][name]).with_name("spec.json")))
        if spec["code_sha256"] != replay["code_sha256"]:
            raise ValueError("Replay specification and code binding disagree")
        reset_specs.append({k: v for k, v in spec.items() if k != "code_sha256"})
        for check in replay["checks"]:
            computed = None
            if check["status"] == "executed":
                try:
                    data = json.loads(check["output"]) if isinstance(check["output"], str) else check["output"]
                    if configured:
                        definition = next(c for c in spec["checks"] if c["name"] == check["name"])
                        computed = assess_output(definition.get("assertions"), data)
                    else:
                        computed = functional_pass(check["name"], data)
                except (ValueError, TypeError, AttributeError):
                    pass
            if computed is not check.get("passed"):
                raise ValueError("Stored functional result differs from its execution output")
        replays[name] = replay
    if any(s != reset_specs[0] for s in reset_specs[1:]):
        raise ValueError("Repair versions did not use the same reset and action specification")
    conditions = [(r.get("browser_version"), r.get("viewport"), r.get("launch")) for r in replays.values()]
    if len(set(json.dumps(c, sort_keys=True) for c in conditions)) > 1:
        raise ValueError("Repair browser/runtime conditions differ")
    transitions = []
    for row in value["regressions"]:
        checks = [{c["name"]: c.get("passed") for c in replays[v]["checks"]
                   if not configured or c["name"] in row["check_ids"]} for v in (row["before"], row["after"])]
        result = regression_result(*checks)
        if any(result[k] != row[k] for k in ("status", "baseline_passed", "failed_after", "missing_after")):
            raise ValueError("Regression summary disagrees with actual passing baseline")
        transitions.append({"before": row["before"], "after": row["after"], **result})
    regression_by_pair = {(r["before"], r["after"]): r["status"] for r in transitions}
    targets = []
    for row in value["targets"]:
        packet = read_json(remember(Path(value["reconstruction"]).parent.parent / "inputs" / (row["id"] + ".json")))
        for image in packet["images"]:
            remember(image)
        if packet["packet"]["target"] != {k: row[k] for k in packet["packet"]["target"]}:
            raise ValueError("Repair criterion differs from the judged input")
        for which in ("before", "after"):
            if packet["packet"][which + "_code"] != versions[row[which]]["code_manifest"]["sha256"]:
                raise ValueError("Repair judgment is bound to different source files")
        parsed = ((row.get("judge") or {}).get("parsed") or {})
        if configured:
            record = next(r for r in records if r["id"] == row["id"])
            if any(record[k] != row[k] for k in ("before", "after", "expectation", "policy_event", "source_action_ordinals", "source_observation_ordinals")):
                raise ValueError("Repair record and target disagree")
            for which in ("before", "after"):
                if record[which + "_code"] != packet["packet"][which + "_code"]:
                    raise ValueError("Repair record refers to different source files")
            if row.get("assessment_source") == "assertions":
                checked = [next(c for c in replays[row[v]]["checks"] if c["name"] == row["check"]) for v in ("before", "after")]
                parsed = {"before_satisfied": checked[0].get("passed"), "after_satisfied": checked[1].get("passed")}
                if any(row["satisfaction"].get(k) is not v for k, v in parsed.items()):
                    raise ValueError("Repair satisfaction differs from configured execution assertions")
        outcome = repair_outcome(parsed.get("before_satisfied"), parsed.get("after_satisfied")) if row.get("valid_judge_output") else "insufficient_evidence"
        if outcome != row["outcome"]:
            raise ValueError("Repair outcome differs from independent before/after satisfaction labels")
        targets.append({"id": row["id"], "policy_event": row["policy_event"],
                        "before": row["before"], "after": row["after"], "outcome": outcome,
                        "regression_status": regression_by_pair.get((row["before"], row["after"]), "insufficient_evidence")})
    counts = dict(Counter(r["outcome"] for r in targets))
    reproduced = [r for r in targets if r["outcome"] in {"fixed", "not_fixed"}]
    safety_assessed = [r for r in reproduced if r["regression_status"] != "insufficient_evidence"]
    summary = {"unit": "audited target repair attempt",
               "protocol": "configured-replay-2" if configured else "legacy-case-pilot-1",
               "scope": "configured targets and regression checks" if configured else "case-specific development pilot",
               "target_count": len(targets), "code_version_count": len(versions), "transition_count": len(transitions),
               "outcome_counts": counts, "human_calibrated": False,
               "target_fixed_given_reproduced_failure": rate(counts.get("fixed", 0), len(reproduced)),
               "fixed_without_observed_regression": rate(sum(r["outcome"] == "fixed" and r["regression_status"] == "no_observed_regression" for r in safety_assessed), len(safety_assessed)),
               "regression_status_counts": dict(Counter(r["status"] for r in transitions)),
               "regressions": transitions, "targets": targets,
               "limitations": value.get("limitations", [])}
    complete = (bool(targets) and not counts.get("insufficient_evidence", 0)
                and all(r["status"] != "insufficient_evidence" for r in transitions))
    return summary, complete


def evaluate_pipeline(config_path, output_dir, root, *, run_missing=False):
    root, output = Path(root).resolve(), Path(output_dir).resolve()
    config = read_json(config_path)
    resolve = lambda p: (root / p).resolve()
    # Never overwrite historical reports, even when changing a scoring configuration.
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Choose a new empty output directory; historical results are read-only")
    source = resolve(config["run_json"])
    source_hash = sha256(source)
    task = resolve(config["agent_task_root"]) / "prompt.txt"
    scorer_task = resolve(config["scorer_task_root"])
    if sha256(task) != sha256(scorer_task / "prompt.txt"):
        raise ValueError("Test and Visual Judgment must use the same original task")
    extracted = extract_episodes(source)
    timeline = {r["ordinal"]: r for r in read_json(source)["timeline"]}
    workflow = load_workflow(scorer_task / "workflow.json")  # Offline evaluator only.
    files = {}

    def remember(path):
        path = Path(path).resolve()
        files[str(path)] = sha256(path)
        return path

    for path in (config_path, source, task, scorer_task / "workflow.json"):
        remember(path)
    for path in sorted((root / "src/multimodalcode/vsv_eval").glob("*.py")):
        remember(path)
    for name in ("score_vsv.py", "score_repair_pilot.py", "replay_recorded_browser.cjs", "evaluate_trajectory.py"):
        remember(root / "scripts/vision2web" / name)
    remember(resolve(config["judge_config"]))
    for path in config.get("audit_artifacts", []):
        remember(resolve(path))
    summaries, joins, warnings = {}, {}, []
    for stage in STAGES:
        settings = config.get("stages", {}).get(stage, {})
        path = resolve(settings["scores"]) if settings.get("scores") else output / stage / "scores.json"
        if not path.is_file() and run_missing and settings:
            # Never write to a configured historical artifact location.
            if settings.get("scores"):
                raise FileNotFoundError(f"Configured historical artifact missing: {path}")
            run_missing_stage(stage, settings, config, root, path.parent)
        if not path.is_file():
            summaries[stage] = {"status": "missing", "reason": "No completed stage artifact; no score inferred"}
            continue
        value = read_json(remember(path))
        validate_source(value, extracted, source_hash)
        for key, expected in (("task_prompt_sha256", sha256(task)), ("workflow_sha256", sha256(scorer_task / "workflow.json"))):
            if key in value and value[key] != expected:
                raise ValueError(f"Stage input changed: {key}")
        if not value.get("source_run_sha256"):
            warnings.append(f"{stage}: legacy artifact has no score-time source hash; current inputs checked, not a retrospective provenance guarantee")
        if stage == "test":
            groups = [g for e in value["episodes"] for g in e["test"]["groups"]]
            raw_by_id = {e["episode_id"]: e for e in extracted["episodes"]}
            for episode in value["episodes"]:
                if episode["events"] != raw_by_id[episode["episode_id"]]["events"]:
                    raise ValueError("Saved Test events differ from current original extraction")
            joint = [e["test"]["joint_assessment"] for e in value["episodes"]] if value.get("schema") == "multimodalcode-vsv-score-3" else None
            summary = test_summary(groups, len(workflow), workflow, joint)
            summary["unit"] = "original call groups for execution/relevance; connected episode evidence for unique workflow item coverage" if joint is not None else "original browser tool-call group; workflow coverage uses unique official item IDs"
            summary["reasonableness"] = rate(summary["reasonableness_counts"].get("reasonable", 0), summary["assessed_groups"])
            summary["effective_test"] = effective_test_rate(groups)
            complete = (summary["assessed_groups"] == len(groups)
                        and summary["workflow_coverage"]["assessment_complete"])
            joins[stage] = {e["episode_id"]: [g["group_id"] for g in e["test"]["groups"]] for e in value["episodes"]}
        elif stage == "visual_judgment":
            summary, complete = collect_visual(value, timeline, remember, task.read_text())
            joins[stage] = {e["episode_id"]: [j["judgment_ordinal"] for j in e["visual_judgment"]["judgments"]] for e in value["episodes"]}
        else:
            adapter = settings.get("adapter")
            if adapter == "smartrecruiters_edit_pilot":
                if value["case_id"] != "frontend/smartrecruiters":
                    raise ValueError("Legacy repair artifacts are limited to their audited case")
            elif adapter != "configured_replay" or value.get("schema") != "multimodalcode-repair-score-2":
                raise ValueError("Configure replay spec v2 with verified versions and task-specific checks")
            frozen_spec = remember(path.parent / "pilot_spec.json")
            if settings.get("spec") and read_json(frozen_spec) != read_json(remember(resolve(settings["spec"]))):
                raise ValueError("Repair specification changed")
            summary, complete = collect_repair(value, source_hash, remember)
            joins[stage] = {e["episode_id"]: [r["id"] for r in summary["targets"] if e["start_ordinal"] <= r["policy_event"] <= e["end_ordinal"]] for e in extracted["episodes"]}
        summaries[stage] = {"status": "complete" if complete else "partial", "artifact": str(path),
                            "artifact_sha256": files[str(path)], "source_schema": value.get("schema", "legacy"), "summary": summary}
    result = {"schema": "multimodalcode-vsv-pipeline-1", "case_id": extracted["case_id"],
              "model": extracted["model"], "framework": extracted["framework"], "mode": extracted["mode"],
              "source_run": str(source), "source_run_sha256": source_hash,
              "execution_status": "complete" if all(s["status"] == "complete" for s in summaries.values()) else "partial",
              "research_status": "development_pilot_uncalibrated", "task_count": 1,
              "episode_count": len(extracted["episodes"]), "stages": summaries,
              "scorecard": make_scorecard(summaries),
              "episodes": [{"episode_id": e["episode_id"], "start_ordinal": e["start_ordinal"], "end_ordinal": e["end_ordinal"],
                            **{stage: joins.get(stage, {}).get(e["episode_id"], []) for stage in STAGES}} for e in extracted["episodes"]],
              "single_composite_score": None,
              "limitations": warnings + config.get("audit_notes", []) + ["Pipeline completion is not evidence of agent success or evaluator validity.",
                  "Units differ across stages and are correlated within a task; no pooled overall accuracy.",
                  "No claim of visual causality, complete episode recall, or exhaustive regression coverage.",
                  "Hashes taken on integration freeze current evidence, not the original runtime/environment."]}
    write_json(output / "scores.json", result)
    write_json(output / "manifest.json", {"recorded_at": datetime.now(timezone.utc).isoformat(),
               "hash_scope": "current imported scores, evidence inputs and integration code; not original score-time environment",
               "files_sha256": files})
    write_summary_html(result, output / "index.html")
    return result


def write_summary_html(result, destination):
    esc = html.escape
    rows = []
    for name, stage in result["stages"].items():
        link = ""
        if stage.get("artifact"):
            link = f'<a href="{esc(os.path.relpath(stage["artifact"], destination.parent))}">原始逐项结果</a>'
        rows.append(f'<h2>{esc(name)} · {esc(stage["status"])}</h2>{link}<pre>{esc(json.dumps(stage.get("summary", stage), ensure_ascii=False, indent=2))}</pre>')
    text = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>VSV 三阶段统一结果</title><style>body{font:16px/1.6 system-ui;max-width:1000px;margin:32px auto;padding:0 20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f7;padding:16px}</style>'
    text += f'<h1>{esc(result["case_id"])}</h1><p>{esc(result["model"])} / {esc(result["framework"])} / {esc(result["mode"])}</p>'
    text += '<p>这是离线研究诊断，不是 Vision2Web 官方得分。complete 仅指三个计算阶段完成；Judge 未经人工校准。三个不同分母的指标不合成总分。</p>'
    if result.get("scorecard"):
        labels = {"T": "有效且合理的检查 ↑", "C": "workflow 覆盖 ↑", "J": "严格视觉判断正确率 ↑",
                  "R": "安全修复率 ↑", "regression": "已测版本转换回归率 ↓"}
        text += '<h2>百分制分项记录</h2><table><tr><th>指标</th><th>分数 / 100</th><th>分子 / 分母</th><th>阶段状态</th></tr>'
        for key, label in labels.items():
            entry = result["scorecard"][key]
            score = '—' if entry['score'] is None else f'{entry["score"]:.2f}'
            fraction = f'{entry["numerator"]} / {entry["denominator"]}' if entry['numerator'] is not None and entry['denominator'] is not None else '—'
            if key == 'C' and entry.get('is_lower_bound') and entry['score'] is not None:
                score = '≥ ' + score
            text += f'<tr><td>{esc(key + " · " + label)}</td><td>{score}</td><td>{fraction}</td><td>{esc(entry["stage_status"])}</td></tr>'
        text += '</table><p>“—”表示未获得数值，不是 0 分。partial 表示阶段未完整评估；排除数量及原因保留在 JSON 和下方明细。这里仅换算原始标签，未重新调用 Judge，也未把旧协议分数标作新协议。</p>'
    text += ''.join(rows) + '<h2>限制</h2><ul>' + ''.join(f'<li>{esc(s)}</li>' for s in result['limitations']) + '</ul></html>'
    destination.write_text(text, encoding="utf-8")
