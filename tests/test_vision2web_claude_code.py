from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from unittest.mock import patch
from pathlib import Path

from multimodalcode.agent_harness.cases import AgentCase
from multimodalcode.agent_harness.claude_code_runner import (
    ClaudeBrowserEvidenceObserver,
    _attempt_artifact_path,
    build_claude_code_command,
    build_claude_code_environment,
    read_claude_session_id,
    run_claude_code,
)
from multimodalcode.agent_harness.trajectory import normalize_claude_code


ROOT = Path(__file__).resolve().parents[1]


def _load_token_cap_module():
    source = ROOT / "scripts/vision2web/litellm_output_token_cap.py"
    spec = importlib.util.spec_from_file_location("litellm_output_token_cap", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = object
    with patch.dict(
        sys.modules,
        {
            "litellm": types.ModuleType("litellm"),
            "litellm.integrations": types.ModuleType("litellm.integrations"),
            "litellm.integrations.custom_logger": custom_logger,
        },
    ):
        spec.loader.exec_module(module)
    return module


def test_litellm_proxy_enforces_declared_output_limit(tmp_path: Path):
    output = tmp_path / "litellm.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/vision2web/write_litellm_proxy_config.py"),
            "--output",
            str(output),
            "--model-name",
            "Qwen3.5-9B",
            "--api-base",
            "http://model.invalid/v1",
            "--max-input-tokens",
            "262144",
            "--max-output-tokens",
            "8192",
        ],
        check=True,
    )
    config = json.loads(output.read_text())
    assert config["litellm_settings"]["modify_params"] is True
    assert config["litellm_settings"]["callbacks"] == [
        "litellm_output_token_cap.proxy_handler_instance"
    ]
    assert config["model_list"][0]["model_info"]["max_output_tokens"] == 8192


def test_litellm_proxy_can_reference_secret_without_persisting_it(tmp_path: Path):
    output = tmp_path / "litellm.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/vision2web/write_litellm_proxy_config.py"),
            "--output",
            str(output),
            "--model-name",
            "relay-model",
            "--api-base",
            "http://relay.invalid/v1",
            "--api-key-env",
            "LLM_RELAY_API_KEY",
            "--supports-vision",
            "false",
            "--max-input-tokens",
            "131072",
            "--max-output-tokens",
            "16384",
        ],
        check=True,
    )
    config_text = output.read_text()
    config = json.loads(config_text)
    params = config["model_list"][0]["litellm_params"]
    assert params["api_key"] == "os.environ/LLM_RELAY_API_KEY"
    assert config["model_list"][0]["model_info"]["supports_vision"] is False
    assert "secret-value" not in config_text


def test_litellm_hook_caps_only_max_tokens():
    module = _load_token_cap_module()
    request = {
        "model": "Qwen3.5-9B",
        "max_tokens": 32000,
        "messages": [{"role": "user", "content": "unchanged"}],
        "temperature": 0.2,
    }
    capped = module.cap_max_tokens(request, 8192)
    assert capped == {
        "model": "Qwen3.5-9B",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": "unchanged"}],
        "temperature": 0.2,
    }


def test_litellm_hook_preserves_tool_result_and_attaches_pixels():
    module = _load_token_cap_module()
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read-1",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "pixel-base64",
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    normalized, evidence = module.normalize_anthropic_image_blocks(request)
    assert normalized["messages"][0]["content"] == [
        {"type": "tool_result", "tool_use_id": "read-1", "content": ""},
        {"type": "image", "source": "pixel-base64"},
    ]
    assert evidence == [
        {
            "message_index": 0,
            "location": "tool_result",
            "tool_use_id": "read-1",
            "media_type": "image/png",
            "base64_characters": 12,
        }
    ]


def test_retry_artifacts_preserve_previous_job_trace(tmp_path: Path):
    base = tmp_path / "claude.events.jsonl"
    first = _attempt_artifact_path(base, attempt=1, run_token="new-run")
    assert first.name == "claude.events.attempt-1.jsonl"
    first.write_text("historical evidence\n")
    retry = _attempt_artifact_path(base, attempt=1, run_token="new-run")
    assert retry.name == "claude.events.new-run.attempt-1.jsonl"
    assert first.read_text() == "historical evidence\n"


def test_claude_environment_is_identical_to_frozen_upstream():
    source = ROOT / "evaluate/vision2web/upstream/vision2web/core/utils.py"
    spec = importlib.util.spec_from_file_location("frozen_vision2web_utils", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = module.build_claude_code_env(
        "http://proxy.invalid", "token", "Qwen3.5-9B"
    )
    actual = build_claude_code_environment(
        base_url="http://proxy.invalid", api_key="token", model="Qwen3.5-9B"
    )
    assert actual == expected


def test_direct_command_removes_only_docker_exec_transport():
    command = build_claude_code_command("official prompt", executable=sys.executable)
    assert command[1:] == [
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "-p",
        "official prompt",
    ]


def test_same_context_command_resumes_the_exact_claude_session(tmp_path: Path):
    raw = tmp_path / "claude.events.jsonl"
    raw.write_text(
        json.dumps({"type": "system", "session_id": "session-123"}) + "\n",
        encoding="utf-8",
    )
    session_id = read_claude_session_id(raw)
    command = build_claude_code_command(
        "neutral follow-up",
        executable=sys.executable,
        resume_session_id=session_id,
    )
    assert session_id == "session-123"
    assert command[1:] == [
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "--resume",
        "session-123",
        "-p",
        "neutral follow-up",
    ]


def test_normalize_claude_stream_preserves_tool_actions(tmp_path: Path):
    raw = tmp_path / "claude.events.jsonl"
    raw.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "tool-1",
                                    "name": "Bash",
                                    "input": {"command": "playwright-cli snapshot"},
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "done",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "trajectory.json"
    normalized = normalize_claude_code(
        raw,
        output,
        {"benchmark": "vision2web", "case_id": "webpage/example"},
    )
    assert normalized["scaffold"] == "claude-code-cli"
    assert normalized["events"][0]["tool"] == "Bash"
    assert "playwright-cli snapshot" in normalized["events"][0]["text"]


def test_direct_runner_records_versions_and_claude_timeline(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "fake-claude"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib\n"
        "pathlib.Path('start.sh').write_text('#!/usr/bin/env bash\\n')\n"
        "print(json.dumps({'type':'assistant','message':{'content':["
        "{'type':'tool_use','id':'x','name':'Write','input':{'file_path':'start.sh'}},"
        "{'type':'tool_use','id':'y','name':'Bash','input':{'command':'playwright-cli snapshot'}}]}}))\n"
        "print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'done'}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    case = AgentCase(
        benchmark="vision2web",
        case_id="webpage/example",
        task_type="webpage",
        prompt="official prompt",
    )
    raw = tmp_path / "run" / "claude.events.jsonl"
    raw.parent.mkdir()
    outcome = run_claude_code(
        case,
        workspace,
        raw,
        raw.parent / "claude.stderr.log",
        model="model",
        base_url="http://proxy.invalid",
        api_key="EMPTY",
        timeout=30,
        max_retries=0,
        executable=str(executable),
    )
    assert outcome["status"] == "success"
    assert outcome["framework"] == "claude_code"
    assert outcome["versions"]["P_final"]["path"]
    summary = json.loads(
        (Path(outcome["development_trace"]) / "timeline_summary.json").read_text()
    )
    assert summary["framework"] == "claude_code"
    assert summary["claude_attempts"] == 1
    timeline = [
        json.loads(line)
        for line in Path(summary["path"]).read_text().splitlines()
    ]
    types = {row["type"] for row in timeline}
    assert {"edit_action", "browser_tool_call", "submit_action"} <= types


def test_claude_conditions_use_official_browser_affordance_without_duplicate_alias():
    config = json.loads(
        (ROOT / "configs/vision2web/self_verify_claude_code.json").read_text()
    )
    conditions = {row["name"]: row for row in config["conditions"]}
    assert conditions["official"]["task_prompt"] == "official-byte-identical"
    assert set(conditions) == {"official", "guided_vsv"}
    assert "identical to official" in conditions["guided_vsv"]["tools"]
    assert config["information_boundary"]["post_trajectory_only"][0] == "workflow.json"


def test_claude_stream_proves_png_enters_model_context_and_checkpoints(tmp_path: Path):
    import base64

    workspace = tmp_path / "workspace"
    trace = tmp_path / "trace"
    workspace.mkdir()
    trace.mkdir()
    (workspace / "index.html").write_text("observed-version")
    image = b"\x89PNG\r\n\x1a\nsynthetic-test-payload"
    screenshot = workspace / "page.png"
    screenshot.write_bytes(image)
    observer = ClaudeBrowserEvidenceObserver(workspace, trace)

    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "browser-1",
                        "name": "Bash",
                        "input": {
                            "command": "/usr/bin/playwright-cli open http://localhost:3000 && playwright-cli snapshot"
                        },
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "browser-1",
                        "content": "- button 'Go' [ref=e1]",
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "read-1",
                        "name": "Read",
                        "input": {"file_path": str(screenshot)},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read-1",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64.b64encode(image).decode(),
                                },
                            }
                        ],
                    }
                ]
            },
        },
    ]
    for sequence, event in enumerate(events):
        observer.consume(
            json.dumps(event),
            timestamp=f"2026-01-01T00:00:0{sequence}+00:00",
            monotonic_ns=sequence,
            sequence=sequence,
        )
        if sequence == 1:
            # The successful ``open`` result is already a model-facing page
            # observation. A later PNG Read must not move P_first forward.
            (workspace / "index.html").write_text("changed-after-open")
    summary = observer.summary()
    assert summary["playwright_command_count"] == 2
    assert summary["model_context_image_confirmed"] is True
    assert (trace / "versions" / "P_first" / "index.html").read_text() == "observed-version"
    browser_rows = [
        json.loads(line)
        for line in (trace / "browser.events.jsonl").read_text().splitlines()
    ]
    png_row = next(row for row in browser_rows if row["type"] == "claude_png_entered_model_context")
    assert png_row["payload"]["stream_json_contains_image_block"] is True
    assert png_row["payload"]["images"][0]["sha256"]
    png_binding = png_row["payload"]["observation_binding"]
    assert png_binding["program_sha256"]
    assert Path(png_binding["file_manifest_path"]).is_file()
    command_row = next(
        row for row in browser_rows if row["type"] == "claude_browser_command_result"
    )
    assert command_row["payload"]["observation_binding"]["program_sha256"]
