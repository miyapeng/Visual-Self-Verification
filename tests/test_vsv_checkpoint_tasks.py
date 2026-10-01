import json
from pathlib import Path

import pytest

from multimodalcode.agent_harness.cases import AgentCase
from multimodalcode.agent_harness.vision2web_trace import manifest_hash, program_manifest
from multimodalcode.vsv_eval.checkpoint_tasks import (
    build_tasks, stage_task, verify_package, validate_runtime, prepare_agent_case, sha256,
    _screenshot_outputs, run_verification_handoff,
)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _fixture(tmp_path, *, start=True, objects=True):
    public = tmp_path / "public"
    (public / "prototypes").mkdir(parents=True)
    (public / "prototypes/home.jpg").write_bytes(b"original image")
    (public / "prompt.txt").write_text("Build a page with a working Buy button.")
    (public / "workflow.json").write_text("DO NOT COPY THIS ORACLE")
    case = AgentCase("vision2web", "frontend/example", "frontend", "Original benchmark prompt.",
                     source_dir=public)
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.js").write_text("button.onclick = () => {}; // unfinished behavior")
    if start:
        (source / "start.sh").write_text("#!/bin/bash\nnode app.js\n")
    manifest = program_manifest(source)
    digest = manifest_hash(manifest)
    trace = tmp_path / "trace"
    _write(trace / "manifests/state.json", manifest)
    if objects:
        for name, row in manifest.items():
            obj = trace / "program_objects" / row["sha256"][:2] / row["sha256"]
            obj.parent.mkdir(parents=True, exist_ok=True)
            obj.write_bytes((source / name).read_bytes())
    binding = {"schema": "multimodalcode-observation-binding-1", "program_sha256": digest,
               "file_manifest_path": str(trace / "manifests/state.json"),
               "program_objects": {"program_object_root": str(trace / "program_objects"), "reconstructable": True}}
    (trace / "browser.events.jsonl").write_text(json.dumps({"binding": binding}) + "\n")
    image = tmp_path / "observed.png"
    image.write_bytes(b"recorded screenshot")
    events = []
    for n in (2, 4):
        events.extend([
            {"ordinal": n, "timestamp": f"2026-09-08T00:00:0{n}Z", "kind": "action",
             "category": "browser", "tool": "browser_get_state", "tool_call_id": f"call{n}",
             "browser_url_context": "http://localhost:3000", "text": "get_state"},
            {"ordinal": n+1, "timestamp": f"2026-09-08T00:00:0{n+1}Z", "kind": "observation",
             "category": "browser", "is_error": False, "payload": {"tool_use_id": f"call{n}"},
             "images": [{"path": str(image)}], "text": "Page is visible"},
        ])
    run = {"case_id": case.case_id, "model": "source-model", "framework": "openhands",
           "mode": "browser_enabled", "timeline": events,
           "development": {"root": str(trace), "events": [
               {"timestamp": "2026-09-08T00:00:01Z", "type": "workspace_change",
                "payload": {"program_sha256": digest, "created": ["app.js", "start.sh"]}}
           ]}}
    path = tmp_path / "run.json"
    _write(path, run)
    return path, case, manifest


def test_construct_deduplicates_versions_preserves_prompt_and_no_oracle(tmp_path):
    run, case, manifest = _fixture(tmp_path)
    result = build_tasks(run, tmp_path / "dataset", case=case)
    assert result["candidate_count"] == 2
    assert result["sample_count"] == 1
    assert not result["excluded"]
    row = result["samples"][0]
    assert [o["action_ordinal"] for o in row["origins"]] == [2, 4]
    sample = tmp_path / "dataset" / row["path"]
    task = verify_package(sample)
    assert task["fresh_session"] and task["repair_allowed"]
    assert not (sample / "runtime-check.json").exists()  # construction is NOT runnable admission
    assert "Candidates only" in result["admission"]
    assert (sample / "prompt.txt").read_text().startswith(case.prompt + "\n\n")
    assert not (sample / "workspace/workflow.json").exists()
    assert (sample / "workspace/prototypes/home.jpg").read_bytes() == b"original image"
    for name, row in manifest.items():
        assert (sample / "workspace" / name).read_bytes() == (tmp_path / "source" / name).read_bytes()
    assert (case.source_dir / "workflow.json").read_text() == "DO NOT COPY THIS ORACLE"
    stage_task(sample, tmp_path / "fresh")
    assert not (tmp_path / "fresh/provenance.json").exists()
    assert not (tmp_path / "fresh/task.json").exists()


@pytest.mark.parametrize("start,objects,reason", [
    (False, True, "no_start_sh_at_check"),
    (True, False, "missing_or_corrupt_source"),
])
def test_missing_start_or_objects_not_constructed(tmp_path, start, objects, reason):
    run, case, _ = _fixture(tmp_path, start=start, objects=objects)
    result = build_tasks(run, tmp_path / "dataset", case=case)
    assert result["sample_count"] == 0
    assert all(reason in r["reason"] for r in result["excluded"])


def test_never_substitutes_final_for_missing_pre_check_state(tmp_path):
    run, case, _ = _fixture(tmp_path)
    value = json.loads(run.read_text())
    value["development"]["events"][0]["payload"]["program_sha256"] = "a" * 64
    _write(run, value)
    result = build_tasks(run, tmp_path / "dataset", case=case)
    assert result["sample_count"] == 0
    assert {r["reason"] for r in result["excluded"]} == {"exact_pre_check_snapshot_unavailable"}


def test_metadata_file_change_is_not_ignored_when_selecting_version(tmp_path):
    run, case, _ = _fixture(tmp_path)
    value = json.loads(run.read_text())
    value["development"]["events"].append({"timestamp": "2026-09-08T00:00:03Z",
        "type": "workspace_change", "payload": {"program_sha256": "b" * 64, "created": ["README.md"]}})
    _write(run, value)
    result = build_tasks(run, tmp_path / "dataset", case=case)
    assert result["sample_count"] == 1
    assert result["excluded"][0]["action_ordinal"] == 4


def test_hash_integrity_and_no_overwrite(tmp_path):
    run, case, _ = _fixture(tmp_path)
    result = build_tasks(run, tmp_path / "dataset", case=case, action_ordinals={2})
    sample = tmp_path / "dataset" / result["samples"][0]["path"]
    with pytest.raises(FileExistsError):
        build_tasks(run, tmp_path / "dataset", case=case)
    destination = tmp_path / "busy"
    destination.mkdir()
    (destination / "user.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        stage_task(sample, destination)
    (sample / "workspace/app.js").write_text("future patch")
    with pytest.raises(ValueError, match="Workspace no longer matches"):
        stage_task(sample, tmp_path / "fresh")
    assert not (tmp_path / "fresh").exists()
    assert (destination / "user.txt").read_text() == "keep"


def test_prompt_tamper_rejected(tmp_path):
    run, case, _ = _fixture(tmp_path)
    result = build_tasks(run, tmp_path / "dataset", case=case)
    sample = tmp_path / "dataset" / result["samples"][0]["path"]
    (sample / "prompt.txt").write_text("Here is the answer")
    with pytest.raises(ValueError, match="Prompt no longer matches"):
        verify_package(sample)


def test_inference_preparation_requires_matching_runtime_probe(tmp_path):
    run, case, _ = _fixture(tmp_path)
    result = build_tasks(run, tmp_path / "dataset", case=case)
    sample = tmp_path / "dataset" / result["samples"][0]["path"]
    probe = tmp_path / "runtime-check.json"
    _write(probe, {"status": "fail"})
    with pytest.raises(ValueError, match="successful runtime check"):
        prepare_agent_case(sample, tmp_path / "fresh", probe)
    assert not (tmp_path / "fresh").exists()
    _write(probe, {"status": "pass", "task_id": result["samples"][0]["task_id"],
                   "task_sha256": sha256(sample / "task.json")})
    prepared = prepare_agent_case(sample, tmp_path / "fresh", probe)
    assert prepared.prompt.startswith(case.prompt)
    assert prepared.metadata["protocol"] == "checkpoint_verify"
    assert prepared.source_dir == tmp_path / "fresh"
    assert not (prepared.source_dir / "runtime-check.json").exists()


def test_unsupported_input_and_action_rejected(tmp_path):
    run, case, _ = _fixture(tmp_path)
    with pytest.raises(ValueError, match="Not an extracted check"):
        build_tasks(run, tmp_path / "dataset", case=case, action_ordinals={999})
    _write(run, {"events": []})
    with pytest.raises(ValueError, match="normalized"):
        build_tasks(run, tmp_path / "dataset", case=case)


def test_refuses_path_traversal_in_manifest(tmp_path):
    run, case, manifest = _fixture(tmp_path)
    path = tmp_path / "trace/manifests/state.json"
    manifest["../escape"] = manifest["start.sh"]
    _write(path, manifest)
    result = build_tasks(run, tmp_path / "dataset", case=case)
    assert result["sample_count"] == 0
    assert not (tmp_path / "dataset/samples").exists()


def test_no_source_script_execution_on_development_machine(tmp_path):
    with pytest.raises(ValueError, match="fresh container"):
        validate_runtime(tmp_path / "sample", tmp_path / "out", workspace=tmp_path / "workspace")
    assert not (tmp_path / "out").exists()


def test_old_screenshots_are_removed_and_do_not_create_extra_questions(tmp_path, monkeypatch):
    import multimodalcode.vsv_eval.checkpoint_tasks as construction
    run, case, manifest = _fixture(tmp_path)
    value = json.loads(run.read_text())
    original_episodes = construction.extract_episodes(run)
    # A recorded screenshot consumption identifies an old observation file.
    value["timeline"].append({"ordinal": 8, "kind": "action", "category": "view-image",
                              "payload": {"file_path": "/workspace/old_page.png"}})
    original_episodes["episodes"][0]["evidence_ordinals"].append(8)
    monkeypatch.setattr(construction, "extract_episodes", lambda _: original_episodes)
    payload = b"old deployed page, not a prototype"
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()
    second = {**manifest, "old_page.png": {"sha256": digest, "bytes": len(payload), "mode": 0o644}}
    trace = tmp_path / "trace"
    _write(trace / "manifests/second.json", second)
    obj = trace / "program_objects" / digest[:2] / digest
    obj.parent.mkdir(parents=True, exist_ok=True)
    obj.write_bytes(payload)
    with (trace / "browser.events.jsonl").open("a") as stream:
        stream.write(json.dumps({"schema": "multimodalcode-observation-binding-1",
            "program_sha256": manifest_hash(second), "file_manifest_path": str(trace / "manifests/second.json"),
            "program_objects": {"program_object_root": str(trace / "program_objects"), "reconstructable": True}}) + "\n")
    value["development"]["events"].append({"timestamp": "2026-09-08T00:00:03Z", "type": "workspace_change",
        "payload": {"created": ["old_page.png"], "program_sha256": manifest_hash(second)}})
    _write(run, value)
    result = build_tasks(run, tmp_path / "dataset", case=case)
    assert result["sample_count"] == 1
    sample = tmp_path / "dataset" / result["samples"][0]["path"]
    assert not (sample / "workspace/old_page.png").exists()
    assert len(result["samples"][0]["origins"]) == 2
    assert len({r["source_program_sha256"] for r in result["samples"][0]["origins"]}) == 2


def test_snapshot_with_only_versions_json_metadata_is_supported(tmp_path):
    run, case, manifest = _fixture(tmp_path)
    trace = tmp_path / "trace"
    snapshot = trace / "versions/P_first"
    import shutil
    shutil.copytree(tmp_path / "source", snapshot)
    _write(trace / "versions.json", {"P_first": {"path": str(snapshot), "files": manifest,
                                                 "program_sha256": manifest_hash(manifest)}})
    result = build_tasks(run, tmp_path / "dataset", case=case)
    assert result["sample_count"] == 1


def test_screenshot_loop_variable_is_not_executed_or_missed():
    command = 'outfile="/workspace/screenshot_${page:-home}_full.png"\nchrome --screenshot="$outfile"'
    assert _screenshot_outputs(command) == ["screenshot_*_full.png"]
    assert _screenshot_outputs('playwright-cli screenshot --filename=/workspace/page.png') == ["page.png"]
    assert _screenshot_outputs('chrome --screenshot="$unknown"') == []


def _audited_fixture(tmp_path):
    import hashlib
    import shutil
    run, case, _ = _fixture(tmp_path, start=False, objects=False)
    resource = case.source_dir / "resources/logo.svg"
    resource.parent.mkdir()
    resource.write_text("<svg/>")
    value = json.loads(run.read_text())
    value["timeline"][:0] = [
        {"ordinal": 0, "kind": "action", "tool": "Bash", "tool_call_id": "launch",
         "payload": {"command": "node app.js > server.log &"}},
        {"ordinal": 1, "kind": "observation", "is_error": False,
         "payload": {"tool_use_id": "launch"}, "text": "Listening on port 3000"},
    ]
    _write(run, value)
    root = tmp_path / "audited/V1"
    shutil.copytree(tmp_path / "source", root / "app")
    (root / "app/resources").symlink_to(resource.parent, target_is_directory=True)
    files = {"app.js": sha256(root / "app/app.js")}
    reconstruction = tmp_path / "reconstruction.json"
    _write(reconstruction, {"source_run_sha256": sha256(run), "scope": "Reviewed application only",
        "versions": [{"version": "V1", "after_event": 1, "workspace": str(root),
                      "code_manifest": {"files": files,
                          "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}}]})
    config = tmp_path / "sources.json"
    _write(config, {"source_run_sha256": sha256(run), "reconstruction": str(reconstruction),
        "reconstruction_sha256": sha256(reconstruction), "code_subdir": "app",
        "public_mappings": {"app/resources": "resources"},
        "launch": {"command": ["node", "app.js"], "cwd": "app", "action_ordinal": 0, "result_ordinal": 1},
        "checkpoints": [{"version": "V1", "action_ordinal": 2}]})
    return run, case, config, reconstruction, root


def test_audited_application_without_start_uses_recorded_launch_and_public_assets(tmp_path):
    run, case, config, _, root = _audited_fixture(tmp_path)
    result = build_tasks(run, tmp_path / "dataset", case=case, sources_config=config)
    assert result["sample_count"] == 1
    row = result["samples"][0]
    assert row["checkpoint_kind"] == "audited_application"
    sample = tmp_path / "dataset" / row["path"]
    task = verify_package(sample)
    assert task["launch_command"] == ["node", "app.js"]
    assert task["launch_cwd"] == "app"
    assert not (sample / "workspace/start.sh").exists()
    assert (sample / "workspace/app/app.js").read_bytes() == (root / "app/app.js").read_bytes()
    assert (sample / "workspace/app/resources/logo.svg").read_bytes() == (sample / "workspace/resources/logo.svg").read_bytes()
    assert not (sample / "workspace/app/resources").is_symlink()
    prompt = (sample / "prompt.txt").read_text()
    assert "`node app.js`" in prompt and "`/workspace/app`" in prompt
    assert "{launch_" not in prompt and "Listening on port" not in prompt
    assert not (sample / "workspace/workflow.json").exists()


@pytest.mark.parametrize("error", ["source", "snapshot", "file", "launch", "future", "intervening", "extra_file", "private_mapping"])
def test_audited_sources_reject_unproven_inputs(tmp_path, error):
    run, case, config, reconstruction, root = _audited_fixture(tmp_path)
    c = json.loads(config.read_text())
    if error == "source":
        c["source_run_sha256"] = "0" * 64
    elif error == "snapshot":
        c["reconstruction_sha256"] = "0" * 64
    elif error == "file":
        (root / "app/app.js").write_text("later code")
    elif error == "launch":
        c["launch"]["command"] = ["node", "not-recorded.js"]
    elif error == "future":
        r = json.loads(reconstruction.read_text())
        r["versions"][0]["after_event"] = 3
        _write(reconstruction, r)
        c["reconstruction_sha256"] = sha256(reconstruction)
    elif error == "intervening":
        c["checkpoints"][0]["action_ordinal"] = 4  # action 2 has not been reviewed
    elif error == "extra_file":
        (root / "app/future.js").write_text("unlisted future code")
    elif error == "private_mapping":
        c["public_mappings"] = {"app/private": "workflow.json"}
    _write(config, c)
    with pytest.raises(ValueError):
        build_tasks(run, tmp_path / "dataset", case=case, sources_config=config)
    assert not (tmp_path / "dataset").exists()


def test_launch_directory_cannot_escape_workspace(tmp_path):
    run, case, config, _, _ = _audited_fixture(tmp_path)
    c = json.loads(config.read_text())
    c["launch"]["cwd"] = "../escape"
    _write(config, c)
    result = build_tasks(run, tmp_path / "dataset", case=case, sources_config=config)
    assert result["sample_count"] == 0
    assert "Unsafe checkpoint path" in result["excluded"][0]["reason"]


@pytest.mark.parametrize("framework", ["claude_code", "openhands"])
def test_handoff_reuses_frozen_input_and_native_runner_without_auto_continuation(tmp_path, monkeypatch, framework):
    run, case, config, _, _ = _audited_fixture(tmp_path)
    built = build_tasks(run, tmp_path / "dataset", case=case, sources_config=config)
    sample = tmp_path / "dataset" / built["samples"][0]["path"]
    original_task_hash = sha256(sample / "task.json")
    probe = tmp_path / "probe.json"
    _write(probe, {"status": "pass", "task_id": built["samples"][0]["task_id"],
                   "task_sha256": original_task_hash})
    captured = []

    def fake_runner(prepared, workspace, raw, stderr, **kwargs):
        captured.append((prepared, kwargs))
        assert prepared.metadata["protocol"] == "verification_handoff"
        assert prepared.metadata["analysis_only"]
        assert case.prompt in prepared.prompt
        assert "Your current assignment" in prepared.prompt
        assert "not taking ownership of the whole development task" in prepared.prompt
        assert "when to submit" not in prepared.prompt
        assert "Resume" not in prepared.prompt
        assert "`node app.js`" in prepared.prompt
        assert prepared.metadata["effective_prompt_sha256"] != json.loads((sample / "task.json").read_text())["effective_prompt_sha256"]
        assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 600
        assert "resume_session_id" not in kwargs
        expected_mode = "official" if framework == "claude_code" else "browser_enabled"
        assert kwargs["vision2web_mode"] == expected_mode
        assert not (workspace / "workflow.json").exists()
        assert not (workspace / "provenance.json").exists()
        (workspace / "app/app.js").write_text("// local repair in verifier branch")
        raw.write_text('{"result":"Checked the button; local change recorded."}\n')
        return {"status": "success", "versions": {}}

    if framework == "claude_code":
        from multimodalcode.agent_harness import claude_code_runner as module
        monkeypatch.setattr(module, "run_claude_code", fake_runner)
    else:
        from multimodalcode.agent_harness import openhands_runner as module
        monkeypatch.setattr(module, "run_openhands", fake_runner)
    output = tmp_path / "run"
    result = run_verification_handoff(sample, probe, output, framework=framework,
        model="verifier", base_url="http://endpoint.invalid", api_key="secret-not-to-save",
        timeout=600, workspace=tmp_path / "branch")
    assert len(captured) == 1
    assert result["automatic_return_to_A"] is False
    assert "secret-not-to-save" not in (output / "input.json").read_text()
    assert "secret-not-to-save" not in (output / "result.json").read_text()
    assert sha256(sample / "task.json") == original_task_hash
    verify_package(sample)  # B's local repair did not overwrite A's frozen input.
    with pytest.raises(FileExistsError):
        run_verification_handoff(sample, probe, output, framework=framework, model="verifier",
            base_url="http://endpoint.invalid", api_key="EMPTY", timeout=600,
            workspace=tmp_path / "another-branch")
    assert not (tmp_path / "another-branch").exists()


@pytest.mark.parametrize("framework", ["claude_code", "openhands"])
@pytest.mark.parametrize("handoff", [False, True])
def test_handoff_end_does_not_require_full_start_sh_deliverable(tmp_path, framework, handoff):
    # Real subprocess/recorder plumbing, synthetic CLI output: no LLM/browser claim.
    binary = tmp_path / "fake-agent"
    binary.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"type":"result","result":"Inspection ended"}\'\n')
    binary.chmod(0o755)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "server.js").write_text("// original implementation without start.sh")
    case = AgentCase("vision2web", "webpage/example", "webpage", "Inspect current implementation",
                     metadata={"protocol": "verification_handoff"} if handoff else {})
    kwargs = dict(model="model", base_url="http://endpoint.invalid", api_key="EMPTY", timeout=10, max_retries=0)
    if framework == "claude_code":
        from multimodalcode.agent_harness.claude_code_runner import run_claude_code
        runner = run_claude_code
        kwargs["executable"] = str(binary)
    else:
        from multimodalcode.agent_harness.openhands_runner import run_openhands
        runner = run_openhands
        kwargs.update(runtime_root=tmp_path, require_vision=False, installed_executable=str(binary))
    result = runner(case, workspace, tmp_path / "events.jsonl", tmp_path / "stderr.log", **kwargs)
    assert result["status"] == ("success" if handoff else "failed")
    assert not (workspace / "start.sh").exists()
    assert result["attempt_count"] == 1
    assert (Path(result["versions"]["P_final"]["path"]) / "server.js").is_file()
