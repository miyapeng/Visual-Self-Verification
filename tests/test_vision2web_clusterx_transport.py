from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCKER_SHIM = PROJECT_ROOT / "evaluate" / "vision2web" / "clusterx_transport" / "docker"


OFFICIAL_RESET_COMMAND = (
    "rm -rf /workspace/* /workspace/.[!.]* /workspace/..?* 2>/dev/null || true; "
    "if [ -d /tmp/workspace ]; then "
    "  cp -r /tmp/workspace/* /workspace/ 2>/dev/null || true; "
    "  cp -r /tmp/workspace/.[!.]* /workspace/ 2>/dev/null || true; "
    "  cp -r /tmp/workspace/..?* /workspace/ 2>/dev/null || true; "
    "  rm -rf /tmp/workspace; "
    "fi"
)


def _run(environment: dict[str, str], *arguments: str, check: bool = True):
    return subprocess.run(
        [sys.executable, str(DOCKER_SHIM), *arguments],
        env=environment,
        check=check,
        text=True,
        capture_output=True,
    )


def test_transport_preserves_official_lifecycle_and_redacts_secrets(tmp_path: Path):
    image = "registry.example/vision2web:immutable"
    source = tmp_path / "host_input"
    source.mkdir()
    (source / "start.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (source / ".hidden").write_text("present\n", encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "input.txt").write_text("payload\n", encoding="utf-8")

    outer_workspace = tmp_path / "outer_workspace"
    outer_workspace.mkdir()
    (outer_workspace / "stale.txt").write_text("must disappear\n", encoding="utf-8")
    output = tmp_path / "host_output"
    state_dir = tmp_path / "transport"
    trace = state_dir / "trace.jsonl"

    environment = os.environ.copy()
    environment.update(
        {
            "V2W_CLUSTERX_ENABLED": "1",
            "V2W_CLUSTERX_STATE_DIR": str(state_dir),
            "V2W_CLUSTERX_TRACE": str(trace),
            "V2W_CLUSTERX_WORKSPACE": str(outer_workspace),
            "V2W_CLUSTERX_ALLOWED_INPUT_ROOT": str(tmp_path),
            "V2W_CLUSTERX_ALLOWED_OUTPUT_ROOT": str(tmp_path),
            "V2W_CLUSTERX_EXPECTED_IMAGE": image,
        }
    )

    image_result = _run(environment, "images", "-q", image)
    assert len(image_result.stdout.strip()) == 64

    created = _run(
        environment,
        "create",
        "--name",
        "vision2web-test",
        "--workdir",
        str(outer_workspace),
        "--rm",
        "-e",
        "HTTP_PROXY=http://proxy.invalid:3128",
        image,
        "sleep",
        "infinity",
    )
    container_id = created.stdout.strip()
    assert len(container_id) == 64
    _run(environment, "start", container_id)
    _run(environment, "cp", f"{source}/.", f"{container_id}:/tmp/workspace")
    _run(environment, "exec", container_id, "sh", "-c", OFFICIAL_RESET_COMMAND)

    assert not (outer_workspace / "stale.txt").exists()
    assert (outer_workspace / ".hidden").read_text(encoding="utf-8") == "present\n"
    assert (outer_workspace / "nested" / "input.txt").read_text(encoding="utf-8") == "payload\n"

    secret = "never-write-this-token-to-the-trace"
    executed = _run(
        environment,
        "exec",
        "-w",
        str(outer_workspace),
        "-e",
        f"TEST_SECRET={secret}",
        container_id,
        "bash",
        "-c",
        'mkdir -p test_results/workflow_0 && printf "%s" "$TEST_SECRET" '
        "> test_results/workflow_0/value.txt",
    )
    assert executed.returncode == 0
    _run(environment, "cp", f"{container_id}:/workspace/test_results", str(output))
    assert (output / "test_results" / "workflow_0" / "value.txt").read_text() == secret

    _run(environment, "stop", container_id)
    trace_text = trace.read_text(encoding="utf-8")
    assert secret not in trace_text
    assert "TEST_SECRET=<sha256:" in trace_text
    records = [json.loads(line) for line in trace_text.splitlines()]
    assert all(record["status"] == "ok" for record in records)


def test_transport_fails_closed_for_unknown_operations(tmp_path: Path):
    environment = os.environ.copy()
    environment.update(
        {
            "V2W_CLUSTERX_ENABLED": "1",
            "V2W_CLUSTERX_STATE_DIR": str(tmp_path / "state"),
            "V2W_CLUSTERX_WORKSPACE": str(tmp_path / "workspace"),
            "V2W_CLUSTERX_ALLOWED_INPUT_ROOT": str(tmp_path),
            "V2W_CLUSTERX_ALLOWED_OUTPUT_ROOT": str(tmp_path),
            "V2W_CLUSTERX_EXPECTED_IMAGE": "registry.example/vision2web:immutable",
        }
    )
    result = _run(environment, "run", "alpine", check=False)
    assert result.returncode == 125
    assert "unsupported docker subcommand" in result.stderr


def test_transport_is_disabled_by_default():
    environment = os.environ.copy()
    environment.pop("V2W_CLUSTERX_ENABLED", None)
    result = _run(environment, "images", "-q", "anything", check=False)
    assert result.returncode == 125
    assert "transport is disabled" in result.stderr
