from __future__ import annotations

import json
import importlib.util
import base64
import subprocess
import sys
from pathlib import Path

from multimodalcode.agent_harness.openhands_runner import build_openhands_command
from multimodalcode.agent_harness.cases import load_case
from multimodalcode.agent_harness.registry import SCAFFOLDS
from multimodalcode.agent_harness.vision2web_experiment import (
    GUIDED_VSV_INSTRUCTION,
    normalize_vision2web_mode,
    prompt_for_mode,
)
from multimodalcode.agent_harness.vision2web_trace import (
    NativeOpenHandsBrowserRecorder,
    Vision2WebRunRecorder,
    build_development_timeline,
    capture_observation_binding,
    capture_first_application_observation,
    copy_program,
    is_deployed_application_url,
    manifest_hash,
    program_manifest,
)


def test_observation_binding_is_content_addressed_and_changes_with_code(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    trace = tmp_path / "trace"
    workspace.mkdir()
    (workspace / "index.html").write_text("version-one", encoding="utf-8")

    first = capture_observation_binding(
        workspace,
        trace,
        url="http://127.0.0.1:3000/",
        observation_succeeded=True,
    )
    assert first is not None
    assert first["schema"] == "multimodalcode-observation-binding-1"
    assert first["program_sha256"]
    assert first["file_manifest_sha256"]
    assert first["file_count"] == 1
    assert first["deployment"]["port"] == 3000
    assert first["deployment"]["browser_observation_succeeded"] is True
    assert first["recorder_latency_ms"] >= 0
    manifest_path = Path(first["file_manifest_path"])
    assert manifest_path.is_file()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["index.html"][
        "sha256"
    ]

    repeated = capture_observation_binding(
        workspace,
        trace,
        url="http://localhost:3000/path",
        observation_succeeded=True,
    )
    assert repeated is not None
    assert repeated["program_sha256"] == first["program_sha256"]
    assert repeated["file_manifest_path"] == first["file_manifest_path"]

    (workspace / "index.html").write_text("version-two", encoding="utf-8")
    changed = capture_observation_binding(
        workspace,
        trace,
        url="http://localhost:3000/",
        observation_succeeded=True,
    )
    assert changed is not None
    assert changed["program_sha256"] != first["program_sha256"]
    assert changed["file_manifest_path"] != first["file_manifest_path"]

    assert (
        capture_observation_binding(
            workspace,
            trace,
            url="file:///workspace/prototypes/home.jpg",
            observation_succeeded=True,
        )
        is None
    )


def test_official_and_browser_enabled_keep_the_exact_official_task_prompt():
    prompt = "OFFICIAL\nPROMPT\n"
    assert prompt_for_mode(prompt, "official") == prompt
    assert prompt_for_mode(prompt, "browser_enabled") == prompt
    assert prompt_for_mode(prompt, "guided_vsv") == (
        "OFFICIAL\nPROMPT\n\n" + GUIDED_VSV_INSTRUCTION
    )


def test_legacy_mode_names_are_normalized_without_becoming_active_conditions():
    assert normalize_vision2web_mode("tools", scaffold="openhands") == "browser_enabled"
    assert normalize_vision2web_mode("tools", scaffold="claude_code") == "official"
    assert normalize_vision2web_mode("self_verify") == "guided_vsv"


def test_guided_vsv_prompt_artifact_matches_runtime_constant():
    root = Path(__file__).resolve().parents[1]
    frozen = (root / "configs/prompts/vision2web/guided_vsv.txt").read_text(
        encoding="utf-8"
    )
    assert frozen.rstrip("\n") == GUIDED_VSV_INSTRUCTION
    assert "/workspace/start.sh" in frozen
    assert "one user-observable requirement at a time" in frozen
    assert "actual screenshot pixels enter your context" in frozen
    assert "replay the failed check" in frozen


def test_frozen_task_prompts_are_byte_identical_to_upstream_constants():
    import hashlib

    expected = {
        "webpage/classic-clashes": "0ec0f6a4465ad81e14fc67290ed2c1a077fe150715d0b818ef113db7c9159b34",
        "frontend/forum_vectorworks": "b8c72e84c9efd85cba8ada58c0a527e69c771776f0a2a35296bede0317da3c9f",
        "website/permanent": "dc748cb63d8ba62da48b53c759244c9ca473c7f2d1465f03b2556c96d5c95177",
    }
    for case_id, digest in expected.items():
        prompt = load_case("vision2web", case_id).prompt
        assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == digest


def test_browser_modes_use_a_separate_entrypoint_without_changing_official():
    runtime = SCAFFOLDS["vision2web"].source_root
    official, _ = build_openhands_command(
        "task", runtime_root=runtime, python_executable=sys.executable
    )
    browser_enabled, env = build_openhands_command(
        "task",
        runtime_root=runtime,
        vision2web_mode="browser_enabled",
        python_executable=sys.executable,
    )
    assert official[1:3] == ["-m", "openhands_cli.entrypoint"]
    assert browser_enabled[1:3] == [
        "-m",
        "multimodalcode.agent_harness.vision2web_openhands_browser_entrypoint",
    ]
    assert env["PYTHONPATH"].split(":")[0].endswith("/MultimodalCode/src")


def test_program_versions_exclude_public_inputs_and_runtime_dependencies(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "App.jsx").write_text("export default 1")
    (workspace / "start.sh").write_text("npm run dev")
    (workspace / "prototypes").mkdir()
    (workspace / "prototypes" / "oracle.jpg").write_bytes(b"public-reference")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "vite.js").write_text("dependency")

    destination = tmp_path / "versions" / "P_first"
    destination.parent.mkdir()
    record = copy_program(workspace, destination)

    assert (destination / "src" / "App.jsx").is_file()
    assert (destination / "start.sh").is_file()
    assert not (destination / "prototypes").exists()
    assert not (destination / "node_modules").exists()
    assert record["program_sha256"] == manifest_hash(program_manifest(destination))
    assert destination.with_suffix(".json").is_file()


def test_timeline_keeps_edit_browser_and_submit_order(tmp_path: Path):
    trace = tmp_path / "trace"
    trace.mkdir()
    (trace / "workspace.events.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:01+00:00",
                "type": "workspace_change",
                "payload": {"modified": ["src/App.jsx"]},
            }
        )
        + "\n"
    )
    (trace / "browser.events.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:02+00:00",
                "type": "browser_observation",
                "payload": {"url": "http://localhost:3000"},
            }
        )
        + "\n"
    )
    raw = tmp_path / "events.jsonl"
    raw.write_text(
        json.dumps(
            {
                "id": "finish",
                "timestamp": "2026-01-01T00:00:03",
                "kind": "ActionEvent",
                "tool_name": "finish",
                "action": {"kind": "FinishAction"},
            }
        )
        + "\n"
    )

    summary = build_development_timeline(raw, trace)
    rows = [json.loads(line) for line in Path(summary["path"]).read_text().splitlines()]
    assert [row["type"] for row in rows] == [
        "workspace_change",
        "browser_observation",
        "submit_action",
    ]


def test_timeline_includes_every_preserved_retry_attempt(tmp_path: Path):
    trace = tmp_path / "trace"
    trace.mkdir()
    first = tmp_path / "attempt-1.jsonl"
    second = tmp_path / "attempt-2.jsonl"
    first.write_text(
        json.dumps(
            {
                "id": "edit-1",
                "timestamp": "2026-01-01T00:00:01",
                "kind": "ActionEvent",
                "tool_name": "file_editor",
                "action": {"command": "create", "path": "index.html"},
            }
        )
        + "\n"
    )
    second.write_text(
        json.dumps(
            {
                "id": "finish-2",
                "timestamp": "2026-01-01T00:00:02",
                "kind": "ActionEvent",
                "tool_name": "finish",
                "action": {"kind": "FinishAction"},
            }
        )
        + "\n"
    )

    summary = build_development_timeline(
        second, trace, attempt_trajectories=[first, second]
    )
    rows = [json.loads(line) for line in Path(summary["path"]).read_text().splitlines()]
    assert summary["openhands_attempts"] == 2
    assert [(row["attempt"], row["type"]) for row in rows] == [
        (1, "edit_action"),
        (2, "submit_action"),
    ]


def test_native_browser_recorder_is_a_passive_jsonl_sidecar(tmp_path: Path):
    workspace = tmp_path / "workspace"
    trace = tmp_path / "trace"
    raw = tmp_path / "openhands.events.jsonl"
    workspace.mkdir()
    trace.mkdir()
    (workspace / "index.html").write_text("observed-version", encoding="utf-8")
    raw.write_text(
        json.dumps(
            {
                "id": "native-observation-1",
                "kind": "ObservationEvent",
                "tool_name": "browser_get_state",
                "observation": {
                    "is_error": False,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"url": "http://localhost:3000/page"}
                            ),
                        }
                    ],
                    "screenshot_data": base64.b64encode(b"native-png").decode(),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    recorder = NativeOpenHandsBrowserRecorder(raw, workspace, trace)
    recorder.start()
    recorder.finish()

    first = trace / "versions/P_first/index.html"
    assert first.read_text(encoding="utf-8") == "observed-version"
    browser_rows = [
        json.loads(line)
        for line in (trace / "browser.events.jsonl").read_text().splitlines()
    ]
    assert browser_rows[0]["payload"]["state_source"] == (
        "unmodified_openhands_jsonl"
    )
    assert browser_rows[0]["payload"]["inline_image_content"] is True


def test_browser_executor_is_mechanical_and_does_not_import_evaluator():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/multimodalcode/agent_harness/vision2web_openhands_browser_entrypoint.py"
    ).read_text(encoding="utf-8")
    assert "from openhands.tools.browser_use.definition import BrowserToolSet" in source
    assert "Tool(name=BrowserToolSet.name)" in source
    assert "vision2web_browser_plan" not in source
    assert "Vision2WebBrowserToolSet" not in source

    native = (
        Path(__file__).resolve().parents[1]
        / "scaffolds/openhands/src/openhands/tools/browser_use/definition.py"
    ).read_text(encoding="utf-8")
    for tool in (
        "BrowserNavigateTool",
        "BrowserClickTool",
        "BrowserGetStateTool",
        "BrowserGetContentTool",
        "BrowserTypeTool",
        "BrowserScrollTool",
        "BrowserGoBackTool",
    ):
        assert tool in native


def test_first_version_requires_a_model_facing_deployed_app_observation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    trace = tmp_path / "trace"
    workspace.mkdir()
    (workspace / "index.html").write_text("first")
    recorder = Vision2WebRunRecorder(workspace, trace, poll_seconds=10)
    recorder.start()

    assert not is_deployed_application_url("file:///workspace/prototypes/home.jpg")
    assert not is_deployed_application_url("https://example.com")
    assert is_deployed_application_url("http://localhost:3000/path")
    assert (
        capture_first_application_observation(
            workspace,
            trace,
            framework="openhands",
            tool="browser_get_state",
            url="file:///workspace/prototypes/home.jpg",
        )
        is None
    )
    assert not (trace / "versions" / "P_first").exists()

    record = capture_first_application_observation(
        workspace,
        trace,
        framework="openhands",
        tool="browser_get_state",
        url="http://127.0.0.1:3000/",
        screenshot_sha256="pixel-hash",
    )
    assert record and record["checkpoint"]["tool"] == "browser_get_state"
    (workspace / "index.html").write_text("final")
    versions = recorder.finish()
    assert versions["schema"].endswith("-2")
    assert versions["first_application_observed"] is True
    assert (Path(versions["P_first"]["path"]) / "index.html").read_text() == "first"
    assert (Path(versions["P_final"]["path"]) / "index.html").read_text() == "final"
    events = [
        json.loads(line)
        for line in (trace / "workspace.events.jsonl").read_text().splitlines()
    ]
    workspace_changes = [row for row in events if row["type"] == "workspace_change"]
    assert workspace_changes[-1]["payload"]["modified"] == ["index.html"]
    assert [row["type"] for row in events][-2:] == [
        "workspace_change",
        "final_submission_version",
    ]


def test_browser_entrypoint_closes_the_native_executor_on_exit():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/multimodalcode/agent_harness/vision2web_openhands_browser_entrypoint.py"
    ).read_text(encoding="utf-8")
    assert "def _close_native_browser()" in source
    assert "executor.close()" in source
    assert "finally:\n        _close_native_browser()" in source


def test_frozen_selection_covers_all_three_levels_without_hidden_inputs():
    root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (root / "configs/vision2web/self_verify_openhands.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["browser_interface_revision"] == "openhands-native-browser-toolset-v1"
    fixed = config["selection"]["fixed_experiment"]
    assert {level: len(rows) for level, rows in fixed.items()} == {
        "Level 1": 6,
        "Level 2": 6,
        "Level 3": 3,
    }
    prefixes = {"Level 1": "webpage/", "Level 2": "frontend/", "Level 3": "website/"}
    all_cases = []
    for level, rows in fixed.items():
        assert all(case_id.startswith(prefixes[level]) for case_id in rows)
        all_cases.extend(rows)
    assert len(all_cases) == len(set(all_cases)) == 15
    assert "no workflow" in config["selection"]["rule"].casefold()


def test_clusterx_workers_receive_only_explicit_proxy_and_unique_job_names(
    monkeypatch,
):
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts/vision2web/submit_self_verify.py"
    spec = importlib.util.spec_from_file_location("submit_self_verify_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.WORKER_GPUS == 0
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    arguments = module.worker_proxy_arguments()
    assert arguments == [
        "-e",
        "HTTP_PROXY=http://proxy.invalid:8080",
        "-e",
        "HTTPS_PROXY=http://proxy.invalid:8080",
        "-e",
        "http_proxy=http://proxy.invalid:8080",
        "-e",
        "https_proxy=http://proxy.invalid:8080",
    ]
    first = module.job_name("guided_vsv", "frontend/forum_vectorworks", "run-a")
    second = module.job_name("guided_vsv", "frontend/forum_vectorworks", "run-b")
    assert first != second
    assert len(first) <= 32
    assert len(second) <= 32


def test_vision2web_trajectory_audit_rejects_private_workflow_access(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "trajectory.json").write_text(
        json.dumps(
            {
                "benchmark": "vision2web",
                "case_id": "webpage/example",
                "scaffold": "openhands-headless-cli",
                "events": [
                    {
                        "actor": "assistant",
                        "kind": "action",
                        "tools": ["cat /data/miyapeng/mmcode/MultimodalCode/data/vision2web/extracted/webpage/example/workflow.json"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(root / "scripts/agent_smoke/audit_trajectory.py"), str(run_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    audit = json.loads((run_dir / "trajectory_audit.json").read_text(encoding="utf-8"))
    assert audit["experiment_contaminated"] is True
    assert audit["eligible_for_controlled_experiment"] is False
    assert set(audit["forbidden_evaluator_access"]) == {
        "workflow",
        "frozen_dataset_source",
    }
