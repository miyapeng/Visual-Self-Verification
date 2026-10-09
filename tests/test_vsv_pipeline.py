import json
from pathlib import Path

import pytest

from multimodalcode.vsv_eval.checks import test_summary as summarize_test
from multimodalcode.vsv_eval.pipeline import evaluate_pipeline, sha256, validate_source, run_missing_stage, effective_test_rate, make_scorecard

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/vision2web/vsv_smartrecruiters_pipeline.json"


def real_config():
    config = json.loads(CONFIG.read_text())
    required = [ROOT / s['scores'] for s in config['stages'].values()]
    required += [ROOT / config['run_json'], ROOT / config['agent_task_root'] / 'prompt.txt',
                 ROOT / config['scorer_task_root'] / 'prompt.txt', ROOT / config['scorer_task_root'] / 'workflow.json']
    if not all(path.is_file() for path in required):
        pytest.skip("Local completed evaluation artifacts unavailable")
    return config


def test_three_completed_stages_integrate_without_network_or_replay(tmp_path, monkeypatch):
    real_config()
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: pytest.fail("Cache integration must not execute stages"))
    output = tmp_path / "joined"
    result = evaluate_pipeline(CONFIG, output, ROOT)
    assert result["execution_status"] == "complete"
    assert result["research_status"] == "development_pilot_uncalibrated"
    assert result["single_composite_score"] is None and result["task_count"] == 1
    stages = result["stages"]
    assert stages["test"]["summary"]["workflow_coverage"]["numerator"] == 3
    assert stages["test"]["summary"]["workflow_coverage"]["denominator"] == 13
    assert stages["visual_judgment"]["summary"]["assessment_counts"] == {"correct": 8, "partially_correct": 3}
    assert stages["safe_repair"]["summary"]["outcome_counts"] == {"fixed": 3, "not_fixed": 2}
    assert stages["safe_repair"]["summary"]["transition_count"] == 3
    card = result["scorecard"]
    assert card["T"]["numerator"] == 10 and card["T"]["denominator"] == 12
    assert card["T"]["score"] == pytest.approx(100 * 10 / 12)
    assert card["C"]["score"] == pytest.approx(100 * 3 / 13)
    assert card["J"]["score"] == pytest.approx(100 * 8 / 11)
    assert card["R"]["score"] == 60
    assert card["regression"]["score"] == 0
    assert "Metrics (0–100)" in (output / "index.html").read_text()
    assert len(result["episodes"][0]["test"]) == 6  # no retrospective split into artificial scenarios
    assert (output / "manifest.json").is_file() and (output / "index.html").is_file()
    before = sha256(output / "scores.json")
    with pytest.raises(FileExistsError):
        evaluate_pipeline(CONFIG, output, ROOT)
    assert sha256(output / "scores.json") == before


def test_missing_stage_is_not_zero_or_success(tmp_path):
    config = real_config()
    config["stages"].pop("safe_repair")
    saved = tmp_path / "config.json"
    saved.write_text(json.dumps(config))
    result = evaluate_pipeline(saved, tmp_path / "joined", ROOT)
    assert result["execution_status"] == "partial"
    assert result["stages"]["safe_repair"]["status"] == "missing"
    assert "summary" not in result["stages"]["safe_repair"]
    assert result["scorecard"]["R"]["score"] is None
    assert result["scorecard"]["R"]["denominator"] is None


def test_different_task_source_and_partial_episode_set_are_rejected(tmp_path):
    source = tmp_path / "run.json"
    source.write_text("{}"); checksum = sha256(source)
    raw = {"case_id": "a", "episodes": [{"episode_id": "a"}, {"episode_id": "b"}]}
    value = {"source_run": str(source), "case_id": "wrong"}
    with pytest.raises(ValueError, match="different task"):
        validate_source(value, raw, checksum)
    value["case_id"] = "a"
    value["episodes"] = [{"episode_id": "a"}]
    with pytest.raises(ValueError, match="subset"):
        validate_source(value, raw, checksum)
    value["episodes"] = raw["episodes"]
    value["source_run_sha256"] = "changed"
    with pytest.raises(ValueError, match="recorded input hash"):
        validate_source(value, raw, checksum)


def test_no_checks_is_distinct_from_judge_unavailable():
    result = summarize_test([], 13)
    assert result["workflow_coverage"]["numerator"] == 0
    assert result["workflow_coverage"]["rate"] == 0
    assert result["reasonable_rate"] is None  # no conditional accuracy denominator


def test_effective_test_is_joint_not_product_of_marginal_rates():
    def group(execution, relevance):
        return {"execution": {"status": execution}, "reasonableness": {"status": relevance}}
    groups = [group("pass", "reasonable"), group("fail", "reasonable"),
              group("partial", "reasonable"), group("pass", "unreasonable"),
              group("pass", "not_evaluable"), group("not_evaluable", "reasonable")]
    result = effective_test_rate(groups)
    assert result == {"numerator": 1, "denominator": 4, "rate": .25}
    assert effective_test_rate([])["rate"] is None


def test_scorecard_unknown_coverage_and_missing_judges_are_not_zero():
    t = summarize_test([], 13)
    t["effective_test"] = effective_test_rate([])
    empty = make_scorecard({"test": {"status": "complete", "summary": t}})
    assert empty["T"]["score"] is None and empty["C"]["score"] == 0
    assert empty["J"]["score"] is None and empty["R"]["score"] is None
    t["action_group_count"] = 2
    t["workflow_coverage"].update(numerator=1, rate=1/13, is_lower_bound=True,
                                  items=[{"status": "full"}, {"status": "unknown"}])
    card = make_scorecard({"test": {"status": "partial", "summary": t}})
    assert card["C"]["score"] == pytest.approx(100/13)
    assert card["C"]["is_lower_bound"] and card["C"]["unknown_count"] == 1
    assert card["T"]["excluded_count"] == 2


def test_scorecard_preserves_exclusions_and_nonrepair_regressions():
    stages = {
        "visual_judgment": {"status": "partial", "summary": {
            "judgment_count": 5, "unavailable_count": 1,
            "strict_correct_given_decidable": {"numerator": 2, "denominator": 4}}},
        "safe_repair": {"status": "partial", "summary": {
            "target_count": 5, "transition_count": 3,
            "fixed_without_observed_regression": {"numerator": 1, "denominator": 2},
            "regression_status_counts": {"regression": 1, "no_observed_regression": 1, "insufficient_evidence": 1},
            "outcome_counts": {"regression": 1}}},
    }
    card = make_scorecard(stages)
    assert card["J"]["score"] == 50 and card["J"]["excluded_count"] == 2
    assert card["R"]["score"] == 50 and card["R"]["excluded_count"] == 3
    assert card["regression"]["score"] == 50 and card["regression"]["excluded_count"] == 1
    assert card["regression"]["target_regression_count"] == 1


def test_stage_delegation_keeps_workflow_out_of_visual_stage(tmp_path, monkeypatch):
    config = json.loads(CONFIG.read_text())
    commands = []
    monkeypatch.setattr("subprocess.run", lambda command, **kw: commands.append((command, kw)))
    run_missing_stage("visual_judgment", config["stages"]["visual_judgment"], config, ROOT, tmp_path)
    command, options = commands[0]
    assert "--visual-only" in command
    assert command[command.index("--task-root") + 1].endswith("agent_visible")
    assert "--primary-profile" in command and options["timeout"] == 7200
    with pytest.raises(ValueError, match="audited"):
        run_missing_stage("safe_repair", {}, config, ROOT, tmp_path)


def test_configured_repair_delegation_does_not_require_a_specific_case(tmp_path, monkeypatch):
    config = json.loads((ROOT / "configs/vision2web/vsv_smartrecruiters_pipeline_v2.json").read_text())
    settings = {**config["stages"]["safe_repair"], "fixture": "data/another-reviewed-fixture"}
    commands = []
    monkeypatch.setattr("subprocess.run", lambda command, **kw: commands.append(command))
    run_missing_stage("safe_repair", settings, config, ROOT, tmp_path)
    assert commands[0][commands[0].index("--fixture") + 1].endswith("another-reviewed-fixture")
    assert commands[0][commands[0].index("--spec") + 1].endswith("smartrecruiters_repair_v2.json")


def test_pipeline_keeps_joint_coverage_instead_of_legacy_empty_matches(tmp_path):
    from multimodalcode.vsv_eval.workflow import load_workflow
    config = real_config()
    workflow = load_workflow(ROOT / config['scorer_task_root'] / 'workflow.json')
    value = json.loads((ROOT / config["stages"]["test"]["scores"]).read_text())
    value["schema"] = "multimodalcode-vsv-score-3"
    value["source_run_sha256"] = sha256(ROOT / config["run_json"])
    for episode in value["episodes"]:
        for group in episode["test"]["groups"]:
            group["reasonableness"]["matches"] = []
        group = next(g for g in episode['test']['groups'] if g['execution']['status'] == 'pass')
        episode["test"]["joint_assessment"] = {"assessment_complete": True, "items": [
            {"workflow_id": item['workflow_id'], "status": "full" if i == 0 else "uncovered",
             "evidence_chains": [[group['action_ordinal'], group['execution']['evidence_ordinals'][0]]] if i == 0 else [],
             "episode_id": episode["episode_id"], "reason": "Synthetic aggregation fixture, not a model label"}
            for i, item in enumerate(workflow)
        ]}
    saved = tmp_path / "test.json"
    saved.write_text(json.dumps(value))
    config["stages"]["test"]["scores"] = str(saved)
    saved_config = tmp_path / "config.json"
    saved_config.write_text(json.dumps(config))
    result = evaluate_pipeline(saved_config, tmp_path / "joined", ROOT)
    assert result["stages"]["test"]["summary"]["coverage_protocol"] == "joint-episode-2"
    assert result["stages"]["test"]["summary"]["workflow_coverage"]["numerator"] == 1


def test_configured_real_browser_smoke_joins_without_inventing_visual_scores(tmp_path):
    from multimodalcode.vsv_eval.pipeline import collect_repair
    path = ROOT / 'runs/vsv_eval/protocol-v2-smoke-0907/repair/scores.json'
    if not path.is_file():
        pytest.skip('Local browser smoke artifact unavailable')
    value = json.loads(path.read_text())
    if not Path(value['source_run']).is_file():
        pytest.skip('Archived browser smoke source path unavailable on this machine')
    summary, complete = collect_repair(value, sha256(value['source_run']), Path)
    assert summary['protocol'] == 'configured-replay-2'
    assert summary['code_version_count'] == 4 and summary['transition_count'] == 3
    assert summary['regression_status_counts'] == {'no_observed_regression': 3}
    assert summary['outcome_counts'] == {'insufficient_evidence': 5}
    assert not complete
    (tmp_path / 'repair-summary.json').write_text(json.dumps(summary, indent=2))
